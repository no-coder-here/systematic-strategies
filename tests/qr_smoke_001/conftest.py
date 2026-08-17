"""QR-SMOKE-001 test fixtures. All runs are OFFLINE against the persisted
snapshot under `data/` (spec §2.2 — no re-fetch of Hyperliquid candles).
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from experiments.qr_smoke_001 import pipeline, reconstruction
from experiments.qr_smoke_001.pipeline import run_window_a, run_window_b1, run_window_b2

BASE_DIR = "data"


@pytest.fixture(scope="session")
def window_a():
    return run_window_a(BASE_DIR)


@pytest.fixture(scope="session")
def window_a_no_cf():
    return run_window_a(BASE_DIR, compute_counterfactual=False)


@pytest.fixture(scope="session")
def window_b1():
    return run_window_b1(BASE_DIR)


@pytest.fixture(scope="session")
def window_b2():
    return run_window_b2(BASE_DIR)


@pytest.fixture(scope="session")
def manual_periods(window_a):
    """spec §4.1 — the FULL independent reconstruction of every period in
    Window A (not just the 3 selected-for-reporting indices). Session-scoped
    and shared across test modules (test_manual_verification.py's full-path
    assertion (W1) and test_vacuity.py's M20 funding-boundary coverage) so
    the (~4511-period, pure-Python) reconstruction runs exactly once."""
    raw_start, raw_end = pipeline.eval_window_to_raw_window(
        pipeline.WINDOW_A_EVAL_START, pipeline.WINDOW_A_EVAL_END
    )
    raw = reconstruction.load_raw_ohlcv(
        BASE_DIR, "hyperliquid", "BTC", raw_start, raw_end + pd.Timedelta(hours=1)
    )
    raw_sig = reconstruction.compute_sma_and_signal(raw, 100)
    funding = reconstruction.load_raw_funding(
        BASE_DIR, "BTC", pd.Timestamp("2023-05-01", tz="UTC"), pd.Timestamp("2026-08-20", tz="UTC")
    )
    cfg = reconstruction.ManualConfig(
        execution_lag=1,
        fee_bps=4.5,
        slippage_bps=1.0,
        funding_notional_basis="period_start",
        initial_capital=1_000_000.0,
    )
    return reconstruction.reconstruct_path(raw_sig, funding, 99, cfg)


@pytest.fixture(scope="session")
def written_summary_artifact():
    """spec §4.5 v1.1 (BD-A) — runs the REAL experiment driver
    (`experiments.qr_smoke_001.run_all.main`), which writes
    `artifacts/summary.json` to disk, then reads that file BACK FROM DISK.
    Provenance tests MUST assert against this fixture, not against an
    in-memory dict, per the v1.1 correction ('asserting three booleans in
    memory is not compliance')."""
    from experiments.qr_smoke_001 import run_all

    run_all.main(BASE_DIR)
    with open(run_all.ARTIFACT_DIR / "summary.json") as f:
        return json.load(f)
