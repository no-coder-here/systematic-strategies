"""spec §4.6 — determinism, across separate PROCESSES and differing
`PYTHONHASHSEED`. Comparison method is PINNED (W19): `BacktestResult` is
`@dataclass(frozen=True, eq=False)`, so `r1 == r2` is ALWAYS `False` (identity
fallback). Compare via `.values.tobytes()` per field.
"""
from __future__ import annotations

import os
import pickle
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_WORKER_SCRIPT = r"""
import pickle, sys
from experiments.qr_smoke_001.pipeline import run_window_a

run = run_window_a("data", compute_counterfactual=True)
r = run.result
out = {
    "equity_curve": r.equity_curve.to_numpy().tobytes(),
    "net_return": r.net_return.to_numpy().tobytes(),
    "gross_return": r.gross_return.to_numpy().tobytes(),
    "fee_return": r.fee_return.to_numpy().tobytes(),
    "slippage_return": r.slippage_return.to_numpy().tobytes(),
    "funding_return": r.funding_return.to_numpy().tobytes(),
    "fee_cost": r.fee_cost.to_numpy().tobytes(),
    "funding_pnl_cash": r.funding_pnl_cash.to_numpy().tobytes(),
    "quantity": r.quantity.to_numpy().tobytes(),
    "turnover": r.turnover.to_numpy().tobytes(),
    "gross_exposure": r.gross_exposure.to_numpy().tobytes(),
    "rebalance_flag": r.rebalance_flag.to_numpy().tobytes(),
    "total_return": r.metrics["total_return"],
    "sharpe": r.metrics["sharpe"],
    "sortino": r.metrics["sortino"],
    "max_drawdown": r.metrics["max_drawdown"],
    "ruined": r.ruined,
    "counterfactual_status": r.counterfactual_status,
    "funding_events_excluded": r.funding_events_excluded,
}
with open(sys.argv[1], "wb") as f:
    pickle.dump(out, f)
"""


def _run_in_subprocess(tmp_path: Path, hashseed: int) -> dict:
    out_path = tmp_path / f"result_{hashseed}.pkl"
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(hashseed)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    subprocess.run(
        [sys.executable, "-c", _WORKER_SCRIPT, str(out_path)],
        check=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    with open(out_path, "rb") as f:
        return pickle.load(f)


def test_determinism_across_processes_and_hashseed(tmp_path):
    r1 = _run_in_subprocess(tmp_path, 0)
    r2 = _run_in_subprocess(tmp_path, 999983)  # different PYTHONHASHSEED

    byte_fields = [
        "equity_curve", "net_return", "gross_return", "fee_return",
        "slippage_return", "funding_return", "fee_cost", "funding_pnl_cash",
        "quantity", "turnover", "gross_exposure", "rebalance_flag",
    ]
    for field in byte_fields:
        assert r1[field] == r2[field], f"{field!r} differs across processes/PYTHONHASHSEED (§16/§4.6)"

    for scalar_field in ("total_return", "sharpe", "sortino", "max_drawdown", "ruined", "counterfactual_status", "funding_events_excluded"):
        assert r1[scalar_field] == r2[scalar_field], f"{scalar_field!r} differs across processes/PYTHONHASHSEED"

# NOTE (v1.1 repair, W5): a test named
# `test_determinism_check_discriminates_against_a_real_nondeterminism`
# previously lived here. It compared two HAND-WRITTEN byte strings
# (`(1.0).hex().encode()` vs `(2.0).hex().encode()`) and asserted they were
# unequal -- a tautology about Python's own `!=` operator that never
# exercised the pipeline, the subprocess harness, or `_run_in_subprocess` at
# all. It has been DELETED rather than kept as decoration: the genuinely
# discriminating test is `test_determinism_across_processes_and_hashseed`
# above, which runs the REAL pipeline twice, in separate processes, under
# different `PYTHONHASHSEED`, and would fail if any field were
# nondeterministic.
