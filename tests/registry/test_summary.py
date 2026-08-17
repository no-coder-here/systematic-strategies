"""R§15 — human-readable summary rendering, pinned rules."""
from __future__ import annotations

import pytest

from _factories import mk_code_identity, mk_dataset_ref, mk_result_summary, record_kwargs
from registry.models import ValidationError


def test_dirty_worktree_shows_DIRTY_token(registry):
    fe = registry.record_experiment(**record_kwargs(code_identity=mk_code_identity(dirty_worktree=True, dirty_summary={"M": 1})))
    s = registry.summary(fe.record.experiment_id)
    assert "DIRTY" in s


def test_clean_worktree_shows_CLEAN_token(registry):
    fe = registry.record_experiment(**record_kwargs(code_identity=mk_code_identity(dirty_worktree=False)))
    s = registry.summary(fe.record.experiment_id)
    assert "CLEAN" in s
    assert "DIRTY" not in s


def test_survivorship_unknown_renders_as_unknown_never_false(registry):
    fe = registry.record_experiment(**record_kwargs(survivorship_safe=None))
    s = registry.summary(fe.record.experiment_id)
    assert "survivorship_safe: unknown" in s
    assert "survivorship_safe: False" not in s


def test_proxy_dataset_renders_PROXY_for(registry):
    fe = registry.record_experiment(
        **record_kwargs(datasets=(mk_dataset_ref(native_or_proxy="proxy", proxy_for="Hyperliquid"),))
    )
    s = registry.summary(fe.record.experiment_id)
    assert "PROXY(for=Hyperliquid)" in s


def test_suppressed_cagr_renders_na_suppressed_never_zero(registry):
    metrics = dict(mk_result_summary().metrics)
    raw_cagr = metrics["cagr"]
    metrics["cagr"] = None
    metrics["cagr_raw_suppressed"] = raw_cagr
    results = mk_result_summary(metrics=metrics, result_warnings=("CAGR_SUPPRESSED",))
    fe = registry.record_experiment(**record_kwargs(results=results))
    s = registry.summary(fe.record.experiment_id)
    assert "metric.cagr: n/a (suppressed)" in s
    assert "metric.cagr: 0" not in s


def test_cagr_none_without_suppression_warning_raises(registry):
    """R§20.11 (MW-k, blocking) — `cagr is None` MUST only ever mean
    "explicitly suppressed": both `CAGR_SUPPRESSED` in `result_warnings` AND
    `cagr_raw_suppressed` in `metrics` are required, or the renderer would be
    asserting a suppression nobody declared."""
    metrics = dict(mk_result_summary().metrics)
    metrics["cagr"] = None
    with pytest.raises(ValidationError):
        mk_result_summary(metrics=metrics, result_warnings=())  # missing CAGR_SUPPRESSED
    metrics2 = dict(mk_result_summary().metrics)
    metrics2["cagr"] = None
    del metrics2["cagr"]  # no 'cagr' key at all -> not a suppression claim, must NOT raise
    mk_result_summary(metrics=metrics2, result_warnings=())


def test_content_hash_none_renders_unavailable(registry):
    fe = registry.record_experiment(**record_kwargs(datasets=(mk_dataset_ref(content_hash=None, content_hash_method=None),)))
    s = registry.summary(fe.record.experiment_id)
    assert "content_hash: unavailable" in s


def test_nan_metric_renders_nan_never_zero_or_blank(registry):
    metrics = dict(mk_result_summary().metrics)
    metrics["sharpe"] = float("nan")
    results = mk_result_summary(metrics=metrics)
    fe = registry.record_experiment(**record_kwargs(results=results))
    s = registry.summary(fe.record.experiment_id)
    assert "metric.sharpe: nan" in s
