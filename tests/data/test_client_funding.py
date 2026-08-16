"""D§5.2 (F4) — funding pagination: 500-record cap, forward walk, stall guard."""

from __future__ import annotations

import pytest

from data.hyperliquid.client import HyperliquidAPIError, HyperliquidClient

from conftest import HOUR_MS, funding_event


def test_funding_pagination_walks_forward_past_500_cap(scripted_client):
    total_events = 1200
    all_events = {i: funding_event(i * HOUR_MS, 0.0001) for i in range(total_events)}
    cap = 500

    def handler(payload):
        start_ms, end_ms = payload["startTime"], payload["endTime"]
        window = [ev for i, ev in all_events.items() if start_ms <= i * HOUR_MS <= end_ms]
        window.sort(key=lambda e: e["time"])
        return window[:cap]  # venue caps at 500 records per request (F4)

    client, transport = scripted_client(handler)
    result = client.fetch_funding_paginated("BTC", 0, (total_events - 1) * HOUR_MS, cap)
    assert len(result) == total_events
    assert result[0]["time"] == 0
    assert result[-1]["time"] == (total_events - 1) * HOUR_MS
    assert len(transport.calls) >= 3  # 1200 / 500 -> at least 3 pages


def test_funding_pagination_short_page_stops():
    def handler(payload):
        return [funding_event(0, 0.0001), funding_event(HOUR_MS, 0.0002)]

    from data.hyperliquid.client import HyperliquidClient as _Client

    calls = []

    def transport(body):
        import json

        payload = json.loads(body)
        calls.append(payload)
        return json.dumps(handler(payload)).encode()

    client = _Client(transport=transport, max_retries=2, backoff_base_seconds=0.0)
    result = client.fetch_funding_paginated("BTC", 0, 100 * HOUR_MS, 500)
    assert len(result) == 2
    assert len(calls) == 1  # short page (< cap) -> pagination stops after one request


def test_funding_pagination_duplicate_disagreement_raises(scripted_client):
    # Two overlapping pages report DIFFERENT (rate, premium) for the SAME
    # timestamp -> blocking error, never silently resolved.
    call_count = {"n": 0}

    def handler(payload):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [funding_event(i * HOUR_MS, 0.0001) for i in range(500)]
        # second page re-reports t=499h with a DIFFERENT rate
        return [funding_event(499 * HOUR_MS, 0.9999)] + [funding_event(i * HOUR_MS, 0.0001) for i in range(500, 550)]

    client, transport = scripted_client(handler)
    with pytest.raises(HyperliquidAPIError, match="duplicate disagreement"):
        client.fetch_funding_paginated("BTC", 0, 600 * HOUR_MS, 500)


def test_funding_pagination_identical_duplicate_merges_without_error(scripted_client):
    call_count = {"n": 0}

    def handler(payload):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [funding_event(i * HOUR_MS, 0.0001) for i in range(500)]
        return [funding_event(499 * HOUR_MS, 0.0001)] + [funding_event(i * HOUR_MS, 0.0001) for i in range(500, 520)]

    client, transport = scripted_client(handler)
    result = client.fetch_funding_paginated("BTC", 0, 600 * HOUR_MS, 500)
    assert len(result) == 520


def test_funding_pagination_stalled_cursor_raises():
    # A page that returns exactly `cap` records but whose last event does NOT
    # advance the cursor MUST raise rather than loop forever.
    cap = 3

    def handler(payload):
        # always return the same 3 events regardless of requested startTime
        return [funding_event(0, 0.0001), funding_event(HOUR_MS, 0.0001), funding_event(2 * HOUR_MS, 0.0001)]

    import json

    def transport(body):
        payload = json.loads(body)
        return json.dumps(handler(payload)).encode()

    client = HyperliquidClient(transport=transport, max_retries=2, backoff_base_seconds=0.0)
    with pytest.raises(HyperliquidAPIError, match="stalled"):
        client.fetch_funding_paginated("BTC", 0, 100 * HOUR_MS, cap)


def test_funding_malformed_body_raises(scripted_client):
    def handler(payload):
        return {"unexpected": "shape"}

    client, transport = scripted_client(handler)
    with pytest.raises(HyperliquidAPIError, match="malformed body"):
        client.fetch_funding_history("BTC", 0, HOUR_MS)


def test_funding_transport_exception_propagates(scripted_client):
    def handler(payload):
        return TimeoutError("simulated timeout")

    client, transport = scripted_client(handler)
    with pytest.raises(HyperliquidAPIError):
        client.fetch_funding_history("BTC", 0, HOUR_MS)
