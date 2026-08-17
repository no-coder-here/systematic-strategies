"""QR-SMOKE-001 v1.0 FROZEN — experiment driver.

Runs Window A, B1, B2 (spec §2.2) plus the Window C cross-venue comparison
(spec §4.4) against the REAL, persisted, OFFLINE data layer and the REAL
engine, and writes summary artifacts (headline numbers + provenance) to
`experiments/qr_smoke_001/artifacts/`.

    .venv/bin/python -m experiments.qr_smoke_001.run_all

Per spec §2.2, this MUST NOT re-fetch Hyperliquid candles: every provider is
constructed `offline=True`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .crossvenue import crossvenue_report
from .pipeline import TARGET_EXECUTION_VENUE, WindowRun, run_window_a, run_window_b1, run_window_b2

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"


def _json_default(o):
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def _dataset_provenance_dict(p) -> dict:
    """spec §4.5 v1.1 — per-dataset provenance fields that MUST survive to
    the serialized artifact: source_venue, native_or_proxy, proxy_for,
    dataset_id, processing_version (plus field_type for disambiguation)."""
    return {
        "field_type": p.field_type,
        "source_venue": p.source_venue,
        "native_or_proxy": p.native_or_proxy,
        "proxy_for": p.proxy_for,
        "dataset_id": p.dataset_id,
        "processing_version": p.processing_version,
    }


def _universe_provenance_dict(u) -> dict:
    """spec §4.5 v1.1 — universe provenance including `survivorship_safe`."""
    if u is None:
        return None
    return {
        "universe_source": u.universe_source,
        "universe_asof_policy": u.universe_asof_policy,
        "listing_data_source": u.listing_data_source,
        "survivorship_safe": u.survivorship_safe,
        "notes": u.notes,
    }


def summarize(run: WindowRun) -> dict:
    r = run.result
    m = r.metrics
    return {
        "name": run.name,
        "n_raw_bars": len(run.raw_index),
        "n_frame_bars": len(run.frame_index),
        "n_periods": len(r.net_return),
        "frame_start": run.frame_index[0].isoformat(),
        "frame_end": run.frame_index[-1].isoformat(),
        "first_frame_signal": run.first_frame_signal,
        "total_return": m["total_return"],
        "annualized_volatility": m.get("annualized_volatility"),
        "sharpe": m.get("sharpe"),
        "sortino": m.get("sortino"),
        "max_drawdown": m.get("max_drawdown"),
        # spec §4.9 — cagr/calmar suppressed/footnoted, never headline numbers.
        "cagr_footnote": "suppressed per spec §4.9 (meaningless / can raise OverflowError on short/long-af samples)",
        "trade_count": int(r.rebalance_flag.sum()),
        "unexecuted_rebalances": [t.isoformat() for t in r.unexecuted_rebalances],
        "funding_modelled": r.funding_modelled,
        "funding_notional_basis": r.funding_notional_basis,
        "funding_events_excluded": r.funding_events_excluded,
        "funding_gap_tolerance_suspicious": r.funding_gap_tolerance_suspicious,
        "max_gross_exposure": float(r.gross_exposure.max()) if len(r.gross_exposure) else None,
        "leverage_breach": r.leverage_breach,
        "n_leverage_breach_periods": len(r.leverage_breach_timestamps),
        "ruined": r.ruined,
        "uses_proxy_data": r.uses_proxy_data,
        "survivorship_safe": r.survivorship_safe,
        "provenance_complete": r.provenance_complete,
        "counterfactual_status": r.counterfactual_status,
        "total_drag_return": r.total_drag_return,
        "drag_comparable": r.drag_comparable,
        # spec §4.5 v1.1 (BD-A) — provenance MUST survive serialization to
        # the artifact, not merely be set in memory. Read back and asserted
        # in tests/qr_smoke_001/test_qr_provenance.py.
        "provenance": {
            "target_execution_venue": TARGET_EXECUTION_VENUE,
            "datasets": [_dataset_provenance_dict(p) for p in run.dataset_provenance],
            "universe": _universe_provenance_dict(run.universe_provenance),
            "funding_notional_basis": run.config.funding_notional_basis,
            "funding_modelled": r.funding_modelled,
            "uses_proxy_data": r.uses_proxy_data,
        },
        "config": {
            "fee_bps": run.config.fee_bps,
            "slippage_bps": run.config.slippage_bps,
            "execution_mode": run.config.execution_mode,
            "execution_lag": run.config.execution_lag,
            "funding_mode": run.config.funding_mode,
            "funding_notional_basis": run.config.funding_notional_basis,
            "max_gross_leverage": run.config.max_gross_leverage,
        },
    }


def main(base_dir: str = "data") -> dict:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    a = run_window_a(base_dir)
    b1 = run_window_b1(base_dir)
    b2 = run_window_b2(base_dir)
    cv = crossvenue_report(base_dir)

    out = {
        "window_a": summarize(a),
        "window_b1": summarize(b1),
        "window_b2": summarize(b2),
        "crossvenue": {
            "rho_by_lag": cv["alignment"].rho_by_lag,
            "rho_0": cv["alignment"].rho_0,
            "argmax_lag": cv["alignment"].argmax_lag,
            "signal_agreement_rate": cv["signal_comparison"]["agreement_rate"],
            "n_differing_signals": cv["signal_comparison"]["n_differing"],
            # spec §4.4 v1.1 (W4) — the differing-signal analysis MUST be
            # written to the artifact, not merely computed: the timestamps
            # plus close/SMA100/relative-margin on BOTH venues.
            "differing_signal_detail": cv["signal_comparison"]["differing_signal_detail"],
            "hl_transitions": cv["signal_comparison"]["hl_transitions"],
            "binance_transitions": cv["signal_comparison"]["binance_transitions"],
            "hl": cv["hl"],
            "binance": cv["binance"],
        },
    }

    with open(ARTIFACT_DIR / "summary.json", "w") as f:
        json.dump(out, f, indent=2, default=_json_default, sort_keys=True)

    print(json.dumps(out, indent=2, default=_json_default, sort_keys=True))
    return out


if __name__ == "__main__":
    main()
