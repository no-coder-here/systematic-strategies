"""Fee / slippage / turnover primitives (contract §5.6, §6.0 Steps 1-2, §6.2, §6.3).

Pure functions only. No engine state, no NAV ledger.
"""

from __future__ import annotations

import numpy as np


def compute_trade(
    quantity_prev: np.ndarray,
    w_target: np.ndarray,
    w_pre: np.ndarray,
) -> np.ndarray:
    """§6.0 Step 1.

    trade[j] = 0.0 if (q_prev[j] == 0 and w_target[j] == 0) else w_target[j] - w_pre[j]

    The zero branch is assigned literally, never computed, so it can never
    inherit a NaN from an unvalidated (INACTIVE) w_pre/w_target pair.
    """
    zero_branch = (quantity_prev == 0.0) & (w_target == 0.0)
    # Defence-in-depth: `np.where` evaluates BOTH branches eagerly, so
    # `w_target - w_pre` is computed even where `zero_branch` is True. This
    # is not load-bearing today because the caller (`_step_period`) never
    # supplies a NaN `w_pre`/`w_target` pair on this branch in the first
    # place (§5.3's INACTIVE prices are never read at all) — the `np.where`
    # selection is what makes the *result* correct regardless.
    trade = np.where(zero_branch, 0.0, w_target - w_pre)
    return trade


def compute_turnover(trade: np.ndarray) -> float:
    """§6.3 — one-way, fraction of NAV, no factor of 0.5."""
    return float(np.sum(np.abs(trade)))


def fee_cost(turnover: float, nav_pre: float, fee_bps: float) -> float:
    """§6.0 Step 2."""
    return turnover * nav_pre * fee_bps / 10_000.0


def slippage_cost(turnover: float, nav_pre: float, slippage_bps: float) -> float:
    """§6.0 Step 2."""
    return turnover * nav_pre * slippage_bps / 10_000.0


def fee_basis_notional(turnover: float, nav_pre: float) -> float:
    """§6.2 — `turnover[i] * NAV_pre[i]` is the fee basis, NOT traded notional."""
    return turnover * nav_pre
