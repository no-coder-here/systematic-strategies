"""R§20.8.6 (blocking, BD-A6/MW-A10) -- one discriminating test for EACH
survivor the code audit reported. Organized by the area named in R§20.8.6's
own enumeration. Each test's docstring states which area it targets.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from backtest.models import BacktestResult
from experiments.qr_smoke_001 import pipeline as qr_pipeline
from registry.backtest_adapter import record_backtest_result
from registry.models import RegistryError, RegistryIntegrityError, ValidationError
from registry.store import ExperimentRegistry

from _factories import mk_code_identity, mk_dataset_ref, mk_result_summary, mk_strategy_ref, record_kwargs


# ===========================================================================
# R§8.4 -- fold order / notes append / tags_added union / unknown event /
# blank line
# ===========================================================================


def test_annotate_appends_notes_and_unions_tags_sorted(registry):
    fe = registry.record_experiment(**record_kwargs(notes="original note", tags=("z",)))
    registry.annotate(fe.record.experiment_id, note="second note", tags=("a", "m"))
    folded = registry.annotate(fe.record.experiment_id, note="third note", tags=("b",))
    assert folded.notes == "original note\nsecond note\nthird note"
    assert folded.tags == ("a", "b", "m", "z")


def test_unknown_history_event_kind_raises_on_fold(registry):
    fe = registry.record_experiment(**record_kwargs())
    history_path = registry.root / "history.jsonl"
    with open(history_path, "a") as f:
        f.write(
            '{"seq":2,"at":{"$ts":"2026-08-17T00:00:00+00:00"},"logged_at":{"$ts":"2026-08-17T00:00:00+00:00"},'
            '"prev_line_sha256":null,"event":"some_unknown_kind","experiment_id":"%s","payload":{}}\n'
            % fe.record.experiment_id
        )
    with pytest.raises(RegistryIntegrityError):
        registry.load_experiment(fe.record.experiment_id)


def test_blank_history_line_raises(registry):
    fe = registry.record_experiment(**record_kwargs())
    history_path = registry.root / "history.jsonl"
    with open(history_path, "a") as f:
        f.write("\n")
    with pytest.raises(RegistryIntegrityError):
        registry.list_experiments()


# ===========================================================================
# R§5.5 -- result_warnings in the operand; folded recomputation
# ===========================================================================


def test_changed_result_warnings_between_reruns_is_DIVERGED_with_detail(registry):
    """result_warnings IS part of the R§5.5 comparison operand -- a token
    appearing/disappearing between two runs of one configuration is a real
    divergence and MUST NOT be masked."""
    registry.record_experiment(**record_kwargs(results=mk_result_summary(result_warnings=())))
    fe2 = registry.record_experiment(
        **record_kwargs(results=mk_result_summary(result_warnings=("RUINED",)), reason_for_run="rerun")
    )
    assert fe2.reproducibility_status == "DIVERGED"
    assert "result_warnings" in fe2.divergence_detail


def test_reproducibility_status_recomputed_when_baseline_status_changes_later(registry):
    """R§5.5 (BD11) -- reproducibility_status is a property of the FOLDED
    view, recomputed on every load, never frozen at creation. Changing the
    BASELINE's status after the fact must change the rerun's reported
    status the next time it is loaded."""
    fe1 = registry.record_experiment(**record_kwargs())
    fe2 = registry.record_experiment(**record_kwargs(reason_for_run="rerun"))
    assert fe2.reproducibility_status == "REPRODUCED"
    registry.set_status(fe1.record.experiment_id, "REJECTED", "bad Sharpe, rejecting the whole config")
    reloaded = registry.load_experiment(fe2.record.experiment_id)
    assert reloaded.reproducibility_status == "NOT_COMPARABLE"


# ===========================================================================
# R§12.3 -- uses_proxy_data cross-check; PROVENANCE_INCOMPLETE
# ===========================================================================


@pytest.fixture(scope="module")
def window_a():
    return qr_pipeline.run_window_a()


def _dataset_windows_for(run) -> dict:
    return {
        "hyperliquid.ohlcv.1h.BTC": {
            "data_start": run.raw_index[0], "data_end": run.raw_index[-1],
            "eval_start": run.frame_index[0], "eval_end": run.frame_index[-1],
            "symbols": ("BTC",), "content_hash": "a" * 64,
        },
        "hyperliquid.funding.BTC": {
            "data_start": pd.Timestamp("2023-05-01", tz="UTC"), "data_end": pd.Timestamp("2026-08-20", tz="UTC"),
            "eval_start": None, "eval_end": None, "symbols": ("BTC",), "content_hash": "b" * 64,
        },
    }


def _code_identity():
    from data.provenance import PROCESSING_VERSION
    from registry.models import SCHEMA_VERSION

    return mk_code_identity(
        contract_versions={
            "backtest_contract": "1.5.1", "data_contract": "1.4",
            "registry_schema": SCHEMA_VERSION, "data_processing_version": PROCESSING_VERSION,
        }
    )


def test_uses_proxy_data_mismatch_raises(registry, window_a):
    """R§12.3 -- a `result.uses_proxy_data` that disagrees with the datasets
    DERIVED from `result.provenance` MUST raise, never be silently accepted."""
    flipped = dataclasses.replace(window_a.result, uses_proxy_data=not window_a.result.uses_proxy_data)
    with pytest.raises(ValidationError):
        record_backtest_result(
            registry, flipped, strategy=mk_strategy_ref(), dataset_windows=_dataset_windows_for(window_a),
            universe_policy="single_symbol_fixed:BTC", code_identity=_code_identity(),
            experiment_type="pipeline_validation", research_stage="exploratory",
            reason_for_run="M-uses-proxy-mismatch", created_at=pd.Timestamp("2026-08-17", tz="UTC"),
            n_configs_evaluated=1,
        )


def test_provenance_incomplete_emits_record_level_warning(registry, window_a):
    """R§12.3 -- `provenance_complete is False` MUST emit the record-level
    `PROVENANCE_INCOMPLETE` warning."""
    incomplete = dataclasses.replace(window_a.result, provenance_complete=False)
    fe = record_backtest_result(
        registry, incomplete, strategy=mk_strategy_ref(), dataset_windows=_dataset_windows_for(window_a),
        universe_policy="single_symbol_fixed:BTC", code_identity=_code_identity(),
        experiment_type="pipeline_validation", research_stage="exploratory",
        reason_for_run="M-provenance-incomplete", created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        n_configs_evaluated=1,
    )
    assert "PROVENANCE_INCOMPLETE" in fe.warnings


# ===========================================================================
# R§4.6.2 -- funding-basis coherence
# ===========================================================================


def test_funding_disabled_with_non_sentinel_basis_raises(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(
            **record_kwargs(backtest_config={"funding_mode": "disabled", "funding_notional_basis": "event_price"})
        )


@pytest.mark.parametrize("basis", [None, "not_modelled"])
def test_funding_disabled_with_sentinel_basis_succeeds(registry, basis):
    fe = registry.record_experiment(
        **record_kwargs(backtest_config={"funding_mode": "disabled", "funding_notional_basis": basis})
    )
    assert fe.record.backtest_config["funding_notional_basis"] == basis


# ===========================================================================
# R§4.3.1 -- PROCESSING_VERSION_MISMATCH; registry_schema equality
# ===========================================================================


def test_processing_version_mismatch_warns(registry):
    ds = mk_dataset_ref(processing_version="qr-data-001-v1.0-OLD")
    fe = registry.record_experiment(**record_kwargs(datasets=(ds,)))
    assert any(w.startswith("PROCESSING_VERSION_MISMATCH:") for w in fe.warnings)


def test_registry_schema_mismatch_raises():
    from registry.models import CodeIdentity, ValidationError as VE

    with pytest.raises(VE):
        mk_code_identity(contract_versions={
            "backtest_contract": "1.5.1", "data_contract": "1.4",
            "registry_schema": "some-other-schema-version",
            "data_processing_version": "qr-data-001-v1.3",
        })


# ===========================================================================
# SURVIVORSHIP_UNKNOWN / CONTENT_HASH_UNAVAILABLE / GIT_UNAVAILABLE
# ===========================================================================


def test_survivorship_unknown_warns_when_none(registry):
    fe = registry.record_experiment(**record_kwargs(survivorship_safe=None))
    assert "SURVIVORSHIP_UNKNOWN" in fe.warnings
    assert "SURVIVORSHIP_UNSAFE" not in fe.warnings


def test_content_hash_unavailable_warns(registry):
    ds = mk_dataset_ref(content_hash=None, content_hash_method=None)
    fe = registry.record_experiment(**record_kwargs(datasets=(ds,)))
    assert any(w.startswith("CONTENT_HASH_UNAVAILABLE:") for w in fe.warnings)


def test_git_unavailable_warns(registry):
    fe = registry.record_experiment(**record_kwargs(code_identity=mk_code_identity(git_available=False, git_commit=None)))
    assert "GIT_UNAVAILABLE" in fe.warnings


# ===========================================================================
# R§13.1 -- warning_token union of record-level and result-level
# ===========================================================================


def test_warning_token_matches_result_level_token_too(registry):
    fe = registry.record_experiment(**record_kwargs(results=mk_result_summary(result_warnings=("RUINED",))))
    got = registry.find_experiments(warning_token="RUI")
    assert fe.record.experiment_id in {f.record.experiment_id for f in got}


# ===========================================================================
# R§11 -- no-op status transition
# ===========================================================================


def test_no_op_status_transition_raises(registry):
    fe = registry.record_experiment(**record_kwargs())
    with pytest.raises(ValidationError):
        registry.set_status(fe.record.experiment_id, "COMPLETED", "no-op")


# ===========================================================================
# R§8.3 -- seq numbering; BAD_SEQ
# ===========================================================================


def test_history_seq_increments_by_one_per_event(registry):
    fe = registry.record_experiment(**record_kwargs())
    registry.set_status(fe.record.experiment_id, "REJECTED", "r1")
    registry.annotate(fe.record.experiment_id, note="n1")
    events = registry._read_history_lines()
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_verify_registry_reports_BAD_SEQ_on_tampered_seq(registry):
    fe = registry.record_experiment(**record_kwargs())
    registry.set_status(fe.record.experiment_id, "REJECTED", "r1")
    history_path = registry.root / "history.jsonl"
    lines = history_path.read_text().splitlines()
    import json as _json

    line2 = _json.loads(lines[1])
    line2["seq"] = 99
    lines[1] = _json.dumps(line2, sort_keys=True, separators=(",", ":"))
    history_path.write_text("\n".join(lines) + "\n")
    findings = registry.verify_registry()
    assert "BAD_SEQ:99" in findings


# ===========================================================================
# R§10.3 -- sorted warnings/symbols/datasets on disk
# ===========================================================================


def test_symbols_and_tags_stored_sorted_on_disk(registry):
    ds = mk_dataset_ref(symbols=("ETH", "BTC"))
    fe = registry.record_experiment(**record_kwargs(datasets=(ds,), tags=("z", "a", "m")))
    assert fe.record.datasets[0].symbols == ("BTC", "ETH")
    assert fe.record.tags == ("a", "m", "z")
    text = (registry.root / "records" / f"{fe.record.experiment_id}.json").read_text()
    assert text.index('"BTC"') < text.index('"ETH"')


# ===========================================================================
# Strict-parser record reads
# ===========================================================================


def test_record_file_with_raw_nan_token_raises_on_read(registry):
    fe = registry.record_experiment(**record_kwargs())
    path = registry.root / "records" / f"{fe.record.experiment_id}.json"
    text = path.read_text()
    tampered = text.replace('"total_return": 0.1', '"total_return": NaN', 1)
    assert tampered != text  # self-guard
    path.write_text(tampered)
    with pytest.raises(RegistryIntegrityError):
        registry.load_experiment(fe.record.experiment_id)


# ===========================================================================
# R§14.5 -- parent chronology for out_of_sample
# ===========================================================================


def test_oos_parent_created_at_must_be_before_child(tmp_path):
    from _factories import make_git_repo

    repo = tmp_path / "repo"
    make_git_repo(repo, files={"spec.md": "spec"})
    reg = ExperimentRegistry(tmp_path / "registry", repo_root=repo)
    parent = reg.record_experiment(**record_kwargs(created_at=pd.Timestamp("2026-08-17 12:00", tz="UTC")))
    with pytest.raises(ValidationError):
        reg.record_experiment(
            **record_kwargs(
                research_stage="out_of_sample",
                frozen_spec_ref="spec.md",
                parent_experiment_id=parent.record.experiment_id,
                change_from_parent="oos before parent",
                created_at=pd.Timestamp("2026-08-17 00:00", tz="UTC"),  # BEFORE the parent
            )
        )


# ===========================================================================
# verify_registry(): MISSING_RECORD / DANGLING_PARENT / PARENT_CYCLE /
# RUN_SEQ_GAP / PREFIX_COLLISION / INCONSISTENT_CONTENT_HASH
# ===========================================================================


def test_verify_registry_MISSING_RECORD(registry):
    fe = registry.record_experiment(**record_kwargs())
    (registry.root / "records" / f"{fe.record.experiment_id}.json").unlink()
    assert f"MISSING_RECORD:{fe.record.experiment_id}" in registry.verify_registry()


def test_verify_registry_DANGLING_PARENT(registry):
    parent = registry.record_experiment(**record_kwargs())
    child = registry.record_experiment(
        **record_kwargs(parent_experiment_id=parent.record.experiment_id, change_from_parent="c")
    )
    (registry.root / "records" / f"{parent.record.experiment_id}.json").unlink()
    findings = registry.verify_registry()
    assert f"DANGLING_PARENT:{child.record.experiment_id}" in findings


def test_verify_registry_PARENT_CYCLE(registry):
    a = registry.record_experiment(**record_kwargs())
    b = registry.record_experiment(**record_kwargs(parent_experiment_id=a.record.experiment_id, change_from_parent="a->b"))
    # Manually corrupt a's record file to point its parent at b, forming a cycle a->b->a.
    import json as _json

    path_a = registry.root / "records" / f"{a.record.experiment_id}.json"
    tree = _json.loads(path_a.read_text())
    tree["parent_experiment_id"] = b.record.experiment_id
    tree["change_from_parent"] = "corrupted cycle"
    path_a.write_text(_json.dumps(tree, indent=2, sort_keys=True) + "\n")
    findings = registry.verify_registry()
    assert any(f.startswith("PARENT_CYCLE:") for f in findings)


def test_verify_registry_RUN_SEQ_GAP(registry):
    fe0 = registry.record_experiment(**record_kwargs())
    fe1 = registry.record_experiment(**record_kwargs(reason_for_run="rerun 1"))
    fe2 = registry.record_experiment(**record_kwargs(reason_for_run="rerun 2"))
    # Delete the MIDDLE record (r01) only -- r00 and r02 remain, so the
    # surviving run_seq set for this exact_hash is {0, 2}: not contiguous.
    (registry.root / "records" / f"{fe1.record.experiment_id}.json").unlink()
    history_path = registry.root / "history.jsonl"
    lines = [l for l in history_path.read_text().splitlines() if f'"{fe1.record.experiment_id}"' not in l]
    history_path.write_text("\n".join(lines) + "\n")
    findings = registry.verify_registry()
    assert any(f.startswith("RUN_SEQ_GAP:") for f in findings)


def test_verify_registry_INCONSISTENT_CONTENT_HASH(registry):
    ds1 = mk_dataset_ref(content_hash="a" * 64)
    ds2 = mk_dataset_ref(content_hash="b" * 64)
    registry.record_experiment(**record_kwargs(datasets=(ds1,)))
    registry.record_experiment(**record_kwargs(datasets=(ds2,), reason_for_run="different content_hash, same window"))
    findings = registry.verify_registry()
    assert any(f.startswith("INCONSISTENT_CONTENT_HASH:") for f in findings)


# ===========================================================================
# Query filters not otherwise dedicated-tested
# ===========================================================================


def test_remaining_query_filters_each_discriminate(registry):
    ds_a = mk_dataset_ref(dataset_id="hyperliquid.ohlcv.1h.BTC", source_venue="Hyperliquid", field_type="ohlcv", native_or_proxy="native", proxy_for=None)
    ds_b = mk_dataset_ref(dataset_id="binance.ohlcv.1h.BTC", source_venue="Binance", field_type="ohlcv", native_or_proxy="proxy", proxy_for="Hyperliquid")

    a = registry.record_experiment(
        **record_kwargs(
            datasets=(ds_a,), research_stage="exploratory", hypothesis_id=None,
            backtest_config={"funding_mode": "required", "funding_notional_basis": "period_start"},
            created_at=pd.Timestamp("2026-01-01", tz="UTC"),
        )
    )
    b = registry.record_experiment(
        **record_kwargs(
            datasets=(ds_b,), research_stage="in_sample", reason_for_run="b",
            backtest_config={"funding_mode": "disabled", "funding_notional_basis": None},
            parent_experiment_id=a.record.experiment_id, change_from_parent="a->b",
            created_at=pd.Timestamp("2026-06-01", tz="UTC"),
        )
    )

    assert {f.record.experiment_id for f in registry.find_experiments(semantic_hash=a.record.semantic_hash)} == {a.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(exact_hash=a.record.exact_hash)} == {a.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(research_stage="in_sample")} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(parent_experiment_id=a.record.experiment_id)} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(dataset_id="binance.ohlcv.1h.BTC")} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(source_venue="Binance")} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(field_type="ohlcv")} == {a.record.experiment_id, b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(native_or_proxy="proxy")} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(uses_proxy_data=True)} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(funding_mode="disabled")} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(reproducibility_status="UNIQUE")} == {a.record.experiment_id, b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(created_after=pd.Timestamp("2026-03-01", tz="UTC"))} == {b.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(created_before=pd.Timestamp("2026-03-01", tz="UTC"))} == {a.record.experiment_id}
    assert {f.record.experiment_id for f in registry.find_experiments(hypothesis_id=None)} == {a.record.experiment_id, b.record.experiment_id}


# ===========================================================================
# annotate() / summary_table() / diverged() / status_history / suppress_cagr
# ===========================================================================


def test_annotate_requires_note_or_tags(registry):
    fe = registry.record_experiment(**record_kwargs())
    with pytest.raises(ValidationError):
        registry.annotate(fe.record.experiment_id)


def test_diverged_returns_only_diverged_records(registry):
    registry.record_experiment(**record_kwargs())
    metrics2 = dict(mk_result_summary().metrics)
    metrics2["sharpe"] = 42.0
    diverged_fe = registry.record_experiment(
        **record_kwargs(results=mk_result_summary(metrics=metrics2), code_identity=mk_code_identity())
    )
    other = registry.record_experiment(**record_kwargs(reason_for_run="unrelated unique", code_identity=mk_code_identity(code_fingerprint="8" * 64), datasets=(mk_dataset_ref(dataset_id="hyperliquid.ohlcv.1h.ETH"),)))
    ids = {fe.record.experiment_id for fe in registry.diverged()}
    assert ids == {diverged_fe.record.experiment_id}


def test_status_history_begins_with_creation_status(registry):
    fe = registry.record_experiment(**record_kwargs())
    assert fe.status_history[0][1] is None
    assert fe.status_history[0][2] == "COMPLETED"


def test_suppress_cagr_via_adapter_marks_metrics_and_warning(registry, window_a):
    fe = record_backtest_result(
        registry, window_a.result, strategy=mk_strategy_ref(), dataset_windows=_dataset_windows_for(window_a),
        universe_policy="single_symbol_fixed:BTC", code_identity=_code_identity(),
        experiment_type="pipeline_validation", research_stage="exploratory",
        reason_for_run="M-suppress-cagr", created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        suppress_cagr=True,
        n_configs_evaluated=1,
    )
    assert fe.record.results.metrics["cagr"] is None
    assert "cagr_raw_suppressed" in fe.record.results.metrics
    assert "CAGR_SUPPRESSED" in fe.record.results.result_warnings
