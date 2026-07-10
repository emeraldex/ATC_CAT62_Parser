"""Frame-processing and message-building tests, including the crash regressions.

Covers handle_frame (multi-record, malformed, and the position-only KeyError
that used to abort the whole replay) and derive_track_id fallbacks.
"""
import asyncio
import struct

import pytest

from parser_server import (
    Asterix62, WSHub, handle_frame, build_json, derive_track_id,
)
from generate_sample_data import create_cat62_record


def position_only_record(lat=51.5, lon=-0.1):
    """A CAT62 record carrying ONLY I062/105 (no I062/010, no I062/040).

    FSPEC: octet1=0x01 (FX only) -> octet2=0x20 (bit5 = I062/105).
    This is exactly the shape that triggered the old KeyError in handle_frame.
    """
    lsb = 180.0 / (1 << 23)
    body = struct.pack('>i', int(lat / lsb)) + struct.pack('>i', int(lon / lsb))
    length = 3 + 2 + len(body)  # cat+len(3) + fspec(2) + body
    return bytes([62]) + struct.pack('>H', length) + bytes([0x01, 0x20]) + body


# ---- derive_track_id -------------------------------------------------------

def test_derive_track_id_prefers_callsign():
    assert derive_track_id({'id': 'ABC123', 'addr': 'AABBCC'}) == 'ABC123'


def test_derive_track_id_uses_addr():
    assert derive_track_id({'addr': 'AABBCC'}) == 'AABBCC'


def test_derive_track_id_composite():
    assert derive_track_id({'src': {'sac': 0, 'sic': 10}, 'tn': 5}) == '0.10.5'


def test_derive_track_id_position_only_never_raises():
    # No id/addr/src/tn at all -> placeholder key, no exception.
    assert derive_track_id({'pos': {'lat': 1.0, 'lon': 2.0}}) == '?.?.?'


# ---- build_json ------------------------------------------------------------

def test_build_json_position_only():
    dec = Asterix62(position_only_record())
    assert dec.ok, dec.error_msg
    msg = build_json(dec.items, ts=1.0)
    assert msg is not None
    assert 'pos' in msg and 'src' not in msg and 'tn' not in msg


def test_build_json_none_without_position():
    # Only I062/010 -> no position -> build_json returns None (nothing to plot).
    rec = bytes([62, 0, 6, 0x80, 1, 2])  # cat, len=6, fspec 0x80 (I062/010), sac,sic
    dec = Asterix62(rec)
    assert dec.ok
    assert build_json(dec.items, ts=1.0) is None


# ---- handle_frame (async) --------------------------------------------------

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_handle_frame_position_only_no_crash():
    """The old KeyError regression: a position-only frame must be ingested."""
    hub = WSHub()
    run(handle_frame(hub, position_only_record()))
    assert len(hub.snapshot_tracks()) == 1
    active, _, _ = hub.counts()
    assert active == 1


def test_handle_frame_multi_record():
    hub = WSHub()
    frame = (create_cat62_record(1, 51.5, -0.1, 400, 90)
             + create_cat62_record(2, 52.0, 0.2, 300, 180)
             + create_cat62_record(3, 50.5, -1.0, 500, 270))
    run(handle_frame(hub, bytes(frame)))
    assert len(hub.snapshot_tracks()) == 3


def test_handle_frame_malformed_no_crash():
    hub = WSHub()
    for junk in (b'', b'\x00\x01\x02', b'\x3e\x00\x40\x91\x21', b'\xff' * 40):
        run(handle_frame(hub, junk))  # must not raise
    # Garbage should not have produced phantom tracks.
    assert len(hub.snapshot_tracks()) == 0


def test_handle_frame_truncated_record_then_valid_stops_cleanly():
    # Valid record followed by a truncated one: valid is ingested, no crash.
    good = create_cat62_record(7, 51.0, 0.0, 350, 45)
    frame = bytes(good) + bytes([62, 0xFF, 0xFF])  # bogus length
    hub = WSHub()
    run(handle_frame(hub, frame))
    assert len(hub.snapshot_tracks()) == 1
