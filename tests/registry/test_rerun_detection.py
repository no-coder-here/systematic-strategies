"""R§18.1(7) — duplicate/rerun detection (R§5.4/5.5).

Covers M2 (results in the hash payload, DIVERGED sub-case), M6 (isclose
tolerance), M7 (NaN-unsafe ==), M21 (prefix collision with ID_PREFIX_HEX=2),
M25 (NOT_COMPARABLE collapsed to DIVERGED)."""
from __future__ import annotations

import pandas as pd
import pytest

from registry import store as store_mod
from registry.models import RegistryError
from registry.store import ExperimentRegistry

from _factories import mk_result_summary, record_kwargs


def test_rerun_gets_run_seq_1_and_REPRODUCED(registry: ExperimentRegistry):
    fe1 = registry.record_experiment(**record_kwargs())
    fe2 = registry.record_experiment(**record_kwargs(reason_for_run="rerun"))
    assert fe2.record.run_seq == 1
    assert fe2.record.rerun_of == fe1.record.experiment_id
    assert fe2.reproducibility_status == "REPRODUCED"
    assert fe1.reproducibility_status == "UNIQUE"


def test_M7_nan_sharpe_rerun_is_REPRODUCED_not_diverged(registry: ExperimentRegistry):
    """M7 target — NaN Sharpe is reachable whenever annualized_volatility ==
    0.0 or n_periods < 2 (metrics.py:44-57). A raw float `==` comparison
    would report every NaN-Sharpe rerun DIVERGED; canonical-form comparison
    (NaN -> literal $nonfinite wrapper) reports REPRODUCED."""
    metrics = dict(mk_result_summary().metrics)
    metrics["sharpe"] = float("nan")
    results = mk_result_summary(metrics=metrics)
    assert results.metrics["sharpe"] != results.metrics["sharpe"]  # self-guard: really NaN

    registry.record_experiment(**record_kwargs(results=results))
    fe2 = registry.record_experiment(**record_kwargs(results=results, reason_for_run="rerun"))
    assert fe2.reproducibility_status == "REPRODUCED"
    assert fe2.divergence_detail == ()


def test_M6_a_1e9_perturbation_is_DIVERGED_with_detail(registry: ExperimentRegistry):
    base_results = mk_result_summary()
    perturbed_metrics = dict(base_results.metrics)
    perturbed_metrics["sharpe"] = perturbed_metrics["sharpe"] + 1e-9
    perturbed = mk_result_summary(metrics=perturbed_metrics)
    assert perturbed.metrics["sharpe"] != base_results.metrics["sharpe"]  # self-guard

    registry.record_experiment(**record_kwargs(results=base_results))
    fe2 = registry.record_experiment(**record_kwargs(results=perturbed, reason_for_run="rerun"))
    assert fe2.reproducibility_status == "DIVERGED"
    assert "metrics.sharpe" in fe2.divergence_detail


def test_diverged_on_changed_custom_subkey(registry: ExperimentRegistry):
    base = mk_result_summary(custom={"max_gross_exposure": 1.0})
    changed = mk_result_summary(custom={"max_gross_exposure": 1.05})
    registry.record_experiment(**record_kwargs(results=base))
    fe2 = registry.record_experiment(**record_kwargs(results=changed, reason_for_run="rerun"))
    assert fe2.reproducibility_status == "DIVERGED"
    assert fe2.divergence_detail == ("custom.max_gross_exposure",)


def test_M25_NOT_COMPARABLE_when_baseline_is_failed(registry: ExperimentRegistry):
    """M25 target. R§17's record 5 IS a FAILED run_seq==0; a rerun of the
    same exact_hash config that later COMPLETES must be NOT_COMPARABLE, not
    DIVERGED (DIVERGED would misreport a fixed data problem as a
    determinism defect)."""
    kw = record_kwargs(
        status="FAILED",
        status_reason="DataIntegrityError: boom",
        results=None,
        run_facts={"note": "first attempt failed"},
    )
    fe1 = registry.record_experiment(**kw)
    assert fe1.status == "FAILED"

    fe2 = registry.record_experiment(**record_kwargs(reason_for_run="retry, now completes"))
    assert fe2.record.run_seq == 1
    assert fe2.reproducibility_status == "NOT_COMPARABLE"
    assert fe2.divergence_detail == ()


def test_exact_rerun_groups_and_semantic_duplicates(registry: ExperimentRegistry, tmp_path):
    fe1 = registry.record_experiment(**record_kwargs())
    fe2 = registry.record_experiment(**record_kwargs(reason_for_run="rerun"))
    groups = registry.exact_rerun_groups()
    assert groups[fe1.record.exact_hash] == (fe1.record.experiment_id, fe2.record.experiment_id)

    # semantic_duplicates: same semantic_hash, different exact_hash (code changed).
    from _factories import mk_code_identity

    fe3 = registry.record_experiment(**record_kwargs(code_identity=mk_code_identity(code_fingerprint="9" * 64), reason_for_run="same config, new code"))
    assert fe3.record.semantic_hash == fe1.record.semantic_hash
    assert fe3.record.exact_hash != fe1.record.exact_hash

    dups = registry.semantic_duplicates()
    assert fe1.record.semantic_hash in dups
    assert set(dups[fe1.record.semantic_hash]) == {fe1.record.experiment_id, fe2.record.experiment_id, fe3.record.experiment_id}
    # size >= 2 only
    assert all(len(v) >= 2 for v in dups.values())


def test_run_seq_over_99_raises(registry: ExperimentRegistry, monkeypatch):
    # Pre-populate the records/ directory with 100 fake files sharing one
    # prefix so the 101st call sees run_seq == 100.
    fe0 = registry.record_experiment(**record_kwargs())
    prefix = fe0.record.experiment_id.split("-r")[0]
    records_dir = registry.root / "records"
    import shutil

    src = records_dir / f"{fe0.record.experiment_id}.json"
    for i in range(1, 100):
        shutil.copy(src, records_dir / f"{prefix}-r{i:02d}.json")

    with pytest.raises(RegistryError):
        registry.record_experiment(**record_kwargs(reason_for_run="one too many"))


def test_M21_prefix_collision_detected_with_small_id_prefix_hex(registry: ExperimentRegistry, monkeypatch):
    """M21 target. At the real 16-hex prefix (64 bits) no fixture can
    construct a collision; with `ID_PREFIX_HEX = 2` (1 byte) a collision is
    reachable in a small number of tries by varying an unhashed... no,
    varying a HASHED field (fee_bps) across many distinct configurations
    until two land on the same 1-byte exact_hash prefix but have DIFFERENT
    full exact_hash values."""
    monkeypatch.setattr(store_mod, "ID_PREFIX_HEX", 2)

    seen_prefix_to_hash = {}
    first_id_for_prefix = None
    collided_kwargs = None
    for fee_bps in range(1, 2000):
        kw = record_kwargs(backtest_config={"funding_mode": "required", "funding_notional_basis": "period_start", "fee_bps": float(fee_bps)})
        # Compute the hash without writing, by peeking at the private hash
        # helper the same way record_experiment does internally.
        from _factories import mk_dataset_ref, mk_strategy_ref
        from registry.models import SCHEMA_VERSION

        data_sorted = sorted((d.semantic_dict() for d in kw["datasets"]), key=lambda x: (x["dataset_id"], x["field_type"], x["source_venue"]))
        semantic_payload = {
            "schema_version": SCHEMA_VERSION,
            "experiment_type": kw["experiment_type"],
            "data": data_sorted,
            "universe_policy": kw["universe_policy"],
            "survivorship_safe": kw["survivorship_safe"],
            "strategy": kw["strategy"].to_dict(),
            "backtest_config": dict(kw["backtest_config"]),
            "frozen_spec_sha256": None,
            "recorded_via": "manual",
        }
        semantic_hash = registry._hash(semantic_payload)
        exact_payload = {
            "semantic_hash": semantic_hash,
            "code": {
                "git_commit": kw["code_identity"].git_commit,
                "dirty_worktree": kw["code_identity"].dirty_worktree,
                "code_fingerprint": kw["code_identity"].code_fingerprint,
                "contract_versions": dict(kw["code_identity"].contract_versions),
            },
        }
        exact_hash = registry._hash(exact_payload)
        prefix = exact_hash[:2]
        if prefix in seen_prefix_to_hash and seen_prefix_to_hash[prefix] != exact_hash:
            collided_kwargs = (first_id_for_prefix[prefix], kw)
            break
        if prefix not in seen_prefix_to_hash:
            seen_prefix_to_hash[prefix] = exact_hash
            if first_id_for_prefix is None:
                first_id_for_prefix = {}
            first_id_for_prefix[prefix] = kw

    assert collided_kwargs is not None, "failed to brute-force a 1-byte prefix collision in 2000 tries"
    kw_a, kw_b = collided_kwargs
    registry.record_experiment(**kw_a)
    with pytest.raises(RegistryError):
        registry.record_experiment(**kw_b)
