"""D§16 — thin HTTP client for `data.binance.vision` monthly USDⓈ-M kline
archives: S3-style directory listing, zip+checksum download, per-file CSV
header sniffing (F17), and SHA-256 checksum verification (D§16.6.1).

Uses only the Python standard library (`urllib`, `zipfile`, `hashlib`,
`csv`) — no `requests`/`boto3` dependency. Talks only to the PUBLIC,
read-only `data.binance.vision` static archive and (for symbol-existence
review only, not at runtime) `fapi.binance.com`'s public `exchangeInfo`.
Never touches an authenticated/account endpoint, never places orders.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import time
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Optional

from ..rate_limit import RateLimiter

__all__ = [
    "BinanceArchiveError",
    "BinanceChecksumError",
    "BinanceClient",
    "DATA_VISION_BASE",
    "LISTING_BASE",
    "KLINE_CSV_FIELDS",
    "verify_checksum",
    "extract_csv_from_zip",
    "sniff_has_header",
    "parse_kline_csv",
]

DATA_VISION_BASE = "https://data.binance.vision"
# S3-compatible bucket listing endpoint (read-only, public; no AWS
# credentials or `--request-payer` needed — this is a PUBLIC bucket, unlike
# the Hyperliquid archive buckets in D§14, F9).
LISTING_BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

# Binance UM futures monthly kline CSV column order (both header-present and
# header-absent eras, F17) — 12 columns, header-absent files never include
# the trailing "ignore" header name, but the DATA columns are identical.
KLINE_CSV_FIELDS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


class BinanceArchiveError(Exception):
    """Transport error, non-200, malformed body, or missing archive object."""


class BinanceChecksumError(BinanceArchiveError):
    """D§16.6.1 (M32) — SHA-256 mismatch. FATAL for that file, never a
    warn-and-accept, never silently retried into acceptance.
    """


class BinanceClient:
    """`fetch_bytes`/`transport` seam mirrors `HyperliquidClient`'s design
    (D§11.1): production code uses real `urllib`; tests inject a
    deterministic, OFFLINE `transport: Callable[[str], bytes]` (raises to
    simulate a transport failure; URL routing is the test's responsibility).
    """

    def __init__(
        self,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        timeout_seconds: float = 20.0,
        transport: Optional[Callable[[str], bytes]] = None,
        rate_limiter: Optional[RateLimiter] = None,
        min_interval_seconds: float = 0.02,
    ):
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._timeout = timeout_seconds
        self._transport = transport
        # Proactive rate limiting (D§16's bulk download makes the v1.0
        # self-reported "retry-on-failure only" gap material): shared across
        # however many worker threads the caller uses for concurrent
        # downloads, since `RateLimiter` is thread-safe.
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter(min_interval_seconds)

    # -- transport -----------------------------------------------------

    def _get(self, url: str, *, allow_404: bool = False) -> Optional[bytes]:
        self._rate_limiter.wait()
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                if self._transport is not None:
                    return self._transport(url)
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec - public static archive
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and allow_404:
                    return None
                last_exc = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
            if attempt + 1 < self._max_retries:
                time.sleep(self._backoff_base * (2**attempt))
                continue
            raise BinanceArchiveError(f"GET {url} failed after {self._max_retries} attempt(s): {last_exc}") from last_exc
        raise BinanceArchiveError(f"GET {url} failed: {last_exc}")  # pragma: no cover - unreachable

    # -- discovery -------------------------------------------------------

    def list_available_months(self, symbol: str, interval: str = "1h") -> list:
        """Lists the monthly `.zip` archives actually present for `symbol`,
        via the S3-compatible bucket listing endpoint. Used to determine
        D§16.2's "first genuine trading candle" start point WITHOUT guessing
        month-by-month with 404s, and to recompute the D§16.5 gate at
        runtime from REAL counts rather than the frozen estimate.

        Returns a sorted list of `"YYYY-MM"` strings.
        """
        prefix = f"data/futures/um/monthly/klines/{symbol}/{interval}/"
        url = f"{LISTING_BASE}?delimiter=/&prefix={prefix}&max-keys=2000"
        body = self._get(url)
        xml = body.decode("utf-8")
        if "<IsTruncated>true</IsTruncated>" in xml:
            raise BinanceArchiveError(
                f"listing for {symbol}/{interval} was TRUNCATED (>2000 keys) — pagination not implemented, "
                "would silently under-report available months"
            )
        keys = re.findall(r"<Key>(.*?)</Key>", xml)
        zips = sorted(k for k in keys if k.endswith(".zip"))
        prefix_name = f"{symbol}-{interval}-"
        return [k.split("/")[-1][len(prefix_name):-len(".zip")] for k in zips]

    # -- monthly file fetch -----------------------------------------------

    def fetch_month(self, symbol: str, interval: str, year_month: str) -> Optional[dict]:
        """Fetches ONE monthly kline archive: the `.zip` payload and its
        `.zip.CHECKSUM` sidecar. Returns `None` if the object genuinely does
        not exist (404) — this is expected for months outside a symbol's
        listed range and MUST be distinguished from a transport failure
        (which raises).

        Returns `{"filename", "zip_bytes", "checksum_text"}`.
        """
        filename = f"{symbol}-{interval}-{year_month}.zip"
        zip_url = f"{DATA_VISION_BASE}/data/futures/um/monthly/klines/{symbol}/{interval}/{filename}"
        checksum_url = f"{zip_url}.CHECKSUM"
        zip_bytes = self._get(zip_url, allow_404=True)
        if zip_bytes is None:
            return None
        checksum_bytes = self._get(checksum_url, allow_404=True)
        if checksum_bytes is None:
            raise BinanceArchiveError(f"{filename} exists but its .CHECKSUM sidecar is missing (D§16.6.1)")
        return {"filename": filename, "zip_bytes": zip_bytes, "checksum_text": checksum_bytes.decode("utf-8")}


def verify_checksum(zip_bytes: bytes, checksum_text: str, expected_filename: str) -> str:
    """D§16.6.1 (M32) — verifies `zip_bytes`' SHA-256 against the
    `sha256sum`-format `.CHECKSUM` sidecar content. A mismatch is FATAL for
    that file — raises `BinanceChecksumError`, never a warning, never
    silently accepted.

    Returns the verified sha256 hex digest (for the D§16.6.3 checksum
    manifest).
    """
    parts = checksum_text.strip().split()
    if len(parts) < 2:
        raise BinanceChecksumError(f"malformed .CHECKSUM content: {checksum_text!r}")
    expected_hash, checksum_filename = parts[0], parts[1]
    if checksum_filename != expected_filename:
        raise BinanceChecksumError(
            f"checksum sidecar names {checksum_filename!r}, expected {expected_filename!r} — "
            "wrong file paired with wrong checksum is exactly the kind of silent corruption D§16.6.1 guards against"
        )
    actual_hash = hashlib.sha256(zip_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise BinanceChecksumError(
            f"SHA-256 MISMATCH for {expected_filename}: expected {expected_hash}, got {actual_hash} "
            "(D§16.6.1, M32) — FATAL, not a warning"
        )
    return actual_hash


def extract_csv_from_zip(zip_bytes: bytes, expected_filename: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        csv_name = expected_filename[: -len(".zip")] + ".csv"
        if csv_name not in names:
            if len(names) != 1:
                raise BinanceArchiveError(f"unexpected zip contents for {expected_filename}: {names}")
            csv_name = names[0]
        return zf.read(csv_name)


def sniff_has_header(csv_bytes: bytes) -> bool:
    """F17 (M31) — the CSV header MUST be sniffed PER FILE: `2020-01` era
    files have NO header row; `2022-06`+ era files DO. Assuming either form
    unconditionally silently shifts every row by one (a header row parsed as
    data, or a real first bar discarded as if it were a header).

    Sniff rule: the first field of the first line is `open_time` (the exact
    header string) => header present. Otherwise the first field MUST parse
    as an integer (an epoch-millisecond `open_time` value) => header absent.
    Anything else is a malformed file and raises rather than guessing.
    """
    first_line = csv_bytes.split(b"\n", 1)[0].decode("utf-8").strip()
    first_field = first_line.split(",")[0]
    if first_field == "open_time":
        return True
    try:
        int(first_field)
    except ValueError:
        raise BinanceArchiveError(
            f"cannot sniff CSV header: first field {first_field!r} is neither 'open_time' nor an integer (F17)"
        )
    return False


def parse_kline_csv(csv_bytes: bytes) -> list:
    """F17/D§16.6.2 (M31) — parses ONE monthly kline CSV, sniffing the
    header per-file rather than assuming its presence/absence.

    Returns a list of dicts with RAW string/native fields (open_time as int
    ms, OHLCV as str, count as int) — normalization to the D§4.1 schema is
    the provider's job, not this client's.
    """
    has_header = sniff_has_header(csv_bytes)
    text = csv_bytes.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if has_header:
        rows = rows[1:]
    out = []
    for r in rows:
        if not r or (len(r) == 1 and r[0] == ""):
            continue
        if len(r) < 11:
            raise BinanceArchiveError(f"malformed kline CSV row (expected >=11 fields, got {len(r)}): {r}")
        out.append(
            {
                "open_time": int(r[0]),
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5],
                "close_time": int(r[6]),
                "quote_volume": r[7],
                "count": int(r[8]),
                "taker_buy_volume": r[9],
                "taker_buy_quote_volume": r[10],
            }
        )
    return out
