#!/usr/bin/env python3
"""QR-PREP-001 P§5 — seal the ONE-TIME bootstrap protected-OOS reserve (OOS-001).

Implements the M§9.1.6 ordering exactly: define intervals -> persist immutable
sliced snapshots -> record provenance+hashes -> run the M§9.1.2 scan -> write
`research/oos/protected_windows.json`.

Refuses to run twice (M§9.1.6 "the bootstrap is one-time") and refuses if any
`alpha_research` record exists (M§9.1.7, blocking).

Hashing follows M§9.9.1: `hash_dataframe_content` over the canonical normalized
LONG-FORM frame sliced to each dataset's own inclusive [covers_start,
covers_end] -- upstream of `to_engine_frame`, never a whole source file.
"""
from __future__ import annotations
import json, os, sys, datetime as dt
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data.storage import (read_binance_ohlcv_parquet, read_funding_parquet,
                          read_binance_provenance, read_provenance,
                          binance_ohlcv_dataset_id, funding_dataset_id)
from data.symbol_map import mapped_symbols
from registry.datahash import hash_dataframe_content
from registry.store import ExperimentRegistry

HASH_METHOD = "col-buffer-v2"
WINDOW_ID = "OOS-001"
# Option A, user-selected 2026-08-20.
DEPENDENCY_START   = pd.Timestamp("2025-02-01T00:00:00Z")
EVALUATION_START   = pd.Timestamp("2025-08-01T00:00:00Z")
EVALUATION_END     = pd.Timestamp("2026-07-31T23:00:00Z")
FUNDING_COVER_END  = pd.Timestamp("2026-08-01T01:00:00Z")  # >= eval_end + 1 funding interval + jitter

OOS_DIR   = ROOT / "research" / "oos"
SNAP_DIR  = OOS_DIR / "snapshots"
WINDOWS   = OOS_DIR / "protected_windows.json"
BLOCKING_TYPES = {"alpha_research", "robustness", "replication"}
INFRA_TYPES    = {"infrastructure", "data_audit", "pipeline_validation"}


def _slice(df, a, b):
    return df.loc[(df["timestamp"] >= a) & (df["timestamp"] <= b)].reset_index(drop=True)


def main() -> int:
    # --- M§9.1.6 one-time guard -------------------------------------------
    if WINDOWS.exists():
        existing = json.loads(WINDOWS.read_text())
        if any(w.get("origin") == "BOOTSTRAP" for w in existing):
            print("REFUSE: a BOOTSTRAP window already exists (M§9.1.6 one-time).")
            return 1
    else:
        existing = []

    # --- M§9.1.7 hard precondition ----------------------------------------
    reg = ExperimentRegistry(str(ROOT / "experiments" / "registry"))
    recs = reg.list_experiments()
    alpha = [fe for fe in recs if fe.record.experiment_type == "alpha_research"]
    if alpha:
        print(f"REFUSE (M§9.1.7): {len(alpha)} alpha_research record(s) exist.")
        return 1
    print(f"[gate] M§9.1.7 OK — 0 alpha_research records among {len(recs)}.")

    # --- M§9.1.2 scan: refuse on prior RESEARCH, disclose prior INFRA -----
    seal_lo, seal_hi = DEPENDENCY_START, FUNDING_COVER_END
    refused, prior_infra = [], []
    for fe in recs:
        rc = fe.record
        for d in rc.datasets:
            ds, de = pd.Timestamp(d.data_start), pd.Timestamp(d.data_end)
            if ds <= seal_hi and seal_lo <= de:          # closed-interval overlap
                tag = f"{rc.experiment_id} ({rc.experiment_type}, {rc.research_stage}, {d.dataset_id} {ds}..{de})"
                if rc.experiment_type in BLOCKING_TYPES:
                    refused.append(tag)
                elif rc.experiment_type in INFRA_TYPES:
                    prior_infra.append(tag)
                break
    if refused:
        print("REFUSE (M§9.1.2): prior research intersects the window:")
        for t in refused: print("   ", t)
        return 1
    print(f"[gate] M§9.1.2 OK — 0 blocking records; {len(prior_infra)} infrastructure record(s) to disclose.")

    # --- snapshots ---------------------------------------------------------
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = []

    def seal_one(dataset_id, df_full, lo, hi, prov, rel):
        sl = _slice(df_full, lo, hi)
        if sl.empty:
            return None
        out = SNAP_DIR / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            print(f"REFUSE: write-once violation, {out} exists"); sys.exit(1)
        sl.to_parquet(out, index=False)
        h = hash_dataframe_content(sl)
        return {
            "dataset_id": dataset_id,
            "content_hash": h,
            "content_hash_method": HASH_METHOD,
            "processing_version": prov.processing_version,
            "source_venue": prov.source_venue,
            "native_or_proxy": prov.native_or_proxy,
            "proxy_for": getattr(prov, "proxy_for", None),
            "retrieval_date": str(pd.Timestamp(prov.retrieved_at).date()),
            "covers_start": sl["timestamp"].min().isoformat(),
            "covers_end": sl["timestamp"].max().isoformat(),
            "rows": int(len(sl)),
            "path": str(out.relative_to(ROOT)),
        }

    syms = list(mapped_symbols())
    print(f"[seal] slicing {len(syms)} Binance proxy datasets to [{DEPENDENCY_START}, {EVALUATION_END}] ...")
    for i, s in enumerate(syms, 1):
        try:
            df = read_binance_ohlcv_parquet(str(ROOT / "data"), s, check_provenance=False)
        except FileNotFoundError:
            continue
        prov = read_binance_provenance(str(ROOT / "data"), binance_ohlcv_dataset_id(s))
        e = seal_one(binance_ohlcv_dataset_id(s), df, DEPENDENCY_START, EVALUATION_END, prov,
                     f"binance/ohlcv/1h/{s}.parquet")
        if e: snapshot.append(e)
        if i % 50 == 0: print(f"   [{i}/{len(syms)}]")

    print(f"[seal] slicing Hyperliquid BTC funding to [{DEPENDENCY_START}, {FUNDING_COVER_END}] ...")
    fdf = read_funding_parquet(str(ROOT / "data"), "BTC", check_provenance=False)
    fprov = read_provenance(str(ROOT / "data"), funding_dataset_id("BTC"))
    e = seal_one(funding_dataset_id("BTC"), fdf, DEPENDENCY_START, FUNDING_COVER_END, fprov,
                 "hyperliquid/funding/BTC.parquet")
    if e: snapshot.append(e)

    total_rows = sum(x["rows"] for x in snapshot)
    eval_hours = int((EVALUATION_END - EVALUATION_START) / pd.Timedelta("1h")) + 1
    entry = {
        "window_id": WINDOW_ID,
        "evaluation_start": EVALUATION_START.isoformat(),
        "evaluation_end": EVALUATION_END.isoformat(),
        "dependency_start": DEPENDENCY_START.isoformat(),
        "funding_coverage_end": FUNDING_COVER_END.isoformat(),
        "origin": "BOOTSTRAP",
        "sealed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "SEALED",
        "reveal_count": 0,
        "reveals": [],
        "enumerated_freezes": [],
        "max_reveals": 1,
        "expected_trades_statement": (
            f"Evaluation window spans {eval_hours} hourly bars (365 days) with up to 189 Binance-proxy "
            "symbols fully covering it. A daily-rebalanced cross-sectional strategy therefore gets ~365 "
            "rebalances; an hourly one ~8760. Adequate for Sharpe discrimination at daily frequency; "
            "NOT adequate for regime-conditional claims, which would need multiple such windows. "
            "max_reveals=1: this is the only historical reserve and a reveal burns it permanently (M§10.1)."
        ),
        "prior_infrastructure_use": prior_infra,
        "selected_option": "A (Research Lead recommendation, user-authorised 2026-08-20)",
        "limitations": [
            "PROXY-PRICED: prices are Binance USD-M 1h labelled proxy_for=Hyperliquid. Hyperliquid-native "
            "1h reaches back only ~209 days, so a native-priced window of this length is impossible today "
            "(CLAUDE.md's native-execution preference cannot be met at this depth).",
            "FUNDING IS BTC-ONLY: only hyperliquid.funding.BTC existed at seal time. A multi-asset "
            "funding/carry strategy CANNOT use this window, because M§9.9.2 requires every loaded "
            "timestamp to fall inside a sealed snapshot entry and snapshot[] is fixed at seal time.",
            "Hyperliquid-native 1h OHLCV is deliberately NOT sealed and NOT part of this window. It "
            "remains available for infrastructure/cost/liquidity work only; it MUST NOT be used for "
            "alpha research inside [dependency_start, evaluation_end] (M§9.0, procedural).",
        ],
        "snapshot": snapshot,
    }
    OOS_DIR.mkdir(parents=True, exist_ok=True)
    WINDOWS.write_text(json.dumps(existing + [entry], indent=2, sort_keys=True) + "\n")
    print(f"\n[done] sealed {len(snapshot)} datasets, {total_rows:,} rows -> {WINDOWS.relative_to(ROOT)}")
    print(f"[done] eval {EVALUATION_START.date()}..{EVALUATION_END.date()} | dep_start {DEPENDENCY_START.date()} | funding_cover_end {FUNDING_COVER_END}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
