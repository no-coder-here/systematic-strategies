"""Driver for the D§5.5.1 asset_ctxs oracle extraction.

The library (`src/data/hyperliquid/asset_ctxs_archive.py`) had no entry point;
this only wires it up. No pipeline logic lives here.

    python scripts/asset_ctxs_ingest.py START_DATE END_DATE [--symbols N]

Dates are YYYYMMDD archive segment names.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from data.hyperliquid.asset_ctxs_archive import (  # noqa: E402
    archive_dates_in_range,
    build_alignment_report,
    compact_parquet_path,
    run_incremental_extraction,
)
from data.hyperliquid.client import HyperliquidClient  # noqa: E402


def fetch_funding(symbols, start_ms, end_ms) -> pd.DataFrame:
    client = HyperliquidClient()
    rows = []
    for i, sym in enumerate(symbols, 1):
        try:
            for ev in client.fetch_funding_paginated(sym, start_ms, end_ms, 500):
                rows.append({"symbol": sym, "timestamp": ev["time"], "funding_rate": float(ev["fundingRate"])})
        except Exception as exc:  # surfaced, never swallowed into an empty frame (D§7)
            print(f"  !! funding fetch failed for {sym}: {exc!r}", file=sys.stderr)
            raise
        if i % 25 == 0:
            print(f"  funding {i}/{len(symbols)} symbols, {len(rows)} events", flush=True)
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("start_date")
    ap.add_argument("end_date")
    ap.add_argument("--symbols", type=int, default=0, help="limit symbol count (0 = all)")
    args = ap.parse_args()

    base_dir = ROOT / "data"
    dates = archive_dates_in_range(args.start_date, args.end_date)
    print(f"archive dates in range: {len(dates)} ({dates[0]}..{dates[-1]})")

    meta = HyperliquidClient().fetch_meta()
    symbols = [u["name"] for u in meta["universe"]]
    if args.symbols:
        symbols = symbols[: args.symbols]
    print(f"symbols: {len(symbols)}")

    start_ms = int(pd.Timestamp(f"{args.start_date[:4]}-{args.start_date[4:6]}-{args.start_date[6:]}", tz="UTC").timestamp() * 1000)
    end_ms = int((pd.Timestamp(f"{args.end_date[:4]}-{args.end_date[4:6]}-{args.end_date[6:]}", tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1000)

    t0 = time.time()
    funding = fetch_funding(symbols, start_ms, end_ms)
    print(f"funding events: {len(funding):,} in {time.time()-t0:.0f}s")

    t1 = time.time()
    res = run_incremental_extraction(base_dir, funding, args.start_date, args.end_date)
    dt = time.time() - t1
    print(f"\nEXTRACTION: dates={len(res.dates_processed)} rows_appended={res.rows_appended:,} "
          f"total_rows={res.total_rows:,} hwm={res.high_water_mark} no_op={res.skipped_no_op} in {dt:.0f}s")

    # M46 — a refresh over the same range must be a genuine no-op.
    t2 = time.time()
    res2 = run_incremental_extraction(base_dir, funding, args.start_date, args.end_date)
    print(f"REFRESH (same range): no_op={res2.skipped_no_op} dates={len(res2.dates_processed)} "
          f"rows_appended={res2.rows_appended} in {time.time()-t2:.2f}s")

    pq = compact_parquet_path(base_dir)
    compact = pd.read_parquet(pq)
    rep = build_alignment_report(compact, funding, res.event_ctx_offsets)
    print(f"\nCOMPACT: {pq.relative_to(ROOT)}  rows={len(compact):,}  size={pq.stat().st_size/1e6:.2f} MB "
          f"({pq.stat().st_size/max(1,len(compact)):.1f} B/row)")
    print("ALIGNMENT:", json.dumps(getattr(rep, "__dict__", {}), default=str, indent=2)[:1200])

    print("\nRAW RETENTION CHECK (D§5.5.1 rule 1 / M47):")
    subprocess.run(["find", str(base_dir), "-iname", "*asset_ctx*", "-o", "-iname", "*.lz4", "-o", "-iname", "*.csv"],
                   check=False)
    subprocess.run(["du", "-sh", str(base_dir)], check=False)


if __name__ == "__main__":
    main()
