# Testing Guide

## Automated suite (pytest)

```bash
pip install -r requirements-dev.txt
python -m pytest              # from the repo root
```

Configuration lives in `pytest.ini` (`testpaths = tests`). The tests are pure
unit/integration tests — they do **not** require a running server or network.

### What's covered

| File | Focus |
|------|-------|
| `tests/test_parser.py` | Byte helpers, IA-5, minimal record parse, stats, `TrackData` |
| `tests/test_decoder_roundtrip.py` | Generator ↔ decoder round-trip for I062/105 position and I062/185 velocity (locks the synthetic-data contract) |
| `tests/test_pipeline.py` | `handle_frame` (multi-record, malformed, and the **position-only** record that used to `KeyError`), `build_json`, `derive_track_id` |
| `tests/test_http_api.py` | `/api/tracks`, `/api/stats`, `/api/config`, `/api/health` on an ephemeral port |
| `tests/test_concurrency.py` | `WSHub` locking — hammers snapshot reads against concurrent writes + culls (regression for `dictionary changed size`) |
| `tests/test_integration.py` | End-to-end frame ingest into the hub, track lifecycle, error recovery |

## Manual / runtime checks

```bash
python generate_sample_data.py
python parser_server.py --pcap sample_radar.pcap --loop --verbose
```

- **Web UI:** open http://localhost:7878 — markers should appear and move, the
  connection dot should be green, and stats should populate.
- **API:** `curl http://localhost:7878/api/tracks` (and `/api/stats`,
  `/api/health`, `/api/config`).
- **Custom ports:** `--ws-port 9001 --http-port 8001` — the client reads
  `/api/config` and connects to the right WS port automatically.
- **Offline basemap:** with tiles unreachable, the map shows a coordinate
  graticule instead of a blank page (Leaflet itself is vendored locally).
- **Lifecycle:** after the PCAP finishes the server keeps serving; Ctrl‑C shuts
  down cleanly with no traceback.

## Scope reminder

These tests verify the software behaves correctly **for synthetic data**. They
do not — and cannot, without real captures and the authoritative spec — verify
EUROCONTROL CAT62 conformance. See SCOPE & LIMITATIONS in the README.
