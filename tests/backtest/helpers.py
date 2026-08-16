"""Shared fixture-construction helpers for the §18 mandatory test suite.

No alpha logic, purely synthetic fixtures per §19.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.models import MarketData

# ---------------------------------------------------------------------------
# §17 floating-point policy — shared tolerance constants.
#
# Every TOLERANCE-classified assertion in the §18 suite MUST use exactly the
# row of §17's table that governs the quantity being compared, re-derived
# per assertion rather than pattern-matched. These constants exist so every
# test file spells the SAME numbers for the SAME row, instead of each test
# picking its own ad hoc tolerance.
# ---------------------------------------------------------------------------

# "equity reconstruction check §8" row: rtol=1e-12, atol=1e-9 (USD). Governs
# any USD-denominated ledger value (equity_curve, NAV_after_cost, cash PnL
# figures of the same magnitude as NAV).
TOL_EQUITY = dict(rel=1e-12, abs=1e-9)

# "decomposition identity (D), ruin_decomposition_residual" row:
# rtol=1e-12, atol=1e-15.
TOL_DECOMP = dict(rel=1e-12, abs=1e-15)

# "reconstructed / derived weights, exposures" row: rtol=1e-12, atol=1e-15.
TOL_WEIGHT = dict(rel=1e-12, abs=1e-15)

# "turnover and cost assertions" row: rtol=1e-12, atol=1e-15.
TOL_TURNOVER = dict(rel=1e-12, abs=1e-15)

# "metrics, drag statistics" row: rtol=1e-10, atol=1e-12.
TOL_METRIC = dict(rel=1e-10, abs=1e-12)


def dates(n: int, freq: str = "1D") -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC")


def single_symbol_frame(index: pd.DatetimeIndex, values, symbol: str = "A") -> pd.DataFrame:
    return pd.DataFrame({symbol: list(values)}, index=index)


def mask_series(index: pd.DatetimeIndex, true_at) -> pd.Series:
    true_set = set(true_at)
    return pd.Series([i in true_set for i in range(len(index))], index=index, dtype=bool)


def _poison_frame(prices: pd.DataFrame) -> pd.DataFrame:
    """A same-shape frame filled with NaN — used to poison whichever series
    §4.2 does NOT select, so every fixture using `md()` actively proves the
    unselected series is never read (BD-23)."""
    return pd.DataFrame(
        np.full(prices.shape, np.nan), index=prices.index, columns=prices.columns
    )


def md(prices: pd.DataFrame, *, execution_mode: str = "next_open") -> MarketData:
    """§4.2 test helper — wraps a single price-series fixture as MarketData
    for every §18 fixture that states only one price series `P`.

    The series `execution_mode` does NOT select is filled with `NaN`
    (BD-23): under `next_open`, `close` is poisoned; under `next_close`,
    `open` is poisoned. `execution_mode` defaults to `"next_open"`,
    matching `BacktestConfig`'s own default (§15), so every existing call
    site that does not override `execution_mode` is unaffected.

    Verified (per the round-3 audit): poisoning with NaN — and, separately,
    with -1e9 — leaves every pinned §18 fixture's output bit-identical
    across 600 runs, because `_select_execution_price_frame` (§4.2) is the
    ONLY place `market_data.open`/`market_data.close` are read, and the
    unselected series is never consulted again anywhere in the engine.

    PERIMETER (W-5) — every site that constructs `MarketData` WITHOUT going
    through `md()` (and is therefore NOT BD-23-poisoned), and why each is
    individually justified. None of these is load-bearing for §4.2
    selection itself (that guarantee is what `md()` exists to protect); each
    one below needs a genuinely populated "other" series for a DIFFERENT
    reason:

    - `test_funding.py:173` (F8) — via the separate `md_both()` helper
      (below): compares `next_open` vs `next_close` funding windows on
      IDENTICAL underlying prices, not §4.2 selection.
    - `test_anti_lookahead.py:91` (E2) — the test's entire point is that
      `execution_mode` selects the CORRECT series: it needs two genuinely
      DIFFERENT, populated `open`/`close` frames on the SAME `MarketData`
      object so that reading the wrong one produces a detectably different
      final NAV. Poisoning one side with NaN would make this untestable
      (the "wrong" read would ruin/raise instead of silently mismatching).
    - `test_anti_lookahead.py:109` (`_select_execution_price_frame` unit
      test) — a direct unit test of the selector function itself; it
      asserts `is open_df` / `is close_df` identity, which requires both
      frames to be genuinely distinct, independently-constructed objects.
    - `test_anti_lookahead.py:131,135` (mismatched index / mismatched
      columns raise `DataIntegrityError`) — these MUST construct a `close`
      frame with a different index / different columns than `open`.
      `_poison_frame` always copies `prices.index`/`prices.columns`
      exactly, so `md()` can never produce a genuine mismatch — it is
      structurally incapable of exercising this check.
    - `test_result_surface.py:130` (BD-9) — runs BOTH `next_open` and
      `next_close` on the SAME `MarketData` object and asserts real
      (non-NaN, non-poisoned) equity values and timestamps under BOTH
      modes; poisoning either side would make one of the two assertions
      vacuous.
    - `test_data_integrity_coverage.py:58,82` (BD-1 duplicate columns) —
      construct `MarketData(open=prices, close=prices)` directly with a
      genuinely duplicate-column frame on BOTH sides. This is deliberate:
      `_poison_frame` happens to preserve `prices.columns` (including
      duplicates) today, so `md()` would incidentally still work, but these
      tests intentionally do not rely on that incidental behaviour — the
      §11.2 duplicate-column defect class is kept independent of the BD-23
      poisoning mechanism so neither test's correctness is coupled to the
      other's implementation detail.
    - `test_data_integrity_coverage.py:159` (CG-1) — needs the IDENTICAL
      frame (carrying a NaN at the one bar the cost-stage ruin must NOT
      reach) on both `open` and `close`, so a failure here can only mean
      "Step 5 ran when it shouldn't have" — never "the engine read the
      unselected series". BD-23 (wrong-series-read) is exercised
      separately, elsewhere, via `md()`; conflating the two here would
      leave it ambiguous which defect a red result indicated.
    - `test_core_accounting.py:221` (P2, subprocess determinism) — a raw
      `python -c "..."` string run in a FRESH subprocess that has no access
      to this `tests/backtest` package (no `helpers` import at all); it has
      no choice but to construct `MarketData` inline.

    Tests that genuinely need BOTH series populated with the SAME frame
    (F8) use the separate, explicit `md_both()` constructor below instead
    of calling this helper.
    """
    poison = _poison_frame(prices)
    if execution_mode == "next_open":
        return MarketData(open=prices, close=poison)
    if execution_mode == "next_close":
        return MarketData(open=poison, close=prices)
    raise ValueError(f"unknown execution_mode {execution_mode!r}")


def md_both(prices: pd.DataFrame) -> MarketData:
    """Explicit, NON-poisoned constructor for tests that genuinely need both
    `open` and `close` populated with the SAME frame (BD-23) — e.g. F8,
    which compares `next_open` vs `next_close` funding windows on identical
    underlying prices, not §4.2 price-series selection itself."""
    return MarketData(open=prices, close=prices)
