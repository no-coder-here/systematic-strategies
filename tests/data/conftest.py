from __future__ import annotations

import json
from typing import Callable, Optional

import pandas as pd
import pytest

from data.hyperliquid.client import HyperliquidClient
from data.rate_limit import RateLimiter

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
FOUR_HOUR_MS = 4 * HOUR_MS

BAR_MS = {"1h": HOUR_MS, "4h": FOUR_HOUR_MS, "1d": DAY_MS}


def candle(t_ms: int, o: float, h: float, l: float, c: float, v: float, n: int, interval: str = "1h",
           coin: str = "BTC") -> dict:
    delta = BAR_MS[interval]
    return {
        "t": t_ms,
        "T": t_ms + delta - 1,
        "s": coin,
        "i": interval,
        "o": str(o),
        "h": str(h),
        "l": str(l),
        "c": str(c),
        "v": str(v),
        "n": n,
    }


def funding_event(t_ms: int, rate: float, premium: float = 0.0001, coin: str = "BTC") -> dict:
    return {"coin": coin, "fundingRate": str(rate), "premium": str(premium), "time": t_ms}


class ScriptedTransport:
    """A deterministic, OFFLINE transport for `HyperliquidClient` (D§11.1).

    `handler(payload: dict) -> object` decides the response for each request;
    it NEVER touches the network. Every call is recorded in `.calls` so tests
    can assert the mock transport was exercised as expected and never bypassed.
    """

    def __init__(self, handler: Callable[[dict], object]):
        self._handler = handler
        self.calls: list = []

    def __call__(self, body: bytes) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        self.calls.append(payload)
        result = self._handler(payload)
        if isinstance(result, Exception):
            raise result
        return json.dumps(result).encode("utf-8")


@pytest.fixture
def scripted_client():
    def _make(handler: Callable[[dict], object]) -> tuple:
        transport = ScriptedTransport(handler)
        client = HyperliquidClient(
            transport=transport, max_retries=2, backoff_base_seconds=0.0, rate_limiter=RateLimiter(0.0)
        )
        return client, transport

    return _make


class MultiEndpointTransport:
    """Routes a scripted `HyperliquidClient` by request `type` + (for candles)
    `interval`, returning windowed slices of pre-registered per-interval
    candle/funding/meta fixtures. Every call is recorded for assertion.
    """

    def __init__(self, candles: Optional[dict] = None, funding: Optional[list] = None, meta: Optional[dict] = None):
        # candles: {interval: {t_ms: candle_dict}}
        self.candles = candles or {}
        self.funding = sorted(funding or [], key=lambda e: e["time"])
        self.meta = meta
        self.calls: list = []

    def __call__(self, body: bytes) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        self.calls.append(payload)
        result = self._route(payload)
        if isinstance(result, Exception):
            raise result
        return json.dumps(result).encode("utf-8")

    def _route(self, payload: dict):
        if payload["type"] == "meta":
            if self.meta is None:
                raise AssertionError("no meta fixture registered")
            return self.meta
        if payload["type"] == "candleSnapshot":
            req = payload["req"]
            interval = req["interval"]
            start_ms, end_ms = req["startTime"], req["endTime"]
            by_t = self.candles.get(interval, {})
            window = [bar for t, bar in by_t.items() if start_ms <= t <= end_ms]
            window.sort(key=lambda b: b["t"])
            return window
        if payload["type"] == "fundingHistory":
            start_ms, end_ms = payload["startTime"], payload["endTime"]
            return [e for e in self.funding if start_ms <= e["time"] <= end_ms]
        raise AssertionError(f"unexpected request type {payload['type']!r}")


@pytest.fixture
def multi_client():
    def _make(candles: Optional[dict] = None, funding: Optional[list] = None, meta: Optional[dict] = None):
        transport = MultiEndpointTransport(candles=candles, funding=funding, meta=meta)
        client = HyperliquidClient(
            transport=transport, max_retries=2, backoff_base_seconds=0.0, rate_limiter=RateLimiter(0.0)
        )
        return client, transport

    return _make


@pytest.fixture
def raising_transport():
    """A transport that raises on ANY call — proves offline reload never
    touches the network (D§8.3) and that transport exceptions propagate
    rather than being swallowed into an empty dataset (D§7, M13).
    """

    def _transport(body: bytes) -> bytes:
        raise AssertionError("network transport MUST NOT be called (D§8.3 / offline mode)")

    return _transport
