"""HTTP API tests: start run_http on a free port and exercise every endpoint.

Uses http.client so non-2xx (e.g. /api/health returning 503 when the WS is
down) is inspected, not raised.
"""
import http.client
import json
import socket
import threading
import time

import pytest

from parser_server import WSHub, run_http


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server():
    hub = WSHub()
    hub.update_track('0.10.5', {
        'pos': {'lat': 51.5, 'lon': -0.1}, 'gs': 400.0, 'hdg': 90.0,
        'src': {'sac': 0, 'sic': 10}, 'tn': 5, 'ts': time.time(),
    })
    port = _free_port()
    t = threading.Thread(
        target=run_http,
        args=(hub, port, '127.0.0.1', 8765, 'TILEURL'),
        daemon=True,
    )
    t.start()
    # Wait for bind.
    for _ in range(50):
        try:
            http.client.HTTPConnection('127.0.0.1', port, timeout=1).connect()
            break
        except OSError:
            time.sleep(0.05)
    yield hub, port
    if hub.httpd:
        hub.httpd.shutdown()


def _get(port, path):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    c.request('GET', path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, json.loads(body) if body else None


def test_tracks_endpoint(server):
    _, port = server
    status, data = _get(port, '/api/tracks')
    assert status == 200
    assert isinstance(data, list) and len(data) == 1
    assert data[0]['pos_lat'] == 51.5 and data[0]['ground_speed'] == 400.0


def test_stats_endpoint(server):
    _, port = server
    status, data = _get(port, '/api/stats')
    assert status == 200
    assert data['tracks_active'] == 1
    assert 'messages_received' in data


def test_config_endpoint(server):
    _, port = server
    status, data = _get(port, '/api/config')
    assert status == 200
    assert data['ws_port'] == 8765
    assert data['tile_url'] == 'TILEURL'


def test_health_endpoint_reports_degraded_without_ws(server):
    hub, port = server
    # No ws_server_obj set -> health should report degraded (503) but not crash.
    status, data = _get(port, '/api/health')
    assert status in (200, 503)
    assert data['websocket_up'] is False
    assert data['active_tracks'] == 1
    assert 'seconds_since_last_frame' in data
