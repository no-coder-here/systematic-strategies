"""Plain (non-conftest) factory helpers for the QR-INFRA-002 registry test
suite. A regular module (not `conftest.py`) so sibling test modules can
`from _factories import ...` them directly — pytest's per-directory
`sys.path` insertion (this directory has no `__init__.py`) makes that import
resolve.

R§18.3 / CLAUDE.md workspace integrity — every helper here writes to
`tmp_path` only, never to the real `experiments/registry/`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd

from registry.models import ArtifactRef, CodeIdentity, DatasetRef, ResultSummary, StrategyRef
from registry.store import ExperimentRegistry

__all__ = [
    "ExperimentRegistry",
    "CONTRACT_VERSIONS",
    "mk_code_identity",
    "mk_dataset_ref",
    "mk_strategy_ref",
    "mk_result_summary",
    "mk_artifact_ref",
    "record_kwargs",
    "make_git_repo",
]

CONTRACT_VERSIONS = {
    "backtest_contract": "1.5.1",
    "data_contract": "1.4",
    "registry_schema": "qr-infra-002-v1.3",
    "data_processing_version": "qr-data-001-v1.3",
}


def mk_code_identity(**overrides) -> CodeIdentity:
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


def mk_dataset_ref(**overrides) -> DatasetRef:
    base = dict(
        dataset_id="hyperliquid.ohlcv.1h.BTC",
        source_venue="Hyperliquid",
        field_type="ohlcv",
        native_or_proxy="native",
        proxy_for=None,
        processing_version="qr-data-001-v1.3",
        dataset_version=None,
        retrieval_date=None,
        dataset_span_start=pd.Timestamp("2026-01-01", tz="UTC"),
        dataset_span_end=pd.Timestamp("2026-08-01", tz="UTC"),
        data_start=pd.Timestamp("2026-01-20 21:00", tz="UTC"),
        data_end=pd.Timestamp("2026-07-31 23:00", tz="UTC"),
        eval_start=pd.Timestamp("2026-01-25", tz="UTC"),
        eval_end=pd.Timestamp("2026-07-31 23:00", tz="UTC"),
        symbols=("BTC",),
        symbol_mapping=None,
        content_hash="c" * 64,
        content_hash_method="col-buffer-v2",
        provenance_notes="test dataset",
    )
    base.update(overrides)
    return DatasetRef(**base)


def mk_strategy_ref(**overrides) -> StrategyRef:
    base = dict(name="qr_smoke_001", version="1.0", params={}, frequency="1h", target_execution_venue="Hyperliquid")
    base.update(overrides)
    return StrategyRef(**base)


def mk_result_summary(**overrides) -> ResultSummary:
    base = dict(
        metrics={
            "total_return": 0.1,
            "cagr": 0.2,
            "annualized_volatility": 0.1,
            "sharpe": 1.0,
            "downside_dev_ann": 0.05,
            "sortino": 1.5,
            "max_drawdown": -0.1,
            "calmar": 2.0,
            "avg_turnover": 0.01,
            "annualized_turnover": 10.0,
        },
        n_periods=100,
        rebalance_count=5,
        ruined=False,
        custom={},
        result_warnings=(),
    )
    base.update(overrides)
    return ResultSummary(**base)


def mk_artifact_ref(**overrides) -> ArtifactRef:
    base = dict(
        name="equity_curve",
        kind="equity_curve",
        path="experiments/qr_smoke_001/artifacts/summary.json",
        sha256="d" * 64,
        size_bytes=123,
        recorded_at=pd.Timestamp("2026-08-17", tz="UTC"),
        description="test artifact",
    )
    base.update(overrides)
    return ArtifactRef(**base)


def record_kwargs(**overrides) -> dict:
    """A complete, valid `record_experiment(**kwargs)` call, so tests only
    need to override the one or two fields relevant to what they exercise."""
    base = dict(
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run="test run",
        code_identity=mk_code_identity(),
        datasets=(mk_dataset_ref(),),
        universe_policy="single_symbol_fixed:BTC",
        survivorship_safe=False,
        strategy=mk_strategy_ref(),
        backtest_config={"funding_mode": "required", "funding_notional_basis": "period_start"},
        status="COMPLETED",
        results=mk_result_summary(),
        created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        # R§20.3.2 — `record_experiment`'s default (no `capability` passed)
        # is `recorded_via="manual"` (only `record_backtest_result` passes
        # the adapter capability), and a manual record carrying `results`
        # REQUIRES this non-empty.
        manual_results_justification="test fixture: hand-asserted result for a non-adapter unit test",
        # R§21.7.1 (blocking) — REQUIRED, no default: every fixture built
        # from this factory genuinely reflects a single evaluated
        # configuration.
        n_configs_evaluated=1,
    )
    base.update(overrides)
    return base


def make_git_repo(root: Path, *, files: dict) -> str:
    """Initializes a real git repo under `root`, writes `files` (relpath ->
    content), stages and commits them, and returns the commit sha. Used by
    the R§6/R§18.1(4) dirty-worktree tests and the R§18.2 M11/M12 mutation
    fixtures — a `git init`ed fixture, never the live repo (R§6.4)."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for relpath, content in files.items():
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        subprocess.run(["git", "-C", str(root), "add", relpath], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "-C",
            str(root),
            "commit",
            "-q",
            "-m",
            "initial",
        ],
        cwd=root,
        check=True,
    )
    out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return out.stdout.strip()
