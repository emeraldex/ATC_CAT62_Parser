"""Round-trip tests: encode with the sample generator, decode with Asterix62.

These lock the generator<->decoder contract for the fields that actually carry
data (I062/010, 040, 105, 185). If either side's scaling/layout drifts, these
fail. (Scope note: this validates internal consistency for synthetic data, not
EUROCONTROL-spec compliance -- see SCOPE & LIMITATIONS in the README.)
"""
import math
import pytest

from parser_server import Asterix62, build_json, derive_track_id
from generate_sample_data import create_cat62_record


@pytest.mark.parametrize("lat,lon,speed,heading", [
    (51.5, -0.1, 350.0, 0.0),
    (3.1390, 101.6869, 480.0, 270.0),
    (-33.9, 151.2, 420.0, 135.0),
    (0.0, 0.0, 300.0, 45.0),
])
def test_position_velocity_roundtrip(lat, lon, speed, heading):
    rec = create_cat62_record(track_id=1, latitude=lat, longitude=lon,
                              speed=speed, heading=heading)
    dec = Asterix62(rec)
    assert dec.ok, dec.error_msg

    pos = dec.items['I062/105']['pos']
    # WGS-84 LSB is 180/2^23 deg; allow one LSB of quantization slack.
    lsb = 180.0 / (1 << 23)
    assert abs(pos['lat'] - lat) <= lsb * 2
    assert abs(pos['lon'] - lon) <= lsb * 2

    vel = dec.items['I062/185']
    # Ground speed round-trips within the 0.25 m/s LSB (~0.5 kt).
    assert abs(vel['gs'] - speed) < 1.0
    if speed > 0:
        # Heading within ~1 degree (velocity quantization).
        dh = abs((vel['hdg'] - heading + 180) % 360 - 180)
        assert dh < 1.5


def test_build_json_shape_from_generated_record():
    rec = create_cat62_record(2, 51.5, -0.1, 400.0, 90.0)
    dec = Asterix62(rec)
    msg = build_json(dec.items, ts=123.0)
    assert msg['type'] == 'track'
    assert set(('pos', 'gs', 'hdg', 'src', 'tn')).issubset(msg.keys())
    assert msg['src'] == {'sac': 0, 'sic': 10}
    # derive_track_id must produce a stable string key.
    assert isinstance(derive_track_id(msg), str)
