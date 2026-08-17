"""R§7.3/R§20.8.3/R§20.8.4 -- `datahash.py` content hashing (`col-buffer-v2`).

Audit finding this file exists to close: three independent mutations of
`datahash.py` survived the ENTIRE 143-test v1.1 suite because this module
had ZERO dedicated tests. Every discriminating property named by R§20.8.3 is
covered here: column name in the digest, sorted column order, `(timestamp,
symbol)` row sort, and a pinned golden digest for a small fixed frame.
"""
from __future__ import annotations

import pandas as pd

from registry.datahash import CONTENT_HASH_METHOD, hash_dataframe_content


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"]
            ).tz_convert("UTC"),
            "symbol": ["BTC", "BTC"],
            "open": [100.0, 101.0],
            "close": [101.0, 102.0],
            "trade_count": [10, 20],
            "native_traded": [True, False],
        }
    )


def test_method_id_is_col_buffer_v2():
    assert CONTENT_HASH_METHOD == "col-buffer-v2"


GOLDEN_DIGEST = "f674d7d686594cc996096dc7f487e18beed92f3e29037d4d39782c48dff7f9bb"


def test_golden_digest_pinned_for_a_small_fixed_frame():
    """R§20.8.3 -- a pinned golden digest, so any accidental encoding change
    (byte order, column order, row order, type dispatch) is caught even if
    a same-file differential test would not notice a globally-consistent
    change. GOLDEN_DIGEST was computed once from `col-buffer-v2` against the
    exact `_base_df()` fixture below and is pinned here deliberately -- a
    future encoding change MUST bump `CONTENT_HASH_METHOD` (R§19 D11) rather
    than silently changing what this constant means."""
    digest = hash_dataframe_content(_base_df())
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    assert digest == GOLDEN_DIGEST
    assert digest == hash_dataframe_content(_base_df())  # determinism


def test_missing_timestamp_or_symbol_column_raises():
    df = _base_df().drop(columns=["symbol"])
    try:
        hash_dataframe_content(df)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_column_name_is_part_of_the_digest():
    """M-datahash-1: renaming a column (same values) MUST change the digest
    -- the column NAME's UTF-8 bytes are hashed, not just its values."""
    df_a = _base_df()
    df_b = _base_df().rename(columns={"open": "open_renamed"})
    assert hash_dataframe_content(df_a) != hash_dataframe_content(df_b)


def test_column_order_is_sorted_before_hashing():
    """M-datahash-2: two DataFrames with the SAME columns in a DIFFERENT
    insertion order MUST hash identically -- columns are iterated in
    SORTED name order, never construction/insertion order."""
    df_a = _base_df()
    df_b = df_a[["native_traded", "trade_count", "close", "open", "symbol", "timestamp"]]
    assert hash_dataframe_content(df_a) == hash_dataframe_content(df_b)


def test_rows_are_sorted_by_timestamp_then_symbol_before_hashing():
    """M-datahash-3: two DataFrames containing the SAME rows in a DIFFERENT
    order MUST hash identically -- rows are sorted by (timestamp, symbol)
    before hashing, never the frame's incoming order."""
    df_a = _base_df()
    df_b = df_a.iloc[::-1].reset_index(drop=True)
    assert hash_dataframe_content(df_a) == hash_dataframe_content(df_b)
    # Self-guard: prove the two frames really did start in different row
    # order (otherwise this test would pass vacuously).
    assert not df_a.reset_index(drop=True).equals(df_b)


def test_multi_symbol_rows_sorted_by_symbol_within_a_timestamp():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01T00:00:00Z"] * 2).tz_convert("UTC"),
            "symbol": ["ETH", "BTC"],
            "open": [1.0, 2.0],
        }
    )
    df_sorted_input = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01T00:00:00Z"] * 2).tz_convert("UTC"),
            "symbol": ["BTC", "ETH"],
            "open": [2.0, 1.0],
        }
    )
    assert hash_dataframe_content(df) == hash_dataframe_content(df_sorted_input)


def test_datetime_column_value_change_changes_digest():
    df_a = _base_df()
    df_b = _base_df()
    df_b.loc[0, "timestamp"] = pd.Timestamp("2026-01-01T00:00:01Z")
    assert hash_dataframe_content(df_a) != hash_dataframe_content(df_b)


def test_float_column_value_change_changes_digest():
    df_a = _base_df()
    df_b = _base_df()
    df_b.loc[0, "open"] = 999.0
    assert hash_dataframe_content(df_a) != hash_dataframe_content(df_b)


def test_integer_column_value_change_changes_digest():
    """R§20.8.4 -- the `trade_count` int64 column (not named in R§7.3's
    literal three-case table; pinned by R§20.8.4's col-buffer-v2 vocabulary)
    MUST discriminate on value."""
    df_a = _base_df()
    df_b = _base_df()
    df_b.loc[0, "trade_count"] = 999
    assert hash_dataframe_content(df_a) != hash_dataframe_content(df_b)


def test_bool_column_value_change_changes_digest():
    """R§20.8.4 -- the `native_traded` bool column."""
    df_a = _base_df()
    df_b = _base_df()
    df_b.loc[0, "native_traded"] = not df_b.loc[0, "native_traded"]
    assert hash_dataframe_content(df_a) != hash_dataframe_content(df_b)


def test_string_column_value_change_changes_digest():
    df_a = _base_df()
    df_b = _base_df()
    df_b.loc[0, "symbol"] = "ETH"
    assert hash_dataframe_content(df_a) != hash_dataframe_content(df_b)


def test_string_length_prefix_prevents_concatenation_collision():
    """R§20.8.4 -- the length prefix on string encoding means two frames
    whose string columns concatenate to the same bytes (but split
    differently) MUST NOT collide."""
    df_a = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01T00:00:00Z"]).tz_convert("UTC"),
            "symbol": ["AB"],
            "note": ["CD"],
        }
    )
    df_b = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01T00:00:00Z"]).tz_convert("UTC"),
            "symbol": ["ABC"],
            "note": ["D"],
        }
    )
    assert hash_dataframe_content(df_a) != hash_dataframe_content(df_b)
