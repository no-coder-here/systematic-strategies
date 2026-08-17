"""R§18.1(10) — artifact references (R§9).

R§21.6.2 (material warning fix) — the local `_make_artifact_ref_from_file`
helper this file used to define re-implemented `ArtifactRef.from_file`'s hash
computation independently, so these tests validated a helper defined INSIDE
this test file (which no defect in the real constructor could ever break),
not the production code path. Every test below now calls
`ArtifactRef.from_file` directly — the REAL constructor
(`tests/registry/test_r20_amendments.py`'s
`test_artifact_ref_from_file_computes_real_hash_and_size` already covers its
hash/size correctness end to end; this file focuses on the R§9 vocabulary/
path invariants and the registry-level MISSING_ARTIFACT/verify wiring).
"""
from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from registry.models import ArtifactRef, ValidationError

from _factories import mk_artifact_ref, record_kwargs


def test_hash_and_size_captured(tmp_path):
    repo_root = tmp_path / "repo"
    f = repo_root / "out.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"a": 1}')
    ref = ArtifactRef.from_file(
        repo_root, "out.json", name="equity_curve", kind="equity_curve",
        recorded_at=pd.Timestamp("2026-08-17", tz="UTC"),
    )
    assert ref.sha256 == hashlib.sha256(b'{"a": 1}').hexdigest()
    assert ref.size_bytes == len(b'{"a": 1}')


def test_add_artifact_and_verify_modified(registry, tmp_path):
    repo_root = tmp_path / "repo"
    f = repo_root / "artifacts" / "eq.json"
    f.parent.mkdir(parents=True)
    f.write_text("v1")
    fe = registry.record_experiment(**record_kwargs())
    ref = ArtifactRef.from_file(
        repo_root, "artifacts/eq.json", name="equity_curve", kind="equity_curve",
        recorded_at=pd.Timestamp("2026-08-17", tz="UTC"),
    )
    fe2 = registry.add_artifact(fe.record.experiment_id, ref)
    assert any(a.name == ref.name for a in fe2.artifacts)

    f.write_text("v2 modified")
    on_disk_hash = hashlib.sha256(f.read_bytes()).hexdigest()
    stored = [a for a in fe2.artifacts if a.name == ref.name][0]
    assert stored.sha256 != on_disk_hash  # MODIFIED, by direct comparison


def test_missing_after_deletion_allow_missing_emits_warning(registry, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    ref = ArtifactRef.from_file(
        repo_root, "artifacts/gone.json", name="gone", kind="equity_curve",
        recorded_at=pd.Timestamp("2026-08-17", tz="UTC"), allow_missing=True,
    )
    assert ref.sha256 is None
    fe = registry.record_experiment(**record_kwargs(artifacts=(ref,)))
    assert any(w.startswith("MISSING_ARTIFACT:gone") for w in fe.warnings)


def test_absolute_path_raises():
    with pytest.raises(ValidationError):
        mk_artifact_ref(path="/tmp/absolute/path.json")


def test_kind_must_be_in_closed_vocabulary():
    with pytest.raises(ValidationError):
        mk_artifact_ref(kind="not_a_real_kind")


def test_R21_6_2_fabricated_sha256_for_a_real_path_is_caught_by_verify_artifacts(tmp_path):
    """R§21.6.2 (material warning) — `ArtifactRef.__init__` remains
    permissive: a caller CAN hand-construct an `ArtifactRef` asserting an
    arbitrary `sha256` for an arbitrary `path` without going through
    `from_file` (this is unavoidable given `ExperimentRecord.from_dict` MUST
    reconstruct an `ArtifactRef` from a persisted record without re-hashing
    a file that may since have moved or been deleted — R§8.2 write-once
    already accepted this at record time). The registry's actual defence
    against a FABRICATED hash is `verify_artifacts()`/`verify_registry()`,
    which recompute from the real file and report `MODIFIED` whenever a
    claimed `sha256` does not match — this test targets THAT safety net
    directly, on a hash that was WRONG from the moment of construction (not
    merely changed afterwards, which `test_verify_artifacts_OK_MISSING_MODIFIED_UNVERIFIABLE`
    in `test_r20_amendments.py` already covers)."""
    from registry.store import ExperimentRegistry

    repo_root = tmp_path / "repo"
    (repo_root / "artifacts").mkdir(parents=True)
    real_path = repo_root / "artifacts" / "real.txt"
    real_path.write_bytes(b"the real content")

    fabricated = ArtifactRef(
        name="fab", kind="other", path="artifacts/real.txt",
        sha256="0" * 64,  # NOT the real file's hash, asserted directly (no from_file call)
        size_bytes=999,
        recorded_at=pd.Timestamp("2026-08-17", tz="UTC"), description=None,
    )
    reg = ExperimentRegistry(tmp_path / "registry", repo_root=repo_root)
    fe = reg.record_experiment(**record_kwargs(artifacts=(fabricated,)))

    status = reg.verify_artifacts(fe.record.experiment_id)
    assert status["fab"] == "MODIFIED"
    assert f"ARTIFACT_MODIFIED:{fe.record.experiment_id}:fab" in reg.verify_registry()
