# Robustness Hardening Pass

This document records the robustness work done on top of the original
prototype. Scope was **robustness only** — the ASTERIX decoder semantics were
deliberately left unchanged (it remains matched to `generate_sample_data.py`,
not the EUROCONTROL spec; see the SCOPE note in the README).

## What changed

### Concurrency (crash: `RuntimeError: dictionary changed size`)
`WSHub` now guards `tracks`/`stats`/`clients` with a single lock. The HTTP
thread reads via snapshot helpers (`snapshot_tracks`, `stats_snapshot`,
`counts`) that copy under the lock and serialize/write outside it. The lock is
never held across an `await` or a socket write, so the event loop can't stall on
a slow HTTP client, and there's no deadlock path.

### Crash paths
- `handle_frame` derives the track id via `derive_track_id()` with `.get()`
  fallbacks — a position-only record (no I062/010 / I062/040) no longer raises
  `KeyError` and aborts the replay. Each record is processed in its own
  try/except so one bad record can't kill the frame.
- `udp_loop` socket setup (bind / multicast join) is inside try/except and
  closes the socket in `finally`; a persistent receive error backs off instead
  of hot-looping.
- `ws_server` bind failure (e.g. port in use) is caught and logged — the app
  keeps serving HTTP instead of a silent dead task.
- `run_http` bind failure is caught; the server sets `allow_reuse_address` and
  tolerates client disconnects mid-response.

### Lifecycle
- The server **stays alive after a finite PCAP ends** (`idle_serve`), so a
  connected operator client isn't dropped. `--loop` replays continuously.
- SIGINT/SIGTERM trigger a graceful shutdown (cancel ingest, close the WS
  server, shut down the HTTP server, close sockets and the PCAP file).
  Cross-platform: falls back to `signal.signal` where `add_signal_handler`
  is unsupported (Windows).

### Config
- Ports and bind address are CLI flags (`--http-port`, `--ws-port`, `--bind`)
  and no longer hardcoded globals. New `/api/config` lets the browser client
  discover the WebSocket port at runtime.

### Web client (was completely non-functional)
- Fixed the WebSocket endpoint: the client now reads `/api/config` and connects
  to the correct WS port (previously it hit `/ws` on the HTTP port — a route
  that never existed).
- Fixed the message protocol: the client now consumes the server's actual
  `type:'track'` per-track messages (`pos.lat/lon`, `gs`, `hdg`) instead of a
  `type:'tracks'` array shape the server never sent. Markers now actually plot.
- Real wall-clock staleness culling replaces the previous no-op.
- Leaflet is vendored locally (`client/vendor/leaflet/`) for air-gapped use;
  the basemap degrades to a coordinate graticule when tiles are unreachable.
- Removed the ~300 lines of shadowed, drifted embedded client strings from
  `parser_server.py` (the on-disk `client/` is now the single source of truth).

### Ops
- `websockets` pinned in `requirements.txt`; Dockerfile installs from it.
- `docker-compose.yml`: resource limits moved under `deploy.resources` (Compose
  v2 was ignoring the old top-level key); obsolete `version` dropped; dangling
  `/app/logs` mount removed; `LOG_LEVEL` is now honored by the app.
- `/api/health` reports real liveness (WS up + time since last frame), `503`
  when degraded, instead of a static `"healthy"`.
- systemd unit: removed the SIGHUP `ExecReload` footgun, added `TimeoutStopSec`,
  cgroup‑v2 `MemoryMax`.

### Tests
Added a real pytest suite (`tests/`) covering the decode round-trip, frame
processing (malformed + the position-only regression), the HTTP API, and the
concurrency locking. Removed the print-only pseudo-test scripts.

## What was intentionally NOT done
- No spec-compliant decoder rewrite (no real captures / spec to validate
  against, and the data is synthetic).
- No authentication / TLS.
- No safety certification — this remains a non-operational tool.
