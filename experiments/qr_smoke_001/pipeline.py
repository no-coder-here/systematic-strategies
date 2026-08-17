"""QR-SMOKE-001 v1.0 FROZEN — the harness/runner (spec §2, §3, §6).

Wires the REAL data layer (`src/data`) to the REAL strategy
(`strategies/qr_smoke_001`) to the REAL engine (`src/backtest`), producing
the Window A, B1 and B2 runs pinned in spec §2.2. Contains no accounting
logic of its own -- all PnL/NAV/fees/funding accounting is the engine's.

**Ambiguity flagged (see implementation report).** Spec §2.2's Window A row
literally prints:

    SMA warm-up:     2026-01-20 20:00:00Z -> 2026-01-24 23:00:00Z (100 bars, exact)
    evaluated frame: 2026-01-25 00:00:00Z -> 2026-07-31 23:00:00Z (4512 bars)

read literally, these are two ADJACENT, NON-OVERLAPPING 100-bar/4512-bar
windows. That contradicts backtest_contract.md §2.3/spec §4.7, which pin
`frame.index[0] == raw.index[99]` -- i.e. the frame's FIRST bar IS the last
warm-up bar (the one whose SMA first becomes fully defined), not the bar
after it. Taking the table at face value would make the evaluated frame's
first bar (2026-01-25 00:00) index 100 of the raw series, one bar later than
the contract's own index-99 rule and one bar later than §4.7's mandatory
`frame.index[0] == raw.index[99]` test can accept.

This implementation resolves the discrepancy by treating the **evaluated
frame's timestamps and count (4512 bars, 2026-01-25 00:00 -> 2026-07-31
23:00)** as authoritative -- it is the number that is independently checked
(4512) and the one that determines every downstream statistic (signal
transition counts, trade counts, PnL) -- and derives the raw load window
from it via the contract's own rule (`raw_start = eval_start - 99*Delta =
2026-01-20 21:00Z`, ONE HOUR LATER than the table's printed warm-up start of
20:00Z). The same resolution is applied to Window B1 ("warm-up consuming the
first 100 bars" is read as "consuming the first 99 bars plus the frame's own
first row", i.e. `len(frame) == len(raw) - 99` per contract §4.7, not `-100`)
and to Window B2 (whose single printed boundary, `2024-08-15 15:00Z`, is
read as the evaluated frame's start, i.e. `raw.index[99]`).

This is a 1-bar (Window A, B2) / 1-bar-count (B1) discrepancy against the
spec's own prose, on a fixture explicitly described as a pipeline smoke
test where profitability is irrelevant; it has no material effect on any
correctness conclusion, but IS flagged here rather than silently resolved,
per instruction, because it is a genuine internal inconsistency between two
FROZEN documents (spec §2.2 vs contract §2.3 / spec §4.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from backtest.engine import run_backtest
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    DataIntegrityError,
    DatasetProvenance,
    FundingCoverage,
    FundingEvent,
    MarketData,
    UniverseProvenance,
)

from data import storage
from data.base import to_engine_frame
from data.binance.provider import BinanceUMProvider
from data.hyperliquid.provider import HyperliquidProvider
from data.provenance import build_universe_provenance

SYMBOL = "BTC"
FREQUENCY = "1h"
SMA_WINDOW = 100  # spec §1.1, FROZEN

# spec §2.1 -- "target_execution_venue = Hyperliquid in all runs" (v1.1
# §4.5: this MUST survive to the serialized artifact).
TARGET_EXECUTION_VENUE = "Hyperliquid"

# ---------------------------------------------------------------------------
# spec §2.2 — PINNED window boundaries (evaluated-frame convention; see the
# module docstring for the resolved off-by-one ambiguity).
# ---------------------------------------------------------------------------

WINDOW_A_EVAL_START = pd.Timestamp("2026-01-25 00:00:00", tz="UTC")
WINDOW_A_EVAL_END = pd.Timestamp("2026-07-31 23:00:00", tz="UTC")  # inclusive bar label

WINDOW_B1_RAW_START = pd.Timestamp("2020-01-01 00:00:00", tz="UTC")
WINDOW_B1_RAW_END = pd.Timestamp("2026-07-31 23:00:00", tz="UTC")  # inclusive bar label

WINDOW_B2_EVAL_START = pd.Timestamp("2024-08-15 15:00:00", tz="UTC")
WINDOW_B2_EVAL_END = pd.Timestamp("2026-07-31 23:00:00", tz="UTC")  # inclusive bar label

# spec §3 — FROZEN engine configuration.
FEE_BPS = 4.5
SLIPPAGE_BPS = 1.0
INITIAL_CAPITAL = 1_000_000.0
MAX_GROSS_LEVERAGE = 1.05
ANNUALIZATION_FACTOR = 8760


@dataclass(frozen=True)
class WindowRun:
    name: str
    result: BacktestResult
    raw_index: pd.DatetimeIndex
    frame_index: pd.DatetimeIndex
    raw_close: pd.Series
    raw_open: pd.Series
    frame_md: MarketData
    config: BacktestConfig
    dataset_provenance: tuple
    universe_provenance: UniverseProvenance
    covering_funding_record: Optional[FundingCoverage]
    first_frame_signal: bool


def eval_window_to_raw_window(eval_start: pd.Timestamp, eval_end: pd.Timestamp, delta: pd.Timedelta = pd.Timedelta(hours=1)):
    """contract §2.3/§4.7 — `frame.index[0] == raw.index[99]`: the raw load
    MUST begin exactly `(SMA_WINDOW - 1)` bars before the evaluated frame's
    first bar so slicing at position `SMA_WINDOW - 1` reproduces it.
    """
    raw_start = eval_start - (SMA_WINDOW - 1) * delta
    return raw_start, eval_end


def load_raw_market_data(provider, symbol: str, raw_start: pd.Timestamp, raw_end_inclusive: pd.Timestamp) -> MarketData:
    """Fetches normalized OHLCV over `[raw_start, raw_end_inclusive]`
    (inclusive bar label) and hands off via the D§4.6/§2.4 bridge
    `to_engine_frame(..., policy="raise")` -- any grid gap surfaces as
    `DataIntegrityError`, never silently filled (spec §2.4).
    """
    delta = pd.Timedelta(hours=1)
    df = provider.get_ohlcv([symbol], FREQUENCY, raw_start, raw_end_inclusive + delta)
    md = to_engine_frame(df, FREQUENCY, policy="raise")
    return md


def slice_evaluated_frame(raw_md: MarketData, sma_window: int = SMA_WINDOW) -> MarketData:
    """contract §2.3 — the frame handed to the engine MUST begin at the
    first bar with a fully-defined SMA(window), i.e. index `window - 1` of
    the loaded raw series (verified by spec §4.7).
    """
    idx = raw_md.open.index
    if len(idx) <= sma_window - 1:
        raise DataIntegrityError(
            f"raw series too short to slice at index {sma_window - 1} (len={len(idx)})"
        )
    frame_index = idx[sma_window - 1 :]
    return MarketData(open=raw_md.open.loc[frame_index], close=raw_md.close.loc[frame_index])


def build_config(*, funding_mode: str, funding_notional_basis: str, compute_counterfactual: bool = True) -> BacktestConfig:
    """spec §3 — FROZEN engine configuration."""
    return BacktestConfig(
        frequency=FREQUENCY,
        annualization_factor=ANNUALIZATION_FACTOR,
        execution_mode="next_open",
        execution_lag=1,
        funding_mode=funding_mode,
        funding_notional_basis=funding_notional_basis,
        initial_capital=INITIAL_CAPITAL,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        risk_free_per_period=0.0,
        mar_per_period=0.0,
        compute_counterfactual=compute_counterfactual,
        max_gross_leverage=MAX_GROSS_LEVERAGE,
    )


def load_full_hl_funding(base_dir, symbol: str = SYMBOL) -> tuple[list, list]:
    """spec §2.2 funding-coverage window rule — loads funding over the
    ENTIRE available Hyperliquid funding history, which is (by construction)
    always strictly wider on both sides than any run's price window used in
    this work order.
    """
    hl = HyperliquidProvider(offline=True, storage_base_dir=base_dir)
    start = pd.Timestamp("2023-05-01", tz="UTC")
    end = pd.Timestamp("2026-08-20", tz="UTC")
    funding_df = hl.get_funding([symbol], start, end)
    coverage = hl.get_funding_coverage([symbol], start, end)
    events = [
        FundingEvent(timestamp=row.timestamp, symbol=row.symbol, funding_rate=float(row.funding_rate), notional_price=None)
        for row in funding_df.itertuples()
    ]
    return events, coverage


def assert_single_funding_coverage_record(
    coverage: Sequence[FundingCoverage], symbol: str, T0: pd.Timestamp, T_last: pd.Timestamp
) -> FundingCoverage:
    """spec §2.2 — 'the run MUST verify `coverage_start <= T_0` and
    `coverage_end >= T_{n-1}` within a SINGLE `FundingCoverage` record before
    running.' Mirrors (independently of the engine) `_find_covering_record`'s
    single-record requirement (contract §7.7.2 condition 2), performed here
    as an explicit pre-flight check with its own error message.
    """
    candidates = [
        c for c in coverage
        if c.symbol == symbol and c.coverage_start <= T0 and T_last <= c.coverage_end
    ]
    if len(candidates) != 1:
        raise DataIntegrityError(
            f"spec §2.2 funding-coverage window rule violated for {symbol!r}: expected exactly ONE "
            f"FundingCoverage record covering [{T0!r}, {T_last!r}], found {len(candidates)}"
        )
    return candidates[0]


def hl_ohlcv_provenance(base_dir) -> DatasetProvenance:
    prov = storage.read_provenance(base_dir, storage.ohlcv_dataset_id(FREQUENCY, SYMBOL))
    if prov is None:
        raise DataIntegrityError("missing Hyperliquid OHLCV provenance sidecar (D§9.2)")
    return prov.to_engine_provenance()


def hl_funding_provenance(base_dir) -> DatasetProvenance:
    prov = storage.read_provenance(base_dir, storage.funding_dataset_id(SYMBOL))
    if prov is None:
        raise DataIntegrityError("missing Hyperliquid funding provenance sidecar (D§9.2)")
    return prov.to_engine_provenance()


def binance_ohlcv_provenance(base_dir) -> DatasetProvenance:
    prov = storage.read_binance_provenance(base_dir, storage.binance_ohlcv_dataset_id(SYMBOL))
    if prov is None:
        raise DataIntegrityError("missing Binance OHLCV provenance sidecar (D§9.2)")
    return prov.to_engine_provenance()


def _first_frame_signal(raw_md: MarketData, frame_index: pd.DatetimeIndex, symbol: str) -> bool:
    from strategies.qr_smoke_001 import compute_signal

    sig = compute_signal(raw_md.close[symbol])
    return bool(sig.loc[frame_index[0]])


def _run(
    *,
    name: str,
    price_provider,
    eval_start: pd.Timestamp,
    eval_end: pd.Timestamp,
    funding_mode: str,
    funding_notional_basis: str,
    dataset_provenance: tuple,
    funding_events: list,
    funding_coverage: list,
    compute_counterfactual: bool = True,
) -> WindowRun:
    from strategies.qr_smoke_001 import build_strategy_output_for_frame

    raw_start, raw_end = eval_window_to_raw_window(eval_start, eval_end)
    raw_md = load_raw_market_data(price_provider, SYMBOL, raw_start, raw_end)
    frame_md = slice_evaluated_frame(raw_md)
    frame_index = frame_md.open.index

    covering_record = None
    if funding_mode == "required":
        delta = pd.Timedelta(hours=1)
        T0 = frame_index[0]
        T_last = frame_index[-1]  # execution_instant under next_open == bar label
        covering_record = assert_single_funding_coverage_record(funding_coverage, SYMBOL, T0, T_last)

    strategy_output = build_strategy_output_for_frame(raw_md.close[SYMBOL], frame_index, SYMBOL)
    first_signal = _first_frame_signal(raw_md, frame_index, SYMBOL)

    config = build_config(
        funding_mode=funding_mode,
        funding_notional_basis=funding_notional_basis,
        compute_counterfactual=compute_counterfactual,
    )

    result = run_backtest(
        config,
        frame_md,
        strategy_output,
        funding_events=funding_events,
        funding_coverage=funding_coverage,
        dataset_provenance=dataset_provenance,
        universe_provenance=build_universe_provenance(notes=f"QR-SMOKE-001 {name}"),
    )

    return WindowRun(
        name=name,
        result=result,
        raw_index=raw_md.open.index,
        frame_index=frame_index,
        raw_close=raw_md.close[SYMBOL],
        raw_open=raw_md.open[SYMBOL],
        frame_md=frame_md,
        config=config,
        dataset_provenance=dataset_provenance,
        universe_provenance=build_universe_provenance(notes=f"QR-SMOKE-001 {name}"),
        covering_funding_record=covering_record,
        first_frame_signal=first_signal,
    )


def run_window_a(base_dir="data", compute_counterfactual: bool = True) -> WindowRun:
    """spec §2.2 Window A — bounded HL-native validation window, HL funding required."""
    hl = HyperliquidProvider(offline=True, storage_base_dir=base_dir)
    funding_events, funding_coverage = load_full_hl_funding(base_dir)
    prov = (hl_ohlcv_provenance(base_dir), hl_funding_provenance(base_dir))
    return _run(
        name="A",
        price_provider=hl,
        eval_start=WINDOW_A_EVAL_START,
        eval_end=WINDOW_A_EVAL_END,
        funding_mode="required",
        funding_notional_basis="period_start",
        dataset_provenance=prov,
        funding_events=funding_events,
        funding_coverage=funding_coverage,
        compute_counterfactual=compute_counterfactual,
    )


def run_window_b1(base_dir="data", compute_counterfactual: bool = True) -> WindowRun:
    """spec §2.2 Window B1 — full Binance long history, funding NOT modelled."""
    binance = BinanceUMProvider(offline=True, storage_base_dir=base_dir)
    prov = (binance_ohlcv_provenance(base_dir),)
    return _run(
        name="B1",
        price_provider=binance,
        eval_start=WINDOW_B1_RAW_START + (SMA_WINDOW - 1) * pd.Timedelta(hours=1),
        eval_end=WINDOW_B1_RAW_END,
        funding_mode="disabled",
        funding_notional_basis="not_modelled",
        dataset_provenance=prov,
        funding_events=[],
        funding_coverage=[],
        compute_counterfactual=compute_counterfactual,
    )


def run_window_b2(base_dir="data", compute_counterfactual: bool = True) -> WindowRun:
    """spec §2.2 Window B2 — long funding-enabled run, Binance proxy prices,
    Hyperliquid-native funding (spec §2.2 W6 rationale / CLAUDE.md: Hyperliquid
    is the execution venue in every run)."""
    binance = BinanceUMProvider(offline=True, storage_base_dir=base_dir)
    funding_events, funding_coverage = load_full_hl_funding(base_dir)
    prov = (binance_ohlcv_provenance(base_dir), hl_funding_provenance(base_dir))
    return _run(
        name="B2",
        price_provider=binance,
        eval_start=WINDOW_B2_EVAL_START,
        eval_end=WINDOW_B2_EVAL_END,
        funding_mode="required",
        funding_notional_basis="period_start",
        dataset_provenance=prov,
        funding_events=funding_events,
        funding_coverage=funding_coverage,
        compute_counterfactual=compute_counterfactual,
    )
