"""QR-PREP-001 P§1.7 — offline, mocked-client tests for
`data.hyperliquid.ingest`. NEVER touch the network (D§11.1).

One test per P§1.7 behaviour, each independently mutation-provable:
    1. older rows retained when the fetch window starts later
    2. conflict counted and existing value preserved
    3. row-count regression refused
    4. min(timestamp) regression refused
    5. second identical run leaves parquet bytes unchanged
    6. provenance version stamped from the constant, not a literal
    7. source-restriction: the ingest module imports no trade/fill/L2/
       asset_ctxs module
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from data import storage
from data.hyperliquid import ingest as ingest_mod
from data.hyperliquid.ingest import (
    AccretionRegressionError,
    accrete_ohlcv,
    ingest_symbol_1h,
    reconcile_conflicting_bars,
    repair_trailing_unclosed_bar,
    _assert_no_regression,
)
from data.hyperliquid.provider import HyperliquidProvider
from data.schemas import OHLCV_COLUMNS

from conftest import DAY_MS, HOUR_MS, candle

DAY0 = pd.Timestamp("2024-01-01", tz="UTC")
DAY0_MS = int(DAY0.timestamp() * 1000)


def _hour(n: int) -> int:
    return DAY0_MS + n * HOUR_MS


def _hour_ts(n: int) -> pd.Timestamp:
    return pd.Timestamp(_hour(n), unit="ms", tz="UTC")


def _row(hour: int, close: float, symbol: str = "BTC", volume: float = 5.0, trade_count: int = 3,
         native_traded: bool = True) -> dict:
    return {
        "timestamp": _hour_ts(hour),
        "symbol": symbol,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": volume,
        "trade_count": trade_count,
        "native_traded": native_traded,
        "source_venue": "Hyperliquid",
        "native_or_proxy": "native",
        "source_type": "hyperliquid_candle",
        "dataset_id": "hyperliquid.ohlcv.1h.BTC",
    }


def _frame(rows: list) -> pd.DataFrame:
    if not rows:
        df = pd.DataFrame(columns=OHLCV_COLUMNS)
    else:
        df = pd.DataFrame(rows)[OHLCV_COLUMNS]
    df["symbol"] = df["symbol"].astype("string")
    df["source_venue"] = df["source_venue"].astype("string")
    df["native_or_proxy"] = df["native_or_proxy"].astype("string")
    df["source_type"] = df["source_type"].astype("string")
    df["dataset_id"] = df["dataset_id"].astype("string")
    df["trade_count"] = df["trade_count"].astype("int64")
    df["native_traded"] = df["native_traded"].astype("bool")
    return df.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)


# ---------------------------------------------------------------------------
# 1. older rows retained when the fetch window starts later
# ---------------------------------------------------------------------------


def test_older_rows_retained_when_fetch_window_starts_later():
    # `existing` covers hours 0-2 (persisted from an earlier, wider backfill).
    existing = _frame([_row(0, 100.0), _row(1, 101.0), _row(2, 102.0)])
    # `fetched` simulates a REFRESH whose window starts LATER (hour 2 onward,
    # per candleSnapshot's rolling-window nature) and only sees hours 2-4.
    fetched = _frame([_row(2, 102.0), _row(3, 103.0), _row(4, 104.0)])

    union, conflicts = accrete_ohlcv(existing, fetched)

    assert conflicts == 0
    assert len(union) == 5  # hours 0,1,2,3,4 -- NOT truncated to the fetch window
    assert union["timestamp"].min() == _hour_ts(0)
    assert set(union["timestamp"]) == {_hour_ts(h) for h in range(5)}


# ---------------------------------------------------------------------------
# 2. conflict counted and existing value preserved
# ---------------------------------------------------------------------------


def test_conflict_counted_and_existing_value_preserved():
    existing = _frame([_row(0, 100.0), _row(1, 101.0)])
    # `fetched` re-reports hour 1 with a DIFFERENT close (a hypothetical
    # re-quotation from the rolling source) and adds a genuinely new hour 2.
    fetched = _frame([_row(1, 999.0), _row(2, 102.0)])

    union, conflicts = accrete_ohlcv(existing, fetched)

    assert conflicts == 1
    assert len(union) == 3  # hour 1 was a conflict (not a new row), hour 2 is new
    preserved = union.loc[union["timestamp"] == _hour_ts(1), "close"].iloc[0]
    assert preserved == 101.0  # EXISTING value wins, never silently overwritten


def test_identical_overlap_is_not_counted_as_a_conflict():
    existing = _frame([_row(0, 100.0), _row(1, 101.0)])
    fetched = _frame([_row(1, 101.0), _row(2, 102.0)])  # hour 1 identical to existing

    union, conflicts = accrete_ohlcv(existing, fetched)

    assert conflicts == 0
    assert len(union) == 3


# ---------------------------------------------------------------------------
# 3. row-count regression refused
# ---------------------------------------------------------------------------


def test_row_count_regression_refused():
    existing = _frame([_row(h, 100.0 + h) for h in range(5)])
    # A fabricated "candidate union" that DROPPED a persisted row -- this must
    # never happen via `accrete_ohlcv` itself (which is structurally
    # incapable of it), but the independent guard MUST catch it regardless,
    # proving the invariant is impossible rather than merely unlikely.
    shrunk_union = existing.iloc[:-1].reset_index(drop=True)

    with pytest.raises(AccretionRegressionError, match="REDUCE row count"):
        _assert_no_regression(existing, shrunk_union, "BTC")


# ---------------------------------------------------------------------------
# 4. min(timestamp) regression refused
# ---------------------------------------------------------------------------


def test_min_timestamp_regression_refused():
    existing = _frame([_row(h, 100.0 + h) for h in range(5)])
    # A fabricated candidate union that walked the start FORWARD (dropped the
    # earliest persisted row and added a later one) -- same row count, but a
    # later min(timestamp). This is EXACTLY the defect P§1.3.4 exists to
    # prevent (a sealed OOS window silently invalidated).
    walked_forward = pd.concat(
        [existing.iloc[1:], _frame([_row(5, 999.0)])], ignore_index=True
    ).sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    assert len(walked_forward) == len(existing)
    assert walked_forward["timestamp"].min() > existing["timestamp"].min()

    with pytest.raises(AccretionRegressionError, match="move min\\(timestamp\\) LATER"):
        _assert_no_regression(existing, walked_forward, "BTC")


# ---------------------------------------------------------------------------
# 5. second identical run leaves parquet bytes unchanged (P§1.5 no-op)
# ---------------------------------------------------------------------------


def _make_provider(multi_client, candles_1d=None, candles_1h=None):
    client, transport = multi_client(candles={"1d": candles_1d or {}, "1h": candles_1h or {}})
    return HyperliquidProvider(client=client), transport


def _fixture_candles():
    candles_1d = {DAY0_MS: candle(DAY0_MS, 100, 105, 99, 103, 50.0, 20, "1d")}
    candles_1h = {_hour(h): candle(_hour(h), 100 + h, 101 + h, 99 + h, 100 + h, 5.0, 3, "1h") for h in range(10)}
    return candles_1d, candles_1h


def test_second_identical_run_leaves_parquet_bytes_unchanged(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)  # well past the last fixture bar (hour 9); no new data exists

    result1 = ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    assert result1.mode == "full_backfill"
    assert result1.parquet_written is True
    assert result1.rows == 10

    parquet_path = storage.ohlcv_parquet_path(tmp_path, "1h", "BTC")
    bytes1 = parquet_path.read_bytes()

    result2 = ingest_symbol_1h(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))
    assert result2.mode == "refresh"
    assert result2.conflicts == 0
    assert result2.rows == 10
    assert result2.parquet_written is False  # true content no-op (P§1.5)

    bytes2 = parquet_path.read_bytes()
    assert bytes1 == bytes2  # byte-for-byte unchanged


def test_refresh_with_no_new_data_is_a_content_noop_even_with_conflicting_refetch(tmp_path, multi_client):
    """A REFRESH that re-fetches an already-persisted window and gets back
    IDENTICAL values (the ordinary case: the venue's rolling window still
    reports the same closed bars) must not rewrite the parquet file, and
    must report zero conflicts and the unchanged row count.
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)

    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    calls_before = len(transport.calls)
    result = ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    assert len(transport.calls) > calls_before  # the mocked transport WAS exercised, never bypassed
    assert result.conflicts == 0
    assert result.parquet_written is False


# ---------------------------------------------------------------------------
# 6. provenance version stamped from the constant, not a literal
# ---------------------------------------------------------------------------


def test_provenance_version_stamped_from_constant_not_literal(tmp_path, multi_client, monkeypatch):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)

    sentinel = "qr-data-001-vSENTINEL"
    monkeypatch.setattr(ingest_mod, "PROCESSING_VERSION", sentinel)

    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)

    prov = storage.read_provenance(tmp_path, storage.ohlcv_dataset_id("1h", "BTC"))
    # If the code had a hardcoded literal instead of reading the module
    # constant, patching `ingest_mod.PROCESSING_VERSION` would have no
    # effect and this would still read the real PROCESSING_VERSION.
    assert prov.processing_version == sentinel


# ---------------------------------------------------------------------------
# 7. source-restriction: the ingest module never imports a trade/fill/L2/
#    asset_ctxs module (P§1.2)
# ---------------------------------------------------------------------------


def _imported_module_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                prefix = "." * node.level
                names.add(f"{prefix}{node.module}")
            elif node.level:
                names.add("." * node.level)
    return names


def test_ingest_module_does_not_import_trade_fill_l2_or_asset_ctxs_modules():
    ingest_path = Path(ingest_mod.__file__)
    names = _imported_module_names(ingest_path)
    forbidden_fragments = ("archive", "oracle", "asset_ctxs", "node_trades", "node_fills")
    offending = [n for n in names if any(frag in n for frag in forbidden_fragments)]
    assert not offending, f"ingest.py imports a forbidden module (P§1.2): {offending}"


def test_ast_scan_self_test_detects_a_synthetic_violation():
    import tempfile

    src = "from . import archive\nfrom .oracle import resolve\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        tmp_path_file = Path(f.name)
    try:
        names = _imported_module_names(tmp_path_file)
        offending = [n for n in names if "archive" in n or "oracle" in n]
        assert offending, "self-test fixture must be detected as a violation"
    finally:
        tmp_path_file.unlink()


# ---------------------------------------------------------------------------
# Extra coverage: mode auto-selection and empty-history handling.
# ---------------------------------------------------------------------------


def test_full_backfill_selected_when_nothing_persisted(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    result = ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(20))
    assert result.mode == "full_backfill"
    assert result.parquet_written is True


def test_refresh_selected_when_already_persisted(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(20))
    result2 = ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(21))
    assert result2.mode == "refresh"


def test_force_full_backfill_overrides_auto_selection(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(20))
    result2 = ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(21), force_full_backfill=True)
    assert result2.mode == "full_backfill"


def test_no_native_history_writes_nothing(tmp_path, multi_client):
    # No 1d candle at all -> first_native_1d is None -> get_ohlcv always empty.
    provider, transport = _make_provider(multi_client, candles_1d={}, candles_1h={})
    result = ingest_symbol_1h(provider, tmp_path, "NEWCOIN", now=_hour_ts(20))
    assert result.rows == 0
    assert result.parquet_written is False
    assert result.start is None and result.end is None
    assert not storage.ohlcv_parquet_path(tmp_path, "1h", "NEWCOIN").exists()


# ---------------------------------------------------------------------------
# Repair-cycle-1 D1 -- never persist a bar that has not closed.
# ---------------------------------------------------------------------------


def test_unclosed_bar_is_not_persisted(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()  # 10 fixture bars, hours 0-9
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(9) + pd.Timedelta(minutes=30)  # hour 9 is still IN PROGRESS

    result = ingest_symbol_1h(provider, tmp_path, "BTC", now=now)

    assert result.rows == 9  # hours 0-8 persisted; hour 9 (unclosed) withheld
    assert result.end == _hour_ts(8)
    persisted = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert _hour_ts(9) not in set(persisted["timestamp"])
    assert len(persisted) == 9


def test_bar_is_persisted_once_it_closes_on_a_later_refresh(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now1 = _hour_ts(9) + pd.Timedelta(minutes=30)  # hour 9 withheld (unclosed)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=now1)

    now2 = _hour_ts(11)  # hour 9 has since fully closed
    result2 = ingest_symbol_1h(provider, tmp_path, "BTC", now=now2)

    assert result2.rows == 10
    assert result2.conflicts == 0  # hour 9's value never changed, only its closedness did
    persisted = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert _hour_ts(9) in set(persisted["timestamp"])


# ---------------------------------------------------------------------------
# Repair-cycle-1 -- an already-persisted CLOSED bar is never altered, even
# when a later refresh re-fetches a genuinely conflicting value for it
# (end-to-end through `ingest_symbol_1h`, not just the `accrete_ohlcv` unit).
# ---------------------------------------------------------------------------


def test_persisted_closed_bar_is_never_altered_by_a_later_conflicting_refetch(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)  # hours 0-9 all long closed

    result1 = ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    assert result1.rows == 10
    before = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    close_before = before.loc[before["timestamp"] == _hour_ts(7), "close"].iloc[0]
    assert close_before == 107.0  # from _fixture_candles: close = 100 + h

    # REFRESH only re-fetches the trailing few bars (P§1.5: last_persisted - 3
    # bars); hour 7 is inside that window (hours 6-9). Mutate it in place to
    # simulate a genuine venue re-quote of an ALREADY-CLOSED, ALREADY-
    # PERSISTED bar -- a real anomaly, never routine post-D1.
    transport.candles["1h"][_hour(7)] = candle(_hour(7), 100, 1000, 99, 999.0, 5.0, 3, "1h")

    result2 = ingest_symbol_1h(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    assert result2.conflicts == 1
    assert result2.rows == 10  # no row added or dropped
    after = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    close_after = after.loc[after["timestamp"] == _hour_ts(7), "close"].iloc[0]
    assert close_after == close_before == 107.0  # UNCHANGED: existing wins, closed bar never altered


# ---------------------------------------------------------------------------
# Repair-cycle-1 D2 -- explicit, narrow `repair_trailing_unclosed_bar`.
# ---------------------------------------------------------------------------


def test_repair_replaces_conflicting_now_closed_trailing_bar(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(9) + pd.Timedelta(minutes=30)  # hour 9 unclosed -> withheld by D1
    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    persisted = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert len(persisted) == 9  # hour 9 not yet persisted -- trailing row is hour 8

    # Simulate the D2 scenario directly: manually poison the ALREADY-
    # PERSISTED trailing row (hour 8) as if it had been written unclosed by
    # the pre-D1-fix code, then let the venue's value for that hour settle
    # to something different (a completed value) by the time of repair.
    poisoned = persisted.copy()
    poisoned.loc[poisoned["timestamp"] == _hour_ts(8), "close"] = 12345.0
    prov = storage.read_provenance(tmp_path, storage.ohlcv_dataset_id("1h", "BTC"))
    storage.write_ohlcv_parquet(tmp_path, "1h", "BTC", poisoned, prov)

    repair_now = _hour_ts(11)  # well past hour 8's close
    result = repair_trailing_unclosed_bar(provider, tmp_path, "BTC", now=repair_now)

    assert result.repaired is True
    assert result.reason == "repaired"
    assert result.timestamp == _hour_ts(8)
    assert result.old_row["close"] == 12345.0
    assert result.new_row["close"] == 108.0  # the venue's true (fixture) value for hour 8

    repaired_frame = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert len(repaired_frame) == 9  # row count unchanged
    assert repaired_frame["timestamp"].min() == _hour_ts(0)  # min(timestamp) unchanged
    fixed_close = repaired_frame.loc[repaired_frame["timestamp"] == _hour_ts(8), "close"].iloc[0]
    assert fixed_close == 108.0
    # every OTHER row is untouched
    others_before = poisoned.loc[poisoned["timestamp"] != _hour_ts(8)].reset_index(drop=True)
    others_after = repaired_frame.loc[repaired_frame["timestamp"] != _hour_ts(8)].reset_index(drop=True)
    pd.testing.assert_frame_equal(others_before, others_after)


def test_repair_skips_when_no_conflict(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    path = storage.ohlcv_parquet_path(tmp_path, "1h", "BTC")
    bytes_before = path.read_bytes()

    result = repair_trailing_unclosed_bar(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    assert result.repaired is False
    assert result.reason == "no_conflict_nothing_to_repair"
    assert path.read_bytes() == bytes_before  # untouched


def test_repair_refuses_when_fetched_bar_not_yet_closed(tmp_path, multi_client):
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(9) + pd.Timedelta(minutes=30)  # trailing persisted row is hour 8
    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    path = storage.ohlcv_parquet_path(tmp_path, "1h", "BTC")

    # Manually poison hour 8 (the trailing row) but call repair with a `now`
    # that has NOT yet elapsed past hour 8's close -- refuse.
    persisted = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    poisoned = persisted.copy()
    poisoned.loc[poisoned["timestamp"] == _hour_ts(8), "close"] = 12345.0
    prov = storage.read_provenance(tmp_path, storage.ohlcv_dataset_id("1h", "BTC"))
    storage.write_ohlcv_parquet(tmp_path, "1h", "BTC", poisoned, prov)
    bytes_after_poison = path.read_bytes()

    still_open_now = _hour_ts(8) + pd.Timedelta(minutes=10)  # hour 8 NOT yet closed at this `now`
    result = repair_trailing_unclosed_bar(provider, tmp_path, "BTC", now=still_open_now)

    assert result.repaired is False
    assert result.reason == "fetched_bar_not_yet_closed_refusing_repair"
    assert path.read_bytes() == bytes_after_poison  # file unchanged by the repair call itself
    after = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert after.loc[after["timestamp"] == _hour_ts(8), "close"].iloc[0] == 12345.0  # still poisoned


def test_repair_skips_when_no_existing_data(tmp_path, multi_client):
    provider, transport = _make_provider(multi_client, candles_1d={}, candles_1h={})
    result = repair_trailing_unclosed_bar(provider, tmp_path, "NEWCOIN", now=_hour_ts(20))
    assert result.repaired is False
    assert result.reason == "no_existing_data"
    assert result.timestamp is None


def test_repair_is_never_invoked_by_normal_ingest(tmp_path, multi_client, monkeypatch):
    """QR-PREP-001 D2 -- the repair path MUST NOT run implicitly as part of a
    normal refresh. If `ingest_symbol_1h` ever called it, monkeypatching it to
    raise would surface here.
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(20))

    def _boom(*args, **kwargs):
        raise AssertionError("repair_trailing_unclosed_bar MUST NOT be called by a normal refresh")

    monkeypatch.setattr(ingest_mod, "repair_trailing_unclosed_bar", _boom)

    # A normal refresh must complete without ever touching the patched-to-explode repair function.
    result = ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(21))
    assert result.mode == "refresh"


# ---------------------------------------------------------------------------
# Repair-cycle-2 D3 -- explicit, narrow `reconcile_conflicting_bars`, which
# extends D2's trailing-only repair to ANY conflicting persisted bar
# (interior rows included, not just the max-timestamp row).
# ---------------------------------------------------------------------------


def _poison_bar(tmp_path, symbol: str, ts: pd.Timestamp, bad_close: float) -> pd.DataFrame:
    """Test helper: overwrite one already-persisted bar's `close` value
    in-place on disk, simulating a bar written while unclosed under the
    pre-D1-fix code and later buried as an interior row by later refreshes.
    """
    persisted = storage.read_ohlcv_parquet(tmp_path, "1h", symbol, check_provenance=False)
    poisoned = persisted.copy()
    poisoned.loc[poisoned["timestamp"] == ts, "close"] = bad_close
    prov = storage.read_provenance(tmp_path, storage.ohlcv_dataset_id("1h", symbol))
    storage.write_ohlcv_parquet(tmp_path, "1h", symbol, poisoned, prov)
    return poisoned


def test_reconcile_replaces_conflicting_interior_bar(tmp_path, multi_client):
    """An INTERIOR (non-trailing) conflicting bar IS reconciled -- exactly the
    case `repair_trailing_unclosed_bar` (D2) can never reach, since D2 only
    ever inspects the max-timestamp row of the persisted frame.
    """
    candles_1d, candles_1h = _fixture_candles()  # fixture bars hours 0-9
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)  # all 10 fixture bars long closed

    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    persisted = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert len(persisted) == 10

    # Poison an INTERIOR row (hour 3, nowhere near the trailing hour 9) --
    # D2's trailing-only repair could never reach this row.
    _poison_bar(tmp_path, "BTC", _hour_ts(3), bad_close=12345.0)

    result = reconcile_conflicting_bars(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    assert len(result.reconciled) == 1
    assert result.reconciled[0].timestamp == _hour_ts(3)
    assert result.reconciled[0].old_row["close"] == 12345.0
    assert result.reconciled[0].new_row["close"] == 103.0  # fixture's true value: close = 100 + h
    assert result.reason == "reconciled"

    repaired_frame = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert len(repaired_frame) == 10  # row count unchanged
    assert repaired_frame["timestamp"].min() == _hour_ts(0)  # min(timestamp) unchanged
    fixed_close = repaired_frame.loc[repaired_frame["timestamp"] == _hour_ts(3), "close"].iloc[0]
    assert fixed_close == 103.0
    # every OTHER row is untouched
    others_before = persisted.loc[persisted["timestamp"] != _hour_ts(3)].reset_index(drop=True)
    others_after = repaired_frame.loc[repaired_frame["timestamp"] != _hour_ts(3)].reset_index(drop=True)
    pd.testing.assert_frame_equal(others_before, others_after)


def test_reconcile_never_writes_a_bar_that_has_not_closed(tmp_path, multi_client):
    """A conflicting persisted bar is NEVER replaced if, relative to the
    `now` supplied to `reconcile_conflicting_bars`, the freshly fetched value
    at that timestamp has not yet closed -- `_drop_unclosed_bars` (the same
    D1 guard `ingest_symbol_1h` uses) filters it out of the comparison
    entirely, so this path can never trade a closed persisted value for an
    unclosed one.
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)

    # Poison hour 5 in the persisted frame.
    _poison_bar(tmp_path, "BTC", _hour_ts(5), bad_close=12345.0)
    path = storage.ohlcv_parquet_path(tmp_path, "1h", "BTC")
    bytes_after_poison = path.read_bytes()

    # Call reconcile with a `now` at which hour 5 has NOT yet closed
    # (hour 5's bar closes at hour 6; `now` here is only 30 minutes into it).
    still_open_now = _hour_ts(5) + pd.Timedelta(minutes=30)
    result = reconcile_conflicting_bars(provider, tmp_path, "BTC", now=still_open_now)

    assert result.reconciled == ()
    assert result.reason == "no_conflicts_found"
    assert path.read_bytes() == bytes_after_poison  # file unchanged by the reconcile call
    after = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert after.loc[after["timestamp"] == _hour_ts(5), "close"].iloc[0] == 12345.0  # still poisoned


def test_reconcile_leaves_non_conflicting_bars_byte_identical(tmp_path, multi_client):
    """A symbol with NO conflicting bars is left completely untouched --
    byte-for-byte identical parquet file, zero rows reconciled.
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    path = storage.ohlcv_parquet_path(tmp_path, "1h", "BTC")
    bytes_before = path.read_bytes()

    result = reconcile_conflicting_bars(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    assert result.reconciled == ()
    assert result.reason == "no_conflicts_found"
    assert result.rows_checked == 10  # every persisted bar overlapped the fresh fetch
    assert path.read_bytes() == bytes_before  # byte-for-byte untouched


def test_reconcile_propagates_regression_guard_and_leaves_file_untouched(tmp_path, multi_client, monkeypatch):
    """The row-count / min(timestamp) guard (`_assert_no_regression`, shared
    with `ingest_symbol_1h` and `repair_trailing_unclosed_bar`) is still
    invoked by `reconcile_conflicting_bars`, and firing it REFUSES the write
    (leaves the prior parquet file untouched) exactly like every other write
    path in this module (P§1.3.4).
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    _poison_bar(tmp_path, "BTC", _hour_ts(3), bad_close=12345.0)  # forces the "reconciled" branch
    path = storage.ohlcv_parquet_path(tmp_path, "1h", "BTC")
    bytes_before = path.read_bytes()

    def _boom(existing, union, symbol):
        raise AccretionRegressionError(f"synthetic regression for {symbol} (test)")

    monkeypatch.setattr(ingest_mod, "_assert_no_regression", _boom)

    with pytest.raises(AccretionRegressionError, match="synthetic regression"):
        reconcile_conflicting_bars(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    assert path.read_bytes() == bytes_before  # guard fired BEFORE any write; file untouched


def test_reconcile_is_never_invoked_by_normal_ingest(tmp_path, multi_client, monkeypatch):
    """QR-PREP-001 D3 -- like D2, this path MUST NOT run implicitly as part
    of a normal refresh. If `ingest_symbol_1h` ever called it, monkeypatching
    it to raise would surface here.
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(20))

    def _boom(*args, **kwargs):
        raise AssertionError("reconcile_conflicting_bars MUST NOT be called by a normal refresh")

    monkeypatch.setattr(ingest_mod, "reconcile_conflicting_bars", _boom)

    result = ingest_symbol_1h(provider, tmp_path, "BTC", now=_hour_ts(21))
    assert result.mode == "refresh"


def test_reconcile_skips_when_no_existing_data(tmp_path, multi_client):
    provider, transport = _make_provider(multi_client, candles_1d={}, candles_1h={})
    result = reconcile_conflicting_bars(provider, tmp_path, "NEWCOIN", now=_hour_ts(20))
    assert result.reconciled == ()
    assert result.reason == "no_existing_data"
    assert result.rows_checked == 0


# ---------------------------------------------------------------------------
# Repair-cycle-3 -- `reconcile_conflicting_bars` must NEVER treat a rolling-
# window LEFT-EDGE PLACEHOLDER (`volume==0, trade_count==0`) or a fully
# absent live bar as authoritative over an already-persisted bar that shows
# genuine trading. Regression test built on the real observed SKR case
# (2026-08-19/20, persisted span begins 2026-01-22T08:00Z): as a bar's
# timestamp nears candleSnapshot's rolling ~209 day left edge, the venue
# degrades it to a flat zero-volume placeholder before it disappears from
# the response entirely.
# ---------------------------------------------------------------------------


def test_reconcile_refuses_left_edge_placeholder_downgrade_and_counts_it(tmp_path, multi_client):
    """The exact observed failure shape: two ALREADY-PERSISTED bars showing
    genuine trading (volume=5.0, trade_count=3, per `_fixture_candles`) are,
    on a later `candleSnapshot` call, reported back with `volume==0` and
    `trade_count==0` (hours 2, 3); two EARLIER bars (hours 0, 1) have rolled
    off the window and are absent from the live response entirely. Nothing
    may be overwritten in either case, and the placeholder-downgrade refusal
    must be counted and surfaced, not silently skipped.
    """
    candles_1d, candles_1h = _fixture_candles()  # fixture bars hours 0-9, volume=5.0/trade_count=3 each
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)

    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    persisted_before = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert len(persisted_before) == 10
    path = storage.ohlcv_parquet_path(tmp_path, "1h", "BTC")
    bytes_before = path.read_bytes()

    # Left edge of the rolling window retreating: hours 2 and 3 degrade to a
    # flat zero-volume/zero-trade placeholder (the key is still present, but
    # the value has collapsed) ...
    transport.candles["1h"][_hour(2)] = candle(_hour(2), 100, 100, 100, 100, 0.0, 0, "1h")
    transport.candles["1h"][_hour(3)] = candle(_hour(3), 100, 100, 100, 100, 0.0, 0, "1h")
    # ... while hours 0 and 1 have rolled off the window entirely and no
    # longer appear in the live response at all.
    del transport.candles["1h"][_hour(0)]
    del transport.candles["1h"][_hour(1)]

    result = reconcile_conflicting_bars(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    # Nothing was overwritten: the persisted file is byte-for-byte identical.
    assert path.read_bytes() == bytes_before
    persisted_after = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    pd.testing.assert_frame_equal(persisted_before, persisted_after)

    # The zero-volume-placeholder downgrade at hours 2 and 3 is detected and
    # REFUSED -- counted and reported, not silently skipped.
    assert result.reconciled == ()
    assert result.reason == "refused_placeholder_downgrade"
    assert len(result.refused) == 2
    refused_ts = {rb.timestamp for rb in result.refused}
    assert refused_ts == {_hour_ts(2), _hour_ts(3)}
    for rb in result.refused:
        assert rb.old_row["volume"] == 5.0  # genuine persisted trading, kept
        assert rb.old_row["trade_count"] == 3
        assert rb.new_row["volume"] == 0.0  # rejected placeholder
        assert rb.new_row["trade_count"] == 0

    # Hours 0 and 1, entirely absent from the live response, are also
    # untouched -- they never even become a replacement candidate, since
    # `common_keys` is the intersection of persisted and fetched keys.
    assert _hour_ts(0) in set(persisted_after["timestamp"])
    assert _hour_ts(1) in set(persisted_after["timestamp"])
    assert persisted_after.loc[persisted_after["timestamp"] == _hour_ts(0), "volume"].iloc[0] == 5.0
    assert persisted_after.loc[persisted_after["timestamp"] == _hour_ts(1), "volume"].iloc[0] == 5.0

    # rows_checked reflects only keys that overlapped the fetch (hours 2-9);
    # hours 0 and 1 never entered the comparison at all.
    assert result.rows_checked == 8


def test_reconcile_still_replaces_a_legitimate_non_placeholder_conflict(tmp_path, multi_client):
    """The repair-cycle-3 placeholder-downgrade guard must NOT block a
    genuine re-quote reconciliation (the original repair-cycle-2 fix) -- a
    conflicting fresh value that is NOT a flat zero-volume/zero-trade
    placeholder is still reconciled exactly as before.
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)

    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    persisted = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert len(persisted) == 10

    _poison_bar(tmp_path, "BTC", _hour_ts(3), bad_close=12345.0)

    result = reconcile_conflicting_bars(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    assert result.refused == ()  # NOT a placeholder downgrade -- the fresh fixture value has real volume
    assert len(result.reconciled) == 1
    assert result.reconciled[0].timestamp == _hour_ts(3)
    assert result.reconciled[0].new_row["close"] == 103.0
    assert result.reason == "reconciled"

    repaired_frame = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    fixed_close = repaired_frame.loc[repaired_frame["timestamp"] == _hour_ts(3), "close"].iloc[0]
    assert fixed_close == 103.0


def test_reconcile_reports_refused_alongside_a_genuine_reconciliation(tmp_path, multi_client):
    """A single call may both reconcile a genuine conflict AND refuse a
    separate placeholder-downgrade -- both outcomes are surfaced
    independently, and the refusal never gets silently absorbed into (or
    hidden by) the write triggered by the genuine reconciliation.
    """
    candles_1d, candles_1h = _fixture_candles()
    provider, transport = _make_provider(multi_client, candles_1d, candles_1h)
    now = _hour_ts(20)

    ingest_symbol_1h(provider, tmp_path, "BTC", now=now)
    _poison_bar(tmp_path, "BTC", _hour_ts(3), bad_close=12345.0)  # legitimate conflict -> reconciled

    # Hour 5 degrades to a placeholder in the live response -> must be refused.
    transport.candles["1h"][_hour(5)] = candle(_hour(5), 100, 100, 100, 100, 0.0, 0, "1h")

    result = reconcile_conflicting_bars(provider, tmp_path, "BTC", now=now + pd.Timedelta(hours=1))

    assert result.reason == "reconciled"
    assert len(result.reconciled) == 1
    assert result.reconciled[0].timestamp == _hour_ts(3)
    assert len(result.refused) == 1
    assert result.refused[0].timestamp == _hour_ts(5)
    assert result.refused[0].new_row["volume"] == 0.0

    repaired_frame = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC", check_provenance=False)
    assert repaired_frame.loc[repaired_frame["timestamp"] == _hour_ts(3), "close"].iloc[0] == 103.0
    # hour 5 NOT overwritten -- persisted genuine-trading value untouched.
    assert repaired_frame.loc[repaired_frame["timestamp"] == _hour_ts(5), "close"].iloc[0] == 105.0
    assert repaired_frame.loc[repaired_frame["timestamp"] == _hour_ts(5), "volume"].iloc[0] == 5.0
