"""QR-SMOKE-001 spec §4.1 — INDEPENDENT manual-path reconstruction.

**Independence requirement (spec §4.1, BD8, W21).** This module MUST NOT
import `src/backtest/engine.py`, `src/backtest/costs.py`,
`src/backtest/metrics.py`, the QR-SMOKE-001 strategy/signal module
(`strategies/qr_smoke_001/**`), or the harness's price-frame construction
(`experiments/qr_smoke_001/pipeline.py`). It recomputes the SMA, the signal
and the execution price independently from the RAW normalized OHLCV parquet
files (`open`/`close` columns, `open` selected per contract §4.2 since every
run uses `execution_mode="next_open"`), reading them directly with
`pandas.read_parquet` rather than through any provider class.

Importing `backtest.models` is permitted and unavoidable per spec §4.1 (it
carries only dataclasses/exceptions, no accounting algebra) -- used here
purely for the `DataIntegrityError` type and nothing else.

This is a SECOND, independently-written implementation of
`backtest_contract.md` §6.0's accounting sequence, deliberately kept
single-symbol and scalar (no vectorized/generalized machinery shared with
the engine) so that the two implementations cannot share a bug by
construction. It is a validation instrument, not a third normative
accounting path: where it disagrees with the engine, THAT is the finding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from backtest.models import DataIntegrityError

__all__ = [
    "ManualConfig",
    "load_raw_ohlcv",
    "load_raw_funding",
    "compute_sma_and_signal",
    "reconstruct_path",
]


@dataclass(frozen=True)
class ManualConfig:
    execution_lag: int
    fee_bps: float
    slippage_bps: float
    funding_notional_basis: str  # "period_start" | "not_modelled"
    initial_capital: float
    sma_window: int = 100


def load_raw_ohlcv(base_dir, venue: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Reads the RAW normalized OHLCV parquet DIRECTLY (no provider class).

    `venue` is `"hyperliquid"` or `"binance"`. `end` is EXCLUSIVE (half-open,
    matching contract §2's bar semantics).
    """
    if venue == "hyperliquid":
        path = Path(base_dir) / "processed" / "hyperliquid" / "ohlcv" / "1h" / f"{symbol}.parquet"
    elif venue == "binance":
        path = Path(base_dir) / "processed" / "binance" / "ohlcv" / "1h" / f"{symbol}.parquet"
    else:
        raise ValueError(f"unknown venue {venue!r}")
    df = pd.read_parquet(path, columns=["timestamp", "symbol", "open", "close"])
    df = df.loc[df["symbol"] == symbol].sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    mask = (df["timestamp"] >= start) & (df["timestamp"] < end)
    df = df.loc[mask].reset_index(drop=True)
    if df["timestamp"].duplicated().any():
        raise DataIntegrityError("duplicate timestamps in raw OHLCV — cannot reconstruct")
    if not df["timestamp"].is_monotonic_increasing:
        raise DataIntegrityError("non-monotonic timestamps in raw OHLCV — cannot reconstruct")
    diffs = df["timestamp"].diff().dropna()
    if len(diffs) and (diffs != pd.Timedelta(hours=1)).any():
        raise DataIntegrityError("irregular grid in raw OHLCV — cannot reconstruct")
    return df


def load_raw_funding(base_dir, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Reads the RAW funding parquet directly. `end` is EXCLUSIVE."""
    path = Path(base_dir) / "processed" / "hyperliquid" / "funding" / f"{symbol}.parquet"
    df = pd.read_parquet(path, columns=["timestamp", "symbol", "funding_rate"])
    df = df.loc[df["symbol"] == symbol].sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    mask = (df["timestamp"] >= start) & (df["timestamp"] < end)
    return df.loc[mask].reset_index(drop=True)


def compute_sma_and_signal(raw: pd.DataFrame, window: int) -> pd.DataFrame:
    """spec §1.1, reconstructed independently: SMA100[t] over 100 COMPLETED
    bars inclusive of t; signal[t] = close[t] > SMA100[t], STRICT inequality.
    """
    out = raw.copy()
    out["sma"] = out["close"].rolling(window=window, min_periods=window).mean()
    out["signal"] = out["close"] > out["sma"]
    return out


@dataclass
class ManualPeriod:
    i: int
    signal_observation_ts: pd.Timestamp
    sma_value: float
    signal_value: bool
    target_weight: float
    rebalance_decision: bool
    execution_ts: pd.Timestamp
    execution_price: float
    w_pre: float
    quantity_pre: float
    trade: float
    turnover: float
    fee_cost: float
    slippage_cost: float
    fee_basis_notional: float
    nav_pre: float
    nav_after_cost: float
    quantity_post: float
    asset_pnl_cash: float
    funding_pnl_cash: float
    nav_end: float
    net_return: float
    gross_return: float
    fee_return: float
    slippage_return: float
    funding_return: float
    n_funding_events: int


def reconstruct_path(
    raw_with_signal: pd.DataFrame,
    funding: pd.DataFrame,
    frame_start_pos: int,
    cfg: ManualConfig,
) -> list[ManualPeriod]:
    """Independently re-executes contract §6.0 Steps 0-12 for a single,
    always-long-or-flat, never-shorting, single-symbol book, over the
    evaluated frame `raw_with_signal.iloc[frame_start_pos:]`.

    `raw_with_signal` is the FULL raw frame (with warm-up rows) carrying
    `sma`/`signal` columns from `compute_sma_and_signal`. `frame_start_pos`
    is the position of the first evaluated bar (contract §2.3 / §4.7:
    `frame.index[0] == raw.index[99]` for `sma_window=100`).

    Assumes no ruin occurs (verified separately) — this reconstruction does
    NOT implement §6.7's ruin branch; it raises if NAV would go non-positive,
    since that is not an expected occurrence for this smoke-test fixture and
    handling it independently is out of scope for a spot-check instrument.
    """
    frame = raw_with_signal.iloc[frame_start_pos:].reset_index(drop=True)
    n = len(frame)
    if n < 2:
        raise ValueError("evaluated frame too short to reconstruct any period")

    timestamps = frame["timestamp"].tolist()
    opens = frame["open"].to_numpy(dtype=float)
    signals = frame["signal"].to_numpy(dtype=bool)
    smas = frame["sma"].to_numpy(dtype=float)

    funding_events = list(zip(funding["timestamp"].tolist(), funding["funding_rate"].tolist()))
    fi = 0  # pointer into funding_events, sorted ascending

    execution_lag = cfg.execution_lag
    fee_bps = cfg.fee_bps
    slippage_bps = cfg.slippage_bps

    quantity_prev = 0.0
    prev_target: Optional[float] = None
    nav_pre = cfg.initial_capital

    periods: list[ManualPeriod] = []

    for i in range(n - 1):
        t_pos = i - execution_lag
        rebalance = False
        w_target = None
        if 0 <= t_pos <= n - 1:
            target_here = 1.0 if bool(signals[t_pos]) else 0.0
            # spec §1.3 — StrategyOutput.rebalance_on_change: True on the
            # frame's own first row unconditionally, else on any weight change.
            rebalance = (t_pos == 0) or (target_here != prev_target)
            if rebalance:
                w_target = target_here
            # prev_target is updated for every t_pos regardless of whether it
            # was itself a rebalance bar: `rebalance_on_change` (spec §1.3)
            # diffs CONSECUTIVE rows of the full weights series, not
            # consecutive rebalances.
            prev_target = target_here

        P_i = opens[i]
        P_ip1 = opens[i + 1]
        if not (math.isfinite(P_i) and P_i > 0):
            raise DataIntegrityError(f"invalid execution price at {timestamps[i]!r}: {P_i!r}")

        q_prev_zero = quantity_prev == 0.0
        w_pre = 0.0 if q_prev_zero else quantity_prev * P_i / nav_pre

        if rebalance:
            trade = 0.0 if (q_prev_zero and w_target == 0.0) else (w_target - w_pre)
            turnover = abs(trade)
        else:
            trade = 0.0
            turnover = 0.0

        fee_cost = turnover * nav_pre * fee_bps / 10_000.0
        slippage_cost = turnover * nav_pre * slippage_bps / 10_000.0
        fee_basis_notional = turnover * nav_pre
        nav_after_cost = nav_pre - fee_cost - slippage_cost
        if not math.isfinite(nav_after_cost) or nav_after_cost <= 0:
            raise ValueError(f"reconstruction hit non-finite/non-positive NAV_after_cost at i={i} — ruin path not implemented")

        if rebalance:
            quantity_i = 0.0 if w_target == 0.0 else w_target * nav_after_cost / P_i
        else:
            quantity_i = quantity_prev

        active = quantity_i != 0.0
        asset_pnl_cash = quantity_i * (P_ip1 - P_i) if active else 0.0

        # Funding (§7.5, period_start basis): T_i <= e.timestamp < T_{i+1}.
        T_i = timestamps[i]
        T_ip1 = timestamps[i + 1]
        n_events = 0
        funding_pnl_cash = 0.0
        if active and cfg.funding_notional_basis == "period_start":
            while fi < len(funding_events) and funding_events[fi][0] < T_i:
                fi += 1
            j = fi
            while j < len(funding_events) and funding_events[j][0] < T_ip1:
                ts_e, rate_e = funding_events[j]
                notional_e = quantity_i * P_i
                funding_pnl_cash += -notional_e * rate_e
                n_events += 1
                j += 1

        nav_end = nav_after_cost + asset_pnl_cash + funding_pnl_cash
        if not math.isfinite(nav_end) or nav_end <= 0:
            raise ValueError(f"reconstruction hit non-finite/non-positive NAV_end at i={i} — ruin path not implemented")

        net_return = nav_end / nav_pre - 1.0
        gross_return = asset_pnl_cash / nav_pre
        fee_return = -fee_cost / nav_pre
        slippage_return = -slippage_cost / nav_pre
        funding_return = funding_pnl_cash / nav_pre

        periods.append(
            ManualPeriod(
                i=i,
                signal_observation_ts=timestamps[t_pos] if 0 <= t_pos <= n - 1 else None,
                sma_value=float(smas[t_pos]) if 0 <= t_pos <= n - 1 else float("nan"),
                signal_value=bool(signals[t_pos]) if 0 <= t_pos <= n - 1 else None,
                target_weight=w_target if w_target is not None else float("nan"),
                rebalance_decision=rebalance,
                execution_ts=T_i,
                execution_price=P_i,
                w_pre=w_pre,
                quantity_pre=quantity_prev,
                trade=trade,
                turnover=turnover,
                fee_cost=fee_cost,
                slippage_cost=slippage_cost,
                fee_basis_notional=fee_basis_notional,
                nav_pre=nav_pre,
                nav_after_cost=nav_after_cost,
                quantity_post=quantity_i,
                asset_pnl_cash=asset_pnl_cash,
                funding_pnl_cash=funding_pnl_cash,
                nav_end=nav_end,
                net_return=net_return,
                gross_return=gross_return,
                fee_return=fee_return,
                slippage_return=slippage_return,
                funding_return=funding_return,
                n_funding_events=n_events,
            )
        )

        quantity_prev = quantity_i
        nav_pre = nav_end

    return periods
