"""R§18.1(3) — failed-experiment retention: no survivorship bias, no delete
path. Covers M8 (list_experiments() filtering to COMPLETED)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from registry.store import ExperimentRegistry

from _factories import record_kwargs

REGISTRY_SRC = Path(__file__).resolve().parents[2] / "src" / "registry"


def test_default_listing_includes_every_status(registry: ExperimentRegistry):
    completed = registry.record_experiment(**record_kwargs(status="COMPLETED"))
    rejected = registry.record_experiment(**record_kwargs(status="REJECTED", status_reason="bad Sharpe", results=None, run_facts={}, datasets=(), experiment_type="infrastructure", no_datasets_reason="rejected before any dataset touched", backtest_config={}))
    invalid = registry.record_experiment(**record_kwargs(status="INVALID", status_reason="methodology error", results=None, run_facts={}, datasets=(), experiment_type="infrastructure", no_datasets_reason="invalid before any dataset touched", backtest_config={}))
    failed = registry.record_experiment(**record_kwargs(status="FAILED", status_reason="DataIntegrityError: boom", results=None, run_facts={}, datasets=(), experiment_type="infrastructure", no_datasets_reason="failed before any dataset touched", backtest_config={}))

    ids = {fe.record.experiment_id for fe in registry.list_experiments()}
    assert ids == {
        completed.record.experiment_id,
        rejected.record.experiment_id,
        invalid.record.experiment_id,
        failed.record.experiment_id,
    }


def test_list_experiments_takes_no_parameters():
    import inspect

    sig = inspect.signature(ExperimentRegistry.list_experiments)
    params = [p for name, p in sig.parameters.items() if name != "self"]
    assert params == []


def test_no_delete_api_by_attribute_name(registry: ExperimentRegistry):
    public_methods = {name for name in dir(registry) if not name.startswith("_")}
    forbidden_substrings = ("delete", "purge", "prune", "archive", "truncate", "remove", "unlink")
    for name in public_methods:
        for bad in forbidden_substrings:
            assert bad not in name.lower(), f"public method {name!r} looks like a delete API"


def test_no_delete_api_by_source_scan():
    """R§8.2.2 (finding 12) — MUST scan source, not just public attribute
    names, since a private `_unlink_record` helper would pass the attribute
    check above but still be a delete path."""
    forbidden = ["os.remove(", "os.unlink(", "shutil.rmtree(", ".unlink(", ".truncate("]
    offending = []
    for path in REGISTRY_SRC.glob("*.py"):
        text = path.read_text()
        for tok in forbidden:
            if tok in text:
                offending.append((path.name, tok))
        # `open(..., "w")` / "w+" / "wb" targeting records/ or history.jsonl
        for m in re.finditer(r'open\([^)]*["\']w[b+]?["\']', text):
            offending.append((path.name, m.group(0)))
    assert offending == []
