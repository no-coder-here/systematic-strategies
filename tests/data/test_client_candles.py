"""D§4.2 (F1) — candle pagination, truncation, overlap verification.

Covers D§11.1 adversarial fixtures: truncated pages, a full page exactly at
the limit, an overlap that disagrees, unsorted responses (via out-of-order
merge), malformed body.
"""

from __future__ import annotations

import pytest

from data.hyperliquid.client import HyperliquidAPIError, HyperliquidClient

from conftest import HOUR_MS, candle


def test_naive_unbounded_request_would_truncate_but_paginated_reconstructs_full_history(scripted_client):
    # Simulate a venue with 12 total 1h bars but which truncates ANY single
    # request to at most 5 bars, returning only the most recent slice of the
    # requested window (mirrors F1's real behaviour).
    total_bars = 12
    max_window = 5
    all_bars = {i: candle(i * HOUR_MS, 100 + i, 101 + i, 99 + i, 100 + i, 10, 5) for i in range(total_bars)}

    def handler(payload):
        req = payload["req"]
        start_ms, end_ms = req["startTime"], req["endTime"]
        window = [b for i, b in all_bars.items() if start_ms <= i * HOUR_MS <= end_ms]
        window.sort(key=lambda b: b["t"])
        return window[-max_window:]  # venue truncates to the MOST RECENT slice (F1)

    client, transport = scripted_client(handler)

    # A single unbounded request only sees the most recent `max_window` bars —
    # this is the defect M1 introduces if pagination is bypassed.
    naive = client.fetch_candle_snapshot("BTC", "1h", 0, (total_bars - 1) * HOUR_MS)
    assert len(naive) == max_window

    # Paginated fetch (self-imposed window == max_window) MUST reconstruct
    # the FULL history, not just the most recent slice.
    full = client.fetch_candles_paginated("BTC", "1h", 0, (total_bars - 1) * HOUR_MS, HOUR_MS, max_window)
    assert len(full) == total_bars
    assert full[0]["t"] == 0
    assert full[-1]["t"] == (total_bars - 1) * HOUR_MS
    assert len(transport.calls) > 1  # more than one request was actually issued


def test_full_page_exactly_at_limit_triggers_further_request(scripted_client):
    # D§4.2.2 — a full response (len == limit) MUST be treated as possibly
    # truncated and trigger a further request, even if it happens to be
    # exactly the true end of history (the client cannot know that without asking).
    bars = {i: candle(i * HOUR_MS, 100, 101, 99, 100, 1, 1) for i in range(6)}

    call_count = {"n": 0}

    def handler(payload):
        call_count["n"] += 1
        req = payload["req"]
        start_ms, end_ms = req["startTime"], req["endTime"]
        window = [b for i, b in bars.items() if start_ms <= i * HOUR_MS <= end_ms]
        window.sort(key=lambda b: b["t"])
        return window

    client, transport = scripted_client(handler)
    # window size 3 bars; total history is 6 bars -> first page is exactly full (3==3)
    result = client.fetch_candles_paginated("BTC", "1h", 0, 5 * HOUR_MS, HOUR_MS, 3)
    assert len(result) == 6
    assert call_count["n"] >= 2


def test_truncated_page_stops_pagination(scripted_client):
    # A short (non-full) page means we've reached the true beginning of history.
    bars = {i: candle(i * HOUR_MS, 100, 101, 99, 100, 1, 1) for i in range(4)}

    def handler(payload):
        req = payload["req"]
        start_ms, end_ms = req["startTime"], req["endTime"]
        window = [b for i, b in bars.items() if start_ms <= i * HOUR_MS <= end_ms]
        window.sort(key=lambda b: b["t"])
        return window

    client, transport = scripted_client(handler)
    result = client.fetch_candles_paginated("BTC", "1h", -100 * HOUR_MS, 3 * HOUR_MS, HOUR_MS, 10)
    assert len(result) == 4
    assert len(transport.calls) == 1  # first (only) page was already short -> no further requests


def test_overlap_disagreement_raises(scripted_client):
    # D§4.2.3 — disagreement across an overlap is a BLOCKING error. With
    # max_bars_per_window=2 over a 3-bar range [0, 1h, 2h], pagination MUST
    # issue two windows sharing exactly the boundary bar at t=1h: window 1
    # (newest) = [1h, 2h], window 2 (older) = [0, 1h]. Making the two windows'
    # versions of the SHARED bar (t=1h) disagree must raise.
    bad_bar_1h = candle(HOUR_MS, 999, 1000, 998, 999, 1, 1)
    good_bar_1h = candle(HOUR_MS, 100, 101, 99, 100, 1, 1)

    def handler(payload):
        req = payload["req"]
        end_ms = req["endTime"]
        if end_ms >= 2 * HOUR_MS:
            # newest window [1h, 2h]: BAD version of the shared boundary bar
            return [bad_bar_1h, candle(2 * HOUR_MS, 100, 101, 99, 100, 1, 1)]
        # older window [0, 1h]: GOOD version of the shared boundary bar
        return [candle(0, 100, 101, 99, 100, 1, 1), good_bar_1h]

    client, transport = scripted_client(handler)
    with pytest.raises(HyperliquidAPIError, match="overlap disagreement"):
        client.fetch_candles_paginated("BTC", "1h", 0, 2 * HOUR_MS, HOUR_MS, 2)
    assert len(transport.calls) == 2


def test_malformed_body_raises_api_failure(scripted_client):
    def handler(payload):
        return {"not": "a list"}  # candleSnapshot must return a list

    client, transport = scripted_client(handler)
    with pytest.raises(HyperliquidAPIError, match="malformed body"):
        client.fetch_candle_snapshot("BTC", "1h", 0, HOUR_MS)


def test_empty_response_is_returned_not_raised(scripted_client):
    def handler(payload):
        return []

    client, transport = scripted_client(handler)
    result = client.fetch_candle_snapshot("BTC", "1h", 0, HOUR_MS)
    assert result == []


def test_transport_exception_propagates_not_swallowed(scripted_client):
    # D§7 (M13) — a transport exception MUST propagate, never silently become [].
    def handler(payload):
        return ConnectionError("simulated network failure")

    client, transport = scripted_client(handler)
    with pytest.raises(HyperliquidAPIError):
        client.fetch_candle_snapshot("BTC", "1h", 0, HOUR_MS)


def test_unequal_duplicate_across_pages_raises_not_dropped(scripted_client):
    # This is effectively the same protection as the overlap-disagreement
    # test but constructed via two NON-adjacent pages sharing one key with
    # differing values, exercising the merge path directly.
    from data.hyperliquid.client import _merge_candle_pages

    page_a = [candle(0, 100, 101, 99, 100, 1, 1)]
    page_b = [candle(0, 200, 201, 199, 200, 1, 1)]  # same t, different values
    with pytest.raises(HyperliquidAPIError, match="overlap disagreement"):
        _merge_candle_pages([page_a, page_b])


def test_identical_duplicate_across_pages_is_merged_without_error():
    from data.hyperliquid.client import _merge_candle_pages

    bar = candle(0, 100, 101, 99, 100, 1, 1)
    page_a = [bar]
    page_b = [dict(bar), candle(HOUR_MS, 100, 101, 99, 100, 1, 1)]
    merged = _merge_candle_pages([page_a, page_b])
    assert len(merged) == 2
    assert merged[0]["t"] == 0
    assert merged[1]["t"] == HOUR_MS
