"""R§17 -- QR-SMOKE-001 retrospective registration migration.

R§17.1 (blocking): the migration MUST construct every provider `offline=True`
and MUST NOT re-fetch. A test MUST assert the module contains no
`offline=False` and performs no network call.

The full end-to-end test writes into `tmp_path` (never the real
`experiments/registry/`, which is populated exactly once by the real
migration run and is then write-once-protected) and takes ~15-30s (Window B1
is a ~57k-bar run) -- acceptable as a single, non-parametrized test.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

MIGRATION_SRC = Path(__file__).resolve().parents[2] / "experiments" / "registry_migration" / "register_qr_smoke_001.py"


def test_module_source_contains_no_offline_false():
    text = MIGRATION_SRC.read_text()
    assert "offline=False" not in text


def test_module_source_never_calls_provider_with_offline_omitted_as_true_literal():
    text = MIGRATION_SRC.read_text()
    # every direct provider construction in this file passes offline=True explicitly
    import re

    for m in re.finditer(r"(HyperliquidProvider|BinanceUMProvider)\(([^)]*)\)", text):
        assert "offline=True" in m.group(2), f"{m.group(0)} does not pass offline=True explicitly"


def test_M22_migration_ohlcv_window_uses_loaded_raw_span_not_requested_window():
    """M22 target (R§17.5): exercises the MIGRATION MODULE's OWN
    `_ohlcv_window` helper directly (not a locally re-implemented test
    helper), so a mutation that swaps `run.raw_index` for `run.frame_index`
    in that function is actually caught. Measured: Window A raw start
    2026-01-20 21:00Z != frame start 2026-01-25 00:00Z."""
    import sys

    sys.path.insert(0, str(MIGRATION_SRC.parents[2] / "src"))
    sys.path.insert(0, str(MIGRATION_SRC.parents[2]))
    import experiments.registry_migration.register_qr_smoke_001 as M

    run = M.P.run_window_a(base_dir=M.BASE_DIR)
    window = M._ohlcv_window(run, "dummy_hash")
    assert window["data_start"] == run.raw_index[0]
    assert window["data_start"] != run.frame_index[0]
    assert window["eval_start"] == run.frame_index[0]


def test_no_network_call_during_a_real_offline_pipeline_run(monkeypatch):
    """Monkeypatched network guard: any attempt to open a socket during an
    offline pipeline run fails the test loudly."""

    def _guard(*args, **kwargs):
        raise AssertionError("network call attempted during an offline run (R§17.1)")

    monkeypatch.setattr(socket.socket, "connect", _guard)

    import sys

    sys.path.insert(0, str(MIGRATION_SRC.parents[2] / "src"))
    from experiments.qr_smoke_001 import pipeline as P

    run = P.run_window_a(base_dir=str(MIGRATION_SRC.parents[2] / "data"))
    assert run.result is not None


def _snapshot_file_set(root: Path) -> set:
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


@pytest.mark.slow
def test_full_migration_end_to_end_into_tmp_path(monkeypatch):
    """R§9 pins artifact paths as repo-root-relative (an absolute/outside
    -repo path raises `ValidationError`), so this end-to-end exercise
    (which attaches real dataset-snapshot/equity-curve artifacts) MUST use
    a registry root INSIDE the repo tree -- `tmp_path` would not satisfy
    R§9's own constraint. It is a scratch directory distinct from the real
    `experiments/registry/` (never written to by any test, per CLAUDE.md /
    R§18.3) and is removed in this test's `finally` block regardless of
    outcome.

    R§20.8.2 (blocking, BD-A3) -- the R§17.3 rerun subprocess re-imports this
    module FRESH in a child process, so monkeypatching `M.REGISTRY_ROOT` in
    THIS (parent) process's object is not, by itself, proof the child agrees.
    This test additionally captures the REAL registry's dataset-snapshot
    directory file set before and after the whole run and asserts it is
    byte-for-byte unchanged -- this is exactly the assertion that would have
    caught the measured v1.1 defect (two stray parquets written by the child
    subprocess into the real `experiments/registry/artifacts/datasets/`).
    """
    import shutil

    import experiments.registry_migration.register_qr_smoke_001 as M

    repo_root = MIGRATION_SRC.parents[2]
    scratch_root = repo_root / "experiments" / "_test_migration_registry_scratch"
    if scratch_root.exists():
        shutil.rmtree(scratch_root)

    real_snapshot_dir = repo_root / "experiments" / "registry" / "artifacts" / "datasets"
    real_snapshot_files_before = _snapshot_file_set(real_snapshot_dir)

    monkeypatch.setattr(M, "REGISTRY_ROOT", scratch_root)
    monkeypatch.setattr(M, "ARTIFACTS_ROOT", scratch_root / "artifacts")
    monkeypatch.setattr(M, "DATASET_SNAPSHOT_DIR", scratch_root / "artifacts" / "datasets")

    try:
        M.main()

        from registry.store import ExperimentRegistry

        reg = ExperimentRegistry(scratch_root, repo_root=repo_root)
        _assert_migration_registry_healthy(reg, scratch_root)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    real_snapshot_files_after = _snapshot_file_set(real_snapshot_dir)
    assert real_snapshot_files_after == real_snapshot_files_before, (
        "R§20.8.2: the real experiments/registry/artifacts/datasets/ file set changed during a "
        "scratch-root migration run -- the R§17.3 rerun subprocess did not inherit the scratch root"
    )


def _assert_migration_registry_healthy(reg, scratch_root):
    records = reg.list_experiments()
    assert len(records) == 5
    assert reg.verify_registry() == ()

    statuses = {fe.status for fe in records}
    assert statuses == {"COMPLETED", "FAILED"}
    failed = [fe for fe in records if fe.status == "FAILED"]
    assert len(failed) == 1
    assert "DataIntegrityError" in failed[0].record.status_reason

    reruns = reg.exact_rerun_groups()
    rerun_group = [v for v in reruns.values() if len(v) == 2]
    assert len(rerun_group) == 1
    ids = rerun_group[0]
    fe_a, fe_b = reg.load_experiment(ids[0]), reg.load_experiment(ids[1])
    assert fe_b.reproducibility_status == "REPRODUCED" or fe_a.reproducibility_status == "REPRODUCED"

    assert (scratch_root / "artifacts" / "query_demo.txt").exists()
