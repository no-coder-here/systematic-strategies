"""R§8/R§10/R§11/R§13 — persistence, lifecycle, query and the public API.

Layout (R§10.1):
    <root>/records/EXP-<prefix>-r<NN>.json     write-once (O_CREAT|O_EXCL)
    <root>/history.jsonl                       append-only
    <root>/artifacts/                          gitignored payloads
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from .codeid import verify_code_state
from .models import (
    ArtifactRef,
    CodeIdentity,
    DatasetRef,
    ExperimentRecord,
    RECORD_WARNING_PREFIXES,
    RECORDED_VIA_VALUES,
    REPRODUCIBILITY_STATUSES,
    RegistryError,
    RegistryIntegrityError,
    ResultSummary,
    SCHEMA_VERSION,
    STATUSES,
    StrategyRef,
    ValidationError,
    config_family_payload,
)
from .serialize import canonical_json, decode, encode, strict_json_loads, stored_json

__all__ = ["ExperimentRegistry", "FoldedExperiment", "ID_PREFIX_HEX"]

# R§20.2.1 — the ONLY record-level warning `record_experiment` will accept
# through its private `_extra_warnings` channel (R§20.8.9 / MW-A3). This
# channel exists solely so `backtest_adapter.record_backtest_result` can pass
# through PROVENANCE_INCOMPLETE, which only a raw BacktestResult can know
# (R§4.9's rationale). Anything else arriving here is an internal-caller bug,
# not a legitimate warning, and MUST raise rather than widen the vocabulary
# by accident.
_ALLOWED_EXTRA_WARNINGS = frozenset({"PROVENANCE_INCOMPLETE"})

# R§5.3 — module-level constant, deliberately mutable at the module level so
# a test can monkeypatch `store.ID_PREFIX_HEX = 2` and brute-force a prefix
# collision (R§5.3.2). Referenced by *name* below (never captured into a
# default argument) so the monkeypatched value is picked up at call time.
ID_PREFIX_HEX = 16


# ---------------------------------------------------------------------------
# R§21.1.2 — adapter trust capability (blocking)
# ---------------------------------------------------------------------------
#
# `record_experiment` used to accept a public `_recorded_via` keyword: a
# single string bought adapter trust from any caller. R§21.1 replaces that
# with a module-private capability object, checked by IDENTITY (`is`), never
# `isinstance` (forgeable by subclassing), truthiness, or a string. Only a
# reference to THIS exact singleton -- obtained by importing it, which only
# `backtest_adapter.py` is meant to do -- satisfies the check. A freshly
# constructed `_AdapterCapability()`, a subclass instance, or any other
# object is rejected: `RegistryError` on anything that is not `None` and is
# not `is _ADAPTER_CAPABILITY`.
class _AdapterCapability:
    """R§21.1.2 — opaque capability token. Deliberately carries no state and
    supports no equality/hash override, so identity (`is`) is the only
    possible check; nothing about its VALUE can be reproduced by a caller
    who does not already hold a reference to `_ADAPTER_CAPABILITY`."""

    __slots__ = ()


_ADAPTER_CAPABILITY = _AdapterCapability()


# ---------------------------------------------------------------------------
# Folded view (R§1.3 / R§8.4)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FoldedExperiment:
    """R§8.4 — an immutable `ExperimentRecord` plus its events applied in
    `seq` order. This is what every `ExperimentRegistry` method returns.
    Any attribute not explicitly overridden here delegates to the underlying
    immutable record (`__getattr__` below)."""

    record: ExperimentRecord
    status: str
    status_reason: Optional[str]
    artifacts: tuple
    tags: tuple
    notes: Optional[str]
    warnings: tuple
    status_history: tuple
    reproducibility_status: str
    divergence_detail: tuple

    def __getattr__(self, item):
        # Only called when normal attribute lookup fails, i.e. for anything
        # not one of this dataclass's own fields — so this can never shadow
        # the folded overrides above.
        return getattr(self.record, item)


def _records_dir(root: Path) -> Path:
    return Path(root) / "records"


def _history_path(root: Path) -> Path:
    return Path(root) / "history.jsonl"


def _artifacts_dir(root: Path) -> Path:
    return Path(root) / "artifacts"


def _record_path(root: Path, experiment_id: str) -> Path:
    return _records_dir(root) / f"{experiment_id}.json"


def _read_json_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        tree = strict_json_loads(text)
    except ValueError as exc:
        raise RegistryIntegrityError(f"UNPARSEABLE_RECORD: {path}: {exc}") from exc
    return decode(tree)


class ExperimentRegistry:
    """R§11 — the public API. All methods return folded views (R§8.4)."""

    def __init__(
        self,
        root,
        *,
        hash_fn=hashlib.sha256,
        repo_root: Optional[Path] = None,
        _clock: Optional[Callable[[], pd.Timestamp]] = None,
    ):
        self.root = Path(root)
        self.hash_fn = hash_fn
        # R§20.6.5 — the anchor `frozen_spec_ref` is resolved against. Every
        # real caller under `experiments/**` runs from the repo root
        # (pyproject.toml `testpaths` convention); tests pass an explicit
        # `repo_root` fixture so nothing depends on the live repository.
        self.repo_root = Path(repo_root) if repo_root is not None else Path.cwd()
        # R§21.1.3 (blocking) — the SOLE seam for the wall-clock witness
        # (`logged_at`, and every other internally-stamped event time). A
        # zero-arg callable, re-invoked on every call, never a frozen value —
        # defaults to the real wall clock. This replaces the v1.2 per-call
        # `_logged_at_override` keyword on `record_experiment`, which was a
        # backdating switch reachable by any caller; injecting the clock only
        # through the constructor keeps the determinism-test seam (R§16.3)
        # while removing the per-record one.
        self._clock: Callable[[], pd.Timestamp] = _clock if _clock is not None else (lambda: pd.Timestamp.now(tz="UTC"))
        _records_dir(self.root).mkdir(parents=True, exist_ok=True)
        _artifacts_dir(self.root).mkdir(parents=True, exist_ok=True)

    # -- hashing (R§5) ----------------------------------------------------

    def _hash(self, payload) -> str:
        return self.hash_fn(canonical_json(payload).encode("utf-8")).hexdigest()

    # -- history log (R§8.3) ----------------------------------------------

    def _read_history_raw_lines(self) -> list:
        """Raw text lines (no trailing `\\n`), exactly as stored — the input
        to the R§20.7.2 hash chain. Kept separate from `_read_history_lines`
        (which decodes) so chain verification hashes the EXACT bytes that
        were hashed at write time, never a round-tripped re-encoding."""
        path = _history_path(self.root)
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def _read_history_lines(self) -> list:
        raw_lines = self._read_history_raw_lines()
        events = []
        for i, line in enumerate(raw_lines, start=1):
            if not line.strip():
                raise RegistryIntegrityError(f"history.jsonl line {i} is blank/truncated (R§8.3)")
            try:
                tree = strict_json_loads(line)
            except ValueError as exc:
                raise RegistryIntegrityError(f"history.jsonl line {i} is malformed JSON (R§8.3): {exc}") from exc
            ev = decode(tree)
            for key in ("seq", "at", "event", "experiment_id", "payload", "logged_at", "prev_line_sha256"):
                if key not in ev:
                    raise RegistryIntegrityError(f"history.jsonl line {i} missing required key {key!r} (R§8.3/R§20.7)")
            events.append(ev)
        return events

    def _append_history_event(
        self, *, event: str, experiment_id: str, payload: dict, at: pd.Timestamp, logged_at: Optional[pd.Timestamp] = None
    ) -> None:
        raw_lines = self._read_history_raw_lines()
        seq = len(raw_lines) + 1
        # R§20.7.2 (blocking) — append-only hash chain: each line's
        # `prev_line_sha256` is the SHA-256 of the PREVIOUS line's exact
        # bytes (as written, i.e. the canonical-JSON string with no trailing
        # newline); `null` for the genesis line. Because each line's own
        # bytes already include ITS predecessor's hash, the chain covers the
        # entire history back to line 1 — deleting-and-renumbering a line
        # anywhere breaks every subsequent link, not just the tampered one.
        prev_line_sha256 = hashlib.sha256(raw_lines[-1].encode("utf-8")).hexdigest() if raw_lines else None
        # R§20.7.1/R§21.1.3 (blocking) — `logged_at` is STORE-STAMPED (never
        # the caller's `created_at`), excluded from every hash/comparison, and
        # is what actually answers "when was the registry told about this?".
        # Always drawn from `self._clock()` — the sole seam (R§21.1.3).
        logged_at = logged_at if logged_at is not None else self._clock()
        line = {
            "seq": seq,
            "at": at,
            "logged_at": logged_at,
            "prev_line_sha256": prev_line_sha256,
            "event": event,
            "experiment_id": experiment_id,
            "payload": payload,
        }
        with open(_history_path(self.root), "a", encoding="utf-8") as f:
            f.write(canonical_json(line) + "\n")

    def _events_for(self, experiment_id: str) -> list:
        return [ev for ev in self._read_history_lines() if ev["experiment_id"] == experiment_id]

    # -- record I/O ---------------------------------------------------------

    def _write_record_once(self, record: ExperimentRecord) -> bytes:
        path = _record_path(self.root, record.experiment_id)
        payload = stored_json(record.to_dict()).encode("utf-8")
        # R§8.2 — the SINGLE write-once guard. R§8.2.1 (BD12/M13): there is
        # deliberately no redundant "if path.exists(): raise" pre-check —
        # that would make the O_EXCL mutation survivable, since the
        # pre-check alone would still raise and mask the loss of the real
        # guard.
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise RegistryError(f"record {record.experiment_id} already exists (R§8.2 write-once)") from exc
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        return payload

    def _read_record(self, experiment_id: str) -> ExperimentRecord:
        path = _record_path(self.root, experiment_id)
        if not path.exists():
            raise KeyError(experiment_id)
        d = _read_json_file(path)
        return ExperimentRecord.from_dict(d)

    # -- folding (R§8.4) ----------------------------------------------------

    def _fold_status_only(self, record: ExperimentRecord, events: list) -> str:
        status = record.status
        for ev in events:
            if ev["event"] == "status_change":
                status = ev["payload"]["to"]
        return status

    def _reproducibility(self, record: ExperimentRecord, folded_status: str) -> tuple:
        if record.run_seq == 0:
            return "UNIQUE", ()
        prefix = record.exact_hash[:ID_PREFIX_HEX]
        baseline_id = f"EXP-{prefix}-r00"
        try:
            baseline = self.load_experiment(baseline_id)
        except KeyError as exc:
            raise RegistryIntegrityError(
                f"exact-hash baseline {baseline_id} for {record.experiment_id} is missing (R§5.4)"
            ) from exc
        # R§5.5 — NOT_COMPARABLE whenever either side's FOLDED status isn't
        # COMPLETED, or either side has no results. This is a derived
        # property of the folded view, never frozen into the record, because
        # a later set_status on either side can change the verdict.
        if folded_status != "COMPLETED" or baseline.status != "COMPLETED":
            return "NOT_COMPARABLE", ()
        if record.results is None or baseline.record.results is None:
            return "NOT_COMPARABLE", ()

        a = record.results.comparison_dict()
        b = baseline.record.results.comparison_dict()
        diffs = []
        for key in ("n_periods", "rebalance_count", "ruined", "result_warnings"):
            if canonical_json(a[key]) != canonical_json(b[key]):
                diffs.append(key)
        for group in ("metrics", "custom"):
            a_sub, b_sub = a[group], b[group]
            for k in sorted(set(a_sub) | set(b_sub)):
                if (k in a_sub) != (k in b_sub) or canonical_json(a_sub.get(k)) != canonical_json(b_sub.get(k)):
                    diffs.append(f"{group}.{k}")
        if diffs:
            return "DIVERGED", tuple(sorted(diffs))
        return "REPRODUCED", ()

    def _fold(self, record: ExperimentRecord) -> FoldedExperiment:
        events = self._events_for(record.experiment_id)
        status = record.status
        status_reason = record.status_reason
        status_history = [(record.created_at, None, record.status, record.status_reason)]
        artifacts = list(record.artifacts)
        tags = set(record.tags)
        notes = record.notes
        for ev in events:
            kind = ev["event"]
            payload = ev["payload"]
            if kind == "status_change":
                status_history.append((ev["at"], payload["from"], payload["to"], payload["reason"]))
                status = payload["to"]
                status_reason = payload["reason"]
            elif kind == "artifact_added":
                artifacts.append(ArtifactRef.from_dict(payload))
            elif kind == "annotation":
                if payload.get("note") is not None:
                    notes = payload["note"] if notes is None else f"{notes}\n{payload['note']}"
                if payload.get("tags_added"):
                    tags |= set(payload["tags_added"])
            elif kind in ("created", "created_backfilled"):
                pass
            else:
                raise RegistryIntegrityError(f"unknown history event kind {kind!r} (R§8.3)")

        artifacts_t = tuple(artifacts)
        warn_set = set(record.warnings)
        for a in artifacts_t:
            if a.sha256 is None:
                warn_set.add(f"MISSING_ARTIFACT:{a.name}")

        # R§20.4.1 (blocking) — sticky, IRREMOVABLE record-level warnings.
        # Derived from the full status history (creation status + every
        # status_change ever applied), recomputed on every fold, never
        # persisted into the on-disk record. An INVALID/REJECTED/FAILED
        # status that is later laundered back to COMPLETED via set_status
        # therefore still shows up in the DEFAULT summary/warning surface —
        # the R§20.4 defect this closes is that v1.1's ONLY trace of such
        # laundering was `status_history`, which nothing rendered and no
        # filter matched.
        ever_statuses = {s[2] for s in status_history}
        if "INVALID" in ever_statuses:
            warn_set.add("WAS_INVALIDATED")
        if "REJECTED" in ever_statuses:
            warn_set.add("WAS_REJECTED")
        if "FAILED" in ever_statuses:
            warn_set.add("WAS_FAILED")

        warnings = tuple(sorted(warn_set))

        repro_status, divergence_detail = self._reproducibility(record, status)

        return FoldedExperiment(
            record=record,
            status=status,
            status_reason=status_reason,
            artifacts=artifacts_t,
            tags=tuple(sorted(tags)),
            notes=notes,
            warnings=warnings,
            status_history=tuple(status_history),
            reproducibility_status=repro_status,
            divergence_detail=divergence_detail,
        )

    # -- R§11 item 2 --------------------------------------------------------

    def load_experiment(self, experiment_id: str) -> FoldedExperiment:
        record = self._read_record(experiment_id)
        return self._fold(record)

    # -- R§11 item 3 --------------------------------------------------------

    def list_experiments(self) -> tuple:
        ids = sorted(p.stem for p in _records_dir(self.root).glob("EXP-*.json"))
        return tuple(self.load_experiment(i) for i in ids)

    # -- record-level warnings assembly (R§4.9) -----------------------------

    def _assemble_warnings(
        self,
        *,
        code_identity: CodeIdentity,
        datasets: tuple,
        survivorship_safe,
        artifacts: tuple,
        research_stage: str,
        parent_experiment_id: Optional[str],
        eval_windows: list,
        extra_warnings: tuple,
        created_at: pd.Timestamp,
        logged_at: pd.Timestamp,
        strategy_name: str,
        search_space_id: Optional[str],
        config_family_hash: str,
        frozen_spec_blob_sha: Optional[str],
        recorded_via: str,
        results: Optional[ResultSummary],
        manual_results_justification: Optional[str],
        n_configs_evaluated: Optional[int],
    ) -> tuple:
        for tok in extra_warnings:
            if tok not in _ALLOWED_EXTRA_WARNINGS:
                raise RegistryError(
                    f"internal: _extra_warnings token {tok!r} is not in the allowed set "
                    f"{sorted(_ALLOWED_EXTRA_WARNINGS)} (R§20.8.9/MW-A3)"
                )
        warnings = set(extra_warnings)
        if any(d.native_or_proxy == "proxy" for d in datasets):
            warnings.add("PROXY_DATA")
        if survivorship_safe is None:
            warnings.add("SURVIVORSHIP_UNKNOWN")
        elif survivorship_safe is False:
            warnings.add("SURVIVORSHIP_UNSAFE")
        if code_identity.dirty_worktree:
            warnings.add("DIRTY_WORKTREE")
        if not code_identity.git_available:
            warnings.add("GIT_UNAVAILABLE")
        # R§20.7.3 — a record-level trace that the code state pinned by this
        # record already includes untracked files at record time (distinct
        # from DIRTY_WORKTREE: a MODIFIED tracked file is also dirty but is
        # at least resolvable from git history; an untracked file is not).
        if code_identity.untracked_code_files > 0:
            warnings.add("UNTRACKED_CODE_AT_RECORD_TIME")
        expected_pv = code_identity.contract_versions.get("data_processing_version")
        for d in datasets:
            if d.processing_version != expected_pv:
                warnings.add(f"PROCESSING_VERSION_MISMATCH:{d.dataset_id}")
            if d.content_hash is None:
                warnings.add(f"CONTENT_HASH_UNAVAILABLE:{d.dataset_id}")
        for a in artifacts:
            if a.sha256 is None:
                warnings.add(f"MISSING_ARTIFACT:{a.name}")

        # R§20.7.1 — a record whose declared `created_at` is far from the
        # moment the registry actually wrote it down. Measured: v1.1 accepted
        # a `created_at` of `2024-01-01` silently.
        if abs((logged_at - created_at).total_seconds()) > 3600:
            warnings.add("BACKDATED_CREATED_AT")

        # R§20.3.2 — a manual record carrying an uncross-checked result.
        if recorded_via == "manual" and results is not None:
            warnings.add("UNVERIFIED_MANUAL_RESULTS")

        # R§21.7.3 (blocking) — `n_configs_evaluated is None` means the
        # multiple-testing denominator for this record is UNKNOWN, not "1".
        if n_configs_evaluated is None:
            warnings.add("N_CONFIGS_UNKNOWN")

        # R§20.6.1 (blocking) — OOS_WINDOW_OVERLAP MUST be checked against
        # EVERY ancestor in lineage_of(parent), not the direct parent alone,
        # AND every record sharing (strategy_name, search_space_id).
        # Measured: pointing parent_experiment_id at an unrelated record, or
        # a grandparent-once-removed, suppressed the warning entirely under
        # v1.1's direct-parent-only check.
        if research_stage == "out_of_sample" and parent_experiment_id is not None:
            candidates = {}
            try:
                for fe in self.lineage_of(parent_experiment_id):
                    candidates[fe.record.experiment_id] = fe
            except KeyError:
                pass
            if search_space_id:
                for fe in self.list_experiments():
                    if fe.record.strategy.name == strategy_name and fe.record.search_space_id == search_space_id:
                        candidates[fe.record.experiment_id] = fe
            for cand_id, fe in candidates.items():
                cand_windows = [(d.eval_start, d.eval_end) for d in fe.record.datasets if d.eval_start is not None]
                for (s, e) in eval_windows:
                    if s is None or e is None:
                        continue
                    for (ps, pe) in cand_windows:
                        if s <= pe and ps <= e:  # interval intersection
                            warnings.add(f"OOS_WINDOW_OVERLAP:{cand_id}")
                            break

            # R§20.6.3 — the child's frozen_spec_blob_sha differs from its
            # direct parent's.
            try:
                parent = self.load_experiment(parent_experiment_id)
            except KeyError:
                parent = None
            if (
                parent is not None
                and parent.record.frozen_spec_blob_sha is not None
                and frozen_spec_blob_sha is not None
                and parent.record.frozen_spec_blob_sha != frozen_spec_blob_sha
            ):
                warnings.add("SPEC_CHANGED_SINCE_PARENT")

        # R§20.6.4 — an out_of_sample record whose config_family_hash matches
        # a PRIOR non-OOS record: relabelling an identical computation as OOS
        # is not an out-of-sample test.
        if research_stage == "out_of_sample":
            for fe in self.list_experiments():
                if fe.record.research_stage != "out_of_sample" and fe.record.config_family_hash == config_family_hash:
                    warnings.add(f"OOS_RELABEL_OF:{fe.record.experiment_id}")

        for tok in warnings:
            base = tok.split(":", 1)[0]
            if base not in RECORD_WARNING_PREFIXES:
                raise RegistryError(f"internal: assembled an out-of-vocabulary warning token {tok!r} (R§4.9)")
        return tuple(sorted(warnings))

    # -- R§11 item 1 ----------------------------------------------------------

    def _verify_committed_blob(self, relpath: str) -> tuple:
        """R§20.6.2 (blocking) — verifies `relpath` (already resolved and
        anchored under `self.repo_root`) is TRACKED and has no uncommitted
        difference from HEAD, then returns `(commit_sha, blob_sha)`.
        Rationale: v1.1's content hash caught later EDITS to an
        already-recorded spec, but not the sequence that matters — look at
        the OOS result, edit the spec, THEN register. A committed blob
        establishes prior commitment; a working-tree file does not."""
        git_timeout = 5.0
        try:
            tracked = subprocess.run(
                ["git", "-C", str(self.repo_root), "ls-files", "--error-unmatch", relpath],
                capture_output=True, text=True, timeout=git_timeout,
            )
        except Exception as exc:
            raise ValidationError(f"frozen_spec_ref {relpath!r}: git unavailable, cannot verify committed blob (R§20.6.2): {exc}") from exc
        if tracked.returncode != 0:
            raise ValidationError(
                f"frozen_spec_ref {relpath!r} MUST be a git-tracked file for research_stage=='out_of_sample' "
                f"(R§20.6.2) — not tracked at {self.repo_root}"
            )
        diff = subprocess.run(
            ["git", "-C", str(self.repo_root), "diff", "--quiet", "HEAD", "--", relpath],
            capture_output=True, text=True, timeout=git_timeout,
        )
        if diff.returncode != 0:
            raise ValidationError(
                f"frozen_spec_ref {relpath!r} has uncommitted changes relative to HEAD (R§20.6.2) — "
                "an OOS record MUST reference a git-committed blob, not a dirty working-tree file"
            )
        commit_proc = subprocess.run(
            ["git", "-C", str(self.repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=git_timeout,
        )
        blob_proc = subprocess.run(
            ["git", "-C", str(self.repo_root), "rev-parse", f"HEAD:{relpath}"],
            capture_output=True, text=True, timeout=git_timeout,
        )
        if commit_proc.returncode != 0 or blob_proc.returncode != 0:
            raise ValidationError(f"frozen_spec_ref {relpath!r}: could not resolve HEAD commit/blob sha (R§20.6.2)")
        return commit_proc.stdout.strip(), blob_proc.stdout.strip()

    def record_experiment(
        self,
        *,
        experiment_type: str,
        research_stage: str,
        reason_for_run: str,
        code_identity: CodeIdentity,
        datasets: tuple,
        universe_policy: str,
        survivorship_safe,
        strategy: StrategyRef,
        backtest_config: dict,
        status: str,
        status_reason: Optional[str] = None,
        results: Optional[ResultSummary] = None,
        run_facts: Optional[dict] = None,
        artifacts: tuple = (),
        parent_experiment_id: Optional[str] = None,
        hypothesis_id: Optional[str] = None,
        change_from_parent: Optional[str] = None,
        frozen_spec_ref: Optional[str] = None,
        tags: tuple = (),
        notes: Optional[str] = None,
        no_datasets_reason: Optional[str] = None,
        created_at: pd.Timestamp,
        run_executed_at: Optional[pd.Timestamp] = None,
        # R§20.5.1/R§21.7.1 (blocking) — multiple-testing grouping.
        # REQUIRED, no default, tri-state: an `int >= 1` (a VERIFIED count)
        # or `None` (UNKNOWN). Omission is a TypeError (Python enforces this
        # for a keyword-only parameter with no default) rather than silently
        # asserting "exactly one configuration was evaluated" — the v1.2
        # defect this closes (D14 — self-reported, unenforceable, but a
        # visible UNKNOWN is strictly better than an invisible, possibly
        # false, "1").
        search_space_id: Optional[str] = None,
        n_configs_evaluated: Optional[int],
        # R§20.3.2 (blocking) — REQUIRED (non-empty) iff this ends up
        # recorded_via=="manual" AND results is not None.
        manual_results_justification: Optional[str] = None,
        # Not part of R§11's public signature. Used exclusively by
        # `backtest_adapter.record_backtest_result` to pass through the one
        # record-level token (PROVENANCE_INCOMPLETE, R§12.3) that can only be
        # known from a raw `BacktestResult`, which this generic API never
        # sees. See the implementation report for why this channel exists —
        # R§4.9 assigns emission of PROVENANCE_INCOMPLETE to "the adapter",
        # but R§11's signature has no parameter for adapter-derived tokens.
        _extra_warnings: tuple = (),
        # R§21.1.2 (blocking) — REPLACES v1.2's public `_recorded_via: str`
        # keyword, which a single string bought adapter trust for. Trust is
        # now conferred ONLY by object IDENTITY (`is`), never `isinstance`
        # (forgeable by subclassing), truthiness, or a string:
        #   - `capability is None` (the default) => `recorded_via = "manual"`.
        #   - `capability is _ADAPTER_CAPABILITY` => `recorded_via = "adapter"`.
        #   - anything else => `RegistryError`, including a freshly
        #     constructed `_AdapterCapability()` built by an external caller.
        # Only `backtest_adapter.record_backtest_result` is meant to import
        # and pass `_ADAPTER_CAPABILITY`.
        capability: object = None,
    ) -> FoldedExperiment:
        run_facts = dict(run_facts or {})
        datasets = tuple(datasets)
        tags = tuple(tags)
        artifacts = tuple(artifacts)
        if status not in STATUSES:
            raise ValidationError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
        # R§21.1.2 (blocking) — identity check, not `isinstance`/truthiness.
        if capability is None:
            recorded_via = "manual"
        elif capability is _ADAPTER_CAPABILITY:
            recorded_via = "adapter"
        else:
            raise RegistryError(
                "record_experiment: `capability` must be either omitted/None or the module-private "
                "adapter capability singleton obtained from `registry.store._ADAPTER_CAPABILITY` "
                "(R§21.1.2) — a look-alike object (including a freshly constructed "
                "`_AdapterCapability()`) is rejected by identity, not type"
            )
        if n_configs_evaluated is not None and n_configs_evaluated < 1:
            raise ValidationError("n_configs_evaluated MUST be >= 1 or None (UNKNOWN) (R§21.7.1)")

        if parent_experiment_id is not None:
            try:
                parent = self.load_experiment(parent_experiment_id)
            except KeyError as exc:
                raise RegistryError(
                    f"parent_experiment_id {parent_experiment_id!r} does not resolve to an existing record (R§14.1)"
                ) from exc
            if research_stage == "out_of_sample" and parent.record.created_at > created_at:
                raise ValidationError("parent.created_at MUST be <= created_at for an out_of_sample run (R§14.5)")

        frozen_spec_sha256 = None
        frozen_spec_commit = None
        frozen_spec_blob_sha = None
        if frozen_spec_ref is not None:
            # R§20.6.5 (blocking) — anchored to `self.repo_root`; a `..`
            # segment (or a resolved path escaping repo_root) is refused,
            # mirroring R§9's absolute-path guard on ArtifactRef.path.
            if ".." in Path(frozen_spec_ref).parts:
                raise ValidationError(f"frozen_spec_ref {frozen_spec_ref!r} MUST NOT contain '..' (R§20.6.5)")
            repo_root_resolved = self.repo_root.resolve()
            spec_path = (self.repo_root / frozen_spec_ref).resolve()
            try:
                spec_path.relative_to(repo_root_resolved)
            except ValueError as exc:
                raise ValidationError(
                    f"frozen_spec_ref {frozen_spec_ref!r} escapes repo_root {repo_root_resolved} (R§20.6.5)"
                ) from exc
            if not spec_path.is_file():
                raise ValidationError(
                    f"frozen_spec_ref {frozen_spec_ref!r} MUST resolve to an existing repo-relative file (R§14.5), "
                    f"resolved to {spec_path}"
                )
            frozen_spec_sha256 = hashlib.sha256(spec_path.read_bytes()).hexdigest()
            if research_stage == "out_of_sample":
                frozen_spec_commit, frozen_spec_blob_sha = self._verify_committed_blob(frozen_spec_ref)
        if research_stage == "out_of_sample" and frozen_spec_ref is None:
            raise ValidationError("frozen_spec_ref is REQUIRED when research_stage == 'out_of_sample' (R§14.5)")

        uses_proxy_data = any(d.native_or_proxy == "proxy" for d in datasets)

        # R§5.1 semantic_hash — exactly the pinned key set + R§20.3.1's
        # recorded_via.
        data_sorted = sorted(
            (d.semantic_dict() for d in datasets),
            key=lambda x: (x["dataset_id"], x["field_type"], x["source_venue"]),
        )
        semantic_payload = {
            "schema_version": SCHEMA_VERSION,
            "experiment_type": experiment_type,
            "data": data_sorted,
            "universe_policy": universe_policy,
            "survivorship_safe": survivorship_safe,
            "strategy": strategy.to_dict(),
            "backtest_config": dict(backtest_config),
            "frozen_spec_sha256": frozen_spec_sha256,
            "recorded_via": recorded_via,
        }
        semantic_hash = self._hash(semantic_payload)
        # R§20.5.4 — the near-duplicate grouping key: same payload, minus the
        # fields that change across a re-ingest/window-nudge/recording-path
        # without changing the underlying idea being tested.
        config_family_hash = self._hash(config_family_payload(semantic_payload))
        exact_payload = {
            "semantic_hash": semantic_hash,
            "code": {
                "git_commit": code_identity.git_commit,
                "dirty_worktree": code_identity.dirty_worktree,
                "code_fingerprint": code_identity.code_fingerprint,
                "contract_versions": dict(code_identity.contract_versions),
            },
        }
        exact_hash = self._hash(exact_payload)

        # R§21.1.3 (blocking) — the sole seam: `self._clock()`, injected only
        # via the `ExperimentRegistry` constructor. No per-call override.
        logged_at = self._clock()

        eval_windows = [(d.eval_start, d.eval_end) for d in datasets if d.eval_start is not None]
        warnings = self._assemble_warnings(
            code_identity=code_identity,
            datasets=datasets,
            survivorship_safe=survivorship_safe,
            artifacts=artifacts,
            research_stage=research_stage,
            parent_experiment_id=parent_experiment_id,
            eval_windows=eval_windows,
            extra_warnings=_extra_warnings,
            created_at=created_at,
            logged_at=logged_at,
            strategy_name=strategy.name,
            search_space_id=search_space_id,
            config_family_hash=config_family_hash,
            frozen_spec_blob_sha=frozen_spec_blob_sha,
            recorded_via=recorded_via,
            results=results,
            manual_results_justification=manual_results_justification,
            n_configs_evaluated=n_configs_evaluated,
        )

        prefix = exact_hash[:ID_PREFIX_HEX]
        existing = sorted(_records_dir(self.root).glob(f"EXP-{prefix}-r*.json"))
        run_seq = len(existing)
        if run_seq > 99:
            raise RegistryError(f"run_seq > 99 for exact_hash prefix {prefix!r} (R§5.3)")
        if run_seq > 0:
            # R§5.3.2 — prefix collision check: compare against the FULL
            # exact_hash of the existing run_seq==0 record.
            baseline_id = f"EXP-{prefix}-r00"
            baseline_record = self._read_record(baseline_id)
            if baseline_record.exact_hash != exact_hash:
                raise RegistryError(
                    f"exact_hash prefix collision at {prefix!r}: existing run_seq==0 record "
                    f"{baseline_id} has a different full exact_hash (R§5.3.2)"
                )

        experiment_id = f"EXP-{prefix}-r{run_seq:02d}"
        rerun_of = None if run_seq == 0 else f"EXP-{prefix}-r00"

        record = ExperimentRecord(
            schema_version=SCHEMA_VERSION,
            experiment_id=experiment_id,
            semantic_hash=semantic_hash,
            exact_hash=exact_hash,
            run_seq=run_seq,
            created_at=created_at,
            run_executed_at=run_executed_at,
            status=status,
            status_reason=status_reason,
            experiment_type=experiment_type,
            hypothesis_id=hypothesis_id,
            parent_experiment_id=parent_experiment_id,
            reason_for_run=reason_for_run,
            change_from_parent=change_from_parent,
            research_stage=research_stage,
            frozen_spec_ref=frozen_spec_ref,
            frozen_spec_sha256=frozen_spec_sha256,
            frozen_spec_commit=frozen_spec_commit,
            frozen_spec_blob_sha=frozen_spec_blob_sha,
            tags=tags,
            notes=notes,
            code=code_identity,
            datasets=datasets,
            universe_policy=universe_policy,
            survivorship_safe=survivorship_safe,
            uses_proxy_data=uses_proxy_data,
            no_datasets_reason=no_datasets_reason,
            strategy=strategy,
            backtest_config=dict(backtest_config),
            recorded_via=recorded_via,
            manual_results_justification=manual_results_justification,
            search_space_id=search_space_id,
            n_configs_evaluated=n_configs_evaluated,
            config_family_hash=config_family_hash,
            results=results,
            run_facts=run_facts,
            warnings=warnings,
            artifacts=artifacts,
            rerun_of=rerun_of,
        )

        payload_bytes = self._write_record_once(record)
        record_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        self._append_history_event(
            event="created",
            experiment_id=experiment_id,
            payload={
                "semantic_hash": semantic_hash,
                "exact_hash": exact_hash,
                "run_seq": run_seq,
                "status": status,
                "experiment_type": experiment_type,
                "record_sha256": record_sha256,
            },
            at=created_at,
            logged_at=logged_at,
        )
        return self.load_experiment(experiment_id)

    # -- R§11 item 5 ----------------------------------------------------------

    def set_status(self, experiment_id: str, status: str, reason: str) -> FoldedExperiment:
        if status not in STATUSES:
            raise ValidationError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
        if status != "COMPLETED" and not (reason and reason.strip()):
            raise ValidationError("reason is REQUIRED non-empty for a non-COMPLETED status (R§8.1/R§11 item 5)")
        current = self.load_experiment(experiment_id)
        if current.status == status:
            raise ValidationError(f"no-op status transition {status!r} -> {status!r} is not permitted (R§11 item 5)")
        self._append_history_event(
            event="status_change",
            experiment_id=experiment_id,
            payload={"from": current.status, "to": status, "reason": reason},
            at=self._clock(),
        )
        return self.load_experiment(experiment_id)

    # -- R§11 item 6 ----------------------------------------------------------

    def add_artifact(self, experiment_id: str, artifact: ArtifactRef) -> FoldedExperiment:
        self.load_experiment(experiment_id)  # KeyError if absent
        self._append_history_event(
            event="artifact_added",
            experiment_id=experiment_id,
            payload=artifact.to_dict(),
            at=artifact.recorded_at,
        )
        return self.load_experiment(experiment_id)

    # -- R§11 item 7 ----------------------------------------------------------

    def annotate(self, experiment_id: str, *, note: Optional[str] = None, tags: tuple = ()) -> FoldedExperiment:
        self.load_experiment(experiment_id)
        if note is None and not tags:
            raise ValidationError("annotate() requires at least one of note or tags")
        payload = {}
        if note is not None:
            payload["note"] = note
        if tags:
            payload["tags_added"] = list(tags)
        self._append_history_event(
            event="annotation",
            experiment_id=experiment_id,
            payload=payload,
            at=self._clock(),
        )
        return self.load_experiment(experiment_id)

    # -- R§11 item 8: lineage / groupings ---------------------------------

    def children_of(self, experiment_id: str) -> tuple:
        return tuple(fe for fe in self.list_experiments() if fe.record.parent_experiment_id == experiment_id)

    def descendants_of(self, experiment_id: str) -> tuple:
        result = []
        visited = set()
        frontier = [experiment_id]
        while frontier:
            current = frontier.pop()
            if current in visited:
                raise RegistryIntegrityError(f"PARENT_CYCLE detected while walking descendants of {experiment_id!r}")
            visited.add(current)
            kids = self.children_of(current)
            result.extend(kids)
            frontier.extend(fe.record.experiment_id for fe in kids)
        # R§20.8.9 (MW-A8) — sorted by experiment_id: traversal order (a
        # stack-based frontier) is an implementation detail, not a query
        # contract.
        return tuple(sorted(result, key=lambda fe: fe.record.experiment_id))

    def lineage_of(self, experiment_id: str) -> tuple:
        chain = []
        visited = set()
        current_id = experiment_id
        while current_id is not None:
            if current_id in visited:
                raise RegistryIntegrityError(f"PARENT_CYCLE detected in lineage of {experiment_id!r}")
            visited.add(current_id)
            fe = self.load_experiment(current_id)
            chain.append(fe)
            current_id = fe.record.parent_experiment_id
        chain.reverse()
        return tuple(chain)

    def exact_rerun_groups(self) -> dict:
        groups: dict = {}
        for fe in self.list_experiments():
            groups.setdefault(fe.record.exact_hash, []).append(fe.record.experiment_id)
        return {k: tuple(sorted(v)) for k, v in groups.items()}

    def semantic_duplicates(self) -> dict:
        groups: dict = {}
        for fe in self.list_experiments():
            groups.setdefault(fe.record.semantic_hash, []).append(fe.record.experiment_id)
        return {k: tuple(sorted(v)) for k, v in groups.items() if len(v) >= 2}

    def near_duplicates(self) -> dict:
        """R§20.5.4 (blocking) — groups (size >= 2) sharing a
        `config_family_hash`: "have we tested this configuration before?"
        across a data re-ingest, a window nudge, or a different
        `recorded_via` path — none of which `semantic_hash` alone survives."""
        groups: dict = {}
        for fe in self.list_experiments():
            groups.setdefault(fe.record.config_family_hash, []).append(fe.record.experiment_id)
        return {k: tuple(sorted(v)) for k, v in groups.items() if len(v) >= 2}

    def sibling_count(self, experiment_id: str) -> int:
        """R§20.5.3 — how many OTHER records share this record's
        `search_space_id` (0 if the record has none)."""
        fe = self.load_experiment(experiment_id)
        ssid = fe.record.search_space_id
        if not ssid:
            return 0
        return sum(
            1
            for other in self.list_experiments()
            if other.record.search_space_id == ssid and other.record.experiment_id != experiment_id
        )

    def search_space_summary(self, search_space_id: str) -> dict:
        """R§20.5.3/R§21.7.4 — the denominator of any reported Sharpe MUST be
        retrievable: how many records, how many configs were evaluated in
        total (self-reported, D14), what statuses exist, and the best/worst
        Sharpe among COMPLETED records with a result.

        R§21.7.4 (blocking) — `n_configs_evaluated_total` sums only the
        KNOWN (non-`None`) members; it MUST NOT silently treat an UNKNOWN
        member as 0 or 1, since either would understate or misstate the
        multiple-testing denominator. `n_records_with_unknown_n_configs`
        counts the UNKNOWN members, and
        `n_configs_evaluated_total_is_lower_bound` is `True` whenever any
        member is UNKNOWN — the total is then a lower bound, not the true
        count.
        """
        members = [fe for fe in self.list_experiments() if fe.record.search_space_id == search_space_id]
        known = [fe.record.n_configs_evaluated for fe in members if fe.record.n_configs_evaluated is not None]
        n_unknown = sum(1 for fe in members if fe.record.n_configs_evaluated is None)
        n_configs_total = sum(known)
        statuses = tuple(sorted({fe.status for fe in members}))
        sharpes = [
            fe.record.results.metrics.get("sharpe")
            for fe in members
            if fe.status == "COMPLETED" and fe.record.results is not None
            and fe.record.results.metrics.get("sharpe") is not None
        ]
        sharpes = [s for s in sharpes if not (isinstance(s, float) and s != s)]  # drop NaN
        best_worst = (max(sharpes), min(sharpes)) if sharpes else (None, None)
        return {
            "n_records": len(members),
            "n_configs_evaluated_total": n_configs_total,
            "n_records_with_unknown_n_configs": n_unknown,
            "n_configs_evaluated_total_is_lower_bound": n_unknown > 0,
            "statuses": statuses,
            "best_and_worst_sharpe": best_worst,
        }

    def diverged(self) -> tuple:
        return tuple(fe for fe in self.list_experiments() if fe.reproducibility_status == "DIVERGED")

    def failed_or_rejected(self) -> tuple:
        # Name is shorthand (R§13.4 demo #2): covers FAILED, REJECTED AND
        # INVALID, per the query demonstration's own description.
        return tuple(fe for fe in self.list_experiments() if fe.status in ("FAILED", "REJECTED", "INVALID"))

    # -- R§13 query ---------------------------------------------------------

    _ALLOWED_FILTERS = frozenset(
        {
            "semantic_hash",
            "exact_hash",
            "experiment_type",
            "status",
            "research_stage",
            "strategy_name",
            "hypothesis_id",
            "parent_experiment_id",
            "dataset_id",
            "source_venue",
            "field_type",
            "native_or_proxy",
            "symbol",
            "uses_proxy_data",
            "survivorship_safe",
            "funding_mode",
            "funding_disabled",
            "tag",
            "warning_token",
            "reproducibility_status",
            "created_after",
            "created_before",
            "ever_status",
            "search_space_id",
            "config_family_hash",
            "n_configs_unknown",
        }
    )

    def _match_one(self, fe: FoldedExperiment, key: str, value) -> bool:
        r = fe.record
        if key == "semantic_hash":
            return r.semantic_hash == value
        if key == "exact_hash":
            return r.exact_hash == value
        if key == "experiment_type":
            return r.experiment_type == value
        if key == "status":
            return fe.status == value  # R§13.1 MW6 — folded value
        if key == "research_stage":
            return r.research_stage == value
        if key == "strategy_name":
            return r.strategy.name == value
        if key == "hypothesis_id":
            return r.hypothesis_id == value
        if key == "parent_experiment_id":
            return r.parent_experiment_id == value
        if key == "dataset_id":
            return any(d.dataset_id == value for d in r.datasets)
        if key == "source_venue":
            return any(d.source_venue == value for d in r.datasets)
        if key == "field_type":
            return any(d.field_type == value for d in r.datasets)
        if key == "native_or_proxy":
            return any(d.native_or_proxy == value for d in r.datasets)
        if key == "symbol":
            return any(value in d.symbols for d in r.datasets)  # exact membership, no substring
        if key == "uses_proxy_data":
            return r.uses_proxy_data == value
        if key == "survivorship_safe":
            if value in (None, "unknown"):
                return r.survivorship_safe is None
            return r.survivorship_safe == value
        if key == "funding_mode":
            return r.backtest_config.get("funding_mode") == value
        if key == "funding_disabled":
            # R§20.8.9 (MW-A7) — a record whose backtest_config carries no
            # `funding_mode` key at all (R§4.4.3's empty-config exemption)
            # MUST NOT match `funding_disabled=False`: R§13.1's general rule
            # is "a filter on a key absent from backtest_config does not
            # match", and the old `None == "disabled"` comparison collapsed
            # "absent" and "explicitly not disabled" into the same `False`.
            if "funding_mode" not in r.backtest_config:
                return False
            is_disabled = r.backtest_config.get("funding_mode") == "disabled"
            return is_disabled == bool(value)
        if key == "ever_status":
            # R§20.4.3 — matches any status the record has EVER held,
            # including its creation status, not just the current folded one.
            return value in {s[2] for s in fe.status_history}
        if key == "search_space_id":
            return r.search_space_id == value
        if key == "config_family_hash":
            return r.config_family_hash == value
        if key == "n_configs_unknown":
            # R§21.7.3 (blocking) — matches records whose n_configs_evaluated
            # is UNKNOWN (`None`) when `value` is truthy, and KNOWN
            # (non-`None`) records when `value` is falsy.
            return (r.n_configs_evaluated is None) == bool(value)
        if key == "tag":
            return value in fe.tags
        if key == "warning_token":
            tokens = set(fe.warnings)
            if r.results is not None:
                tokens |= set(r.results.result_warnings)
            return any(tok.startswith(value) for tok in tokens)
        if key == "reproducibility_status":
            if value not in REPRODUCIBILITY_STATUSES:
                raise ValidationError(f"reproducibility_status filter value must be one of {sorted(REPRODUCIBILITY_STATUSES)}")
            return fe.reproducibility_status == value
        if key == "created_after":
            if value.tzinfo is None:
                raise ValidationError("created_after MUST be tz-aware")
            return r.created_at >= value
        if key == "created_before":
            if value.tzinfo is None:
                raise ValidationError("created_before MUST be tz-aware")
            return r.created_at <= value
        raise AssertionError(f"unreachable: unhandled allowed filter {key!r}")

    def find_experiments(self, **filters) -> tuple:
        for key in filters:
            if key not in self._ALLOWED_FILTERS:
                raise ValidationError(f"unknown filter keyword {key!r} (R§13.1) — refusing to return a silent superset")
        out = []
        for fe in self.list_experiments():
            ok = True
            for key, value in filters.items():
                values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
                if not any(self._match_one(fe, key, v) for v in values):
                    ok = False
                    break
            if ok:
                out.append(fe)
        return tuple(out)

    # -- R§11 item 9: consistency -------------------------------------------

    def verify_registry(self) -> tuple:
        findings = []
        records_by_id = {}
        for path in sorted(_records_dir(self.root).glob("EXP-*.json")):
            exp_id = path.stem
            try:
                d = _read_json_file(path)
            except RegistryIntegrityError:
                findings.append(f"UNPARSEABLE_RECORD:{exp_id}")
                continue
            # R§11 item 9 — SCHEMA_VERSION_UNKNOWN is a DISTINCT finding from
            # UNPARSEABLE_RECORD: the file parses as JSON fine, it just names
            # a schema version this reader does not understand (R§19 D8),
            # which must never be silently coerced into "corrupt".
            if d.get("schema_version") != SCHEMA_VERSION:
                findings.append(f"SCHEMA_VERSION_UNKNOWN:{exp_id}")
                continue
            try:
                rec = ExperimentRecord.from_dict(d)
            except Exception:
                findings.append(f"UNPARSEABLE_RECORD:{exp_id}")
                continue
            records_by_id[exp_id] = (rec, path)

        events = self._read_history_lines()
        created_sha_by_id = {}
        seen_created_ids = set()
        for i, ev in enumerate(events, start=1):
            if ev["seq"] != i:
                findings.append(f"BAD_SEQ:{ev['seq']}")
            if ev["event"] == "created":
                seen_created_ids.add(ev["experiment_id"])
                created_sha_by_id[ev["experiment_id"]] = ev["payload"]["record_sha256"]
            if ev["event"] == "created_backfilled":
                seen_created_ids.add(ev["experiment_id"])
                created_sha_by_id[ev["experiment_id"]] = ev["payload"]["record_sha256"]

        for exp_id, (rec, path) in records_by_id.items():
            if exp_id not in seen_created_ids:
                findings.append(f"ORPHAN_RECORD:{exp_id}")
            else:
                actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual_sha != created_sha_by_id[exp_id]:
                    findings.append(f"RECORD_MODIFIED:{exp_id}")

        for exp_id in seen_created_ids:
            if exp_id not in records_by_id:
                findings.append(f"MISSING_RECORD:{exp_id}")

        # DANGLING_PARENT / PARENT_CYCLE
        for exp_id, (rec, _path) in records_by_id.items():
            if rec.parent_experiment_id is not None and rec.parent_experiment_id not in records_by_id:
                findings.append(f"DANGLING_PARENT:{exp_id}")
        for exp_id in records_by_id:
            visited = set()
            current = exp_id
            while current is not None:
                if current in visited:
                    findings.append(f"PARENT_CYCLE:{exp_id}")
                    break
                visited.add(current)
                entry = records_by_id.get(current)
                current = entry[0].parent_experiment_id if entry else None

        # RUN_SEQ_GAP / PREFIX_COLLISION, by full exact_hash and by prefix
        by_exact_hash: dict = {}
        by_prefix: dict = {}
        for exp_id, (rec, _path) in records_by_id.items():
            by_exact_hash.setdefault(rec.exact_hash, []).append(rec.run_seq)
            by_prefix.setdefault(rec.exact_hash[:ID_PREFIX_HEX], set()).add(rec.exact_hash)
        for exact_hash, seqs in by_exact_hash.items():
            seqs_sorted = sorted(seqs)
            if seqs_sorted and seqs_sorted != list(range(seqs_sorted[0], seqs_sorted[-1] + 1)):
                findings.append(f"RUN_SEQ_GAP:{exact_hash}")
        for prefix, hashes in by_prefix.items():
            if len(hashes) > 1:
                ids = sorted(
                    exp_id for exp_id, (rec, _p) in records_by_id.items() if rec.exact_hash[:ID_PREFIX_HEX] == prefix
                )
                findings.append(f"PREFIX_COLLISION:{','.join(ids)}")

        # INCONSISTENT_CONTENT_HASH
        by_window: dict = {}
        for exp_id, (rec, _path) in records_by_id.items():
            for d in rec.datasets:
                if d.content_hash is None:
                    continue
                key = (d.dataset_id, d.data_start, d.data_end)
                by_window.setdefault(key, set()).add(d.content_hash)
        for (dataset_id, _s, _e), hashes in by_window.items():
            if len(hashes) > 1:
                findings.append(f"INCONSISTENT_CONTENT_HASH:{dataset_id}")

        # R§20.7.2 (blocking) — HISTORY_CHAIN_BROKEN: recompute each line's
        # `prev_line_sha256` from the ACTUAL previous line's raw bytes and
        # compare. Deleting a record + its history line + renumbering `seq`
        # (the "deliberate fraud" scenario R§20.7.2 names) breaks the chain
        # at that point and every link after it.
        raw_lines = self._read_history_raw_lines()
        for i, raw_line in enumerate(raw_lines):
            try:
                decoded = decode(strict_json_loads(raw_line))
            except (ValueError, RegistryIntegrityError):
                continue  # already reported via the events-parsing path above
            expected_prev = hashlib.sha256(raw_lines[i - 1].encode("utf-8")).hexdigest() if i > 0 else None
            if decoded.get("prev_line_sha256") != expected_prev:
                findings.append(f"HISTORY_CHAIN_BROKEN:{decoded.get('seq', i + 1)}")

        # R§20.5.5 (blocking) — SEMANTIC_DUP_RESULT_DIFF: two records sharing
        # a semantic_hash (same configuration) but with DIFFERENT metrics —
        # an accounting change hiding as two unrelated-looking UNIQUE
        # records, invisible to `diverged()` (which only compares within one
        # exact_hash).
        by_semantic: dict = {}
        for exp_id, (rec, _path) in records_by_id.items():
            by_semantic.setdefault(rec.semantic_hash, []).append(exp_id)
        for sem_hash, ids in by_semantic.items():
            ids_sorted = sorted(ids)
            for i in range(len(ids_sorted)):
                for j in range(i + 1, len(ids_sorted)):
                    a_rec = records_by_id[ids_sorted[i]][0]
                    b_rec = records_by_id[ids_sorted[j]][0]
                    a_metrics = a_rec.results.metrics if a_rec.results is not None else None
                    b_metrics = b_rec.results.metrics if b_rec.results is not None else None
                    if a_metrics is None or b_metrics is None:
                        continue
                    if canonical_json(a_metrics) != canonical_json(b_metrics):
                        findings.append(f"SEMANTIC_DUP_RESULT_DIFF:{ids_sorted[i]}:{ids_sorted[j]}")

        # R§20.11 — DIVERGED MUST appear in verify_registry() output (v1.1: a
        # DIVERGED verdict appeared in no file, no table, no verification
        # output at all).
        for exp_id in records_by_id:
            try:
                fe = self.load_experiment(exp_id)
            except (KeyError, RegistryIntegrityError):
                continue
            if fe.reproducibility_status == "DIVERGED":
                findings.append(f"DIVERGED:{exp_id}")

        # R§20.8.1 — ARTIFACT_MISSING / ARTIFACT_MODIFIED, from
        # `verify_artifacts()` for every record with artifacts.
        for exp_id, (rec, _path) in records_by_id.items():
            for name, status_ in self._verify_artifacts_for(rec).items():
                if status_ == "MISSING":
                    findings.append(f"ARTIFACT_MISSING:{exp_id}:{name}")
                elif status_ == "MODIFIED":
                    findings.append(f"ARTIFACT_MODIFIED:{exp_id}:{name}")

        return tuple(sorted(set(findings)))

    def _verify_artifacts_for(self, record: ExperimentRecord) -> dict:
        result = {}
        for a in record.artifacts:
            if a.sha256 is None:
                result[a.name] = "UNVERIFIABLE"
                continue
            abs_path = self.repo_root / a.path
            if not abs_path.is_file():
                result[a.name] = "MISSING"
                continue
            actual = hashlib.sha256(abs_path.read_bytes()).hexdigest()
            result[a.name] = "OK" if actual == a.sha256 else "MODIFIED"
        return result

    def verify_artifacts(self, experiment_id: str) -> dict:
        """R§9/R§20.8.1 (blocking) — `{artifact_name: "OK"|"MISSING"|
        "MODIFIED"|"UNVERIFIABLE"}`, recomputed from the ACTUAL file at
        `self.repo_root / artifact.path` against the `sha256` pinned at
        record time. `UNVERIFIABLE` covers an artifact recorded with
        `allow_missing=True` (`sha256 is None`) — there is nothing to verify
        against, by design."""
        fe = self.load_experiment(experiment_id)
        return self._verify_artifacts_for(fe.record)

    def repair_orphan(self, experiment_id: str) -> FoldedExperiment:
        path = _record_path(self.root, experiment_id)
        if not path.exists():
            raise RegistryError(f"cannot repair {experiment_id!r}: no record file on disk")
        events = self._events_for(experiment_id)
        if any(ev["event"] in ("created", "created_backfilled") for ev in events):
            raise RegistryError(f"{experiment_id!r} is not an ORPHAN_RECORD — a creation event already exists")
        record_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self._append_history_event(
            event="created_backfilled",
            experiment_id=experiment_id,
            payload={"record_sha256": record_sha256, "recovered_from": "ORPHAN_RECORD"},
            at=self._clock(),
        )
        return self.load_experiment(experiment_id)

    # -- R§15 summary rendering ----------------------------------------------

    def summary(self, experiment_id: str) -> str:
        fe = self.load_experiment(experiment_id)
        return render_summary(fe)

    def summary_table(self, records) -> str:
        # R§20.11 — gains warning-count, recorded_via and
        # reproducibility_status columns.
        ordered = sorted(records, key=lambda fe: (fe.record.created_at, fe.record.experiment_id))
        lines = []
        for fe in ordered:
            n_warnings = len(fe.warnings) + (len(fe.record.results.result_warnings) if fe.record.results is not None else 0)
            lines.append(
                f"{fe.record.experiment_id}  {fe.record.created_at.isoformat()}  {fe.status:9s}  "
                f"{fe.record.experiment_type:18s}  {fe.record.strategy.name}  "
                f"recorded_via={fe.record.recorded_via}  warnings={n_warnings}  "
                f"reproducibility_status={fe.reproducibility_status}"
            )
        return "\n".join(lines)


def render_summary(fe: FoldedExperiment) -> str:
    """R§15/R§20.11 — pinned rendering rules. See the docstring table in the
    spec: this function MUST NOT be "simplified" to drop any of the
    DIRTY/unknown/PROXY/n-a-suppressed/unavailable/nan/NOT-A-RESEARCH-RESULT
    literal tokens — each is asserted by a dedicated test."""
    r = fe.record
    lines = []

    # R§20.11/R§20.9.1 — the banner MUST render ABOVE any metric line for
    # experiment_type in {pipeline_validation, infrastructure}: these types
    # exist precisely to mark "not a research observation", and v1.1's
    # summary() printed Window B1's total_return/sharpe with no caveat at all.
    if r.experiment_type in ("pipeline_validation", "infrastructure"):
        lines.append(f"NOT A RESEARCH RESULT (experiment_type={r.experiment_type})")

    lines.append(f"experiment_id: {r.experiment_id}")
    lines.append(f"status: {fe.status}" + (f" ({fe.status_reason})" if fe.status_reason else ""))
    lines.append(f"experiment_type: {r.experiment_type}")
    lines.append(f"research_stage: {r.research_stage}")
    lines.append(f"reason_for_run: {r.reason_for_run}")
    lines.append(f"recorded_via: {r.recorded_via}")
    if r.recorded_via == "manual":
        lines.append("WARNING: provenance/metrics NOT cross-checked against a BacktestResult")
        # R§21.2.2 (blocking) — `manual_results_justification` is REQUIRED
        # *and rendered* (R§20.3.2) whenever it is non-empty; v1.2 recorded it
        # but passed it to `_assemble_warnings` as a dead parameter that was
        # never printed anywhere. Rendered immediately beneath the
        # manual-path warning line.
        if r.manual_results_justification:
            lines.append(f"manual_results_justification: {r.manual_results_justification}")
    if r.search_space_id:
        lines.append(f"search_space_id: {r.search_space_id}")
    # R§21.7.2 (blocking) — UNKNOWN (`None`) MUST render ALWAYS; a verified
    # count renders only when > 1 (unchanged from R§20.11), tagged
    # "(verified)" so UNKNOWN and a verified 1 (the common, safe case, which
    # is intentionally NOT rendered here — see the > 1 condition) can never
    # be confused by a reader skimming for the literal token.
    if r.n_configs_evaluated is None:
        lines.append("n_configs_evaluated: UNKNOWN")
    elif r.n_configs_evaluated > 1:
        lines.append(f"n_configs_evaluated: {r.n_configs_evaluated} (verified)")
    lines.append(f"strategy: {r.strategy.name} v{r.strategy.version}")

    commit = r.code.git_commit[:7] if r.code.git_commit else "NONE"
    dirty_token = "DIRTY" if r.code.dirty_worktree else "CLEAN"
    lines.append(f"code: commit {commit} ({dirty_token}) fingerprint {r.code.code_fingerprint[:12]} ({r.code.code_fingerprint_n_files} files)")

    for d in r.datasets:
        proxy_tok = f"PROXY(for={d.proxy_for})" if d.native_or_proxy == "proxy" else "native"
        chash = d.content_hash if d.content_hash is not None else "unavailable"
        window = f"eval=[{d.eval_start}, {d.eval_end}]" if d.eval_start is not None else "eval=n/a"
        lines.append(f"dataset: {d.dataset_id} [{d.field_type}] {proxy_tok} content_hash: {chash} {window}")

    lines.append(f"funding_mode: {r.backtest_config.get('funding_mode', 'n/a')}")

    if r.results is not None:
        for key in ("total_return", "sharpe", "cagr", "max_drawdown"):
            v = r.results.metrics.get(key)
            if v is None:
                rendered = "n/a (suppressed)" if key == "cagr" else "n/a"
            elif isinstance(v, float) and v != v:  # NaN
                rendered = "nan"
            else:
                rendered = str(v)
            lines.append(f"metric.{key}: {rendered}")
    else:
        lines.append("metrics: none (no result)")

    lines.append(f"warnings[record]: {', '.join(fe.warnings) if fe.warnings else 'none'}")
    result_warnings = r.results.result_warnings if r.results is not None else ()
    lines.append(f"warnings[result]: {', '.join(result_warnings) if result_warnings else 'none'}")

    lines.append(f"survivorship_safe: {'unknown' if r.survivorship_safe is None else r.survivorship_safe}")
    lines.append(f"parent_experiment_id: {r.parent_experiment_id or 'none'}")
    lines.append(f"rerun_of: {r.rerun_of or 'none'}")
    lines.append(f"reproducibility_status: {fe.reproducibility_status}")
    if fe.reproducibility_status == "DIVERGED":
        lines.append(f"divergence_detail: {', '.join(fe.divergence_detail)}")
    # R§20.4.2 — the full status_history whenever it exceeds length 1.
    if len(fe.status_history) > 1:
        hist_str = "; ".join(f"{at.isoformat()}: {frm or 'CREATED'} -> {to} ({reason})" for at, frm, to, reason in fe.status_history)
        lines.append(f"status_history: {hist_str}")
    # R§21.2.1 (blocking) — the FOLDED notes (`fe.notes`), never the
    # immutable record's own `r.notes`: R§8.4 makes the folded view what
    # every registry method returns, and `annotate()`'s appended notes were
    # silently absent from `summary()` before this fix (R§20.11's entire
    # rationale for the sticky WAS_* tokens is that an unrendered correction
    # is how honesty statements get lost — the identical failure mode here).
    if fe.notes:
        lines.append(f"notes: {fe.notes}")
    return "\n".join(lines)
