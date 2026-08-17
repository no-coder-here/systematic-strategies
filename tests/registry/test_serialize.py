"""R§18.1(6) — canonical serialization (R§3)."""
from __future__ import annotations

import datetime as dt
import json
import math
import struct

import numpy as np
import pandas as pd
import pytest

from registry.serialize import (
    SerializationError,
    canonical_json,
    decode,
    encode,
    stored_json,
    strict_json_loads,
)


def _bits(f: float) -> bytes:
    return struct.pack("<d", f)


class TestRoundTrip:
    @pytest.mark.parametrize(
        "value",
        [None, True, False, 0, -7, 12345678901234, "hello", "", "unicode-é中"],
    )
    def test_scalars(self, value):
        assert decode(encode(value)) == value

    @pytest.mark.parametrize("value", [0.0, -0.0, 1.5, -1.5, 1e300, 1e-300, 0.1])
    def test_finite_floats_bitwise(self, value):
        out = decode(encode(value))
        assert _bits(out) == _bits(value)

    def test_nan(self):
        out = decode(encode(float("nan")))
        assert isinstance(out, float) and math.isnan(out)

    @pytest.mark.parametrize("value", [float("inf"), float("-inf")])
    def test_infinities(self, value):
        assert decode(encode(value)) == value

    def test_tz_aware_timestamp_ns_precision(self):
        ts = pd.Timestamp("2026-01-01 00:00:00.123456789", tz="UTC")
        out = decode(encode(ts))
        assert out == ts
        assert out.tz == ts.tz
        assert out.value == ts.value

    def test_tz_naive_timestamp_raises(self):
        with pytest.raises(SerializationError):
            encode(pd.Timestamp("2026-01-01 00:00:00"))

    def test_date(self):
        d = dt.date(2026, 8, 17)
        assert decode(encode(d)) == d

    def test_timedelta_ns(self):
        td = pd.Timedelta(hours=1, nanoseconds=7)
        out = decode(encode(td))
        assert out == td
        assert out.value == td.value

    def test_nested_containers(self):
        value = {"a": [1, 2.5, {"b": (3, "x")}], "c": None}
        out = decode(encode(value))
        assert out == {"a": [1, 2.5, {"b": [3, "x"]}], "c": None}

    def test_numpy_scalars(self):
        assert decode(encode(np.int64(7))) == 7
        assert isinstance(decode(encode(np.int64(7))), int)
        assert decode(encode(np.float64(1.5))) == 1.5
        assert decode(encode(np.bool_(True))) is True

    def test_set_encodes_sorted_canonical_list(self):
        out = encode({3, 1, 2})
        assert out == [1, 2, 3]

    def test_tuple_and_list_both_encode_to_list_order_preserved(self):
        assert encode((1, 2, 3)) == [1, 2, 3]
        assert encode([3, 2, 1]) == [3, 2, 1]

    def test_unsupported_type_raises_no_str_fallback(self):
        class Unsupported:
            def __repr__(self):
                return "<Unsupported at some address>"

        with pytest.raises(SerializationError):
            encode(Unsupported())

    def test_bool_before_int_isinstance_ordering(self):
        # M16-adjacent regression: `isinstance(True, int)` is True in Python;
        # bool MUST be encoded as bool, not silently coerced to 0/1, so a
        # dict differing only True vs 1 hashes differently.
        assert canonical_json({"x": True}) != canonical_json({"x": 1})

    def test_reserved_key_collision_raises(self):
        with pytest.raises(SerializationError):
            encode({"$ts": "hello"})
        with pytest.raises(SerializationError):
            encode({"$nonfinite": "x"})

    def test_non_str_dict_key_raises(self):
        with pytest.raises(SerializationError):
            encode({1: "x"})


class TestCanonicalFormProperties:
    def test_no_nan_or_infinity_tokens_in_canonical_string(self):
        s = canonical_json({"a": float("nan"), "b": float("inf"), "c": float("-inf")})
        assert "NaN" not in s
        assert "Infinity" not in s
        assert "-Infinity" not in s

    def test_canonical_string_parses_under_strict_parser(self):
        s = canonical_json({"a": float("nan"), "b": float("inf")})
        # MUST parse cleanly under a strict parser that rejects the NaN/
        # Infinity extension tokens — proving the payload contains the
        # $nonfinite wrapper, not a raw extension token.
        parsed = strict_json_loads(s)
        assert parsed == {"a": {"$nonfinite": "nan"}, "b": {"$nonfinite": "inf"}}

    def test_a_raw_nan_token_is_rejected_by_the_strict_parser(self):
        # Proves the strict parser used above is actually strict (i.e. the
        # test fixture that "no NaN token" checks against is self-guarding):
        # ordinary json.loads happily accepts `NaN`; ours must not.
        with pytest.raises(ValueError):
            strict_json_loads("NaN")
        # Self-guarding: prove ordinary `json.loads` DOES accept the token
        # (i.e. this fixture would not discriminate anything if it didn't).
        assert math.isnan(json.loads("NaN"))

    def test_key_insertion_order_invariance_R16_4(self):
        a = canonical_json({"a": 1, "b": 2})
        b = canonical_json({"b": 2, "a": 1})
        assert a == b

    def test_stored_json_has_trailing_newline_and_indent(self):
        s = stored_json({"a": 1})
        assert s.endswith("\n")
        assert "\n" in s.rstrip("\n")  # indented (multi-line)

    def test_canonical_json_is_compact_no_whitespace(self):
        s = canonical_json({"a": 1, "b": [1, 2]})
        assert s == '{"a":1,"b":[1,2]}'
