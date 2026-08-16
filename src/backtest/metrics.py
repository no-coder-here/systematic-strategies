"""Performance metrics (contract §12). Pure functions, no engine dependency.

Consumes the authoritative equity ledger and the derived `net_return` series
(both supplied by the caller — this module does not know about NAV, costs,
funding or the accounting sequence at all).
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def compute_metrics(
    net_return: np.ndarray,
    equity_curve: np.ndarray,
    turnover: np.ndarray,
    af: float,
    risk_free_per_period: float = 0.0,
    mar_per_period: float = 0.0,
) -> dict:
    """§12 — pinned formulas, §12.3 degenerate cases, §12.4 ruin behaviour.

    `net_return`, `equity_curve` and `turnover` are 1-D arrays. `equity_curve`
    has one more element than `net_return` and `turnover` (§8).
    """
    net_return = np.asarray(net_return, dtype=float)
    equity_curve = np.asarray(equity_curve, dtype=float)
    turnover = np.asarray(turnover, dtype=float)

    n_periods = net_return.shape[0]

    with np.errstate(all="ignore"):
        # BD-4: the exponentiation MUST be inside the suppressed errstate
        # block. A short, high-frequency sample (e.g. af=8760, n_periods=2)
        # can overflow this power to `inf` (§21 B5, accepted spec debt — the
        # `inf` itself is NOT converted to `nan`, only the RuntimeWarning is
        # suppressed so a `-W error` run does not fail on an already-known,
        # documented degenerate case).
        total_return = float(equity_curve[-1] / equity_curve[0] - 1.0)
        cagr = float((equity_curve[-1] / equity_curve[0]) ** (af / n_periods) - 1.0)

        if n_periods < 2:
            # §12.3 — n_periods < 2 -> every dispersion-based metric is nan.
            annualized_volatility = float("nan")
            sharpe = float("nan")
            downside_dev_ann = float("nan")
            sortino = float("nan")
        else:
            std = np.std(net_return, ddof=1)
            annualized_volatility = float(std * np.sqrt(af))
            ann_excess_arith = float(np.mean(net_return - risk_free_per_period) * af)
            if annualized_volatility == 0.0:
                sharpe = float("nan")
            else:
                sharpe = float(ann_excess_arith / annualized_volatility)

            downside = np.minimum(net_return - mar_per_period, 0.0)
            downside_dev_ann = float(np.sqrt(np.mean(downside ** 2)) * np.sqrt(af))
            ann_excess_mar_arith = float(np.mean(net_return - mar_per_period) * af)
            if downside_dev_ann == 0.0:
                sortino = float("nan")
            else:
                sortino = float(ann_excess_mar_arith / downside_dev_ann)

        running_max = np.maximum.accumulate(equity_curve)
        drawdown_series = equity_curve / running_max - 1.0
        max_drawdown = float(np.min(drawdown_series))

        if max_drawdown == 0.0:
            calmar = float("nan")
        else:
            calmar = float(cagr / abs(max_drawdown))

    avg_turnover = float(np.mean(turnover)) if turnover.shape[0] > 0 else float("nan")
    annualized_turnover = avg_turnover * af

    return {
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": annualized_volatility,
        "sharpe": sharpe,
        "downside_dev_ann": downside_dev_ann,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "avg_turnover": avg_turnover,
        "annualized_turnover": annualized_turnover,
    }
