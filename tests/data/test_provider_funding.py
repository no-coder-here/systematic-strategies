"""D§5 — HyperliquidProvider.get_funding() / get_funding_coverage().

Covers D§11.1 adversarial fixtures: an 8h funding gap, jittered timestamps.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.base import MAX_FUNDING_GAP
from data.hyperliquid.provider import HyperliquidProvider

from conftest import HOUR_MS, funding_event

DAY0 = pd.Timestamp("2024-01-01", tz="UTC")
DAY0_MS = int(DAY0.timestamp() * 1000)


def _hour(n: int) -> int:
    return DAY0_MS + n * HOUR_MS


def _make_provider(multi_client, funding):
    client, transport = multi_client(funding=funding)
    return HyperliquidProvider(client=client), transport


def test_funding_rate_passthrough_no_rescale_D5_1(multi_client):
    # D§5.1 (M18) — used EXACTLY as returned: not annualized, not rescaled.
    events = [funding_event(_hour(0), 0.0001)]
    provider, transport = _make_provider(multi_client, events)
    df = provider.get_funding(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=1))
    assert df["funding_rate"].iloc[0] == pytest.approx(0.0001, abs=0)


def test_funding_ms_precision_preserved_not_rounded_D3_1_3(multi_client):
    # D§3.1.3 (M7) — jittered timestamps MUST NOT be rounded/floored/snapped
    # to the hour.
    jittered_ms = _hour(0) + 24 * 60 * 1000 - 37_000  # ~24min jitter, F5
    events = [funding_event(jittered_ms, 0.0001)]
    provider, transport = _make_provider(multi_client, events)
    df = provider.get_funding(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=1))
    expected = pd.Timestamp(jittered_ms, unit="ms", tz="UTC")
    assert df["timestamp"].iloc[0] == expected
    assert df["timestamp"].iloc[0].value % 1000 == 0  # ms precision retained, not further rounded


def test_notional_price_always_nan_D5_5(multi_client):
    events = [funding_event(_hour(0), 0.0001)]
    provider, transport = _make_provider(multi_client, events)
    df = provider.get_funding(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=1))
    assert pd.isna(df["notional_price"].iloc[0])


def test_coverage_splits_at_8h_gap_D5_4(multi_client):
    # D§5.4 — an 8h gap (2023-05/06 era, F5) MUST split coverage into two
    # disjoint records, never be swallowed by a widened max_funding_gap.
    events = [
        funding_event(_hour(0), 0.0001),
        funding_event(_hour(1), 0.0001),
        funding_event(_hour(9), 0.0001),  # 8h gap from hour 1 -> hour 9
        funding_event(_hour(10), 0.0001),
    ]
    provider, transport = _make_provider(multi_client, events)
    coverage = provider.get_funding_coverage(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=11))
    assert len(coverage) == 2
    assert coverage[0].coverage_start == pd.Timestamp(_hour(0), unit="ms", tz="UTC")
    assert coverage[0].coverage_end == pd.Timestamp(_hour(1), unit="ms", tz="UTC")
    assert coverage[1].coverage_start == pd.Timestamp(_hour(9), unit="ms", tz="UTC")
    assert coverage[1].coverage_end == pd.Timestamp(_hour(10), unit="ms", tz="UTC")
    for c in coverage:
        assert c.max_funding_gap == MAX_FUNDING_GAP
        assert c.source_venue == "Hyperliquid"


def test_coverage_gap_exactly_at_max_is_accepted_not_split(multi_client):
    # §7.7.2 — a gap exactly equal to max_funding_gap is ACCEPTED ("exceeds"
    # means strictly greater), so no split occurs.
    events = [
        funding_event(_hour(0), 0.0001),
        funding_event(_hour(0) + int(MAX_FUNDING_GAP.total_seconds() * 1000), 0.0001),
    ]
    provider, transport = _make_provider(multi_client, events)
    coverage = provider.get_funding_coverage(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=3))
    assert len(coverage) == 1


def test_coverage_records_disjoint_not_touching_D7_2(multi_client):
    events = [
        funding_event(_hour(0), 0.0001),
        funding_event(_hour(1), 0.0001),
        funding_event(_hour(9), 0.0001),
    ]
    provider, transport = _make_provider(multi_client, events)
    coverage = provider.get_funding_coverage(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=10))
    assert len(coverage) == 2
    # non-intersecting closures: strictly disjoint, no touching endpoints.
    assert coverage[0].coverage_end < coverage[1].coverage_start


def test_coverage_reflects_actually_retrieved_events_D5_6(multi_client):
    # D§5.6 (M8) — coverage MUST equal the ACTUAL retrieved event span, never
    # the wider requested window.
    events = [funding_event(_hour(2), 0.0001), funding_event(_hour(3), 0.0001)]
    provider, transport = _make_provider(multi_client, events)
    requested_start = DAY0
    requested_end = DAY0 + pd.Timedelta(hours=20)
    coverage = provider.get_funding_coverage(["BTC"], requested_start, requested_end)
    assert len(coverage) == 1
    assert coverage[0].coverage_start == pd.Timestamp(_hour(2), unit="ms", tz="UTC")
    assert coverage[0].coverage_end == pd.Timestamp(_hour(3), unit="ms", tz="UTC")
    # neither bound equals the requested window
    assert coverage[0].coverage_start != requested_start
    assert coverage[0].coverage_end != requested_end


def test_no_funding_events_produces_no_coverage_claim(multi_client):
    provider, transport = _make_provider(multi_client, [])
    coverage = provider.get_funding_coverage(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=5))
    assert coverage == []


def test_non_finite_funding_rate_raises(multi_client):
    events = [funding_event(_hour(0), float("nan"))]
    provider, transport = _make_provider(multi_client, events)
    with pytest.raises(DataIntegrityError, match="non-finite"):
        provider.get_funding(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=1))


# ---------------------------------------------------------------------------
# D§5.5 — funding_notional_basis default ("period_start") vs an explicitly
# supplied oracle_price_lookup ("event_price"). The `asset_ctxs` archive
# download itself is NOT authorized (D§5.5.1); this proves the WIRING is
# correct against a caller-supplied (mocked) lookup, per the work order's
# "implement and unit-test the extraction path against mocked data" scope.
# ---------------------------------------------------------------------------


def test_default_funding_basis_is_period_start_notional_price_nan(multi_client):
    events = [funding_event(_hour(0), 0.0001)]
    provider, transport = _make_provider(multi_client, events)
    df = provider.get_funding(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=1))
    assert df["notional_price"].isna().all()


def test_event_price_basis_populates_notional_price_via_lookup(multi_client):
    events = [funding_event(_hour(0), 0.0001)]
    provider, transport = _make_provider(multi_client, events)

    def lookup(symbol, ts):
        assert symbol == "BTC"
        return 42000.0

    df = provider.get_funding(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=1), oracle_price_lookup=lookup)
    assert df["notional_price"].iloc[0] == pytest.approx(42000.0)


def test_event_price_basis_leaves_unpriced_events_as_nan(multi_client):
    events = [funding_event(_hour(0), 0.0001)]
    provider, transport = _make_provider(multi_client, events)
    df = provider.get_funding(["BTC"], DAY0, DAY0 + pd.Timedelta(hours=1), oracle_price_lookup=lambda s, t: None)
    assert df["notional_price"].isna().all()  # never fabricated -- D§5.5
