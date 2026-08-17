"""R§3 — canonical serialization. ONE encoder feeds both hashing and storage.

Everything that gets hashed (R§5) or written to disk (R§10) flows through
`encode()` first. Two textual forms are produced from the identical encoded
value tree:

- `canonical_json()` — `sort_keys=True`, no whitespace, `allow_nan=False`.
  Used for hashing and equality comparison.
- `stored_json()` — same tree, `indent=2`, trailing newline. Used for the
  human-readable files under `experiments/registry/records/`.

R§3.1.1 (blocking): there is deliberately NO `str(obj)` catch-all. A silent
`str()` fallback would let an unsupported parameter type produce a hash that
is either process-dependent (a `repr()` containing a memory address) or
falsely stable (a lossy `repr()` colliding two different objects) — both
invisible in a green test suite. Anything not in the table below raises
`SerializationError`.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "SerializationError",
    "encode",
    "decode",
    "canonical_json",
    "stored_json",
    "sha256_hexdigest",
    "strict_json_loads",
    "RESERVED_WRAPPER_KEYS",
]


class SerializationError(Exception):
    """R§3 — an input could not be encoded under the canonical scheme."""


# R§3.1.2 — reserved wrapper keys. A plain dict containing one of these as a
# key would be ambiguous with an encoder-generated wrapper on decode, so
# encoding such a dict is refused outright rather than silently corrupted.
RESERVED_WRAPPER_KEYS = frozenset({"$nonfinite", "$ts", "$date", "$td_ns"})


def _encode_float(x: float) -> Any:
    if math.isnan(x):
        return {"$nonfinite": "nan"}
    if math.isinf(x):
        return {"$nonfinite": "inf" if x > 0 else "-inf"}
    return x


def encode(value: Any) -> Any:
    """R§3.1 — normative type-encoding table. Produces a plain JSON-able tree
    (only `None`/`bool`/`int`/`str`/`float`/`dict`/`list`)."""

    # `bool` MUST be checked before `int` — `isinstance(True, int)` is `True`
    # in Python, so testing `int` first would silently encode booleans as 0/1
    # and make `True != 1` invisible in the hash (R§3.1 table note).
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _encode_float(value)

    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return _encode_float(float(value))

    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            raise SerializationError(
                f"tz-naive pd.Timestamp {value!r} is not permitted (R§3.2) — "
                "the platform's data layer is UTC throughout; a naive "
                "timestamp is ambiguous and could silently misstate a data "
                "window by hours."
            )
        return {"$ts": value.isoformat()}
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return {"$date": value.isoformat()}
    if isinstance(value, _dt.datetime):
        # A bare stdlib datetime (not a pd.Timestamp) is not one of R§3.1's
        # supported input types — only `pd.Timestamp` is listed. Refusing it
        # explicitly is safer than silently accepting a tz-aware stdlib
        # datetime that would `isoformat()` differently from pd.Timestamp on
        # decode (`pd.Timestamp` round-trip is what R§3.2 pins).
        raise SerializationError(
            f"bare datetime.datetime {value!r} is not a supported type (R§3.1) — use pd.Timestamp"
        )
    if isinstance(value, pd.Timedelta):
        return {"$td_ns": int(value.value)}

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise SerializationError(f"dict key {k!r} is not a str (R§3.1)")
            if k in RESERVED_WRAPPER_KEYS:
                raise SerializationError(
                    f"dict key {k!r} collides with a reserved wrapper key {sorted(RESERVED_WRAPPER_KEYS)} "
                    "(R§3.1.2) — this would decode ambiguously"
                )
            out[k] = encode(v)
        return out

    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]

    if isinstance(value, (set, frozenset)):
        # R§3.1 / R§16.2 — sorted by the CANONICAL form of each encoded
        # element, never by set-iteration order (hash-seed dependent).
        encoded = [encode(v) for v in value]
        return sorted(encoded, key=lambda t: canonical_json(t))

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return encode(to_dict())
        return encode(dataclasses.asdict(value))

    raise SerializationError(
        f"unsupported type {type(value)!r} for canonical serialization (R§3.1) — "
        "no str() fallback is permitted"
    )


class _RaiseOnConstant:
    """R§3.1.3 — a strict JSON parser that rejects the `NaN`/`Infinity`/
    `-Infinity` extension tokens `json.loads` accepts by default."""

    def __call__(self, token: str) -> float:
        raise ValueError(f"non-standard JSON constant {token!r} encountered (R§3.1.3)")


def strict_json_loads(s: str) -> Any:
    return json.loads(s, parse_constant=_RaiseOnConstant())


def canonical_json(value: Any) -> str:
    tree = encode(value)
    # `allow_nan=False` is a backstop (R§3): encode() already replaces every
    # non-finite float with a `$nonfinite` wrapper, so this should never
    # actually fire — but if some non-finite float ever slipped through, a
    # loud `ValueError` here is strictly better than emitting the
    # non-standard `NaN`/`Infinity` tokens other JSON parsers reject.
    return json.dumps(tree, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def stored_json(value: Any) -> str:
    tree = encode(value)
    return json.dumps(tree, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def sha256_hexdigest(value: Any, *, hash_fn=hashlib.sha256) -> str:
    return hash_fn(canonical_json(value).encode("utf-8")).hexdigest()


def _decode_tree(tree: Any) -> Any:
    if isinstance(tree, dict):
        if len(tree) == 1:
            (key, val), = tree.items()
            if key == "$nonfinite":
                return {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}[val]
            if key == "$ts":
                return pd.Timestamp(val)
            if key == "$date":
                return _dt.date.fromisoformat(val)
            if key == "$td_ns":
                return pd.Timedelta(val, unit="ns")
        return {k: _decode_tree(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [_decode_tree(v) for v in tree]
    return tree


def decode(tree: Any) -> Any:
    """Reverses `encode()`. Containers decode back to `dict`/`list` — R§3.1
    defines no `$set` wrapper (JSON has no set type), so `set`/`frozenset`
    inputs are one-way encodable for hashing/storage purposes only; R§3.3's
    round-trip requirement is evaluated here for every type that has an
    inverse wrapper in the table (scalars, timestamps, dates, timedeltas,
    dicts, lists/tuples-as-lists)."""
    return _decode_tree(tree)
