"""D§17 — cross-venue proxy validation. Numerical identity is NOT required
and MUST NOT be asserted; this validates the METRICS machinery, not that
Binance == Hyperliquid.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.validation import CrossVenueReport, UnitNormalizationError, compare_cross_venue


def _df(idx, closes, opens=None, highs=None, lows=None):
    opens = opens if opens is not None else closes
    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": opens,
            "high": highs if highs is not None else [c * 1.001 for c in closes],
            "low": lows if lows is not None else [c * 0.999 for c in closes],
            "close": closes,
        }
    )


def test_identical_series_perfect_correlation_and_zero_diff():
    idx = pd.date_range("2024-01-01", periods=30, freq="1h", tz="UTC")
    closes = [100 + i * 0.1 for i in range(30)]
    hl = _df(idx, closes)
    bn = _df(idx, closes)
    report = compare_cross_venue(hl, bn, "BTC")
    assert isinstance(report, CrossVenueReport)
    assert report.n_overlapping_bars == 30
    assert report.return_correlation == pytest.approx(1.0, abs=1e-6)
    assert report.mean_abs_return_diff == pytest.approx(0.0, abs=1e-12)


def test_metrics_shape_and_percentile_keys():
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    rng = np.random.default_rng(0)
    hl_closes = 100 + np.cumsum(rng.normal(0, 1, 10))
    bn_closes = hl_closes + rng.normal(0, 0.1, 10)
    hl = _df(idx, hl_closes)
    bn = _df(idx, bn_closes)
    report = compare_cross_venue(hl, bn, "BTC")
    assert set(report.return_diff_percentiles.keys()) == {1, 5, 25, 50, 75, 95, 99}
    for field in ("open", "high", "low", "close"):
        assert set(report.ohlc_relative_diff_percentiles[field].keys()) == {1, 5, 25, 50, 75, 95, 99}
    assert report.hl_volatility is not None
    assert report.binance_volatility is not None
    assert report.volatility_ratio is not None


def test_large_discrepancy_events_enumerated_with_timestamps():
    idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    hl_closes = [100.0] * 5
    bn_closes = [100.0, 100.0, 200.0, 100.0, 100.0]  # one huge spike at index 2
    hl = _df(idx, hl_closes)
    bn = _df(idx, bn_closes)
    report = compare_cross_venue(hl, bn, "BTC")
    assert len(report.large_discrepancy_events) >= 1
    flagged_ts = {e["timestamp"] for e in report.large_discrepancy_events}
    assert idx[2] in flagged_ts


def test_no_overlap_returns_empty_metrics_gracefully():
    idx_hl = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    idx_bn = pd.date_range("2025-01-01", periods=3, freq="1h", tz="UTC")  # disjoint window
    hl = _df(idx_hl, [100.0, 101.0, 102.0])
    bn = _df(idx_bn, [200.0, 201.0, 202.0])
    report = compare_cross_venue(hl, bn, "BTC")
    assert report.n_overlapping_bars == 0
    assert report.return_correlation is None


def test_listing_conditioning_computed_when_listing_timestamp_given():
    idx = pd.date_range("2024-01-01", periods=20, freq="1h", tz="UTC")
    closes = [100 + i * 0.5 for i in range(20)]
    hl = _df(idx, closes)
    bn = _df(idx, closes)
    listing_ts = idx[10]
    report = compare_cross_venue(hl, bn, "BTC", listing_timestamp=listing_ts)
    assert report.around_listing is not None
    assert report.around_listing["n_bars"] > 0


def test_high_vol_period_conditioning_computed_with_enough_bars():
    idx = pd.date_range("2024-01-01", periods=40, freq="1h", tz="UTC")
    rng = np.random.default_rng(1)
    closes = 100 + np.cumsum(rng.normal(0, 1, 40))
    hl = _df(idx, closes)
    bn = _df(idx, closes)
    report = compare_cross_venue(hl, bn, "BTC")
    assert report.high_vol_period is not None


# ---------------------------------------------------------------------------
# D§17.2/D§17.4 (v1.3 DECISION 3) -- unit normalization BEFORE comparison.
# ---------------------------------------------------------------------------


def test_numerical_identity_not_asserted_ordinary_divergence_tolerated():
    """D§17.4 — ordinary microstructure divergence is expected and is NOT a
    defect; this function must not raise merely because the two series
    disagree modestly.
    """
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    hl = _df(idx, [100.0] * 10)
    bn = _df(idx, [100.2] * 10)  # 0.2% modest, ordinary divergence
    report = compare_cross_venue(hl, bn, "BTC")  # must not raise
    assert report.n_overlapping_bars == 10


def test_k_asset_matched_multipliers_normalize_to_near_identity_D17_2():
    """D§17.2 (v1.3 DECISION 3) — a `kPEPE`-like pair where BOTH venues
    already quote per-1000-tokens (hl_unit_multiplier=venue_unit_multiplier=
    1000) normalizes to a NO-OP ratio adjustment: the two RAW series here are
    deliberately close in level (as they would be for a correctly-verified
    k-asset pair), and normalizing must not introduce any artificial
    discrepancy or raise.
    """
    idx = pd.date_range("2024-01-01", periods=20, freq="1h", tz="UTC")
    closes = [0.0012 + i * 0.000001 for i in range(20)]
    hl = _df(idx, closes)
    bn = _df(idx, [c * 1.001 for c in closes])  # tiny ordinary divergence
    report = compare_cross_venue(hl, bn, "kPEPE", hl_unit_multiplier=1000, venue_unit_multiplier=1000)
    assert report.n_overlapping_bars == 20
    assert report.hl_unit_multiplier == 1000
    assert report.venue_unit_multiplier == 1000
    # near-zero relative diff after normalization (normalization is a no-op
    # here since both multipliers are equal).
    assert report.ohlc_relative_diff_percentiles["close"][50] == pytest.approx(0.001, abs=1e-6)


def test_wrong_multiplier_produces_unexplained_order_of_magnitude_gap_M44():
    """D§17.4/M44 — if the recorded multipliers do NOT actually cancel the
    real unit difference between the two raw series (i.e. normalization is
    applied but the WRONG multiplier was recorded/used, so an
    order-of-magnitude gap survives), `compare_cross_venue` MUST raise
    `UnitNormalizationError` rather than silently reporting a huge
    "large_discrepancy_event" as if it were ordinary proxy divergence.

    Here the raw HL series is ~1000x the raw Binance series (as it would be
    if HL truly quotes per-1000-tokens but the mapped Binance contract does
    NOT, e.g. mis-mapped to a non-'1000'-prefixed contract) -- but the caller
    incorrectly supplies venue_unit_multiplier=1000 (should be 1), so
    normalizing does NOT cancel the real gap. This must fail loudly.
    """
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    hl_closes = [1.2] * 10
    bn_closes = [0.0012] * 10  # genuinely ~1000x smaller raw level
    hl = _df(idx, hl_closes)
    bn = _df(idx, bn_closes)
    with pytest.raises(UnitNormalizationError):
        # Both sides claim 1000; if that were correct the gap would cancel.
        # It does NOT cancel here (0.0012*1000 != 1.2/1000), proving this is
        # a genuine order-of-magnitude gap surviving the recorded multipliers.
        compare_cross_venue(hl, bn, "kX", hl_unit_multiplier=1000, venue_unit_multiplier=1000)


def test_normalization_applied_before_metrics_not_after_M44():
    """D§17.2 (M44) — normalization MUST happen BEFORE any metric is
    computed, not merely as a post-hoc label. A raw ~1000x scale difference
    that IS correctly explained by the recorded multipliers (hl=1000,
    venue=1 -- e.g. HL quotes per-1000-tokens, Binance quotes per-token) must
    normalize away to a small, ordinary-looking diff and must NOT raise.
    """
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    hl_closes = [1.2] * 10  # HL: price per 1000-token contract
    bn_closes = [0.0012] * 10  # Binance: price per SINGLE token
    hl = _df(idx, hl_closes)
    bn = _df(idx, bn_closes)
    # hl_price/1000 == 0.0012 == bn_price/1 -> normalizes to an exact match.
    report = compare_cross_venue(hl, bn, "kX", hl_unit_multiplier=1000, venue_unit_multiplier=1)
    assert report.n_overlapping_bars == 10
    assert report.ohlc_relative_diff_percentiles["close"][50] == pytest.approx(0.0, abs=1e-9)


def test_multiplier_never_inferred_from_price_ratio_M43():
    """D§16.3.4/M43 — this function takes multipliers ONLY as fixed,
    caller-supplied parameters; it never inspects the observed price ratio
    to derive/correct a multiplier. Proof: feeding an UNEXPLAINED gap with
    the default (1, 1) multipliers raises rather than silently "discovering"
    and applying the true ~1000x ratio to make the check pass.
    """
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    hl_closes = [1.2] * 10
    bn_closes = [0.0012] * 10  # true ratio is 1000x, but NOT declared via multipliers
    hl = _df(idx, hl_closes)
    bn = _df(idx, bn_closes)
    with pytest.raises(UnitNormalizationError):
        compare_cross_venue(hl, bn, "kX")  # defaults hl_unit_multiplier=venue_unit_multiplier=1
