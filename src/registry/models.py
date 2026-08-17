"""R§4 — record schema dataclasses and their self-contained validation.

Every dataclass here is frozen (write-once in memory, matching the R§8.2
write-once-on-disk guarantee). `__post_init__` enforces exactly the R§14
invariants that are evaluable from the object's own fields alone; invariants
that need registry state (parent existence, exact-hash collisions, OOS
window overlap against a *different* record) live in `store.py`, which has
that state.

`src/registry` MAY import `backtest.models` and `data.provenance` only
(R§2.1) — imported below for `DatasetProvenance`/`UniverseProvenance` type
adaptation in `backtest_adapter.py`, not here.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Optional

import pandas as pd

from . import serialize

__all__ = [
    "ValidationError",
    "RegistryError",
    "RegistryIntegrityError",
    "SCHEMA_VERSION",
    "EXPERIMENT_TYPES",
    "STATUSES",
    "RESEARCH_STAGES",
    "FIELD_TYPES",
    "NATIVE_OR_PROXY_VALUES",
    "ARTIFACT_KINDS",
    "REPRODUCIBILITY_STATUSES",
    "CodeIdentity",
    "DatasetRef",
    "StrategyRef",
    "ResultSummary",
    "ArtifactRef",
    "ExperimentRecord",
    "RECORDED_VIA_VALUES",
    "RECORD_WARNING_PREFIXES",
    "RESULT_WARNING_PREFIXES",
    "config_family_payload",
]


class ValidationError(Exception):
    """R§4 — a REQUIRED/conditional field was missing, empty, or out of the
    pinned vocabulary at construction time."""


class RegistryError(Exception):
    """R§5/R§8/R§10 — a registry-state-level failure (write-once collision,
    prefix collision, run_seq overflow, unknown parent, ...)."""


class RegistryIntegrityError(Exception):
    """R§8.3/R§11.9 — the on-disk store itself is corrupt (malformed/
    truncated history line, modified record, ...). Never silently skipped."""


# R§4 — schema_version is a constant, bumped only with a written migration
# note (R§19 D8); an unknown value on read MUST raise, never be guessed at.
# R§20 (v1.2) bumps this: new REQUIRED fields (recorded_via, search_space_id,
# n_configs_evaluated, config_family_hash, ...) mean a v1.1 record is never
# silently reinterpreted as v1.2 — R11's readers raise SCHEMA_VERSION_UNKNOWN.
SCHEMA_VERSION = "qr-infra-002-v1.3"

# R§4.1.1
EXPERIMENT_TYPES = frozenset(
    {"pipeline_validation", "infrastructure", "data_audit", "alpha_research", "robustness", "replication"}
)
# R§8.1
STATUSES = frozenset({"COMPLETED", "FAILED", "REJECTED", "INVALID"})
# R§4.2
RESEARCH_STAGES = frozenset({"exploratory", "in_sample", "robustness", "validation", "out_of_sample"})
# R§4.4.1 (BD3) — pinned to the data layer's REAL field_type vocabulary, not
# v1.0's invented `ohlcv_1h`/`funding`.
FIELD_TYPES = frozenset({"ohlcv", "funding_rate", "asset_ctx", "universe", "other"})
NATIVE_OR_PROXY_VALUES = frozenset({"native", "proxy"})
# R§9
ARTIFACT_KINDS = frozenset(
    {"equity_curve", "weights", "trades", "metrics", "log", "notes", "report", "dataset_snapshot", "other"}
)
# R§4.11
REPRODUCIBILITY_STATUSES = frozenset({"UNIQUE", "REPRODUCED", "DIVERGED", "NOT_COMPARABLE"})

# R§20.3.1
RECORDED_VIA_VALUES = frozenset({"adapter", "manual"})

# R§4.9 — closed warning-token vocabularies (bare prefixes; some tokens carry
# a `:<suffix>` that is not enumerated here since the suffix is data, not
# vocabulary). R§20 adds the sticky WAS_* tokens (R§20.4.1), the OOS-gate
# tokens (R§20.6.3/R§20.6.4), UNTRACKED_CODE_AT_RECORD_TIME (R§20.7.3),
# BACKDATED_CREATED_AT (R§20.7.1) and UNVERIFIED_MANUAL_RESULTS (R§20.3.2).
RECORD_WARNING_PREFIXES = frozenset(
    {
        "PROXY_DATA",
        "SURVIVORSHIP_UNKNOWN",
        "SURVIVORSHIP_UNSAFE",
        "PROVENANCE_INCOMPLETE",
        "DIRTY_WORKTREE",
        "GIT_UNAVAILABLE",
        "PROCESSING_VERSION_MISMATCH",
        "MISSING_ARTIFACT",
        "CONTENT_HASH_UNAVAILABLE",
        "OOS_WINDOW_OVERLAP",
        "WAS_INVALIDATED",
        "WAS_REJECTED",
        "WAS_FAILED",
        "SPEC_CHANGED_SINCE_PARENT",
        "OOS_RELABEL_OF",
        "UNTRACKED_CODE_AT_RECORD_TIME",
        "BACKDATED_CREATED_AT",
        "UNVERIFIED_MANUAL_RESULTS",
        # R§21.7.3 (blocking) — `n_configs_evaluated is None` (UNKNOWN).
        "N_CONFIGS_UNKNOWN",
    }
)
RESULT_WARNING_PREFIXES = frozenset(
    {
        "RUINED",
        "LEVERAGE_BREACH",
        "FUNDING_GAP_SUSPICIOUS",
        "FUNDING_NOT_MODELLED",
        "COUNTERFACTUAL_",
        "UNEXECUTED_REBALANCES",
        "DRAG_NOT_COMPARABLE",
        "CAGR_SUPPRESSED",
    }
)


def _require_nonempty_str(value, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} is REQUIRED and non-empty")


def _ts_or_none(v):
    return v


# ---------------------------------------------------------------------------
# R§6 CodeIdentity
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CodeIdentity:
    git_commit: Optional[str]
    git_available: bool
    dirty_worktree: bool
    dirty_summary: dict
    untracked_code_files: int
    code_fingerprint: str
    code_fingerprint_n_files: int
    code_scope_patterns: tuple
    contract_versions: dict

    def __post_init__(self) -> None:
        if self.git_commit is not None and (len(self.git_commit) != 40 or not all(c in "0123456789abcdef" for c in self.git_commit)):
            raise ValidationError(f"CodeIdentity.git_commit must be 40-hex or None, got {self.git_commit!r}")
        if not self.code_fingerprint or len(self.code_fingerprint) != 64:
            raise ValidationError("CodeIdentity.code_fingerprint is REQUIRED, 64-hex, never None (R§4.3)")
        if self.code_fingerprint_n_files <= 0:
            # R§6.2 — a zero-file fingerprint is the constant hash of `[]`,
            # which would make every experiment's code identity identical.
            raise RegistryError("CodeIdentity.code_fingerprint_n_files MUST be > 0 (R§6.2)")
        required_keys = {"backtest_contract", "data_contract", "registry_schema", "data_processing_version"}
        if set(self.contract_versions.keys()) != required_keys:
            raise ValidationError(f"CodeIdentity.contract_versions MUST have exactly keys {sorted(required_keys)} (R§4.3.1)")
        if self.contract_versions["registry_schema"] != SCHEMA_VERSION:
            raise ValidationError(
                "CodeIdentity.contract_versions['registry_schema'] MUST equal the record schema_version (R§4.3.1)"
            )

    def to_dict(self) -> dict:
        return {
            "git_commit": self.git_commit,
            "git_available": self.git_available,
            "dirty_worktree": self.dirty_worktree,
            "dirty_summary": dict(self.dirty_summary),
            "untracked_code_files": self.untracked_code_files,
            "code_fingerprint": self.code_fingerprint,
            "code_fingerprint_n_files": self.code_fingerprint_n_files,
            "code_scope_patterns": list(self.code_scope_patterns),
            "contract_versions": dict(self.contract_versions),
        }

    @staticmethod
    def from_dict(d: dict) -> "CodeIdentity":
        return CodeIdentity(
            git_commit=d["git_commit"],
            git_available=d["git_available"],
            dirty_worktree=d["dirty_worktree"],
            dirty_summary=dict(d["dirty_summary"]),
            untracked_code_files=d["untracked_code_files"],
            code_fingerprint=d["code_fingerprint"],
            code_fingerprint_n_files=d["code_fingerprint_n_files"],
            code_scope_patterns=tuple(d["code_scope_patterns"]),
            contract_versions=dict(d["contract_versions"]),
        )


# ---------------------------------------------------------------------------
# R§4.4 DatasetRef
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DatasetRef:
    dataset_id: str
    source_venue: str
    field_type: str
    native_or_proxy: str
    proxy_for: Optional[str]
    processing_version: str
    dataset_version: Optional[str]
    retrieval_date: Optional[_dt.date]
    dataset_span_start: Optional[pd.Timestamp]
    dataset_span_end: Optional[pd.Timestamp]
    data_start: pd.Timestamp
    data_end: pd.Timestamp
    eval_start: Optional[pd.Timestamp]
    eval_end: Optional[pd.Timestamp]
    symbols: tuple
    symbol_mapping: Optional[str]
    content_hash: Optional[str]
    content_hash_method: Optional[str]
    provenance_notes: Optional[str]

    def __post_init__(self) -> None:
        _require_nonempty_str(self.dataset_id, "DatasetRef.dataset_id")
        _require_nonempty_str(self.source_venue, f"DatasetRef.source_venue (dataset_id={self.dataset_id!r})")
        if self.field_type not in FIELD_TYPES:
            raise ValidationError(
                f"DatasetRef.field_type must be one of {sorted(FIELD_TYPES)} (R§4.4.1), got {self.field_type!r} "
                f"for dataset_id={self.dataset_id!r}"
            )
        if self.native_or_proxy not in NATIVE_OR_PROXY_VALUES:
            # R§7.2 — no silent default. `native_or_proxy` has no default
            # value in this dataclass at all; an absent/garbage value raises
            # here rather than being coerced to "native".
            raise ValidationError(
                f"DatasetRef.native_or_proxy must be 'native' or 'proxy' (R§4.4/R§7.2), got "
                f"{self.native_or_proxy!r} for dataset_id={self.dataset_id!r}"
            )
        if self.native_or_proxy == "proxy" and not (self.proxy_for and self.proxy_for.strip()):
            raise ValidationError(
                f"DatasetRef.proxy_for is REQUIRED non-empty when native_or_proxy=='proxy' (R§14.11) "
                f"for dataset_id={self.dataset_id!r}"
            )
        _require_nonempty_str(self.processing_version, f"DatasetRef.processing_version (dataset_id={self.dataset_id!r})")
        if not self.symbols:
            raise ValidationError(f"DatasetRef.symbols MUST be non-empty for dataset_id={self.dataset_id!r} (R§4.4)")
        if self.content_hash is not None and not self.content_hash_method:
            raise ValidationError(
                f"DatasetRef.content_hash_method is REQUIRED when content_hash is set (R§4.4) "
                f"for dataset_id={self.dataset_id!r}"
            )
        if self.data_end < self.data_start:
            raise ValidationError(f"DatasetRef.data_end must be >= data_start (R§14.12) for dataset_id={self.dataset_id!r}")
        if self.field_type == "ohlcv":
            if self.eval_start is None or self.eval_end is None:
                raise ValidationError(
                    f"DatasetRef.eval_start/eval_end are REQUIRED when field_type=='ohlcv' (R§4.4) "
                    f"for dataset_id={self.dataset_id!r}"
                )
        if self.eval_start is not None and self.eval_end is not None:
            if self.eval_end < self.eval_start:
                raise ValidationError(f"DatasetRef.eval_end must be >= eval_start (R§14.12) for dataset_id={self.dataset_id!r}")
            if self.data_start > self.eval_start:
                raise ValidationError(f"DatasetRef.data_start must be <= eval_start (R§14.12) for dataset_id={self.dataset_id!r}")
            if self.eval_end > self.data_end:
                raise ValidationError(f"DatasetRef.eval_end must be <= data_end (R§14.12) for dataset_id={self.dataset_id!r}")
        # R§4.4 — sorted on store.
        object.__setattr__(self, "symbols", tuple(sorted(self.symbols)))

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "source_venue": self.source_venue,
            "field_type": self.field_type,
            "native_or_proxy": self.native_or_proxy,
            "proxy_for": self.proxy_for,
            "processing_version": self.processing_version,
            "dataset_version": self.dataset_version,
            "retrieval_date": self.retrieval_date,
            "dataset_span_start": self.dataset_span_start,
            "dataset_span_end": self.dataset_span_end,
            "data_start": self.data_start,
            "data_end": self.data_end,
            "eval_start": self.eval_start,
            "eval_end": self.eval_end,
            "symbols": list(self.symbols),
            "symbol_mapping": self.symbol_mapping,
            "content_hash": self.content_hash,
            "content_hash_method": self.content_hash_method,
            "provenance_notes": self.provenance_notes,
        }

    # R§5.1 — the subset of fields that participate in `semantic_hash`.
    def semantic_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "source_venue": self.source_venue,
            "field_type": self.field_type,
            "native_or_proxy": self.native_or_proxy,
            "proxy_for": self.proxy_for,
            "processing_version": self.processing_version,
            "dataset_version": self.dataset_version,
            "data_start": self.data_start,
            "data_end": self.data_end,
            "eval_start": self.eval_start,
            "eval_end": self.eval_end,
            "symbols": list(self.symbols),
            "content_hash": self.content_hash,
            "content_hash_method": self.content_hash_method,
        }

    @staticmethod
    def from_dict(d: dict) -> "DatasetRef":
        return DatasetRef(
            dataset_id=d["dataset_id"],
            source_venue=d["source_venue"],
            field_type=d["field_type"],
            native_or_proxy=d["native_or_proxy"],
            proxy_for=d.get("proxy_for"),
            processing_version=d["processing_version"],
            dataset_version=d.get("dataset_version"),
            retrieval_date=d.get("retrieval_date"),
            dataset_span_start=d.get("dataset_span_start"),
            dataset_span_end=d.get("dataset_span_end"),
            data_start=d["data_start"],
            data_end=d["data_end"],
            eval_start=d.get("eval_start"),
            eval_end=d.get("eval_end"),
            symbols=tuple(d["symbols"]),
            symbol_mapping=d.get("symbol_mapping"),
            content_hash=d.get("content_hash"),
            content_hash_method=d.get("content_hash_method"),
            provenance_notes=d.get("provenance_notes"),
        )


# ---------------------------------------------------------------------------
# R§4.5 StrategyRef
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StrategyRef:
    name: str
    version: str
    params: dict
    frequency: str
    target_execution_venue: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "StrategyRef.name")
        _require_nonempty_str(self.version, "StrategyRef.version")
        _require_nonempty_str(self.frequency, "StrategyRef.frequency")
        _require_nonempty_str(self.target_execution_venue, "StrategyRef.target_execution_venue")
        if not isinstance(self.params, dict):
            raise ValidationError("StrategyRef.params must be a dict (MAY be {})")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "params": dict(self.params),
            "frequency": self.frequency,
            "target_execution_venue": self.target_execution_venue,
        }

    @staticmethod
    def from_dict(d: dict) -> "StrategyRef":
        return StrategyRef(
            name=d["name"],
            version=d["version"],
            params=dict(d["params"]),
            frequency=d["frequency"],
            target_execution_venue=d["target_execution_venue"],
        )


# ---------------------------------------------------------------------------
# R§4.7 ResultSummary
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ResultSummary:
    metrics: dict
    n_periods: int
    rebalance_count: int
    ruined: bool
    custom: dict
    result_warnings: tuple

    def __post_init__(self) -> None:
        if self.n_periods < 1:
            raise ValidationError("ResultSummary.n_periods MUST be >= 1 (R§4.7/R§14.10)")
        for tok in self.result_warnings:
            if not any(tok == p or tok.startswith(p) for p in RESULT_WARNING_PREFIXES):
                raise ValidationError(f"result_warnings token {tok!r} is not in the closed vocabulary (R§4.9)")
        # R§20.11 (MW-k) — `cagr is None` MUST only ever mean "explicitly
        # suppressed", never an accidental/omitted value. Both halves of the
        # suppression contract are checked so the renderer's "n/a
        # (suppressed)" line is never asserting a suppression nobody declared.
        if self.metrics.get("cagr") is None and "cagr" in self.metrics:
            if "CAGR_SUPPRESSED" not in self.result_warnings:
                raise ValidationError(
                    "metrics['cagr'] is None but 'CAGR_SUPPRESSED' is not in result_warnings (R§20.11/R§4.7.1) "
                    "-- cagr may only be None via an explicit suppress_cagr=True"
                )
            if "cagr_raw_suppressed" not in self.metrics:
                raise ValidationError(
                    "metrics['cagr'] is None but metrics['cagr_raw_suppressed'] is absent (R§20.11/R§4.7.1) "
                    "-- the raw value MUST be preserved, never destroyed"
                )
        object.__setattr__(self, "result_warnings", tuple(sorted(self.result_warnings)))

    def to_dict(self) -> dict:
        return {
            "metrics": dict(self.metrics),
            "n_periods": self.n_periods,
            "rebalance_count": self.rebalance_count,
            "ruined": self.ruined,
            "custom": dict(self.custom),
            "result_warnings": list(self.result_warnings),
        }

    # R§5.5 — the exact operand of the reproducibility comparison.
    def comparison_dict(self) -> dict:
        return {
            "metrics": dict(self.metrics),
            "n_periods": self.n_periods,
            "rebalance_count": self.rebalance_count,
            "ruined": self.ruined,
            "custom": dict(self.custom),
            "result_warnings": list(self.result_warnings),
        }

    @staticmethod
    def from_dict(d: dict) -> "ResultSummary":
        return ResultSummary(
            metrics=dict(d["metrics"]),
            n_periods=d["n_periods"],
            rebalance_count=d["rebalance_count"],
            ruined=d["ruined"],
            custom=dict(d["custom"]),
            result_warnings=tuple(d["result_warnings"]),
        )


# ---------------------------------------------------------------------------
# R§9 ArtifactRef
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ArtifactRef:
    name: str
    kind: str
    path: str
    sha256: Optional[str]
    size_bytes: Optional[int]
    recorded_at: pd.Timestamp
    description: Optional[str]

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "ArtifactRef.name")
        if self.kind not in ARTIFACT_KINDS:
            raise ValidationError(f"ArtifactRef.kind must be one of {sorted(ARTIFACT_KINDS)} (R§9), got {self.kind!r}")
        _require_nonempty_str(self.path, "ArtifactRef.path")
        if self.path.startswith("/") or (len(self.path) > 1 and self.path[1] == ":"):
            raise ValidationError(
                f"ArtifactRef.path MUST be repo-root-relative, POSIX (R§9) — got absolute path {self.path!r}"
            )
        if "\\" in self.path:
            raise ValidationError(f"ArtifactRef.path MUST be POSIX (R§9) — got backslash in {self.path!r}")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "recorded_at": self.recorded_at,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d: dict) -> "ArtifactRef":
        return ArtifactRef(
            name=d["name"],
            kind=d["kind"],
            path=d["path"],
            sha256=d.get("sha256"),
            size_bytes=d.get("size_bytes"),
            recorded_at=d["recorded_at"],
            description=d.get("description"),
        )

    # -- R§20.8.1 (blocking) -------------------------------------------------

    @staticmethod
    def from_file(
        repo_root,
        relpath: str,
        *,
        name: str,
        kind: str,
        recorded_at: pd.Timestamp,
        description: Optional[str] = None,
        allow_missing: bool = False,
    ) -> "ArtifactRef":
        """R§9/R§20.8.1 — computes `sha256`/`size_bytes` FROM THE ACTUAL FILE
        at `repo_root/relpath`, so an `ArtifactRef` can never assert an
        arbitrary hash for an arbitrary path (the v1.1 defect: the R§18.1(10)
        tests validated a helper defined inside the test file, which never
        touched this constructor at all).

        `relpath` is repo-root-relative POSIX (R§9); `repo_root` anchors it.
        When the file is absent: `allow_missing=False` (default) raises
        `ValidationError`; `allow_missing=True` returns an `ArtifactRef` with
        `sha256=None`/`size_bytes=None` (the caller is responsible for the
        record-level `MISSING_ARTIFACT:<name>` warning this triggers, R§9).
        """
        import hashlib as _hashlib
        from pathlib import Path as _Path

        abs_path = _Path(repo_root) / relpath
        if not abs_path.is_file():
            if allow_missing:
                return ArtifactRef(
                    name=name,
                    kind=kind,
                    path=relpath,
                    sha256=None,
                    size_bytes=None,
                    recorded_at=recorded_at,
                    description=description,
                )
            raise ValidationError(
                f"ArtifactRef.from_file: {abs_path} does not exist (R§9) — pass allow_missing=True "
                "if this is expected"
            )
        data = abs_path.read_bytes()
        return ArtifactRef(
            name=name,
            kind=kind,
            path=relpath,
            sha256=_hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            recorded_at=recorded_at,
            description=description,
        )


# ---------------------------------------------------------------------------
# R§4 ExperimentRecord
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ExperimentRecord:
    schema_version: str
    experiment_id: str
    semantic_hash: str
    exact_hash: str
    run_seq: int
    created_at: pd.Timestamp
    run_executed_at: Optional[pd.Timestamp]
    status: str
    status_reason: Optional[str]
    experiment_type: str

    hypothesis_id: Optional[str]
    parent_experiment_id: Optional[str]
    reason_for_run: str
    change_from_parent: Optional[str]
    research_stage: str
    frozen_spec_ref: Optional[str]
    frozen_spec_sha256: Optional[str]
    frozen_spec_commit: Optional[str]
    frozen_spec_blob_sha: Optional[str]
    tags: tuple
    notes: Optional[str]

    code: CodeIdentity

    datasets: tuple
    universe_policy: str
    survivorship_safe: Optional[bool]
    uses_proxy_data: bool
    no_datasets_reason: Optional[str]

    strategy: StrategyRef
    backtest_config: dict

    recorded_via: str
    manual_results_justification: Optional[str]

    search_space_id: Optional[str]
    # R§21.7.1 (blocking) — tri-state: `int >= 1` (a VERIFIED count) or
    # `None` (UNKNOWN). Unhashed (R§21.7.5) — metadata about the search, not
    # about the configuration under test.
    n_configs_evaluated: Optional[int]
    config_family_hash: str

    results: Optional[ResultSummary]
    run_facts: dict

    warnings: tuple
    artifacts: tuple

    rerun_of: Optional[str]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise RegistryIntegrityError(
                f"unknown schema_version {self.schema_version!r} — a reader MUST raise, never guess (R§4/R§19 D8)"
            )
        if self.experiment_type not in EXPERIMENT_TYPES:
            raise ValidationError(f"experiment_type must be one of {sorted(EXPERIMENT_TYPES)} (R§4.1.1), got {self.experiment_type!r}")
        if self.status not in STATUSES:
            raise ValidationError(f"status must be one of {sorted(STATUSES)} (R§8.1), got {self.status!r}")
        if self.status != "COMPLETED" and not (self.status_reason and self.status_reason.strip()):
            raise ValidationError("status_reason is REQUIRED non-empty unless status == COMPLETED (R§4.1/R§14.9)")
        if self.research_stage not in RESEARCH_STAGES:
            raise ValidationError(f"research_stage must be one of {sorted(RESEARCH_STAGES)} (R§4.2), got {self.research_stage!r}")
        _require_nonempty_str(self.reason_for_run, "reason_for_run")
        if self.experiment_type == "alpha_research" and not (self.hypothesis_id and self.hypothesis_id.strip()):
            raise ValidationError("hypothesis_id is REQUIRED non-empty when experiment_type == 'alpha_research' (R§14.6)")
        if self.parent_experiment_id is not None and not (self.change_from_parent and self.change_from_parent.strip()):
            raise ValidationError("change_from_parent is REQUIRED non-empty when parent_experiment_id is set (R§14.3)")
        if self.research_stage == "out_of_sample":
            if not self.frozen_spec_ref:
                raise ValidationError("frozen_spec_ref is REQUIRED when research_stage == 'out_of_sample' (R§14.5)")
            if not self.parent_experiment_id:
                raise ValidationError("parent_experiment_id is REQUIRED when research_stage == 'out_of_sample' (R§14.5)")
            # R§20.6.2 — structural half of "frozen_spec_ref MUST resolve to a
            # git-committed blob": the ACTUAL git verification (tracked,
            # clean, HEAD-matching) happens in store.py (which has repo
            # access); this dataclass only enforces that the two identifying
            # fields it produced are present, never silently None.
            if not self.frozen_spec_commit or not self.frozen_spec_blob_sha:
                raise ValidationError(
                    "frozen_spec_commit/frozen_spec_blob_sha are REQUIRED when research_stage == "
                    "'out_of_sample' (R§20.6.2) — frozen_spec_ref MUST resolve to a git-committed blob"
                )

        # R§20.5.1 — search_space_id REQUIRED for the four "real research"
        # experiment types; optional (a grouping label with no comparison
        # sibling) for pipeline_validation/infrastructure/data_audit.
        if self.experiment_type in ("alpha_research", "robustness", "validation", "replication"):
            if not (self.search_space_id and self.search_space_id.strip()):
                raise ValidationError(
                    f"search_space_id is REQUIRED non-empty when experiment_type == {self.experiment_type!r} "
                    "(R§20.5.1)"
                )
        if self.n_configs_evaluated is not None and self.n_configs_evaluated < 1:
            raise ValidationError("n_configs_evaluated MUST be >= 1 or None (UNKNOWN) (R§20.5.2/R§21.7.1)")

        # R§20.3 — recorded_via provenance of the record itself.
        if self.recorded_via not in RECORDED_VIA_VALUES:
            raise ValidationError(
                f"recorded_via must be one of {sorted(RECORDED_VIA_VALUES)} (R§20.3.1), got {self.recorded_via!r}"
            )
        if self.recorded_via == "manual" and self.results is not None:
            if not (self.manual_results_justification and self.manual_results_justification.strip()):
                raise ValidationError(
                    "manual_results_justification is REQUIRED non-empty when recorded_via=='manual' and "
                    "results is not None (R§20.3.2)"
                )

        exempt = self.experiment_type in ("infrastructure", "data_audit")
        if not self.datasets:
            if not exempt:
                raise ValidationError(
                    "datasets MUST be non-empty except for experiment_type in "
                    "{'infrastructure','data_audit'} (R§4.4.3/R§14.13)"
                )
            if not (self.no_datasets_reason and self.no_datasets_reason.strip()):
                raise ValidationError("no_datasets_reason is REQUIRED non-empty when datasets is empty (R§4.4.3)")
        if not self.datasets and not self.backtest_config and not exempt:
            raise ValidationError("backtest_config MAY only be empty for experiment_type in {'infrastructure','data_audit'} (R§4.4.3)")

        derived_proxy = any(d.native_or_proxy == "proxy" for d in self.datasets)
        if self.uses_proxy_data != derived_proxy:
            raise ValidationError(
                f"uses_proxy_data ({self.uses_proxy_data}) does not match derived value from datasets "
                f"({derived_proxy}) — this field MUST be derived, never independently supplied (R§4.4/R§14.7)"
            )

        # R§4.6.2 (BD4) — funding-basis coherence, checked against the
        # CALLER's config dict (backtest_config), never the engine's
        # always-"not_modelled" result field.
        if self.backtest_config.get("funding_mode") == "disabled":
            basis = self.backtest_config.get("funding_notional_basis")
            if basis not in (None, "not_modelled"):
                raise ValidationError(
                    f"backtest_config['funding_notional_basis'] must be None or 'not_modelled' when "
                    f"funding_mode=='disabled' (R§4.6.2), got {basis!r}"
                )

        if self.results is not None and self.results.n_periods < 1:
            raise ValidationError("results.n_periods MUST be >= 1 (R§14.10)")

        for tok in self.warnings:
            base = tok.split(":", 1)[0]
            if base not in RECORD_WARNING_PREFIXES:
                raise ValidationError(f"record-level warning token {tok!r} is not in the closed vocabulary (R§4.9)")

        object.__setattr__(self, "tags", tuple(sorted(self.tags)))
        object.__setattr__(self, "warnings", tuple(sorted(self.warnings)))
        object.__setattr__(self, "run_seq", int(self.run_seq))
        if self.run_seq == 0 and self.rerun_of is not None:
            raise ValidationError("rerun_of MUST be None iff run_seq == 0 (R§4.11)")
        if self.run_seq != 0 and self.rerun_of is None:
            raise ValidationError("rerun_of MUST be set when run_seq != 0 (R§4.11)")

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict:
        """The exact on-disk (and hashed-superset) tree. `reproducibility_status`
        and `divergence_detail` are deliberately absent — R§4.11 pins them as
        folded-view-only, never frozen into the creation snapshot."""
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "semantic_hash": self.semantic_hash,
            "exact_hash": self.exact_hash,
            "run_seq": self.run_seq,
            "created_at": self.created_at,
            "run_executed_at": self.run_executed_at,
            "status": self.status,
            "status_reason": self.status_reason,
            "experiment_type": self.experiment_type,
            "hypothesis_id": self.hypothesis_id,
            "parent_experiment_id": self.parent_experiment_id,
            "reason_for_run": self.reason_for_run,
            "change_from_parent": self.change_from_parent,
            "research_stage": self.research_stage,
            "frozen_spec_ref": self.frozen_spec_ref,
            "frozen_spec_sha256": self.frozen_spec_sha256,
            "frozen_spec_commit": self.frozen_spec_commit,
            "frozen_spec_blob_sha": self.frozen_spec_blob_sha,
            "tags": list(self.tags),
            "notes": self.notes,
            "code": self.code.to_dict(),
            "datasets": [d.to_dict() for d in self.datasets],
            "universe_policy": self.universe_policy,
            "survivorship_safe": self.survivorship_safe,
            "uses_proxy_data": self.uses_proxy_data,
            "no_datasets_reason": self.no_datasets_reason,
            "strategy": self.strategy.to_dict(),
            "backtest_config": dict(self.backtest_config),
            "recorded_via": self.recorded_via,
            "manual_results_justification": self.manual_results_justification,
            "search_space_id": self.search_space_id,
            "n_configs_evaluated": self.n_configs_evaluated,
            "config_family_hash": self.config_family_hash,
            "results": self.results.to_dict() if self.results is not None else None,
            "run_facts": dict(self.run_facts),
            "warnings": list(self.warnings),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "rerun_of": self.rerun_of,
        }

    @staticmethod
    def from_dict(d: dict) -> "ExperimentRecord":
        return ExperimentRecord(
            schema_version=d["schema_version"],
            experiment_id=d["experiment_id"],
            semantic_hash=d["semantic_hash"],
            exact_hash=d["exact_hash"],
            run_seq=d["run_seq"],
            created_at=d["created_at"],
            run_executed_at=d.get("run_executed_at"),
            status=d["status"],
            status_reason=d.get("status_reason"),
            experiment_type=d["experiment_type"],
            hypothesis_id=d.get("hypothesis_id"),
            parent_experiment_id=d.get("parent_experiment_id"),
            reason_for_run=d["reason_for_run"],
            change_from_parent=d.get("change_from_parent"),
            research_stage=d["research_stage"],
            frozen_spec_ref=d.get("frozen_spec_ref"),
            frozen_spec_sha256=d.get("frozen_spec_sha256"),
            frozen_spec_commit=d.get("frozen_spec_commit"),
            frozen_spec_blob_sha=d.get("frozen_spec_blob_sha"),
            tags=tuple(d.get("tags", ())),
            notes=d.get("notes"),
            code=CodeIdentity.from_dict(d["code"]),
            datasets=tuple(DatasetRef.from_dict(x) for x in d["datasets"]),
            universe_policy=d["universe_policy"],
            survivorship_safe=d.get("survivorship_safe"),
            uses_proxy_data=d["uses_proxy_data"],
            no_datasets_reason=d.get("no_datasets_reason"),
            strategy=StrategyRef.from_dict(d["strategy"]),
            backtest_config=dict(d["backtest_config"]),
            recorded_via=d["recorded_via"],
            manual_results_justification=d.get("manual_results_justification"),
            search_space_id=d.get("search_space_id"),
            n_configs_evaluated=d["n_configs_evaluated"],
            config_family_hash=d["config_family_hash"],
            results=ResultSummary.from_dict(d["results"]) if d.get("results") is not None else None,
            run_facts=dict(d.get("run_facts", {})),
            warnings=tuple(d.get("warnings", ())),
            artifacts=tuple(ArtifactRef.from_dict(x) for x in d.get("artifacts", ())),
            rerun_of=d.get("rerun_of"),
        )

    # -- R§5.1 semantic hash payload --------------------------------------

    def semantic_payload(self) -> dict:
        data = sorted(
            (d.semantic_dict() for d in self.datasets),
            key=lambda x: (x["dataset_id"], x["field_type"], x["source_venue"]),
        )
        return {
            "schema_version": self.schema_version,
            "experiment_type": self.experiment_type,
            "data": data,
            "universe_policy": self.universe_policy,
            "survivorship_safe": self.survivorship_safe,
            "strategy": self.strategy.to_dict(),
            "backtest_config": dict(self.backtest_config),
            "frozen_spec_sha256": self.frozen_spec_sha256,
            # R§20.3.1 (blocking) — a hand-asserted, uncross-checked record is
            # not the same experiment as one derived from a real
            # BacktestResult, so recorded_via is now part of the semantic
            # identity.
            "recorded_via": self.recorded_via,
        }


# R§20.5.4 (blocking) — near-duplicate grouping: the semantic_hash payload
# with the fields that legitimately change across a data re-ingest or a
# window nudge (but NOT the underlying idea being tested) removed. This is a
# module-level function (not a method) because `store.py` computes it BEFORE
# an `ExperimentRecord` exists (config_family_hash is itself a field on the
# record, derived alongside semantic_hash/exact_hash at creation time).
def config_family_payload(semantic_payload: dict) -> dict:
    """R§20.5.4's literal text names `content_hash`, `content_hash_method`,
    `data_start`, `data_end`, `eval_start`, `eval_end` and `recorded_via` as
    the fields to strip. `frozen_spec_sha256` is ALSO stripped here, beyond
    that literal list -- see the implementation report for why this is a
    flagged reading, not a silent extension: R§20.6.4 requires
    `config_family_hash` to be able to MATCH between an `out_of_sample`
    record (which R§14.5 makes REQUIRE a non-null `frozen_spec_sha256`) and
    a PRIOR NON-OOS record (which essentially never sets `frozen_spec_ref`
    at all, so `frozen_spec_sha256 is None`). Keeping `frozen_spec_sha256`
    in this payload would make every OOS record's `config_family_hash`
    differ from every non-OOS record's by construction, making
    `OOS_RELABEL_OF` permanently unreachable -- the EXACT structural-
    unreachability defect R§20.6.4's own rationale names as v1.1's failure
    mode for the analogous `research_stage`-exclusion promise (R§5.1.2/D2).
    """
    payload = dict(semantic_payload)
    payload["data"] = [
        {k: v for k, v in d.items() if k not in ("content_hash", "content_hash_method", "data_start", "data_end", "eval_start", "eval_end")}
        for d in payload["data"]
    ]
    payload.pop("recorded_via", None)
    payload.pop("frozen_spec_sha256", None)
    return payload
