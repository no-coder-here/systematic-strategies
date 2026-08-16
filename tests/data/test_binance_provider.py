"""D§16 — `BinanceUMProvider`: price history ONLY, role-separation refusals,
gate, checksum-verified ingestion, single canonical frequency, raw cleanup,
seam-flagging on opt-in splice.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.binance.client import BinanceClient, BinanceChecksumError
from data.binance.provider import (
    BinanceUMProvider,
    FundingNotSupportedError,
    GateExceededError,
    SymbolNotMappedError,
    UnitEquivalenceError,
    assert_unit_equivalence,
    check_gate,
    estimate_gate,
)
from data.rate_limit import RateLimiter
from data.segments import splice_with_explicit_seam
from data import storage
from conftest import candle

HEADER_CSV_TEMPLATE = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore\n"
)


def _kline_row(open_time_ms, o, h, l, c, v, n):
    close_time = open_time_ms + 3_600_000 - 1
    return f"{open_time_ms},{o},{h},{l},{c},{v},{close_time},{v*o:.2f},{n},0,0,0\n"


def _zip_with_checksum(filename: str, csv_content: str):
    buf = io.BytesIO()
    csv_name = filename[: -len(".zip")] + ".csv"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(csv_name, csv_content)
    zip_bytes = buf.getvalue()
    digest = hashlib.sha256(zip_bytes).hexdigest()
    checksum_text = f"{digest}  {filename}\n"
    return zip_bytes, checksum_text


def _listing_xml(symbol: str, months: list, interval="1h"):
    keys = []
    for m in months:
        keys.append(f"data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{m}.zip")
        keys.append(f"data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{m}.zip.CHECKSUM")
    contents = "".join(
        f"<Contents><Key>{k}</Key><LastModified>2021-01-01T00:00:00.000Z</LastModified>"
        f"<ETag>&quot;x&quot;</ETag><Size>100</Size><StorageClass>STANDARD</StorageClass></Contents>"
        for k in keys
    )
    return (
        '<?xml version="1.0"?><ListBucketResult><IsTruncated>false</IsTruncated>'
        f"{contents}</ListBucketResult>"
    ).encode()


def _make_transport(symbol: str, months_data: dict):
    """`months_data`: {year_month: csv_content (WITH header)}."""
    files = {}
    for ym, csv_content in months_data.items():
        filename = f"{symbol}-1h-{ym}.zip"
        zip_bytes, checksum_text = _zip_with_checksum(filename, csv_content)
        files[filename] = (zip_bytes, checksum_text)

    def transport(url: str) -> bytes:
        if "s3-ap-northeast-1" in url:
            return _listing_xml(symbol, sorted(months_data.keys()))
        for filename, (zip_bytes, checksum_text) in files.items():
            if url.endswith(f"{filename}.CHECKSUM"):
                return checksum_text.encode()
            if url.endswith(filename):
                return zip_bytes
        raise AssertionError(f"unrouted URL: {url}")

    return transport


def _make_provider(symbol: str, months_data: dict, storage_base_dir):
    transport = _make_transport(symbol, months_data)
    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0), max_retries=1)
    provider = BinanceUMProvider(client=client, storage_base_dir=storage_base_dir)
    return provider


# ---------------------------------------------------------------------------
# D§16.1 (M29, M30) — role separation.
# ---------------------------------------------------------------------------


def test_venue_is_binance():
    provider = BinanceUMProvider(offline=True, storage_base_dir="/nonexistent")
    assert provider.venue == "Binance"


def test_get_funding_refused_M30():
    provider = BinanceUMProvider(offline=True, storage_base_dir="/nonexistent")
    with pytest.raises(FundingNotSupportedError):
        provider.get_funding(["BTC"], pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"))


def test_get_funding_coverage_refused_M30():
    provider = BinanceUMProvider(offline=True, storage_base_dir="/nonexistent")
    with pytest.raises(FundingNotSupportedError):
        provider.get_funding_coverage(["BTC"], pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"))


def test_get_universe_refused_binance_not_universe_authority():
    provider = BinanceUMProvider(offline=True, storage_base_dir="/nonexistent")
    with pytest.raises(DataIntegrityError):
        provider.get_universe()


def test_ingested_rows_labelled_proxy_never_native_M29(tmp_path):
    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + "".join(
        _kline_row(1704067200000 + i * 3_600_000, 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0 + i, 1 + i)
        for i in range(3)
    )
    provider = _make_provider("BTCUSDT", {ym: csv}, tmp_path)
    provider.ingest_symbol_1h("BTC")
    df = storage.read_binance_ohlcv_parquet(tmp_path, "BTC", check_provenance=False)
    assert (df["source_venue"] == "Binance").all()
    assert (df["native_or_proxy"] == "proxy").all()
    assert not (df["native_or_proxy"] == "native").any()
    assert not (df["source_venue"] == "Hyperliquid").any()


# ---------------------------------------------------------------------------
# D§16.3 (M35) — rename chains never auto-mapped, at the provider level.
# ---------------------------------------------------------------------------


def test_unmapped_symbol_raises_not_silently_empty(tmp_path):
    provider = _make_provider("XUSDT", {}, tmp_path)
    with pytest.raises(SymbolNotMappedError):
        provider.ingest_symbol_1h("MATIC")  # rename chain, deliberately unmapped


# ---------------------------------------------------------------------------
# D§16.6.1 (M32) — checksum verification is FATAL during ingestion.
# ---------------------------------------------------------------------------


def test_ingestion_raises_on_checksum_mismatch(tmp_path):
    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + _kline_row(1704067200000, 100, 101, 99, 100.5, 1.0, 1)
    filename = f"BTCUSDT-1h-{ym}.zip"
    zip_bytes, _ = _zip_with_checksum(filename, csv)
    bad_checksum = "0" * 64 + f"  {filename}\n"

    def transport(url: str) -> bytes:
        if "s3-ap-northeast-1" in url:
            return _listing_xml("BTCUSDT", [ym])
        if url.endswith(".CHECKSUM"):
            return bad_checksum.encode()
        return zip_bytes

    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0), max_retries=1)
    provider = BinanceUMProvider(client=client, storage_base_dir=tmp_path)
    with pytest.raises(BinanceChecksumError):
        provider.ingest_symbol_1h("BTC")


# ---------------------------------------------------------------------------
# D§16.4 (M37, M38) — single canonical frequency; raw cleanup.
# ---------------------------------------------------------------------------


def test_4h_and_1d_derived_not_separately_stored_M37(tmp_path):
    ym = "2024-01"
    idx = pd.date_range("2024-01-01", periods=8, freq="1h", tz="UTC")
    csv = HEADER_CSV_TEMPLATE + "".join(
        _kline_row(int(t.timestamp() * 1000), 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0, 1) for i, t in enumerate(idx)
    )
    provider = _make_provider("BTCUSDT", {ym: csv}, tmp_path)
    provider.ingest_symbol_1h("BTC")

    # ONLY a 1h parquet file exists -- no separate 4h/1d file was ever written.
    ohlcv_root = tmp_path / "processed" / "binance" / "ohlcv"
    assert (ohlcv_root / "1h" / "BTC.parquet").exists()
    assert not (ohlcv_root / "4h").exists()
    assert not (ohlcv_root / "1d").exists()

    # 4h IS obtainable, but DERIVED on demand from the 1h canonical file.
    offline_provider = BinanceUMProvider(offline=True, storage_base_dir=tmp_path)
    df_4h = offline_provider.get_ohlcv(["BTC"], "4h", idx[0], idx[0] + pd.Timedelta(hours=8))
    assert len(df_4h) == 2
    assert (df_4h["is_aggregated"] == True).all()  # noqa: E712


def test_raw_zip_never_persisted_after_ingestion_M38(tmp_path):
    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + _kline_row(1704067200000, 100, 101, 99, 100.5, 1.0, 1)
    provider = _make_provider("BTCUSDT", {ym: csv}, tmp_path)
    provider.ingest_symbol_1h("BTC")
    raw_root = tmp_path / "raw" / "binance"
    assert not raw_root.exists()  # never created -- D§16.4: raw MUST NOT be retained
    # sanity: confirm SOMETHING was actually written (the canonical processed file).
    assert (tmp_path / "processed" / "binance" / "ohlcv" / "1h" / "BTC.parquet").exists()


def test_provenance_carries_checksum_manifest_D16_6_3(tmp_path):
    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + _kline_row(1704067200000, 100, 101, 99, 100.5, 1.0, 1)
    provider = _make_provider("BTCUSDT", {ym: csv}, tmp_path)
    prov = provider.ingest_symbol_1h("BTC")
    assert len(prov.checksum_manifest_entries) == 1
    entry = prov.checksum_manifest_entries[0]
    assert entry["symbol"] == "BTCUSDT"
    assert entry["month"] == ym
    assert len(entry["sha256"]) == 64
    assert entry["rows"] == 1


# ---------------------------------------------------------------------------
# D§16.5 (M39) — pre-download gate, recomputed at RUNTIME.
# ---------------------------------------------------------------------------


def test_gate_passes_for_small_estimate(tmp_path):
    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + _kline_row(1704067200000, 100, 101, 99, 100.5, 1.0, 1)
    transport = _make_transport("BTCUSDT", {ym: csv})
    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0), max_retries=1)
    estimate = estimate_gate(["BTC"], client, sample_symbol="BTCUSDT")
    assert estimate.matched_symbol_count == 1
    check_gate(estimate)  # does not raise


def test_gate_exceeded_stops_before_download_M39(tmp_path):
    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + _kline_row(1704067200000, 100, 101, 99, 100.5, 1.0, 1)
    transport = _make_transport("BTCUSDT", {ym: csv})
    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0), max_retries=1)
    estimate = estimate_gate(["BTC"], client, sample_symbol="BTCUSDT")
    # Inflate the estimate to simulate a genuinely oversized universe/history
    # and confirm the gate STOPS rather than proceeding.
    inflated = estimate.__class__(
        matched_symbol_count=estimate.matched_symbol_count,
        total_monthly_files=estimate.total_monthly_files,
        estimated_row_count=estimate.estimated_row_count,
        estimated_transient_zip_gb=estimate.estimated_transient_zip_gb,
        estimated_processed_parquet_gb=999.0,
        measured_bytes_per_row_zip=estimate.measured_bytes_per_row_zip,
        measured_bytes_per_row_parquet=estimate.measured_bytes_per_row_parquet,
    )
    with pytest.raises(GateExceededError):
        check_gate(inflated)


def test_skipping_the_gate_check_would_proceed_anyway_M39():
    """Pins the discriminating behaviour directly: `check_gate` on an
    over-limit estimate MUST raise; simply not calling it (the M39 mutation)
    would let an oversized download proceed silently -- the raise is the
    only thing standing in the way.
    """
    from data.binance.provider import GateEstimate

    huge = GateEstimate(
        matched_symbol_count=210, total_monthly_files=999999, estimated_row_count=10**12,
        estimated_transient_zip_gb=999.0, estimated_processed_parquet_gb=999.0,
        measured_bytes_per_row_zip=50.0, measured_bytes_per_row_parquet=49.0,
    )
    with pytest.raises(GateExceededError):
        check_gate(huge)


# ---------------------------------------------------------------------------
# D§16.2 (M33, M34) — no spot substitution; no pre-listing fabrication.
# ---------------------------------------------------------------------------


def test_never_requests_spot_path_M33(tmp_path):
    """D§16.2 — Binance SPOT MUST NOT be substituted automatically. Every
    URL this pipeline ever constructs is under `futures/um/`, never `spot/`.
    """
    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + _kline_row(1704067200000, 100, 101, 99, 100.5, 1.0, 1)
    requested_urls = []
    base_transport = _make_transport("BTCUSDT", {ym: csv})

    def recording_transport(url: str) -> bytes:
        requested_urls.append(url)
        return base_transport(url)

    client = BinanceClient(transport=recording_transport, rate_limiter=RateLimiter(0.0), max_retries=1)
    provider = BinanceUMProvider(client=client, storage_base_dir=tmp_path)
    provider.ingest_symbol_1h("BTC")
    assert len(requested_urls) > 0
    for url in requested_urls:
        assert "futures/um" in url
        assert "/spot/" not in url


def test_no_fabrication_before_first_listed_month_M34(tmp_path):
    """D§16.2/D§16.3.4 (M34) — history before the Binance contract's first
    ARCHIVED month MUST NOT be fabricated. Only months actually present in
    the listing are ever ingested; the earliest bar in the canonical
    dataset is exactly the earliest bar of the earliest LISTED month, never
    earlier.
    """
    ym = "2024-03"  # deliberately NOT starting at "2024-01" -- simulates a
    # late-onboarded contract; nothing before this month is available.
    idx = pd.date_range("2024-03-01", periods=3, freq="1h", tz="UTC")
    csv = HEADER_CSV_TEMPLATE + "".join(
        _kline_row(int(t.timestamp() * 1000), 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0, 1) for i, t in enumerate(idx)
    )
    provider = _make_provider("BTCUSDT", {ym: csv}, tmp_path)
    provider.ingest_symbol_1h("BTC")
    df = storage.read_binance_ohlcv_parquet(tmp_path, "BTC", check_provenance=False)
    assert df["timestamp"].min() == idx[0]  # NOT fabricated any earlier


# ---------------------------------------------------------------------------
# D§16.3.4 (M36) — unit equivalence, MUST FAIL not warn.
# ---------------------------------------------------------------------------


def test_unit_equivalence_passes_for_comparable_price_levels():
    assert_unit_equivalence(hl_price=0.0012, binance_price=0.00125, hl_symbol="kPEPE", binance_symbol="1000PEPEUSDT")


def test_unit_equivalence_fails_hard_on_1000x_discrepancy_M36():
    """The exact M36 signature: if the multiplier were dropped (comparing
    kPEPE's per-1000-token price against a hypothetical bare per-token
    Binance quote), the ratio would be ~1000x. This MUST raise, not warn.
    """
    with pytest.raises(UnitEquivalenceError):
        assert_unit_equivalence(hl_price=1.2, binance_price=0.0012, hl_symbol="kPEPE", binance_symbol="PEPEUSDT_WRONG")


def test_unit_equivalence_would_not_raise_if_check_were_only_a_warning_M36():
    """Demonstrates the discriminating property the mutation table asks
    for: this MUST be an exception, not a warning that could be ignored. A
    version downgrading `UnitEquivalenceError` to a `warnings.warn` would
    make this test's `pytest.raises` block fail to catch anything.
    """
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(UnitEquivalenceError):
            assert_unit_equivalence(hl_price=100.0, binance_price=0.1, hl_symbol="kX", binance_symbol="1000XUSDT")
    # even if some unrelated warning fired, the important thing already
    # asserted above is that a hard exception WAS raised (not just a warning).


# ---------------------------------------------------------------------------
# D§16.7 (M40) — no splicing by default; seam bar flagged when opted in.
# ---------------------------------------------------------------------------


def test_no_splicing_by_default_D16_7():
    idx1 = pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC")
    idx2 = pd.date_range("2024-01-01 02:00", periods=2, freq="1h", tz="UTC")
    a = pd.DataFrame({"timestamp": idx1, "close": [1.0, 2.0]})
    b = pd.DataFrame({"timestamp": idx2, "close": [3.0, 4.0]})
    with pytest.raises(DataIntegrityError):
        splice_with_explicit_seam(a, b, idx2[0])  # opt_in defaults to False


def test_seam_bar_flagged_when_splicing_opted_in_M40():
    idx1 = pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC")
    idx2 = pd.date_range("2024-01-01 02:00", periods=2, freq="1h", tz="UTC")
    a = pd.DataFrame({"timestamp": idx1, "close": [1.0, 2.0]})
    b = pd.DataFrame({"timestamp": idx2, "close": [3.0, 4.0]})
    merged = splice_with_explicit_seam(a, b, idx2[0], opt_in=True)
    assert merged["seam_bar"].tolist() == [False, False, True, False]


def test_splice_rejects_overlapping_ranges():
    idx1 = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")  # extends PAST the seam
    idx2 = pd.date_range("2024-01-01 02:00", periods=2, freq="1h", tz="UTC")
    a = pd.DataFrame({"timestamp": idx1, "close": [1.0, 2.0, 3.0]})
    b = pd.DataFrame({"timestamp": idx2, "close": [3.0, 4.0]})
    with pytest.raises(DataIntegrityError):
        splice_with_explicit_seam(a, b, idx2[0], opt_in=True)


# ---------------------------------------------------------------------------
# D§16.7 DECISION 1 (M49) -- Binance and Hyperliquid canonical datasets are
# maintained SIDE BY SIDE, never merged by default.
# ---------------------------------------------------------------------------


def test_binance_and_hyperliquid_datasets_never_auto_merged_M49(tmp_path, multi_client):
    """DECISION 1 (v1.3, D§16.7) -- querying BOTH providers for the SAME
    symbol over the SAME overlapping window MUST return two INDEPENDENT,
    single-source frames; neither provider's output is silently combined
    with the other's (no implicit concatenation, no cross-venue fallback,
    no shared cache bleeding one venue's rows into the other's result). The
    ONLY path that ever combines two venues' rows is
    `segments.splice_with_explicit_seam`, and only with `opt_in=True`
    (already covered by M40's tests) -- there is no OTHER function anywhere
    in this layer that does so.
    """
    from data.hyperliquid.provider import HyperliquidProvider

    ym = "2024-01"
    csv = HEADER_CSV_TEMPLATE + "".join(
        _kline_row(1704067200000 + i * 3_600_000, 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0, 1) for i in range(5)
    )
    binance_provider = _make_provider("BTCUSDT", {ym: csv}, tmp_path)
    binance_provider.ingest_symbol_1h("BTC")

    start = pd.Timestamp("2024-01-01", tz="UTC")
    end = pd.Timestamp("2024-01-01 05:00", tz="UTC")
    bn_df = binance_provider.get_ohlcv(["BTC"], "1h", start, end)

    day0_ms = 1704067200000
    hl_candles_1h = {
        day0_ms + i * 3_600_000: candle(day0_ms + i * 3_600_000, 9000 + i, 9001 + i, 8999 + i, 9000.5 + i,
                                         5.0, 3, "1h", coin="BTC")
        for i in range(5)
    }
    # D§4.4 first_native_bar is inferred from the 1d series -- supply a
    # genuinely-traded 1d bar covering this window so the whole window is
    # NOT quarantined as pre-listing backfill.
    hl_candles_1d = {day0_ms: candle(day0_ms, 9000, 9010, 8990, 9005, 50.0, 20, "1d", coin="BTC")}
    hl_client, _ = multi_client(candles={"1h": hl_candles_1h, "1d": hl_candles_1d})
    hl_provider = HyperliquidProvider(client=hl_client)
    hl_df = hl_provider.get_ohlcv(["BTC"], "1h", start, end)

    # Each provider's output is exclusively its OWN venue -- never mixed.
    assert set(bn_df["source_venue"].unique()) == {"Binance"}
    assert set(hl_df["source_venue"].unique()) == {"Hyperliquid"}
    assert set(bn_df["native_or_proxy"].unique()) == {"proxy"}
    assert set(hl_df["native_or_proxy"].unique()) == {"native"}

    # Both cover the FULL requested window independently -- neither is
    # truncated, deduplicated against, or backfilled from the other.
    assert len(bn_df) == 5
    assert len(hl_df) == 5

    # The two price series are clearly DIFFERENT (Binance ~100, HL ~9000) --
    # proof that nothing silently substituted one venue's values for the
    # other's, and that simply having both providers available in the same
    # process does not implicitly merge their outputs.
    assert bn_df["close"].max() < 200
    assert hl_df["close"].min() > 1000

    # No function in this layer combines them without the explicit opt-in
    # gate -- `splice_with_explicit_seam` is the ONLY such path and it is
    # never called here.
    combined_naively = pd.concat([bn_df, hl_df], ignore_index=True)
    assert len(combined_naively) == len(bn_df) + len(hl_df)  # a plain concat is NOT deduplicated/merged for you
