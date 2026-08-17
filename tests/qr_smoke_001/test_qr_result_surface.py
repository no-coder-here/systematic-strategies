"""spec §4.8 — result-surface properties not otherwise tested (W8).

**Flagged spec finding (see implementation report).** §4.8's literal
`funding_events_excluded` identity — "Σ events charged + funding_events_excluded
== total events in [T_0, T_{n-1}]" — does NOT hold on Window A as a
TWO-term equation: measured `charged=2200`, `excluded=23545`,
`total_loaded=28056` (`2200+23545 = 25745 != 28056`). The gap is exactly
`2311` events landing inside INACTIVE periods (BTC flat / no position):
contract §6.0 Step 8 masks funding to `active = {j : quantity[i,j] != 0}`
(post-trade, contract §7.7.1), so an event whose timestamp falls inside a
period where the strategy holds no position is neither charged NOR counted
by `funding_events_excluded` (which counts ONLY events outside `[T_0,
T_{n-1})` by TIMESTAMP, irrespective of activity). QR-SMOKE-001's own
long-or-flat strategy genuinely has flat stretches (2205 INACTIVE + 106
EXITING = 2311 non-funding-accruing periods out of 4511), so this is not a
corner case avoided by the fixture — it is the normal case. The identity
the spec intends almost certainly assumed an always-in-market symbol; the
CORRECT three-term identity (`charged + skipped_inactive + excluded ==
total_loaded`) is verified exactly below.
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd
import pytest

from backtest.models import BacktestConfig, MarketData, StrategyOutput
from backtest.engine import run_backtest
from experiments.qr_smoke_001 import reconstruction

# D2 fix (v1.1 repair cycle 2): the funding-events-excluded identity below
# used to source BOTH the "total loaded" count and the in-range event
# timestamps from `pipeline.load_full_hl_funding` -- the SAME adapter that
# feeds the engine's own funding_events. A timestamp shift applied only in
# that adapter (M12) would therefore be self-consistent between the count
# used by the identity and the count the engine actually saw, and invisible
# to this test. Both counts are now read INDEPENDENTLY, directly from the
# raw funding parquet, via `reconstruction.load_raw_funding` (which reads
# `data/processed/hyperliquid/funding/BTC.parquet` with plain
# `pandas.read_parquet` and does NOT go through `HyperliquidProvider` or
# `pipeline.py` at all -- see that module's independence docstring).
FUNDING_HISTORY_START = pd.Timestamp("2023-05-01", tz="UTC")
FUNDING_HISTORY_END = pd.Timestamp("2026-08-20", tz="UTC")


def _load_independent_funding_timestamps(base_dir="data", symbol="BTC") -> list[pd.Timestamp]:
    df = reconstruction.load_raw_funding(base_dir, symbol, FUNDING_HISTORY_START, FUNDING_HISTORY_END)
    return sorted(pd.Timestamp(ts) for ts in df["timestamp"])


def test_unexecuted_rebalances_synthetic_case():
    """contract §4.4 — a signal flip at bar n-2 or n-1 produces an
    unexecuted rebalance (recorded, never raised). Window A itself has NONE
    (verified: `window_a.result.unexecuted_rebalances == []`, reported, not
    hidden) — a signal flip that late in real BTC data over this window did
    not happen to occur, so a controlled synthetic fixture is used to prove
    the mechanism fires when it should.
    """
    idx = pd.date_range("2026-01-01", periods=6, freq="1h", tz="UTC")
    prices = pd.DataFrame({"BTC": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]}, index=idx)
    md = MarketData(open=prices, close=prices)
    weights = pd.DataFrame({"BTC": [0.0, 0.0, 0.0, 0.0, 1.0, 1.0]}, index=idx)  # flips at bar 4 (n-2)
    mask = pd.Series([True, False, False, False, True, False], index=idx, dtype=bool)
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        frequency="1h", fee_bps=1.0, slippage_bps=1.0, funding_mode="disabled",
        annualization_factor=8760, execution_lag=1, compute_counterfactual=False,
    )
    result = run_backtest(cfg, md, so)
    assert idx[4] in result.unexecuted_rebalances


def test_window_a_unexecuted_rebalances_reported(window_a):
    print(f"\nspec §4.8: Window A unexecuted_rebalances = {window_a.result.unexecuted_rebalances}")
    assert isinstance(window_a.result.unexecuted_rebalances, list)


def test_terminal_bar_lengths(window_a):
    r = window_a.result
    n = len(window_a.frame_index)
    assert len(r.equity_curve) == n
    assert len(r.net_return) == n - 1


def test_terminal_bar_earns_nothing(window_a):
    """contract §4.4 — 'Terminal valuation instant: T_{n-1}. No trade occurs
    and bar n-1 earns no return.' `net_return` has only `n-1` entries (period
    indices 0..n-2), so there is structurally no period indexed `n-1`. Also
    confirms `equity_curve[-1]` is exactly the last period's own carried
    NAV_end (contract §8's Step-12 carry, TOLERANCE per §17 -- reconstructed
    via a DIFFERENT arithmetic path, `equity[-2] * (1 + net_return[-1])`,
    not the same stored double)."""
    r = window_a.result
    n = len(window_a.frame_index)
    assert len(r.net_return) == n - 1
    reconstructed = r.equity_curve.iloc[-2] * (1.0 + r.net_return.iloc[-1])
    assert r.equity_curve.iloc[-1] == pytest.approx(reconstructed, rel=1e-12, abs=1e-9)


def test_funding_events_excluded_naive_two_term_identity_does_not_hold(window_a):
    """Documents the flagged finding above: the LITERAL two-term identity
    fails on Window A because of genuine flat (INACTIVE) stretches."""
    r = window_a.result
    event_ts = _load_independent_funding_timestamps()
    T0, T_last = window_a.frame_index[0], window_a.frame_index[-1]
    total_loaded = len(event_ts)
    charged = _count_charged_events(window_a, event_ts, T0, T_last)
    naive_sum = charged + r.funding_events_excluded
    assert naive_sum != total_loaded, (
        "expected the naive two-term identity to FAIL on this fixture (flagged spec finding); "
        "if this now passes, the fixture's activity pattern changed and the finding should be re-examined"
    )


def test_funding_events_excluded_correct_three_term_identity_EXACT(window_a):
    """The CORRECT identity: charged + skipped_due_to_inactivity + excluded
    == total events loaded. EXACT integer equality (spec §4.8). Both
    `charged`/`skipped_inactive` and `total_loaded` are counted
    INDEPENDENTLY of the engine's own funding-events adapter (D2 fix, v1.1
    repair cycle 2) -- see the module-level note above.

    **Flagged measured finding (D2, v1.1 repair cycle 2, honesty over
    forcing a result).** This AGGREGATE three-term identity is, on the
    frozen Window A dataset, VACUOUS specifically against mutation M12
    (`spec §4.3, table row M12`: every loaded `FundingEvent.timestamp`
    shifted `+1h` in the pipeline adapter). Verified independently (pure
    Python against the raw parquet, reproduced separately by actually
    running the mutated engine): `total_loaded=28056` is unaffected by a
    pure timestamp shift (event count is conserved), and
    `funding_events_excluded` is measured to be EXACTLY 23545 both with and
    without the M12 shift -- a genuine coincidence in this real dataset: one
    boundary event (2026-01-24T23:00:00.017Z, originally excluded as
    `< T0`) shifts to `>= T0` and becomes included, while a second boundary
    event (2026-07-31T22:00:00.004Z, originally included) shifts to
    `>= T_last` and becomes excluded -- a net-zero change that leaves this
    SCALAR identity unable to discriminate M12. `charged`/`skipped_inactive`
    are independent of the mutation by construction (bucketed from
    unshifted raw timestamps) and therefore cannot detect it either. This is
    NOT limited to the aggregate identity: because Hyperliquid funding fires
    at a near-exactly-hourly cadence matching the 1h bar grid, a uniform
    `+1h` (one bar width) shift is close to a pure re-labelling of
    which-event-lands-in-which-period, so even the finer PER-PERIOD
    presence/absence check below is ALSO measured to be vacuous against
    M12 specifically. M12 IS discriminated elsewhere in the suite --
    `test_manual_verification.py`'s full-path engine-vs-reconstruction
    `funding_pnl_cash` assertion breaks under M12 (it compares the ACTUAL
    VALUE contributed by whichever specific event lands in each period, not
    merely a count), which is the correct place to catch a mutation that
    reassigns events between periods on an hourly-cadence fixture. Per spec
    §4.3.1 ('a vacuity check must itself be able to fail' / must be
    REPORTED, never silently passed): this is reported, not hidden, and the
    assertion below is NOT loosened or re-engineered to force a false
    positive against M12.
    """
    r = window_a.result
    event_ts = _load_independent_funding_timestamps()
    T0, T_last = window_a.frame_index[0], window_a.frame_index[-1]
    total_loaded = len(event_ts)
    charged, skipped_inactive = _count_charged_and_skipped(window_a, event_ts, T0, T_last)
    assert charged + skipped_inactive + r.funding_events_excluded == total_loaded


def test_funding_events_excluded_per_period_presence_matches_raw_independent(window_a):
    """Strengthens D2 beyond the aggregate three-term identity: for every
    ACTIVE period (spec §7.7.1 mask), whether the ENGINE actually charged
    ANY funding (`funding_pnl_cash != 0.0`) MUST agree, period-by-period,
    with whether the RAW, adapter-INDEPENDENT parquet has at least one event
    timestamp bucketed into that period's `[T_i, T_{i+1})` window. This is a
    genuine (if, per the finding above, not M12-specific) integer/boolean
    count check per spec §4.1.1, and is strictly finer-grained than the
    window-level aggregate identity above.
    """
    r = window_a.result
    event_ts = _load_independent_funding_timestamps()
    frame_idx = window_a.frame_index
    n = len(frame_idx)
    active = r.symbol_state["BTC"].isin(["ENTERING", "HELD"]).to_numpy()
    fpc = r.funding_pnl_cash.to_numpy()
    for i in range(n - 1):
        if not active[i]:
            continue
        lo = bisect.bisect_left(event_ts, frame_idx[i])
        hi = bisect.bisect_left(event_ts, frame_idx[i + 1])
        has_event_raw = (hi - lo) > 0
        engine_charged = fpc[i] != 0.0
        assert has_event_raw == engine_charged, (
            f"period i={i}: raw-independent event presence ({has_event_raw}) "
            f"disagrees with engine funding_pnl_cash != 0 ({engine_charged})"
        )


def _count_charged_events(window_a, event_ts, T0, T_last) -> int:
    charged, _ = _count_charged_and_skipped(window_a, event_ts, T0, T_last)
    return charged


def _count_charged_and_skipped(window_a, event_ts, T0, T_last):
    r = window_a.result
    active = r.symbol_state["BTC"].isin(["ENTERING", "HELD"]).to_numpy()
    frame_idx = window_a.frame_index
    n = len(frame_idx)
    ev_times = sorted(t for t in event_ts if T0 <= t < T_last)
    charged = 0
    skipped_inactive = 0
    for i in range(n - 1):
        lo = bisect.bisect_left(ev_times, frame_idx[i])
        hi = bisect.bisect_left(ev_times, frame_idx[i + 1])
        cnt = hi - lo
        if active[i]:
            charged += cnt
        else:
            skipped_inactive += cnt
    return charged, skipped_inactive


def test_counterfactual_isolation_bit_identical_9_5_2(window_a, window_a_no_cf):
    """contract §9.5.2 — actual-path outputs MUST be bit-identical regardless
    of `compute_counterfactual`."""
    r_true = window_a.result
    r_false = window_a_no_cf.result
    assert r_false.counterfactual_status == "NOT_COMPUTED"
    assert r_true.counterfactual_status != "NOT_COMPUTED"
    for field in ("equity_curve", "net_return", "gross_return", "fee_return", "slippage_return", "funding_return", "turnover", "gross_exposure"):
        a = getattr(r_true, field).to_numpy()
        b = getattr(r_false, field).to_numpy()
        np.testing.assert_array_equal(a, b, err_msg=f"actual-path field {field!r} differs by compute_counterfactual (§9.5.2)")
    for frame_field in ("quantity", "positions", "trades"):
        a = getattr(r_true, frame_field).to_numpy()
        b = getattr(r_false, frame_field).to_numpy()
        np.testing.assert_array_equal(a, b, err_msg=f"actual-path frame {frame_field!r} differs by compute_counterfactual (§9.5.2)")
    assert r_true.ruined == r_false.ruined
    assert r_true.metrics["total_return"] == r_false.metrics["total_return"]
