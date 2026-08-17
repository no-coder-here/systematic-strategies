"""R§12 — `BacktestResult` -> `ExperimentRecord` adapter, R§11.1's
`record_run` context manager, and R§20.2.1's `run_and_register`.

R§12.2 (blocking): this module copies fields off `BacktestResult`; it MUST
NOT recompute any metric, and MUST NOT import `backtest.metrics` (R§2.1.1) —
a second accounting authority above the engine is exactly what CLAUDE.md's
"Backtesting Principle" forbids.

R§20.2.1 (blocking) rescopes R§2.1's engine-import prohibition: THIS MODULE
(and only this module) MAY import `backtest.engine`, because it is the one
place `run_and_register` calls `run_backtest` on the driver's behalf.
`store.py`/`models.py`/`serialize.py`/`codeid.py`/`datahash.py` MUST remain
engine-free — see `tests/registry/test_registry_layering.py`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import traceback
from typing import Optional

import pandas as pd

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, BacktestResult

from .datahash import CONTENT_HASH_METHOD
from .models import ArtifactRef, CodeIdentity, DatasetRef, ResultSummary, StrategyRef, ValidationError
from .store import _ADAPTER_CAPABILITY, ExperimentRegistry, FoldedExperiment

__all__ = ["record_backtest_result", "record_run", "run_and_register"]

_UNSET = object()


def _backtest_config_dict(config: BacktestConfig) -> dict:
    """R§4.6.1 (blocking) — MUST enumerate `dataclasses.fields(BacktestConfig)`,
    never a hand-written literal list, so an amendment to the frozen contract
    is never silently dropped from the hash."""
    return {f.name: getattr(config, f.name) for f in dataclasses.fields(BacktestConfig)}


def _build_datasets(result: BacktestResult, dataset_windows: dict) -> tuple:
    """R§12.1 (BD1, blocking) — one `DatasetRef` per element of
    `result.provenance` (the engine's OWN tuple), never from a caller-declared
    list. The caller supplies only what provenance cannot know."""
    provenance = tuple(result.provenance or ())
    provenance_ids = {p.dataset_id for p in provenance}
    caller_ids = set(dataset_windows.keys())
    missing_caller = provenance_ids - caller_ids
    if missing_caller:
        raise ValidationError(
            f"result.provenance has dataset_id(s) {sorted(missing_caller)} with no matching entry in "
            f"dataset_windows (R§12.1)"
        )
    extra_caller = caller_ids - provenance_ids
    if extra_caller:
        raise ValidationError(
            f"dataset_windows has dataset_id(s) {sorted(extra_caller)} with no matching element in "
            f"result.provenance (R§12.1)"
        )

    datasets = []
    for p in provenance:
        window = dataset_windows[p.dataset_id]
        span = p.time_range or (None, None)
        content_hash = window.get("content_hash")
        datasets.append(
            DatasetRef(
                dataset_id=p.dataset_id,
                source_venue=p.source_venue,
                field_type=p.field_type,
                native_or_proxy=p.native_or_proxy,
                proxy_for=p.proxy_for,
                processing_version=p.processing_version,
                dataset_version=p.dataset_version,
                retrieval_date=p.retrieval_date,
                dataset_span_start=span[0],
                dataset_span_end=span[1],
                data_start=window["data_start"],
                data_end=window["data_end"],
                eval_start=window.get("eval_start"),
                eval_end=window.get("eval_end"),
                symbols=tuple(window["symbols"]),
                symbol_mapping=p.symbol_mapping,
                content_hash=content_hash,
                content_hash_method=CONTENT_HASH_METHOD if content_hash is not None else None,
                provenance_notes=p.notes,
            )
        )
    return tuple(datasets)


def _check_window_containment(result: BacktestResult, datasets: tuple) -> None:
    """R§12.4 (blocking, BD7/M27) — BOTH bounds inclusive. A strict (`<`)
    implementation would make every real record raise (equity_curve has
    n_periods+1 elements and spans the frame inclusively on both ends)."""
    ec_index = result.equity_curve.index
    frame_start, frame_end = ec_index[0], ec_index[-1]
    for d in datasets:
        if d.field_type != "ohlcv":
            continue
        if not (d.data_start <= frame_start and d.data_end >= frame_end):
            raise ValidationError(
                f"dataset {d.dataset_id!r} does not contain the evaluated frame "
                f"[{frame_start!r}, {frame_end!r}] within [{d.data_start!r}, {d.data_end!r}] (R§12.4)"
            )
        if d.eval_start != frame_start or d.eval_end != frame_end:
            raise ValidationError(
                f"dataset {d.dataset_id!r} eval_start/eval_end MUST equal the equity curve's first/last "
                f"index ({frame_start!r}, {frame_end!r}) (R§12.4)"
            )
        if d.data_start > d.eval_start:
            raise ValidationError(f"dataset {d.dataset_id!r} data_start MUST be <= eval_start (R§12.4)")


def _result_warnings(result: BacktestResult, *, suppress_cagr: bool) -> tuple:
    """R§4.9 result-level closed vocabulary, derived purely from the result
    surface — never recomputed, only read off already-computed fields."""
    tokens = set()
    if result.ruined:
        tokens.add("RUINED")
    if result.leverage_breach:
        tokens.add("LEVERAGE_BREACH")
    if result.funding_gap_tolerance_suspicious:
        tokens.add("FUNDING_GAP_SUSPICIOUS")
    if not result.funding_modelled:
        tokens.add("FUNDING_NOT_MODELLED")
    if result.counterfactual_status != "COMPLETED":
        tokens.add(f"COUNTERFACTUAL_{result.counterfactual_status}")
    if result.unexecuted_rebalances:
        tokens.add(f"UNEXECUTED_REBALANCES:{len(result.unexecuted_rebalances)}")
    if not result.drag_comparable:
        tokens.add("DRAG_NOT_COMPARABLE")
    if suppress_cagr:
        tokens.add("CAGR_SUPPRESSED")
    return tuple(sorted(tokens))


def record_backtest_result(
    registry: ExperimentRegistry,
    result: BacktestResult,
    *,
    strategy: StrategyRef,
    dataset_windows: dict,
    universe_policy: str,
    code_identity: CodeIdentity,
    experiment_type: str,
    research_stage: str,
    reason_for_run: str,
    created_at: pd.Timestamp,
    run_executed_at: Optional[pd.Timestamp] = None,
    survivorship_safe=_UNSET,
    parent_experiment_id: Optional[str] = None,
    hypothesis_id: Optional[str] = None,
    change_from_parent: Optional[str] = None,
    frozen_spec_ref: Optional[str] = None,
    tags: tuple = (),
    notes: Optional[str] = None,
    artifacts: tuple = (),
    run_facts: Optional[dict] = None,
    custom: Optional[dict] = None,
    suppress_cagr: bool = False,
    search_space_id: Optional[str] = None,
    # R§21.7 (blocking) — REQUIRED, no default, tri-state (mirrors
    # `record_experiment`'s own parameter, which this forwards to verbatim):
    # this adapter registers exactly ONE `BacktestResult`, but it cannot
    # itself know whether the calling driver evaluated other configurations
    # and registered only this one (D14) — defaulting to `1` here would
    # silently reintroduce the identical "omission reads as a verified 1"
    # defect R§21.7 closes at the `record_experiment` layer, one call frame
    # up. The caller MUST state it explicitly.
    n_configs_evaluated: Optional[int],
    no_datasets_reason: Optional[str] = None,
) -> FoldedExperiment:
    """R§12 — the sole `BacktestResult` -> `ExperimentRecord` bridge.

    `no_datasets_reason` matters when `result.provenance` is genuinely empty
    (an `infrastructure`/`data_audit` run with no dataset provenance at
    all — R§4.4.3) — without threading it through, that combination was
    UNREACHABLE via the adapter even though `record_experiment` itself
    supports it directly.
    """
    datasets = _build_datasets(result, dataset_windows)

    derived_uses_proxy = any(d.native_or_proxy == "proxy" for d in datasets)
    if derived_uses_proxy != result.uses_proxy_data:
        raise ValidationError(
            f"derived uses_proxy_data ({derived_uses_proxy}) != result.uses_proxy_data "
            f"({result.uses_proxy_data}) (R§12.3)"
        )

    if survivorship_safe is _UNSET:
        survivorship_safe = result.survivorship_safe
    elif survivorship_safe != result.survivorship_safe:
        raise ValidationError(
            f"caller-supplied survivorship_safe ({survivorship_safe!r}) must match "
            f"result.survivorship_safe ({result.survivorship_safe!r}) (R§12.3)"
        )

    _check_window_containment(result, datasets)

    backtest_config = _backtest_config_dict(result.config)

    metrics = dict(result.metrics)
    if suppress_cagr:
        metrics["cagr_raw_suppressed"] = metrics.get("cagr")
        metrics["cagr"] = None

    results = ResultSummary(
        metrics=metrics,
        n_periods=len(result.net_return),
        rebalance_count=int(result.rebalance_flag.sum()),
        ruined=result.ruined,
        custom=dict(custom or {}),
        result_warnings=_result_warnings(result, suppress_cagr=suppress_cagr),
    )

    extra_warnings = ()
    if not result.provenance_complete:
        extra_warnings = ("PROVENANCE_INCOMPLETE",)

    return registry.record_experiment(
        experiment_type=experiment_type,
        research_stage=research_stage,
        reason_for_run=reason_for_run,
        code_identity=code_identity,
        datasets=datasets,
        universe_policy=universe_policy,
        survivorship_safe=survivorship_safe,
        strategy=strategy,
        backtest_config=backtest_config,
        status="COMPLETED",
        status_reason=None,
        results=results,
        run_facts=run_facts,
        artifacts=artifacts,
        parent_experiment_id=parent_experiment_id,
        hypothesis_id=hypothesis_id,
        change_from_parent=change_from_parent,
        frozen_spec_ref=frozen_spec_ref,
        tags=tags,
        notes=notes,
        no_datasets_reason=no_datasets_reason,
        created_at=created_at,
        run_executed_at=run_executed_at,
        search_space_id=search_space_id,
        n_configs_evaluated=n_configs_evaluated,
        _extra_warnings=extra_warnings,
        # R§21.1.2 (blocking) — the ONLY call site in the codebase permitted
        # to pass this: a record built from a real, cross-checked
        # `BacktestResult` is `recorded_via="adapter"`, never "manual". Trust
        # is conferred by object IDENTITY, not by a string a caller could
        # forge.
        capability=_ADAPTER_CAPABILITY,
    )


class _RunHandle:
    def __init__(self) -> None:
        self._result: Optional[BacktestResult] = None

    def set_result(self, result: BacktestResult) -> None:
        self._result = result


@contextlib.contextmanager
def record_run(
    registry: ExperimentRegistry,
    *,
    strategy: StrategyRef,
    universe_policy: str,
    code_identity: CodeIdentity,
    experiment_type: str,
    research_stage: str,
    reason_for_run: str,
    created_at: pd.Timestamp,
    run_executed_at: Optional[pd.Timestamp] = None,
    dataset_windows: Optional[dict] = None,
    datasets: tuple = (),
    backtest_config: Optional[dict] = None,
    survivorship_safe=_UNSET,
    no_datasets_reason: Optional[str] = None,
    parent_experiment_id: Optional[str] = None,
    hypothesis_id: Optional[str] = None,
    change_from_parent: Optional[str] = None,
    frozen_spec_ref: Optional[str] = None,
    tags: tuple = (),
    notes: Optional[str] = None,
    artifacts: tuple = (),
    run_facts: Optional[dict] = None,
    custom: Optional[dict] = None,
    suppress_cagr: bool = False,
    search_space_id: Optional[str] = None,
    # R§21.7 (blocking) — REQUIRED, no default, tri-state; forwarded
    # verbatim to `record_experiment`/`record_backtest_result` (see the
    # identical rationale on `record_backtest_result` above).
    n_configs_evaluated: Optional[int],
    manual_results_justification: Optional[str] = None,
):
    """R§11.1/R§20.2 (blocking, MW15/BI-1/BI-2) — a run inside this block is
    ALWAYS registered:

    - `COMPLETED` from `run.set_result(result)` on normal exit (via
      `record_backtest_result`, `recorded_via="adapter"`).
    - `INVALID` (R§20.2.4) if the block exits normally WITHOUT ever calling
      `run.set_result(...)` — never a bare raise, which under v1.1 produced
      ZERO records and no complaint.
    - `FAILED` (with the exception's type+message as `status_reason` and the
      traceback tail in `run_facts`) on ANY `BaseException` (R§20.2.3, not
      just `Exception`) — `KeyboardInterrupt`/`SystemExit` get
      `status_reason = "ABORTED: <ExcType>: <msg>"`. Measured under v1.1:
      Ctrl-C and `sys.exit()` each produced zero records and no complaint —
      the accidental survivorship path this closes. Always re-raised.

    On the exception/no-result paths there is no `BacktestResult`, so
    `datasets` cannot be derived from `result.provenance` (R§12.1's mechanism
    does not exist yet) — the caller MAY pre-supply `datasets`/
    `backtest_config` directly (as `record_experiment` itself accepts) for
    the failure narrative (R§20.3.4/D12). These branches are always
    `recorded_via="manual"` — there is no `BacktestResult` to cross-check
    against.
    """
    run = _RunHandle()
    try:
        yield run
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            status_reason = f"ABORTED: {type(exc).__name__}: {exc}"
        else:
            status_reason = f"{type(exc).__name__}: {exc}"
        tb_tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
        merged_facts = dict(run_facts or {})
        merged_facts["traceback_tail"] = tb_tail
        registry.record_experiment(
            experiment_type=experiment_type,
            research_stage=research_stage,
            reason_for_run=reason_for_run,
            code_identity=code_identity,
            datasets=datasets,
            universe_policy=universe_policy,
            survivorship_safe=None if survivorship_safe is _UNSET else survivorship_safe,
            strategy=strategy,
            backtest_config=dict(backtest_config or {}),
            status="FAILED",
            status_reason=status_reason,
            results=None,
            run_facts=merged_facts,
            artifacts=artifacts,
            parent_experiment_id=parent_experiment_id,
            hypothesis_id=hypothesis_id,
            change_from_parent=change_from_parent,
            frozen_spec_ref=frozen_spec_ref,
            tags=tags,
            notes=notes,
            no_datasets_reason=no_datasets_reason,
            created_at=created_at,
            run_executed_at=run_executed_at,
            search_space_id=search_space_id,
            n_configs_evaluated=n_configs_evaluated,
            # `capability` omitted (defaults to `None`) — recorded_via="manual"
            # (R§21.1.2): there is no `BacktestResult` here to cross-check.
        )
        raise
    else:
        if run._result is None:
            # R§20.2.4 (blocking) — register INVALID, do NOT merely raise.
            # Measured under v1.1: catching the exception INSIDE the block
            # and complaining OUTSIDE it produced zero records; a run that
            # silently never calls set_result() must still leave a
            # permanent, visible trace, exactly like every other failure
            # mode this registry exists to make undeniable.
            registry.record_experiment(
                experiment_type=experiment_type,
                research_stage=research_stage,
                reason_for_run=reason_for_run,
                code_identity=code_identity,
                datasets=datasets,
                universe_policy=universe_policy,
                survivorship_safe=None if survivorship_safe is _UNSET else survivorship_safe,
                strategy=strategy,
                backtest_config=dict(backtest_config or {}),
                status="INVALID",
                status_reason="NO_RESULT: run_and_register/set_result was never called",
                results=None,
                run_facts=dict(run_facts or {}),
                artifacts=artifacts,
                parent_experiment_id=parent_experiment_id,
                hypothesis_id=hypothesis_id,
                change_from_parent=change_from_parent,
                frozen_spec_ref=frozen_spec_ref,
                tags=tags,
                notes=notes,
                no_datasets_reason=no_datasets_reason,
                created_at=created_at,
                run_executed_at=run_executed_at,
                search_space_id=search_space_id,
                n_configs_evaluated=n_configs_evaluated,
                # `capability` omitted -- recorded_via="manual" (R§21.1.2).
            )
            return
        record_backtest_result(
            registry,
            run._result,
            strategy=strategy,
            dataset_windows=dataset_windows or {},
            universe_policy=universe_policy,
            code_identity=code_identity,
            experiment_type=experiment_type,
            research_stage=research_stage,
            reason_for_run=reason_for_run,
            created_at=created_at,
            run_executed_at=run_executed_at,
            survivorship_safe=survivorship_safe,
            parent_experiment_id=parent_experiment_id,
            hypothesis_id=hypothesis_id,
            change_from_parent=change_from_parent,
            frozen_spec_ref=frozen_spec_ref,
            tags=tags,
            notes=notes,
            no_datasets_reason=no_datasets_reason,
            artifacts=artifacts,
            run_facts=run_facts,
            custom=custom,
            suppress_cagr=suppress_cagr,
            search_space_id=search_space_id,
            n_configs_evaluated=n_configs_evaluated,
        )


def run_and_register(
    registry: ExperimentRegistry,
    config: BacktestConfig,
    market_data,
    strategy_output,
    *,
    record_kwargs: dict,
    **run_kwargs,
) -> BacktestResult:
    """R§20.2.1 (blocking) — the ONLY sanctioned way a driver obtains a
    `BacktestResult`: calls `run_backtest(config, market_data,
    strategy_output, **run_kwargs)`, then registers via
    `record_backtest_result` (through `record_run`, so a `BaseException`
    during `run_backtest` itself is ALSO captured as a `FAILED` record and
    re-raised — R§20.2.3/R§20.2.4 apply here too, not just to a hand-written
    `with record_run(...)` block).

    `record_kwargs` is passed through to `record_run` verbatim (everything
    `record_run` accepts EXCEPT `dataset_windows`, which is threaded through
    to `record_backtest_result` on success). Returns the `BacktestResult` so
    the driver can still use it (e.g. to print a summary) — registration has
    already happened by the time this returns.
    """
    dataset_windows = record_kwargs.pop("dataset_windows", None)
    with record_run(registry, dataset_windows=dataset_windows, **record_kwargs) as run:
        result = run_backtest(config, market_data, strategy_output, **run_kwargs)
        run.set_result(result)
    return result
