"""QR-PREP-001 P§4.9 — `src/research/runner.py::run_research_experiment`.

One test per behaviour named in P§4.9, each independently mutation-provable
against the runner's own production code (the runner is a thin wrapper
around the FROZEN `registry.backtest_adapter.run_and_register`, so these
tests exercise the runner's own two refusals, its pass-through of
`run_and_register`'s COMPLETED/FAILED/INVALID guarantee, and its
non-mutation of the caller's `record_kwargs`).

Self-contained: does not import `tests/registry/_factories.py` (a different,
non-package test directory) to avoid a cross-directory import that would
depend on pytest's rootdir-insertion order.
"""
from __future__ import annotations

import copy
import json
import math

import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, DatasetProvenance, MarketData, StrategyOutput
from data.provenance import PROCESSING_VERSION
from registry.models import CodeIdentity, DatasetRef, SCHEMA_VERSION, StrategyRef
from registry.store import ExperimentRegistry
from research.runner import (
    ProtectedWindowOverlapError,
    ResearchRunnerError,
    RunNotRegisteredError,
    run_research_experiment,
)


def _metrics_equal(a: dict, b: dict) -> bool:
    """`==` on a `float('nan')` value is always `False`, even against
    itself -- a plain `dict ==` would falsely report divergence on this
    tiny fixture's `sharpe`/`sortino`/`calmar` (all NaN: zero trading
    days with a return). NaN-aware equality only, nothing else relaxed."""
    if a.keys() != b.keys():
        return False
    for k in a:
        va, vb = a[k], b[k]
        if isinstance(va, float) and isinstance(vb, float) and math.isnan(va) and math.isnan(vb):
            continue
        if va != vb:
            return False
    return True

CONTRACT_VERSIONS = {
    "backtest_contract": "1.5.1",
    "data_contract": "1.4",
    "registry_schema": SCHEMA_VERSION,
    "data_processing_version": PROCESSING_VERSION,
}


def _code_identity(**overrides) -> CodeIdentity:
    base = dict(
        git_commit="a" * 40,
        git_available=True,
        dirty_worktree=False,
        dirty_summary={},
        untracked_code_files=0,
        code_fingerprint="b" * 64,
        code_fingerprint_n_files=5,
        code_scope_patterns=("src/**/*.py",),
        contract_versions=dict(CONTRACT_VERSIONS),
    )
    base.update(overrides)
    return CodeIdentity(**base)


def _strategy_ref() -> StrategyRef:
    return StrategyRef(
        name="qr_prep_001_runner_test",
        version="1.0",
        params={},
        frequency="1d",
        target_execution_venue="Hyperliquid",
    )


def _tiny_config() -> BacktestConfig:
    """A zero-warm-up, real `BacktestConfig` -- deterministic, no engine
    randomness, so metrics are reproducibly comparable across two
    independent `run_backtest` calls."""
    return BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        fee_bps=0,
        slippage_bps=0,
        execution_mode="next_open",
        execution_lag=1,
        funding_mode="disabled",
        annualization_factor=365,
        compute_counterfactual=False,
    )


def _tiny_market_data_and_output():
    idx = pd.date_range("2026-01-01", periods=4, freq="1D", tz="UTC")
    prices = pd.DataFrame({"BTC": [100.0, 100.0, 200.0, 200.0]}, index=idx)
    md = MarketData(open=prices, close=prices)
    weights = pd.DataFrame({"BTC": [float("nan"), 1.0, float("nan"), float("nan")]}, index=idx)
    mask = pd.Series([False, True, False, False], index=idx)
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    return idx, md, so


_DATASET_ID = "test.ohlcv.1d.BTC"


def _dataset_provenance(idx):
    return (
        DatasetProvenance(
            source_venue="Hyperliquid",
            field_type="ohlcv",
            time_range=(idx[0], idx[-1]),
            native_or_proxy="native",
            dataset_id=_DATASET_ID,
            processing_version=PROCESSING_VERSION,
        ),
    )


def _dataset_ref(idx) -> DatasetRef:
    """A `DatasetRef` matching `_dataset_provenance(idx)`/the dataset_windows
    entry below -- pre-supplied on `record_kwargs['datasets']` so the
    FAILED/INVALID branches of `record_run` (which never see a
    `BacktestResult`, hence never derive `datasets` from `.provenance`) can
    still satisfy `experiment_type == 'alpha_research'`'s non-empty-datasets
    requirement (R§4.4.3/R§14.13 has no status-conditional exemption)."""
    return DatasetRef(
        dataset_id=_DATASET_ID,
        source_venue="Hyperliquid",
        field_type="ohlcv",
        native_or_proxy="native",
        proxy_for=None,
        processing_version=PROCESSING_VERSION,
        dataset_version=None,
        retrieval_date=None,
        dataset_span_start=idx[0],
        dataset_span_end=idx[-1],
        data_start=idx[0],
        data_end=idx[-1],
        eval_start=idx[0],
        eval_end=idx[-1],
        symbols=("BTC",),
        symbol_mapping=None,
        content_hash="a" * 64,
        content_hash_method="col-buffer-v1",
        provenance_notes=None,
    )


def _alpha_record_kwargs(idx, **overrides) -> dict:
    """A complete, valid `record_kwargs` for a run that will be forced to
    `experiment_type='alpha_research'` by the runner -- carries the
    `hypothesis_id`/`search_space_id` that type requires (R§14.6/R§20.5.1),
    and a pre-supplied `datasets` tuple (see `_dataset_ref` above) so
    FAILED/INVALID registration also succeeds for this type."""
    base = dict(
        strategy=_strategy_ref(),
        universe_policy="single_symbol_fixed:BTC",
        code_identity=_code_identity(),
        research_stage="exploratory",
        reason_for_run="QR-PREP-001 P§4 runner test",
        created_at=pd.Timestamp("2026-08-18", tz="UTC"),
        hypothesis_id="HYP-001",
        search_space_id="SS-001",
        n_configs_evaluated=1,
        datasets=(_dataset_ref(idx),),
        backtest_config={"funding_mode": "disabled", "funding_notional_basis": "not_modelled"},
        dataset_windows={
            _DATASET_ID: {
                "data_start": idx[0],
                "data_end": idx[-1],
                "eval_start": idx[0],
                "eval_end": idx[-1],
                "symbols": ("BTC",),
                "content_hash": "a" * 64,
            }
        },
    )
    base.update(overrides)
    return base


def _registry(tmp_path) -> ExperimentRegistry:
    return ExperimentRegistry(tmp_path / "registry")


# ---------------------------------------------------------------------------
# success path: COMPLETED, result returned unmodified
# ---------------------------------------------------------------------------


def test_success_path_registers_COMPLETED_and_returns_result_unmodified(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"
    assert records[0].record.experiment_type == "alpha_research"
    # "returns the result unmodified" here only means value-equal to an
    # independent re-run -- it does NOT prove object identity. See
    # `test_success_path_returns_the_identical_result_object` below for the
    # `is`-based check that actually pins identity (W3/S1b).
    reference = run_backtest(_tiny_config(), md, so, dataset_provenance=_dataset_provenance(idx))
    assert _metrics_equal(result.metrics, reference.metrics)
    assert result.net_return.equals(reference.net_return)


# ---------------------------------------------------------------------------
# exception path: FAILED, re-raised
# ---------------------------------------------------------------------------


def test_exception_registers_FAILED_and_reraises_original_exception(tmp_path, monkeypatch):
    import registry.backtest_adapter as BA

    class Boom(RuntimeError):
        pass

    def _boom(*a, **kw):
        raise Boom("synthetic run_backtest failure")

    monkeypatch.setattr(BA, "run_backtest", _boom)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(Boom, match="synthetic run_backtest failure"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "FAILED"
    assert records[0].record.status_reason == "Boom: synthetic run_backtest failure"


# ---------------------------------------------------------------------------
# no-result path: INVALID, not raised
# ---------------------------------------------------------------------------


def test_no_result_registers_INVALID_and_does_not_raise(tmp_path, monkeypatch):
    import registry.backtest_adapter as BA

    def _no_result(*a, **kw):
        return None

    monkeypatch.setattr(BA, "run_backtest", _no_result)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    returned = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert returned is None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "INVALID"
    assert records[0].record.status_reason == "NO_RESULT: run_and_register/set_result was never called"


# ---------------------------------------------------------------------------
# record_kwargs is copied, never mutated
# ---------------------------------------------------------------------------


def test_caller_record_kwargs_dict_unchanged_after_call(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    before = copy.deepcopy(kwargs)
    run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=kwargs,
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert kwargs == before
    assert "dataset_windows" in kwargs  # explicitly: run_and_register's internal
    # pop() must never remove this key from the CALLER's own dict.
    assert "experiment_type" not in kwargs  # the runner's forced default must
    # land only on its internal copy, never write back onto the caller's dict.


# ---------------------------------------------------------------------------
# non-alpha_research experiment_type refused
# ---------------------------------------------------------------------------


def test_non_alpha_research_experiment_type_is_refused(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match="alpha_research"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx, experiment_type="robustness"),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# missing research_root refused
# ---------------------------------------------------------------------------


def test_missing_research_root_is_refused(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(ResearchRunnerError, match="research_root"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=nonexistent,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()

    # Repair cycle 2 (F4.3) -- this specific substring ("is REQUIRED") is
    # only produced by the explicit `research_root is None` branch, never by
    # the fallback `except TypeError` branch (which produces a DIFFERENT
    # message: "research_root must be a str or os.PathLike, got None
    # (NoneType) ..."). A bare `match="research_root"` (as used elsewhere in
    # this test) does NOT distinguish the two, since both messages contain
    # that substring -- deleting the explicit None branch entirely would
    # still pass a bare `match="research_root"` check via the TypeError
    # fallback, which is exactly the mutation-survivor this pins shut.
    with pytest.raises(ResearchRunnerError, match="research_root is REQUIRED"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=None,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# metrics on the record equal the engine's (runner is pass-through)
# ---------------------------------------------------------------------------


def test_registered_metrics_equal_the_engines_own_metrics(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    records = reg.list_experiments()
    assert len(records) == 1
    reference = run_backtest(_tiny_config(), md, so, dataset_provenance=_dataset_provenance(idx))
    assert _metrics_equal(records[0].record.results.metrics, reference.metrics)


# ---------------------------------------------------------------------------
# repair cycle 1 (audit findings) -- result-object identity (W3/S1b)
# ---------------------------------------------------------------------------


def test_success_path_returns_the_identical_result_object(tmp_path, monkeypatch):
    """S1b: mutating the runner to `return copy.deepcopy(run_and_register(...))`
    must go RED here. Spies on the real `run_backtest` (called deep inside
    `run_and_register`) to capture the exact object it builds, then asserts
    `is` identity against what `run_research_experiment` returns -- a value
    comparison (as in the test above) cannot distinguish a copy from the
    original."""
    import registry.backtest_adapter as BA

    captured = {}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        obj = original_run_backtest(*a, **kw)
        captured["obj"] = obj
        return obj

    monkeypatch.setattr(BA, "run_backtest", _spy)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert "obj" in captured
    assert result is captured["obj"]


# ---------------------------------------------------------------------------
# repair cycle 1 (audit findings) -- KeyboardInterrupt/SystemExit are never
# swallowed by run_research_experiment itself (S2)
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_propagates_through_run_research_experiment_and_registers_FAILED(
    tmp_path, monkeypatch
):
    """S2: wrapping the delegated call in
    `except (KeyboardInterrupt, SystemExit): return None` must go RED here.
    Exercises `run_research_experiment` itself (not `record_run` directly) --
    prior coverage never drove a KeyboardInterrupt/SystemExit through this
    function at all."""
    import registry.backtest_adapter as BA

    def _interrupt(*a, **kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr(BA, "run_backtest", _interrupt)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(KeyboardInterrupt):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "FAILED"
    assert records[0].record.status_reason == "ABORTED: KeyboardInterrupt: "


def test_system_exit_propagates_through_run_research_experiment_and_registers_FAILED(
    tmp_path, monkeypatch
):
    import registry.backtest_adapter as BA

    def _exit(*a, **kw):
        raise SystemExit(1)

    monkeypatch.setattr(BA, "run_backtest", _exit)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(SystemExit):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "FAILED"
    assert records[0].record.status_reason == "ABORTED: SystemExit: 1"


# ---------------------------------------------------------------------------
# repair cycle 1 (audit findings) -- explicit experiment_type="alpha_research"
# is ACCEPTED, not refused (S3, the acceptance half of P§4.4)
# ---------------------------------------------------------------------------


def test_explicit_alpha_research_experiment_type_is_accepted(tmp_path):
    """S3: mutating the guard from `!= "alpha_research"` to
    `"experiment_type" in record_kwargs` must go RED here -- no prior test
    ever supplied `experiment_type="alpha_research"` explicitly, so a
    regression that refuses the one mandated value would have gone
    undetected."""
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx, experiment_type="alpha_research"),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"
    assert records[0].record.experiment_type == "alpha_research"


# ---------------------------------------------------------------------------
# repair cycle 1 (audit findings) -- research_root == "" is refused (S4/W1)
# ---------------------------------------------------------------------------


def test_empty_string_research_root_is_refused(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match="research_root"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root="",
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# repair cycle 1 (audit findings) -- non-path-like research_root raises
# ResearchRunnerError, not a bare TypeError (W2)
# ---------------------------------------------------------------------------


def test_non_path_like_research_root_raises_ResearchRunnerError(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match="research_root"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=123,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# repair cycle 2 (F4.2) -- research_root pointing at an existing regular
# FILE (not a directory) is refused. `is_dir()` catches this; `exists()`
# would not.
# ---------------------------------------------------------------------------


def test_research_root_pointing_at_a_regular_file_is_refused(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    a_file = tmp_path / "not_a_directory.txt"
    a_file.write_text("this is a file, not a directory")
    with pytest.raises(ResearchRunnerError, match="research_root"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=a_file,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# repair cycle 2 (F4.4) -- ResearchRunnerError's base class is pinned.
# ---------------------------------------------------------------------------


def test_ResearchRunnerError_is_a_ValueError():
    assert issubclass(ResearchRunnerError, ValueError)


def test_RunNotRegisteredError_is_a_RuntimeError_and_distinct_from_ResearchRunnerError():
    assert issubclass(RunNotRegisteredError, RuntimeError)
    assert not issubclass(RunNotRegisteredError, ResearchRunnerError)
    assert not issubclass(ResearchRunnerError, RunNotRegisteredError)


# ---------------------------------------------------------------------------
# repair cycle 1 (B1) -- missing/empty record_kwargs["datasets"] is refused
# BEFORE run_backtest/run_and_register is ever invoked, and nothing is
# registered. This is the core B1 boundary: previously, omitting `datasets`
# let the run start, and only failed (registering ZERO records, replacing
# the original exception with a registry ValidationError) once a run
# actually failed.
# ---------------------------------------------------------------------------


def test_missing_datasets_refused_before_run_backtest_is_ever_invoked(tmp_path, monkeypatch):
    import registry.backtest_adapter as BA

    called = {"n": 0}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        called["n"] += 1
        return original_run_backtest(*a, **kw)

    monkeypatch.setattr(BA, "run_backtest", _spy)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    del kwargs["datasets"]
    with pytest.raises(ResearchRunnerError, match="datasets"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=kwargs,
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_empty_tuple_datasets_is_refused(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match="datasets"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx, datasets=()),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# repair cycle 2 (F4.1) -- `no_datasets_reason` does NOT license an empty
# `datasets` tuple for `experiment_type == "alpha_research"`. This pins the
# single most dangerous mutation survivor from the independent re-audit:
# `if not record_kwargs.get("datasets"):` mutated to
# `... and not record_kwargs.get("no_datasets_reason")` previously stayed
# 15/15 green because nothing exercised this exact combination.
# ---------------------------------------------------------------------------


def test_no_datasets_reason_does_not_license_empty_datasets_for_alpha_research(tmp_path):
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match="datasets"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(
                idx, datasets=(), no_datasets_reason="infra-only run, not applicable here"
            ),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# repair cycle 2 (F1) -- non-empty but WRONG-SHAPED `datasets` is refused,
# before `run_backtest` is ever invoked, and nothing is registered. Each
# shape here is a natural caller mistake that was `truthy` (hence accepted
# by repair cycle 1's `if not record_kwargs.get("datasets")` guard) but
# not actually registerable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_datasets",
    [
        ("a-bare-string-in-a-tuple",),
        ({"dataset_id": "x"},),
        (None,),
        "bare string, not even a tuple",
        5,
        {"a": 1},
    ],
    ids=[
        "tuple_of_str",
        "tuple_of_dict",
        "tuple_of_none",
        "bare_str",
        "bare_int",
        "bare_dict",
    ],
)
def test_non_empty_but_invalid_datasets_shapes_are_refused_before_run_backtest(
    tmp_path, monkeypatch, bad_datasets
):
    import registry.backtest_adapter as BA

    called = {"n": 0}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        called["n"] += 1
        return original_run_backtest(*a, **kw)

    monkeypatch.setattr(BA, "run_backtest", _spy)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match="datasets"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx, datasets=bad_datasets),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# repair cycle 2 (F2) -- `dataset_windows` is validated up front: missing
# entirely, wrong shape, or missing an entry for a declared dataset_id are
# all refused BEFORE run_backtest is ever invoked, and nothing is
# registered. Prior to this repair, a genuinely SUCCESSFUL backtest could
# run to completion and only THEN fail to register anything, because
# `_build_datasets` (backtest_adapter.py) requires an exact key-set match
# against `result.provenance` and this runner validated nothing about
# `dataset_windows` at all.
# ---------------------------------------------------------------------------


def test_missing_dataset_windows_refused_before_run_backtest_is_ever_invoked(tmp_path, monkeypatch):
    import registry.backtest_adapter as BA

    called = {"n": 0}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        called["n"] += 1
        return original_run_backtest(*a, **kw)

    monkeypatch.setattr(BA, "run_backtest", _spy)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    del kwargs["dataset_windows"]
    with pytest.raises(ResearchRunnerError, match="dataset_windows"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=kwargs,
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_dataset_windows_missing_declared_dataset_id_is_refused(tmp_path, monkeypatch):
    """`dataset_windows` present, non-empty, str-keyed -- but keyed on an id
    that does NOT match the one declared in `record_kwargs['datasets']`.
    This is exactly the "keyed on a wrong id" shape named in F2."""
    import registry.backtest_adapter as BA

    called = {"n": 0}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        called["n"] += 1
        return original_run_backtest(*a, **kw)

    monkeypatch.setattr(BA, "run_backtest", _spy)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    kwargs["dataset_windows"] = {"some.other.dataset.id": kwargs["dataset_windows"][_DATASET_ID]}
    with pytest.raises(ResearchRunnerError, match="dataset_windows"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=kwargs,
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


@pytest.mark.parametrize(
    "bad_windows",
    [
        [],
        {},
        {1: {"data_start": None}},
    ],
    ids=["bare_list", "empty_dict", "non_str_key"],
)
def test_dataset_windows_wrong_shape_is_refused_before_run_backtest(tmp_path, monkeypatch, bad_windows):
    import registry.backtest_adapter as BA

    called = {"n": 0}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        called["n"] += 1
        return original_run_backtest(*a, **kw)

    monkeypatch.setattr(BA, "run_backtest", _spy)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx, dataset_windows=bad_windows)
    with pytest.raises(ResearchRunnerError, match="dataset_windows"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=kwargs,
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# repair cycle 2 (F3) -- the post-delegation safety net. When the delegated
# `run_and_register` call itself does not result in a new registry record
# (an unforeseen failure mode this runner's upfront checks cannot
# anticipate), `run_research_experiment` must raise the distinctly-typed
# `RunNotRegisteredError` rather than silently returning/propagating with
# zero records -- on BOTH the exception path and the success path.
# ---------------------------------------------------------------------------


def test_run_not_registered_error_raised_when_delegate_raises_without_registering_anything(
    tmp_path, monkeypatch
):
    import research.runner as runner_module

    class Boom(RuntimeError):
        pass

    def _boom(*a, **kw):
        raise Boom("unforeseen registry-layer failure, nothing registered")

    monkeypatch.setattr(runner_module, "run_and_register", _boom)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(RunNotRegisteredError) as excinfo:
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    # The original exception MUST be chained, never destroyed or replaced.
    assert isinstance(excinfo.value.__cause__, Boom)
    assert "unforeseen registry-layer failure" in str(excinfo.value.__cause__)
    assert reg.list_experiments() == ()


def test_run_not_registered_error_raised_when_delegate_succeeds_without_registering_anything(
    tmp_path, monkeypatch
):
    import research.runner as runner_module

    sentinel = object()

    def _fake_success(*a, **kw):
        return sentinel

    monkeypatch.setattr(runner_module, "run_and_register", _fake_success)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(RunNotRegisteredError):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert reg.list_experiments() == ()


def test_run_not_registered_error_is_not_raised_on_normal_successful_registration(tmp_path):
    """Sanity check: the safety net must NOT be a false-positive trap on the
    ordinary, fully-successful path -- it only fires when the registry
    genuinely gained nothing."""
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    assert len(reg.list_experiments()) == 1


# ---------------------------------------------------------------------------
# repair cycle 3 (Fix 1) -- hypothesis_id / search_space_id are REQUIRED and
# validated BEFORE run_and_register/run_backtest is ever invoked. Prior to
# this repair both defaulted to None all the way down to
# registry.models.ExperimentRecord.__post_init__, which is the ONLY place
# that unconditionally requires them non-blank for experiment_type ==
# "alpha_research" -- meaning a caller who omitted either one ran the full
# engine, computed real metrics, and still registered ZERO records on BOTH
# the success and the FAILED path.
# ---------------------------------------------------------------------------


def _assert_refused_before_run_backtest(monkeypatch, tmp_path, kwargs, match):
    import registry.backtest_adapter as BA

    called = {"n": 0}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        called["n"] += 1
        return original_run_backtest(*a, **kw)

    monkeypatch.setattr(BA, "run_backtest", _spy)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match=match):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=kwargs,
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_missing_hypothesis_id_refused_before_run_backtest_is_ever_invoked(tmp_path, monkeypatch):
    idx, _, _ = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    del kwargs["hypothesis_id"]
    _assert_refused_before_run_backtest(monkeypatch, tmp_path, kwargs, "hypothesis_id")


def test_missing_search_space_id_refused_before_run_backtest_is_ever_invoked(tmp_path, monkeypatch):
    idx, _, _ = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    del kwargs["search_space_id"]
    _assert_refused_before_run_backtest(monkeypatch, tmp_path, kwargs, "search_space_id")


@pytest.mark.parametrize(
    "bad_value",
    [None, "", "   ", 123, 1.5, ("HYP-001",), {"id": "HYP-001"}],
    ids=["none", "empty_str", "whitespace_str", "int", "float", "tuple", "dict"],
)
def test_invalid_hypothesis_id_values_refused_before_run_backtest(tmp_path, monkeypatch, bad_value):
    idx, _, _ = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx, hypothesis_id=bad_value)
    _assert_refused_before_run_backtest(monkeypatch, tmp_path, kwargs, "hypothesis_id")


@pytest.mark.parametrize(
    "bad_value",
    [None, "", "   ", 123, 1.5, ("SS-001",), {"id": "SS-001"}],
    ids=["none", "empty_str", "whitespace_str", "int", "float", "tuple", "dict"],
)
def test_invalid_search_space_id_values_refused_before_run_backtest(tmp_path, monkeypatch, bad_value):
    idx, _, _ = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx, search_space_id=bad_value)
    _assert_refused_before_run_backtest(monkeypatch, tmp_path, kwargs, "search_space_id")


def test_valid_hypothesis_id_and_search_space_id_are_accepted(tmp_path):
    """Sanity check: a well-formed, non-blank string for both fields is NOT
    refused -- the run proceeds and registers COMPLETED, one record."""
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx, hypothesis_id="HYP-XYZ", search_space_id="SS-XYZ"),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"


# ---------------------------------------------------------------------------
# repair cycle 3 (Fix 2) -- exact set equality between
# {d.dataset_id for d in datasets} and set(dataset_windows), enforced in
# BOTH directions. Before this repair only the "declared dataset missing a
# window" direction was checked; an EXTRA, unmatched dataset_windows key
# passed this runner's checks, ran a full successful backtest, and only
# then failed deep inside backtest_adapter._build_datasets
# (ValidationError: "dataset_windows has dataset_id(s) [...] with no
# matching element in result.provenance"), registering ZERO records.
# ---------------------------------------------------------------------------


def test_dataset_windows_with_extra_unmatched_key_is_refused_before_run_backtest(tmp_path, monkeypatch):
    """The NEW direction this repair adds: dataset_windows carries an EXTRA
    id with no counterpart in datasets. Must be refused up front, not left
    to fail inside _build_datasets after a full successful backtest run."""
    idx, _, _ = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    kwargs["dataset_windows"] = dict(kwargs["dataset_windows"])
    kwargs["dataset_windows"]["not.in.provenance"] = kwargs["dataset_windows"][_DATASET_ID]
    _assert_refused_before_run_backtest(monkeypatch, tmp_path, kwargs, "dataset_windows")


def test_datasets_declaring_id_with_no_window_is_refused_before_run_backtest(tmp_path, monkeypatch):
    """The pre-existing direction, re-pinned here alongside the new one so
    both directions of the exact-set-equality check are exercised together:
    datasets declares a dataset_id with NO corresponding dataset_windows
    entry."""
    idx, _, _ = _tiny_market_data_and_output()
    kwargs = _alpha_record_kwargs(idx)
    kwargs["dataset_windows"] = {}
    _assert_refused_before_run_backtest(monkeypatch, tmp_path, kwargs, "dataset_windows")


def test_dataset_windows_exact_match_with_datasets_is_accepted(tmp_path):
    """Sanity check: when the dataset_windows key set is EXACTLY equal to
    the declared datasets' dataset_id set (the ordinary, correct case),
    nothing is refused -- the run proceeds and registers COMPLETED."""
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"


# ---------------------------------------------------------------------------
# repair cycle 3 -- post-hoc net exception-path message accuracy. A failure
# BEFORE any run actually executed (e.g. a plain TypeError raised by
# run_and_register/record_run for a missing/misnamed kwarg) must not be
# described as "a run executed".
# ---------------------------------------------------------------------------


def test_run_not_registered_message_does_not_claim_a_run_executed_when_delegate_raises_before_running(
    tmp_path, monkeypatch
):
    import research.runner as runner_module

    def _type_error(*a, **kw):
        raise TypeError("missing required keyword-only argument (synthetic, nothing ran)")

    monkeypatch.setattr(runner_module, "run_and_register", _type_error)

    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(RunNotRegisteredError) as excinfo:
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert "a run executed and raised" not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, TypeError)
    assert reg.list_experiments() == ()


# ---------------------------------------------------------------------------
# sealed protected-OOS overlap guard (this work order) --
# `ProtectedWindowOverlapError`, `research_root / "oos" / "protected_windows.json"`.
#
# The run's ACTUAL data window is always derived from `market_data.close.index`
# via `_market_data_and_output_for_range` below -- never from
# `record_kwargs["dataset_windows"]`/`datasets` (which, in the ordinary tests
# here, are built to describe the SAME idx via `_alpha_record_kwargs(idx)`,
# and in the evasion test below are deliberately built to describe a
# DIFFERENT, non-overlapping idx).
# ---------------------------------------------------------------------------


_EVAL_START = "2025-08-01T00:00:00+00:00"
_EVAL_END = "2025-08-05T00:00:00+00:00"


def _write_protected_windows(research_root, entries) -> None:
    oos_dir = research_root / "oos"
    oos_dir.mkdir(parents=True, exist_ok=True)
    (oos_dir / "protected_windows.json").write_text(json.dumps(entries))


def _sealed_entry(
    window_id: str = "OOS-TEST-001",
    evaluation_start: str = _EVAL_START,
    evaluation_end: str = _EVAL_END,
    status: str = "SEALED",
) -> dict:
    return {
        "window_id": window_id,
        "status": status,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
    }


def _market_data_and_output_for_range(start, periods: int, freq: str = "1D"):
    """Builds a `MarketData`/`StrategyOutput` pair whose actual data window
    is exactly `[start, start + (periods-1)*freq]` -- the ONLY thing the
    guard under test may read to determine the run's window."""
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    prices = pd.DataFrame({"BTC": [100.0 + i for i in range(periods)]}, index=idx)
    md = MarketData(open=prices, close=prices)
    weights_col = [float("nan")] * periods
    mask_col = [False] * periods
    if periods >= 2:
        weights_col[1] = 1.0
        mask_col[1] = True
    weights = pd.DataFrame({"BTC": weights_col}, index=idx)
    mask = pd.Series(mask_col, index=idx)
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    return idx, md, so


def _spy_on_run_backtest(monkeypatch):
    import registry.backtest_adapter as BA

    called = {"n": 0}
    original_run_backtest = BA.run_backtest

    def _spy(*a, **kw):
        called["n"] += 1
        return original_run_backtest(*a, **kw)

    monkeypatch.setattr(BA, "run_backtest", _spy)
    return called


def test_run_overlapping_sealed_evaluation_interval_is_refused_and_engine_not_invoked(
    tmp_path, monkeypatch
):
    _write_protected_windows(tmp_path, [_sealed_entry()])
    called = _spy_on_run_backtest(monkeypatch)
    reg = _registry(tmp_path)
    # Fully inside [2025-08-01, 2025-08-05].
    idx, md, so = _market_data_and_output_for_range("2025-08-02", periods=2)
    with pytest.raises(ProtectedWindowOverlapError, match="OOS-TEST-001"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_run_entirely_before_evaluation_start_is_allowed(tmp_path):
    _write_protected_windows(tmp_path, [_sealed_entry()])
    reg = _registry(tmp_path)
    # 2025-07-01 .. 2025-07-02, strictly before evaluation_start 2025-08-01.
    idx, md, so = _market_data_and_output_for_range("2025-07-01", periods=2)
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"


def test_run_entirely_after_evaluation_end_is_allowed(tmp_path):
    _write_protected_windows(tmp_path, [_sealed_entry()])
    reg = _registry(tmp_path)
    # 2025-08-10 .. 2025-08-11, strictly after evaluation_end 2025-08-05.
    idx, md, so = _market_data_and_output_for_range("2025-08-10", periods=2)
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"


def test_run_touching_evaluation_start_exactly_is_refused(tmp_path, monkeypatch):
    _write_protected_windows(tmp_path, [_sealed_entry()])
    called = _spy_on_run_backtest(monkeypatch)
    reg = _registry(tmp_path)
    # Ends exactly AT evaluation_start (2025-07-30, 2025-07-31, 2025-08-01T00:00:00Z).
    idx, md, so = _market_data_and_output_for_range(
        pd.Timestamp(_EVAL_START) - pd.Timedelta(days=2), periods=3
    )
    assert idx.max() == pd.Timestamp(_EVAL_START)
    with pytest.raises(ProtectedWindowOverlapError, match="OOS-TEST-001"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_run_touching_evaluation_end_exactly_is_refused(tmp_path, monkeypatch):
    _write_protected_windows(tmp_path, [_sealed_entry()])
    called = _spy_on_run_backtest(monkeypatch)
    reg = _registry(tmp_path)
    # Starts exactly AT evaluation_end (2025-08-05T00:00:00Z, 08-06, 08-07).
    idx, md, so = _market_data_and_output_for_range(pd.Timestamp(_EVAL_END), periods=3)
    assert idx.min() == pd.Timestamp(_EVAL_END)
    with pytest.raises(ProtectedWindowOverlapError, match="OOS-TEST-001"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_non_sealed_status_entry_is_not_guarded(tmp_path):
    _write_protected_windows(tmp_path, [_sealed_entry(status="PENDING")])
    reg = _registry(tmp_path)
    # Fully inside the entry's interval, but its status is not SEALED.
    idx, md, so = _market_data_and_output_for_range("2025-08-02", periods=2)
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    records = reg.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"


def test_missing_protected_windows_file_is_allowed(tmp_path):
    # No oos/ directory at all -- tmp_path is otherwise empty.
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    result = run_research_experiment(
        registry=reg,
        config=_tiny_config(),
        market_data=md,
        strategy_output=so,
        record_kwargs=_alpha_record_kwargs(idx),
        research_root=tmp_path,
        dataset_provenance=_dataset_provenance(idx),
    )
    assert result is not None
    assert len(reg.list_experiments()) == 1


def test_malformed_json_protected_windows_file_is_refused(tmp_path, monkeypatch):
    oos_dir = tmp_path / "oos"
    oos_dir.mkdir(parents=True)
    (oos_dir / "protected_windows.json").write_text("{ this is not valid JSON ]")
    called = _spy_on_run_backtest(monkeypatch)
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_entry_missing_evaluation_end_is_refused(tmp_path, monkeypatch):
    """A malformed entry MUST fail closed as a MALFORMED-FILE refusal, and MUST
    NOT be silently defaulted into some huge interval that then refuses via the
    OVERLAP path instead. `ProtectedWindowOverlapError` subclasses
    `ResearchRunnerError`, so asserting the base class alone does NOT
    discriminate: a mutation defaulting `evaluation_end` to "2999-01-01" was
    measured SURVIVING that weaker assertion (it raised the overlap error, and
    the test passed for the wrong reason). Hence the explicit
    not-an-overlap-error assertion plus the field name in the message.
    """
    entry = _sealed_entry()
    del entry["evaluation_end"]
    _write_protected_windows(tmp_path, [entry])
    called = _spy_on_run_backtest(monkeypatch)
    reg = _registry(tmp_path)
    idx, md, so = _tiny_market_data_and_output()
    with pytest.raises(ResearchRunnerError, match="evaluation_end") as excinfo:
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=md,
            strategy_output=so,
            record_kwargs=_alpha_record_kwargs(idx),
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(idx),
        )
    assert not isinstance(excinfo.value, ProtectedWindowOverlapError)
    assert called["n"] == 0
    assert reg.list_experiments() == ()


def test_declared_clean_window_does_not_evade_the_guard_when_actual_data_overlaps(
    tmp_path, monkeypatch
):
    """The most important case: `record_kwargs["datasets"]`/`dataset_windows`
    declare a CLEAN, non-overlapping window (2025-07-01..2025-07-02), while
    `market_data` -- what the engine would actually run on -- spans the
    sealed evaluation interval. The guard MUST derive the run's window from
    `market_data.close.index`, never from the caller's declared window, so
    this must still be refused."""
    _write_protected_windows(tmp_path, [_sealed_entry()])
    called = _spy_on_run_backtest(monkeypatch)
    reg = _registry(tmp_path)

    declared_idx, _, _ = _market_data_and_output_for_range("2025-07-01", periods=2)
    _, actual_md, actual_so = _market_data_and_output_for_range("2025-08-02", periods=2)

    kwargs = _alpha_record_kwargs(declared_idx)
    with pytest.raises(ProtectedWindowOverlapError, match="OOS-TEST-001"):
        run_research_experiment(
            registry=reg,
            config=_tiny_config(),
            market_data=actual_md,
            strategy_output=actual_so,
            record_kwargs=kwargs,
            research_root=tmp_path,
            dataset_provenance=_dataset_provenance(declared_idx),
        )
    assert called["n"] == 0
    assert reg.list_experiments() == ()
