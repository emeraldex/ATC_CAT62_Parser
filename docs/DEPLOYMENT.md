# Radar CAT62 Parser - Deployment Guide

> ⚠️ **Non-operational tool.** Deploy on a trusted network for demo/training/dev
> only. It is not certified for operational ATC and has no auth/TLS. See
> **SCOPE & LIMITATIONS** in the [README](../README.md). Some older details in
> this guide (log-file paths, tunables) may be superseded — the README,
> `deploy/Dockerfile`, `deploy/docker-compose.yml`, and
> `deploy/cat62-parser.service` are the source of truth.

## Prerequisites

- Python 3.10+
- pip/pip3
- Network access to radar data source or PCAP files (trusted network only)

## Installation

```bash
# Clone or download the project
cd ATC_CAT62_Parser

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On Linux/Mac:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install websockets

# Optional: Install additional tools
pip install colorama  # For colored logging
```

## Quick Start

### UDP Mode (Live Radar Feed)

**Unicast:**
```bash
python parser_server.py --udp 0.0.0.0:31002
```

**Multicast:**
```bash
python parser_server.py --udp 224.0.0.1:31002 --mcast 224.0.0.1
```

Then open browser: `http://localhost:7878`

### PCAP Playback Mode

```bash
# Playback at normal speed
python parser_server.py --pcap capture.pcap

# Playback at 2x speed
python parser_server.py --pcap capture.pcap --speed 2.0

# Playback at 0.5x speed (slow motion)
python parser_server.py --pcap capture.pcap --speed 0.5
```

## Configuration

Edit `parser_server.py` constants:

```python
WS_PORT = 8765           # WebSocket port
HTTP_PORT = 7878         # Web UI port
MAX_TRACK_HISTORY = 300  # Points per track
TRACK_TIMEOUT = 30       # Stale track timeout (seconds)
STATS_INTERVAL = 5       # Stats reporting interval
```

## API Endpoints

### Get All Tracks
```bash
curl http://localhost:7878/api/tracks
```

Response:
```json
[
  {
    "track_id": "ABC123",
    "pos_lat": 3.1390,
    "pos_lon": 101.6869,
    "callsign": "ABC123",
    "ground_speed": 450.5,
    "heading": 270.0,
    "mode3a": "1234",
    "timestamp": 1234567890.123
  }
]
```

### Get Statistics
```bash
curl http://localhost:7878/api/stats
```

Response:
```json
{
  "messages_received": 15420,
  "messages_parsed": 15380,
  "messages_failed": 40,
  "tracks_active": 42,
  "avg_speed": 425.3,
  "max_speed": 550.2,
  "avg_heading": 185.5
}
```

### Health Check
```bash
curl http://localhost:7878/api/health
```

Response:
```json
{
  "status": "healthy",
  "connected_clients": 3,
  "active_tracks": 42,
  "timestamp": 1234567890.123
}
```

## WebSocket Message Format

### Track Update
```json
{
  "type": "track",
  "ts": 1234567890.123,
  "id": "ABC123",
  "addr": "A1B2C3",
  "m3a": "1234",
  "pos": {"lat": 3.1390, "lon": 101.6869},
  "gs": 450.5,
  "hdg": 270.0,
  "src": {"sac": 1, "sic": 10},
  "tn": 42,
  "status": {"conf": true, "man": false, "dup": false}
}
```

### Statistics
```json
{
  "type": "stats",
  "data": {
    "messages_received": 15420,
    "messages_parsed": 15380,
    "avg_speed": 425.3
  }
}
```

## PCAP File Format

Supported formats:
- PCAP (libpcap) `.pcap`
- PCAP-NG `.pcapng` (may require conversion)

### Convert PCAP-NG to PCAP
```bash
# Using editcap (Wireshark)
editcap -F pcap input.pcapng output.pcap
```

### Create Test PCAP
```bash
# Using tcpdump
sudo tcpdump -i eth0 -w capture.pcap udp port 31002
```

## Systemd Service (Linux)

Create `/etc/systemd/system/cat62-parser.service`:

```ini
[Unit]
Description=Radar CAT62 Parser
After=network.target

[Service]
Type=simple
User=radar
WorkingDirectory=/opt/cat62-parser
ExecStart=/opt/cat62-parser/venv/bin/python parser_server.py --udp 0.0.0.0:31002
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable cat62-parser
sudo systemctl start cat62-parser
sudo systemctl status cat62-parser
```

## Docker Deployment

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install websockets

EXPOSE 7878 8765 31002/udp

CMD ["python", "parser_server.py", "--udp", "0.0.0.0:31002"]
```

Build and run:
```bash
docker build -t cat62-parser .
docker run -p 7878:7878 -p 8765:8765 -p 31002:31002/udp cat62-parser
```

## Troubleshooting

### WebSocket Connection Fails
- Check firewall allows port 8765
- Verify parser is running: `lsof -i :8765`
- Check browser console for errors

### No Tracks Appearing
- Verify UDP data is arriving: `tcpdump -i eth0 udp port 31002`
- Check parser logs for parse errors: `--verbose` flag
- Verify PCAP file contains valid CAT62 data

### High CPU Usage
- Reduce track history: `MAX_TRACK_HISTORY = 100`
- Increase track timeout: `TRACK_TIMEOUT = 60`
- Check for malformed packets in logs

### Memory Growth
- Monitor with: `top`, `ps aux | grep python`
- Verify stale track culling works
- Check for connection leaks in WebSocket

## Performance Tuning

### For High Message Rate (>5000/sec)
```python
MAX_TRACK_HISTORY = 50   # Reduce history
TRACK_TIMEOUT = 15       # Shorter timeout
STATS_INTERVAL = 10      # Less frequent stats
```

### For Low Latency
```python
TRACK_TIMEOUT = 5        # Quick cleanup
STATS_INTERVAL = 2       # Fast updates
```

### For High Volume Clients (>50)
- Use reverse proxy (nginx)
- Enable compression
- Run multiple parser instances

## Monitoring

### Log Monitoring
```bash
# Real-time logs
tail -f logs/cat62.log

# Search for errors
grep ERROR logs/cat62.log

# Count parsed messages
grep "parsed" logs/cat62.log | wc -l
```

### Performance Metrics
```bash
# Check memory
ps aux | grep python | head -1

# Monitor CPU
top -p $(pgrep -f parser_server)

# Connection count
netstat -an | grep 31002
```

## Backup and Recovery

### Backup Configuration
```bash
cp parser_server.py parser_server.py.backup
cp -r client client.backup
```

### PCAP Archival
```bash
# Compress PCAP files
gzip capture.pcap

# Backup to external storage
rsync -av *.pcap /backup/radar/
```

## Security Considerations

1. **Firewall**: Restrict UDP/WebSocket access
2. **Authentication**: Add reverse proxy auth layer
3. **TLS/SSL**: Use wss:// for WebSocket encryption
4. **Rate Limiting**: Implement at reverse proxy
5. **Data Privacy**: Encrypt stored PCAP files

## Support & Diagnostics

Enable verbose logging:
```bash
python parser_server.py --udp 0.0.0.0:31002 --verbose
```

Collect diagnostics:
```bash
# System info
uname -a
python --version
pip list

# Port status
netstat -tlnp | grep -E '31002|7878|8765'

# Recent logs
tail -100 logs/cat62.log
```
