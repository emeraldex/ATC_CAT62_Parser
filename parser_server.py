#!/usr/bin/env python3
import argparse, asyncio, socket, struct, time, threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path

WS_PORT = 8765
HTTP_PORT = 7878

# ------------------------------- Utilities ---------------------------------

def _u8(b, o=0): return b[o]
def _u16(b, o): return struct.unpack_from('>H', b, o)[0]
def _i16(b, o): return struct.unpack_from('>h', b, o)[0]
def _u32(b, o): return struct.unpack_from('>I', b, o)[0]
def _i32(b, o): return struct.unpack_from('>i', b, o)[0]

# WGS‑84 Lat/Lon scaling for many ASTERIX categories (degrees per LSB)
WGS84_DEG_LSB = 180.0 / (1 << 23)

# ---------------------------- CAT62 Decoder --------------------------------
class Asterix62:
    """Very small, pragmatic CAT62 parser that knows how to walk FSPEC and
    decode a subset of common items. Unknown items are skipped safely.
    """
    def __init__(self, payload: bytes):
        self.b = payload
        self.items = {}
        self.ok = self._parse()

    def _parse(self) -> bool:
        b = self.b
        if len(b) < 3:
            return False
        cat = b[0]
        if cat != 62:
            return False
        length = _u16(b, 1)
        if length > len(b):
            return False
        fspec_start = 3
        fspec_end = fspec_start
        # Read FSPEC (variable length, bit7 indicates extension)
        while True:
            if fspec_end >= length:
                return False
            octet = b[fspec_end]
            fspec_end += 1
            if (octet & 0x01) == 0:  # LSB=FX=0 -> last FSPEC
                break
        # Now iterate items per FSPEC bits; we map bit positions to decoders.
        pos = fspec_end

        def take(n):
            nonlocal pos
            if pos + n > length: raise ValueError('Truncated')
            chunk = b[pos:pos+n]
            pos += n
            return chunk

        # Bit map (Octet1 MSB=bit8 -> 128, ... bit2 -> 2) excluding FX bit1.
        # This table includes a subset; extend as needed.
        decoders = [
            ('I062/010', self._dec_010),  # FSPEC bit 8 of first octet
            ('I062/015', self._dec_015),
            ('I062/020', self._skip_var),
            ('I062/040', self._dec_040),
            ('I062/060', self._dec_060),
            ('I062/070', self._skip_var),
            ('I062/080', self._dec_080),
            ('FX', None),  # extension bit

            # If there is a 2nd FSPEC octet, continue mapping:
            ('I062/090', self._skip_var),
            ('I062/100', self._dec_100),
            ('I062/105', self._dec_105),
            ('I062/110', self._skip_var),
            ('I062/120', self._skip_var),
            ('I062/135', self._skip_var),
            ('I062/136', self._skip_var),
            ('FX2', None),

            ('I062/185', self._dec_185),
            ('I062/200', self._dec_200),
            ('I062/210', self._skip_var),
            ('I062/220', self._skip_var),
            ('I062/245', self._dec_245),
            ('I062/270', self._skip_var),
            ('I062/300', self._skip_var),
            ('FX3', None),
        ]

        fspec_bits = b[fspec_start:fspec_end]
        bit_index = 0
        for oct_i, oct_val in enumerate(fspec_bits):
            for bit in range(7, -1, -1):  # bit7..0
                name, fn = decoders[bit_index] if bit_index < len(decoders) else ('UNK', self._skip_var)
                setbit = (oct_val >> bit) & 1
                is_fx = name.startswith('FX')
                if setbit:
                    if not is_fx and fn:
                        try:
                            self.items[name] = fn(take)
                        except Exception:
                            return False
                bit_index += 1
        return True

 # ---------------- Item decoders -----------------
    def _dec_010(self, take):
        # Data Source Identifier: 2 bytes (SAC,SIC)
        sac = take(1)[0]
        sic = take(1)[0]
        return {'sac': sac, 'sic': sic}

    def _dec_015(self, take):
        # Service Identification: 1 byte
        return {'svc': take(1)[0]}

    def _dec_040(self, take):
        # Track Number: 2 bytes (usually 12 bits used)
        tn = _u16(take(2), 0) & 0x0FFF
        return {'tn': tn}
    
    def _dec_060(self, take):
        # Track Status: variable length, FX in LSB
        # We parse a subset (Confidence, Manually Init, Duplicate)
        out = {}
        while True:
            b = take(1)[0]
            out['conf'] = bool((b >> 7) & 1)  # example mapping; adjust per site
            out['man']  = bool((b >> 6) & 1)
            out['dup']  = bool((b >> 5) & 1)
            if (b & 0x01) == 0:
                break
        return out

    def _dec_080(self, take):
        # Mode 3/A code: 2 bytes + status octet; keep as 4‑digit octal string if possible
        code_bytes = take(2)
        code = _u16(code_bytes, 0) & 0x0FFF
        return {'m3a': f"{code:04o}"}
    
    def _dec_100(self, take):
        # Calculated Track Position (Cartesian) – 2 x 2 bytes (X,Y) in meters (LSB often 1/4 m)
        x = _i16(take(2), 0)
        y = _i16(take(2), 0)
        return {'xy': {'x': float(x), 'y': float(y)}}

    def _dec_105(self, take):
        # Calculated Track Position (WGS‑84): 2 x 4 bytes signed, LSB ~ 180/2^23 deg
        lat = _i32(take(4), 0) * WGS84_DEG_LSB
        lon = _i32(take(4), 0) * WGS84_DEG_LSB
        return {'pos': {'lat': lat, 'lon': lon}}

    def _dec_185(self, take):
        # Calculated Track Velocity (Cartesian): Vx,Vy (2x2 bytes). Convert to ground speed & heading.
        vx = _i16(take(2), 0)  # m/s * LSB (site dependent). Assume 1/4 m/s if needed.
        vy = _i16(take(2), 0)
        # Assume LSB=0.25 m/s (adjust if your site differs):
        scale = 0.25
        vx *= scale; vy *= scale
        gs = (vx*vx + vy*vy) ** 0.5
        import math
        hdg = (math.degrees(math.atan2(vx, vy)) + 360.0) % 360.0  # 0°=North, cw
        return {'gs': gs, 'hdg': hdg}
    
    def _dec_200(self, take):
        # Target Address (Mode S ICAO 24‑bit)
        addr = _u32(b"\x00" + take(3), 0)
        return {'addr': f"{addr:06X}"}

    def _dec_245(self, take):
        # Target Identification (callsign), 6 bytes IA‑5; we’ll map to string
        raw = take(6)
        # IA‑5 6‑bit unpacking into 8 chars (common ASTERIX packing)
        bits = int.from_bytes(raw, 'big')
        chars = []
        # Extract 8 groups of 6 bits from MSB to LSB
        for i in range(7, -1, -1):
            val = (bits >> (i*6)) & 0x3F
            chars.append(_ia5(val))
        ident = ''.join(chars).strip()
        return {'id': ident}


def _ia5(v):
    # Minimal IA‑5 subset used in IDs (A‑Z, 0‑9, space)
    tbl = {i: chr(ord('A')+i-1) for i in range(1, 27)}
    for i, d in enumerate('0123456789'):
        tbl[48+i] = d
    tbl[32] = ' '
    return tbl.get(v, ' ')

# -------------------------- WebSocket Broadcaster ---------------------------
class WSHub:
    def __init__(self):
        self.clients = set()

    async def register(self, ws):
        self.clients.add(ws)

    async def unregister(self, ws):
        self.clients.discard(ws)

    async def broadcast(self, msg_bytes):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(msg_bytes)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)

# ------------------------------ HTTP server --------------------------------
class SPAHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        root = Path(__file__).parent / 'client'
        if path == '/' or path == '':
            return str(root / 'index.html')
        p = root / path.lstrip('/')
        return str(p)
    
# ------------------------------ PCAP Reader --------------------------------
# Minimal PCAP parser for LINKTYPE_ETHERNET and LINKTYPE_RAW (UDP only)
PCAP_HDR = struct.Struct('>IHHiiii')
PKT_HDR = struct.Struct('>IIII')

class PcapSource:
    def __init__(self, fname):
        self.f = open(fname, 'rb')
        self._init()

    def _init(self):
        gh = self.f.read(24)
        if len(gh) != 24:
            raise RuntimeError('Not a PCAP file')
        # Endianness sniff
        magic = struct.unpack_from('I', gh, 0)[0]
        self.le = (magic == 0xA1B2C3D4)
        self.linktype = struct.unpack('<I' if self.le else '>I', gh[20:24])[0]

    def packets(self):
        rd = self.f.read
        while True:
            hdr = rd(16)
            if len(hdr) != 16: break
            if self.le:
                ts_sec, ts_usec, incl, orig = struct.unpack('<IIII', hdr)
            else:
                ts_sec, ts_usec, incl, orig = struct.unpack('>IIII', hdr)
            data = rd(incl)
            yield ts_sec + ts_usec/1e6, data

# ------------------------------ Main runner --------------------------------
async def ws_server(hub: WSHub):
    import websockets
    async def handler(websocket):
        await hub.register(websocket)
        try:
            async for _ in websocket:
                pass
        finally:
            await hub.unregister(websocket)
    return await websockets.serve(handler, '0.0.0.0', WS_PORT)

async def udp_loop(hub: WSHub, bind, mcast=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(bind)
    if mcast:
        mreq = socket.inet_aton(mcast) + socket.inet_aton(bind[0])
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    while True:
        data, _ = await loop.run_in_executor(None, sock.recvfrom, 65536)
        await handle_frame(hub, data)

async def pcap_loop(hub: WSHub, fname, speed=1.0):
    src = PcapSource(fname)
    t0 = None; m0 = None
    for ts, frame in src.packets():
        # Very naive UDP extraction: try to locate ASTERIX payload after UDP header
        # Works for Ethernet/IP/UDP and RAW/UDP captures.
        payload = _udp_payload(frame)
        if not payload: continue
        if t0 is None:
            t0 = ts; m0 = time.time()
        # timing
        target = m0 + (ts - t0) / max(speed, 0.001)
        while time.time() < target:
            await asyncio.sleep(0.001)
        await handle_frame(hub, payload)

async def handle_frame(hub: WSHub, payload: bytes):
    # ASTERIX frames may contain multiple records back‑to‑back.
    i = 0
    now = time.time()
    while i + 3 <= len(payload):
        cat = payload[i]
        if cat != 62: break
        if i + 5 > len(payload): break
        length = _u16(payload, i+1)
        if length <= 3 or i + length > len(payload):
            break
        rec = payload[i:i+length]
        dec = Asterix62(rec)
        if dec.ok:
            msg = build_json(dec.items, now)
            if msg:
                import json
                await hub.broadcast(json.dumps(msg).encode('utf-8'))
        i += length

def build_json(items, ts):
    if not items: return None
    out = { 'type': 'track', 'ts': ts }
    if 'I062/010' in items: out['src'] = items['I062/010']
    if 'I062/015' in items: out['src'] = { **out.get('src', {}), **items['I062/015'] }
    if 'I062/040' in items: out['tn']  = items['I062/040']['tn']
    if 'I062/060' in items: out['status'] = items['I062/060']
    if 'I062/080' in items: out['m3a'] = items['I062/080']['m3a']
    if 'I062/100' in items: out['xy']  = items['I062/100']['xy']
    if 'I062/105' in items: out['pos'] = items['I062/105']['pos']
    if 'I062/185' in items: out.update({k: items['I062/185'][k] for k in ('gs','hdg')})
    if 'I062/200' in items: out['addr'] = items['I062/200']['addr']
    if 'I062/245' in items: out['id']   = items['I062/245']['id']
    # Require at least a position to render
    if 'pos' not in out and 'xy' not in out:
        return None
    return out

# Quick‑n‑dirty UDP payload extraction (IPv4 only)
def _udp_payload(frame: bytes):
    try:
        # Ethernet?
        if len(frame) >= 14:
            eth_type = struct.unpack('>H', frame[12:14])[0]
            if eth_type == 0x0800:  # IPv4
                iphdr_off = 14
            else:
                # Maybe RAW/IP
                iphdr_off = 0
        else:
            iphdr_off = 0
        ver_ihl = frame[iphdr_off]
        ihl = (ver_ihl & 0x0F) * 4
        proto = frame[iphdr_off+9]
        if proto != 17: return None  # UDP
        udpoff = iphdr_off + ihl
        srcp, dstp, length = struct.unpack('>HHH', frame[udpoff:udpoff+6])
        data = frame[udpoff+8 : udpoff+8+length-8]
        return data
    except Exception:
        return None

# ------------------------------- Entrypoint --------------------------------
def run_http():
    Path('client').mkdir(exist_ok=True)
    # Write client files if missing
    idx = Path('client/index.html')
    if not idx.exists(): idx.write_text(INDEX_HTML)
    js = Path('client/app.js')
    if not js.exists(): js.write_text(APP_JS)
    httpd = HTTPServer(('0.0.0.0', HTTP_PORT), SPAHandler)
    print(f"HTTP on http://127.0.0.1:{HTTP_PORT}")
    httpd.serve_forever()

async def main():
    ap = argparse.ArgumentParser(description='CAT62 -> Web map')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--udp', help='Bind UDP host:port (e.g. 0.0.0.0:31002)')
    g.add_argument('--pcap', help='Replay from PCAP file')
    ap.add_argument('--mcast', help='Join multicast group (with --udp)')
    ap.add_argument('--speed', type=float, default=1.0)
    args = ap.parse_args()

    hub = WSHub()
    # Start WS server
    import websockets
    ws_srv = await websockets.serve(lambda ws: hub.register(ws) or ws.wait_closed(), '0.0.0.0', WS_PORT)
    # Proper handler variant
    ws_task = asyncio.create_task(ws_server(hub))

    # HTTP server in thread
    t = threading.Thread(target=run_http, daemon=True)
    t.start()

    # Source loop
    if args.udp:
        host, port = args.udp.split(':'); port = int(port)
        await udp_loop(hub, (host, port), args.mcast)
    else:
        await pcap_loop(hub, args.pcap, args.speed)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

# ------------------------------ Static client ------------------------------
INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>CAT62 Live Viewer</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style> html,body,#map{height:100%;margin:0} .badge{background:#0008;color:#fff;padding:2px 6px;border-radius:6px;font:12px/16px system-ui;}
  .panel{position:absolute;top:8px;left:8px;background:#fff;border-radius:12px;box-shadow:0 6px 20px #0002;padding:10px 12px;font:14px system-ui}
  .panel h1{font-size:16px;margin:0 0 8px}
  </style>
</head>
<body>
  <div id='map'></div>
  <div class='panel'>
    <h1>CAT62 Live Viewer</h1>
    <div>WS: <span id='wsstat'>connecting…</span></div>
    <div>Tracks: <span id='trkcount'>0</span></div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="app.js"></script>
</body>
</html>"""

APP_JS = """
const map = L.map('map').setView([3.1390, 101.6869], 8); // KL area default
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 12 }).addTo(map);

const wsstat = document.getElementById('wsstat');
const trkcount = document.getElementById('trkcount');

const tracks = new Map();

function makeKey(msg){
  // Prefer ICAO address, else Track Number, else composite
  return msg.addr || (msg.src? `${msg.src.sac}.${msg.src.sic}.${msg.tn||'?'}`: `${Date.now()}-${Math.random()}`);
}

function ensureLayer(t){
  if(!t.layer){ t.layer = L.layerGroup().addTo(map); }
  if(!t.marker){ t.marker = L.marker([0,0], {rotationAngle:0}).addTo(t.layer); }
  if(!t.trail){ t.trail = L.polyline([], {weight:2, opacity:0.6}).addTo(t.layer); }
}

function headingArrow(hdg){
  const len = 0.05; // degrees-ish; fine for visualization
  const rad = (hdg||0) * Math.PI / 180;
  return [Math.sin(rad)*len, Math.cos(rad)*len];
}

function updateTrack(msg){
  const key = makeKey(msg);
  let t = tracks.get(key);
  if(!t){ t = { history: [] }; tracks.set(key, t); }
  ensureLayer(t);
  const now = Date.now();

  // Position
  let lat, lon;
  if(msg.pos){ lat = msg.pos.lat; lon = msg.pos.lon; }
  else if(msg.xy){ return; } // skip cartesian only for now
  else { return; }

  // Update history (max 60 points)
  t.history.push([lat, lon]);
  if(t.history.length > 60) t.history.shift();

  // Heading vector
  let hdg = msg.hdg || null;
  t.marker.setLatLng([lat,lon]);
  t.trail.setLatLngs(t.history);

  const label = `${msg.id || msg.addr || ''} ${msg.m3a? '('+msg.m3a+')':''} ${msg.gs? Math.round(msg.gs)+'kt':''}`.trim();
  const div = L.divIcon({className:'', html:`<div class='badge'>${label||'UNK'}</div>`});
  t.marker.setIcon(div);

  if(hdg != null){
    const [dx,dy] = headingArrow(hdg);
    if(!t.arrow){ t.arrow = L.polyline([], {weight:2, dashArray:'4 4'}).addTo(t.layer); }
    t.arrow.setLatLngs([[lat,lon],[lat+dy,lon+dx]]);
  }

  t.last = now;
  trkcount.textContent = tracks.size;
}

function cull(){
  const now = Date.now();
  for(const [k,t] of tracks){
    if(!t.last || now - t.last > 15000){ // 15s stale
      if(t.layer) t.layer.remove();
      tracks.delete(k);
    }
  }
  trkcount.textContent = tracks.size;
}
setInterval(cull, 2000);

function connect(){
  const ws = new WebSocket('ws://'+location.hostname+':8765');
  ws.onopen = ()=> wsstat.textContent = 'connected';
  ws.onclose = ()=> { wsstat.textContent = 'disconnected'; setTimeout(connect, 1000); };
  ws.onmessage = (ev)=>{
    try{
      const msg = JSON.parse(ev.data);
      if(msg.type==='track') updateTrack(msg);
    }catch(e){ console.error(e); }
  };
}
connect();
"""