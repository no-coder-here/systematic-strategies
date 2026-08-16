#!/usr/bin/env python3
"""D§17 — cross-venue proxy validation report: BTC, ETH + 8 liquid altcoins,
selected by Hyperliquid volume BEFORE results are computed (D§17.1).

Fetches live Hyperliquid 1h OHLCV over its native retention window and
compares against the just-ingested Binance canonical 1h series over the
SAME overlapping window, via `data.validation.compare_cross_venue`.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pandas as pd  # noqa: E402

from data import storage  # noqa: E402
from data.hyperliquid.provider import HyperliquidProvider  # noqa: E402
from data.symbol_map import get_mapping  # noqa: E402
from data.validation import UnitNormalizationError, compare_cross_venue  # noqa: E402

# Selected BEFORE computing results (D§17.1): BTC/ETH mandatory, plus 8
# liquid altcoins by Hyperliquid volume/name-recognition, PLUS at least one
# k-asset (v1.3 DECISION 3) to demonstrate the unit-normalized path
# end-to-end (D§16.3.4/D§17.2).
SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK", "ARB", "OP", "SUI", "kPEPE"]

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def main() -> int:
    provider = HyperliquidProvider()
    end = pd.Timestamp.utcnow().floor("h")
    start = end - pd.Timedelta(days=200)  # within native ~208-day 1h retention

    reports = {}
    for sym in SYMBOLS:
        try:
            hl_df = provider.get_ohlcv([sym], "1h", start, end)
        except Exception as exc:
            reports[sym] = {"error": f"HL fetch failed: {exc}"}
            continue
        if hl_df.empty:
            reports[sym] = {"error": "no HL data"}
            continue
        try:
            bn_df = storage.read_binance_ohlcv_parquet(DATA_DIR, sym, check_provenance=False)
        except Exception as exc:
            reports[sym] = {"error": f"Binance read failed: {exc}"}
            continue
        bn_df = bn_df[(bn_df["timestamp"] >= start) & (bn_df["timestamp"] < end)]

        # D§17.2 (v1.3 DECISION 3) -- normalize to a common underlying unit
        # using the CHECKED-IN, verified multipliers (never inferred from
        # the price data itself, D§16.3.4 M43).
        mapping = get_mapping(sym)
        if mapping is None or mapping.status != "mapped":
            reports[sym] = {"error": f"no reviewed symbol mapping for {sym!r}"}
            continue
        hl_um = mapping.hl_unit_multiplier
        venue_um = mapping.venue_unit_multiplier

        try:
            report = compare_cross_venue(
                hl_df, bn_df, sym, hl_unit_multiplier=hl_um, venue_unit_multiplier=venue_um,
            )
        except UnitNormalizationError as exc:
            # D§17.4 -- an unexplained order-of-magnitude gap SURVIVING
            # normalization is a mapping defect, not a proxy finding. MUST
            # be raised/reported as such, never silently swallowed.
            reports[sym] = {"error": f"UnitNormalizationError (D§17.4 mapping defect): {exc}"}
            continue
        reports[sym] = {
            "n_overlapping_bars": report.n_overlapping_bars,
            "hl_unit_multiplier": report.hl_unit_multiplier,
            "venue_unit_multiplier": report.venue_unit_multiplier,
            "return_correlation": report.return_correlation,
            "mean_abs_return_diff": report.mean_abs_return_diff,
            "return_diff_percentiles": report.return_diff_percentiles,
            "hl_volatility": report.hl_volatility,
            "binance_volatility": report.binance_volatility,
            "volatility_ratio": report.volatility_ratio,
            "ohlc_relative_diff_percentiles": report.ohlc_relative_diff_percentiles,
            "n_large_discrepancy_events": len(report.large_discrepancy_events),
            "large_discrepancy_events_sample": [
                {**e, "timestamp": str(e["timestamp"])} for e in report.large_discrepancy_events[:10]
            ],
            "high_vol_period": report.high_vol_period,
        }
        print(f"{sym}: n={report.n_overlapping_bars} corr={report.return_correlation} "
              f"mean_abs_diff={report.mean_abs_return_diff} "
              f"vol_ratio={report.volatility_ratio} "
              f"large_disc={len(report.large_discrepancy_events)} "
              f"hl_um={hl_um} venue_um={venue_um}")

    out_path = os.path.join(DATA_DIR, "metadata", "_crossvenue_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(reports, f, indent=2, sort_keys=True, default=str)
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
