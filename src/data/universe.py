"""D§6 — point-in-time universe construction.

D§2.1: MUST NOT import `src/data/hyperliquid/**`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

import pandas as pd

from .base import SymbolMeta, UniverseSnapshot

__all__ = [
    "listed_at",
    "delisted_at",
    "is_member",
    "filter_universe_asof",
    "KNOWN_RENAME_CANDIDATES",
    "detect_rename_candidates",
]

# D§6.2
def listed_at(meta: SymbolMeta) -> Optional[pd.Timestamp]:
    return meta.first_native_bar


def delisted_at(meta: SymbolMeta) -> Optional[pd.Timestamp]:
    return meta.last_native_bar if meta.is_delisted else None


def is_member(meta: SymbolMeta, t: pd.Timestamp) -> bool:
    """D§6.2 — `member(symbol, t) := listed_at <= t <= (delisted_at or +inf)`."""
    la = listed_at(meta)
    if la is None or t < la:
        return False
    da = delisted_at(meta)
    if da is not None and t > da:
        return False
    return True


def filter_universe_asof(universe: UniverseSnapshot, as_of: pd.Timestamp) -> UniverseSnapshot:
    """D§6.2 — point-in-time membership filter.

    Interpretation note (flagged ambiguity, see implementation report):
    `get_universe(as_of=...)` restricts the returned snapshot to symbols that
    were members at `as_of` per D§6.2's inferred listed_at/delisted_at. This
    does NOT restrict to currently-live names (that would be the survivorship
    bias D§6.1 explicitly prohibits) — a symbol delisted before `as_of` is
    excluded because it was NOT a member then, not because it is delisted now.
    """
    members = {name: meta for name, meta in universe.symbols.items() if is_member(meta, as_of)}
    return replace(universe, symbols=members)


# D§6.4 / F8 — known, CONFIRMED rename candidates only (advisory, never spliced).
# A general name-similarity heuristic is deliberately NOT implemented: it would
# itself be a fragile, unverifiable auto-association mechanism, exactly what
# D§6.4 forbids acting on. This list may be extended only with equally
# confirmed pairs; it is advisory metadata, never used to merge/splice data.
KNOWN_RENAME_CANDIDATES = (
    ("MATIC", "POL"),
    ("RNDR", "RENDER"),
    ("FTM", "S"),
)


def detect_rename_candidates(universe: UniverseSnapshot) -> list:
    """D§6.4 — returns `(delisted_name, relisted_name)` pairs present in this
    universe snapshot, drawn ONLY from `KNOWN_RENAME_CANDIDATES` (F8). This is
    advisory only: the layer MUST NOT act on these (never splice).
    """
    out = []
    for old, new in KNOWN_RENAME_CANDIDATES:
        old_meta = universe.symbols.get(old)
        new_meta = universe.symbols.get(new)
        if old_meta is not None and new_meta is not None and old_meta.is_delisted:
            out.append((old, new))
    return out
