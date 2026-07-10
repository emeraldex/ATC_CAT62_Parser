# CAT62 Parser

A Python tool that decodes ASTERIX **CAT62**-style radar track messages and
visualizes them live on a web map. It ingests either a UDP feed or a PCAP
replay, decodes tracks, and streams them to a browser client over WebSocket.

> ## ⚠️ SCOPE & LIMITATIONS — read first
>
> **This is a demonstration / training / development tool. It is NOT certified
> or suitable for operational air traffic control.** Operational ATC
> surveillance software requires formal safety assurance (e.g. EUROCONTROL /
> DO‑278A / ED‑109A), independent verification & validation, redundancy, and
> validation against real radar — none of which this project provides.
>
> Specific technical limitations you must understand:
>
> - **The decoder is matched to this repo's own sample generator
>   (`generate_sample_data.py`), not to the real EUROCONTROL CAT62 UAP.** The
>   FSPEC/field ordering and LSB scaling are internally self-consistent for
>   synthetic data. **On a real radar feed it will mis-decode / desync.** Do not
>   point it at live operational data and trust the output.
> - **Only a few fields carry data:** I062/010 (SAC/SIC), I062/040 (track
>   number), I062/105 (WGS‑84 position), I062/185 (velocity → ground speed /
>   heading). Altitude, Mode 3/A, and callsign are **not** meaningfully decoded.
> - No authentication, authorization, or TLS. Bind to a trusted network only.
>
> This pass hardened robustness (crash-safety, concurrency, lifecycle, offline
> UI, tests) — see [HARDENING.md](HARDENING.md). It did **not** make the decoder
> spec-compliant.

## Requirements

- Python 3.10+
- `websockets` (see `requirements.txt`)

## Install

```bash
python -m venv .venv
. .venv/Scripts/activate        # Windows;  use  source .venv/bin/activate  on Linux/macOS
pip install -r requirements.txt
```

## Run

Generate a sample capture, then replay it:

```bash
python generate_sample_data.py            # writes sample_radar.pcap
python parser_server.py --pcap sample_radar.pcap --loop
```

Or listen on a UDP feed (synthetic sender that matches the generator):

```bash
python parser_server.py --udp 0.0.0.0:31002
```

Then open **http://localhost:7878** in a browser.

### Options

| Flag | Default | Purpose |
|------|---------|---------|
| `--pcap FILE` | — | Replay a PCAP capture |
| `--udp HOST:PORT` | — | Bind a UDP ingest socket |
| `--mcast GROUP` | — | Join a multicast group (with `--udp`) |
| `--speed N` | `1.0` | PCAP playback speed multiplier |
| `--loop` | off | Replay the PCAP repeatedly |
| `--http-port N` | `7878` | HTTP/API + web UI port |
| `--ws-port N` | `8765` | WebSocket port |
| `--bind ADDR` | `0.0.0.0` | Bind address for HTTP + WS |
| `--tile-url URL` | OSM | Map tile template served to the client |
| `--verbose` | off | Debug logging (also `LOG_LEVEL=DEBUG`) |

The server keeps serving after a finite PCAP finishes (tracks age out); press
Ctrl‑C (or send SIGTERM) for a clean shutdown.

## HTTP API

| Endpoint | Returns |
|----------|---------|
| `GET /api/tracks` | Current tracks (array) |
| `GET /api/stats` | Message/speed/heading statistics |
| `GET /api/config` | `{ws_port, http_port, tile_url}` (the client reads this to find the WS port) |
| `GET /api/health` | Liveness: `200` when the WS is up and frames are recent, `503` when degraded |

## Offline / air-gapped use

Leaflet is **vendored** under `client/vendor/leaflet/`, so the UI loads with no
internet. Map **tiles** still come from the `--tile-url` (OpenStreetMap by
default); when tiles are unreachable the map falls back to a coordinate
graticule so track markers stay usable. To use a local basemap, point
`--tile-url` at a reachable local tile server / path.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite covers the decode round-trip (generator ↔ decoder), frame processing
(including malformed and position-only records), the HTTP API, and the
WSHub concurrency locking. See [TESTING_GUIDE.md](TESTING_GUIDE.md).

## Deployment

`Dockerfile` + `docker-compose.yml` (Compose v2) build a non-root container with
a real healthcheck; `cat62-parser.service` is a hardened systemd unit. Pin the
runtime via `requirements.txt`. Again: **trusted networks only, non-operational
use.**

## Layout

```
parser_server.py        # server: decoder, UDP/PCAP ingest, WS hub, HTTP API
generate_sample_data.py # synthetic CAT62 PCAP generator (defines the decoder contract)
client/                 # web UI (index.html, app.js, style.css, vendor/leaflet)
tests/                  # pytest suite
```
