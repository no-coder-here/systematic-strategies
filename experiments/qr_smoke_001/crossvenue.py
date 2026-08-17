"""QR-SMOKE-001 spec §4.4 — Binance proxy vs Hyperliquid native (Window C = Window A).

Pinned experimental design (spec §4.4, W5):

1. Each run derives its OWN signal from its OWN venue's closes and executes
   at its OWN venue's opens (confounds signal + execution effects; the
   differing-signal analysis below is what separates them).
2. Both runs charge the SAME Hyperliquid-native funding events, with
   `period_start` notional computed on THAT run's own price frame.
3. Identical bar index (W4): asserted, not assumed.

The alignment test is FALSIFIABLE (W3): `argmax_l rho(l) == 0` (EXACT) and
`rho(0) >= 0.99`, both asserted, not merely reported. Failure of either is
an automatic SMOKE FAIL (spec §4.4, §6).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from data.binance.provider import BinanceUMProvider
from data.hyperliquid.provider import HyperliquidProvider

from .pipeline import (
    SYMBOL,
    WINDOW_A_EVAL_END,
    WINDOW_A_EVAL_START,
    WindowRun,
    binance_ohlcv_provenance,
    hl_funding_provenance,
    hl_ohlcv_provenance,
    load_full_hl_funding,
    _run,
)

LAGS = (-2, -1, 0, 1, 2)


def run_window_c(base_dir="data", compute_counterfactual: bool = True) -> tuple[WindowRun, WindowRun]:
    """Runs Window C = Window A on BOTH venues, unchanged strategy, no splice."""
    hl = HyperliquidProvider(offline=True, storage_base_dir=base_dir)
    binance = BinanceUMProvider(offline=True, storage_base_dir=base_dir)
    funding_events, funding_coverage = load_full_hl_funding(base_dir)

    hl_run = _run(
        name="C-hyperliquid",
        price_provider=hl,
        eval_start=WINDOW_A_EVAL_START,
        eval_end=WINDOW_A_EVAL_END,
        funding_mode="required",
        funding_notional_basis="period_start",
        dataset_provenance=(hl_ohlcv_provenance(base_dir), hl_funding_provenance(base_dir)),
        funding_events=funding_events,
        funding_coverage=funding_coverage,
        compute_counterfactual=compute_counterfactual,
    )
    binance_run = _run(
        name="C-binance",
        price_provider=binance,
        eval_start=WINDOW_A_EVAL_START,
        eval_end=WINDOW_A_EVAL_END,
        funding_mode="required",
        funding_notional_basis="period_start",
        # rule 2 (spec §4.4): Hyperliquid is the execution venue in BOTH
        # cases; the Binance run applies native HL funding to a
        # proxy-priced notional (labelled as such via dataset_provenance).
        dataset_provenance=(binance_ohlcv_provenance(base_dir), hl_funding_provenance(base_dir)),
        funding_events=funding_events,
        funding_coverage=funding_coverage,
        compute_counterfactual=compute_counterfactual,
    )
    return hl_run, binance_run


def assert_identical_bar_index(hl_run: WindowRun, binance_run: WindowRun) -> None:
    """spec §4.4 rule 3 (W4) — EXACT identical bar index, warm-up length, config."""
    if not hl_run.frame_index.equals(binance_run.frame_index):
        raise AssertionError("spec §4.4: HL and Binance frame indices are not identical (W4)")
    if len(hl_run.raw_index) != len(binance_run.raw_index):
        raise AssertionError("spec §4.4: HL and Binance raw (warm-up-inclusive) lengths differ (W4)")
    if hl_run.config != binance_run.config:
        raise AssertionError("spec §4.4: HL and Binance configs are not identical (W4)")


def log_return(close: pd.Series) -> pd.Series:
    return np.log(close).diff().dropna()


def lagged_correlation(hl_close: pd.Series, binance_close: pd.Series, lags=LAGS) -> dict:
    """rho(l): correlation of HL's log-return at t against Binance's log-return
    at t - l (l>0 => Binance LEADS; l<0 => Binance LAGS)."""
    hl_ret = log_return(hl_close)
    bn_ret = log_return(binance_close)
    aligned = pd.DataFrame({"hl": hl_ret, "bn": bn_ret}).dropna()
    out = {}
    for lag in lags:
        shifted = aligned["bn"].shift(lag)
        pair = pd.DataFrame({"hl": aligned["hl"], "bn": shifted}).dropna()
        out[lag] = float(pair["hl"].corr(pair["bn"])) if len(pair) > 1 else float("nan")
    return out


@dataclass(frozen=True)
class AlignmentResult:
    rho_by_lag: dict
    rho_0: float
    argmax_lag: int
    passes_argmax: bool
    passes_rho0: bool


def assert_alignment(hl_close: pd.Series, binance_close: pd.Series) -> AlignmentResult:
    rho = lagged_correlation(hl_close, binance_close)
    argmax_lag = max(rho, key=lambda l: rho[l])
    rho0 = rho[0]
    passes_argmax = argmax_lag == 0
    passes_rho0 = rho0 >= 0.99
    if not passes_argmax:
        raise AssertionError(f"spec §4.4 SMOKE FAIL: argmax_l rho(l) == {argmax_lag}, expected 0. rho={rho!r}")
    if not passes_rho0:
        raise AssertionError(f"spec §4.4 SMOKE FAIL: rho(0) == {rho0!r} < 0.99")
    return AlignmentResult(rho_by_lag=rho, rho_0=rho0, argmax_lag=argmax_lag, passes_argmax=passes_argmax, passes_rho0=passes_rho0)


def compare_signals(hl_run: WindowRun, binance_run: WindowRun) -> dict:
    """spec §4.4 — signal agreement rate; entry/exit counts; differing
    timestamps. v1.1 (W4): ALSO records, for every differing timestamp, the
    `close`, `SMA100` and relative margin `|close - SMA100| / SMA100` on
    BOTH venues, so the artifact carries the evidence needed to distinguish
    a marginal crossing from a structural disagreement, per spec §4.4.
    """
    from strategies.qr_smoke_001 import compute_sma, compute_signal

    hl_sma_full = compute_sma(hl_run.raw_close)
    bn_sma_full = compute_sma(binance_run.raw_close)
    hl_sig = compute_signal(hl_run.raw_close, hl_sma_full).loc[hl_run.frame_index]
    bn_sig = compute_signal(binance_run.raw_close, bn_sma_full).loc[binance_run.frame_index]

    agree = (hl_sig.to_numpy() == bn_sig.to_numpy())
    agreement_rate = float(agree.mean())
    differing_ts = list(hl_sig.index[~agree])

    def _venue_detail(ts: pd.Timestamp, close_full: pd.Series, sma_full: pd.Series) -> dict:
        close_v = float(close_full.loc[ts])
        sma_v = float(sma_full.loc[ts])
        rel_margin = abs(close_v - sma_v) / sma_v if sma_v != 0.0 else float("nan")
        return {"close": close_v, "sma100": sma_v, "relative_margin": rel_margin}

    differing_signal_detail = [
        {
            "timestamp": ts,
            "hl": _venue_detail(ts, hl_run.raw_close, hl_sma_full),
            "binance": _venue_detail(ts, binance_run.raw_close, bn_sma_full),
        }
        for ts in differing_ts
    ]

    def entry_exit_counts(sig: pd.Series) -> dict:
        arr = sig.to_numpy()
        transitions = np.diff(arr.astype(int))
        entries = int((transitions == 1).sum())
        exits = int((transitions == -1).sum())
        return {"entries": entries, "exits": exits}

    return {
        "agreement_rate": agreement_rate,
        "n_differing": int((~agree).sum()),
        "differing_timestamps": differing_ts,
        "differing_signal_detail": differing_signal_detail,
        "hl_transitions": entry_exit_counts(hl_sig),
        "binance_transitions": entry_exit_counts(bn_sig),
    }


def realized_vol(net_return: pd.Series, af: float) -> float:
    return float(net_return.std(ddof=1) * (af ** 0.5))


def repeated_close_count(close: pd.Series) -> int:
    """W6 — count of bars whose close bitwise repeats the PREVIOUS bar's
    close exactly. Measured: 17/4512 on Hyperliquid Window A, 0/4512 on
    Binance over the identical window. Reported, not treated as a defect
    (real trades with non-trivial volume are present at these bars; likely
    thin-hour HL prints rather than staleness)."""
    return int((close.diff() == 0.0).sum())


def crossvenue_report(base_dir="data", compute_counterfactual: bool = True) -> dict:
    hl_run, binance_run = run_window_c(base_dir, compute_counterfactual=compute_counterfactual)
    assert_identical_bar_index(hl_run, binance_run)
    alignment = assert_alignment(hl_run.raw_close.loc[hl_run.frame_index], binance_run.raw_close.loc[binance_run.frame_index])
    signal_cmp = compare_signals(hl_run, binance_run)

    return {
        "alignment": alignment,
        "signal_comparison": signal_cmp,
        "hl": {
            "trade_count": int(hl_run.result.rebalance_flag.sum()),
            "total_return": hl_run.result.metrics["total_return"],
            "max_drawdown": hl_run.result.metrics["max_drawdown"],
            "realized_volatility": realized_vol(hl_run.result.net_return, hl_run.config.annualization_factor),
            "repeated_close_count": repeated_close_count(hl_run.raw_close.loc[hl_run.frame_index]),
        },
        "binance": {
            "trade_count": int(binance_run.result.rebalance_flag.sum()),
            "total_return": binance_run.result.metrics["total_return"],
            "max_drawdown": binance_run.result.metrics["max_drawdown"],
            "realized_volatility": realized_vol(binance_run.result.net_return, binance_run.config.annualization_factor),
            "repeated_close_count": repeated_close_count(binance_run.raw_close.loc[binance_run.frame_index]),
        },
        "hl_run": hl_run,
        "binance_run": binance_run,
    }
