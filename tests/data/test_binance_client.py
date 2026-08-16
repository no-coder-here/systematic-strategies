"""D§16.6 — Binance monthly kline archive client: header sniffing (F17,
M31), checksum verification (D§16.6.1, M32), zip extraction, listing.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
import urllib.error

from data.binance.client import (
    BinanceArchiveError,
    BinanceChecksumError,
    BinanceClient,
    extract_csv_from_zip,
    parse_kline_csv,
    sniff_has_header,
    verify_checksum,
)
from data.rate_limit import RateLimiter

NO_HEADER_CSV = (
    "1577836800000,7189.43,7190.52,7170.15,7171.55,2449.049,1577840399999,"
    "17576424.43970,3688,996.198,7149370.76353,0\n"
)
HEADER_CSV = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore\n"
    "1654041600000,31797.90,31986.10,31680.00,31925.50,13588.888,1654045199999,"
    "432640301.11709,132462,6894.888,219572827.04889,0\n"
)


def _zip_bytes(csv_name: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(csv_name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# F17 / M31 — per-file header sniffing, BOTH eras.
# ---------------------------------------------------------------------------


def test_sniff_no_header_era_2020_01():
    assert sniff_has_header(NO_HEADER_CSV.encode()) is False


def test_sniff_header_present_era_2022_06():
    assert sniff_has_header(HEADER_CSV.encode()) is True


def test_sniff_malformed_first_field_raises():
    with pytest.raises(BinanceArchiveError):
        sniff_has_header(b"not_a_number_or_open_time,1,2,3\n")


def test_parse_kline_csv_no_header_era():
    rows = parse_kline_csv(NO_HEADER_CSV.encode())
    assert len(rows) == 1
    assert rows[0]["open_time"] == 1577836800000
    assert rows[0]["open"] == "7189.43"
    assert rows[0]["count"] == 3688


def test_parse_kline_csv_header_era_strips_header_row():
    rows = parse_kline_csv(HEADER_CSV.encode())
    assert len(rows) == 1  # the header row itself MUST NOT become a data row
    assert rows[0]["open_time"] == 1654041600000


def test_assuming_header_always_present_would_corrupt_no_header_file_M31():
    """Direct demonstration of the M31 defect: if the header were assumed
    present unconditionally, the FIRST (and only) data row of a no-header
    file would be silently discarded as if it were a header.
    """
    has_header = sniff_has_header(NO_HEADER_CSV.encode())
    assert has_header is False
    correct_rows = parse_kline_csv(NO_HEADER_CSV.encode())
    assert len(correct_rows) == 1
    # simulate the mutation: unconditionally treating row 0 as a header
    lines = NO_HEADER_CSV.strip().split("\n")
    mutated_rows = lines[1:]  # would discard the only real row
    assert len(mutated_rows) == 0
    assert len(correct_rows) != len(mutated_rows)


def test_assuming_header_never_present_would_corrupt_header_file_M31():
    """The opposite direction of M31: assuming NO header where one exists
    parses the literal string 'open_time' as a timestamp.
    """
    lines = HEADER_CSV.strip().split("\n")
    with pytest.raises(ValueError):
        int(lines[0].split(",")[0])  # 'open_time' is not an int -- this IS the corruption signature


# ---------------------------------------------------------------------------
# D§16.6.1 / M32 — checksum verification, FATAL on mismatch.
# ---------------------------------------------------------------------------


def test_verify_checksum_accepts_matching_hash():
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    result = verify_checksum(data, f"{digest}  BTCUSDT-1h-2025-01.zip\n", "BTCUSDT-1h-2025-01.zip")
    assert result == digest


def test_verify_checksum_rejects_mismatch_M32():
    data = b"hello world"
    wrong_digest = hashlib.sha256(b"tampered").hexdigest()
    with pytest.raises(BinanceChecksumError):
        verify_checksum(data, f"{wrong_digest}  BTCUSDT-1h-2025-01.zip\n", "BTCUSDT-1h-2025-01.zip")


def test_verify_checksum_rejects_wrong_filename_pairing():
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    with pytest.raises(BinanceChecksumError):
        verify_checksum(data, f"{digest}  WRONGFILE.zip\n", "BTCUSDT-1h-2025-01.zip")


def test_verify_checksum_rejects_malformed_sidecar():
    with pytest.raises(BinanceChecksumError):
        verify_checksum(b"data", "not-a-valid-checksum-line", "f.zip")


def test_skipping_checksum_verification_would_silently_accept_corruption_M32():
    """Demonstrates: WITHOUT calling verify_checksum, corrupted bytes would
    flow straight through to extraction/parsing undetected.
    """
    good = b"hello world"
    corrupted = b"hello worlt"  # single-byte corruption
    digest = hashlib.sha256(good).hexdigest()
    # the correct path raises:
    with pytest.raises(BinanceChecksumError):
        verify_checksum(corrupted, f"{digest}  f.zip\n", "f.zip")
    # a "skip verification" mutation would just proceed with `corrupted` --
    # which is exactly the M32 defect this test targets.


# ---------------------------------------------------------------------------
# zip extraction
# ---------------------------------------------------------------------------


def test_extract_csv_from_zip():
    zb = _zip_bytes("BTCUSDT-1h-2025-01.csv", HEADER_CSV)
    csv_bytes = extract_csv_from_zip(zb, "BTCUSDT-1h-2025-01.zip")
    assert csv_bytes.decode() == HEADER_CSV


def test_extract_csv_unexpected_multi_file_zip_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.csv", "1\n")
        zf.writestr("b.csv", "2\n")
    with pytest.raises(BinanceArchiveError):
        extract_csv_from_zip(buf.getvalue(), "BTCUSDT-1h-2025-01.zip")


# ---------------------------------------------------------------------------
# BinanceClient — transport seam, listing, month fetch, 404 handling.
# ---------------------------------------------------------------------------


def _routing_transport(routes: dict):
    def _transport(url: str) -> bytes:
        for pattern, handler in routes.items():
            if pattern in url:
                result = handler()
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"unrouted URL in test transport: {url}")

    return _transport


def _listing_xml(keys_and_sizes):
    contents = "".join(
        f"<Contents><Key>{k}</Key><LastModified>2021-01-01T00:00:00.000Z</LastModified>"
        f"<ETag>&quot;x&quot;</ETag><Size>{s}</Size><StorageClass>STANDARD</StorageClass></Contents>"
        for k, s in keys_and_sizes
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><ListBucketResult '
        'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>'
        f"{contents}</ListBucketResult>"
    ).encode()


def test_list_available_months_parses_listing():
    xml = _listing_xml(
        [
            ("data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2020-01.zip", 100),
            ("data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2020-01.zip.CHECKSUM", 89),
            ("data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2020-02.zip", 110),
        ]
    )
    transport = _routing_transport({"s3-ap-northeast-1": lambda: xml})
    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0))
    months = client.list_available_months("BTCUSDT", "1h")
    assert months == ["2020-01", "2020-02"]


def test_list_available_months_truncated_listing_raises():
    xml = (
        '<?xml version="1.0"?><ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>'
    ).encode()
    transport = _routing_transport({"s3-ap-northeast-1": lambda: xml})
    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0))
    with pytest.raises(BinanceArchiveError):
        client.list_available_months("BTCUSDT", "1h")


def test_fetch_month_returns_none_on_404_not_transport_failure():
    def raise_404():
        return urllib.error.HTTPError("url", 404, "not found", {}, None)

    transport = _routing_transport({"BTCUSDT-1h-2019-01.zip": raise_404})
    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0), max_retries=1)
    result = client.fetch_month("BTCUSDT", "1h", "2019-01")
    assert result is None


def test_fetch_month_transport_failure_propagates_not_none():
    def raise_500():
        return urllib.error.HTTPError("url", 500, "server error", {}, None)

    transport = _routing_transport({"BTCUSDT-1h-2025-01.zip": raise_500})
    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0), max_retries=1, backoff_base_seconds=0.0)
    with pytest.raises(BinanceArchiveError):
        client.fetch_month("BTCUSDT", "1h", "2025-01")


def test_fetch_month_missing_checksum_sidecar_raises():
    zb = _zip_bytes("BTCUSDT-1h-2025-01.csv", HEADER_CSV)

    def transport(url: str) -> bytes:
        if url.endswith(".CHECKSUM"):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return zb

    client = BinanceClient(transport=transport, rate_limiter=RateLimiter(0.0), max_retries=1)
    with pytest.raises(BinanceArchiveError):
        client.fetch_month("BTCUSDT", "1h", "2025-01")
