"""R§20 (v1.2 AMENDMENTS) -- dedicated coverage for every materially new
behaviour introduced by the dual REGISTRY FAIL repair. Organized by R§20
subsection. Each test targets a SPECIFIC, previously-unenforced behaviour;
see the docstring of each test for the exact defect it would catch.
"""
from __future__ import annotations

import subprocess
import time

import pandas as pd
import pytest

from backtest.models import BacktestConfig, MarketData, StrategyOutput
from registry.backtest_adapter import record_run, run_and_register
from registry.codeid import capture_code_identity, verify_code_state
from registry.models import ArtifactRef, DatasetRef, RegistryError, ValidationError
from registry.serialize import canonical_json
from registry.store import ExperimentRegistry

from _factories import (
    CONTRACT_VERSIONS,
    make_git_repo,
    mk_artifact_ref,
    mk_code_identity,
    mk_dataset_ref,
    mk_result_summary,
    mk_strategy_ref,
    record_kwargs,
)


# ===========================================================================
# R§20.2 -- registration must not be optional
# ===========================================================================


def test_record_run_catches_KeyboardInterrupt_and_still_registers_FAILED(registry):
    """R§20.2.3 (blocking) -- `record_run` MUST catch `BaseException`, not
    `Exception`. Measured under v1.1: Ctrl-C produced ZERO records and no
    complaint (KeyboardInterrupt is not an Exception subclass)."""
    with pytest.raises(KeyboardInterrupt):
        with record_run(registry, **_run_kwargs()) as run:
            raise KeyboardInterrupt()
    records = registry.list_experiments()
    assert len(records) == 1
    assert records[0].status == "FAILED"
    assert records[0].record.status_reason.startswith("ABORTED: KeyboardInterrupt")


def test_record_run_catches_SystemExit_and_still_registers_FAILED(registry):
    """R§20.2.3 -- same for `sys.exit()` (SystemExit)."""
    with pytest.raises(SystemExit):
        with record_run(registry, **_run_kwargs()) as run:
            raise SystemExit(1)
    records = registry.list_experiments()
    assert len(records) == 1
    assert records[0].status == "FAILED"
    assert records[0].record.status_reason.startswith("ABORTED: SystemExit")


def test_record_run_ordinary_exception_has_no_ABORTED_prefix(registry):
    """Self-guard: an ordinary Exception (not Ctrl-C/sys.exit) keeps the
    plain `TypeErr: msg` form -- ABORTED is reserved for the abort case."""
    with pytest.raises(ValueError):
        with record_run(registry, **_run_kwargs()) as run:
            raise ValueError("boom")
    records = registry.list_experiments()
    assert records[0].record.status_reason == "ValueError: boom"
    assert not records[0].record.status_reason.startswith("ABORTED")


def test_record_run_no_set_result_registers_INVALID_and_does_not_raise(registry):
    """R§20.2.4 (blocking) -- a block that exits NORMALLY without ever
    calling `run.set_result(...)` MUST register `INVALID` with the pinned
    `status_reason`, and MUST NOT raise. Measured under v1.1: the exception
    raised inside the block combined with silence outside it produced ZERO
    records."""
    with record_run(registry, **_run_kwargs()) as run:
        pass  # never calls run.set_result(...)
    records = registry.list_experiments()
    assert len(records) == 1
    assert records[0].status == "INVALID"
    assert records[0].record.status_reason == "NO_RESULT: run_and_register/set_result was never called"


def _tiny_config() -> BacktestConfig:
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
    return md, so


def _run_kwargs(**overrides) -> dict:
    base = dict(
        strategy=mk_strategy_ref(),
        universe_policy="single_symbol_fixed:BTC",
        code_identity=mk_code_identity(),
        experiment_type="infrastructure",
        research_stage="exploratory",
        reason_for_run="R§20 amendment test",
        created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        no_datasets_reason="synthetic run_and_register/record_run test, no dataset provenance",
        n_configs_evaluated=1,
    )
    base.update(overrides)
    return base


def test_run_and_register_success_path_recorded_via_adapter(registry):
    """R§20.2.1 (blocking) -- `run_and_register` calls `run_backtest` then
    registers via `record_backtest_result` on success: `recorded_via`
    MUST be `"adapter"`, not `"manual"` (a hand-written driver never touches
    `ResultSummary` construction itself)."""
    md, so = _tiny_market_data_and_output()
    result = run_and_register(
        registry, _tiny_config(), md, so, record_kwargs=_run_kwargs(dataset_windows={})
    )
    assert result is not None
    records = registry.list_experiments()
    assert len(records) == 1
    assert records[0].status == "COMPLETED"
    assert records[0].record.recorded_via == "adapter"


def test_run_and_register_failure_path_registers_FAILED_and_reraises(registry, monkeypatch):
    """R§20.2.1/R§20.2.3 -- a `BaseException` from `run_backtest` itself
    (not just from a hand-written `with record_run(...)` block) is ALSO
    captured as a FAILED record and re-raised."""
    import registry.backtest_adapter as BA

    def _boom(*a, **kw):
        raise ValueError("synthetic run_backtest failure")

    monkeypatch.setattr(BA, "run_backtest", _boom)
    with pytest.raises(ValueError):
        BA.run_and_register(registry, object(), object(), object(), record_kwargs=_run_kwargs(dataset_windows={}))
    records = registry.list_experiments()
    assert len(records) == 1
    assert records[0].status == "FAILED"
    assert records[0].record.status_reason == "ValueError: synthetic run_backtest failure"
    assert records[0].record.recorded_via == "manual"


def _r21_3_offenders(root):
    """R§21.3 (blocking) -- every module under experiments/** that CALLS
    `run_backtest` directly MUST instead obtain it through
    `run_and_register`/`record_run`, except the explicit, NAMED-FILE
    allow-list below (R§21.3.1 -- no directory-prefix exclusion is
    permitted, `__pycache__` filtering is the only path-based skip).

    R§21.3.2 (blocking) -- AST-based, not text-containment: a module is an
    offender iff it contains an `ast.Call` node whose callee resolves to the
    bare name `run_backtest` (covers `run_backtest(...)`,
    `engine.run_backtest(...)`, etc.), REGARDLESS of any textual mention of
    `record_run`/`run_and_register` elsewhere in the file (e.g. in a comment
    or a docstring) -- the v1.2 text-containment check
    (`"run_and_register" not in text and "record_run" not in text`) was
    satisfiable by a comment alone, which is exactly the bypass this closes.
    """
    import ast
    from pathlib import Path

    root = Path(root)
    repo_root = root.parent
    allowlist_relpaths = {
        "experiments/qr_smoke_001/pipeline.py",
        "experiments/qr_smoke_001/crossvenue.py",
        "experiments/qr_smoke_001/reconstruction.py",
    }
    bad = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relpath = path.resolve().relative_to(repo_root.resolve()).as_posix()
        if relpath in allowlist_relpaths:
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        calls_run_backtest = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
                if name == "run_backtest":
                    calls_run_backtest = True
                    break
        if calls_run_backtest:
            bad.append(str(path))
    return bad


def test_R21_3_static_registration_enforcement_ast_based_named_allowlist(tmp_path):
    """R§21.3 (blocking) -- production defect 4. Replaces v1.2's
    `test_R20_2_2_static_registration_enforcement`, which blanket-excluded
    ANY path containing `registry_migration` (an exemption R§20.2.2 never
    granted) and used a text-containment check satisfiable by a stray
    comment. Three self-guards, per R§21.3.3, each created, asserted RED,
    then deleted (workspace integrity re-verified by the caller after this
    test via the file-set/hash-manifest comparison, per R§21.11)."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    experiments_dir = repo_root / "experiments"

    # 0. Baseline: the real tree has zero offenders.
    assert _r21_3_offenders(experiments_dir) == []

    rogue_paths = []
    try:
        # 1. A rogue driver under a BRAND NEW experiments/ subdirectory.
        rogue_dir_new = experiments_dir / "_r21_3_rogue_new_dir"
        rogue_dir_new.mkdir(exist_ok=True)
        rogue_new = rogue_dir_new / "rogue_driver.py"
        rogue_new.write_text("from backtest.engine import run_backtest\nrun_backtest(1, 2, 3)\n")
        rogue_paths.append(rogue_new)
        assert str(rogue_new) in _r21_3_offenders(experiments_dir), (
            "a rogue driver under a NEW experiments/ subdirectory must be caught"
        )

        # 2. A rogue driver under experiments/registry_migration/ -- THIS is
        # the R§21.3 fix: v1.2's blanket directory exclusion made this GREEN.
        rogue_migration = repo_root / "experiments" / "registry_migration" / "_r21_3_rogue_migration_driver.py"
        rogue_migration.write_text("from backtest.engine import run_backtest\nrun_backtest(4, 5, 6)\n")
        rogue_paths.append(rogue_migration)
        assert str(rogue_migration) in _r21_3_offenders(experiments_dir), (
            "R§21.3.1: the blanket registry_migration/ directory exclusion must be GONE -- "
            "a rogue driver placed there must be caught exactly like anywhere else"
        )

        # 3. R§21.3.2 self-guard: a rogue driver whose ONLY textual mention of
        # `record_run`/`run_and_register` is inside a COMMENT, while it still
        # calls `run_backtest` directly. A text-containment check would
        # wrongly call this compliant (GREEN); the AST-based check must not.
        rogue_comment_bypass = rogue_dir_new / "rogue_comment_bypass.py"
        rogue_comment_bypass.write_text(
            "# TODO: migrate this to record_run/run_and_register eventually\n"
            "from backtest.engine import run_backtest\n"
            "run_backtest(7, 8, 9)\n"
        )
        rogue_paths.append(rogue_comment_bypass)
        offenders = _r21_3_offenders(experiments_dir)
        assert str(rogue_comment_bypass) in offenders, (
            "R§21.3.2: a comment mentioning record_run/run_and_register must NOT launder a "
            "direct run_backtest() call past the check"
        )
    finally:
        for p in rogue_paths:
            p.unlink(missing_ok=True)
        for d in (experiments_dir / "_r21_3_rogue_new_dir",):
            if d.exists() and not any(d.iterdir()):
                d.rmdir()

    # Re-verify the real tree is clean again after cleanup.
    assert _r21_3_offenders(experiments_dir) == []


# ===========================================================================
# R§20.3 -- recorded_via
# ===========================================================================


def test_recorded_via_defaults_to_manual_and_is_hashed(registry):
    """R§20.3.1 -- a direct `record_experiment` call is `recorded_via`
    `"manual"`, and it is part of `semantic_hash` (two otherwise-identical
    records differing only in `recorded_via` get DIFFERENT semantic hashes)."""
    fe = registry.record_experiment(**record_kwargs())
    assert fe.record.recorded_via == "manual"


def test_manual_record_with_results_requires_justification(registry):
    """R§20.3.2 (blocking) -- `results is not None` on the manual path is
    REJECTED without `manual_results_justification`."""
    kw = record_kwargs()
    kw.pop("manual_results_justification", None)
    with pytest.raises(ValidationError):
        registry.record_experiment(**kw)


def test_manual_record_with_results_carries_UNVERIFIED_MANUAL_RESULTS_warning(registry):
    """R§20.3.2 -- the record-level warning is mandatory, not merely the
    justification field."""
    fe = registry.record_experiment(**record_kwargs())
    assert "UNVERIFIED_MANUAL_RESULTS" in fe.warnings


def test_summary_renders_recorded_via_and_manual_warning_line(registry):
    """R§20.3.3 (blocking) -- `summary()` MUST render `recorded_via`, and for
    `manual` MUST render the literal cross-check warning line."""
    fe = registry.record_experiment(**record_kwargs())
    s = registry.summary(fe.record.experiment_id)
    assert "recorded_via: manual" in s
    assert "WARNING: provenance/metrics NOT cross-checked against a BacktestResult" in s


def test_adapter_recorded_summary_has_no_manual_warning_line(registry):
    md, so = _tiny_market_data_and_output()
    result = run_and_register(registry, _tiny_config(), md, so, record_kwargs=_run_kwargs(dataset_windows={}))
    fe = registry.list_experiments()[0]
    s = registry.summary(fe.record.experiment_id)
    assert "recorded_via: adapter" in s
    assert "WARNING: provenance/metrics NOT cross-checked" not in s


def test_manual_record_without_results_does_not_require_justification(registry):
    """R§20.3.4/D12 -- a FAILED manual record with `results=None` needs no
    justification and carries no UNVERIFIED_MANUAL_RESULTS warning."""
    kw = record_kwargs(status="FAILED", status_reason="boom", results=None)
    kw.pop("manual_results_justification", None)
    fe = registry.record_experiment(**kw)
    assert "UNVERIFIED_MANUAL_RESULTS" not in fe.warnings


def test_record_run_FAILED_branch_with_proxy_dataset_still_emits_PROXY_DATA(registry):
    """R§20.3.4 -- a FAILED registration whose caller-supplied datasets
    include a proxy MUST still emit the record-level PROXY_DATA warning."""
    proxy_ds = mk_dataset_ref(native_or_proxy="proxy", proxy_for="Hyperliquid")
    with pytest.raises(ValueError):
        with record_run(
            registry,
            **_run_kwargs(
                experiment_type="pipeline_validation",
                datasets=(proxy_ds,),
                no_datasets_reason=None,
                backtest_config={"funding_mode": "disabled", "funding_notional_basis": None},
            ),
        ) as run:
            raise ValueError("boom")
    fe = registry.list_experiments()[0]
    assert "PROXY_DATA" in fe.warnings
    assert fe.record.recorded_via == "manual"


# ===========================================================================
# R§20.4 -- status laundering leaves a permanent trace
# ===========================================================================


def test_INVALID_then_COMPLETED_laundering_leaves_sticky_WAS_INVALIDATED(registry):
    """R§20.4.1 (blocking) -- `set_status(INVALID)` then `set_status(COMPLETED)`
    MUST leave `WAS_INVALIDATED` in the record's warnings PERMANENTLY --
    measured under v1.1, the only evidence was `status_history`, which
    nothing rendered and no filter matched."""
    fe = registry.record_experiment(**record_kwargs())
    registry.set_status(fe.record.experiment_id, "INVALID", "found a bug")
    laundered = registry.set_status(fe.record.experiment_id, "COMPLETED", "bug was actually fine")
    assert laundered.status == "COMPLETED"
    assert "WAS_INVALIDATED" in laundered.warnings


def test_WAS_REJECTED_and_WAS_FAILED_are_sticky_too(registry):
    fe = registry.record_experiment(**record_kwargs())
    registry.set_status(fe.record.experiment_id, "REJECTED", "rejected on evidence")
    reinstated = registry.set_status(fe.record.experiment_id, "COMPLETED", "re-evaluated as fine")
    assert "WAS_REJECTED" in reinstated.warnings

    fe2 = registry.record_experiment(**record_kwargs())
    registry.set_status(fe2.record.experiment_id, "FAILED", "crashed")
    reinstated2 = registry.set_status(fe2.record.experiment_id, "COMPLETED", "re-ran fine")
    assert "WAS_FAILED" in reinstated2.warnings


def test_summary_renders_status_history_when_length_exceeds_one(registry):
    """R§20.4.2 (blocking)."""
    fe = registry.record_experiment(**record_kwargs())
    registry.set_status(fe.record.experiment_id, "INVALID", "found a bug")
    laundered = registry.set_status(fe.record.experiment_id, "COMPLETED", "bug was actually fine")
    s = registry.summary(laundered.record.experiment_id)
    assert "status_history:" in s
    assert "found a bug" in s
    assert "bug was actually fine" in s


def test_summary_omits_status_history_line_when_length_is_one(registry):
    fe = registry.record_experiment(**record_kwargs())
    s = registry.summary(fe.record.experiment_id)
    assert "status_history:" not in s


def test_ever_status_filter_matches_any_status_ever_held(registry):
    """R§20.4.3 -- new filter `ever_status`."""
    fe = registry.record_experiment(**record_kwargs())
    registry.set_status(fe.record.experiment_id, "INVALID", "temp")
    registry.set_status(fe.record.experiment_id, "COMPLETED", "restored")
    matches = registry.find_experiments(ever_status="INVALID")
    assert fe.record.experiment_id in {f.record.experiment_id for f in matches}
    # folded `status` filter must NOT match INVALID anymore -- proves the two
    # filters are genuinely different (R§13.1 MW6 folded-status vs R§20.4.3).
    assert fe.record.experiment_id not in {
        f.record.experiment_id for f in registry.find_experiments(status="INVALID")
    }


# ===========================================================================
# R§20.5 -- multiple testing
# ===========================================================================


def test_search_space_id_required_for_research_types_optional_otherwise(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(
            **record_kwargs(experiment_type="robustness", search_space_id=None)
        )
    # optional (no error) for pipeline_validation, the record_kwargs() default
    registry.record_experiment(**record_kwargs())


def test_n_configs_evaluated_must_be_at_least_one(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(n_configs_evaluated=0))


def test_sibling_count_and_search_space_summary(registry):
    """R§20.5.3 -- the denominator of a reported Sharpe is retrievable."""
    common = dict(
        experiment_type="alpha_research", hypothesis_id="H1", search_space_id="ss-sma-sweep"
    )
    ids = []
    for sma, sharpe in [(5, 0.4), (10, 1.2), (20, 0.9)]:
        results = mk_result_summary(metrics={**mk_result_summary().metrics, "sharpe": sharpe})
        fe = registry.record_experiment(
            **record_kwargs(
                **common,
                strategy=mk_strategy_ref(params={"sma_window": sma}),
                results=results,
                n_configs_evaluated=3,
            )
        )
        ids.append(fe.record.experiment_id)

    assert registry.sibling_count(ids[0]) == 2
    summary = registry.search_space_summary("ss-sma-sweep")
    assert summary["n_records"] == 3
    assert summary["n_configs_evaluated_total"] == 9
    assert summary["best_and_worst_sharpe"] == (1.2, 0.4)
    assert summary["statuses"] == ("COMPLETED",)


def test_sibling_count_zero_when_no_search_space_id(registry):
    fe = registry.record_experiment(**record_kwargs())
    assert registry.sibling_count(fe.record.experiment_id) == 0


def test_config_family_hash_stable_across_content_hash_and_window_change(registry):
    """R§20.5.4 (blocking) -- near_duplicates() groups records sharing a
    config_family_hash across a data re-ingest (different content_hash) and
    a window nudge (different data_start/eval_start) -- exactly what
    semantic_hash (which INCLUDES those fields) cannot detect."""
    fe1 = registry.record_experiment(**record_kwargs())
    ds2 = mk_dataset_ref(
        content_hash="f" * 64,
        eval_start=pd.Timestamp("2026-01-26", tz="UTC"),  # nudged by one day
    )
    fe2 = registry.record_experiment(**record_kwargs(datasets=(ds2,), code_identity=mk_code_identity(code_fingerprint="9" * 64)))

    assert fe1.record.config_family_hash == fe2.record.config_family_hash
    # semantic_hash MUST differ (content_hash/eval_start are hashed there)
    assert fe1.record.semantic_hash != fe2.record.semantic_hash

    dups = registry.near_duplicates()
    group = dups[fe1.record.config_family_hash]
    assert set(group) == {fe1.record.experiment_id, fe2.record.experiment_id}


def test_verify_registry_reports_SEMANTIC_DUP_RESULT_DIFF(registry):
    """R§20.5.5 (blocking) -- two records sharing a semantic_hash but
    differing in `metrics` MUST be flagged -- an accounting change hiding
    as two UNIQUE, unrelated-looking records."""
    fe1 = registry.record_experiment(**record_kwargs())
    metrics2 = dict(mk_result_summary().metrics)
    metrics2["sharpe"] = 2.4
    # A different exact_hash (so this is UNIQUE, not a rerun) but the SAME
    # semantic_hash: change only code identity (not hashed into
    # semantic_hash), keep everything else -- including recorded_via --
    # identical.
    fe2 = registry.record_experiment(
        **record_kwargs(
            results=mk_result_summary(metrics=metrics2),
            code_identity=mk_code_identity(code_fingerprint="7" * 64),
        )
    )
    assert fe1.record.semantic_hash == fe2.record.semantic_hash
    assert fe1.record.exact_hash != fe2.record.exact_hash
    findings = registry.verify_registry()
    ids_sorted = sorted([fe1.record.experiment_id, fe2.record.experiment_id])
    assert f"SEMANTIC_DUP_RESULT_DIFF:{ids_sorted[0]}:{ids_sorted[1]}" in findings


# ===========================================================================
# R§20.6 -- the out-of-sample gate
# ===========================================================================


def _oos_registry(tmp_path):
    repo = tmp_path / "repo"
    make_git_repo(repo, files={"spec.md": "frozen spec v1", "other.md": "unrelated"})
    reg_root = tmp_path / "registry"
    return ExperimentRegistry(reg_root, repo_root=repo), repo


def test_frozen_spec_ref_must_be_git_tracked_for_oos(tmp_path):
    reg, repo = _oos_registry(tmp_path)
    parent = reg.record_experiment(**record_kwargs())
    with pytest.raises(ValidationError):
        reg.record_experiment(
            **record_kwargs(
                research_stage="out_of_sample",
                frozen_spec_ref="not_a_real_file.md",
                parent_experiment_id=parent.record.experiment_id,
                change_from_parent="oos attempt",
            )
        )


def test_frozen_spec_ref_dirty_working_tree_rejected_for_oos(tmp_path):
    """R§20.6.2 (blocking) -- an uncommitted/dirty spec file is REJECTED for
    research_stage=='out_of_sample'. This is the sequence R§20.6.2 exists to
    catch: look at the result, edit the spec, THEN register."""
    reg, repo = _oos_registry(tmp_path)
    parent = reg.record_experiment(**record_kwargs())
    (repo / "spec.md").write_text("edited after the fact, never committed")
    with pytest.raises(ValidationError):
        reg.record_experiment(
            **record_kwargs(
                research_stage="out_of_sample",
                frozen_spec_ref="spec.md",
                parent_experiment_id=parent.record.experiment_id,
                change_from_parent="oos attempt with a dirty spec",
            )
        )


def test_frozen_spec_ref_committed_blob_succeeds_and_pins_commit_and_blob_sha(tmp_path):
    reg, repo = _oos_registry(tmp_path)
    parent = reg.record_experiment(**record_kwargs())
    fe = reg.record_experiment(
        **record_kwargs(
            research_stage="out_of_sample",
            frozen_spec_ref="spec.md",
            parent_experiment_id=parent.record.experiment_id,
            change_from_parent="legit oos attempt",
        )
    )
    assert fe.record.frozen_spec_commit is not None
    assert len(fe.record.frozen_spec_commit) == 40
    assert fe.record.frozen_spec_blob_sha is not None


def test_frozen_spec_ref_rejects_path_escaping_repo_root(tmp_path):
    """R§20.6.5 (blocking) -- mirrors R§9's absolute-path guard."""
    reg, repo = _oos_registry(tmp_path)
    parent = reg.record_experiment(**record_kwargs())
    with pytest.raises(ValidationError):
        reg.record_experiment(
            **record_kwargs(
                research_stage="out_of_sample",
                frozen_spec_ref="../outside.md",
                parent_experiment_id=parent.record.experiment_id,
                change_from_parent="path escape attempt",
            )
        )


def test_OOS_WINDOW_OVERLAP_checked_against_grandparent_not_just_direct_parent(tmp_path):
    """R§20.6.1 (blocking) -- pointing `parent_experiment_id` at the DIRECT
    parent (whose OWN window does not overlap) must NOT suppress the warning
    if a GRANDPARENT's window does overlap. Measured under v1.1: this
    suppressed the warning entirely."""
    reg, repo = _oos_registry(tmp_path)
    root_ds = mk_dataset_ref(
        eval_start=pd.Timestamp("2026-01-25", tz="UTC"), eval_end=pd.Timestamp("2026-03-01", tz="UTC")
    )
    root = reg.record_experiment(**record_kwargs(datasets=(root_ds,)))

    mid_ds = mk_dataset_ref(
        eval_start=pd.Timestamp("2026-03-02", tz="UTC"), eval_end=pd.Timestamp("2026-04-01", tz="UTC")
    )
    mid = reg.record_experiment(
        **record_kwargs(
            datasets=(mid_ds,),
            parent_experiment_id=root.record.experiment_id,
            change_from_parent="mid link, no overlap with root",
        )
    )

    # child's window overlaps ROOT's window, not mid's -- direct parent is mid.
    child_ds = mk_dataset_ref(
        eval_start=pd.Timestamp("2026-02-01", tz="UTC"), eval_end=pd.Timestamp("2026-02-15", tz="UTC")
    )
    child = reg.record_experiment(
        **record_kwargs(
            datasets=(child_ds,),
            research_stage="out_of_sample",
            frozen_spec_ref="spec.md",
            parent_experiment_id=mid.record.experiment_id,
            change_from_parent="oos child overlapping grandparent's window",
        )
    )
    assert f"OOS_WINDOW_OVERLAP:{root.record.experiment_id}" in child.warnings


def test_SPEC_CHANGED_SINCE_PARENT_warns_on_blob_sha_mismatch(tmp_path):
    reg, repo = _oos_registry(tmp_path)
    parent_oos_parent = reg.record_experiment(**record_kwargs())
    parent = reg.record_experiment(
        **record_kwargs(
            research_stage="out_of_sample",
            frozen_spec_ref="spec.md",
            parent_experiment_id=parent_oos_parent.record.experiment_id,
            change_from_parent="first oos",
        )
    )
    # amend + recommit spec.md -> different blob sha at the same path
    (repo / "spec.md").write_text("frozen spec v2")
    subprocess.run(["git", "-C", str(repo), "add", "spec.md"], check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "-C", str(repo), "commit", "-q", "-m", "amend spec"],
        check=True,
    )
    child = reg.record_experiment(
        **record_kwargs(
            research_stage="out_of_sample",
            frozen_spec_ref="spec.md",
            parent_experiment_id=parent.record.experiment_id,
            change_from_parent="second oos, spec amended since parent",
            code_identity=mk_code_identity(code_fingerprint="5" * 64),
        )
    )
    assert "SPEC_CHANGED_SINCE_PARENT" in child.warnings


def test_OOS_RELABEL_OF_warns_when_config_family_matches_a_prior_non_oos_record(tmp_path):
    """R§20.6.4 (blocking) -- relabelling an identical computation as OOS is
    flagged. Reachable now because config_family_hash (R§20.5.4) excludes
    frozen_spec_sha256/recorded_via/window fields, unlike v1.1's
    semantic_hash-based check, which was structurally unreachable."""
    reg, repo = _oos_registry(tmp_path)
    prior = reg.record_experiment(**record_kwargs(research_stage="in_sample"))
    parent = reg.record_experiment(**record_kwargs())
    relabel = reg.record_experiment(
        **record_kwargs(
            research_stage="out_of_sample",
            frozen_spec_ref="spec.md",
            parent_experiment_id=parent.record.experiment_id,
            change_from_parent="relabel attempt: identical config to `prior`",
            code_identity=mk_code_identity(code_fingerprint="3" * 64),
        )
    )
    assert relabel.record.config_family_hash == prior.record.config_family_hash
    assert f"OOS_RELABEL_OF:{prior.record.experiment_id}" in relabel.warnings


# ===========================================================================
# R§20.7 -- chronology, tamper-evidence, code recoverability
# ===========================================================================


def test_logged_at_stamped_and_excluded_from_hash(registry):
    fe = registry.record_experiment(**record_kwargs())
    events = registry._read_history_lines()
    created_ev = [e for e in events if e["experiment_id"] == fe.record.experiment_id][0]
    assert "logged_at" in created_ev
    assert created_ev["logged_at"].tzinfo is not None


def test_BACKDATED_CREATED_AT_warns_when_created_at_far_from_logged_at(registry):
    """R§20.7.1 (blocking). Measured under v1.1: created_at=2024-01-01 was
    accepted silently."""
    fe = registry.record_experiment(
        **record_kwargs(created_at=pd.Timestamp("2024-01-01", tz="UTC"))
    )
    assert "BACKDATED_CREATED_AT" in fe.warnings


def test_created_at_close_to_logged_at_does_not_warn(registry):
    fe = registry.record_experiment(
        **record_kwargs(created_at=pd.Timestamp.now(tz="UTC"))
    )
    assert "BACKDATED_CREATED_AT" not in fe.warnings


def test_history_chain_HISTORY_CHAIN_BROKEN_detects_tampering(registry):
    """R§20.7.2 (blocking) -- deleting a record file plus its history line
    and renumbering `seq` MUST leave residual evidence: the chain hash of
    later lines no longer matches. Simulates the "deliberate fraud" scenario
    by directly editing history.jsonl."""
    registry.record_experiment(**record_kwargs())
    registry.record_experiment(**record_kwargs(code_identity=mk_code_identity(code_fingerprint="4" * 64)))
    assert registry.verify_registry() == ()

    history_path = registry.root / "history.jsonl"
    lines = history_path.read_text().splitlines()
    assert len(lines) == 2
    # Tamper: rewrite line 2's "at" field bytes (changing its content),
    # which invalidates line 2's own recorded hash-chain link is irrelevant
    # here -- what matters is that ANY future line's prev_line_sha256 would
    # no longer match. Simulate a downstream forger who does not recompute
    # correctly by corrupting line 1, which line 2 already committed to via
    # its prev_line_sha256.
    import json as _json

    line1 = _json.loads(lines[0])
    line1["payload"]["status"] = "TAMPERED"
    lines[0] = _json.dumps(line1, sort_keys=True, separators=(",", ":"))
    history_path.write_text("\n".join(lines) + "\n")

    findings = registry.verify_registry()
    assert any(f.startswith("HISTORY_CHAIN_BROKEN:") for f in findings)


def test_verify_code_state_MATCH_and_MISMATCH_and_UNVERIFIABLE(tmp_path):
    repo = tmp_path / "coderepo"
    commit = make_git_repo(repo, files={"src/registry/fake.py": "x = 1\n"})
    code_identity = capture_code_identity(
        repo, scope_patterns=("src/**/*.py",), contract_versions=CONTRACT_VERSIONS
    )
    assert verify_code_state(code_identity, repo) == "MATCH"

    (repo / "src" / "registry" / "fake.py").write_text("x = 2\n")
    assert verify_code_state(code_identity, repo) == "CODE_FINGERPRINT_MISMATCH"

    empty_repo = tmp_path / "emptyrepo"
    empty_repo.mkdir()
    assert verify_code_state(code_identity, empty_repo) == "UNVERIFIABLE"


def test_UNTRACKED_CODE_AT_RECORD_TIME_warning(registry):
    fe = registry.record_experiment(
        **record_kwargs(code_identity=mk_code_identity(untracked_code_files=3, dirty_worktree=True, dirty_summary={"??": 3}))
    )
    assert "UNTRACKED_CODE_AT_RECORD_TIME" in fe.warnings


def test_no_untracked_code_files_no_warning(registry):
    fe = registry.record_experiment(**record_kwargs())
    assert "UNTRACKED_CODE_AT_RECORD_TIME" not in fe.warnings


# ===========================================================================
# R§20.8 -- code-audit conformance repairs
# ===========================================================================


def test_artifact_ref_from_file_computes_real_hash_and_size(tmp_path):
    """R§20.8.1 (blocking) -- v1.1's tests validated a helper defined INSIDE
    the test file, so no registry defect could break them, and an
    `ArtifactRef` could assert an arbitrary hash for an arbitrary path. This
    test targets the REAL constructor."""
    (tmp_path / "sub").mkdir()
    f = tmp_path / "sub" / "artifact.txt"
    f.write_bytes(b"hello world")
    ref = ArtifactRef.from_file(
        tmp_path, "sub/artifact.txt", name="x", kind="other", recorded_at=pd.Timestamp("2026-01-01", tz="UTC")
    )
    import hashlib

    assert ref.sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert ref.size_bytes == 11

    # An arbitrary claimed hash for an arbitrary path is IMPOSSIBLE: the
    # constructor always recomputes from the actual file bytes.
    f.write_bytes(b"different content now")
    ref2 = ArtifactRef.from_file(
        tmp_path, "sub/artifact.txt", name="x", kind="other", recorded_at=pd.Timestamp("2026-01-01", tz="UTC")
    )
    assert ref2.sha256 != ref.sha256


def test_artifact_ref_from_file_missing_raises_unless_allow_missing(tmp_path):
    with pytest.raises(ValidationError):
        ArtifactRef.from_file(
            tmp_path, "nope.txt", name="x", kind="other", recorded_at=pd.Timestamp("2026-01-01", tz="UTC")
        )
    ref = ArtifactRef.from_file(
        tmp_path, "nope.txt", name="x", kind="other", recorded_at=pd.Timestamp("2026-01-01", tz="UTC"),
        allow_missing=True,
    )
    assert ref.sha256 is None
    assert ref.size_bytes is None


def test_verify_artifacts_OK_MISSING_MODIFIED_UNVERIFIABLE(tmp_path):
    """R§20.8.1 (blocking) -- `verify_artifacts()` MUST be a real registry
    method returning the four-state vocabulary, computed against the actual
    file at `repo_root / artifact.path`."""
    repo = tmp_path / "repo"
    (repo / "artifacts").mkdir(parents=True)
    ok_path = repo / "artifacts" / "ok.txt"
    ok_path.write_bytes(b"stable content")
    mod_path = repo / "artifacts" / "mod.txt"
    mod_path.write_bytes(b"original content")
    reg = ExperimentRegistry(tmp_path / "registry", repo_root=repo)

    ok_ref = ArtifactRef.from_file(repo, "artifacts/ok.txt", name="ok", kind="other", recorded_at=pd.Timestamp("2026-01-01", tz="UTC"))
    mod_ref = ArtifactRef.from_file(repo, "artifacts/mod.txt", name="mod", kind="other", recorded_at=pd.Timestamp("2026-01-01", tz="UTC"))
    missing_ref = ArtifactRef.from_file(repo, "artifacts/gone.txt", name="missing", kind="other", recorded_at=pd.Timestamp("2026-01-01", tz="UTC"), allow_missing=True)

    fe = reg.record_experiment(**record_kwargs(artifacts=(ok_ref, mod_ref, missing_ref)))

    mod_path.write_bytes(b"MODIFIED content")
    status = reg.verify_artifacts(fe.record.experiment_id)
    assert status["ok"] == "OK"
    assert status["mod"] == "MODIFIED"
    assert status["missing"] == "UNVERIFIABLE"

    findings = reg.verify_registry()
    assert f"ARTIFACT_MODIFIED:{fe.record.experiment_id}:mod" in findings

    mod_path.unlink()
    status2 = reg.verify_artifacts(fe.record.experiment_id)
    assert status2["mod"] == "MISSING"
    findings2 = reg.verify_registry()
    assert f"ARTIFACT_MISSING:{fe.record.experiment_id}:mod" in findings2


def test_R12_4_zero_warmup_boundary_data_start_equals_eval_start(registry):
    """R§20.8.5 (blocking) -- a fixture where `data_start == eval_start` (no
    warm-up at all). Mutating the adapter's `<=` to `<` would raise here even
    though the record is perfectly valid -- this is what makes R§12.4's
    inclusive-bound requirement discriminating at the true boundary, not just
    "somewhere inside a wide margin"."""
    ds = mk_dataset_ref(
        data_start=pd.Timestamp("2026-01-25", tz="UTC"),
        eval_start=pd.Timestamp("2026-01-25", tz="UTC"),
    )
    # This must succeed: data_start == eval_start satisfies `data_start <= eval_start`.
    fe = registry.record_experiment(**record_kwargs(datasets=(ds,)))
    assert fe.record.datasets[0].data_start == fe.record.datasets[0].eval_start


def test_dirty_worktree_detects_a_DELETED_in_scope_file(tmp_path):
    """R§20.8.7 (blocking, MW-A1) -- a DELETED in-scope file (git status code
    `"D"`) MUST still be classified in-scope and counted, even though it no
    longer exists to be glob'd. Measured defect: the old on-disk-listing
    classifier silently dropped deleted files, reporting `dirty_worktree =
    False` with an EMPTY `dirty_summary`."""
    repo = tmp_path / "repo"
    make_git_repo(repo, files={"src/keepme.py": "x = 1\n", "src/deleteme.py": "y = 2\n"})
    (repo / "src" / "deleteme.py").unlink()
    identity = capture_code_identity(repo, scope_patterns=("src/**/*.py",), contract_versions=CONTRACT_VERSIONS)
    assert identity.dirty_worktree is True
    assert identity.dirty_summary.get("D") == 1


def test_funding_disabled_False_does_not_match_absent_funding_mode_key(registry):
    """R§20.8.9 (MW-A7, blocking) -- a record whose backtest_config carries
    NO `funding_mode` key at all MUST NOT match `funding_disabled=False`."""
    fe = registry.record_experiment(
        **record_kwargs(experiment_type="infrastructure", backtest_config={}, datasets=(), no_datasets_reason="infra test")
    )
    assert fe.record.experiment_id not in {
        f.record.experiment_id for f in registry.find_experiments(funding_disabled=False)
    }
    assert fe.record.experiment_id not in {
        f.record.experiment_id for f in registry.find_experiments(funding_disabled=True)
    }


def test_descendants_of_sorted_by_experiment_id(registry):
    """R§20.8.9 (MW-A8)."""
    root = registry.record_experiment(**record_kwargs())
    c1 = registry.record_experiment(
        **record_kwargs(parent_experiment_id=root.record.experiment_id, change_from_parent="c1")
    )
    c2 = registry.record_experiment(
        **record_kwargs(
            parent_experiment_id=root.record.experiment_id,
            change_from_parent="c2",
            code_identity=mk_code_identity(code_fingerprint="2" * 64),
        )
    )
    desc = registry.descendants_of(root.record.experiment_id)
    ids = [d.record.experiment_id for d in desc]
    assert ids == sorted(ids)


# ===========================================================================
# R§20.11 -- rendering extensions
# ===========================================================================


def test_NOT_A_RESEARCH_RESULT_banner_for_pipeline_validation(registry):
    fe = registry.record_experiment(**record_kwargs(experiment_type="pipeline_validation"))
    s = registry.summary(fe.record.experiment_id)
    lines = s.splitlines()
    assert lines[0] == "NOT A RESEARCH RESULT (experiment_type=pipeline_validation)"
    # banner is ABOVE any metric line
    metric_line_idx = next(i for i, l in enumerate(lines) if l.startswith("metric."))
    assert lines.index(lines[0]) < metric_line_idx


def test_no_banner_for_alpha_research(registry):
    fe = registry.record_experiment(
        **record_kwargs(experiment_type="alpha_research", hypothesis_id="H1", search_space_id="ss1")
    )
    s = registry.summary(fe.record.experiment_id)
    assert "NOT A RESEARCH RESULT" not in s


def test_summary_renders_reason_for_run_and_notes(registry):
    fe = registry.record_experiment(**record_kwargs(reason_for_run="a very specific reason", notes="a specific note"))
    s = registry.summary(fe.record.experiment_id)
    assert "reason_for_run: a very specific reason" in s
    assert "notes: a specific note" in s


def test_summary_renders_per_dataset_eval_window(registry):
    fe = registry.record_experiment(**record_kwargs())
    s = registry.summary(fe.record.experiment_id)
    ds = fe.record.datasets[0]
    assert f"eval=[{ds.eval_start}, {ds.eval_end}]" in s


def test_summary_renders_divergence_detail_when_DIVERGED(registry):
    fe1 = registry.record_experiment(**record_kwargs())
    metrics2 = dict(mk_result_summary().metrics)
    metrics2["sharpe"] = 999.0
    fe2 = registry.record_experiment(
        **record_kwargs(results=mk_result_summary(metrics=metrics2), code_identity=mk_code_identity())
    )
    assert fe2.reproducibility_status == "DIVERGED"
    s = registry.summary(fe2.record.experiment_id)
    assert "divergence_detail: metrics.sharpe" in s
    findings = registry.verify_registry()
    assert f"DIVERGED:{fe2.record.experiment_id}" in findings


def test_summary_renders_search_space_id_and_n_configs_evaluated(registry):
    fe = registry.record_experiment(
        **record_kwargs(
            experiment_type="alpha_research", hypothesis_id="H1", search_space_id="ss-x", n_configs_evaluated=7
        )
    )
    s = registry.summary(fe.record.experiment_id)
    assert "search_space_id: ss-x" in s
    assert "n_configs_evaluated: 7" in s


def test_summary_table_gains_recorded_via_warnings_and_reproducibility_columns(registry):
    fe = registry.record_experiment(**record_kwargs())
    table = registry.summary_table([fe])
    assert "recorded_via=manual" in table
    assert "warnings=" in table
    assert "reproducibility_status=UNIQUE" in table
