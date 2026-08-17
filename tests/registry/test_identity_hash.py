"""R§18.1(1) — deterministic identity (R§5.1-5.3).

Covers M1 (created_at excluded from semantic_hash), M3 (code_fingerprint
part of exact_hash), M5 (run_seq zero-padding, 11-record fixture), M14
(sort_keys / key-insertion-order invariance).
"""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from registry import store as store_mod
from registry.store import ExperimentRegistry

from _factories import mk_code_identity, mk_dataset_ref, mk_strategy_ref, record_kwargs


def test_created_at_does_not_affect_semantic_or_exact_hash(registry: ExperimentRegistry):
    """M1 target. v1.0's wording ('created_at does not affect experiment_id')
    was unsatisfiable since experiment_id encodes run_seq; the correct
    invariant is that a SECOND identical record shares the id PREFIX with
    run_seq incremented."""
    kwargs1 = record_kwargs(created_at=pd.Timestamp("2020-01-01", tz="UTC"))
    kwargs2 = record_kwargs(created_at=pd.Timestamp("2030-01-01", tz="UTC"))
    fe1 = registry.record_experiment(**kwargs1)
    fe2 = registry.record_experiment(**kwargs2)
    assert fe1.record.semantic_hash == fe2.record.semantic_hash
    assert fe1.record.exact_hash == fe2.record.exact_hash
    assert fe2.record.run_seq == fe1.record.run_seq + 1
    prefix1 = fe1.record.experiment_id.split("-r")[0]
    prefix2 = fe2.record.experiment_id.split("-r")[0]
    assert prefix1 == prefix2


@pytest.mark.parametrize(
    "mutator,field",
    [
        (lambda kw: kw.update(universe_policy="single_symbol_fixed:ETH"), "universe_policy"),
        (lambda kw: kw.update(survivorship_safe=True), "survivorship_safe"),
        (lambda kw: kw["backtest_config"].update(fee_bps=999.0), "backtest_config"),
    ],
)
def test_each_hashed_field_changed_one_at_a_time_changes_hash(registry, mutator, field):
    base = record_kwargs()
    fe1 = registry.record_experiment(**base)

    other_root = registry.root.parent / "other_root"
    other = ExperimentRegistry(other_root)
    changed = record_kwargs()
    mutator(changed)
    fe2 = other.record_experiment(**changed)
    assert fe1.record.semantic_hash != fe2.record.semantic_hash, field
    assert fe1.record.exact_hash != fe2.record.exact_hash, field


def test_dataset_field_change_changes_hash(registry, tmp_path):
    base = record_kwargs()
    fe1 = registry.record_experiment(**base)

    other = ExperimentRegistry(tmp_path / "other_root2")
    changed = record_kwargs(datasets=(mk_dataset_ref(symbols=("ETH",)),))
    fe2 = other.record_experiment(**changed)
    assert fe1.record.semantic_hash != fe2.record.semantic_hash


def test_strategy_params_change_changes_hash(registry, tmp_path):
    base = record_kwargs()
    fe1 = registry.record_experiment(**base)
    other = ExperimentRegistry(tmp_path / "other_root3")
    changed = record_kwargs(strategy=mk_strategy_ref(params={"window": 200}))
    fe2 = other.record_experiment(**changed)
    assert fe1.record.semantic_hash != fe2.record.semantic_hash


class TestKeyInsertionOrderInvariance:
    """R§16.4 — M14 target. Both hashes MUST be invariant to dict key
    insertion order in strategy.params/backtest_config/custom/run_facts."""

    def test_semantic_and_exact_hash_invariant_to_param_dict_key_order(self, tmp_path):
        reg_a = ExperimentRegistry(tmp_path / "a")
        reg_b = ExperimentRegistry(tmp_path / "b")
        kw_a = record_kwargs(strategy=mk_strategy_ref(params={"a": 1, "b": 2}))
        kw_b = record_kwargs(strategy=mk_strategy_ref(params={"b": 2, "a": 1}))
        fe_a = reg_a.record_experiment(**kw_a)
        fe_b = reg_b.record_experiment(**kw_b)
        assert fe_a.record.semantic_hash == fe_b.record.semantic_hash
        assert fe_a.record.exact_hash == fe_b.record.exact_hash

    def test_backtest_config_key_order_invariance(self, tmp_path):
        reg_a = ExperimentRegistry(tmp_path / "a")
        reg_b = ExperimentRegistry(tmp_path / "b")
        cfg1 = {"funding_mode": "required", "funding_notional_basis": "period_start"}
        cfg2 = {"funding_notional_basis": "period_start", "funding_mode": "required"}
        fe_a = reg_a.record_experiment(**record_kwargs(backtest_config=cfg1))
        fe_b = reg_b.record_experiment(**record_kwargs(backtest_config=cfg2))
        assert fe_a.record.semantic_hash == fe_b.record.semantic_hash


def test_semantic_hash_unchanged_when_only_code_changes_but_exact_hash_changes(registry, tmp_path):
    kw1 = record_kwargs(code_identity=mk_code_identity(code_fingerprint="1" * 64))
    fe1 = registry.record_experiment(**kw1)

    other = ExperimentRegistry(tmp_path / "other_code")
    kw2 = record_kwargs(code_identity=mk_code_identity(code_fingerprint="2" * 64))
    fe2 = other.record_experiment(**kw2)

    assert fe1.record.semantic_hash == fe2.record.semantic_hash
    assert fe1.record.exact_hash != fe2.record.exact_hash


def test_M3_code_fingerprint_is_part_of_exact_hash_even_with_identical_git_state(registry, tmp_path):
    """M3 fixture note: the pair MUST be identical in git_commit,
    dirty_worktree, contract_versions, differing ONLY in code_fingerprint —
    otherwise the mutation (dropping code_fingerprint from the exact_hash
    payload) is vacuous because something else already changed the hash."""
    code1 = mk_code_identity(git_commit="f" * 40, dirty_worktree=True, code_fingerprint="1" * 64)
    code2 = mk_code_identity(git_commit="f" * 40, dirty_worktree=True, code_fingerprint="2" * 64)
    assert code1.git_commit == code2.git_commit
    assert code1.dirty_worktree == code2.dirty_worktree
    assert code1.contract_versions == code2.contract_versions
    assert code1.code_fingerprint != code2.code_fingerprint  # self-guard: fixture actually differs

    fe1 = registry.record_experiment(**record_kwargs(code_identity=code1))
    other = ExperimentRegistry(tmp_path / "other_m3")
    fe2 = other.record_experiment(**record_kwargs(code_identity=code2))
    assert fe1.record.semantic_hash == fe2.record.semantic_hash
    assert fe1.record.exact_hash != fe2.record.exact_hash


def test_M5_run_seq_zero_padded_with_eleven_records(registry):
    """M5 fixture note: `{:d}` and `{:02d}` sort/format identically for
    run_seq <= 9 — the mutation only discriminates once run_seq reaches 10,
    so this fixture MUST contain 11 records (r0..r10) sharing one
    exact_hash."""
    ids = []
    for i in range(11):
        fe = registry.record_experiment(**record_kwargs(created_at=pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=i)))
        ids.append(fe.record.experiment_id)

    # Self-guard: prove the fixture really has 11 records sharing one
    # exact_hash before trusting the padding assertion below.
    exact_hashes = {registry.load_experiment(i).record.exact_hash for i in ids}
    assert len(exact_hashes) == 1
    assert len(ids) == 11

    prefix = ids[0].split("-r")[0]
    expected = [f"{prefix}-r{i:02d}" for i in range(11)]
    assert ids == expected

    listed = [fe.record.experiment_id for fe in registry.list_experiments()]
    assert listed == sorted(listed)
    assert listed == expected
