"""R§17 -- QR-SMOKE-001 retrospective registration.

Re-executes the frozen QR-SMOKE-001 pipeline OFFLINE (R§17.1) and registers
exactly five ExperimentRecords: Window A, a Window A REPRODUCIBILITY rerun
(subprocess, different PYTHONHASHSEED -- R§17.3/R§6.3.1), Window B1, Window
B2, and Window B2-PRE (a genuine, MEASURED `FAILED` run -- R§17.4).

Usage:
    .venv/bin/python -m experiments.registry_migration.register_qr_smoke_001

Offline only (R§17.1 / work-order item F): every provider below is
constructed `offline=True`; this module performs no network call, asserted
by `tests/registry/test_migration.py`'s static scan + monkeypatched network
guard. Re-running this script a second time will fail on the R§8.2
write-once guard (by design -- registration is itself an experiment record,
never silently overwritten); that is expected and not a defect.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from backtest.models import DataIntegrityError  # noqa: E402
from data import storage  # noqa: E402
from data.binance.provider import BinanceUMProvider  # noqa: E402
from data.provenance import PROCESSING_VERSION  # noqa: E402
from experiments.qr_smoke_001 import pipeline as P  # noqa: E402
from registry.backtest_adapter import _backtest_config_dict, record_backtest_result  # noqa: E402
from registry.codeid import capture_code_identity  # noqa: E402
from registry.datahash import CONTENT_HASH_METHOD, hash_dataframe_content  # noqa: E402
from registry.models import ArtifactRef, CodeIdentity, DatasetRef, SCHEMA_VERSION, StrategyRef  # noqa: E402
from registry.serialize import decode, stored_json, strict_json_loads  # noqa: E402
from registry.store import ExperimentRegistry  # noqa: E402

BASE_DIR = str(REPO_ROOT / "data")


def _resolve_registry_root() -> Path:
    """R§20.8.2 (blocking, BD-A3) — the registry root MUST be accepted from
    argv/env, so NO invocation of this script -- direct, or the R§17.3
    rerun's `subprocess.run([sys.executable, "-m", ...])` re-import of this
    module in a FRESH process -- can ever silently fall back to writing into
    the real `experiments/registry/`. Measured under v1.1: the test's
    monkeypatch covered the parent process only; the child subprocess
    re-imported the module with the real module-level globals and wrote two
    stray dataset-snapshot parquets into the real registry's artifacts dir.

    Resolution order: `--registry-root PATH` (argv) > `QR_REGISTRY_ROOT`
    (env) > the real default. `main()` additionally sets `QR_REGISTRY_ROOT`
    in `os.environ` unconditionally (whichever source it came from) BEFORE
    spawning the R§17.3 rerun subprocess, so the child -- which re-imports
    this module fresh and re-runs this same resolution -- always agrees with
    the parent, regardless of which source (argv/env/default) the parent
    itself used.
    """
    if "--registry-root" in sys.argv:
        idx = sys.argv.index("--registry-root")
        return Path(sys.argv[idx + 1]).resolve()
    env_val = os.environ.get("QR_REGISTRY_ROOT")
    if env_val:
        return Path(env_val).resolve()
    return REPO_ROOT / "experiments" / "registry"


REGISTRY_ROOT = _resolve_registry_root()
ARTIFACTS_ROOT = REGISTRY_ROOT / "artifacts"
DATASET_SNAPSHOT_DIR = ARTIFACTS_ROOT / "datasets"

CONTRACT_VERSIONS = {
    "backtest_contract": "1.5.1",
    "data_contract": "1.4",
    "registry_schema": SCHEMA_VERSION,
    "data_processing_version": PROCESSING_VERSION,
}

STRATEGY = StrategyRef(
    name="qr_smoke_001",
    version="1.0",
    params={"sma_window": P.SMA_WINDOW},
    frequency=P.FREQUENCY,
    target_execution_venue=P.TARGET_EXECUTION_VENUE,
)

# R§17.3 -- record 2 MUST be executed in a subprocess with a DIFFERENT
# PYTHONHASHSEED than an ordinary invocation. The parent's own ambient
# PYTHONHASHSEED is whatever the caller's shell has (usually unset/random);
# the child is forced to this fixed, distinctive value so the two are
# provably different regardless of the parent's own setting.
_CHILD_PYTHONHASHSEED = "918273645"
_CHILD_ARG = "--child-window-a-rerun"


# ---------------------------------------------------------------------------
# R§17.2 -- dataset content hashing + snapshot preservation
# ---------------------------------------------------------------------------


def _snapshot_dataset(df) -> tuple:
    """Hashes the full persisted dataframe (col-buffer-v1, R§7.3) and copies
    it to `experiments/registry/artifacts/datasets/<content_hash>.parquet`
    (R§17.2) -- the ONLY durable copy of the bytes actually used, since the
    Hyperliquid candleSnapshot is a rolling ~208-day window."""
    content_hash = hash_dataframe_content(df)
    DATASET_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATASET_SNAPSHOT_DIR / f"{content_hash}.parquet"
    if not dest.exists():
        df.to_parquet(dest, index=False)
    return content_hash, dest


def _hl_ohlcv_content_hash():
    return _snapshot_dataset(storage.read_ohlcv_parquet(BASE_DIR, P.FREQUENCY, P.SYMBOL))


def _hl_funding_content_hash():
    return _snapshot_dataset(storage.read_funding_parquet(BASE_DIR, P.SYMBOL))


def _binance_ohlcv_content_hash():
    return _snapshot_dataset(storage.read_binance_ohlcv_parquet(BASE_DIR, P.SYMBOL))


def _artifact_for_path(name: str, kind: str, path: Path, description=None) -> ArtifactRef:
    data = path.read_bytes()
    relpath = path.resolve().relative_to(REPO_ROOT).as_posix()
    return ArtifactRef(
        name=name,
        kind=kind,
        path=relpath,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        recorded_at=pd.Timestamp.now(tz="UTC"),
        description=description,
    )


def _write_equity_curve_artifact(run, name: str) -> Path:
    eq_dir = ARTIFACTS_ROOT / "equity_curves"
    eq_dir.mkdir(parents=True, exist_ok=True)
    path = eq_dir / f"{name}.json"
    series = run.result.equity_curve
    payload = {"index": [ts.isoformat() for ts in series.index], "values": [float(v) for v in series.values]}
    path.write_text(json.dumps(payload, sort_keys=True))
    return path


# ---------------------------------------------------------------------------
# Shared payload builders (R§17.5)
# ---------------------------------------------------------------------------


def _notes(window_name: str) -> str:
    # R§17.1 -- the three MANDATED statements, verbatim in spirit -- plus the
    # R§20.10 mandated disclosure that an earlier, unverifiable registration
    # attempt (v1.1, col-buffer-v1 content hashes, code_fingerprint matching
    # no extant state) was discarded before any commit and is not silently
    # being overwritten.
    return (
        f"QR-SMOKE-001 {window_name}. (1) This record was created by RE-EXECUTING the frozen "
        f"QR-SMOKE-001 pipeline (experiments/qr_smoke_001/pipeline.py) OFFLINE at registration "
        f"time, 2026-08-17. (2) The ORIGINAL {window_name} execution's git commit and worktree "
        f"state (from 2026-08-16/17) are NOT recoverable and are NOT claimed by this record -- "
        f"the code identity recorded here is that of the RE-EXECUTION. (3) Hyperliquid "
        f"candleSnapshot serves a rolling ~208-day window, so the persisted local parquet "
        f"snapshot is the data of record; retrieval_date, processing_version and dataset span "
        f"come from the persisted provenance sidecar, never re-fetched. (4) R§20.10: an earlier "
        f"v1.1 registration attempt (5 records, content_hash method col-buffer-v1) was discarded "
        f"before any commit and re-executed under v1.2/col-buffer-v2 -- the v1.1 records were "
        f"never committed, contained no research observation (experiment_type=pipeline_validation), "
        f"and their code_fingerprint matched no state that existed anywhere (R§20.7.3/R§20.10)."
    )


def _custom_for(run) -> dict:
    r = run.result
    return {
        "funding_events_excluded": r.funding_events_excluded,
        "funding_gap_tolerance_suspicious": r.funding_gap_tolerance_suspicious,
        "max_gross_exposure": float(r.gross_exposure.max()) if len(r.gross_exposure) else None,
        "n_unexecuted_rebalances": len(r.unexecuted_rebalances),
        "total_drag_return": r.total_drag_return,
        "drag_comparable": r.drag_comparable,
        "counterfactual_status": r.counterfactual_status,
        "first_frame_signal": run.first_frame_signal,
    }


def _runtime_env() -> dict:
    """R§20.9.2 (DEFERRED-005's companion, adopted now) -- cheap, no
    identity impact (run_facts is neither hashed nor part of the R§5.5
    reproducibility comparison, R§4.8)."""
    import platform

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
    }


def _run_facts_for(run, *, intended_eval_start, intended_eval_end) -> dict:
    return {
        "n_raw_bars": len(run.raw_index),
        "n_frame_bars": len(run.frame_index),
        "intended_eval_start": intended_eval_start,
        "intended_eval_end": intended_eval_end,
        "offline": True,
        "runtime_env": _runtime_env(),
    }


def _ohlcv_window(run, content_hash) -> dict:
    return {
        "data_start": run.raw_index[0],
        "data_end": run.raw_index[-1],
        "eval_start": run.frame_index[0],
        "eval_end": run.frame_index[-1],
        "symbols": ("BTC",),
        "content_hash": content_hash,
    }


def _funding_window(funding_events, content_hash) -> dict:
    ts = sorted(e.timestamp for e in funding_events)
    return {
        "data_start": ts[0],
        "data_end": ts[-1],
        "eval_start": None,
        "eval_end": None,
        "symbols": ("BTC",),
        "content_hash": content_hash,
    }


# ---------------------------------------------------------------------------
# Record 1 -- Window A
# ---------------------------------------------------------------------------


def register_window_a(reg, code_identity, hl_ohlcv_hash, hl_ohlcv_path, hl_funding_hash, hl_funding_path, funding_events):
    run = P.run_window_a(base_dir=BASE_DIR)
    dataset_windows = {
        "hyperliquid.ohlcv.1h.BTC": _ohlcv_window(run, hl_ohlcv_hash),
        "hyperliquid.funding.BTC": _funding_window(funding_events, hl_funding_hash),
    }
    artifacts = (
        _artifact_for_path("hl_ohlcv_snapshot", "dataset_snapshot", hl_ohlcv_path, "Hyperliquid BTC 1h OHLCV, full persisted span"),
        _artifact_for_path("hl_funding_snapshot", "dataset_snapshot", hl_funding_path, "Hyperliquid BTC funding, full persisted span"),
        _artifact_for_path("equity_curve", "equity_curve", _write_equity_curve_artifact(run, "window_a")),
    )
    fe = record_backtest_result(
        reg,
        run.result,
        strategy=STRATEGY,
        dataset_windows=dataset_windows,
        universe_policy="single_symbol_fixed:BTC",
        code_identity=code_identity,
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run="R§17 retrospective registration of QR-SMOKE-001 Window A",
        created_at=pd.Timestamp.now(tz="UTC"),
        run_executed_at=pd.Timestamp.now(tz="UTC"),
        notes=_notes("Window A"),
        custom=_custom_for(run),
        run_facts=_run_facts_for(run, intended_eval_start=run.frame_index[0], intended_eval_end=run.frame_index[-1]),
        artifacts=artifacts,
        tags=("qr_smoke_001", "window_a"),
        # R§21.7.5 -- each QR-SMOKE-001 window is genuinely ONE evaluated
        # configuration (no in-process sweep), so `1` is a VERIFIED count,
        # not a default standing in for UNKNOWN.
        n_configs_evaluated=1,
    )
    return fe, run


# ---------------------------------------------------------------------------
# Record 2 -- Window A rerun, subprocess, different PYTHONHASHSEED
# ---------------------------------------------------------------------------


def _child_window_a_rerun_main() -> None:
    """R§17.3/R§6.3.1/R§20.3.1 — runs in the CHILD subprocess (different
    PYTHONHASHSEED). Transmits the REAL `BacktestResult` object (pickled,
    base64-encoded) back to the parent, rather than reconstructing a
    `ResultSummary` by hand from JSON — so the parent can register it via
    `record_backtest_result` exactly as it does for Window A itself, getting
    `recorded_via="adapter"` (not "manual") and the full R§12.3/R§12.4
    cross-checks. This also preserves R§6.3.1's `exact_hash` equality between
    records 1 and 2: `recorded_via` is now hashed into `semantic_hash`
    (R§20.3.1), so if this rerun were instead registered via a direct
    `record_experiment(...)` call with no adapter capability (as v1.1 did
    via a public `_recorded_via="manual"` keyword, since removed by R§21.1),
    it would get a DIFFERENT `exact_hash` from Window A purely because of HOW it
    was recorded — defeating the very reproducibility demonstration R§17.3
    exists to provide. Pickle is safe here: both processes run the identical
    installed code (same commit/fingerprint) and the payload never leaves
    this machine.
    """
    code_identity = capture_code_identity(REPO_ROOT, contract_versions=CONTRACT_VERSIONS)
    run = P.run_window_a(base_dir=BASE_DIR)
    hl_ohlcv_hash, _ = _hl_ohlcv_content_hash()
    hl_funding_hash, _ = _hl_funding_content_hash()
    funding_events, _ = P.load_full_hl_funding(BASE_DIR)
    dataset_windows = {
        "hyperliquid.ohlcv.1h.BTC": _ohlcv_window(run, hl_ohlcv_hash),
        "hyperliquid.funding.BTC": _funding_window(funding_events, hl_funding_hash),
    }
    payload = {
        "code_identity": code_identity.to_dict(),
        "result_pickle_b64": base64.b64encode(pickle.dumps(run.result)).decode("ascii"),
        "dataset_windows_pickle_b64": base64.b64encode(pickle.dumps(dataset_windows)).decode("ascii"),
        "custom": _custom_for(run),
        "run_facts": _run_facts_for(run, intended_eval_start=run.frame_index[0], intended_eval_end=run.frame_index[-1]),
        "child_pythonhashseed": os.environ.get("PYTHONHASHSEED"),
    }
    sys.stdout.write(stored_json(payload))


def register_window_a_rerun(reg, *, parent_pythonhashseed):
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = _CHILD_PYTHONHASHSEED
    out = subprocess.run(
        [sys.executable, "-m", "experiments.registry_migration.register_qr_smoke_001", _CHILD_ARG],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = decode(strict_json_loads(out.stdout))
    assert payload["child_pythonhashseed"] == _CHILD_PYTHONHASHSEED
    assert payload["child_pythonhashseed"] != parent_pythonhashseed, (
        "R§17.3 requires the rerun subprocess's PYTHONHASHSEED to differ from the parent process's"
    )
    code_identity = CodeIdentity.from_dict(payload["code_identity"])
    result = pickle.loads(base64.b64decode(payload["result_pickle_b64"]))
    dataset_windows = pickle.loads(base64.b64decode(payload["dataset_windows_pickle_b64"]))
    fe = record_backtest_result(
        reg,
        result,
        strategy=STRATEGY,
        dataset_windows=dataset_windows,
        universe_policy="single_symbol_fixed:BTC",
        code_identity=code_identity,
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run=(
            "R§17 retrospective registration of the QR-SMOKE-001 Window A REPRODUCIBILITY "
            "rerun (R§17.3: executed in a subprocess with a different PYTHONHASHSEED)"
        ),
        created_at=pd.Timestamp.now(tz="UTC"),
        run_executed_at=pd.Timestamp.now(tz="UTC"),
        notes=_notes("Window A rerun"),
        custom=payload["custom"],
        run_facts={**payload["run_facts"], "parent_pythonhashseed": parent_pythonhashseed, "child_pythonhashseed": payload["child_pythonhashseed"]},
        tags=("qr_smoke_001", "window_a", "rerun"),
        n_configs_evaluated=1,
    )
    return fe


# ---------------------------------------------------------------------------
# Record 3 -- Window B1
# ---------------------------------------------------------------------------


def register_window_b1(reg, code_identity, binance_hash, binance_path, parent_id):
    run = P.run_window_b1(base_dir=BASE_DIR)
    dataset_windows = {"binance.ohlcv.1h.BTC": _ohlcv_window(run, binance_hash)}
    artifacts = (
        _artifact_for_path("binance_ohlcv_snapshot", "dataset_snapshot", binance_path, "Binance USDⓂ-M BTC 1h OHLCV, full persisted span"),
        _artifact_for_path("equity_curve", "equity_curve", _write_equity_curve_artifact(run, "window_b1")),
    )
    fe = record_backtest_result(
        reg,
        run.result,
        strategy=STRATEGY,
        dataset_windows=dataset_windows,
        universe_policy="single_symbol_fixed:BTC",
        code_identity=code_identity,
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run="R§17 retrospective registration of QR-SMOKE-001 Window B1",
        created_at=pd.Timestamp.now(tz="UTC"),
        run_executed_at=pd.Timestamp.now(tz="UTC"),
        notes=_notes("Window B1"),
        custom=_custom_for(run),
        # R§20.8.9 (MW-A6, blocking) — MUST be the EVALUATED-FRAME start,
        # not the raw window start: v1.1 passed `P.WINDOW_B1_RAW_START`
        # (2020-01-01, the RAW load boundary), but the actual evaluated
        # frame starts (SMA_WINDOW-1) bars later
        # (`experiments/qr_smoke_001/pipeline.py:324`). `run.frame_index`
        # is the ground truth for every window, so it is used uniformly here
        # (and for Windows A/B2 above/below) rather than a hand-picked
        # per-window constant that can silently drift out of sync.
        run_facts=_run_facts_for(run, intended_eval_start=run.frame_index[0], intended_eval_end=run.frame_index[-1]),
        artifacts=artifacts,
        parent_experiment_id=parent_id,
        change_from_parent=(
            "Binance USDⓂ-M proxy prices for full 2020-2026 history; funding_mode=disabled "
            "-- Hyperliquid did not exist for most of the window, so there is no funding to charge"
        ),
        tags=("qr_smoke_001", "window_b1"),
        n_configs_evaluated=1,
    )
    return fe, run


# ---------------------------------------------------------------------------
# Record 4 -- Window B2
# ---------------------------------------------------------------------------


def register_window_b2(reg, code_identity, binance_hash, binance_path, hl_funding_hash, hl_funding_path, funding_events, parent_id):
    run = P.run_window_b2(base_dir=BASE_DIR)
    dataset_windows = {
        "binance.ohlcv.1h.BTC": _ohlcv_window(run, binance_hash),
        "hyperliquid.funding.BTC": _funding_window(funding_events, hl_funding_hash),
    }
    artifacts = (
        _artifact_for_path("binance_ohlcv_snapshot", "dataset_snapshot", binance_path, "Binance USDⓂ-M BTC 1h OHLCV, full persisted span"),
        _artifact_for_path("hl_funding_snapshot", "dataset_snapshot", hl_funding_path, "Hyperliquid BTC funding, full persisted span"),
        _artifact_for_path("equity_curve", "equity_curve", _write_equity_curve_artifact(run, "window_b2")),
    )
    fe = record_backtest_result(
        reg,
        run.result,
        strategy=STRATEGY,
        dataset_windows=dataset_windows,
        universe_policy="single_symbol_fixed:BTC",
        code_identity=code_identity,
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run="R§17 retrospective registration of QR-SMOKE-001 Window B2",
        created_at=pd.Timestamp.now(tz="UTC"),
        run_executed_at=pd.Timestamp.now(tz="UTC"),
        notes=_notes("Window B2"),
        custom=_custom_for(run),
        run_facts=_run_facts_for(run, intended_eval_start=run.frame_index[0], intended_eval_end=run.frame_index[-1]),
        artifacts=artifacts,
        parent_experiment_id=parent_id,
        change_from_parent=(
            "funding re-enabled with Hyperliquid-native funding over the contiguous-coverage "
            "window starting 2024-08-15"
        ),
        tags=("qr_smoke_001", "window_b2"),
        n_configs_evaluated=1,
    )
    return fe, run


# ---------------------------------------------------------------------------
# Record 5 -- Window B2-PRE (a genuine, measured FAILED run, R§17.4)
# ---------------------------------------------------------------------------


def register_window_b2_pre(reg, code_identity, binance_hash, binance_path, hl_funding_hash, hl_funding_path, funding_events, funding_coverage, parent_id):
    binance = BinanceUMProvider(offline=True, storage_base_dir=BASE_DIR)
    eval_start = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    eval_end = P.WINDOW_B2_EVAL_END
    raw_start, raw_end = P.eval_window_to_raw_window(eval_start, eval_end)
    raw_md = P.load_raw_market_data(binance, P.SYMBOL, raw_start, raw_end)
    frame_md = P.slice_evaluated_frame(raw_md)
    frame_index = frame_md.open.index
    T0, T_last = frame_index[0], frame_index[-1]

    # R§17.4 -- the REAL driver's own pre-flight check. Not bypassed to
    # manufacture the engine's FundingDataError instead (that path is never
    # actually run by this pipeline).
    try:
        P.assert_single_funding_coverage_record(funding_coverage, P.SYMBOL, T0, T_last)
        raise AssertionError(
            "R§17.4 expected the B2-PRE funding-coverage pre-flight to fail, but it succeeded "
            "-- the underlying Hyperliquid funding coverage data has changed since the spec was measured"
        )
    except DataIntegrityError as exc:
        status_reason = f"{type(exc).__name__}: {exc}"

    binance_prov = P.binance_ohlcv_provenance(BASE_DIR)
    hl_funding_prov = P.hl_funding_provenance(BASE_DIR)
    funding_ts_sorted = sorted(e.timestamp for e in funding_events)

    datasets = (
        DatasetRef(
            dataset_id=binance_prov.dataset_id,
            source_venue=binance_prov.source_venue,
            field_type=binance_prov.field_type,
            native_or_proxy=binance_prov.native_or_proxy,
            proxy_for=binance_prov.proxy_for,
            processing_version=binance_prov.processing_version,
            dataset_version=binance_prov.dataset_version,
            retrieval_date=binance_prov.retrieval_date,
            dataset_span_start=binance_prov.time_range[0],
            dataset_span_end=binance_prov.time_range[1],
            data_start=raw_md.open.index[0],
            data_end=raw_md.open.index[-1],
            eval_start=frame_index[0],
            eval_end=frame_index[-1],
            symbols=("BTC",),
            symbol_mapping=binance_prov.symbol_mapping,
            content_hash=binance_hash,
            content_hash_method=CONTENT_HASH_METHOD,
            provenance_notes=binance_prov.notes,
        ),
        DatasetRef(
            dataset_id=hl_funding_prov.dataset_id,
            source_venue=hl_funding_prov.source_venue,
            field_type=hl_funding_prov.field_type,
            native_or_proxy=hl_funding_prov.native_or_proxy,
            proxy_for=hl_funding_prov.proxy_for,
            processing_version=hl_funding_prov.processing_version,
            dataset_version=hl_funding_prov.dataset_version,
            retrieval_date=hl_funding_prov.retrieval_date,
            dataset_span_start=hl_funding_prov.time_range[0],
            dataset_span_end=hl_funding_prov.time_range[1],
            data_start=funding_ts_sorted[0],
            data_end=funding_ts_sorted[-1],
            eval_start=None,
            eval_end=None,
            symbols=("BTC",),
            symbol_mapping=hl_funding_prov.symbol_mapping,
            content_hash=hl_funding_hash,
            content_hash_method=CONTENT_HASH_METHOD,
            provenance_notes=hl_funding_prov.notes,
        ),
    )

    # R§17.4 -- the underlying MEASURED data fact, recorded permanently as a
    # hard data boundary, never re-attempted.
    gaps = [t2 - t1 for t1, t2 in zip(funding_ts_sorted, funding_ts_sorted[1:])]
    tolerance = pd.Timedelta(minutes=90)
    n_gaps_over_tolerance = sum(1 for g in gaps if g > tolerance)
    last_segment_start = max((c.coverage_start for c in funding_coverage), default=None)

    run_facts = {
        "n_raw_bars": len(raw_md.open.index),
        "n_frame_bars": len(frame_index),
        "intended_eval_start": eval_start,
        "intended_eval_end": eval_end,
        "offline": True,
        "runtime_env": _runtime_env(),
        "measured_funding_event_count": len(funding_events),
        "measured_funding_span_start": funding_ts_sorted[0],
        "measured_funding_span_end": funding_ts_sorted[-1],
        "measured_n_gaps_over_90min_tolerance": n_gaps_over_tolerance,
        "measured_n_coverage_segments": len(funding_coverage),
        "measured_last_coverage_segment_start": last_segment_start,
    }

    config_obj = P.build_config(funding_mode="required", funding_notional_basis="period_start")
    backtest_config = _backtest_config_dict(config_obj)

    fe = reg.record_experiment(
        experiment_type="pipeline_validation",
        research_stage="exploratory",
        reason_for_run="R§17 retrospective registration of QR-SMOKE-001 Window B2-PRE (measured pre-flight failure)",
        code_identity=code_identity,
        datasets=datasets,
        universe_policy="single_symbol_fixed:BTC",
        survivorship_safe=P.build_universe_provenance().survivorship_safe,
        strategy=STRATEGY,
        backtest_config=backtest_config,
        status="FAILED",
        status_reason=status_reason,
        results=None,
        run_facts=run_facts,
        parent_experiment_id=parent_id,
        change_from_parent=(
            "same configuration as B2 with the evaluated frame starting 2024-01-01, before "
            "contiguous Hyperliquid funding coverage"
        ),
        notes=_notes("Window B2-PRE"),
        created_at=pd.Timestamp.now(tz="UTC"),
        run_executed_at=pd.Timestamp.now(tz="UTC"),
        tags=("qr_smoke_001", "window_b2_pre", "failed"),
        n_configs_evaluated=1,
    )
    return fe


# ---------------------------------------------------------------------------
# R§13.4 -- the six mandated query demonstrations
# ---------------------------------------------------------------------------


def run_query_demonstrations(reg, fe1) -> str:
    lines = []
    demo1 = reg.find_experiments(strategy_name="qr_smoke_001")
    lines.append("1. find_experiments(strategy_name='qr_smoke_001'): " + ", ".join(sorted(fe.record.experiment_id for fe in demo1)))
    demo2 = reg.failed_or_rejected()
    lines.append("2. failed_or_rejected(): " + ", ".join(sorted(fe.record.experiment_id for fe in demo2)))
    demo3 = reg.find_experiments(dataset_id="binance.ohlcv.1h.BTC")
    lines.append("3. find_experiments(dataset_id='binance.ohlcv.1h.BTC'): " + ", ".join(sorted(fe.record.experiment_id for fe in demo3)))
    demo4 = reg.children_of(fe1.record.experiment_id)
    lines.append(f"4. children_of({fe1.record.experiment_id!r}): " + ", ".join(sorted(fe.record.experiment_id for fe in demo4)))
    demo5 = reg.find_experiments(funding_disabled=True)
    lines.append("5. find_experiments(funding_disabled=True): " + ", ".join(sorted(fe.record.experiment_id for fe in demo5)))
    demo6a = reg.exact_rerun_groups()
    demo6b = reg.semantic_duplicates()
    lines.append("6a. exact_rerun_groups(): " + json.dumps({k: list(v) for k, v in sorted(demo6a.items())}, sort_keys=True))
    lines.append("6b. semantic_duplicates(): " + json.dumps({k: list(v) for k, v in sorted(demo6b.items())}, sort_keys=True))
    text = "\n".join(lines) + "\n"
    ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS_ROOT / "query_demo.txt").write_text(text)
    return text


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    # R§20.8.2 (blocking) -- pin QR_REGISTRY_ROOT in THIS process's
    # environment (whichever source -- argv/env/default -- REGISTRY_ROOT
    # itself came from), so the R§17.3 rerun subprocess -- which re-imports
    # this module fresh via `env=dict(os.environ)` -- resolves the IDENTICAL
    # root rather than silently falling back to the real registry.
    os.environ["QR_REGISTRY_ROOT"] = str(REGISTRY_ROOT)
    print(f"registry root: {REGISTRY_ROOT}")
    REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
    reg = ExperimentRegistry(REGISTRY_ROOT, repo_root=REPO_ROOT)
    # R§6.3.1 -- captured ONCE, before any record is written, reused for
    # every record this (parent) process produces.
    code_identity = capture_code_identity(REPO_ROOT, contract_versions=CONTRACT_VERSIONS)
    parent_pythonhashseed = os.environ.get("PYTHONHASHSEED")

    hl_ohlcv_hash, hl_ohlcv_path = _hl_ohlcv_content_hash()
    hl_funding_hash, hl_funding_path = _hl_funding_content_hash()
    binance_hash, binance_path = _binance_ohlcv_content_hash()
    funding_events, funding_coverage = P.load_full_hl_funding(BASE_DIR)

    fe1, _run_a = register_window_a(reg, code_identity, hl_ohlcv_hash, hl_ohlcv_path, hl_funding_hash, hl_funding_path, funding_events)
    fe2 = register_window_a_rerun(reg, parent_pythonhashseed=parent_pythonhashseed)
    assert fe1.record.exact_hash == fe2.record.exact_hash, "R§6.3.1: records 1 and 2 MUST share exact_hash"

    fe3, _run_b1 = register_window_b1(reg, code_identity, binance_hash, binance_path, parent_id=fe1.record.experiment_id)
    fe4, _run_b2 = register_window_b2(reg, code_identity, binance_hash, binance_path, hl_funding_hash, hl_funding_path, funding_events, parent_id=fe3.record.experiment_id)
    fe5 = register_window_b2_pre(reg, code_identity, binance_hash, binance_path, hl_funding_hash, hl_funding_path, funding_events, funding_coverage, parent_id=fe4.record.experiment_id)

    query_demo_text = run_query_demonstrations(reg, fe1)

    print(query_demo_text)
    print("experiment_ids:")
    print("  1 Window A      :", fe1.record.experiment_id)
    print("  2 Window A rerun:", fe2.record.experiment_id, "reproducibility_status=", fe2.reproducibility_status)
    print("  3 Window B1     :", fe3.record.experiment_id)
    print("  4 Window B2     :", fe4.record.experiment_id)
    print("  5 Window B2-PRE :", fe5.record.experiment_id, "status=", fe5.status, "status_reason=", fe5.record.status_reason)
    print("verify_registry():", reg.verify_registry())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == _CHILD_ARG:
        _child_window_a_rerun_main()
    else:
        main()
