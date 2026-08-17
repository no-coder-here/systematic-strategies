"""R§18.1(2) — lineage: children, descendants, chain, dangling/self/cycle."""
from __future__ import annotations

import pytest

from registry.models import RegistryError, RegistryIntegrityError
from registry.store import ExperimentRegistry

from _factories import record_kwargs


def test_children_and_descendants(registry: ExperimentRegistry):
    root = registry.record_experiment(**record_kwargs(reason_for_run="root"))
    child = registry.record_experiment(
        **record_kwargs(
            reason_for_run="child",
            parent_experiment_id=root.record.experiment_id,
            change_from_parent="tweaked fee",
            backtest_config={"funding_mode": "disabled", "funding_notional_basis": "not_modelled"},
        )
    )
    grandchild = registry.record_experiment(
        **record_kwargs(
            reason_for_run="grandchild",
            parent_experiment_id=child.record.experiment_id,
            change_from_parent="tweaked again",
            universe_policy="single_symbol_fixed:ETH",
        )
    )

    kids = registry.children_of(root.record.experiment_id)
    assert [k.record.experiment_id for k in kids] == [child.record.experiment_id]

    desc = {fe.record.experiment_id for fe in registry.descendants_of(root.record.experiment_id)}
    assert desc == {child.record.experiment_id, grandchild.record.experiment_id}

    lineage = registry.lineage_of(grandchild.record.experiment_id)
    assert [fe.record.experiment_id for fe in lineage] == [
        root.record.experiment_id,
        child.record.experiment_id,
        grandchild.record.experiment_id,
    ]


def test_dangling_parent_rejected(registry: ExperimentRegistry):
    with pytest.raises(RegistryError):
        registry.record_experiment(**record_kwargs(parent_experiment_id="EXP-doesnotexist-r00", change_from_parent="x"))


def test_self_parent_rejected_via_dangling_check(registry: ExperimentRegistry):
    # The child's own id does not exist yet at creation time, so referencing
    # it as its own parent is necessarily a dangling-parent rejection.
    with pytest.raises(RegistryError):
        registry.record_experiment(**record_kwargs(parent_experiment_id="EXP-0000000000000000-r00", change_from_parent="x"))


def test_lineage_of_raises_on_cycle(registry: ExperimentRegistry, monkeypatch):
    a = registry.record_experiment(**record_kwargs(reason_for_run="a"))
    b = registry.record_experiment(
        **record_kwargs(reason_for_run="b", parent_experiment_id=a.record.experiment_id, change_from_parent="x")
    )

    # Corrupt the on-disk record to manufacture a cycle a -> b -> a, since
    # the API itself refuses to create one directly (dangling-parent check
    # would fire first). This exercises the cycle GUARD, not the ordinary
    # creation-time rejection.
    import dataclasses

    a_record = registry._read_record(a.record.experiment_id)
    corrupted = dataclasses.replace(a_record, parent_experiment_id=b.record.experiment_id, change_from_parent="cycle")
    path = registry.root / "records" / f"{a.record.experiment_id}.json"
    path.write_text(__import__("registry.serialize", fromlist=["stored_json"]).stored_json(corrupted.to_dict()))

    with pytest.raises(RegistryIntegrityError):
        registry.lineage_of(b.record.experiment_id)


def test_descendants_of_raises_on_cycle(registry: ExperimentRegistry):
    import dataclasses

    a = registry.record_experiment(**record_kwargs(reason_for_run="a"))
    b = registry.record_experiment(
        **record_kwargs(reason_for_run="b", parent_experiment_id=a.record.experiment_id, change_from_parent="x")
    )
    a_record = registry._read_record(a.record.experiment_id)
    corrupted = dataclasses.replace(a_record, parent_experiment_id=b.record.experiment_id, change_from_parent="cycle")
    path = registry.root / "records" / f"{a.record.experiment_id}.json"
    from registry.serialize import stored_json

    path.write_text(stored_json(corrupted.to_dict()))

    with pytest.raises(RegistryIntegrityError):
        registry.descendants_of(a.record.experiment_id)
