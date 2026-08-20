"""QR-PREP-001 P§4 — the minimal research runner (`docs/qr_prep_001_spec.md`
P§4, FROZEN). Implements a subset of `docs/research_methodology.md` M§4
mechanics already authorised there ("make 'every alpha run is registered,
pass or fail' true, and nothing else" — P§4 preamble).

Honest limit (P§4.7/M§4.5, blocking — MUST be restated wherever this runner
is documented): **D17 is mitigated here, not closed.** Hiding a run requires
only not calling `run_research_experiment` at all — an ephemeral session
leaves no trace, and this module cannot detect or prevent that. No M§5
(search-space/multiple-testing accounting), M§7, M§8, or M§10 (burn ledger)
gate is implemented by this module. A single, narrow M§9 (protected-OOS)
guard IS implemented here (added by a later work order than the rest of this
module): before delegating, this function refuses an `alpha_research` run
whose ACTUAL data window — derived solely from `market_data.close.index`,
never from any caller-declared value — overlaps the closed
`[evaluation_start, evaluation_end]` evaluation interval (M§9.6.9) of any
`status == "SEALED"` entry recorded in
`research_root / "oos" / "protected_windows.json"`. See
`ProtectedWindowOverlapError` and the dedicated section in
`run_research_experiment`'s own docstring below for the exact predicate and
its limits — it is temporal-only and narrow, not a general M§9
implementation: it does not touch `dependency_start`, `funding_coverage_end`,
reveal/burn accounting, or a pre-OOS gate, and it does not prevent a direct
filesystem read of protected data outside this sanctioned run function.
Nothing else here checks a hypothesis's or search-space's CONTENT, a
robustness plan, or a research-stage transition — P§4.8 excludes all of that
from this work order. The `hypothesis_id`/`search_space_id` presence check
and the exact `datasets`/`dataset_windows` set-consistency check described
below are registration PRECONDITIONS, not methodology enforcement: each
checks only that a caller supplied the shape the frozen registry
unconditionally requires for `experiment_type == "alpha_research"`, never
what a hypothesis or search space actually IS, never a hypothesis FILE, and
never search-space contents.

P§4.2 (blocking): this module MUST NOT compute, adjust, reinterpret or round
any performance number, and MUST NOT re-implement any engine or registry
behaviour. It is a thin, refusal-only wrapper around the frozen
`registry.backtest_adapter.run_and_register`, which is itself the only
sanctioned path from a `BacktestConfig`/`MarketData`/`StrategyOutput` triple
to a registered `ExperimentRecord`.

Honest guarantee (repair cycle 3 — repair cycles 1 and 2's wording each
overstated or understated what is achievable). This function validates,
BEFORE `run_and_register`/`run_backtest` is ever invoked, exactly these
things and no more:

  1. `research_root` resolves to an existing directory (P§4.6).
  2. `experiment_type` is absent or already `"alpha_research"` (P§4.4).
  3. `record_kwargs["datasets"]` is a non-empty `tuple`/`list` whose every
     element is a `registry.models.DatasetRef` instance (P§4.5). A
     non-empty container of the WRONG shape (a string, a dict, a tuple of
     strings/dicts/`None`, a bare `int`, ...) is refused here, not allowed
     through on the strength of being merely "truthy" — that was repair
     cycle 1's B1 gap (a `datasets` value that is non-empty but not
     registerable still let a run start, and the FAILED/INVALID branches
     inside `record_run` then raised deep inside `registry.record_experiment`,
     destroying the researcher's real exception).
  4. `record_kwargs["dataset_windows"]` is a non-empty `dict` with every key
     a `str`, AND the set of its keys is EXACTLY EQUAL to
     `{d.dataset_id for d in record_kwargs["datasets"]}` — both directions,
     not just one (repair cycle 3/Fix 2). Before this repair, only the
     "every declared dataset has a window" direction was checked here; a
     caller who ALSO supplied an extra `dataset_windows` key with no
     counterpart in `datasets` passed every check this runner had, ran the
     full backtest to completion, and only then failed inside
     `backtest_adapter._build_datasets` (which enforces the identical exact
     key-set match against `result.provenance`, R§12.1) — registering ZERO
     records for a run that had already computed real metrics.
  5. `record_kwargs["hypothesis_id"]` and `record_kwargs["search_space_id"]`
     are each present and are non-empty, non-whitespace-only `str` values
     (repair cycle 3/Fix 1). Both are unconditionally REQUIRED by
     `registry.models.ExperimentRecord.__post_init__` for
     `experiment_type == "alpha_research"` (R§14.6, R§20.5.1) — a type this
     runner always forces — so a caller who omitted either one previously
     ran the full engine, computed real metrics, and STILL registered ZERO
     records on both the success path and the FAILED/exception path (with
     the researcher's real exception demoted to `__cause__.__context__`
     underneath the registry's OWN `ValidationError` on the FAILED path).
     This runner checks only PRESENCE and non-blank `str`-ness of these two
     identifiers — never their content, format, or whether either one
     refers to a real, previously-registered hypothesis or search space;
     validating that remains out of scope (P§4.8) for a later work order.

**What this function does NOT and CANNOT guarantee up front:** nothing here
can pre-validate `strategy`, `code_identity`, `universe_policy`,
`backtest_config` funding-basis coherence, parent/lineage existence, or any
other `registry.record_experiment`/`ExperimentRecord.__post_init__`
invariant beyond the five items enumerated above — re-implementing those
checks here would itself violate P§4.2's no-re-implementation rule, and
there is no complete, finite, pre-run-checkable list of everything the
frozen registry might still reject. **An unconditional "every call that
reaches `run_and_register` registers something" guarantee is NOT achievable
at this layer, even now that items 4 and 5 above are closed.** What IS
achievable, and is implemented below, is a safety net that makes any
residual gap LOUD instead of silent: after delegating to `run_and_register`,
on both the success path and the exception path, this function checks
whether the registry actually gained a new record. If it did not,
`RunNotRegisteredError` is raised — chaining the original exception with
`raise ... from exc` when there was one, so the researcher's own failure is
NEVER destroyed or replaced. This net is itself limited in ways that MUST be
kept in mind, not oversold: (a) it converts every currently-known gap (items
1-5 above) AND every gap neither this nor a prior repair cycle anticipated
into a loud failure, but it cannot prevent the underlying registry-layer
failure from happening in the first place — it only detects it; (b) it
compares SETS of experiment ids observed via `registry.list_experiments()`
before and after, so a CONCURRENT writer registering some unrelated
experiment during this call's window can mask a genuine non-registration by
changing the set for an unrelated reason; and (c) `registry.list_experiments()`
itself can raise `RegistryIntegrityError` (a corrupt/unparseable history
file, a detected parent cycle, ...) — in that case this function lets that
exception propagate rather than manufacturing a `RunNotRegisteredError` it
has no basis to support.

Note this caller-supplied `datasets` tuple is consumed ONLY by `record_run`'s
FAILED/INVALID branches. On the success path, `record_backtest_result`
derives its own `datasets` tuple purely from `result.provenance` +
`dataset_windows` (R§12.1) and never reads `record_kwargs["datasets"]` at
all, so requiring it here does not conflict with, override, or shadow that
derivation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.models import BacktestConfig, BacktestResult
from registry.backtest_adapter import run_and_register
from registry.models import DatasetRef
from registry.store import ExperimentRegistry

__all__ = [
    "ProtectedWindowOverlapError",
    "ResearchRunnerError",
    "RunNotRegisteredError",
    "run_research_experiment",
]


class ResearchRunnerError(ValueError):
    """P§4 — raised when THIS module refuses a call before ever reaching the
    registry: missing/invalid `research_root` (`None`, empty, not a
    path-like `str`/`os.PathLike`, or not an existing directory), a
    non-`alpha_research` `experiment_type`, a missing/empty/wrong-shaped
    `record_kwargs["datasets"]`, a missing/wrong-shaped/set-mismatched
    `record_kwargs["dataset_windows"]`, or a missing/blank/non-`str`
    `record_kwargs["hypothesis_id"]`/`record_kwargs["search_space_id"]`
    (P§4.5 — see module docstring). Never raised in place of a
    registry/engine exception, which always propagates unchanged."""


class RunNotRegisteredError(RuntimeError):
    """Repair cycle 2 (F3) — the safety net, not a guarantee. Raised AFTER
    delegating to `run_and_register` when this function can positively
    confirm the registry did not gain a new record for this call — on
    EITHER the success path or the exception path. This is deliberately a
    DIFFERENT class from `ResearchRunnerError`: `ResearchRunnerError` means
    "refused before anything ran"; this means "the delegated call was
    invoked (and may or may not have itself executed a run before failing)
    but this function could not confirm a new record was registered".

    When the delegated call itself raised, that original exception is always
    chained via `raise RunNotRegisteredError(...) from original_exc` — it is
    never swallowed or replaced by this class. Recover the original exception
    from `.__cause__`."""


class ProtectedWindowOverlapError(ResearchRunnerError):
    """Added by a later, narrow work order than the rest of this module —
    the sealed-protected-OOS overlap guard. Raised BEFORE `run_and_register`/
    `run_backtest` is ever invoked when the run's ACTUAL data window —
    derived ONLY from `market_data.close.index.min()`/`.max()`, never from
    `record_kwargs['dataset_windows']` or any other caller-declared value —
    overlaps the CLOSED `[evaluation_start, evaluation_end]` evaluation
    interval (M§9.6.9) of a `status == "SEALED"` entry in
    `research_root / "oos" / "protected_windows.json"`.

    Deliberately narrow, and it MUST NOT be read as more than this: it
    guards only the evaluation interval, never `dependency_start` or
    `funding_coverage_end`; it is purely temporal and never conditions on
    `source_venue`, `native_or_proxy`, or `dataset_id`; it guards only THIS
    entry point, never a direct filesystem read elsewhere in the codebase;
    and it is a subclass of `ResearchRunnerError` ("refused before anything
    ran"), never raised in place of, or after, a registry/engine exception.

    A direct consequence of this guard, by design: `run_research_experiment`
    is UNUSABLE for a run whose data spans a sealed window's evaluation
    interval. The OOS evaluator does not call this function at all, so no
    exemption is carved out here — protected evaluation is reached only via
    the designated OOS evaluation path, never through this runner."""


def _parse_protected_window_timestamp(value: Any, context: str) -> datetime:
    """Parses one `evaluation_start`/`evaluation_end` field of one
    `protected_windows.json` entry. Requires a non-blank `str` in
    timezone-aware ISO-8601 form (as `datetime.fromisoformat` accepts,
    e.g. `"2025-08-01T00:00:00+00:00"`). Raises `ValueError` — never
    swallowed here — on anything else: `None`, a non-`str`, an empty/
    whitespace-only string, an unparseable string, or a timezone-NAIVE
    string. This is fail-closed plumbing for `_load_sealed_protected_windows`
    below, not a general-purpose timestamp parser."""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{context} MUST be a non-empty ISO-8601 str; got {value!r}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{context} MUST be a timezone-aware ISO-8601 timestamp; got {value!r}")
    return parsed


def _load_sealed_protected_windows(research_root_path: Path) -> list[tuple[str, datetime, datetime]]:
    """Reads `research_root_path / "oos" / "protected_windows.json"` and
    returns `(window_id, evaluation_start, evaluation_end)` for every entry
    whose `status == "SEALED"`. Entries with any other `status` are parsed
    (to validate the file, see below) but excluded from the returned list —
    this rule only guards SEALED windows (the user's own scoping).

    Source-of-truth file shape: a JSON array of objects, each carrying at
    least `window_id` (str), `status` (str), `evaluation_start` (str,
    timezone-aware ISO-8601), `evaluation_end` (str, timezone-aware
    ISO-8601) — plus other fields (`dependency_start`,
    `funding_coverage_end`, `snapshot`, ...) that this function never reads,
    because this guard covers the evaluation interval only.

    Fail-closed, deliberately: if the file exists but is not valid JSON, is
    not a JSON array, contains a non-object entry, or contains an entry
    missing or with an unparseable `window_id`/`status`/`evaluation_start`/
    `evaluation_end`, this raises `ResearchRunnerError` rather than treating
    the file as though it declared no windows — an unreadable seal file
    must never silently look identical to an absent one. If the `oos`
    directory or the file itself simply does not exist, there are no sealed
    windows and an empty list is returned (the run proceeds normally)."""
    windows_path = research_root_path / "oos" / "protected_windows.json"
    if not windows_path.is_file():
        return []
    try:
        raw_text = windows_path.read_text()
    except OSError as exc:
        raise ResearchRunnerError(
            f"protected windows file {windows_path!s} exists but could not be read "
            f"(fail-closed, refusing the run rather than treating this as 'no windows'): {exc}"
        ) from exc
    try:
        entries = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ResearchRunnerError(
            f"protected windows file {windows_path!s} is not valid JSON (fail-closed, refusing "
            f"the run rather than treating this as 'no windows'): {exc}"
        ) from exc
    if not isinstance(entries, list):
        raise ResearchRunnerError(
            f"protected windows file {windows_path!s} MUST contain a JSON array of window "
            f"entries at the top level (fail-closed); got {type(entries).__name__}"
        )
    sealed: list[tuple[str, datetime, datetime]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ResearchRunnerError(
                f"protected windows file {windows_path!s} entry #{i} MUST be a JSON object "
                f"(fail-closed); got {type(entry).__name__}"
            )
        window_id = entry.get("window_id")
        if not isinstance(window_id, str) or window_id.strip() == "":
            raise ResearchRunnerError(
                f"protected windows file {windows_path!s} entry #{i} has a missing or invalid "
                f"'window_id' (fail-closed); got {window_id!r}"
            )
        status = entry.get("status")
        if not isinstance(status, str) or status.strip() == "":
            raise ResearchRunnerError(
                f"protected windows file {windows_path!s} entry {window_id!r} has a missing or "
                f"invalid 'status' (fail-closed); got {status!r}"
            )
        try:
            eval_start = _parse_protected_window_timestamp(
                entry.get("evaluation_start"), "evaluation_start"
            )
            eval_end = _parse_protected_window_timestamp(
                entry.get("evaluation_end"), "evaluation_end"
            )
        except ValueError as exc:
            raise ResearchRunnerError(
                f"protected windows file {windows_path!s} entry {window_id!r} has a missing or "
                f"unparseable 'evaluation_start'/'evaluation_end' (fail-closed): {exc}"
            ) from exc
        if status == "SEALED":
            sealed.append((window_id, eval_start, eval_end))
    return sealed


def _check_no_sealed_protected_window_overlap(research_root_path: Path, market_data) -> None:
    """The guard itself. Derives the run's ACTUAL data window from
    `market_data.close.index.min()`/`.max()` — deliberately never from
    `record_kwargs['dataset_windows']` or any other caller-declared value,
    so a caller cannot evade this by declaring a clean window while feeding
    the engine sealed data. If `market_data.close.index` is empty, there is
    no run window to check and this is a no-op (an empty-data run fails
    elsewhere, in the engine, for unrelated reasons).

    For every `status == "SEALED"` entry loaded by
    `_load_sealed_protected_windows`, raises `ProtectedWindowOverlapError`
    if the run window intersects `[evaluation_start, evaluation_end]` — a
    CLOSED interval on both ends (M§9.6.9): `run_start <= evaluation_end and
    run_end >= evaluation_start`. This means a run window that only touches
    `evaluation_start` or `evaluation_end` exactly is refused, not allowed
    through. Guards ONLY the evaluation interval — never `dependency_start`
    or `funding_coverage_end` — and is purely temporal: it never reads
    `source_venue`, `native_or_proxy`, or `dataset_id`, so it fires
    identically regardless of venue or dataset."""
    sealed_windows = _load_sealed_protected_windows(research_root_path)
    if not sealed_windows:
        return
    index = market_data.close.index
    if len(index) == 0:
        return
    run_start = index.min()
    run_end = index.max()
    for window_id, eval_start, eval_end in sealed_windows:
        if run_start <= eval_end and run_end >= eval_start:
            raise ProtectedWindowOverlapError(
                "run_research_experiment: refused before run_and_register/run_backtest was "
                f"invoked — the run's actual data window, derived from market_data.close.index, "
                f"is [{run_start!s}, {run_end!s}], which overlaps the SEALED protected OOS "
                f"evaluation interval [{eval_start!s}, {eval_end!s}] of window_id={window_id!r} "
                "(both closed intervals, M§9.6.9). This guard covers the evaluation interval "
                "only, not dependency_start or funding_coverage_end."
            )


def _require_nonblank_str(value: Any, field_name: str) -> None:
    """Repair cycle 3 (Fix 1) — a registration precondition, not content
    validation: checks only that `record_kwargs[field_name]` is present and
    is a non-empty, non-whitespace-only `str`. Never checks format or
    meaning. Rejects `None` (the un-supplied default), any non-`str` type,
    the empty string, and a whitespace-only string."""
    if not isinstance(value, str) or value.strip() == "":
        raise ResearchRunnerError(
            f"record_kwargs[{field_name!r}] MUST be a non-empty, non-whitespace-only str "
            "(registration precondition — registry.models.ExperimentRecord.__post_init__ "
            "unconditionally requires this for experiment_type='alpha_research', which this "
            f"runner always forces, R§14.6/R§20.5.1); got {value!r} ({type(value).__name__})"
        )


def run_research_experiment(
    *,
    registry: ExperimentRegistry,
    config: BacktestConfig,
    market_data,
    strategy_output,
    record_kwargs: dict,
    research_root,
    **run_kwargs: Any,
) -> BacktestResult:
    """P§4.1 — the sanctioned entry point for a driver that wants a run
    registered. Delegates entirely to
    `registry.backtest_adapter.run_and_register`; this function performs its
    refusals up front, before delegating, and otherwise passes every
    argument through unmodified. See the module docstring for exactly what
    is (and is not) guaranteed, and for the post-delegation safety net.

    `research_root` (P§4.6): REQUIRED. Validated to be a real, existing
    directory, as before. This is now also used for exactly one additional,
    narrow purpose: `research_root / "oos" / "protected_windows.json"` is
    read (if present) to look up SEALED protected-OOS windows for the guard
    described below. Beyond that one file, `research_root` is still NOT used
    to scope, isolate, or enforce anything else about where the run reads or
    writes, and MUST NOT be mistaken for broader sandboxing. Rejected:
    `None`, the empty string, any other non-path-like value (raises
    `ResearchRunnerError`, not a bare `TypeError`), and any path that does
    not resolve to an existing directory (including a path that resolves to
    an existing regular FILE, not a directory).

    Sealed protected-OOS overlap guard (added by a later, narrow work
    order): immediately after `research_root` is validated, and BEFORE
    `run_and_register`/`run_backtest` is ever invoked, this function derives
    the run's ACTUAL data window from `market_data.close.index.min()`/
    `.max()` — deliberately NEVER from `record_kwargs["dataset_windows"]` or
    any other caller-declared value, so a caller cannot evade this by
    declaring a clean window while feeding the engine data that actually
    spans a sealed interval. It then loads every `status == "SEALED"` entry
    from `research_root / "oos" / "protected_windows.json"` (if that file or
    the `oos` directory does not exist, there are no sealed windows and the
    run proceeds normally) and raises `ProtectedWindowOverlapError`, naming
    the offending `window_id` and both intervals, if the run window
    intersects any such entry's `[evaluation_start, evaluation_end]` — a
    CLOSED interval on both ends (M§9.6.9): a run window that only touches
    `evaluation_start` or `evaluation_end` exactly is refused, not allowed
    through. Entries whose `status` is anything other than `"SEALED"` are
    not guarded. The check is purely temporal — it never reads
    `source_venue`, `native_or_proxy`, or `dataset_id` — so it fires
    identically regardless of venue or dataset. If `protected_windows.json`
    exists but cannot be parsed, or any entry is missing or has an
    unparseable `window_id`/`status`/`evaluation_start`/`evaluation_end`,
    the run is refused with `ResearchRunnerError` (fail-closed: an unreadable
    seal file is never silently treated as "no windows").

    What this guard does NOT do, stated explicitly so it is not oversold: it
    does NOT guard `dependency_start` or `funding_coverage_end` — only the
    evaluation interval named above. It does NOT prevent a direct filesystem
    read of `data/processed`, `research/oos/snapshots`, or any other path —
    it guards only calls that go through THIS function. It does NOT
    implement a pre-OOS gate, reveal/burn accounting, a ledger, a CLI, or a
    config loader. It does NOT special-case or exempt the OOS evaluator —
    that evaluator does not call `run_research_experiment` at all, so no
    exemption is needed — but a direct, intentional consequence is that this
    guard makes `run_research_experiment` UNUSABLE, by design, for a run
    whose data spans a sealed window's evaluation interval; protected
    evaluation is reached only via the designated OOS evaluation path, never
    through this runner. This closes one specific ACCIDENTAL-leakage shape —
    an ordinary `alpha_research` run through this sanctioned entry point
    whose actual data happens to overlap a sealed evaluation interval — and
    nothing broader; it is not a mechanical or filesystem-level
    access-control mechanism, and does not defend against a user who reads
    protected files directly.

    `record_kwargs` (P§4.3): copied before use, never mutated in place —
    the caller's dict is unchanged after this call returns (closes
    `docs/TODO.md` QR-INFRA-002-B item 3, which is `run_and_register`
    popping `dataset_windows` out of the caller-supplied dict).

    `experiment_type` (P§4.4/M§4.4): forced to `"alpha_research"` for every
    run through this runner. A caller supplying any other value is refused
    with `ResearchRunnerError` — relabelling defeats `near_duplicates()`
    (M§5.5.4) and `OOS_RELABEL_OF` (`store.py`), both of which key on
    `experiment_type` being uniform across a hypothesis lineage. A caller
    explicitly supplying `experiment_type="alpha_research"` (the only value
    that can ever be accepted) is let through, not refused.

    `record_kwargs["hypothesis_id"]` / `record_kwargs["search_space_id"]`
    (repair cycle 3/Fix 1): both are REQUIRED to be present and to be
    non-empty, non-whitespace-only `str` values. `experiment_type` is always
    forced to `"alpha_research"`, and `registry.models.ExperimentRecord`
    unconditionally requires a non-blank `hypothesis_id` (R§14.6) and a
    non-blank `search_space_id` (R§20.5.1) for that type — with no
    exemption. This is a PRESENCE/shape precondition only: neither value's
    content or format is checked, and neither is looked up against any
    hypothesis registry or search-space declaration (P§4.8 excludes that).

    `record_kwargs["datasets"]` (P§4.5): REQUIRED to be a non-empty
    `tuple`/`list` whose every element is a `registry.models.DatasetRef`.
    `experiment_type` is always forced to `"alpha_research"`, which
    `registry.models` never exempts from R§4.4.3's non-empty-`datasets`
    rule, and the FAILED/INVALID branches inside `record_run` have no
    `BacktestResult` to derive `datasets` from. Note explicitly: a caller
    cannot bypass this by also supplying `no_datasets_reason` — that field
    only licenses an empty `datasets` tuple for `experiment_type` in
    `{"infrastructure", "data_audit"}` (R§4.4.3), and `experiment_type` here
    is never one of those.

    `record_kwargs["dataset_windows"]` (P§4.5, repair cycle 3/Fix 2):
    REQUIRED to be a non-empty `dict` with `str` keys whose key SET is
    EXACTLY EQUAL to `{d.dataset_id for d in record_kwargs["datasets"]}` —
    neither a dataset with no window nor a window with no dataset is
    tolerated. This is checked because the success path
    (`record_backtest_result` -> `_build_datasets`) unconditionally requires
    this exact same key-set match against `result.provenance` (R§12.1), and
    checking only the "every dataset has a window" direction (as this
    function did before this repair) still let a caller with an EXTRA,
    unmatched `dataset_windows` key run a full, successful backtest to
    completion and only THEN fail to register anything.

    Registration on success/failure (P§4.5): given the refusals above all
    pass, `run_and_register` is invoked exactly once, and its own
    `record_run` mechanism registers `COMPLETED` on success, `FAILED` on any
    `BaseException` (re-raised, never swallowed), `INVALID` on normal exit
    without a result. This function does not wrap that delegated call in a
    `try`/`except` that could swallow anything from it; it only adds an
    outer check (see `RunNotRegisteredError`) that re-raises unchanged (on
    the exception path) or returns normally (on the success path) whenever a
    new record is confirmed, and otherwise raises `RunNotRegisteredError`,
    chaining any original exception.

    Out of scope (P§4.8, MUST NOT be built here): validating hypothesis or
    search-space CONTENT, hypothesis files, search-space declaration
    contents, robustness-plan validation, ledgers, burn accounting, reveal
    accounting, a pre-OOS gate, stage-transition checks, AST-scan changes, a
    CLI, a config loader. All deferred to a later work order. The ONE
    exception is the minimal sealed-protected-OOS overlap guard documented
    above under `research_root` — deliberately narrow (temporal-only,
    evaluation-interval-only, this entry point only) and not a substitute
    for any of the broader items still listed here as out of scope.
    """
    if research_root is None:
        raise ResearchRunnerError("research_root is REQUIRED (P§4.6)")
    if isinstance(research_root, str) and research_root == "":
        raise ResearchRunnerError("research_root must not be the empty string (P§4.6)")
    try:
        research_root_path = Path(research_root)
    except TypeError as exc:
        raise ResearchRunnerError(
            f"research_root must be a str or os.PathLike, got {research_root!r} "
            f"({type(research_root).__name__}) (P§4.6)"
        ) from exc
    if not research_root_path.is_dir():
        raise ResearchRunnerError(
            f"research_root {research_root_path!s} does not exist or is not a directory (P§4.6)"
        )

    # Sealed protected-OOS overlap guard (see docstring above under
    # `research_root`) — refuses BEFORE run_and_register/run_backtest is
    # ever invoked, using the run's ACTUAL data window derived from
    # market_data.close.index, never from any caller-declared window.
    _check_no_sealed_protected_window_overlap(research_root_path, market_data)

    # P§4.3 (blocking) — copy, never mutate, the caller's dict. `run_and_register`
    # itself pops `dataset_windows` out of whatever dict it is given; passing
    # it the caller's own object would leak that mutation back to the caller.
    record_kwargs = dict(record_kwargs)

    # P§4.4 (blocking) — uniform typing. Absent -> default to alpha_research.
    # Present and different -> refused, never silently overridden. Present
    # and already "alpha_research" -> accepted (not a refusal condition).
    supplied_type = record_kwargs.get("experiment_type", "alpha_research")
    if supplied_type != "alpha_research":
        raise ResearchRunnerError(
            "experiment_type must be 'alpha_research' for every run through "
            f"run_research_experiment (P§4.4/M§4.4), got {supplied_type!r}"
        )
    record_kwargs["experiment_type"] = "alpha_research"

    # Repair cycle 3 (Fix 1) — registration precondition, not methodology
    # enforcement (see module docstring): `experiment_type` is always forced
    # to 'alpha_research' above, and registry.models.ExperimentRecord
    # unconditionally requires a non-blank hypothesis_id (R§14.6) and a
    # non-blank search_space_id (R§20.5.1) for that type. Checking this here
    # — BEFORE run_and_register is invoked — prevents a full engine run from
    # executing, computing real metrics, and then registering ZERO records
    # on both the success and the FAILED/exception path (the FAILED path's
    # own registry.record_experiment call would itself raise ValidationError,
    # demoting the researcher's real exception to __cause__.__context__).
    _require_nonblank_str(record_kwargs.get("hypothesis_id"), "hypothesis_id")
    _require_nonblank_str(record_kwargs.get("search_space_id"), "search_space_id")

    # P§4.5 (blocking, F1) — non-empty is NOT sufficient: every element MUST
    # actually be a DatasetRef, or the FAILED/INVALID branches inside
    # record_run (which pass this tuple straight to registry.record_experiment
    # with no shape-checking of their own) fail deep inside the registry,
    # destroying the researcher's original exception. `no_datasets_reason`
    # does NOT exempt alpha_research from this requirement (see docstring).
    datasets_kw = record_kwargs.get("datasets")
    if (
        not isinstance(datasets_kw, (tuple, list))
        or len(datasets_kw) == 0
        or not all(isinstance(d, DatasetRef) for d in datasets_kw)
    ):
        raise ResearchRunnerError(
            "record_kwargs['datasets'] MUST be a non-empty tuple or list of registry.models.DatasetRef "
            "instances (P§4.5) — experiment_type is always forced to 'alpha_research', which "
            "registry.models never exempts from R§4.4.3's non-empty-datasets requirement (not even "
            "when record_kwargs['no_datasets_reason'] is also supplied), and the FAILED/INVALID "
            f"branches inside record_run have no BacktestResult to derive datasets from; got {datasets_kw!r}"
        )

    # P§4.5 (blocking, repair cycle 3/Fix 2) — dataset_windows is
    # unconditionally required by the success path
    # (backtest_adapter._build_datasets requires an EXACT key-set match
    # against result.provenance, R§12.1). This function cannot know
    # result.provenance before run_backtest executes, but it CAN — and now
    # does — check the identical exact-set-equality requirement against the
    # caller's OWN declared `datasets`, in BOTH directions: every declared
    # dataset_id must have a window, and every window key must correspond to
    # a declared dataset_id. Checking only the first direction (as a prior
    # repair cycle did) still let an extra, unmatched dataset_windows key
    # pass this runner, run a full successful backtest, and only then fail
    # inside _build_datasets with zero records registered.
    dataset_windows_kw = record_kwargs.get("dataset_windows")
    if (
        not isinstance(dataset_windows_kw, dict)
        or len(dataset_windows_kw) == 0
        or not all(isinstance(k, str) for k in dataset_windows_kw.keys())
    ):
        raise ResearchRunnerError(
            "record_kwargs['dataset_windows'] MUST be a non-empty dict with str keys (P§4.5) — "
            f"got {dataset_windows_kw!r}"
        )
    declared_dataset_ids = {d.dataset_id for d in datasets_kw}
    window_ids = set(dataset_windows_kw.keys())
    missing_windows = declared_dataset_ids - window_ids
    extra_windows = window_ids - declared_dataset_ids
    if missing_windows or extra_windows:
        problems = []
        if missing_windows:
            problems.append(
                "dataset_windows is missing entries for dataset_id(s) "
                f"{sorted(missing_windows)} declared in record_kwargs['datasets']"
            )
        if extra_windows:
            problems.append(
                "dataset_windows has entries for dataset_id(s) "
                f"{sorted(extra_windows)} with no matching element in record_kwargs['datasets']"
            )
        raise ResearchRunnerError(
            "record_kwargs['dataset_windows'] keys MUST be EXACTLY the set of dataset_id(s) declared "
            "in record_kwargs['datasets'] (P§4.5, R§12.1) — " + "; ".join(problems)
        )

    # P§4.2 (blocking) — delegate verbatim; no metric is touched here.
    #
    # F3 safety net (repair cycle 2): the checks above are necessarily
    # incomplete (see module docstring) — the frozen registry can still
    # refuse a record for a reason this function did not anticipate. Rather
    # than assert an unconditional guarantee this layer cannot keep, confirm
    # AFTER delegating that a new record actually landed, on both the
    # exception path and the success path. Comparing `list_experiments()` ids
    # before/after (rather than, say, a bare boolean) is deliberate: it is
    # the only check available that only QUERIES the registry (never
    # reimplementing its validation, P§4.2) and it is robust to whichever
    # experiment_id the registry assigns.
    ids_before = {fe.record.experiment_id for fe in registry.list_experiments()}
    try:
        result = run_and_register(
            registry,
            config,
            market_data,
            strategy_output,
            record_kwargs=record_kwargs,
            **run_kwargs,
        )
    except BaseException as exc:
        ids_after = {fe.record.experiment_id for fe in registry.list_experiments()}
        if ids_after == ids_before:
            # Repair cycle 3 — the prior wording here ("a run executed and
            # raised ...") asserted that a run had actually executed, which
            # is not always true: `run_and_register`/`record_run` can raise
            # (e.g. a plain TypeError for a missing/misnamed kwarg) before
            # anything resembling a backtest run ever started. State only
            # what is actually known: the delegated call raised, and no new
            # record was found.
            raise RunNotRegisteredError(
                "run_research_experiment: the delegated call to run_and_register raised "
                f"{type(exc).__name__}: {exc}, and no new record was found in the registry — "
                "this run was NOT registered (whether or not a backtest actually executed before "
                "the failure). The original exception is chained as __cause__."
            ) from exc
        raise
    ids_after = {fe.record.experiment_id for fe in registry.list_experiments()}
    if ids_after == ids_before:
        raise RunNotRegisteredError(
            "run_research_experiment: a run executed and returned successfully, but no new record "
            "was found in the registry — the run was NOT registered."
        )
    return result
