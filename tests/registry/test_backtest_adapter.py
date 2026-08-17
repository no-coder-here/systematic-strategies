"""R§18.1(9) — `BacktestResult` registration, against a REAL `run_window_a()`
result (R§12.4.1). Covers M4 (dropped BacktestConfig field), M22 (data_start
from requested window instead of loaded raw span), M23 (datasets from caller
list instead of result.provenance), M24 (PROXY_DATA misrouted to
result_warnings), M27 (strict containment)."""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, MarketData, StrategyOutput
from data.provenance import PROCESSING_VERSION
from experiments.qr_smoke_001 import pipeline as qr_pipeline
from registry.backtest_adapter import _backtest_config_dict, _build_datasets, _check_window_containment, record_backtest_result, record_run
from registry.models import DatasetRef, SCHEMA_VERSION, ValidationError
from registry.store import ExperimentRegistry

from _factories import mk_code_identity, mk_strategy_ref


@pytest.fixture(scope="module")
def window_a():
    return qr_pipeline.run_window_a()


def _dataset_windows_for(run) -> dict:
    return {
        "hyperliquid.ohlcv.1h.BTC": {
            "data_start": run.raw_index[0],
            "data_end": run.raw_index[-1],
            "eval_start": run.frame_index[0],
            "eval_end": run.frame_index[-1],
            "symbols": ("BTC",),
            "content_hash": "a" * 64,
        },
        "hyperliquid.funding.BTC": {
            "data_start": pd.Timestamp("2023-05-01", tz="UTC"),
            "data_end": pd.Timestamp("2026-08-20", tz="UTC"),
            "eval_start": None,
            "eval_end": None,
            "symbols": ("BTC",),
            "content_hash": "b" * 64,
        },
    }


def test_M4_backtest_config_key_set_equals_dataclass_fields(window_a):
    d = _backtest_config_dict(window_a.result.config)
    assert set(d.keys()) == {f.name for f in dataclasses.fields(BacktestConfig)}


def test_datasets_derived_from_result_provenance(window_a):
    datasets = _build_datasets(window_a.result, _dataset_windows_for(window_a))
    ids = {d.dataset_id for d in datasets}
    assert ids == {"hyperliquid.ohlcv.1h.BTC", "hyperliquid.funding.BTC"}
    ohlcv = [d for d in datasets if d.dataset_id == "hyperliquid.ohlcv.1h.BTC"][0]
    assert ohlcv.native_or_proxy == "native"
    assert ohlcv.field_type == "ohlcv"


def test_M23_omitted_provenance_dataset_raises(window_a):
    windows = _dataset_windows_for(window_a)
    del windows["hyperliquid.funding.BTC"]
    with pytest.raises(ValidationError):
        _build_datasets(window_a.result, windows)


def test_M23_extra_caller_window_with_no_provenance_element_raises(window_a):
    windows = _dataset_windows_for(window_a)
    windows["some.unrelated.dataset"] = windows["hyperliquid.funding.BTC"]
    with pytest.raises(ValidationError):
        _build_datasets(window_a.result, windows)


def test_M22_data_start_is_the_loaded_raw_span_not_the_requested_window(window_a):
    """M22 target: R§4.4.2 — data_start/data_end MUST be the span actually
    READ (warm-up inclusive), not the requested/evaluated window. Measured:
    Window A raw start 2026-01-20 21:00Z != frame start 2026-01-25 00:00Z."""
    datasets = _build_datasets(window_a.result, _dataset_windows_for(window_a))
    ohlcv = [d for d in datasets if d.dataset_id == "hyperliquid.ohlcv.1h.BTC"][0]
    assert ohlcv.data_start == window_a.raw_index[0]
    assert ohlcv.data_start != window_a.frame_index[0]
    assert ohlcv.eval_start == window_a.frame_index[0]


def test_M27_containment_is_inclusive_on_the_real_result(window_a):
    """M27 target: BOTH bounds inclusive. equity_curve.index[0]==frame start,
    index[-1]==frame end, and Window A's raw span's tail coincides EXACTLY
    with the frame's tail (data_end == frame_end) — a strict `<`/`>`
    mutant would raise on this real record."""
    datasets = _build_datasets(window_a.result, _dataset_windows_for(window_a))
    ohlcv = [d for d in datasets if d.dataset_id == "hyperliquid.ohlcv.1h.BTC"][0]
    assert ohlcv.data_end == window_a.result.equity_curve.index[-1]  # self-guard: exact boundary case
    _check_window_containment(window_a.result, datasets)  # must not raise


def _zero_warmup_backtest_result():
    """R§21.4 fixture -- a minimal, REAL `BacktestResult` (via `run_backtest`,
    the production engine, never hand-constructed) whose evaluated frame's
    first bar is the natural frame start (no warm-up bars consumed)."""
    idx = pd.date_range("2026-01-01", periods=4, freq="1D", tz="UTC")
    prices = pd.DataFrame({"BTC": [100.0, 100.0, 200.0, 200.0]}, index=idx)
    md = MarketData(open=prices, close=prices)
    weights = pd.DataFrame({"BTC": [float("nan"), 1.0, float("nan"), float("nan")]}, index=idx)
    mask = pd.Series([False, True, False, False], index=idx)
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    config = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", fee_bps=0, slippage_bps=0,
        execution_mode="next_open", execution_lag=1, funding_mode="disabled",
        annualization_factor=365, compute_counterfactual=False,
    )
    return run_backtest(config, md, so)


def test_R21_4_containment_boundary_zero_warmup_reaches_production_check():
    """R§21.4 (blocking) -- R§20.8.5 was NOT actually closed: the v1.2
    fixture `test_R12_4_zero_warmup_boundary_data_start_equals_eval_start`
    (in test_r20_amendments.py) routes through `record_experiment` ->
    `DatasetRef.__post_init__` and NEVER reaches
    `backtest_adapter._check_window_containment` at all -- real Window A's
    raw start is strictly BEFORE its frame start, so no existing fixture
    exercises the exact `<=`/`>` boundary in the PRODUCTION containment
    check. This fixture calls `_check_window_containment` directly against a
    real `BacktestResult` with `data_start == eval_start ==
    equity_curve.index[0]` (zero warm-up) and `data_end == eval_end ==
    equity_curve.index[-1]`: mutating `backtest_adapter.py:104`'s `<=` to
    `<`, or `:114`'s `>` to `>=`, MUST make this raise even though the
    record is a perfectly valid, zero-warm-up dataset window."""
    result = _zero_warmup_backtest_result()
    frame_start, frame_end = result.equity_curve.index[0], result.equity_curve.index[-1]
    assert frame_start < frame_end  # self-guard: a real, non-degenerate frame

    ds = DatasetRef(
        dataset_id="hyperliquid.ohlcv.1d.BTC", source_venue="Hyperliquid", field_type="ohlcv",
        native_or_proxy="native", proxy_for=None, processing_version="qr-data-001-v1.3",
        dataset_version=None, retrieval_date=None,
        dataset_span_start=frame_start, dataset_span_end=frame_end,
        data_start=frame_start,  # ZERO WARM-UP: data_start == eval_start == frame_start
        data_end=frame_end,      # and data_end == eval_end == frame_end
        eval_start=frame_start, eval_end=frame_end,
        symbols=("BTC",), symbol_mapping=None, content_hash="a" * 64,
        content_hash_method="col-buffer-v1", provenance_notes=None,
    )
    _check_window_containment(result, (ds,))  # MUST NOT raise


def test_containment_violation_raises(window_a):
    windows = _dataset_windows_for(window_a)
    windows["hyperliquid.ohlcv.1h.BTC"]["eval_end"] = windows["hyperliquid.ohlcv.1h.BTC"]["eval_end"] - pd.Timedelta(hours=1)
    datasets = _build_datasets(window_a.result, windows)
    with pytest.raises(ValidationError):
        _check_window_containment(window_a.result, datasets)


def _code_identity():
    return mk_code_identity(
        contract_versions={
            "backtest_contract": "1.5.1",
            "data_contract": "1.4",
            "registry_schema": SCHEMA_VERSION,
            "data_processing_version": PROCESSING_VERSION,
        }
    )


def test_full_record_backtest_result_registration(tmp_path, window_a):
    reg = ExperimentRegistry(tmp_path / "registry")
    fe = record_backtest_result(
        reg,
        window_a.result,
        strategy=mk_strategy_ref(),
        dataset_windows=_dataset_windows_for(window_a),
        universe_policy="single_symbol_fixed:BTC",
        code_identity=_code_identity(),
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run="adapter test against a real Window A result",
        created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        n_configs_evaluated=1,
    )
    assert fe.status == "COMPLETED"
    assert set(fe.record.results.metrics.keys()) == set(window_a.result.metrics.keys())
    assert fe.record.results.n_periods == len(window_a.result.net_return)
    assert fe.record.results.rebalance_count == int(window_a.result.rebalance_flag.sum())


def test_M24_proxy_data_is_record_level_even_on_a_failed_run(tmp_path):
    """M24 target: PROXY_DATA MUST be record-level so it survives even when
    `results is None` (a FAILED record has no ResultSummary to carry a
    result-level token at all)."""
    from registry.models import DatasetRef

    reg = ExperimentRegistry(tmp_path / "registry")
    proxy_ds = DatasetRef(
        dataset_id="binance.ohlcv.1h.BTC", source_venue="Binance", field_type="ohlcv",
        native_or_proxy="proxy", proxy_for="Hyperliquid", processing_version="qr-data-001-v1.3",
        dataset_version=None, retrieval_date=None,
        dataset_span_start=pd.Timestamp("2020-01-01", tz="UTC"), dataset_span_end=pd.Timestamp("2026-08-01", tz="UTC"),
        data_start=pd.Timestamp("2024-08-11", tz="UTC"), data_end=pd.Timestamp("2026-07-31 23:00", tz="UTC"),
        eval_start=pd.Timestamp("2024-08-15 15:00", tz="UTC"), eval_end=pd.Timestamp("2026-07-31 23:00", tz="UTC"),
        symbols=("BTC",), symbol_mapping=None, content_hash="e" * 64, content_hash_method="col-buffer-v1",
        provenance_notes=None,
    )
    fe = reg.record_experiment(
        experiment_type="pipeline_validation", research_stage="exploratory",
        reason_for_run="proxy failed run", code_identity=_code_identity(),
        datasets=(proxy_ds,), universe_policy="single_symbol_fixed:BTC", survivorship_safe=False,
        strategy=mk_strategy_ref(), backtest_config={"funding_mode": "required", "funding_notional_basis": "period_start"},
        status="FAILED", status_reason="DataIntegrityError: boom", results=None,
        created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        n_configs_evaluated=1,
    )
    assert fe.status == "FAILED"
    assert fe.record.results is None
    assert "PROXY_DATA" in fe.warnings


def test_record_run_success_path(tmp_path, window_a):
    reg = ExperimentRegistry(tmp_path / "registry")
    with record_run(
        reg,
        strategy=mk_strategy_ref(),
        universe_policy="single_symbol_fixed:BTC",
        code_identity=_code_identity(),
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run="record_run success path",
        created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        dataset_windows=_dataset_windows_for(window_a),
        n_configs_evaluated=1,
    ) as run:
        run.set_result(window_a.result)
    ids = [fe.record.experiment_id for fe in reg.list_experiments()]
    assert len(ids) == 1
    assert reg.load_experiment(ids[0]).status == "COMPLETED"


def test_record_run_registers_FAILED_and_reraises(tmp_path):
    reg = ExperimentRegistry(tmp_path / "registry")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with record_run(
            reg,
            strategy=mk_strategy_ref(),
            universe_policy="single_symbol_fixed:BTC",
            code_identity=_code_identity(),
            experiment_type="infrastructure",
            research_stage="exploratory",
            reason_for_run="record_run failure path",
            created_at=pd.Timestamp("2026-08-17", tz="UTC"),
            no_datasets_reason="failed before any dataset could be attributed",
            backtest_config={},
            n_configs_evaluated=1,
        ):
            raise Boom("simulated pipeline failure")

    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "FAILED"
    assert records[0].record.status_reason == "Boom: simulated pipeline failure"
    assert "traceback_tail" in records[0].record.run_facts
