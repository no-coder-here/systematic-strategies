"""D§5.5 / D§5.5.1 — `event_price` funding basis via `asset_ctxs` oracle
prices. The bulk `asset_ctxs` download is NOT AUTHORIZED (D§5.5.1 rule 2);
every test here uses a MOCKED row stream, never the network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from data.hyperliquid.oracle import GAP_TOLERANCE, extract_oracle_prices_for_events


def _ctx(t_ms, coin, oracle_px):
    return {"time": t_ms, "coin": coin, "oracle_px": oracle_px}


def _funding_events(rows):
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    return df[["timestamp", "symbol"]]


T0 = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.timestamp() * 1000)


def test_extract_joins_last_row_at_or_before_event_containing_minute():
    events = _funding_events([{"timestamp": T0 + pd.Timedelta(minutes=5), "symbol": "BTC"}])
    stream = [
        _ctx(_ms(T0 + pd.Timedelta(minutes=3)), "BTC", 100.0),
        _ctx(_ms(T0 + pd.Timedelta(minutes=4)), "BTC", 101.0),  # last row <= event minute
        _ctx(_ms(T0 + pd.Timedelta(minutes=6)), "BTC", 102.0),  # AFTER the event -- must not be used
    ]
    out = extract_oracle_prices_for_events(stream, events)
    assert len(out) == 1
    assert out.iloc[0]["oracle_price"] == pytest.approx(101.0)


def test_extract_exact_minute_match():
    events = _funding_events([{"timestamp": T0, "symbol": "BTC"}])
    stream = [_ctx(_ms(T0), "BTC", 250.0)]
    out = extract_oracle_prices_for_events(stream, events)
    assert out.iloc[0]["oracle_price"] == pytest.approx(250.0)


def test_extract_within_2_minute_gap_tolerance_is_priced():
    events = _funding_events([{"timestamp": T0 + pd.Timedelta(minutes=2), "symbol": "BTC"}])
    stream = [_ctx(_ms(T0), "BTC", 100.0)]  # exactly 2 minutes before -- within tolerance (D§5.5)
    out = extract_oracle_prices_for_events(stream, events)
    assert out.iloc[0]["oracle_price"] == pytest.approx(100.0)
    assert (T0 + pd.Timedelta(minutes=2)) - T0 == GAP_TOLERANCE


def test_extract_beyond_2_minute_gap_is_left_unpriced_M27():
    """D§5.5 rule 2 / M27 — NEVER forward-filled across a gap exceeding 2
    minutes. Beyond that, the event MUST be left unpriced (NaN), never
    given a stale price.
    """
    events = _funding_events([{"timestamp": T0 + pd.Timedelta(minutes=3), "symbol": "BTC"}])
    stream = [_ctx(_ms(T0), "BTC", 100.0)]  # 3 minutes before -- BEYOND tolerance
    out = extract_oracle_prices_for_events(stream, events)
    assert pd.isna(out.iloc[0]["oracle_price"])


def test_extract_forward_filling_beyond_gap_would_price_it_M27():
    """Direct demonstration of the M27 mutation: a version that forward-
    fills WITHOUT the 2-minute cap would price this event using the stale
    100.0 row; the correct implementation refuses (previous test) and this
    test pins the VALUE that a forward-filling bug would produce, so a
    mutation removing the gap check is unambiguously caught.
    """
    events = _funding_events([{"timestamp": T0 + pd.Timedelta(minutes=10), "symbol": "BTC"}])
    stream = [_ctx(_ms(T0), "BTC", 100.0)]
    out = extract_oracle_prices_for_events(stream, events)
    assert pd.isna(out.iloc[0]["oracle_price"])  # NOT 100.0


def test_extract_no_prior_row_at_all_is_unpriced():
    events = _funding_events([{"timestamp": T0, "symbol": "ETH"}])
    stream = [_ctx(_ms(T0), "BTC", 100.0)]  # different symbol entirely
    out = extract_oracle_prices_for_events(stream, events)
    assert pd.isna(out.iloc[0]["oracle_price"])


def test_extract_multi_symbol_multi_event():
    events = _funding_events(
        [
            {"timestamp": T0, "symbol": "BTC"},
            {"timestamp": T0 + pd.Timedelta(hours=1), "symbol": "BTC"},
            {"timestamp": T0, "symbol": "ETH"},
        ]
    )
    stream = [
        _ctx(_ms(T0), "BTC", 100.0),
        _ctx(_ms(T0), "ETH", 3000.0),
        _ctx(_ms(T0 + pd.Timedelta(hours=1)), "BTC", 105.0),
    ]
    out = extract_oracle_prices_for_events(stream, events).set_index(["symbol", "timestamp"])
    assert out.loc[("BTC", T0), "oracle_price"] == pytest.approx(100.0)
    assert out.loc[("BTC", T0 + pd.Timedelta(hours=1)), "oracle_price"] == pytest.approx(105.0)
    assert out.loc[("ETH", T0), "oracle_price"] == pytest.approx(3000.0)


def test_extract_rejects_naive_row_timestamp():
    events = _funding_events([{"timestamp": T0, "symbol": "BTC"}])
    stream = [{"time": pd.Timestamp("2024-01-01"), "coin": "BTC", "oracle_px": 100.0}]  # naive
    with pytest.raises(Exception):
        list(extract_oracle_prices_for_events(stream, events).itertuples())


def test_extract_never_retains_full_raw_stream_M41():
    """D§5.5.1 rule 1 (M41, compact-oracle test) — the function's OUTPUT is
    the compact `{timestamp, symbol, oracle_price}` artifact only, sized to
    the number of FUNDING EVENTS, never to the number of raw stream rows
    (which may vastly outnumber events, exactly as the real `asset_ctxs`
    archive does: ~3M events vs. 9.63GB of per-minute rows, F18), AND the
    module retains NO reference to the full raw row set after the call
    returns (a version that stashed it "for later" would be exactly the
    M41 defect: raw retained after extraction).
    """
    import data.hyperliquid.oracle as oracle_module

    events = _funding_events([{"timestamp": T0, "symbol": "BTC"}])
    huge_stream = [_ctx(_ms(T0 - pd.Timedelta(minutes=1)) + i, "BTC", float(i)) for i in range(0, 200_000, 60_000)]
    n_raw_rows = len(huge_stream)
    out = extract_oracle_prices_for_events(iter(huge_stream), events)
    assert list(out.columns) == ["timestamp", "symbol", "oracle_price"]
    assert len(out) == len(events)  # NOT n_raw_rows
    # no module-level (or any other) retained handle to the full raw row set:
    retained = getattr(oracle_module, "_LAST_RAW_ROWS_RETAINED", None)
    assert retained is None or len(retained) == 0
