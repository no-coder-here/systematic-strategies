"""R§14 — research-integrity invariants. Covers M19 (frozen_spec_ref
resolution check dropped) and M20 (status_reason optional for REJECTED)."""
from __future__ import annotations

import pandas as pd
import pytest

from registry.models import ValidationError

from _factories import mk_dataset_ref, record_kwargs


def test_reason_for_run_required(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(reason_for_run="   "))


def test_change_from_parent_required_when_parent_set(registry):
    parent = registry.record_experiment(**record_kwargs())
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(parent_experiment_id=parent.record.experiment_id, change_from_parent=None))


def test_alpha_research_requires_hypothesis_id(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(
            **record_kwargs(experiment_type="alpha_research", hypothesis_id=None, search_space_id="ss1")
        )
    ok = registry.record_experiment(
        **record_kwargs(
            experiment_type="alpha_research", hypothesis_id="H1", reason_for_run="alpha test", search_space_id="ss1"
        )
    )
    assert ok.record.hypothesis_id == "H1"


def test_alpha_research_requires_search_space_id(registry):
    """R§20.5.1 (blocking) — search_space_id REQUIRED non-empty for the four
    'real research' experiment types."""
    with pytest.raises(ValidationError):
        registry.record_experiment(
            **record_kwargs(experiment_type="alpha_research", hypothesis_id="H1", search_space_id=None)
        )
    with pytest.raises(ValidationError):
        registry.record_experiment(
            **record_kwargs(experiment_type="alpha_research", hypothesis_id="H1", search_space_id="")
        )


def test_M19_out_of_sample_requires_frozen_spec_ref_resolving_to_real_file(registry):
    parent = registry.record_experiment(**record_kwargs())
    with pytest.raises(ValidationError):
        registry.record_experiment(
            **record_kwargs(
                research_stage="out_of_sample",
                parent_experiment_id=parent.record.experiment_id,
                change_from_parent="oos test",
                frozen_spec_ref="docs/this_file_does_not_exist_ABC123.md",
            )
        )


_EXISTING_REPO_FILE = "docs/backtest_contract.md"  # any real, committed repo-relative file


def test_out_of_sample_with_real_spec_file_succeeds_and_hashes_it(registry):
    parent = registry.record_experiment(**record_kwargs())
    fe = registry.record_experiment(
        **record_kwargs(
            research_stage="out_of_sample",
            parent_experiment_id=parent.record.experiment_id,
            change_from_parent="oos test",
            frozen_spec_ref=_EXISTING_REPO_FILE,
            reason_for_run="oos run",
        )
    )
    assert fe.record.frozen_spec_sha256 is not None
    assert len(fe.record.frozen_spec_sha256) == 64


def test_out_of_sample_window_overlap_warns_not_errors(registry):
    parent = registry.record_experiment(
        **record_kwargs(
            datasets=(
                mk_dataset_ref(
                    data_start=pd.Timestamp("2025-12-01", tz="UTC"),
                    data_end=pd.Timestamp("2026-06-01", tz="UTC"),
                    eval_start=pd.Timestamp("2026-01-01", tz="UTC"),
                    eval_end=pd.Timestamp("2026-06-01", tz="UTC"),
                ),
            )
        )
    )
    child = registry.record_experiment(
        **record_kwargs(
            research_stage="out_of_sample",
            parent_experiment_id=parent.record.experiment_id,
            change_from_parent="oos test",
            frozen_spec_ref=_EXISTING_REPO_FILE,
            datasets=(
                mk_dataset_ref(
                    data_start=pd.Timestamp("2026-02-01", tz="UTC"),
                    data_end=pd.Timestamp("2026-09-01", tz="UTC"),
                    eval_start=pd.Timestamp("2026-03-01", tz="UTC"),
                    eval_end=pd.Timestamp("2026-09-01", tz="UTC"),
                ),
            ),
        )
    )
    assert any(w.startswith(f"OOS_WINDOW_OVERLAP:{parent.record.experiment_id}") for w in child.warnings)


def test_M20_status_reason_required_for_non_completed(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(status="REJECTED", status_reason=None))
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(status="REJECTED", status_reason="   "))
    ok = registry.record_experiment(**record_kwargs(status="REJECTED", status_reason="bad Sharpe"))
    assert ok.record.status_reason == "bad Sharpe"


def test_results_n_periods_must_be_positive():
    from registry.models import ResultSummary

    with pytest.raises(ValidationError):
        ResultSummary(metrics={}, n_periods=0, rebalance_count=0, ruined=False, custom={}, result_warnings=())


def test_proxy_without_proxy_for_raises_at_dataset_level():
    with pytest.raises(ValidationError):
        mk_dataset_ref(native_or_proxy="proxy", proxy_for="")


def test_dataset_window_ordering_invariants():
    with pytest.raises(ValidationError):
        mk_dataset_ref(data_start=pd.Timestamp("2026-02-01", tz="UTC"), data_end=pd.Timestamp("2026-01-01", tz="UTC"))
    with pytest.raises(ValidationError):
        mk_dataset_ref(eval_start=pd.Timestamp("2026-02-01", tz="UTC"), eval_end=pd.Timestamp("2026-01-01", tz="UTC"))


def test_empty_datasets_requires_exempt_experiment_type_and_reason(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(datasets=(), no_datasets_reason=None))
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(datasets=(), experiment_type="alpha_research", hypothesis_id="H1", no_datasets_reason="n/a"))
    ok = registry.record_experiment(
        **record_kwargs(datasets=(), experiment_type="infrastructure", no_datasets_reason="pure infra test", backtest_config={})
    )
    assert ok.record.datasets == ()
