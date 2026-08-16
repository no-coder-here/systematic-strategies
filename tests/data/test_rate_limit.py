"""Proactive rate limiting — fixes the self-reported v1.0 gap."""

from __future__ import annotations

import threading

import pytest

from data.rate_limit import RateLimiter


def test_first_call_never_waits():
    clock = [0.0]
    limiter = RateLimiter(1.0, time_fn=lambda: clock[0], sleep_fn=lambda s: None)
    assert limiter.wait() == 0.0


def test_second_call_waits_remaining_interval():
    clock = [0.0]
    slept = []

    def sleep_fn(s):
        slept.append(s)
        clock[0] += s

    limiter = RateLimiter(1.0, time_fn=lambda: clock[0], sleep_fn=sleep_fn)
    limiter.wait()
    clock[0] += 0.4  # only 0.4s elapsed, need 0.6s more
    slept_amount = limiter.wait()
    assert slept_amount == pytest.approx(0.6)
    assert slept == [pytest.approx(0.6)]


def test_no_wait_when_interval_already_elapsed():
    clock = [0.0]
    limiter = RateLimiter(1.0, time_fn=lambda: clock[0], sleep_fn=lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))
    limiter.wait()
    clock[0] += 2.0  # well past the interval
    assert limiter.wait() == 0.0


def test_zero_interval_never_sleeps():
    limiter = RateLimiter(0.0, sleep_fn=lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))
    limiter.wait()
    limiter.wait()  # would raise if it ever called sleep_fn


def test_rejects_negative_interval():
    with pytest.raises(ValueError):
        RateLimiter(-1.0)


def test_thread_safe_shared_across_threads():
    """The Binance bulk download shares ONE `RateLimiter` across a thread
    pool; this proves concurrent `.wait()` calls don't corrupt `_last_call`
    (e.g. via an unguarded read-modify-write race).
    """
    clock = [0.0]
    lock = threading.Lock()

    def time_fn():
        with lock:
            return clock[0]

    limiter = RateLimiter(0.01, time_fn=time_fn, sleep_fn=lambda s: None)
    errors = []

    def worker():
        try:
            for _ in range(50):
                limiter.wait()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
