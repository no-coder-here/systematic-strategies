"""R§21 (QR-INFRA-002-A — TARGETED CLOSURE) — dedicated coverage for every
item R§21 lists that is not already covered by a more natural home
elsewhere:

- R§21.1  adapter-capability unforgeability + constructor-only clock seam.
- R§21.2  rendering defects (folded notes, manual_results_justification).
- R§21.5  one test per hashed `DatasetRef` field + the family/semantic hash
          boundary + payload-assembly ordering (A29).
- R§21.6  one discriminating test per listed survivor (A11, A14, A19, A20a,
          A28, A29, A38, N15, N17, N30, N33, H1, H2).
- R§21.7  `n_configs_evaluated` tri-state (UNKNOWN vs a verified count).

(R§21.3's static-registration rewrite lives in `test_r20_amendments.py`
next to the test it replaces; R§21.4's containment-boundary fixture lives in
`test_backtest_adapter.py` next to the adapter it exercises; R§21.6.2's
`ArtifactRef` fix lives in `test_artifacts.py`.)
"""
from __future__ import annotations

import dataclasses
import inspect
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from registry.backtest_adapter import record_backtest_result
from registry.models import DatasetRef, RegistryError, ValidationError
from registry.store import ExperimentRegistry, _ADAPTER_CAPABILITY, _AdapterCapability

from _factories import mk_code_identity, mk_dataset_ref, mk_result_summary, mk_strategy_ref, record_kwargs

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
TESTS_REGISTRY_DIR = Path(__file__).resolve().parent


# ===========================================================================
# R§21.1 — adapter trust becomes unforgeable
# ===========================================================================


def test_R21_1_capability_none_default_is_manual(registry):
    """No `capability` kwarg at all (the ordinary public path) -> manual."""
    fe = registry.record_experiment(**record_kwargs())
    assert fe.record.recorded_via == "manual"


def test_R21_1_capability_string_is_rejected(registry):
    """R§21.1.4(a) -- a forged string 'adapter' (the ENTIRE v1.2 exploit:
    `_recorded_via='adapter'` bought trust for free) MUST raise, never be
    silently accepted as truthy."""
    with pytest.raises(RegistryError):
        registry.record_experiment(**record_kwargs(), capability="adapter")


def test_R21_1_capability_fresh_instance_is_rejected_by_identity_not_type(registry):
    """R§21.1.4(a) -- a FRESHLY CONSTRUCTED `_AdapterCapability()` (same
    TYPE as the real singleton, but not the SAME OBJECT) MUST be rejected:
    proves the check is `is`, not `isinstance`, which a subclass or a second
    instance would satisfy."""
    forged = _AdapterCapability()
    assert forged is not _ADAPTER_CAPABILITY
    with pytest.raises(RegistryError):
        registry.record_experiment(**record_kwargs(), capability=forged)


def test_R21_1_capability_subclass_instance_is_rejected():
    """R§21.1.2 -- a subclass instance would satisfy `isinstance(x,
    _AdapterCapability)` but MUST still be rejected: the check is identity,
    not type."""

    class _Forged(_AdapterCapability):
        pass

    forged = _Forged()
    assert isinstance(forged, _AdapterCapability)
    assert forged is not _ADAPTER_CAPABILITY

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        reg = ExperimentRegistry(Path(td) / "registry")
        with pytest.raises(RegistryError):
            reg.record_experiment(**record_kwargs(), capability=forged)


def test_R21_1_capability_singleton_from_store_grants_adapter(registry):
    """The ONLY value that grants `recorded_via='adapter'` through the
    public API is the real singleton itself."""
    fe = registry.record_experiment(**record_kwargs(), capability=_ADAPTER_CAPABILITY)
    assert fe.record.recorded_via == "adapter"


def test_R21_1_record_experiment_signature_has_no_recorded_via_or_logged_at_parameter():
    """R§21.1.4(c) -- introspects `inspect.signature`, so a reintroduction of
    either private keyword (in any casing/spelling containing the
    substring) fails this test even if it is renamed slightly."""
    sig = inspect.signature(ExperimentRegistry.record_experiment)
    names = [p.lower() for p in sig.parameters]
    assert not any("recorded_via" in n for n in names)
    assert not any("logged_at" in n for n in names)


def test_R21_1_uses_proxy_data_mismatch_rejected_via_the_real_public_adapter_path(registry):
    """R§21.1.4(b) -- the v1.2 exploit this closes required a proxy dataset
    to be declared native so a manual record could be laundered as adapter-
    verified with no caveats at all. With `_recorded_via` gone, the adapter
    path (`record_backtest_result`, not an internal helper) is the ONLY way
    to get `recorded_via='adapter'` from real data, and R§12.3's
    `uses_proxy_data` cross-check (derived from `result.provenance`, never
    caller-suppliable) rejects any mismatch between what the ENGINE says and
    what the record would assert -- re-verified here after the R§21.1
    refactor, through the real public `record_backtest_result` entry point."""
    import dataclasses as _dc

    from experiments.qr_smoke_001 import pipeline as qr_pipeline
    from data.provenance import PROCESSING_VERSION
    from registry.models import SCHEMA_VERSION

    run = qr_pipeline.run_window_a()
    flipped = _dc.replace(run.result, uses_proxy_data=not run.result.uses_proxy_data)
    code_identity = mk_code_identity(
        contract_versions={
            "backtest_contract": "1.5.1", "data_contract": "1.4",
            "registry_schema": SCHEMA_VERSION, "data_processing_version": PROCESSING_VERSION,
        }
    )
    dataset_windows = {
        "hyperliquid.ohlcv.1h.BTC": {
            "data_start": run.raw_index[0], "data_end": run.raw_index[-1],
            "eval_start": run.frame_index[0], "eval_end": run.frame_index[-1],
            "symbols": ("BTC",), "content_hash": "a" * 64,
        },
        "hyperliquid.funding.BTC": {
            "data_start": pd.Timestamp("2023-05-01", tz="UTC"), "data_end": pd.Timestamp("2026-08-20", tz="UTC"),
            "eval_start": None, "eval_end": None, "symbols": ("BTC",), "content_hash": "b" * 64,
        },
    }
    with pytest.raises(ValidationError):
        record_backtest_result(
            registry, flipped, strategy=mk_strategy_ref(), dataset_windows=dataset_windows,
            universe_policy="single_symbol_fixed:BTC", code_identity=code_identity,
            experiment_type="pipeline_validation", research_stage="exploratory",
            reason_for_run="R21.1(b) re-verification", created_at=pd.Timestamp("2026-08-17", tz="UTC"),
            n_configs_evaluated=1,
        )


def test_R21_1_3_clock_injected_only_via_constructor_not_per_call(tmp_path):
    """R§21.1.3 (blocking) -- the wall-clock witness (`logged_at`) is
    injectable ONLY through the `ExperimentRegistry` constructor's `_clock`,
    never per-call. A frozen clock makes `logged_at` deterministic and
    reproducible across two records."""
    frozen = pd.Timestamp("2030-01-01T00:00:00", tz="UTC")
    reg = ExperimentRegistry(tmp_path / "registry", _clock=lambda: frozen)
    fe = reg.record_experiment(**record_kwargs())
    events = reg._read_history_lines()
    created_ev = [e for e in events if e["experiment_id"] == fe.record.experiment_id][0]
    assert created_ev["logged_at"] == frozen

    # No per-call override exists any more: record_experiment's signature has
    # no way to pass a clock/logged_at value at all (already re-asserted by
    # the signature-introspection test above).


def test_R21_1_3_default_clock_is_the_real_wall_clock(tmp_path):
    reg = ExperimentRegistry(tmp_path / "registry")
    before = pd.Timestamp.now(tz="UTC")
    fe = reg.record_experiment(**record_kwargs(created_at=before))
    after = pd.Timestamp.now(tz="UTC")
    events = reg._read_history_lines()
    created_ev = [e for e in events if e["experiment_id"] == fe.record.experiment_id][0]
    assert before <= created_ev["logged_at"] <= after


# ===========================================================================
# R§21.2 — rendering defects
# ===========================================================================


def test_R21_2_1_summary_renders_folded_notes_not_immutable_record_notes(registry):
    """R§21.2.1 (blocking, production defect) -- `render_summary` MUST read
    `fe.notes` (the FOLDED view, which includes every `annotate()` note),
    not `r.notes` (the immutable creation-time snapshot alone). Measured
    defect: after `annotate(id, note='LATER ANNOTATION...')`, the correction
    was present in the folded view but ABSENT from `summary()`."""
    fe = registry.record_experiment(**record_kwargs(notes="original note"))
    registry.annotate(fe.record.experiment_id, note="LATER ANNOTATION: this result was wrong")
    folded = registry.load_experiment(fe.record.experiment_id)
    assert "LATER ANNOTATION" in folded.notes  # self-guard: the fold really has it
    s = registry.summary(fe.record.experiment_id)
    assert "LATER ANNOTATION: this result was wrong" in s
    assert "original note" in s


def test_R21_2_2_manual_results_justification_is_rendered(registry):
    """R§21.2.2 (blocking, production defect) -- `manual_results_justification`
    is recorded AND rendered (R§20.3.2's own wording), immediately beneath
    the manual-path warning line. Measured v1.2 defect: it was recorded and
    validated, but passed to `_assemble_warnings` as a dead parameter and
    printed nowhere."""
    fe = registry.record_experiment(
        **record_kwargs(manual_results_justification="a very specific hand-verification justification")
    )
    s = registry.summary(fe.record.experiment_id)
    lines = s.splitlines()
    warn_idx = lines.index("WARNING: provenance/metrics NOT cross-checked against a BacktestResult")
    assert lines[warn_idx + 1] == "manual_results_justification: a very specific hand-verification justification"


def test_R21_2_2_adapter_recorded_summary_has_no_justification_line(registry):
    """Self-guard: an adapter-recorded (non-manual) record has no
    `manual_results_justification` and MUST NOT render the line at all."""
    from experiments.qr_smoke_001 import pipeline as qr_pipeline
    from data.provenance import PROCESSING_VERSION
    from registry.models import SCHEMA_VERSION

    run = qr_pipeline.run_window_a()
    code_identity = mk_code_identity(
        contract_versions={
            "backtest_contract": "1.5.1", "data_contract": "1.4",
            "registry_schema": SCHEMA_VERSION, "data_processing_version": PROCESSING_VERSION,
        }
    )
    dataset_windows = {
        "hyperliquid.ohlcv.1h.BTC": {
            "data_start": run.raw_index[0], "data_end": run.raw_index[-1],
            "eval_start": run.frame_index[0], "eval_end": run.frame_index[-1],
            "symbols": ("BTC",), "content_hash": "a" * 64,
        },
        "hyperliquid.funding.BTC": {
            "data_start": pd.Timestamp("2023-05-01", tz="UTC"), "data_end": pd.Timestamp("2026-08-20", tz="UTC"),
            "eval_start": None, "eval_end": None, "symbols": ("BTC",), "content_hash": "b" * 64,
        },
    }
    fe = record_backtest_result(
        registry, run.result, strategy=mk_strategy_ref(), dataset_windows=dataset_windows,
        universe_policy="single_symbol_fixed:BTC", code_identity=code_identity,
        experiment_type="pipeline_validation", research_stage="exploratory",
        reason_for_run="R21.2.2 adapter self-guard", created_at=pd.Timestamp("2026-08-17", tz="UTC"),
        n_configs_evaluated=1,
    )
    s = registry.summary(fe.record.experiment_id)
    assert "manual_results_justification:" not in s


# ===========================================================================
# R§21.5 — per-field identity protection
# ===========================================================================

_R21_5_BASE_DATASET_FIELDS = dict(
    dataset_id="hyperliquid.ohlcv.1h.BTC",
    source_venue="Hyperliquid",
    field_type="ohlcv",
    native_or_proxy="proxy",
    proxy_for="Hyperliquid",
    processing_version="qr-data-001-v1.3",
    dataset_version="v1",
    data_start=pd.Timestamp("2026-01-20 21:00", tz="UTC"),
    data_end=pd.Timestamp("2026-07-31 23:00", tz="UTC"),
    eval_start=pd.Timestamp("2026-01-25", tz="UTC"),
    eval_end=pd.Timestamp("2026-07-31 23:00", tz="UTC"),
    symbols=("BTC",),
    content_hash="c" * 64,
    content_hash_method="col-buffer-v2",
)

# (field, changed value, expected config_family_hash change)
# R§21.5.2 -- content_hash/content_hash_method/data_start/data_end/
# eval_start/eval_end MUST NOT change config_family_hash (R§20.5.4's 8-field
# strip list, R§21.9); every other hashed field MUST.
_R21_5_FIELD_CASES = [
    ("dataset_id", "hyperliquid.ohlcv.1h.ETH", True),
    ("source_venue", "Binance", True),
    ("field_type", "funding_rate", True),
    ("native_or_proxy", "native", True),
    ("proxy_for", "Binance", True),
    ("processing_version", "qr-data-001-v1.2", True),
    ("dataset_version", "v2", True),
    ("data_start", pd.Timestamp("2026-01-21 21:00", tz="UTC"), False),
    ("data_end", pd.Timestamp("2026-08-01 23:00", tz="UTC"), False),
    ("eval_start", pd.Timestamp("2026-01-26", tz="UTC"), False),
    ("eval_end", pd.Timestamp("2026-07-30 23:00", tz="UTC"), False),
    ("symbols", ("ETH",), True),
    ("content_hash", "d" * 64, False),
    ("content_hash_method", "col-buffer-v1", False),
]


def _r21_5_mk_ds(**overrides):
    fields = dict(_R21_5_BASE_DATASET_FIELDS)
    fields.update(overrides)
    return mk_dataset_ref(
        symbol_mapping=None,
        retrieval_date=None,
        dataset_span_start=pd.Timestamp("2026-01-01", tz="UTC"),
        dataset_span_end=pd.Timestamp("2026-08-01", tz="UTC"),
        provenance_notes="R21.5 fixture",
        **fields,
    )


@pytest.mark.parametrize(
    "field,changed_value,family_changes",
    _R21_5_FIELD_CASES,
    ids=[c[0] for c in _R21_5_FIELD_CASES],
)
def test_R21_5_per_field_identity_protection(tmp_path, field, changed_value, family_changes):
    """R§21.5.1/R§21.5.2 (blocking) -- ONE test per hashed `DatasetRef`
    field (`semantic_dict`, models.py). Each parametrized case varies EXACTLY
    that field (self-guard below) and asserts: `semantic_hash`/`exact_hash`
    always change, and `config_family_hash` changes iff the field is not one
    of the 6 near-duplicate-exempt window/content fields. Measured v1.2
    defect: every one of these 14 fields was individually deletable from
    `semantic_dict()` with all 245 tests green -- the only covering test
    varied two fields (`content_hash` + `eval_start`) together, so it
    detected the conjunction, not either field alone."""
    base_ds = _r21_5_mk_ds()
    changed_ds = _r21_5_mk_ds(**{field: changed_value})

    base_dict = base_ds.semantic_dict()
    changed_dict = changed_ds.semantic_dict()
    diffs = [k for k in base_dict if base_dict[k] != changed_dict[k]]
    assert diffs == [field], f"fixture varies {diffs}, not exactly [{field!r}] -- not a valid R§21.5.1 case"

    reg_a = ExperimentRegistry(tmp_path / f"a_{field}")
    reg_b = ExperimentRegistry(tmp_path / f"b_{field}")
    fe_a = reg_a.record_experiment(**record_kwargs(datasets=(base_ds,)))
    fe_b = reg_b.record_experiment(**record_kwargs(datasets=(changed_ds,)))

    assert fe_a.record.semantic_hash != fe_b.record.semantic_hash, field
    assert fe_a.record.exact_hash != fe_b.record.exact_hash, field
    if family_changes:
        assert fe_a.record.config_family_hash != fe_b.record.config_family_hash, field
    else:
        assert fe_a.record.config_family_hash == fe_b.record.config_family_hash, field


def test_R21_5_3_A29_semantic_payload_dataset_ordering_is_invariant(tmp_path):
    """R§21.5.3/A29 (blocking) -- datasets MUST be sorted before hashing, so
    supplying the SAME two datasets in a different insertion order produces
    an IDENTICAL semantic/exact/config_family hash. Measured defect: the
    mutant (dropping the `sorted(...)` in the semantic-payload assembly)
    produced TWO DIFFERENT hashes for the same two datasets in reversed
    order -- silently losing duplicate detection for any multi-dataset
    record."""
    ds1 = mk_dataset_ref(dataset_id="hyperliquid.ohlcv.1h.BTC", symbols=("BTC",))
    ds2 = mk_dataset_ref(
        dataset_id="hyperliquid.ohlcv.1h.ETH", symbols=("ETH",),
        content_hash="e" * 64,
    )

    reg_a = ExperimentRegistry(tmp_path / "order_a")
    reg_b = ExperimentRegistry(tmp_path / "order_b")
    fe_a = reg_a.record_experiment(**record_kwargs(datasets=(ds1, ds2)))
    fe_b = reg_b.record_experiment(**record_kwargs(datasets=(ds2, ds1)))  # REVERSED order

    assert fe_a.record.semantic_hash == fe_b.record.semantic_hash
    assert fe_a.record.exact_hash == fe_b.record.exact_hash
    assert fe_a.record.config_family_hash == fe_b.record.config_family_hash


# ===========================================================================
# R§21.6 — remaining known survivors
# ===========================================================================


def test_R21_6_A11_survivorship_safe_caller_result_mismatch_rejected(registry):
    """A11 (backtest_adapter.py:188) -- a caller-supplied `survivorship_safe`
    that disagrees with `result.survivorship_safe` MUST raise, never be
    silently accepted."""
    from experiments.qr_smoke_001 import pipeline as qr_pipeline
    from data.provenance import PROCESSING_VERSION
    from registry.models import SCHEMA_VERSION

    run = qr_pipeline.run_window_a()
    code_identity = mk_code_identity(
        contract_versions={
            "backtest_contract": "1.5.1", "data_contract": "1.4",
            "registry_schema": SCHEMA_VERSION, "data_processing_version": PROCESSING_VERSION,
        }
    )
    dataset_windows = {
        "hyperliquid.ohlcv.1h.BTC": {
            "data_start": run.raw_index[0], "data_end": run.raw_index[-1],
            "eval_start": run.frame_index[0], "eval_end": run.frame_index[-1],
            "symbols": ("BTC",), "content_hash": "a" * 64,
        },
        "hyperliquid.funding.BTC": {
            "data_start": pd.Timestamp("2023-05-01", tz="UTC"), "data_end": pd.Timestamp("2026-08-20", tz="UTC"),
            "eval_start": None, "eval_end": None, "symbols": ("BTC",), "content_hash": "b" * 64,
        },
    }
    disagreeing_value = not run.result.survivorship_safe
    with pytest.raises(ValidationError):
        record_backtest_result(
            registry, run.result, strategy=mk_strategy_ref(), dataset_windows=dataset_windows,
            universe_policy="single_symbol_fixed:BTC", code_identity=code_identity,
            experiment_type="pipeline_validation", research_stage="exploratory",
            reason_for_run="A11 survivorship_safe mismatch", created_at=pd.Timestamp("2026-08-17", tz="UTC"),
            survivorship_safe=disagreeing_value,
            n_configs_evaluated=1,
        )


def test_R21_6_A14_contract_versions_wrong_key_set_rejected():
    """A14 (models.py:170) -- `CodeIdentity.contract_versions` MUST have
    EXACTLY the 4 required keys; an extra or a missing key MUST raise, not
    merely a wrong VALUE for a present key (already covered by
    `test_registry_schema_mismatch_raises`)."""
    from registry.models import CodeIdentity

    base = dict(
        git_commit="a" * 40, git_available=True, dirty_worktree=False, dirty_summary={},
        untracked_code_files=0, code_fingerprint="b" * 64, code_fingerprint_n_files=5,
        code_scope_patterns=("src/**/*.py",),
    )
    # missing key
    with pytest.raises(ValidationError):
        CodeIdentity(
            **base,
            contract_versions={
                "backtest_contract": "1.5.1", "data_contract": "1.4",
                "registry_schema": "qr-infra-002-v1.3",
                # data_processing_version MISSING
            },
        )
    # extra key
    with pytest.raises(ValidationError):
        CodeIdentity(
            **base,
            contract_versions={
                "backtest_contract": "1.5.1", "data_contract": "1.4",
                "registry_schema": "qr-infra-002-v1.3", "data_processing_version": "qr-data-001-v1.3",
                "extra_bogus_key": "x",
            },
        )


def test_R21_6_A19_dirty_worktree_record_level_warning(registry):
    """A19 (store.py:380) -- `DIRTY_WORKTREE` record-level warning MUST be
    present when `code_identity.dirty_worktree` is True. Measured: v1.2 had
    no test asserting the actual TOKEN in `fe.warnings` (only the rendered
    'DIRTY' string, which a separate summary-only mutation would not
    protect)."""
    fe = registry.record_experiment(
        **record_kwargs(code_identity=mk_code_identity(dirty_worktree=True, dirty_summary={"M": 1}))
    )
    assert "DIRTY_WORKTREE" in fe.warnings


def test_R21_6_A20a_missing_artifact_present_in_the_ON_DISK_record(registry, tmp_path):
    """A20a (store.py:396) -- `_assemble_warnings` MUST itself emit
    `MISSING_ARTIFACT:<name>` into the record's OWN persisted `warnings`
    field at creation time. `_fold()` ALSO independently recomputes this
    token from `record.artifacts` on every read, which MASKS a removal of
    the `_assemble_warnings` code path in every folded API return (`summary()`,
    `load_experiment(...).warnings`, ...) -- so this MUST be asserted
    against the raw, unfolded, ON-DISK record, never through the folded
    view."""
    from registry.models import ArtifactRef

    missing_ref = ArtifactRef.from_file(
        tmp_path, "gone.json", name="missing_one", kind="other",
        recorded_at=pd.Timestamp("2026-08-17", tz="UTC"), allow_missing=True,
    )
    fe = registry.record_experiment(**record_kwargs(artifacts=(missing_ref,)))
    on_disk_record = registry._read_record(fe.record.experiment_id)
    assert any(w.startswith("MISSING_ARTIFACT:missing_one") for w in on_disk_record.warnings), (
        "MISSING_ARTIFACT must be emitted into the record's OWN warnings at creation time "
        "(_assemble_warnings), not rely solely on the fold to recompute it"
    )


def test_R21_6_A28_record_level_warnings_sorted_cross_process(tmp_path):
    """A28 (store.py, `_assemble_warnings` return) -- record-level warnings
    MUST be `tuple(sorted(...))`, not `tuple(set(...))`. A single-process
    fixture cannot discriminate a REMOVED `sorted()`: a Python `set`'s
    iteration order is a function of the CURRENT PROCESS's hash seed, not of
    insertion order, so within one process two differently-inserted sets of
    the same tokens iterate IDENTICALLY regardless of whether `sorted()` ran.
    Only a genuine CROSS-PROCESS comparison, with `PYTHONHASHSEED` forced to
    differ, can catch this: an un-sorted `tuple(set(...))` may iterate
    differently across two subprocesses for the IDENTICAL set of warning
    tokens, silently breaking R§10.3's on-disk byte-identity guarantee for
    two otherwise-identical records.

    MEASURED (R§21.11 mutation run): `store.py`'s own
    `return tuple(sorted(warnings))` is currently REDUNDANTLY guarded by an
    independent `ExperimentRecord.__post_init__` re-sort
    (`object.__setattr__(self, "warnings", tuple(sorted(self.warnings)))`,
    models.py). Removing EITHER sort ALONE is VACUOUS (the other one still
    enforces sortedness end to end, confirmed empirically across 12
    `PYTHONHASHSEED` values with zero divergence for each single-location
    mutation); removing BOTH simultaneously reliably diverges (12/12 distinct
    orderings observed). This test therefore protects the COMBINED
    end-to-end guarantee -- which is what R§10.3 actually requires -- rather
    than either individual line in isolation; the double-guard is a
    (harmless) belt-and-suspenders finding, not a defect, and is recorded
    here rather than silently discovered by a future reader."""
    root_a = tmp_path / "reg_a"
    root_b = tmp_path / "reg_b"
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(SRC_DIR)!r})\n"
        f"sys.path.insert(0, {str(TESTS_REGISTRY_DIR)!r})\n"
        "import pandas as pd\n"
        "from registry.store import ExperimentRegistry\n"
        "from _factories import record_kwargs, mk_code_identity, mk_dataset_ref\n"
        "reg = ExperimentRegistry(sys.argv[1])\n"
        "kw = record_kwargs(\n"
        "    code_identity=mk_code_identity(dirty_worktree=True, dirty_summary={'M': 1}, "
        "untracked_code_files=2, git_available=False, git_commit=None),\n"
        "    datasets=(mk_dataset_ref(native_or_proxy='proxy', proxy_for='Hyperliquid', "
        "content_hash=None, content_hash_method=None),),\n"
        "    survivorship_safe=None,\n"
        "    created_at=pd.Timestamp('2024-01-01', tz='UTC'),\n"
        ")\n"
        "fe = reg.record_experiment(**kw)\n"
        "sys.stdout.write(fe.record.experiment_id)\n"
    )
    ids = {}
    for root, seed in ((root_a, "0"), (root_b, "3937174127")):
        import os

        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        out = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            capture_output=True, text=True, env=env, check=True,
        )
        ids[root] = out.stdout.strip()

    reg_a = ExperimentRegistry(root_a)
    reg_b = ExperimentRegistry(root_b)
    rec_a = reg_a._read_record(ids[root_a])
    rec_b = reg_b._read_record(ids[root_b])
    assert len(rec_a.warnings) >= 5  # self-guard: enough tokens for ordering to matter
    assert rec_a.warnings == rec_b.warnings, (
        "the on-disk `warnings` tuple must be BYTE-IDENTICAL across two processes with "
        "different PYTHONHASHSEED for the identical set of warning tokens (R§10.3)"
    )


def test_R21_6_A38_prefix_collision_detected_by_verify_registry(registry):
    """A38 (store.py, PREFIX_COLLISION finding) -- `verify_registry()` MUST
    report `PREFIX_COLLISION` when two ON-DISK records' OWN `exact_hash`
    fields share their 16-hex PREFIX but are otherwise DIFFERENT full
    hashes. `by_prefix` is keyed purely off each record's `exact_hash` field
    (never off the experiment_id filename), so this is manufactured by
    directly editing a second record's `exact_hash` field to share record
    A's prefix -- the live API itself refuses to create this state via
    normal creation (R§5.3.2 raises at write time), mirroring the existing
    PARENT_CYCLE/RUN_SEQ_GAP forged-JSON fixtures in
    `test_r20_8_6_coverage.py`."""
    import json as _json

    a = registry.record_experiment(**record_kwargs())
    b = registry.record_experiment(
        **record_kwargs(
            reason_for_run="unrelated, different exact_hash",
            code_identity=mk_code_identity(code_fingerprint="7" * 64),
        )
    )
    prefix_a = a.record.exact_hash[:16]
    assert not b.record.exact_hash.startswith(prefix_a)  # self-guard: genuinely different prefixes

    forged_exact_hash = prefix_a + "f" * (len(b.record.exact_hash) - len(prefix_a))
    assert forged_exact_hash != a.record.exact_hash  # self-guard: a DIFFERENT full hash, same prefix

    path_b = registry.root / "records" / f"{b.record.experiment_id}.json"
    tree = _json.loads(path_b.read_text())
    tree["exact_hash"] = forged_exact_hash
    path_b.write_text(_json.dumps(tree, indent=2, sort_keys=True) + "\n")

    findings = registry.verify_registry()
    assert any(f.startswith("PREFIX_COLLISION:") for f in findings)


def test_R21_6_N15_near_duplicates_excludes_singleton_groups(registry):
    """N15 (store.py, `near_duplicates()`) -- a `config_family_hash` shared
    by only ONE record MUST NOT appear in `near_duplicates()`'s output (the
    group-size >= 2 contract: "have we tested this configuration before?"
    is meaningless for a group of one)."""
    fe = registry.record_experiment(**record_kwargs())
    dups = registry.near_duplicates()
    assert fe.record.config_family_hash not in dups


def test_R21_6_N17_OOS_overlap_checked_against_same_search_space_id_records_outside_lineage(tmp_path):
    """N17 (store.py, R§20.6.1) -- OOS_WINDOW_OVERLAP MUST fire against ANY
    record sharing (strategy_name, search_space_id), even one that is
    NEITHER an ancestor NOR the parent -- an explicitly blocking clause of
    R§20.6.1 that had no dedicated test (the existing
    `test_OOS_WINDOW_OVERLAP_checked_against_grandparent...` only exercises
    the ANCESTOR half of the same clause)."""
    from _factories import make_git_repo

    repo = tmp_path / "repo"
    make_git_repo(repo, files={"spec.md": "frozen spec v1"})
    reg = ExperimentRegistry(tmp_path / "registry", repo_root=repo)

    sibling_ds = mk_dataset_ref(
        eval_start=pd.Timestamp("2026-02-01", tz="UTC"), eval_end=pd.Timestamp("2026-02-15", tz="UTC")
    )
    sibling = reg.record_experiment(
        **record_kwargs(
            experiment_type="alpha_research", hypothesis_id="H-sib", search_space_id="ss-shared",
            datasets=(sibling_ds,),
        )
    )

    # An UNRELATED parent -- no ancestry connection to `sibling` whatsoever.
    unrelated_parent = reg.record_experiment(
        **record_kwargs(
            reason_for_run="unrelated parent, no shared ancestry with sibling",
            code_identity=mk_code_identity(code_fingerprint="6" * 64),
        )
    )
    child_ds = mk_dataset_ref(
        eval_start=pd.Timestamp("2026-02-05", tz="UTC"), eval_end=pd.Timestamp("2026-02-10", tz="UTC")
    )
    child = reg.record_experiment(
        **record_kwargs(
            experiment_type="alpha_research", hypothesis_id="H-child", search_space_id="ss-shared",
            research_stage="out_of_sample", frozen_spec_ref="spec.md",
            parent_experiment_id=unrelated_parent.record.experiment_id,
            change_from_parent="oos child, shares search_space_id with an unrelated sibling",
            datasets=(child_ds,),
            code_identity=mk_code_identity(code_fingerprint="9" * 64),
        )
    )
    assert f"OOS_WINDOW_OVERLAP:{sibling.record.experiment_id}" in child.warnings


def test_R21_6_N30_descendants_of_unsorted_traversal_order_discriminates(registry):
    """N30 (store.py, `descendants_of`) -- the v1.2 fixture (root with two
    DIRECT sibling children only) is non-discriminating because two direct
    children happen to already come out in sorted order from a stack-based
    LIFO traversal. A 2-LEVEL hierarchy (a child WITH its own grandchild,
    plus a second direct child) makes the natural traversal order and the
    sorted order differ with overwhelming probability, since experiment ids
    are hash-derived (effectively random) strings."""
    root = registry.record_experiment(**record_kwargs())
    child1 = registry.record_experiment(
        **record_kwargs(parent_experiment_id=root.record.experiment_id, change_from_parent="c1")
    )
    grandchild1 = registry.record_experiment(
        **record_kwargs(
            parent_experiment_id=child1.record.experiment_id, change_from_parent="gc1",
            code_identity=mk_code_identity(code_fingerprint="2" * 64),
        )
    )
    child2 = registry.record_experiment(
        **record_kwargs(
            parent_experiment_id=root.record.experiment_id, change_from_parent="c2",
            code_identity=mk_code_identity(code_fingerprint="3" * 64),
        )
    )
    grandchild2a = registry.record_experiment(
        **record_kwargs(
            parent_experiment_id=child2.record.experiment_id, change_from_parent="gc2a",
            code_identity=mk_code_identity(code_fingerprint="4" * 64),
        )
    )
    grandchild2b = registry.record_experiment(
        **record_kwargs(
            parent_experiment_id=child2.record.experiment_id, change_from_parent="gc2b",
            code_identity=mk_code_identity(code_fingerprint="5" * 64),
        )
    )
    desc = registry.descendants_of(root.record.experiment_id)
    ids = [d.record.experiment_id for d in desc]
    expected = {child1.record.experiment_id, grandchild1.record.experiment_id, child2.record.experiment_id,
                grandchild2a.record.experiment_id, grandchild2b.record.experiment_id}
    assert set(ids) == expected
    assert ids == sorted(ids)


def test_R21_6_N33_extra_warnings_allowlist_rejects_unknown_token(registry):
    """N33 (store.py, `_ALLOWED_EXTRA_WARNINGS`) -- an unrecognized token
    passed through the private `_extra_warnings` channel MUST raise
    `RegistryError`; this channel exists ONLY for
    `backtest_adapter.record_backtest_result` to pass `PROVENANCE_INCOMPLETE`
    through, and MUST NOT silently widen the warning vocabulary. Uses an
    otherwise-VALID record-warning token (`SURVIVORSHIP_UNKNOWN`, not
    `PROVENANCE_INCOMPLETE`) rather than a nonsense string: a nonsense
    string is independently caught by the closed-vocabulary check at the
    bottom of `_assemble_warnings` regardless of `_ALLOWED_EXTRA_WARNINGS`,
    so it would not discriminate a WIDENED allow-list -- only a real
    vocabulary token that is not `PROVENANCE_INCOMPLETE` does."""
    with pytest.raises(RegistryError):
        registry.record_experiment(**record_kwargs(), _extra_warnings=("BOGUS_TOKEN",))
    with pytest.raises(RegistryError):
        registry.record_experiment(**record_kwargs(), _extra_warnings=("SURVIVORSHIP_UNKNOWN",))


def test_R21_6_H1_search_space_id_filter_discriminates(registry):
    """H1 (store.py, `find_experiments(search_space_id=...)`) -- the filter
    MUST actually discriminate, not match every record."""
    a = registry.record_experiment(
        **record_kwargs(experiment_type="alpha_research", hypothesis_id="H1", search_space_id="ss-a")
    )
    b = registry.record_experiment(
        **record_kwargs(
            reason_for_run="b", experiment_type="alpha_research", hypothesis_id="H1", search_space_id="ss-b",
            code_identity=mk_code_identity(code_fingerprint="8" * 64),
        )
    )
    got = registry.find_experiments(search_space_id="ss-a")
    ids = {fe.record.experiment_id for fe in got}
    assert ids == {a.record.experiment_id}
    assert b.record.experiment_id not in ids


def test_R21_6_H2_config_family_hash_filter_discriminates(registry):
    """H2 (store.py, `find_experiments(config_family_hash=...)`) -- the
    filter MUST actually discriminate, not match every record."""
    a = registry.record_experiment(**record_kwargs())
    b = registry.record_experiment(
        **record_kwargs(reason_for_run="different config entirely", universe_policy="single_symbol_fixed:ETH")
    )
    assert a.record.config_family_hash != b.record.config_family_hash  # self-guard
    got = registry.find_experiments(config_family_hash=a.record.config_family_hash)
    ids = {fe.record.experiment_id for fe in got}
    assert ids == {a.record.experiment_id}
    assert b.record.experiment_id not in ids


# ===========================================================================
# R§21.7 — n_configs_evaluated: UNKNOWN vs a verified count
# ===========================================================================


def test_R21_7_omission_raises(registry):
    """R§21.7.1 (blocking) -- omitting `n_configs_evaluated` entirely MUST
    raise (a required keyword-only parameter with no default -> Python
    itself raises `TypeError` before the function body runs at all)."""
    kw = record_kwargs()
    kw.pop("n_configs_evaluated")
    with pytest.raises(TypeError):
        registry.record_experiment(**kw)


def test_R21_7_zero_and_negative_raise(registry):
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(n_configs_evaluated=0))
    with pytest.raises(ValidationError):
        registry.record_experiment(**record_kwargs(n_configs_evaluated=-1))


def test_R21_7_none_is_accepted_as_UNKNOWN(registry):
    fe = registry.record_experiment(**record_kwargs(n_configs_evaluated=None))
    assert fe.record.n_configs_evaluated is None
    assert "N_CONFIGS_UNKNOWN" in fe.warnings


def test_R21_7_2_unknown_renders_always_verified_renders_only_when_gt_1(registry):
    """R§21.7.2 (blocking) -- `None` renders as `UNKNOWN` ALWAYS; a verified
    count renders as `<n> (verified)` only when > 1 (a verified `1`, the
    common/safe case, is intentionally NOT rendered -- matching R§20.11's
    existing >1 threshold -- so it can never be visually confused with
    UNKNOWN by a reader skimming for the absence of the line)."""
    fe_unknown = registry.record_experiment(**record_kwargs(n_configs_evaluated=None))
    s_unknown = registry.summary(fe_unknown.record.experiment_id)
    assert "n_configs_evaluated: UNKNOWN" in s_unknown

    fe_verified_1 = registry.record_experiment(
        **record_kwargs(reason_for_run="verified 1", n_configs_evaluated=1)
    )
    s_verified_1 = registry.summary(fe_verified_1.record.experiment_id)
    assert "n_configs_evaluated" not in s_verified_1

    fe_verified_7 = registry.record_experiment(
        **record_kwargs(
            reason_for_run="verified 7", experiment_type="alpha_research", hypothesis_id="H1",
            search_space_id="ss-x", n_configs_evaluated=7,
        )
    )
    s_verified_7 = registry.summary(fe_verified_7.record.experiment_id)
    assert "n_configs_evaluated: 7 (verified)" in s_verified_7


def test_R21_7_3_n_configs_unknown_filter(registry):
    known = registry.record_experiment(**record_kwargs(n_configs_evaluated=3))
    unknown = registry.record_experiment(
        **record_kwargs(reason_for_run="unknown n_configs", n_configs_evaluated=None)
    )
    got_unknown = registry.find_experiments(n_configs_unknown=True)
    assert {fe.record.experiment_id for fe in got_unknown} == {unknown.record.experiment_id}
    got_known = registry.find_experiments(n_configs_unknown=False)
    assert {fe.record.experiment_id for fe in got_known} == {known.record.experiment_id}


def test_R21_7_4_search_space_summary_reports_lower_bound_flag_not_silent_sum(registry):
    """R§21.7.4 (blocking) -- `search_space_summary` MUST NOT silently sum
    over `None` members; it reports the KNOWN total, the count of UNKNOWN
    members, and a `..._is_lower_bound` flag."""
    common = dict(experiment_type="alpha_research", hypothesis_id="H1", search_space_id="ss-mixed")
    registry.record_experiment(**record_kwargs(**common, n_configs_evaluated=5))
    registry.record_experiment(
        **record_kwargs(
            **common, reason_for_run="second", n_configs_evaluated=None,
            code_identity=mk_code_identity(code_fingerprint="1" * 64),
        )
    )
    summary = registry.search_space_summary("ss-mixed")
    assert summary["n_configs_evaluated_total"] == 5
    assert summary["n_records_with_unknown_n_configs"] == 1
    assert summary["n_configs_evaluated_total_is_lower_bound"] is True

    # All-known control case: the flag MUST be False.
    registry2_common = dict(experiment_type="alpha_research", hypothesis_id="H2", search_space_id="ss-all-known")
    registry.record_experiment(
        **record_kwargs(
            **registry2_common, reason_for_run="all known 1", n_configs_evaluated=2,
            code_identity=mk_code_identity(code_fingerprint="2" * 64),
        )
    )
    summary_known = registry.search_space_summary("ss-all-known")
    assert summary_known["n_configs_evaluated_total_is_lower_bound"] is False
    assert summary_known["n_records_with_unknown_n_configs"] == 0
