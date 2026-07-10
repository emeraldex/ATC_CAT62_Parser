"""Regression for the unlocked-shared-state race.

Before WSHub locking, the HTTP thread iterating tracks while the event loop
mutated/culled them raised `RuntimeError: dictionary changed size during
iteration`. This hammers snapshot reads against concurrent writes + culls and
asserts no exception escapes.
"""
import threading
import time

from parser_server import WSHub


def test_snapshot_under_concurrent_mutation():
    hub = WSHub()
    errors = []
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            hub.update_track(f"T{i % 200}", {
                'pos': {'lat': 51.0 + (i % 10) * 0.01, 'lon': (i % 10) * 0.01},
                'gs': 100 + (i % 50), 'hdg': i % 360,
                'src': {'sac': 0, 'sic': 10}, 'tn': i % 200,
                'ts': time.time(),
            })

    def culler():
        while not stop.is_set():
            hub.cull_stale_tracks(timeout=0.0)  # aggressive: everything is stale

    def reader():
        while not stop.is_set():
            try:
                hub.snapshot_tracks()
                hub.stats_snapshot()
                hub.counts()
            except Exception as e:  # the race would surface here
                errors.append(e)
                return

    threads = [threading.Thread(target=writer),
               threading.Thread(target=culler),
               threading.Thread(target=reader),
               threading.Thread(target=reader)]
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"Concurrent access raised: {errors[:3]}"
