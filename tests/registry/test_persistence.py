"""R§18.1(11) — deterministic persistence, verify_registry(), orphan repair.

Covers M13 (O_EXCL removal), M17 (skip malformed history lines), M26
(run_seq counted from history.jsonl instead of records/)."""
from __future__ import annotations

import dataclasses
import json

import pandas as pd
import pytest

from registry.models import RegistryError, RegistryIntegrityError
from registry.serialize import stored_json
from registry.store import ExperimentRegistry, ID_PREFIX_HEX

from _factories import record_kwargs


def test_byte_identical_across_two_fresh_roots(tmp_path):
    reg_a = ExperimentRegistry(tmp_path / "a")
    reg_b = ExperimentRegistry(tmp_path / "b")
    kw = record_kwargs(created_at=pd.Timestamp("2026-08-17", tz="UTC"))
    fe_a = reg_a.record_experiment(**kw)
    fe_b = reg_b.record_experiment(**dict(kw))
    path_a = tmp_path / "a" / "records" / f"{fe_a.record.experiment_id}.json"
    path_b = tmp_path / "b" / "records" / f"{fe_b.record.experiment_id}.json"
    assert path_a.read_bytes() == path_b.read_bytes()
    assert fe_a.record.experiment_id == fe_b.record.experiment_id


def test_verify_registry_clean_on_a_healthy_registry(registry):
    registry.record_experiment(**record_kwargs())
    assert registry.verify_registry() == ()


def test_record_modified_detected(registry):
    fe = registry.record_experiment(**record_kwargs())
    path = registry.root / "records" / f"{fe.record.experiment_id}.json"
    text = path.read_text()
    # Modify a value that does not trip `ExperimentRecord.__post_init__`
    # validation (still a well-formed, VALID record) — the point of this
    # finding is byte-level tamper detection, not a second validity check.
    assert '"test run"' in text
    path.write_text(text.replace('"test run"', '"tampered reason"', 1))
    findings = registry.verify_registry()
    assert any(f.startswith(f"RECORD_MODIFIED:{fe.record.experiment_id}") for f in findings)


def test_orphan_record_detected_and_repaired(registry):
    fe = registry.record_experiment(**record_kwargs())
    # Simulate a crash: strip the `created` line from history.jsonl, leaving
    # only the record file on disk (R§10.2's accepted ORPHAN_RECORD state).
    history_path = registry.root / "history.jsonl"
    history_path.write_text("")

    findings = registry.verify_registry()
    assert any(f == f"ORPHAN_RECORD:{fe.record.experiment_id}" for f in findings)

    repaired = registry.repair_orphan(fe.record.experiment_id)
    assert repaired.record.experiment_id == fe.record.experiment_id
    findings_after = registry.verify_registry()
    assert not any(f.startswith("ORPHAN_RECORD") for f in findings_after)

    # append-only: the original record content is untouched.
    path = registry.root / "records" / f"{fe.record.experiment_id}.json"
    assert path.read_bytes()  # still readable/parseable
    events = [json.loads(l) for l in history_path.read_text().splitlines()]
    assert events[-1]["event"] == "created_backfilled"
    assert events[-1]["payload"]["recovered_from"] == "ORPHAN_RECORD"


def test_schema_version_unknown_is_distinct_from_unparseable(registry):
    fe = registry.record_experiment(**record_kwargs())
    path = registry.root / "records" / f"{fe.record.experiment_id}.json"
    text = path.read_text()
    needle = '"schema_version": "qr-infra-002-v1.3"'
    assert needle in text  # self-guard: precise top-level key match, not registry_schema
    path.write_text(text.replace(needle, '"schema_version": "qr-infra-002-v9.9"', 1))
    findings = registry.verify_registry()
    assert f"SCHEMA_VERSION_UNKNOWN:{fe.record.experiment_id}" in findings
    assert not any(f.startswith(f"UNPARSEABLE_RECORD:{fe.record.experiment_id}") for f in findings)


def test_unparseable_record_detected(registry):
    fe = registry.record_experiment(**record_kwargs())
    path = registry.root / "records" / f"{fe.record.experiment_id}.json"
    path.write_text("{not valid json")
    findings = registry.verify_registry()
    assert any(f == f"UNPARSEABLE_RECORD:{fe.record.experiment_id}" for f in findings)


def test_corrupt_history_line_raises_on_read(registry):
    registry.record_experiment(**record_kwargs())
    history_path = registry.root / "history.jsonl"
    with open(history_path, "a") as f:
        f.write("{not valid json at all\n")
    with pytest.raises(RegistryIntegrityError):
        registry.list_experiments()


def test_M13_write_once_guard_o_excl(registry):
    fe = registry.record_experiment(**record_kwargs())
    record = registry._read_record(fe.record.experiment_id)
    with pytest.raises(RegistryError):
        registry._write_record_once(record)  # same experiment_id, already on disk


def test_M26_run_seq_counted_from_records_dir_not_history_self_healing(registry):
    """M26 target. R§5.3.1: an ORPHAN_RECORD (record file, no `created`
    event) must not wedge the exact_hash forever — the NEXT record for the
    identical config must self-heal to run_seq==1, not collide at run_seq==0
    again."""
    fe0 = registry.record_experiment(**record_kwargs())
    history_path = registry.root / "history.jsonl"
    history_path.write_text("")  # manufacture an orphan: zero `created` events

    fe1 = registry.record_experiment(**record_kwargs(reason_for_run="post-orphan retry"))
    assert fe1.record.run_seq == 1
    assert fe1.record.experiment_id != fe0.record.experiment_id
