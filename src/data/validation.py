"""D§10 — integrity checks -> `ValidationReport`.

D§2.1: MUST NOT import `src/data/hyperliquid/**`.

A report is DATA, never a print statement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import pandas as pd

from .base import MAX_FUNDING_GAP
from .schemas import OHLCV_SOURCE_TYPES, RESERVED_SOURCE_TYPES

__all__ = [
    "ValidationFinding",
    "ValidationReport",
    "validate_ohlcv",
    "validate_funding",
    "validate_universe",
    "compare_cross_venue",
    "CrossVenueReport",
    "UnitNormalizationError",
    "UNIT_NORMALIZATION_RATIO_BOUNDS",
]


@dataclass(frozen=True)
class ValidationFinding:
    severity: str  # "error" | "warning"
    code: str
    symbol: Optional[str]
    timestamp: Optional[pd.Timestamp]
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple  # tuple[ValidationFinding, ...]

    @property
    def counts(self) -> dict:
        out: dict = {}
        for f in self.findings:
            out[f.code] = out.get(f.code, 0) + 1
        return out

    @property
    def status(self) -> str:
        if any(f.severity == "error" for f in self.findings):
            return "failed"
        if any(f.severity == "warning" for f in self.findings):
            return "warnings"
        return "ok"

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _mk(findings: list) -> ValidationReport:
    return ValidationReport(findings=tuple(findings))


# ---------------------------------------------------------------------------
# D§10 — OHLCV
# ---------------------------------------------------------------------------


def validate_ohlcv(df: pd.DataFrame) -> ValidationReport:
    findings: list = []

    # naive / non-UTC timestamps
    dtype = df["timestamp"].dtype
    if not isinstance(dtype, pd.DatetimeTZDtype):
        findings.append(ValidationFinding("error", "NAIVE_TIMESTAMP", None, None,
                                           f"timestamp column is not tz-aware: dtype={dtype!r}"))
    elif str(dtype.tz) != "UTC":
        findings.append(ValidationFinding("error", "NON_UTC_TIMESTAMP", None, None,
                                           f"timestamp column tz is {dtype.tz!r}, expected UTC"))

    # duplicate (symbol, timestamp)
    dup_mask = df.duplicated(subset=["symbol", "timestamp"], keep=False)
    for _, row in df.loc[dup_mask].iterrows():
        findings.append(ValidationFinding("error", "DUPLICATE_KEY", row["symbol"], row["timestamp"],
                                           "duplicate (symbol, timestamp)"))

    # duplicate/colliding symbol names (spot-style names reaching the perp path, D§3.3.3)
    for sym in df["symbol"].unique():
        if "/" in str(sym) or str(sym).startswith("@"):
            findings.append(ValidationFinding("error", "SPOT_STYLE_SYMBOL", sym, None,
                                               f"spot-style symbol name {sym!r} reached the perp OHLCV path (D§3.3.3)"))

    for symbol, g in df.groupby("symbol", sort=False):
        g = g.sort_index()
        ts = g["timestamp"]

        # non-monotonic timestamps
        if not ts.is_monotonic_increasing:
            bad = ts[~(ts.diff().fillna(pd.Timedelta(0)) >= pd.Timedelta(0))]
            first_bad = bad.iloc[0] if len(bad) else ts.iloc[0]
            findings.append(ValidationFinding("error", "NON_MONOTONIC_TIMESTAMP", symbol, first_bad,
                                               "timestamps not sorted ascending within symbol"))

        for _, row in g.iterrows():
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            v, n = row["volume"], row["trade_count"]
            t = row["timestamp"]

            for name, val in (("open", o), ("high", h), ("low", l), ("close", c)):
                if not math.isfinite(val) or val <= 0:
                    findings.append(ValidationFinding("error", "MALFORMED_OHLC", symbol, t,
                                                       f"{name}={val} not finite/positive (D§4.3)"))
            if math.isfinite(h) and math.isfinite(o) and math.isfinite(c) and h < max(o, c):
                findings.append(ValidationFinding("error", "MALFORMED_OHLC", symbol, t,
                                                   f"high={h} < max(open,close)={max(o, c)} (D§4.3)"))
            if math.isfinite(l) and math.isfinite(o) and math.isfinite(c) and l > min(o, c):
                findings.append(ValidationFinding("error", "MALFORMED_OHLC", symbol, t,
                                                   f"low={l} > min(open,close)={min(o, c)} (D§4.3)"))
            if math.isfinite(h) and math.isfinite(l) and h < l:
                findings.append(ValidationFinding("error", "MALFORMED_OHLC", symbol, t,
                                                   f"high={h} < low={l} (D§4.3)"))
            if not math.isfinite(v) or v < 0:
                findings.append(ValidationFinding("error", "NEGATIVE_OR_NONFINITE_VOLUME", symbol, t,
                                                   f"volume={v} (D§4.3)"))
            if n < 0:
                findings.append(ValidationFinding("error", "NEGATIVE_TRADE_COUNT", symbol, t,
                                                   f"trade_count={n} (D§4.3)"))

        # backfill leading-run presence (D§4.4) — advisory: a properly-processed
        # frame should already have this excluded; its presence here means the
        # caller handed validate_ohlcv a pre-quarantine (e.g. raw) frame.
        native = ~(g["volume"].eq(0.0) & g["trade_count"].eq(0))
        if len(native) and not native.iloc[0]:
            n_prefix = int(native.to_numpy().argmax()) if native.any() else len(native)
            findings.append(ValidationFinding(
                "warning", "BACKFILL_PREFIX_PRESENT", symbol, g["timestamp"].iloc[0],
                f"leading zero-volume run of {n_prefix} bar(s) present through "
                f"{g['timestamp'].iloc[n_prefix - 1] if n_prefix else None} (D§4.4) — "
                "these MUST be excluded from processed datasets"
            ))

        # D§15.1 source_type must be a recognized OHLCV source; RESERVED
        # values (e.g. "external_proxy", D§15.2.5) are a hard failure here
        # too (defense in depth alongside schemas.assert_source_type_allowed,
        # which refuses at emission time).
        if "source_type" in g.columns:
            bad_source = ~g["source_type"].isin(OHLCV_SOURCE_TYPES)
            for _, row in g.loc[bad_source].iterrows():
                severity = "error"
                findings.append(ValidationFinding(severity, "BAD_SOURCE_TYPE", symbol, row["timestamp"],
                                                   f"source_type={row['source_type']!r} not in "
                                                   f"{sorted(OHLCV_SOURCE_TYPES)} (D§15.1)"))

    return _mk(findings)


# ---------------------------------------------------------------------------
# D§10 — Funding
# ---------------------------------------------------------------------------


def validate_funding(df: pd.DataFrame, coverage: Optional[Sequence] = None,
                      max_funding_gap: pd.Timedelta = MAX_FUNDING_GAP) -> ValidationReport:
    findings: list = []

    dtype = df["timestamp"].dtype
    if not isinstance(dtype, pd.DatetimeTZDtype):
        findings.append(ValidationFinding("error", "NAIVE_TIMESTAMP", None, None,
                                           f"timestamp column is not tz-aware: dtype={dtype!r}"))
    elif str(dtype.tz) != "UTC":
        findings.append(ValidationFinding("error", "NON_UTC_TIMESTAMP", None, None,
                                           f"timestamp column tz is {dtype.tz!r}, expected UTC"))

    dup_mask = df.duplicated(subset=["symbol", "timestamp"], keep=False)
    for _, row in df.loc[dup_mask].iterrows():
        findings.append(ValidationFinding("error", "DUPLICATE_KEY", row["symbol"], row["timestamp"],
                                           "duplicate (symbol, timestamp)"))

    coverage_by_symbol: dict = {}
    if coverage:
        for c in coverage:
            coverage_by_symbol.setdefault(c.symbol, []).append(c)

    for symbol, g in df.groupby("symbol", sort=False):
        g = g.sort_index()
        ts = g["timestamp"]
        if not ts.is_monotonic_increasing:
            findings.append(ValidationFinding("error", "NON_MONOTONIC_TIMESTAMP", symbol, ts.iloc[0],
                                               "funding timestamps not sorted ascending within symbol"))

        for _, row in g.iterrows():
            rate = row["funding_rate"]
            t = row["timestamp"]
            if not math.isfinite(rate):
                findings.append(ValidationFinding("error", "NON_FINITE_RATE", symbol, t, f"funding_rate={rate}"))
            elif abs(rate) >= 0.01:
                findings.append(ValidationFinding("warning", "IMPLAUSIBLE_RATE", symbol, t,
                                                   f"|funding_rate|={abs(rate)} >= 1%/event (advisory)"))

        if len(g) >= 2:
            gaps = ts.diff().dropna()
            bad = gaps[gaps > max_funding_gap]
            for idx in bad.index:
                findings.append(ValidationFinding("error", "FUNDING_GAP_EXCEEDED", symbol, ts.loc[idx],
                                                   f"spacing {gaps.loc[idx]} > max_funding_gap {max_funding_gap}"))

        symbol_coverage = coverage_by_symbol.get(symbol)
        if symbol_coverage is not None:
            # D§7.2 disjointness — pairwise non-intersecting closures.
            ordered = sorted(symbol_coverage, key=lambda c: c.coverage_start)
            for a, b in zip(ordered, ordered[1:]):
                if b.coverage_start <= a.coverage_end:
                    findings.append(ValidationFinding("error", "COVERAGE_NOT_DISJOINT", symbol, b.coverage_start,
                                                        f"coverage record [{b.coverage_start}, {b.coverage_end}] "
                                                        f"touches/overlaps [{a.coverage_start}, {a.coverage_end}]"))

            # D§5.6 — coverage claimed beyond retrieved events.
            actual_start, actual_end = ts.iloc[0], ts.iloc[-1]
            union_start = min(c.coverage_start for c in ordered)
            union_end = max(c.coverage_end for c in ordered)
            if union_start < actual_start or union_end > actual_end:
                findings.append(ValidationFinding(
                    "error", "FALSE_COVERAGE", symbol, union_start,
                    f"declared coverage [{union_start},{union_end}] exceeds actually retrieved events "
                    f"[{actual_start},{actual_end}] (D§5.6)"
                ))

            # events outside any coverage record
            for _, row in g.iterrows():
                t = row["timestamp"]
                if not any(c.coverage_start <= t <= c.coverage_end for c in ordered):
                    findings.append(ValidationFinding("error", "EVENT_OUTSIDE_COVERAGE", symbol, t,
                                                        "funding event timestamp falls outside every declared "
                                                        "coverage record"))

    return _mk(findings)


# ---------------------------------------------------------------------------
# D§10 — Universe
# ---------------------------------------------------------------------------


def validate_universe(universe) -> ValidationReport:
    findings: list = []
    names = list(universe.symbols.keys())
    if len(names) != len(set(names)):
        findings.append(ValidationFinding("error", "DUPLICATE_SYMBOL_NAME", None, None,
                                           "duplicate symbol names in universe snapshot (D§3.3.4)"))

    prov = universe.provenance
    if prov is None or prov.survivorship_safe is not False:
        findings.append(ValidationFinding("error", "SURVIVORSHIP_SAFE_NOT_FALSE", None, None,
                                           f"survivorship_safe MUST be False (D§6.3), got "
                                           f"{getattr(prov, 'survivorship_safe', None)!r}"))

    return _mk(findings)


# ---------------------------------------------------------------------------
# D§17 — Cross-venue proxy validation
#
# Purpose: establish WHEN Binance prices are a defensible proxy for
# Hyperliquid-executed research — NOT to show the two are identical.
# Numerical identity is NOT required and MUST NOT be asserted (D§17 opening).
#
# This function takes two already-loaded, already-normalized `pd.DataFrame`s
# (D§4.1 schema) for the SAME Hyperliquid symbol from two providers. It does
# NOT import `hyperliquid/**` or `binance/**` (D§2.1) — the caller is
# responsible for producing the two frames.
# ---------------------------------------------------------------------------


class UnitNormalizationError(Exception):
    """D§17.4 (v1.3 DECISION 3, M44) -- an order-of-magnitude LEVEL discrepancy
    that SURVIVES unit normalization is a MAPPING DEFECT, not an ordinary
    cross-venue proxy finding. It MUST fail, never merely warn or get folded
    into `large_discrepancy_events` as if it were routine microstructure
    divergence. A discrepancy fully explained by a recorded, verified
    multiplier (D§16.3.4) is NOT a discrepancy at all and never raises this.
    """


# A generous band around 1.0 for the NORMALIZED (per-common-unit) price
# ratio: ordinary cross-venue microstructure/timing divergence is expected
# and MUST NOT raise (D§17.4); only an order-of-magnitude discrepancy — the
# unmistakable signature of a wrong/dropped/duplicated unit multiplier
# surviving normalization — is treated as fatal. Mirrors
# `binance.provider.UNIT_EQUIVALENCE_RATIO_BOUNDS`, expressed here
# venue-agnostically since D§2.1 forbids this module importing either
# provider package.
# Audit finding D2 — see `binance.provider.UNIT_EQUIVALENCE_RATIO_BOUNDS` for the
# full rationale. Was `(0.1, 10.0)` inclusive, which passed a ratio of exactly
# 0.1 — the signature of recording multiplier 100 instead of 1000. Now (0.5, 2.0)
# compared STRICTLY.
UNIT_NORMALIZATION_RATIO_BOUNDS = (0.5, 2.0)


@dataclass(frozen=True)
class CrossVenueReport:
    symbol: str
    n_overlapping_bars: int
    return_correlation: Optional[float]
    mean_abs_return_diff: Optional[float]
    return_diff_percentiles: dict  # {1,5,25,50,75,95,99: float}
    hl_volatility: Optional[float]
    binance_volatility: Optional[float]
    volatility_ratio: Optional[float]  # binance / hyperliquid
    ohlc_relative_diff_percentiles: dict  # {"open":{...}, "high":{...}, ...}
    large_discrepancy_events: tuple  # tuple of {timestamp, field, relative_diff}
    around_listing: Optional[dict]  # metrics restricted to a window around a listing timestamp, if given
    high_vol_period: Optional[dict]  # metrics restricted to the top decile of realised vol
    hl_unit_multiplier: int  # D§17.2 precondition (v1.3 DECISION 3) -- multiplier ACTUALLY applied
    venue_unit_multiplier: int  # D§17.2 precondition (v1.3 DECISION 3) -- multiplier ACTUALLY applied


_PERCENTILES = (1, 5, 25, 50, 75, 95, 99)
_LARGE_DISCREPANCY_RELATIVE_THRESHOLD = 0.05  # 5% relative OHLC difference — reporting threshold, not a defect


def _pct(series: pd.Series) -> dict:
    if series.empty:
        return {p: None for p in _PERCENTILES}
    return {p: float(series.quantile(p / 100.0)) for p in _PERCENTILES}


def _returns(close: pd.Series) -> pd.Series:
    if len(close) < 2:
        return pd.Series([], dtype="float64")
    return close.pct_change().dropna()


def _ohlc_relative_diffs(hl: pd.DataFrame, bn: pd.DataFrame) -> dict:
    out = {}
    for field in ("open", "high", "low", "close"):
        diff = (bn[field] - hl[field]).abs() / hl[field].abs()
        out[field] = _pct(diff)
    return out


def _summarize_window(hl: pd.DataFrame, bn: pd.DataFrame) -> dict:
    hl_ret = _returns(hl["close"])
    bn_ret = _returns(bn["close"])
    aligned = pd.concat([hl_ret.rename("hl"), bn_ret.rename("bn")], axis=1).dropna()
    corr = float(aligned["hl"].corr(aligned["bn"])) if len(aligned) >= 2 else None
    diff = (aligned["bn"] - aligned["hl"]) if len(aligned) else pd.Series([], dtype="float64")
    return {
        "n_bars": int(len(aligned)),
        "return_correlation": corr,
        "mean_abs_return_diff": float(diff.abs().mean()) if len(diff) else None,
        "return_diff_percentiles": _pct(diff.abs()) if len(diff) else {p: None for p in _PERCENTILES},
    }


def compare_cross_venue(
    hl_df: pd.DataFrame,
    binance_df: pd.DataFrame,
    symbol: str,
    listing_timestamp: Optional[pd.Timestamp] = None,
    listing_window: pd.Timedelta = pd.Timedelta(days=3),
    hl_unit_multiplier: int = 1,
    venue_unit_multiplier: int = 1,
) -> CrossVenueReport:
    """D§17 — cross-venue proxy validation metrics at 1h on the overlapping
    window. `hl_df`/`binance_df` MUST already be filtered to ONE symbol and
    sorted by timestamp; this function aligns them on `timestamp` (inner
    join — the "overlapping window" per D§17.2).

    D§17.2 precondition (v1.3 DECISION 3): BOTH series MUST first be
    normalized to a common underlying unit using the explicit, verified
    multipliers of D§16.3.4 (`symbol_map.SymbolMapping.hl_unit_multiplier` /
    `.venue_unit_multiplier`) BEFORE any metric is computed (M44) —
    normalization is applied FIRST, unconditionally, not as an afterthought
    on top of a raw comparison. Return-based metrics are multiplier-invariant
    by construction; the level comparisons (OHLC relative diffs,
    large-discrepancy events) are NOT, and are meaningless without this step.
    Defaults of `1, 1` are the correct, explicit "no scaling" case (e.g.
    BTC/ETH) — never inferred, always the caller-supplied recorded multiplier
    (D§16.3.4 M43: this function never derives a multiplier from the data it
    is given).

    D§17.4 escalation: an order-of-magnitude discrepancy in the NORMALIZED
    level comparison is a MAPPING DEFECT, not an ordinary proxy finding, and
    MUST fail — this function raises `UnitNormalizationError` for that case
    (unlike ordinary microstructure divergence, which is expected and does
    NOT raise). All OTHER escalation judgement (D§17.4's "not a reasonable
    proxy for an important part of the universe") is NOT decided here — this
    function only computes metrics for that; a human/auditor applies it.
    """
    hl_raw = hl_df.set_index("timestamp").sort_index()
    bn_raw = binance_df.set_index("timestamp").sort_index()

    # D§17.2 (v1.3 DECISION 3, M44) — normalize to a common underlying unit
    # BEFORE computing anything else. Dividing each venue's OWN raw price by
    # its OWN multiplier yields a per-single-underlying-token price; this is
    # NOT an inference of the multiplier (M43) — both multipliers are fixed,
    # caller-supplied, already-verified constants (D§16.3.4).
    price_cols = [c for c in ("open", "high", "low", "close") if c in hl_raw.columns]
    hl = hl_raw.copy()
    bn = bn_raw.copy()
    if hl_unit_multiplier != 1:
        hl[price_cols] = hl[price_cols] / hl_unit_multiplier
    if venue_unit_multiplier != 1:
        bn[price_cols] = bn[price_cols] / venue_unit_multiplier

    common_idx = hl.index.intersection(bn.index)
    hl_c = hl.loc[common_idx]
    bn_c = bn.loc[common_idx]

    # D§17.4 (M44) — an order-of-magnitude LEVEL discrepancy SURVIVING
    # normalization is a mapping defect, not a proxy finding, and MUST fail.
    if len(common_idx) and "close" in hl_c.columns:
        ratio = (bn_c["close"] / hl_c["close"]).replace([float("inf"), float("-inf")], float("nan")).dropna()
        if len(ratio):
            median_ratio = float(ratio.median())
            lo, hi = UNIT_NORMALIZATION_RATIO_BOUNDS
            # STRICT comparison (audit D2) — an exactly-on-boundary ratio is the
            # signature of a power-of-ten multiplier error.
            if not (lo < median_ratio < hi):
                raise UnitNormalizationError(
                    f"D§17.4 (M44): {symbol!r} median normalized close ratio (binance/hl) = "
                    f"{median_ratio:.6g}, outside [{lo},{hi}] AFTER dividing by the recorded "
                    f"hl_unit_multiplier={hl_unit_multiplier}/venue_unit_multiplier={venue_unit_multiplier}. "
                    "This is an unexplained order-of-magnitude gap surviving unit normalization -- a "
                    "MAPPING DEFECT, not ordinary proxy divergence. FAILING, not warning."
                )

    overall = _summarize_window(hl_c, bn_c)
    ohlc_diffs = _ohlc_relative_diffs(hl_c, bn_c) if len(common_idx) else {
        f: {p: None for p in _PERCENTILES} for f in ("open", "high", "low", "close")
    }

    hl_ret = _returns(hl_c["close"]) if len(common_idx) else pd.Series([], dtype="float64")
    bn_ret = _returns(bn_c["close"]) if len(common_idx) else pd.Series([], dtype="float64")
    hl_vol = float(hl_ret.std()) if len(hl_ret) >= 2 else None
    bn_vol = float(bn_ret.std()) if len(bn_ret) >= 2 else None
    vol_ratio = (bn_vol / hl_vol) if (hl_vol not in (None, 0) and bn_vol is not None) else None

    # D§17.2 — large-discrepancy events, ENUMERATED with timestamps.
    large_events = []
    for field in ("open", "high", "low", "close"):
        if len(common_idx) == 0:
            break
        rel = (bn_c[field] - hl_c[field]).abs() / hl_c[field].abs()
        for ts in rel[rel > _LARGE_DISCREPANCY_RELATIVE_THRESHOLD].index:
            large_events.append({"timestamp": ts, "field": field, "relative_diff": float(rel.loc[ts])})
    large_events.sort(key=lambda e: e["timestamp"])

    # D§17.3 — conditioning around listing.
    around_listing = None
    if listing_timestamp is not None and len(common_idx):
        lo, hi = listing_timestamp - listing_window, listing_timestamp + listing_window
        mask = (common_idx >= lo) & (common_idx <= hi)
        if mask.any():
            around_listing = _summarize_window(hl_c.loc[common_idx[mask]], bn_c.loc[common_idx[mask]])

    # D§17.3 — conditioning around high-volatility periods (top decile of
    # realised vol, using a rolling window on the HL series as the
    # reference clock for "when volatility was high").
    high_vol_period = None
    if len(hl_ret) >= 20:
        rolling_vol = hl_ret.rolling(window=20, min_periods=20).std()
        threshold = rolling_vol.quantile(0.9)
        high_vol_ts = rolling_vol[rolling_vol >= threshold].index
        high_vol_ts = high_vol_ts.intersection(common_idx)
        if len(high_vol_ts):
            high_vol_period = _summarize_window(hl_c.loc[high_vol_ts], bn_c.loc[high_vol_ts])

    return CrossVenueReport(
        symbol=symbol,
        n_overlapping_bars=int(len(common_idx)),
        return_correlation=overall["return_correlation"],
        mean_abs_return_diff=overall["mean_abs_return_diff"],
        return_diff_percentiles=overall["return_diff_percentiles"],
        hl_volatility=hl_vol,
        binance_volatility=bn_vol,
        volatility_ratio=vol_ratio,
        ohlc_relative_diff_percentiles=ohlc_diffs,
        large_discrepancy_events=tuple(large_events),
        around_listing=around_listing,
        high_vol_period=high_vol_period,
        hl_unit_multiplier=hl_unit_multiplier,
        venue_unit_multiplier=venue_unit_multiplier,
    )
