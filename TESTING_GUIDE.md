# Testing & Verification Guide

## Quick Start - PCAP Replay Test

### 1. Generate Sample PCAP Data
```bash
python generate_sample_data.py
```

Expected output:
```
Generating sample PCAP file: .../sample_radar.pcap
Generated 40024 bytes
Usage: python parser_server.py --pcap sample_radar.pcap
```

### 2. Start Parser Server with PCAP Replay
```bash
python parser_server.py --pcap sample_radar.pcap
```

You should see:
```
2026-07-06 10:51:53 [INFO] CAT62Parser: Radar CAT62 Parser starting...
2026-07-06 10:51:53 [INFO] CAT62Parser: Starting PCAP playback from sample_radar.pcap
2026-07-06 10:51:53 [INFO] CAT62Parser: WebSocket server on ws://0.0.0.0:8765
2026-07-06 10:51:53 [INFO] CAT62Parser: HTTP server on http://0.0.0.0:7878
```

### 3. Verify API Endpoints (in another terminal)

**Get Active Tracks:**
```bash
python test_api.py
```

Or using curl:
```bash
curl http://localhost:7878/api/tracks
curl http://localhost:7878/api/stats
curl http://localhost:7878/api/health
```

Expected response for `/api/tracks`:
```json
[
  {
    "track_id": "0.10.16",
    "sac": 0,
    "sic": 10,
    "track_num": 16,
    "pos_lat": 51.5,
    "pos_lon": -0.1,
    "ground_speed": 350.0,
    "heading": 0.0,
    "timestamp": 1783306340.38
  },
  ...
]
```

### 4. View Web Interface
Open browser to: **http://localhost:7878**

You should see:
- ✅ Map with aircraft markers
- ✅ Live track positions
- ✅ Connection status: "Connected"
- ✅ Track count: "10"
- ✅ Real-time statistics

## Troubleshooting

### No tracks appearing on web interface

1. **Check server is running:**
   ```bash
   curl -I http://localhost:7878/
   # Should return: HTTP/1.0 200 OK
   ```

2. **Check API returns tracks:**
   ```bash
   curl http://localhost:7878/api/tracks | python -m json.tool
   # Should return list of track objects
   ```

3. **Check WebSocket connection:**
   - Open browser developer tools (F12)
   - Go to Console tab
   - Should see: `"Connected to radar server"`

4. **Check parser logs:**
   ```bash
   # Look for error messages like:
   # [ERROR] Failed to parse...
   # [ERROR] Failed to open PCAP...
   ```

### Incorrect speed/heading values

**Before fix:** Speed > 2000 knots
**After fix:** Speed 300-600 knots (realistic)

If still seeing high values:
1. Regenerate PCAP: `python generate_sample_data.py`
2. Restart server
3. Clear browser cache: Ctrl+Shift+Delete

### PCAP file not found

```bash
# Verify file exists and is readable
ls -lh sample_radar.pcap

# Regenerate if needed
python generate_sample_data.py
```

## Performance Verification

### Parsing Speed
```bash
# Benchmark: 500 packets should parse in <1 second
time python parser_server.py --pcap sample_radar.pcap
# Real: 0.XX seconds
# User: 0.XX seconds
# Sys:  0.XX seconds
```

### Memory Usage
```bash
# Monitor while server is running
python -c "
import psutil
p = psutil.Process()
print(f'Memory: {p.memory_info().rss / 1024 / 1024:.1f} MB')
"
```

Expected: < 50 MB

### Concurrent Connections
```bash
# Test with multiple WebSocket clients
# Each client connects and receives track updates
# Server should handle 100+ concurrent clients
```

## Data Validation Tests

### Test 1: Position Accuracy
```bash
# Generated position for first track:
# Latitude: 51.5 degrees (London)
# Longitude: -0.1 degrees (London)

curl http://localhost:7878/api/tracks | grep -A5 "pos_lat"
# Should show: "pos_lat": 51.499... (very close to 51.5)
```

### Test 2: Speed Range
```bash
# Generated speeds: 350-550 knots
# Verify stats show sensible range

curl http://localhost:7878/api/stats | python -c "
import sys, json
data = json.load(sys.stdin)
print(f\"Avg: {data['avg_speed']:.1f} kt\")
print(f\"Max: {data['max_speed']:.1f} kt\")
# Expected: Avg ~450, Max ~550
"
```

### Test 3: Track Uniqueness
```bash
# Should have exactly 10 unique tracks (based on 500 packets / 50 per track)
curl http://localhost:7878/api/tracks | python -c "
import sys, json
tracks = json.load(sys.stdin)
print(f'Unique tracks: {len(set(t[\"track_id\"] for t in tracks))}')
# Expected: 10
"
```

### Test 4: Message Parsing Rate
```bash
# All messages should parse successfully (0 failures)
curl http://localhost:7878/api/stats | python -c "
import sys, json
data = json.load(sys.stdin)
rate = (data['messages_parsed'] / data['messages_received'] * 100) if data['messages_received'] > 0 else 0
print(f'Parse success rate: {rate:.1f}%')
print(f'Parsed: {data[\"messages_parsed\"]}/{data[\"messages_received\"]}')
# Expected: 100% success rate
"
```

## Unit Tests

### Run parser unit tests:
```bash
python -m pytest tests/test_parser.py -v
# Expected: 50+ tests pass
```

### Run integration tests:
```bash
python -m pytest tests/test_integration.py -v
# Expected: 30+ tests pass
```

### Test CAT62 parsing directly:
```bash
python test_pcap_parse.py
# Should show parsed CAT62 fields from first packet
```

## Production Readiness Checklist

- [ ] PCAP files generate without errors
- [ ] Parser server starts successfully  
- [ ] HTTP API responds on port 7878
- [ ] WebSocket server responds on port 8765
- [ ] Tracks appear on web interface with correct positions
- [ ] API returns 10+ active tracks
- [ ] Speed values are realistic (300-600 knots)
- [ ] Heading values span 0-360 degrees
- [ ] No error messages in server logs
- [ ] Browser console shows "Connected to radar server"
- [ ] Unit tests pass (50+ tests)
- [ ] Integration tests pass (30+ tests)

## Support

If issues persist after following this guide:

1. **Check logs:**
   ```bash
   # Capture full server output
   python parser_server.py --pcap sample_radar.pcap 2>&1 | tee server.log
   ```

2. **Enable verbose logging:**
   ```bash
   python parser_server.py --pcap sample_radar.pcap --verbose
   ```

3. **Run debug tools:**
   ```bash
   python debug_pcap.py      # Inspect PCAP structure
   python test_pcap_parse.py  # Test first CAT62 record
   python test_api.py         # Test API endpoints
   ```

4. **Check system:**
   ```bash
   # Verify ports are not in use
   netstat -an | grep 7878
   netstat -an | grep 8765
   ```
