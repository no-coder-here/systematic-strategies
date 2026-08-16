"""D§16.3 — checked-in symbol mapping table."""

from __future__ import annotations

import pytest

from data.symbol_map import (
    MAPPED_COUNT,
    SYMBOL_MAPPINGS,
    UNMATCHED_COUNT,
    get_mapping,
    mapped_symbols,
    unmatched_symbols,
)

# D§1.2 F14 — pre-verified counts and unmatched-name list.
F14_UNMATCHED = frozenset(
    {
        "MATIC", "RNDR", "HPOS", "RLB", "UNIBOT", "OX", "FRIEND", "SHIA", "CANTO", "REQ",
        "NFTI", "PANDORA", "MNT", "BLAST", "kDOGS", "kNEIRO", "PURR", "JELLY", "LAUNCHCOIN",
        "YZY", "APEX", "CASHCAT",
    }
)


def test_reconciles_with_F14_counts():
    assert len(SYMBOL_MAPPINGS) == 232
    assert MAPPED_COUNT == 210
    assert UNMATCHED_COUNT == 22


def test_reconciles_with_F14_unmatched_name_list():
    assert set(unmatched_symbols()) == F14_UNMATCHED


def test_no_duplicate_hl_symbols():
    names = [m.hl_symbol for m in SYMBOL_MAPPINGS]
    assert len(names) == len(set(names))


def test_mapped_entries_have_binance_symbol_and_multiplier():
    for m in SYMBOL_MAPPINGS:
        if m.status == "mapped":
            assert m.binance_symbol is not None
            assert m.hl_unit_multiplier in (1, 1000)
            assert m.venue_unit_multiplier in (1, 1000)


def test_mapped_entries_have_verified_by_citation_D16_3_4():
    """D§16.3.4 (v1.3 DECISION 3) — every MAPPED entry MUST carry a
    non-empty `verified_by` citation. This is the "checked-in, human-reviewed
    constant carrying a documentation citation" requirement, not merely a
    numeric multiplier.
    """
    for m in SYMBOL_MAPPINGS:
        if m.status == "mapped":
            assert m.verified_by, f"{m.hl_symbol} mapped entry MUST carry verified_by (D§16.3.4)"
            assert len(m.verified_by) > 20, f"{m.hl_symbol} verified_by looks too short to be a real citation"


def test_unmatched_entries_have_no_multiplier_or_verified_by():
    for m in SYMBOL_MAPPINGS:
        if m.status == "unmatched":
            assert m.hl_unit_multiplier is None
            assert m.venue_unit_multiplier is None
            assert m.verified_by is None


def test_unmatched_entries_have_no_binance_symbol_and_a_reason():
    for m in SYMBOL_MAPPINGS:
        if m.status == "unmatched":
            assert m.binance_symbol is None
            assert m.reason


def test_k_prefix_unit_equivalence_M36():
    """D§16.3.4 — the k/1000 unit multiplier MUST be verified, never
    assumed. Every mapped `k`-prefixed Hyperliquid symbol maps to a Binance
    `1000NAME` contract with BOTH `hl_unit_multiplier == 1000` and
    `venue_unit_multiplier == 1000` (never 1) — a mapping whose multiplier
    silently dropped to 1 on either side would create a ~1000x price-level
    discrepancy (D§17.4).
    """
    k_mapped = [m for m in SYMBOL_MAPPINGS if m.hl_symbol.startswith("k") and m.status == "mapped"]
    assert len(k_mapped) > 0
    for m in k_mapped:
        assert m.hl_unit_multiplier == 1000, f"{m.hl_symbol} -> {m.binance_symbol} MUST have hl_unit_multiplier=1000"
        assert m.venue_unit_multiplier == 1000, (
            f"{m.hl_symbol} -> {m.binance_symbol} MUST have venue_unit_multiplier=1000"
        )
        assert m.binance_symbol.startswith("1000")
        assert m.normalization_ratio == pytest.approx(1.0)


def test_get_mapping_returns_none_for_unknown_symbol():
    assert get_mapping("SOME_SYMBOL_NOT_IN_TABLE") is None


def test_rename_chains_stay_unmapped_D6_4_M35():
    """MATIC and RNDR are the CONFIRMED rename chains (F8): Hyperliquid
    renamed them to POL/RENDER. Binance's own POLUSDT/RENDERUSDT exist (and
    are separately, independently mapped as POL/RENDER's OWN entries) but
    MATIC/RNDR themselves MUST stay unmatched -- auto-mapping
    MATIC->POLUSDT would be exactly the prohibited heuristic rename splice.
    """
    matic = get_mapping("MATIC")
    rndr = get_mapping("RNDR")
    assert matic.status == "unmatched" and matic.binance_symbol is None
    assert rndr.status == "unmatched" and rndr.binance_symbol is None
    # POL and RENDER are separately, independently, correctly mapped.
    pol = get_mapping("POL")
    render = get_mapping("RENDER")
    assert pol.status == "mapped" and pol.binance_symbol == "POLUSDT"
    assert render.status == "mapped" and render.binance_symbol == "RENDERUSDT"


def test_no_heuristic_lineage_detection_M45():
    """D§16.3.3/D§6.4 (M45) — migrations/renames MUST NOT be inferred from
    symbol name similarity or any heuristic. `RENDER` is textually similar to
    the delisted `RNDR` root, and `POL` to `MATIC`'s successor naming, but the
    only pairs ever treated specially are the explicitly reviewed F8 pairs
    consumed elsewhere (`universe.KNOWN_RENAME_CANDIDATES`) -- this table
    itself has NO fuzzy-matching machinery at all: every `status='mapped'`
    entry's `binance_symbol` is either the exact deterministic
    `NAME->NAMEUSDT`/`kNAME->1000NAMEUSDT` generator output or nothing.
    """
    for m in SYMBOL_MAPPINGS:
        if m.status != "mapped":
            continue
        expected = f"1000{m.hl_symbol[1:]}USDT" if m.hl_symbol.startswith("k") else f"{m.hl_symbol}USDT"
        assert m.binance_symbol == expected, (
            f"{m.hl_symbol} -> {m.binance_symbol} does not match the deterministic generator rule "
            f"(expected {expected!r}) -- any divergence would indicate a heuristic/fuzzy mapping crept in"
        )


def test_mapped_symbols_and_unmatched_symbols_partition_the_table():
    mapped = set(mapped_symbols())
    unmatched = set(unmatched_symbols())
    assert mapped.isdisjoint(unmatched)
    assert mapped | unmatched == {m.hl_symbol for m in SYMBOL_MAPPINGS}


def test_symbol_mapping_construction_validates_invariants():
    from data.symbol_map import SymbolMapping

    with pytest.raises(ValueError):
        SymbolMapping(
            hl_symbol="X", binance_symbol=None, hl_unit_multiplier=1, venue_unit_multiplier=1,
            verified_by="cite", status="mapped", reason=None,
        )
    with pytest.raises(ValueError):
        SymbolMapping(
            hl_symbol="X", binance_symbol="XUSDT", hl_unit_multiplier=1, venue_unit_multiplier=1,
            verified_by="cite", status="unmatched", reason=None,
        )


def test_mapped_entry_missing_multiplier_fails_the_mapping_M42():
    """D§16.3.4 (v1.3 DECISION 3, M42) — a `status='mapped'` entry with a
    missing `hl_unit_multiplier`/`venue_unit_multiplier` MUST FAIL
    construction outright (a `ValueError`), never be silently accepted or
    merely warned about.
    """
    from data.symbol_map import SymbolMapping

    with pytest.raises(ValueError, match="hl_unit_multiplier"):
        SymbolMapping(
            hl_symbol="kX", binance_symbol="1000XUSDT", hl_unit_multiplier=None, venue_unit_multiplier=1000,
            verified_by="cite", status="mapped", reason=None,
        )
    with pytest.raises(ValueError, match="hl_unit_multiplier"):
        SymbolMapping(
            hl_symbol="kX", binance_symbol="1000XUSDT", hl_unit_multiplier=1000, venue_unit_multiplier=None,
            verified_by="cite", status="mapped", reason=None,
        )


def test_mapped_entry_missing_verified_by_fails_the_mapping_M42():
    """D§16.3.4 (v1.3 DECISION 3, M42) — an UNVERIFIED multiplier (present
    numerically but with no `verified_by` citation) MUST ALSO fail the
    mapping. A multiplier without evidence of where it came from is exactly
    what D§16.3.4 forbids treating as "explicitly verified".
    """
    from data.symbol_map import SymbolMapping

    with pytest.raises(ValueError, match="verified_by"):
        SymbolMapping(
            hl_symbol="kX", binance_symbol="1000XUSDT", hl_unit_multiplier=1000, venue_unit_multiplier=1000,
            verified_by=None, status="mapped", reason=None,
        )
    with pytest.raises(ValueError, match="verified_by"):
        SymbolMapping(
            hl_symbol="kX", binance_symbol="1000XUSDT", hl_unit_multiplier=1000, venue_unit_multiplier=1000,
            verified_by="", status="mapped", reason=None,
        )


def test_unmatched_entry_with_a_multiplier_is_rejected():
    """An `unmatched` entry MUST NOT carry a multiplier or citation at all --
    there is no Binance contract for it to be scaled against."""
    from data.symbol_map import SymbolMapping

    with pytest.raises(ValueError):
        SymbolMapping(
            hl_symbol="X", binance_symbol=None, hl_unit_multiplier=1, venue_unit_multiplier=1,
            verified_by=None, status="unmatched", reason="some reason",
        )


def test_normalization_ratio_none_for_unmatched():
    m = get_mapping("MATIC")
    assert m.normalization_ratio is None


# ---------------------------------------------------------------------------
# Audit finding D2 — the REALISTIC multiplier bug (100 recorded where the truth
# is 1000) produced a price ratio of exactly 0.1, which sat ON the old inclusive
# bound (0.1, 10.0) and did NOT raise. Only the egregious ~1000x case was tested,
# so the boundary itself — precisely where a power-of-ten error lands — was
# unguarded. These tests pin the BOUNDARY, not just the obvious case.
# ---------------------------------------------------------------------------

import pytest as _pytest

from data.binance.provider import (
    UNIT_EQUIVALENCE_RATIO_BOUNDS,
    UnitEquivalenceError,
    assert_unit_equivalence,
)


def test_D2_ten_x_multiplier_error_fails_not_passes():
    """hl recorded as 100x when truth is 1000x => ratio 0.1 => MUST raise."""
    with _pytest.raises(UnitEquivalenceError):
        assert_unit_equivalence(hl_price=0.1, binance_price=1.0, hl_symbol="kPEPE", binance_symbol="1000PEPEUSDT")
    with _pytest.raises(UnitEquivalenceError):
        assert_unit_equivalence(hl_price=10.0, binance_price=1.0, hl_symbol="kPEPE", binance_symbol="1000PEPEUSDT")


def test_D2_exact_two_x_error_on_boundary_fails():
    """An exactly-2x error lands on the bound; strict comparison must reject it."""
    lo, hi = UNIT_EQUIVALENCE_RATIO_BOUNDS
    with _pytest.raises(UnitEquivalenceError):
        assert_unit_equivalence(hl_price=lo, binance_price=1.0, hl_symbol="kX", binance_symbol="1000XUSDT")
    with _pytest.raises(UnitEquivalenceError):
        assert_unit_equivalence(hl_price=hi, binance_price=1.0, hl_symbol="kX", binance_symbol="1000XUSDT")


def test_D2_correctly_mapped_pair_still_passes():
    """Real measured divergence is ~0.04%; a correct pair must NOT be flagged."""
    assert_unit_equivalence(hl_price=1.0004, binance_price=1.0, hl_symbol="kPEPE", binance_symbol="1000PEPEUSDT")
    # self-guard: the bounds must remain loose enough that real divergence passes
    lo, hi = UNIT_EQUIVALENCE_RATIO_BOUNDS
    assert lo < 0.99 and hi > 1.01, "bounds tightened past real cross-venue divergence — would false-positive"
