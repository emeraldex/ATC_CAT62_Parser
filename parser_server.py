#!/usr/bin/env python3
"""
Professional Radar CAT62 ASTERIX Parser
Industry-grade radar data visualization and analysis platform
"""
import argparse, asyncio, socket, struct, time, threading, json, logging, statistics, signal, os
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path
from dataclasses import dataclass, asdict
from collections import deque
from typing import Dict, Optional
import math

# Configuration (defaults; overridable via CLI flags / env)
DEFAULT_WS_PORT = 8765
DEFAULT_HTTP_PORT = 7878
DEFAULT_BIND = '0.0.0.0'
DEFAULT_TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
MAX_TRACK_HISTORY = 300  # Points per track
TRACK_TIMEOUT = 30  # seconds
STATS_INTERVAL = 5  # seconds

# Logging setup (honors LOG_LEVEL env var; --verbose overrides to DEBUG)
_env_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, _env_level, logging.INFO),
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
        """Parse CAT62 ASTERIX record. Sets self.error_msg on any rejection."""
        b = self.b
        if len(b) < 3:
            self.error_msg = f'Too short: {len(b)} bytes (need >= 3)'
            return False

        cat = b[0]
        if cat != 62:
            self.error_msg = f'Wrong category: {cat} (expected 62)'
            return False

        length = _u16(b, 1)
        if length > len(b) or length < 3:
            self.error_msg = f'Bad length field: {length} (payload {len(b)} bytes)'
            return False

        fspec_start = 3
        fspec_end = fspec_start

        # Read FSPEC (variable length, bit7 indicates extension)
        while True:
            if fspec_end >= length:
                self.error_msg = 'Truncated FSPEC'
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
                            self.error_msg = f'Error decoding {name}: {e}'
                            logger.debug(self.error_msg)
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
    """WebSocket hub with track management and broadcasting.

    Shared state (tracks/stats/clients) is touched by both the asyncio event
    loop and the HTTP server's daemon thread, so every access goes through
    ``self._lock``. Rule: never hold the lock across an ``await`` or across a
    blocking socket write -- snapshot under the lock, then act outside it.
    """
    def __init__(self):
        self.clients = set()
        self.tracks: Dict[str, TrackData] = {}
        self.stats = Statistics()
        self._lock = threading.Lock()
        # Lifecycle handles, set once the servers are up (used for shutdown).
        self.httpd = None
        self.ws_server_obj = None
        self.last_frame_ts = 0.0  # wall-clock time of the last ingested frame

    async def register(self, ws):
        with self._lock:
            self.clients.add(ws)
            n = len(self.clients)
        logger.debug(f"WebSocket client connected. Total: {n}")

    async def unregister(self, ws):
        with self._lock:
            self.clients.discard(ws)
            n = len(self.clients)
        logger.debug(f"WebSocket client disconnected. Total: {n}")

    async def broadcast(self, msg_bytes):
        """Broadcast message to all connected clients."""
        with self._lock:
            targets = list(self.clients)
        dead = []
        for ws in targets:
            try:
                await ws.send(msg_bytes)
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")
                dead.append(ws)
        if dead:
            with self._lock:
                for ws in dead:
                    self.clients.discard(ws)

    async def broadcast_json(self, msg_dict):
        """Broadcast JSON message"""
        await self.broadcast(json.dumps(msg_dict).encode('utf-8'))

    def update_track(self, track_id: str, data: Dict):
        """Update or create track (event-loop side; holds the lock briefly)."""
        with self._lock:
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
            self.last_frame_ts = time.time()

    def record_message(self, success: bool):
        """Record a decode outcome in stats (thread-safe)."""
        with self._lock:
            self.stats.record_message(success)

    def cull_stale_tracks(self, timeout: float = TRACK_TIMEOUT) -> int:
        """Remove stale tracks, return count removed."""
        now = time.time()
        with self._lock:
            stale = [tid for tid, t in self.tracks.items() if t.is_stale(now, timeout)]
            for tid in stale:
                del self.tracks[tid]
            return len(stale)

    # -- Snapshot helpers for the HTTP thread (copy under lock, serialize after) --
    def snapshot_tracks(self) -> list:
        with self._lock:
            return [t.to_dict() for t in self.tracks.values()]

    def stats_snapshot(self) -> Dict:
        with self._lock:
            report = self.stats.get_report()
            report['tracks_active'] = len(self.tracks)
            return report

    def counts(self):
        """Return (active_tracks, connected_clients, last_frame_ts)."""
        with self._lock:
            return len(self.tracks), len(self.clients), self.last_frame_ts

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
    
# ------------------------------ Main runner --------------------------------
# (The PCAP reader / _udp_payload live further down, just above main().)
def run_http(hub: WSHub, http_port: int, bind: str, ws_port: int, tile_url: str):
    """Serve the static client + JSON API. Runs in a daemon thread.

    On a bind failure this logs and returns instead of dying silently: the
    rest of the process (WS + ingest) keeps running.
    """

    def _write_json(handler, obj, status=200):
        """Serialize outside any lock, then write; tolerate client disconnects."""
        body = json.dumps(obj).encode()
        try:
            handler.send_response(status)
            handler.send_header('Content-type', 'application/json')
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # client went away mid-response; nothing to do

    # Enhanced HTTP server with API endpoints
    class APIHandler(SPAHandler):
        def do_GET(self):
            try:
                if self.path == '/api/tracks':
                    _write_json(self, hub.snapshot_tracks())
                elif self.path == '/api/stats':
                    _write_json(self, hub.stats_snapshot())
                elif self.path == '/api/config':
                    _write_json(self, {'ws_port': ws_port, 'http_port': http_port,
                                       'bind': bind, 'tile_url': tile_url})
                elif self.path == '/api/health':
                    active, clients, last_ts = hub.counts()
                    ws_up = hub.ws_server_obj is not None
                    age = (time.time() - last_ts) if last_ts else None
                    # "healthy" only if the WS is up and we've seen a frame recently
                    ok = ws_up and (age is None or age < TRACK_TIMEOUT * 2)
                    _write_json(self, {
                        'status': 'healthy' if ok else 'degraded',
                        'websocket_up': ws_up,
                        'connected_clients': clients,
                        'active_tracks': active,
                        'seconds_since_last_frame': round(age, 1) if age is not None else None,
                        'timestamp': time.time(),
                    }, status=200 if ok else 503)
                else:
                    super().do_GET()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as e:
                logger.error(f"HTTP handler error for {self.path}: {e}")

    class _Server(HTTPServer):
        allow_reuse_address = True

    try:
        httpd = _Server((bind, http_port), APIHandler)
    except OSError as e:
        logger.error(f"HTTP server could not bind {bind}:{http_port} ({e}); "
                     f"web UI/API unavailable")
        return
    hub.httpd = httpd
    logger.info(f"HTTP server on http://{bind}:{http_port}")
    try:
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"HTTP server stopped: {e}")
    finally:
        httpd.server_close()

async def ws_server(hub: WSHub, ws_port: int, bind: str):
    import websockets

    async def handler(websocket):
        await hub.register(websocket)
        try:
            async for _ in websocket:
                pass
        finally:
            await hub.unregister(websocket)

    server = await websockets.serve(handler, bind, ws_port)
    logger.info(f"WebSocket server on ws://{bind}:{ws_port}")
    return server

async def _maybe_maintain(hub: WSHub, last_cull: float) -> float:
    """Periodic cull + stats broadcast; returns updated last_cull timestamp."""
    now = time.time()
    if now - last_cull > 2:
        culled = hub.cull_stale_tracks()
        if culled > 0:
            logger.debug(f"Culled {culled} stale tracks")
        last_cull = now
        if hub.stats.should_report():
            await hub.broadcast_json({'type': 'stats', 'data': hub.stats_snapshot()})
    return last_cull


async def udp_loop(hub: WSHub, bind, mcast=None, stop_event: 'asyncio.Event' = None):
    """UDP receive loop."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(bind)
        if mcast:
            mreq = socket.inet_aton(mcast) + socket.inet_aton(bind[0])
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            logger.info(f"Joined multicast group {mcast}")
        sock.setblocking(False)
    except OSError as e:
        logger.error(f"UDP setup failed on {bind[0]}:{bind[1]} ({e}); ingest disabled")
        return

    loop = asyncio.get_running_loop()
    logger.info(f"UDP listening on {bind[0]}:{bind[1]}")
    last_cull = time.time()
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                data, _ = await loop.run_in_executor(None, sock.recvfrom, 65536)
                await handle_frame(hub, data)
            except BlockingIOError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"UDP error: {e}")
                await asyncio.sleep(0.1)  # backoff so a persistent error can't spin
            last_cull = await _maybe_maintain(hub, last_cull)
            await asyncio.sleep(0.01)
    finally:
        sock.close()
        logger.info("UDP loop stopped")

async def pcap_loop(hub: WSHub, fname, speed=1.0, loop_forever=False,
                    stop_event: 'asyncio.Event' = None):
    """PCAP replay loop. Replays once, or repeatedly when loop_forever is set."""
    while stop_event is None or not stop_event.is_set():
        try:
            src = PcapSource(fname)
        except Exception as e:
            logger.error(f"Failed to open PCAP {fname}: {e}")
            return

        t0 = None
        m0 = None
        last_cull = time.time()
        try:
            for ts, frame in src.packets():
                if stop_event is not None and stop_event.is_set():
                    break
                payload = _udp_payload(frame)
                if not payload:
                    continue
                if t0 is None:
                    t0 = ts
                    m0 = time.time()
                    logger.info(f"Starting PCAP playback from {fname}")
                # Timing (pace playback to the capture timestamps)
                target = m0 + (ts - t0) / max(speed, 0.001)
                while time.time() < target:
                    if stop_event is not None and stop_event.is_set():
                        break
                    await asyncio.sleep(0.001)
                await handle_frame(hub, payload)
                last_cull = await _maybe_maintain(hub, last_cull)
        finally:
            src.close()

        if not loop_forever:
            logger.info("PCAP playback complete")
            return
        logger.info("PCAP playback complete; looping")

async def idle_serve(hub: WSHub, stop_event: 'asyncio.Event'):
    """Keep the process alive after ingest ends: cull + heartbeat stats.

    Without this the server would exit the moment a finite pcap finished,
    dropping any connected operator client.
    """
    logger.info("Ingest ended; server idle (still serving HTTP/WS). Ctrl-C to exit.")
    last_cull = time.time()
    while not stop_event.is_set():
        last_cull = await _maybe_maintain(hub, last_cull)
        await asyncio.sleep(0.2)

def derive_track_id(msg: Dict) -> str:
    """Stable track key from a decoded message, tolerant of missing fields.

    Falls back through callsign -> ICAO addr -> sac.sic.tn, using placeholders
    so a position-only record (no I062/010 or I062/040) can never raise.
    """
    if msg.get('id'):
        return str(msg['id'])
    if msg.get('addr'):
        return str(msg['addr'])
    src = msg.get('src') or {}
    return f"{src.get('sac', '?')}.{src.get('sic', '?')}.{msg.get('tn', '?')}"

async def handle_frame(hub: WSHub, payload: bytes):
    """Process ASTERIX frame with multiple records."""
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

        try:
            rec = payload[i:i+length]
            dec = Asterix62(rec)
            hub.record_message(dec.ok)
            if dec.ok:
                msg = build_json(dec.items, now)
                if msg:
                    hub.update_track(derive_track_id(msg), msg)
                    await hub.broadcast_json(msg)
            else:
                logger.debug(f"Failed to parse CAT62 record: {dec.error_msg}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # One malformed record must never abort the rest of the frame.
            logger.debug(f"Skipping bad CAT62 record at offset {i}: {e}")

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

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass

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
    ap.add_argument('--loop', action='store_true', help='Replay the PCAP repeatedly (with --pcap)')
    ap.add_argument('--http-port', type=int, default=DEFAULT_HTTP_PORT, help='HTTP/API port')
    ap.add_argument('--ws-port', type=int, default=DEFAULT_WS_PORT, help='WebSocket port')
    ap.add_argument('--bind', default=DEFAULT_BIND, help='Bind address for HTTP+WS')
    ap.add_argument('--tile-url', default=os.environ.get('TILE_URL', DEFAULT_TILE_URL),
                    help='Map tile URL template served to the client (or a local path)')
    ap.add_argument('--verbose', action='store_true', help='Enable debug logging')

    args = ap.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate --udp early with a clear message rather than a raw traceback.
    udp_target = None
    if args.udp:
        try:
            host, port = args.udp.rsplit(':', 1)
            udp_target = (host, int(port))
        except ValueError:
            ap.error("--udp must be HOST:PORT (e.g. 0.0.0.0:31002)")

    hub = WSHub()
    logger.info("Radar CAT62 Parser starting...")

    stop_event = asyncio.Event()

    # Cross-platform signal handling: prefer the loop's handlers, fall back to
    # signal.signal (Windows has no add_signal_handler for SIGTERM).
    loop = asyncio.get_running_loop()
    # SIGBREAK covers the Windows console CTRL_BREAK / service-stop path.
    for sig in (signal.SIGINT, getattr(signal, 'SIGTERM', None),
                getattr(signal, 'SIGBREAK', None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, AttributeError, ValueError, RuntimeError):
            try:
                signal.signal(sig, lambda *_: stop_event.set())
            except (ValueError, OSError, AttributeError):
                pass

    # Start the WebSocket server. A busy port degrades to "no WebSocket"
    # rather than a silent dead task (the err.txt failure mode).
    try:
        hub.ws_server_obj = await ws_server(hub, args.ws_port, args.bind)
    except OSError as e:
        logger.error(f"WebSocket unavailable on {args.bind}:{args.ws_port} ({e}); "
                     f"continuing without live push")

    # HTTP server in a daemon thread.
    http_thread = threading.Thread(
        target=run_http,
        args=(hub, args.http_port, args.bind, args.ws_port, args.tile_url),
        daemon=True,
    )
    http_thread.start()

    # Ingest source, then idle-serve so the UI survives end-of-pcap.
    async def run_source():
        try:
            if udp_target is not None:
                await udp_loop(hub, udp_target, args.mcast, stop_event)
            else:
                await pcap_loop(hub, args.pcap, args.speed, args.loop, stop_event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Ingest error: {e}", exc_info=True)
        if not stop_event.is_set():
            await idle_serve(hub, stop_event)

    source_task = asyncio.create_task(run_source())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        await asyncio.wait({source_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_event.set()
        source_task.cancel()
        stop_task.cancel()
        await asyncio.gather(source_task, stop_task, return_exceptions=True)
        # Tear down servers cleanly.
        if hub.ws_server_obj is not None:
            hub.ws_server_obj.close()
            try:
                await hub.ws_server_obj.wait_closed()
            except Exception:
                pass
        if hub.httpd is not None:
            hub.httpd.shutdown()
        logger.info("Shutdown complete")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass