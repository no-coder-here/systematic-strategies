"""QR-SMOKE-001 v1.0 FROZEN — strategy definition (spec §1).

A deliberately trivial BTC 1h long-or-flat SMA(100) crossover, built ONLY to
exercise the full pipeline (market data -> signal -> target weights ->
QR-INFRA-001 engine -> execution -> fees/slippage -> funding -> NAV). This
is NOT alpha research (spec §0): profitability is irrelevant and the `100`
window is FIXED by the frozen spec, never tuned, never compared to variants.

Interface obligation (spec §1.4; CLAUDE.md; backtest_contract.md §1): this
module returns ONLY `StrategyOutput(target_weights, rebalance_mask)`. It
MUST NOT compute PnL, NAV, equity, returns, fees, slippage or funding, and
MUST NOT contain strategy-specific accounting. All of that is the frozen
engine's sole responsibility.
"""

from __future__ import annotations

import pandas as pd

from backtest.models import DataIntegrityError, StrategyOutput

__all__ = [
    "SMA_WINDOW",
    "compute_sma",
    "compute_signal",
    "compute_target_weights",
    "build_strategy_output_for_frame",
]

# spec §1.1 — FROZEN. "no variants permitted": this is not a tunable
# parameter and MUST NOT be changed, swept or compared to alternatives.
SMA_WINDOW = 100


def compute_sma(close: pd.Series, window: int = SMA_WINDOW) -> pd.Series:
    """spec §1.1 —

        SMA100[t] = mean(close[t-99], ..., close[t])   # 100 COMPLETED bars, inclusive of t

    `min_periods == window`: a partial-window SMA is a defect (spec §1.1,
    "No backfill, no fudge") and MUST surface as NaN, never a value computed
    over fewer than `window` observations.
    """
    return close.rolling(window=window, min_periods=window).mean()


def compute_signal(close: pd.Series, sma: pd.Series | None = None) -> pd.Series:
    """spec §1.1 — signal[t] = close[t] > SMA100[t], STRICT inequality.

    Where `sma[t]` is NaN (a partial-window bar), pandas' `>` evaluates
    `False` rather than propagating NaN. That is harmless ONLY because the
    caller (`build_strategy_output_for_frame` below, and the harness's own
    frame slicing per spec §2.3) guarantees no partial-window row ever
    reaches a rebalance bar of the engine. This function does not itself
    perform that slicing — it is pure signal arithmetic.
    """
    if sma is None:
        sma = compute_sma(close)
    return close > sma


def compute_target_weights(close: pd.Series, symbol: str) -> pd.DataFrame:
    """spec §1.2 — target_weight[t, symbol] = 1.0 if signal[t] else 0.0.

    No shorting, no leverage above 1x, no scaling, no volatility targeting.
    Returned over `close`'s FULL index (including any partial-window NaN
    rows at the front) — slicing to the evaluated frame is the caller's job.
    """
    signal = compute_signal(close)
    return pd.DataFrame({symbol: signal.astype(float)}, index=close.index)


def build_strategy_output_for_frame(
    raw_close: pd.Series,
    frame_index: pd.DatetimeIndex,
    symbol: str = "BTC",
) -> StrategyOutput:
    """spec §1.2/§1.3 — builds the `StrategyOutput` handed to the engine.

    `raw_close` MUST be the FULL close series INCLUDING the warm-up bars
    (spec §1.1: SMA100[t] at the first evaluated bar needs the preceding 99
    completed bars' closes, which live before `frame_index[0]`). `SMA` and
    `signal` are computed over the full `raw_close` series so the first
    evaluated bar's SMA genuinely uses its warm-up history, then the
    resulting weights are sliced down to `frame_index` (spec §2.3 — warm-up
    bars are consumed by signal computation and are not part of the
    simulated sample) BEFORE `rebalance_on_change` is applied. This ordering
    matters: `rebalance_on_change` (spec §1.3, PINNED decision) emits `True`
    unconditionally on the first row of the frame IT IS GIVEN, so slicing
    first (rather than computing the mask over the full raw series and
    slicing the mask afterward) is what makes `frame_index[0]` the
    unconditional first rebalance bar, matching spec §1.3's stated behaviour.

    Raises `DataIntegrityError` if `frame_index` includes any bar whose
    SMA100 is not fully defined (spec §2.3 / §1.1: "a partial-window SMA is
    a defect").

    NOTE ON A SUBTLE TRAP (found via this module's own test suite): `close >
    NaN` evaluates to `False` in pandas/numpy, NOT `NaN` — a partial-window
    row therefore produces a perfectly well-formed-looking `target_weight ==
    0.0`, NEVER `NaN`. Checking the resulting WEIGHTS for `NaN` (as an
    earlier version of this function did) therefore CANNOT detect a
    partial-window bar smuggled into `frame_index` — it silently looks like
    an ordinary flat/no-signal bar. The guard below checks the SMA itself,
    not the weights derived from it.
    """
    sma_full = compute_sma(raw_close)
    missing = frame_index.difference(sma_full.index)
    if len(missing):
        raise DataIntegrityError(
            f"frame_index contains timestamps absent from raw_close: {list(missing[:3])!r}..."
        )
    if sma_full.loc[frame_index].isna().any():
        raise DataIntegrityError(
            "partial-window SMA100 (NaN) would reach a rebalance bar (spec §2.3 / §1.1, "
            "contract §3 'No NaN target may reach a rebalance bar'): frame_index must begin "
            "at or after the first fully-defined-SMA bar"
        )
    weights_full = compute_target_weights(raw_close, symbol)
    weights = weights_full.loc[frame_index]
    return StrategyOutput.rebalance_on_change(weights)
