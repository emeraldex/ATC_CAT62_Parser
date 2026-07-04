# Quick Start Guide

## 30-Second Setup

```bash
# 1. Enter directory
cd ATC_CAT62_Parser

# 2. Create environment
python3 -m venv venv
source venv/bin/activate

# 3. Install
pip install websockets

# 4. Run with mock/test data
python parser_server.py --pcap test_data.pcap
```

Then open: **http://localhost:7878**

## Real Deployment (UDP Feed)

### Single Radar Source
```bash
python parser_server.py --udp 0.0.0.0:31002
```
- Listens on port 31002
- Opens web UI at http://localhost:7878
- Broadcasts to WebSocket clients

### Multicast Group
```bash
python parser_server.py --udp 224.0.0.1:31002 --mcast 224.0.0.1
```

### Debug Mode
```bash
python parser_server.py --udp 0.0.0.0:31002 --verbose
```
Shows detailed parsing information

## Key Ports

| Port | Purpose |
|------|---------|
| **7878** | Web UI (HTTP) |
| **8765** | WebSocket real-time data |
| **31002** | UDP radar input (default) |

## API Quick Reference

```bash
# Get active tracks
curl http://localhost:7878/api/tracks

# Get statistics
curl http://localhost:7878/api/stats

# Health check
curl http://localhost:7878/api/health
```

## WebSocket Integration

```javascript
// Connect in browser
const ws = new WebSocket('ws://localhost:8765');

// Listen for track updates
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Track:', data);
};
```

## PCAP Replay

```bash
# Normal speed
python parser_server.py --pcap capture.pcap

# Slow motion (0.5x)
python parser_server.py --pcap capture.pcap --speed 0.5

# Fast forward (2x)
python parser_server.py --pcap capture.pcap --speed 2.0
```

## Systemd Service (Linux)

Create `/etc/systemd/system/cat62.service`:

```ini
[Unit]
Description=CAT62 Radar Parser
After=network.target

[Service]
Type=simple
User=radar
WorkingDirectory=/opt/cat62
ExecStart=/opt/cat62/venv/bin/python parser_server.py --udp 0.0.0.0:31002
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable:
```bash
sudo systemctl enable cat62
sudo systemctl start cat62
sudo systemctl status cat62
```

## Docker

```bash
# Build
docker build -t cat62 .

# Run
docker run -p 7878:7878 -p 8765:8765 -p 31002:31002/udp cat62
```

## Configuration

Edit `parser_server.py`:

```python
WS_PORT = 8765              # WebSocket port
HTTP_PORT = 7878            # HTTP port
MAX_TRACK_HISTORY = 300     # Points per track
TRACK_TIMEOUT = 30          # Stale track timeout
STATS_INTERVAL = 5          # Stats reporting interval
```

## Troubleshooting

### No tracks appear
```bash
# Check UDP data arriving
tcpdump -i eth0 udp port 31002

# Run with verbose logging
python parser_server.py --udp 0.0.0.0:31002 --verbose
```

### WebSocket connection fails
```bash
# Check parser is running
lsof -i :8765

# Verify firewall
sudo ufw allow 8765
```

### Memory grows
- Check stale track culling: `logger` output
- Reduce `MAX_TRACK_HISTORY`
- Increase `TRACK_TIMEOUT`

## Features Checklist

- ✅ Real-time map visualization
- ✅ Live aircraft tracking
- ✅ Multiple data sources
- ✅ REST API
- ✅ WebSocket streaming
- ✅ Performance statistics
- ✅ Error handling
- ✅ PCAP replay
- ✅ Production logging
- ✅ Systemd support

## Documentation Files

- **README.md** - Full documentation
- **FEATURES.md** - Feature details
- **DEPLOYMENT.md** - Deployment guide
- **ASTERIX_REFERENCE.md** - CAT62 reference
- **ENHANCEMENT_SUMMARY.md** - What's new

## Support

See **DEPLOYMENT.md** for:
- Detailed installation
- Advanced configuration
- Performance tuning
- Security setup
- Monitoring
- Backup procedures

---

**Ready to use!** 🚀  
For questions, see documentation files.
