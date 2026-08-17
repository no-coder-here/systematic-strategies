"""spec §4.2 — deterministic lookahead mutation test.

Mutation boundary is `index >= k+2` (NOT `> k`, BD1) — period `k` legitimately
earns `P[k] -> P[k+1]`. Comparison is BITWISE for every field (W14) — no
tolerance is admissible. Uses TWO choices of `k` (W13).

**Flagged spec finding (see implementation report — this is NOT silently
papered over).** Spec §4.2 prescribes `close.shift(-1)` (mutation M2) as
"the" proof that this test discriminates. Empirically AND structurally this
does not hold: with `execution_lag=1`, the rebalance decision for period `i`
reads `target_weights` at frame position `t_pos = i - 1`. A signal bug of
the form `close.shift(-m)` at `t_pos` reads raw close at position `t_pos +
m`. For every period `i` in the checked range `0..k` (`t_pos` at most
`k - 1`), the read position is at most `(k - 1) + m`. The mutation only
touches positions `>= k + 2`. Detection therefore REQUIRES `(k-1) + m >= k +
2`, i.e. `m >= 3` -- for ANY `k`, independent of the actual price data.
`close.shift(-1)` (`m=1`) and even `close.shift(-2)` (`m=2`) can NEVER reach
the mutated region and are proven below (`test_shift_by_1_and_2_are_not_detectable_by_this_boundary`)
to survive the mutation regardless of how aggressively prices are perturbed.
This is a genuine inconsistency between spec §4.2's own worked example and
its own `k+2` boundary rule (BD1) -- not an implementation defect. The
discrimination proof below therefore uses `close.shift(-3)`, the minimal
depth that is structurally detectable, and documents why `-1`/`-2` are used
as a NEGATIVE control instead.

The discrimination example also uses a value-agnostic "reflection" mutation
(`2*sma - close`, guaranteeing the boolean flips) rather than a plain scale
factor, because a real historical BTC close may already sit on the "flipped"
side of a scaled comparison purely by chance (verified: scaling Window A's
real prices by 1000x at k=500 did NOT flip the relevant comparison, since
the unscaled close already exceeded the SMA there) -- a plain scale factor
is not a reliable discrimination probe on real, uncontrolled market data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import FundingEvent, MarketData, StrategyOutput
from data.hyperliquid.provider import HyperliquidProvider
from experiments.qr_smoke_001 import pipeline
from strategies.qr_smoke_001 import build_strategy_output_for_frame


def _load_raw_and_config(base_dir="data"):
    hl = HyperliquidProvider(offline=True, storage_base_dir=base_dir)
    raw_start, raw_end = pipeline.eval_window_to_raw_window(
        pipeline.WINDOW_A_EVAL_START, pipeline.WINDOW_A_EVAL_END
    )
    raw_md = pipeline.load_raw_market_data(hl, "BTC", raw_start, raw_end)
    frame_md = pipeline.slice_evaluated_frame(raw_md)
    funding_events, funding_coverage = pipeline.load_full_hl_funding(base_dir)
    config = pipeline.build_config(funding_mode="required", funding_notional_basis="period_start")
    return raw_md, frame_md, funding_events, funding_coverage, config


def _mutate_frame_prices(frame_md: MarketData, k: int, factor: float = 1000.0) -> MarketData:
    """Scales every price bar at FRAME position `>= k+2` by `factor` (spec
    §4.2: 'strictly after T + Delta'). Operates on the engine-facing
    (already-sliced) frame, holding `StrategyOutput` FIXED -- this tests the
    ENGINE's own anti-lookahead behaviour (contract §4.1/§4.4), matching
    spec §4.2's literal text ("mutate every price bar... re-run"), which
    does not ask for the strategy to be recomputed.
    """
    idx = frame_md.open.index
    cutoff = idx[k + 2]
    mask = idx >= cutoff
    open_m = frame_md.open.copy()
    close_m = frame_md.close.copy()
    open_m.loc[mask] = open_m.loc[mask] * factor
    close_m.loc[mask] = close_m.loc[mask] * factor
    return MarketData(open=open_m, close=close_m)


def _mutate_funding(events: list, threshold_ts: pd.Timestamp, factor: float = 1000.0) -> list:
    """Mutates every funding event with `timestamp >= T_{k+1}` (BD2)."""
    out = []
    for e in events:
        if e.timestamp >= threshold_ts:
            out.append(FundingEvent(timestamp=e.timestamp, symbol=e.symbol, funding_rate=e.funding_rate * factor, notional_price=e.notional_price))
        else:
            out.append(e)
    return out


def _assert_periods_0_to_k_bitwise_identical(original, mutated, k: int):
    np.testing.assert_array_equal(
        original.equity_curve.to_numpy()[: k + 2], mutated.equity_curve.to_numpy()[: k + 2]
    )
    for field in ("net_return", "gross_return", "fee_return", "slippage_return", "funding_return", "turnover", "rebalance_flag", "gross_exposure"):
        a = getattr(original, field).to_numpy()[: k + 1]
        b = getattr(mutated, field).to_numpy()[: k + 1]
        np.testing.assert_array_equal(a, b, err_msg=f"field {field!r} changed in periods 0..{k}")
    for frame_field in ("quantity", "trades", "positions", "pre_trade_weights", "symbol_state"):
        a = getattr(original, frame_field).to_numpy()[: k + 1]
        b = getattr(mutated, frame_field).to_numpy()[: k + 1]
        np.testing.assert_array_equal(a, b, err_msg=f"frame field {frame_field!r} changed in periods 0..{k}")


def _run_engine_only_mutation(k: int):
    """Main invariance test: strategy output FIXED (computed once from the
    real, unmutated data); only the engine's market_data/funding inputs are
    mutated. Tests the ENGINE's anti-lookahead behaviour end-to-end."""
    raw_md, frame_md, funding_events, funding_coverage, config = _load_raw_and_config()
    strategy_output = build_strategy_output_for_frame(raw_md.close["BTC"], frame_md.open.index, "BTC")
    idx = frame_md.open.index

    original = run_backtest(
        config, frame_md, strategy_output,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )
    mutated_md = _mutate_frame_prices(frame_md, k)
    mutated_funding = _mutate_funding(funding_events, idx[k + 1])
    mutated = run_backtest(
        config, mutated_md, strategy_output,
        funding_events=mutated_funding, funding_coverage=funding_coverage,
    )
    return original, mutated


def _assert_something_differs_after_k(original, mutated, k: int):
    """Defensive sanity check (not literal spec text): the mutation must
    actually have changed something after the boundary, else 'no change
    before k' would be trivially true even if the mutation were a no-op."""
    n = len(original.net_return)
    if k + 2 >= n:
        pytest.skip("k too close to the end of the sample for a post-mutation region to exist")
    after_orig = original.net_return.to_numpy()[k + 1 :]
    after_mut = mutated.net_return.to_numpy()[k + 1 :]
    assert not np.array_equal(after_orig, after_mut), "mutation had no effect anywhere — test would be vacuous"


# Two choices of k (W13), each verified (via test_manual_verification.py's
# indices 51/52/53, both << 500) to have entries/holds/exits/funding charges
# well before the boundary.
@pytest.mark.parametrize("k", [500, 3500])
def test_lookahead_mutation_no_change_before_k(k):
    original, mutated = _run_engine_only_mutation(k)
    _assert_periods_0_to_k_bitwise_identical(original, mutated, k)
    _assert_something_differs_after_k(original, mutated, k)


def test_lookahead_boundary_is_k_plus_2_not_k_plus_1():
    """BD1 negative control — mutating index k+1 (not k+2) changes period k
    on a CORRECT engine, because period k legitimately earns P[k] -> P[k+1]
    (contract §4.4). Demonstrates the k+2 boundary is not arbitrary."""
    k = 500
    raw_md, frame_md, funding_events, funding_coverage, config = _load_raw_and_config()
    strategy_output = build_strategy_output_for_frame(raw_md.close["BTC"], frame_md.open.index, "BTC")
    idx = frame_md.open.index

    original = run_backtest(
        config, frame_md, strategy_output,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )
    cutoff = idx[k + 1]  # deliberately WRONG boundary
    mask = idx >= cutoff
    open_m = frame_md.open.copy()
    close_m = frame_md.close.copy()
    open_m.loc[mask] = open_m.loc[mask] * 1000.0
    close_m.loc[mask] = close_m.loc[mask] * 1000.0
    wrong_boundary_md = MarketData(open=open_m, close=close_m)

    mutated = run_backtest(
        config, wrong_boundary_md, strategy_output,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )
    assert original.equity_curve.iloc[k + 1] != mutated.equity_curve.iloc[k + 1]


# ---------------------------------------------------------------------------
# Discrimination proof (spec §4.2's "then restore"): confirm the mutation
# METHODOLOGY can catch a real end-to-end signal-lookahead bug when the
# strategy's target weights are genuinely RECOMPUTED from the mutated data
# (unlike the engine-only test above, which intentionally holds weights
# fixed). See the module docstring for why `-3`, not `-1`, is used, and why
# a value-agnostic reflection mutation is used instead of a plain scale.
# ---------------------------------------------------------------------------


def _make_shifted_signal_output(close_full: pd.Series, frame_index: pd.DatetimeIndex, shift_m: int) -> StrategyOutput:
    sig = close_full.shift(-shift_m) > close_full.rolling(100, min_periods=100).mean()
    w_full = pd.DataFrame({"BTC": sig.astype(float)}, index=close_full.index)
    return StrategyOutput.rebalance_on_change(w_full.loc[frame_index])


def _reflect_mutate_raw(raw_md: MarketData, cutoff_ts: pd.Timestamp, shift_m: int) -> MarketData:
    """Guarantees the M2-style comparison flips at every mutated position,
    regardless of the actual price level there (see module docstring)."""
    close_full = raw_md.close["BTC"]
    sma_full = close_full.rolling(100, min_periods=100).mean()
    sma_baseline_for_pos = sma_full.shift(shift_m)  # value at p equals sma_full at (p - shift_m)
    reflected_close = 2 * sma_baseline_for_pos - close_full

    open_m = raw_md.open.copy()
    close_m = raw_md.close.copy()
    mask = open_m.index >= cutoff_ts
    close_m.loc[mask, "BTC"] = reflected_close.loc[mask]
    open_m.loc[mask] = open_m.loc[mask] * 1000.0  # execution price still "substantially" mutated
    return MarketData(open=open_m, close=close_m)


def _recomputed_mutation_result(k: int, shift_m: int):
    raw_md, frame_md, funding_events, funding_coverage, config = _load_raw_and_config()
    idx = frame_md.open.index
    cutoff_ts = idx[k + 2]

    so_original = _make_shifted_signal_output(raw_md.close["BTC"], idx, shift_m)
    original = run_backtest(
        config, frame_md, so_original,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )

    raw_mut = _reflect_mutate_raw(raw_md, cutoff_ts, shift_m)
    frame_mut = pipeline.slice_evaluated_frame(raw_mut)
    so_mutated = _make_shifted_signal_output(raw_mut.close["BTC"], idx, shift_m)
    mutated_funding = _mutate_funding(funding_events, idx[k + 1])
    mutated = run_backtest(
        config, frame_mut, so_mutated,
        funding_events=mutated_funding, funding_coverage=funding_coverage,
    )
    return original, mutated


def test_lookahead_mutation_discriminates_against_a_real_lookahead_bug():
    """Proof of discrimination: with the strategy's signal GENUINELY
    recomputed from the mutated raw data (unlike the engine-only test
    above), a `close.shift(-3)` lookahead bug's decision for period k=500
    CHANGES under the mutation -- the methodology goes RED, proving it can
    catch a real end-to-end lookahead defect."""
    k = 500
    original, mutated = _recomputed_mutation_result(k, shift_m=3)
    with pytest.raises(AssertionError):
        _assert_periods_0_to_k_bitwise_identical(original, mutated, k)


def test_shift_by_1_and_2_are_not_detectable_by_this_boundary():
    """Documents the flagged spec finding (module docstring): `close.shift(-1)`
    (spec §4.2's own literal M2 example) and `close.shift(-2)` are
    STRUCTURALLY invisible to the `index >= k+2` mutation boundary for
    `execution_lag=1`, regardless of price data or mutation magnitude. This
    test locks in that fact so a future accidental "fix" that makes it
    silently pass isn't lost -- and so the discrepancy remains visible
    rather than quietly worked around.
    """
    k = 500
    for shift_m in (1, 2):
        original, mutated = _recomputed_mutation_result(k, shift_m=shift_m)
        # No AssertionError expected: periods 0..k stay identical even
        # though the underlying signal is genuinely broken.
        _assert_periods_0_to_k_bitwise_identical(original, mutated, k)


# ---------------------------------------------------------------------------
# Sharpened close-side probe (spec §4.2 v1.1, MANDATORY). Under `next_open`
# the engine reads NO close prices at all -- `prices` (selected from `open`)
# is the only frame consulted downstream. A correct `signal[t]` feeding any
# period `0..k` uses closes only through index `k-1`. Therefore closes at
# index `>= k` may be mutated, WITH OPENS LEFT ENTIRELY UNTOUCHED, with no
# legitimate effect on periods `0..k` -- and because the strategy is
# RECOMPUTED from the mutated closes (unlike the open-side probe above,
# which holds `strategy_output` fixed and therefore cannot move SMA values,
# signals, or target weights), this probe restores `m >= 1` sensitivity,
# unlike the open-side probe which needs `m >= 3` (see module docstring).
#
# MUST be two-sided (x1000 AND x0.001): a single scaling direction can
# coincidentally fail to flip a `close > sma` comparison for some position
# (a real BTC close may already sit on the "expected" side after scaling
# upward only), so at least one of the two directions MUST discriminate.
# ---------------------------------------------------------------------------


def _close_side_mutate_raw(raw_md: MarketData, cutoff_ts: pd.Timestamp, factor: float) -> MarketData:
    """Mutates CLOSE prices at index >= cutoff_ts by `factor`, leaving OPENS
    entirely untouched (spec §4.2 v1.1 sharpened close-side probe)."""
    close_m = raw_md.close.copy()
    mask = close_m.index >= cutoff_ts
    close_m.loc[mask] = close_m.loc[mask] * factor
    return MarketData(open=raw_md.open, close=close_m)


def _close_side_probe_real_strategy(k: int, factor: float):
    """Primary invariance property, exercised against the REAL (correct)
    QR-SMOKE-001 strategy: mutating closes at frame position >= k (opens
    untouched) and recomputing the real strategy from the mutated closes
    MUST leave periods 0..k bitwise unchanged."""
    raw_md, frame_md, funding_events, funding_coverage, config = _load_raw_and_config()
    idx = frame_md.open.index
    cutoff_ts = idx[k]

    so_original = build_strategy_output_for_frame(raw_md.close["BTC"], idx, "BTC")
    original = run_backtest(
        config, frame_md, so_original,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )

    raw_mut = _close_side_mutate_raw(raw_md, cutoff_ts, factor)
    frame_mut = pipeline.slice_evaluated_frame(raw_mut)
    # Opens MUST be bitwise unchanged -- this probe mutates closes ONLY.
    np.testing.assert_array_equal(
        frame_mut.open["BTC"].to_numpy(), frame_md.open["BTC"].to_numpy()
    )
    so_mutated = build_strategy_output_for_frame(raw_mut.close["BTC"], idx, "BTC")

    mutated = run_backtest(
        config, frame_mut, so_mutated,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )
    return original, mutated


@pytest.mark.parametrize("factor", [1000.0, 0.001])
def test_close_side_probe_real_strategy_no_change_before_k(factor):
    """spec §4.2 v1.1 sharpened close-side probe, applied to the REAL
    strategy: SMA values, signals, target weights, rebalance decisions,
    executions, quantities, NAV, PnL for periods 0..k MUST be bitwise
    unchanged when closes at index >= k are mutated (opens untouched) and
    the strategy is genuinely recomputed from the mutated closes."""
    k = 500
    original, mutated = _close_side_probe_real_strategy(k, factor)
    _assert_periods_0_to_k_bitwise_identical(original, mutated, k)


def _close_side_probe_buggy(k: int, shift_m: int, factor: float):
    """Discrimination proof: a deliberately buggy `close.shift(-shift_m)`
    signal, exercised through the close-side probe's mutation methodology
    (mutate closes at index >= k, opens untouched, RECOMPUTE the buggy
    signal from the mutated closes)."""
    raw_md, frame_md, funding_events, funding_coverage, config = _load_raw_and_config()
    idx = frame_md.open.index
    cutoff_ts = idx[k]

    so_original = _make_shifted_signal_output(raw_md.close["BTC"], idx, shift_m)
    original = run_backtest(
        config, frame_md, so_original,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )

    raw_mut = _close_side_mutate_raw(raw_md, cutoff_ts, factor)
    frame_mut = pipeline.slice_evaluated_frame(raw_mut)
    so_mutated = _make_shifted_signal_output(raw_mut.close["BTC"], idx, shift_m)
    mutated = run_backtest(
        config, frame_mut, so_mutated,
        funding_events=funding_events, funding_coverage=funding_coverage,
    )
    return original, mutated


@pytest.mark.parametrize("shift_m", [1, 2, 3])
def test_close_side_probe_discriminates_lookahead_bug_two_sided(shift_m):
    """spec §4.2 v1.1 MANDATORY — proves the sharpened close-side probe
    discriminates against a `close.shift(-shift_m)` lookahead bug at
    m = 1, 2, AND 3 (restoring m>=1 sensitivity that the open-side probe
    structurally lacks -- it needs m>=3, see module docstring). Two-sided:
    at least ONE of x1000 / x0.001 MUST flip the relevant comparison and
    cause periods 0..k to diverge (go RED)."""
    k = 500
    detected_by = []
    for factor in (1000.0, 0.001):
        original, mutated = _close_side_probe_buggy(k, shift_m, factor)
        try:
            _assert_periods_0_to_k_bitwise_identical(original, mutated, k)
        except AssertionError:
            detected_by.append(factor)
    assert detected_by, (
        f"sharpened close-side probe FAILED to detect a close.shift(-{shift_m}) "
        "lookahead bug under EITHER scaling direction (x1000, x0.001) -- expected "
        "at least one direction to go RED"
    )
