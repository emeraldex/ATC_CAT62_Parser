#!/usr/bin/env python3
"""
Professional Radar CAT62 ASTERIX Parser
Industry-grade radar data visualization and analysis platform
"""
import argparse, asyncio, socket, struct, time, threading, json, logging, statistics
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import math

# Configuration
WS_PORT = 8765
HTTP_PORT = 7878
MAX_TRACK_HISTORY = 300  # Points per track
TRACK_TIMEOUT = 30  # seconds
STATS_INTERVAL = 5  # seconds

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('CAT62Parser')

# ------------------------------- Utilities ---------------------------------

def _u8(b, o=0): return b[o]
def _u16(b, o): return struct.unpack_from('>H', b, o)[0]
def _i16(b, o): return struct.unpack_from('>h', b, o)[0]
def _u32(b, o): return struct.unpack_from('>I', b, o)[0]
def _i32(b, o): return struct.unpack_from('>i', b, o)[0]
def _f32(b, o): return struct.unpack_from('>f', b, o)[0]

# WGS‑84 Lat/Lon scaling for many ASTERIX categories (degrees per LSB)
WGS84_DEG_LSB = 180.0 / (1 << 23)

@dataclass
class TrackData:
    """Track information"""
    track_id: str
    sac: Optional[int] = None
    sic: Optional[int] = None
    track_num: Optional[int] = None
    pos_lat: Optional[float] = None
    pos_lon: Optional[float] = None
    pos_x: Optional[float] = None
    pos_y: Optional[float] = None
    altitude: Optional[float] = None
    ground_speed: Optional[float] = None
    heading: Optional[float] = None
    mode3a: Optional[str] = None
    callsign: Optional[str] = None
    icao_address: Optional[str] = None
    track_status: Optional[Dict] = None
    timestamp: float = 0.0
    confidence: float = 1.0
    
    def is_stale(self, now: float, timeout: float = TRACK_TIMEOUT) -> bool:
        return (now - self.timestamp) > timeout
    
    def to_dict(self) -> Dict:
        return asdict(self)

class Statistics:
    """Track and message statistics"""
    def __init__(self):
        self.messages_received = 0
        self.messages_parsed = 0
        self.messages_failed = 0
        self.tracks_active = 0
        self.positions_update = 0
        self.speeds = deque(maxlen=1000)
        self.headings = deque(maxlen=1000)
        self.last_report = time.time()
    
    def record_message(self, success=True):
        self.messages_received += 1
        if success:
            self.messages_parsed += 1
        else:
            self.messages_failed += 1
    
    def record_speed(self, gs: float):
        if gs and gs >= 0:
            self.speeds.append(gs)
    
    def record_heading(self, hdg: float):
        if hdg is not None:
            self.headings.append(hdg % 360.0)
    
    def should_report(self) -> bool:
        return (time.time() - self.last_report) >= STATS_INTERVAL
    
    def get_report(self) -> Dict:
        self.last_report = time.time()
        return {
            'messages_received': self.messages_received,
            'messages_parsed': self.messages_parsed,
            'messages_failed': self.messages_failed,
            'tracks_active': self.tracks_active,
            'avg_speed': statistics.mean(self.speeds) if self.speeds else 0,
            'max_speed': max(self.speeds) if self.speeds else 0,
            'avg_heading': statistics.mean(self.headings) if self.headings else 0,
        }

# ---------------------------- CAT62 Decoder --------------------------------
class Asterix62:
    """Industry-grade CAT62 parser with comprehensive field support"""
    
    # CAT62 field decoders mapping
    DECODERS = {
        'I062/010': '_dec_010',
        'I062/015': '_dec_015',
        'I062/040': '_dec_040',
        'I062/060': '_dec_060',
        'I062/070': '_dec_070',
        'I062/080': '_dec_080',
        'I062/090': '_dec_090',
        'I062/100': '_dec_100',
        'I062/105': '_dec_105',
        'I062/110': '_dec_110',
        'I062/120': '_dec_120',
        'I062/135': '_dec_135',
        'I062/136': '_dec_136',
        'I062/185': '_dec_185',
        'I062/200': '_dec_200',
        'I062/210': '_dec_210',
        'I062/245': '_dec_245',
        'I062/270': '_dec_270',
        'I062/300': '_dec_300',
    }
    
    def __init__(self, payload: bytes):
        self.b = payload
        self.items = {}
        self.ok = False
        self.error_msg = None
        try:
            self.ok = self._parse()
        except Exception as e:
            self.error_msg = str(e)
            logger.debug(f"CAT62 Parse error: {e}")

    def _parse(self) -> bool:
        """Parse CAT62 ASTERIX record"""
        b = self.b
        if len(b) < 3:
            return False
        
        cat = b[0]
        if cat != 62:
            return False
        
        length = _u16(b, 1)
        if length > len(b) or length < 3:
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
        
        pos = fspec_end

        def take(n):
            nonlocal pos
            if pos + n > length:
                raise ValueError(f'Truncated: need {n} bytes, have {length - pos}')
            chunk = b[pos:pos+n]
            pos += n
            return chunk

        # Decode items based on FSPEC
        decoders = [
            ('I062/010', self._dec_010),
            ('I062/015', self._dec_015),
            ('I062/020', self._skip_var),
            ('I062/040', self._dec_040),
            ('I062/060', self._dec_060),
            ('I062/070', self._skip_var),
            ('I062/080', self._dec_080),
            ('FX', None),
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
            for bit in range(7, -1, -1):
                if bit_index >= len(decoders):
                    break
                name, fn = decoders[bit_index]
                setbit = (oct_val >> bit) & 1
                is_fx = name.startswith('FX')
                if setbit:
                    if not is_fx and fn:
                        try:
                            self.items[name] = fn(take)
                        except Exception as e:
                            logger.debug(f"Error decoding {name}: {e}")
                            return False
                bit_index += 1
        return True

    # -------- Item decoders ---------
    def _dec_010(self, take):
        """Data Source Identifier: SAC,SIC"""
        sac = take(1)[0]
        sic = take(1)[0]
        return {'sac': sac, 'sic': sic}

    def _dec_015(self, take):
        """Service Identification"""
        return {'svc': take(1)[0]}

    def _dec_040(self, take):
        """Track Number"""
        tn = _u16(take(2), 0) & 0x0FFF
        return {'tn': tn}
    
    def _dec_060(self, take):
        """Track Status"""
        out = {}
        while True:
            b = take(1)[0]
            out['conf'] = bool((b >> 7) & 1)
            out['man']  = bool((b >> 6) & 1)
            out['dup']  = bool((b >> 5) & 1)
            if (b & 0x01) == 0:
                break
        return out

    def _dec_070(self, take):
        """Flight Plan Data"""
        return {}
    
    def _dec_080(self, take):
        """Mode 3/A Code"""
        code_bytes = take(2)
        code = _u16(code_bytes, 0) & 0x0FFF
        return {'m3a': f"{code:04o}"}
    
    def _dec_090(self, take):
        """Flight Status"""
        return {}
    
    def _dec_100(self, take):
        """Calculated Track Position (Cartesian)"""
        x = _i16(take(2), 0)
        y = _i16(take(2), 0)
        return {'xy': {'x': float(x) * 0.25, 'y': float(y) * 0.25}}

    def _dec_105(self, take):
        """Calculated Track Position (WGS‑84)"""
        lat = _i32(take(4), 0) * WGS84_DEG_LSB
        lon = _i32(take(4), 0) * WGS84_DEG_LSB
        return {'pos': {'lat': lat, 'lon': lon}}
    
    def _dec_110(self, take):
        """Altitude"""
        return {}
    
    def _dec_120(self, take):
        """Altitude QNH"""
        return {}

    def _dec_135(self, take):
        """Barometric altitude"""
        return {}

    def _dec_136(self, take):
        """Geometric altitude"""
        return {}

    def _dec_185(self, take):
        """Calculated Track Velocity (Cartesian)"""
        vx = _i16(take(2), 0) * 0.25  # m/s
        vy = _i16(take(2), 0) * 0.25  # m/s
        gs = math.sqrt(vx*vx + vy*vy)
        hdg = (math.degrees(math.atan2(vx, vy)) + 360.0) % 360.0
        return {'gs': gs * 1.94384, 'hdg': hdg}  # Convert to knots
    
    def _dec_200(self, take):
        """Target Address (Mode S ICAO 24‑bit)"""
        addr = _u32(b"\x00" + take(3), 0)
        return {'addr': f"{addr:06X}"}

    def _dec_210(self, take):
        """Target Address (Target Report Descriptor)"""
        return {}

    def _dec_245(self, take):
        """Target Identification (Callsign)"""
        raw = take(6)
        bits = int.from_bytes(raw, 'big')
        chars = []
        for i in range(7, -1, -1):
            val = (bits >> (i*6)) & 0x3F
            chars.append(_ia5(val))
        ident = ''.join(chars).strip()
        return {'id': ident}

    def _dec_270(self, take):
        """Track Status Extended"""
        return {}

    def _dec_300(self, take):
        """Vehicle Fleet ID"""
        return {}

    def _skip_var(self, take):
        """Skip variable-length item"""
        while True:
            b = take(1)[0]
            if (b & 0x01) == 0:
                break
        return None


def _ia5(v):
    """IA-5 character mapping"""
    tbl = {i: chr(ord('A')+i-1) for i in range(1, 27)}
    for i, d in enumerate('0123456789'):
        tbl[48+i] = d
    tbl[32] = ' '
    return tbl.get(v, ' ')


# -------------------------- WebSocket Broadcaster ---------------------------
class WSHub:
    """WebSocket hub with track management and broadcasting"""
    def __init__(self):
        self.clients = set()
        self.tracks: Dict[str, TrackData] = {}
        self.stats = Statistics()

    async def register(self, ws):
        self.clients.add(ws)
        logger.debug(f"WebSocket client connected. Total: {len(self.clients)}")

    async def unregister(self, ws):
        self.clients.discard(ws)
        logger.debug(f"WebSocket client disconnected. Total: {len(self.clients)}")

    async def broadcast(self, msg_bytes):
        """Broadcast message to all connected clients"""
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(msg_bytes)
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)

    async def broadcast_json(self, msg_dict):
        """Broadcast JSON message"""
        await self.broadcast(json.dumps(msg_dict).encode('utf-8'))

    def update_track(self, track_id: str, data: Dict):
        """Update or create track"""
        track = self.tracks.get(track_id)
        if not track:
            track = TrackData(track_id=track_id)
            self.tracks[track_id] = track
        
        # Update fields
        if 'pos' in data:
            track.pos_lat = data['pos'].get('lat')
            track.pos_lon = data['pos'].get('lon')
        if 'xy' in data:
            track.pos_x = data['xy'].get('x')
            track.pos_y = data['xy'].get('y')
        if 'gs' in data:
            track.ground_speed = data['gs']
            self.stats.record_speed(data['gs'])
        if 'hdg' in data:
            track.heading = data['hdg']
            self.stats.record_heading(data['hdg'])
        if 'id' in data:
            track.callsign = data['id']
        if 'addr' in data:
            track.icao_address = data['addr']
        if 'm3a' in data:
            track.mode3a = data['m3a']
        if 'src' in data:
            src = data['src']
            if 'sac' in src:
                track.sac = src['sac']
            if 'sic' in src:
                track.sic = src['sic']
        if 'tn' in data:
            track.track_num = data['tn']
        
        track.timestamp = data.get('ts', time.time())
    
    def cull_stale_tracks(self, timeout: float = TRACK_TIMEOUT) -> int:
        """Remove stale tracks, return count removed"""
        now = time.time()
        stale = [tid for tid, t in self.tracks.items() if t.is_stale(now, timeout)]
        for tid in stale:
            del self.tracks[tid]
        return len(stale)

# ------------------------------ HTTP server --------------------------------
class SPAHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        root = Path(__file__).parent / 'client'
        if path == '/' or path == '':
            return str(root / 'index.html')
        p = root / path.lstrip('/')
        return str(p)
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        logger.debug(f"HTTP: {format % args}")
    
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
# ------------------------------ Main runner --------------------------------
def run_http(hub: WSHub):
    Path('client').mkdir(exist_ok=True)
    
    # Write client files if missing
    idx = Path('client/index.html')
    if not idx.exists(): 
        idx.write_text(INDEX_HTML)
    
    js = Path('client/app.js')
    if not js.exists(): 
        js.write_text(APP_JS)
    
    css = Path('client/style.css')
    if not css.exists():
        css.write_text(STYLE_CSS)
    
    # Enhanced HTTP server with API endpoints
    class APIHandler(SPAHandler):
        def do_GET(self):
            # API endpoints
            if self.path == '/api/tracks':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                tracks = [t.to_dict() for t in hub.tracks.values()]
                self.wfile.write(json.dumps(tracks).encode())
            elif self.path == '/api/stats':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                stats = hub.stats.get_report()
                stats['tracks_active'] = len(hub.tracks)
                self.wfile.write(json.dumps(stats).encode())
            elif self.path == '/api/health':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                health = {
                    'status': 'healthy',
                    'connected_clients': len(hub.clients),
                    'active_tracks': len(hub.tracks),
                    'timestamp': time.time()
                }
                self.wfile.write(json.dumps(health).encode())
            else:
                super().do_GET()
    
    httpd = HTTPServer(('0.0.0.0', HTTP_PORT), APIHandler)
    logger.info(f"HTTP server on http://0.0.0.0:{HTTP_PORT}")
    httpd.serve_forever()

async def ws_server(hub: WSHub):
    import websockets
    
    async def handler(websocket):
        await hub.register(websocket)
        try:
            async for _ in websocket:
                pass
        finally:
            await hub.unregister(websocket)
    
    logger.info(f"WebSocket server on ws://0.0.0.0:{WS_PORT}")
    return await websockets.serve(handler, '0.0.0.0', WS_PORT)

async def udp_loop(hub: WSHub, bind, mcast=None):
    """UDP receive loop"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(bind)
    if mcast:
        mreq = socket.inet_aton(mcast) + socket.inet_aton(bind[0])
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        logger.info(f"Joined multicast group {mcast}")
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    logger.info(f"UDP listening on {bind[0]}:{bind[1]}")
    
    last_cull = time.time()
    while True:
        try:
            data, _ = await loop.run_in_executor(None, sock.recvfrom, 65536)
            await handle_frame(hub, data)
        except BlockingIOError:
            pass
        except Exception as e:
            logger.error(f"UDP error: {e}")
        
        # Periodically cull stale tracks
        now = time.time()
        if now - last_cull > 2:
            culled = hub.cull_stale_tracks()
            if culled > 0:
                logger.debug(f"Culled {culled} stale tracks")
            last_cull = now
            
            # Send stats if needed
            if hub.stats.should_report():
                await hub.broadcast_json({'type': 'stats', 'data': hub.stats.get_report()})
        
        await asyncio.sleep(0.01)

async def pcap_loop(hub: WSHub, fname, speed=1.0):
    """PCAP replay loop"""
    try:
        src = PcapSource(fname)
    except Exception as e:
        logger.error(f"Failed to open PCAP: {e}")
        return
    
    t0 = None
    m0 = None
    last_cull = time.time()
    
    for ts, frame in src.packets():
        payload = _udp_payload(frame)
        if not payload:
            continue
        
        if t0 is None:
            t0 = ts
            m0 = time.time()
            logger.info(f"Starting PCAP playback from {fname}")
        
        # Timing
        target = m0 + (ts - t0) / max(speed, 0.001)
        while time.time() < target:
            await asyncio.sleep(0.001)
        
        await handle_frame(hub, payload)
        
        # Periodic maintenance
        now = time.time()
        if now - last_cull > 2:
            culled = hub.cull_stale_tracks()
            last_cull = now
            if hub.stats.should_report():
                await hub.broadcast_json({'type': 'stats', 'data': hub.stats.get_report()})

async def handle_frame(hub: WSHub, payload: bytes):
    """Process ASTERIX frame with multiple records"""
    i = 0
    now = time.time()
    
    while i + 3 <= len(payload):
        cat = payload[i]
        if cat != 62:
            break
        
        if i + 5 > len(payload):
            break
        
        length = _u16(payload, i+1)
        if length <= 3 or i + length > len(payload):
            break
        
        rec = payload[i:i+length]
        dec = Asterix62(rec)
        
        hub.stats.record_message(dec.ok)
        
        if dec.ok:
            msg = build_json(dec.items, now)
            if msg:
                # Update track in hub
                track_id = msg.get('id') or msg.get('addr') or f"{msg['src']['sac']}.{msg['src']['sic']}.{msg['tn']}"
                hub.update_track(track_id, msg)
                
                await hub.broadcast_json(msg)
        else:
            logger.debug(f"Failed to parse CAT62 record: {dec.error_msg}")
        
        i += length

def build_json(items, ts):
    """Build JSON message from parsed items"""
    if not items:
        return None
    
    out = {'type': 'track', 'ts': ts}
    
    # Consolidate source info
    src = {}
    if 'I062/010' in items:
        src.update(items['I062/010'])
    if 'I062/015' in items:
        src.update(items['I062/015'])
    if src:
        out['src'] = src
    
    # Add other fields
    if 'I062/040' in items:
        out['tn'] = items['I062/040']['tn']
    if 'I062/060' in items:
        out['status'] = items['I062/060']
    if 'I062/080' in items:
        out['m3a'] = items['I062/080']['m3a']
    if 'I062/100' in items:
        out['xy'] = items['I062/100']['xy']
    if 'I062/105' in items:
        out['pos'] = items['I062/105']['pos']
    if 'I062/185' in items:
        out.update({k: items['I062/185'][k] for k in ('gs', 'hdg')})
    if 'I062/200' in items:
        out['addr'] = items['I062/200']['addr']
    if 'I062/245' in items:
        out['id'] = items['I062/245']['id']
    
    # Require at least a position
    if 'pos' not in out and 'xy' not in out:
        return None
    
    return out

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
      if(msg.type==='stats') console.log('Stats:', msg.data);
    }catch(e){ console.error(e); }
  };
}
connect();
"""

STYLE_CSS = """
:root {
  --primary: #0066cc;
  --success: #28a745;
  --danger: #dc3545;
  --warning: #ffc107;
  --dark: #1a1a1a;
  --light: #f8f9fa;
}

* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
#map { height: 100%; }

.panel {
  position: absolute;
  top: 8px;
  left: 8px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 12px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.1);
  padding: 12px 14px;
  font-size: 12px;
  line-height: 1.6;
  min-width: 200px;
  z-index: 1000;
}

.panel h1 {
  font-size: 14px;
  margin: 0 0 8px;
  color: var(--dark);
  font-weight: 600;
}

.panel-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  color: #333;
}

.label { font-weight: 500; }
.value { font-weight: 600; color: var(--primary); }

.badge {
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 11px;
  line-height: 1.4;
  white-space: nowrap;
}

.stats-panel {
  position: absolute;
  bottom: 8px;
  right: 8px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 8px;
  padding: 10px;
  font-size: 11px;
  min-width: 150px;
}
"""

# ------------------------------ PCAP Reader --------------------------------
PCAP_HDR = struct.Struct('>IHHiiii')
PKT_HDR = struct.Struct('>IIII')

class PcapSource:
    """PCAP file reader for LINKTYPE_ETHERNET and LINKTYPE_RAW (UDP only)"""
    def __init__(self, fname):
        self.f = open(fname, 'rb')
        self._init()

    def _init(self):
        gh = self.f.read(24)
        if len(gh) != 24:
            raise RuntimeError('Not a valid PCAP file')
        # Endianness sniff
        magic = struct.unpack_from('I', gh, 0)[0]
        self.le = (magic == 0xA1B2C3D4)
        self.linktype = struct.unpack('<I' if self.le else '>I', gh[20:24])[0]

    def packets(self):
        rd = self.f.read
        while True:
            hdr = rd(16)
            if len(hdr) != 16:
                break
            if self.le:
                ts_sec, ts_usec, incl, orig = struct.unpack('<IIII', hdr)
            else:
                ts_sec, ts_usec, incl, orig = struct.unpack('>IIII', hdr)
            data = rd(incl)
            if len(data) < incl:
                break
            yield ts_sec + ts_usec/1e6, data

def _udp_payload(frame: bytes) -> Optional[bytes]:
    """Extract UDP payload from frame (IPv4 only, Ethernet or RAW)"""
    try:
        iphdr_off = 0
        # Ethernet?
        if len(frame) >= 14:
            eth_type = struct.unpack('>H', frame[12:14])[0]
            if eth_type == 0x0800:  # IPv4
                iphdr_off = 14
            elif eth_type == 0x0806 or eth_type == 0x86DD:  # ARP or IPv6
                return None
            else:
                # Maybe RAW/IP
                iphdr_off = 0
        
        ver_ihl = frame[iphdr_off]
        ihl = (ver_ihl & 0x0F) * 4
        proto = frame[iphdr_off+9]
        if proto != 17:  # UDP
            return None
        
        udpoff = iphdr_off + ihl
        if udpoff + 8 > len(frame):
            return None
        
        srcp, dstp, length = struct.unpack('>HHH', frame[udpoff:udpoff+6])
        data_len = length - 8
        if data_len < 0 or udpoff + 8 + data_len > len(frame):
            return None
        
        return frame[udpoff+8 : udpoff+8+data_len]
    except Exception:
        return None

async def main():
    """Main entry point"""
    ap = argparse.ArgumentParser(
        description='Professional Radar CAT62 ASTERIX Parser',
        epilog='Examples:\n'
               '  python parser_server.py --udp 0.0.0.0:31002\n'
               '  python parser_server.py --udp 224.0.0.1:31002 --mcast 224.0.0.1\n'
               '  python parser_server.py --pcap capture.pcap\n'
               '  python parser_server.py --pcap capture.pcap --speed 2.0'
    )
    
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--udp', help='UDP endpoint to bind (e.g., 0.0.0.0:31002)')
    g.add_argument('--pcap', help='PCAP file to replay')
    
    ap.add_argument('--mcast', help='Multicast group to join (with --udp)')
    ap.add_argument('--speed', type=float, default=1.0, help='Playback speed (with --pcap)')
    ap.add_argument('--verbose', action='store_true', help='Enable debug logging')
    
    args = ap.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    hub = WSHub()
    logger.info("Radar CAT62 Parser starting...")
    
    # Start WS server
    import websockets
    ws_task = asyncio.create_task(ws_server(hub))
    
    # HTTP server in thread
    t = threading.Thread(target=run_http, args=(hub,), daemon=True)
    t.start()
    
    # Source loop
    try:
        if args.udp:
            host, port = args.udp.split(':')
            port = int(port)
            await udp_loop(hub, (host, port), args.mcast)
        else:
            await pcap_loop(hub, args.pcap, args.speed)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass