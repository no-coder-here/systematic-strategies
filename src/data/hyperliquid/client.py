"""D§2 — thin HTTP client: endpoints, pagination, retry, rate limit.

Uses only the Python standard library (`urllib`) — no `requests` dependency.
Talks exclusively to the public, read-only `info` endpoint. Never touches
authenticated/account endpoints, never places orders (repo-wide safety rule).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from ..rate_limit import RateLimiter

__all__ = ["HyperliquidAPIError", "HyperliquidClient", "INFO_URL"]

INFO_URL = "https://api.hyperliquid.xyz/info"


class HyperliquidAPIError(Exception):
    """D§7 `API_FAILURE` — transport error, non-200, malformed body, or schema
    violation. MUST propagate; MUST NOT be silently swallowed into an empty
    result (D§7, M13).
    """


class HyperliquidClient:
    """Thin, retrying HTTP client for the public `info` endpoint.

    `transport`, if given, is an injectable seam for OFFLINE, deterministic
    tests (D§11.1): a callable taking the raw JSON request-body bytes and
    returning raw JSON response-body bytes (or raising to simulate a
    transport failure). Production code leaves this `None` and uses `urllib`.
    """

    def __init__(
        self,
        base_url: str = INFO_URL,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        timeout_seconds: float = 10.0,
        transport: Optional[Callable[[bytes], bytes]] = None,
        rate_limiter: Optional[RateLimiter] = None,
        min_interval_seconds: float = 0.05,
    ):
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._base_url = base_url
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._timeout = timeout_seconds
        self._transport = transport
        # Proactive rate limiting (fixes a self-reported v1.0 gap: retry-on-
        # failure alone does not PACE requests to avoid triggering the
        # venue's rate limit in the first place). `rate_limiter=None` with a
        # scripted `transport` (as every offline unit test uses) still
        # constructs a real `RateLimiter`, but at effectively-zero cost since
        # `min_interval_seconds` is small and tests issue few sequential
        # calls; callers needing NO pacing at all may pass
        # `RateLimiter(0.0)` explicitly.
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter(min_interval_seconds)

    # -- transport -----------------------------------------------------

    def _send(self, body: bytes) -> bytes:
        self._rate_limiter.wait()
        if self._transport is not None:
            return self._transport(body)
        req = urllib.request.Request(
            self._base_url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec - public read-only info endpoint
            status = getattr(resp, "status", 200)
            if status != 200:
                raise HyperliquidAPIError(f"non-200 status: {status}")
            return resp.read()

    def _post(self, payload: dict) -> object:
        body = json.dumps(payload).encode("utf-8")
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                raw = self._send(body)
                return json.loads(raw)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
                last_exc = exc
                if attempt + 1 < self._max_retries:
                    time.sleep(self._backoff_base * (2**attempt))
                    continue
                raise HyperliquidAPIError(
                    f"Hyperliquid info request {payload.get('type')!r} failed after "
                    f"{self._max_retries} attempt(s): {exc}"
                ) from exc
        raise HyperliquidAPIError(f"Hyperliquid info request failed: {last_exc}")  # pragma: no cover - unreachable

    # -- raw endpoints ---------------------------------------------------

    def fetch_meta(self) -> dict:
        result = self._post({"type": "meta"})
        if not isinstance(result, dict) or "universe" not in result:
            raise HyperliquidAPIError(f"meta malformed body: expected dict with 'universe', got {result!r}")
        return result

    def fetch_candle_snapshot(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list:
        result = self._post(
            {
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
            }
        )
        if not isinstance(result, list):
            raise HyperliquidAPIError(
                f"candleSnapshot malformed body for {coin}/{interval}: expected list, got {type(result)}"
            )
        return result

    def fetch_funding_history(self, coin: str, start_ms: int, end_ms: int) -> list:
        result = self._post({"type": "fundingHistory", "coin": coin, "startTime": start_ms, "endTime": end_ms})
        if not isinstance(result, list):
            raise HyperliquidAPIError(f"fundingHistory malformed body for {coin}: expected list, got {type(result)}")
        return result

    # -- D§4.2 candle pagination (BACKWARDS from `end_ms`, F1) -----------

    def fetch_candles_paginated(
        self, coin: str, interval: str, start_ms: int, end_ms: int, bar_ms: int, max_bars_per_window: int
    ) -> list:
        """D§4.2 — bounded-window pagination walking BACKWARDS from `end_ms`.

        MUST NOT issue a single unbounded [start, now] request and trust the
        response's first timestamp as "beginning of history" (F1, M1): a
        single large request silently returns only the venue's most recent
        ~5000-5001 bars. Each window requests at most `max_bars_per_window`
        bars; a full page (`len == max_bars_per_window`) is treated as
        POSSIBLY TRUNCATED and triggers a further, earlier request. Windows
        overlap by exactly one bar; the overlap is verified to agree
        bar-for-bar (raw field equality) before merging — disagreement is a
        BLOCKING error (D§4.2.3), never silently resolved.
        """
        if max_bars_per_window < 2:
            raise ValueError("max_bars_per_window must be >= 2 to guarantee a >=1-bar overlap")
        window_span_ms = (max_bars_per_window - 1) * bar_ms
        pages: list = []
        cursor_end = end_ms
        while True:
            cursor_start = max(start_ms, cursor_end - window_span_ms)
            page = self.fetch_candle_snapshot(coin, interval, cursor_start, cursor_end)
            if not page:
                break
            pages.append(page)
            first_t = page[0]["t"]
            is_full = len(page) >= max_bars_per_window
            if first_t <= start_ms or not is_full:
                break
            cursor_end = first_t  # next window ends exactly at this page's first bar (>=1-bar overlap)
        return _merge_candle_pages(pages)

    # -- D§5.2 funding pagination (FORWARDS from `start_ms`, F4) ----------

    def fetch_funding_paginated(self, coin: str, start_ms: int, end_ms: int, max_records_per_page: int) -> list:
        """D§5.2 — walk FORWARD with `startTime` advanced past the last
        received event. A full page is possibly-truncated and triggers a
        further request; a page that fails to advance the cursor MUST raise
        rather than loop forever.
        """
        all_events: dict = {}
        cursor = start_ms
        while cursor <= end_ms:
            page = self.fetch_funding_history(coin, cursor, end_ms)
            if not page:
                break
            for ev in page:
                t = ev["time"]
                if t in all_events and not _funding_equal(all_events[t], ev):
                    raise HyperliquidAPIError(
                        f"funding pagination duplicate disagreement for {coin} at t={t}: "
                        f"{all_events[t]!r} != {ev!r} (D§5.2)"
                    )
                all_events[t] = ev
            if len(page) < max_records_per_page:
                break
            last_t = page[-1]["time"]
            next_cursor = last_t + 1
            if next_cursor <= cursor:
                raise HyperliquidAPIError(
                    f"funding pagination for {coin} stalled: cursor did not advance past {cursor} (D§5.2)"
                )
            cursor = next_cursor
        return [all_events[t] for t in sorted(all_events)]


def _candles_equal(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("o", "h", "l", "c", "v", "n"))


def _merge_candle_pages(pages: list) -> list:
    """D§4.2.3/4 — merges raw candle pages, verifying bar-for-bar overlap
    agreement and de-duplicating on `(t)` identity only after asserting
    equal OHLCV fields. Disagreement raises (blocking), never silently
    resolved.
    """
    merged: dict = {}
    for page in pages:
        for bar in page:
            t = bar["t"]
            if t in merged and not _candles_equal(merged[t], bar):
                raise HyperliquidAPIError(
                    f"pagination overlap disagreement at t={t}: {merged[t]!r} != {bar!r} (D§4.2.3)"
                )
            merged[t] = bar
    return [merged[t] for t in sorted(merged)]


def _funding_equal(a: dict, b: dict) -> bool:
    return a.get("fundingRate") == b.get("fundingRate") and a.get("premium") == b.get("premium")
