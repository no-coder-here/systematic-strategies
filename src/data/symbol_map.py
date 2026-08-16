"""D§16.3 -- the checked-in Hyperliquid<->Binance USDⓈ-M perpetual symbol
mapping table.

THIS IS DATA, NOT A RUNTIME HEURISTIC (D§16.3.1). It was GENERATED ONCE
(2026-08-16) from:

    - a live Hyperliquid `meta` snapshot (232 universe entries), and
    - a live Binance `GET /fapi/v1/exchangeInfo` snapshot (654
      `contractType=="PERPETUAL"`, `quoteAsset=="USDT"` symbols),

by applying the deterministic generator rule from D§1.2 F14 (`NAME ->
NAMEUSDT`, `kNAME -> 1000NAMEUSDT`), and then REVIEWED against F14's
pre-verified 22-name unmatched list and the D§6.4/D§16.3.3 rename-exclusion
rule before being frozen into this file. The generator is not run again at
import time or at any runtime call -- this table is the reviewed OUTPUT of
that one-time process, checked in like any other reviewed constant.

Result (reconciles EXACTLY with D§1.2 F14): 210 mapped, 22 unmatched, 232
total -- see the implementation report for the full re-derivation transcript.

Rename chains stay UNMAPPED (D§6.4/D§16.3.3): `MATIC` and `RNDR` are
deliberately left `unmatched` even though Binance has `POLUSDT` /
`RENDERUSDT` respectively (those are the RENAMED contracts, not `MATICUSDT`/
`RNDRUSDT`, which do not exist on Binance at all) -- `POL` and `RENDER` are
separately, independently mapped to their OWN same-named Binance contracts
as distinct Hyperliquid symbols. No splicing occurs anywhere in this table.

D§2.1: this module MUST NOT import `src/data/hyperliquid/**` or
`src/data/binance/**` -- it is pure data, referenced BY both.

--- v1.3 DECISION 3 (unit-scaled contracts, D§16.3.4/D§16.3.5) ---

Every entry now carries FOUR fields describing unit scale instead of one:

    hl_unit_multiplier      -- underlying tokens per 1 Hyperliquid contract
    venue_unit_multiplier   -- underlying tokens per 1 Binance contract
    verified_by             -- a checked-in, human-reviewed documentation
                                citation explaining WHERE each multiplier
                                came from (never inferred from price data)

Per D§16.3.4, "explicitly verified" means a checked-in, human-reviewed
constant carrying a documentation citation, NOT a value parsed out of the
symbol string at runtime and NOT a value inferred from an observed price
ratio (that would make the D§17 order-of-magnitude check circular, since the
check exists specifically to detect a WRONG multiplier). Neither venue
exposes a machine-readable multiplier field (verified 2026-08-16: Binance
`1000PEPEUSDT` carries only `baseAsset: "1000PEPE"` -- a name convention --
and Hyperliquid's `kPEPE` entry is `{szDecimals: 0, maxLeverage: 10,
marginTableId: 52}` with no unit field at all), so the citations below point
to each venue's OWN documented NAMING CONVENTION (F7's k-prefix list; F14's
Binance `1000<NAME>` contract-naming convention), independently, per side --
never to the other side's price.

A MISSING or UNVERIFIED multiplier on a `status="mapped"` entry FAILS the
mapping outright at construction time (`SymbolMapping.__post_init__` raises)
-- this is enforced in code, not merely documented, so a future edit that
adds a mapped entry without both multipliers and a citation cannot pass
import, let alone review (D§16.3.4 M42: "missing/unverified FAILS the
mapping, not a warning").

Normalization for cross-venue comparison (D§17.2) divides each venue's raw
price by ITS OWN multiplier to obtain a common per-single-underlying-token
price: `hl_price_per_token = hl_raw_price / hl_unit_multiplier`,
`venue_price_per_token = venue_raw_price / venue_unit_multiplier`. For every
current `k`-prefixed mapping (`kPEPE`<->`1000PEPEUSDT` etc.) BOTH multipliers
are 1000 (both venues already quote per-1000-tokens), so normalization is a
structural no-op on the RATIO between the two series -- exactly why this
pairing is legitimate (D§16.3.5). A mapping that instead paired a k-prefixed
HL symbol with a Binance contract lacking the `1000` naming convention would
have `venue_unit_multiplier=1` while `hl_unit_multiplier=1000`, and
normalizing would correctly surface the resulting price levels as NOT
comparable per-token prices (an intentional, not observed-ratio-based,
distinction -- see `validation.compare_cross_venue`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = [
    "SymbolMapping",
    "SYMBOL_MAPPINGS",
    "MAPPED_COUNT",
    "UNMATCHED_COUNT",
    "get_mapping",
    "mapped_symbols",
    "unmatched_symbols",
]


@dataclass(frozen=True)
class SymbolMapping:
    hl_symbol: str
    binance_symbol: Optional[str]  # None iff status == "unmatched"
    hl_unit_multiplier: Optional[int]  # tokens per 1 HL contract; None iff unmatched (D§16.3.4)
    venue_unit_multiplier: Optional[int]  # tokens per 1 Binance contract; None iff unmatched (D§16.3.4)
    verified_by: Optional[str]  # human-reviewed documentation citation; None iff unmatched (D§16.3.4)
    status: str  # "mapped" | "unmatched"
    reason: Optional[str]  # populated iff status == "unmatched"

    def __post_init__(self) -> None:
        if self.status not in ("mapped", "unmatched"):
            raise ValueError(f"SymbolMapping.status must be 'mapped' or 'unmatched', got {self.status!r}")

        if self.status == "mapped":
            if self.binance_symbol is None:
                raise ValueError(f"mapped SymbolMapping for {self.hl_symbol!r} MUST have binance_symbol")
            # D§16.3.4 (v1.3 DECISION 3, M42) -- a missing OR unverified
            # multiplier FAILS the mapping. Not downgraded to a warning.
            if self.hl_unit_multiplier is None or self.venue_unit_multiplier is None:
                raise ValueError(
                    f"mapped SymbolMapping for {self.hl_symbol!r} MUST have an explicit hl_unit_multiplier "
                    "and venue_unit_multiplier (D§16.3.4) -- a missing multiplier FAILS the mapping"
                )
            if self.hl_unit_multiplier < 1 or self.venue_unit_multiplier < 1:
                raise ValueError(
                    f"mapped SymbolMapping for {self.hl_symbol!r} has non-positive multiplier(s) "
                    f"hl={self.hl_unit_multiplier} venue={self.venue_unit_multiplier}"
                )
            if not self.verified_by:
                raise ValueError(
                    f"mapped SymbolMapping for {self.hl_symbol!r} MUST carry verified_by evidence -- a "
                    "checked-in, human-reviewed documentation citation (D§16.3.4). An UNVERIFIED multiplier "
                    "FAILS the mapping; it is not merely downgraded to a warning (M42)."
                )
        else:  # unmatched
            if self.binance_symbol is not None:
                raise ValueError(f"unmatched SymbolMapping for {self.hl_symbol!r} MUST have binance_symbol=None")
            if self.hl_unit_multiplier is not None or self.venue_unit_multiplier is not None:
                raise ValueError(
                    f"unmatched SymbolMapping for {self.hl_symbol!r} MUST NOT carry a unit multiplier "
                    "-- there is no Binance contract to be scaled against"
                )
            if self.verified_by is not None:
                raise ValueError(f"unmatched SymbolMapping for {self.hl_symbol!r} MUST NOT carry verified_by")
            if not self.reason:
                raise ValueError(f"unmatched SymbolMapping for {self.hl_symbol!r} MUST have a reason")

    @property
    def normalization_ratio(self):
        """D§17.2 -- the factor `hl_unit_multiplier / venue_unit_multiplier`.
        Applying it is EQUIVALENT to (and this codebase always implements it
        as) dividing each venue's own raw price by its OWN multiplier before
        comparing -- see `validation.compare_cross_venue`. `None` iff
        unmatched (there is nothing to normalize against).
        """
        if self.hl_unit_multiplier is None or self.venue_unit_multiplier is None:
            return None
        return self.hl_unit_multiplier / self.venue_unit_multiplier


# D§16.3.4 -- shared citation constants. The verification methodology is
# IDENTICAL for every entry of a given kind (standard 1:1 contract vs.
# k-prefixed 1000:1000 contract), so the citation text is a shared, reviewed
# constant rather than re-typed per entry -- still a checked-in, reviewed
# constant per D§16.3.4, not a runtime computation.
_STANDARD_1_TO_1_CITATION = (
    "D§1 F14 (Binance GET /fapi/v1/exchangeInfo, contractType=PERPETUAL quoteAsset=USDT, "
    "retrieved 2026-08-16) + D§1 F7 (Hyperliquid meta.universe, retrieved 2026-08-16): "
    "hl_symbol carries no 'k'-prefix (not one of the 7 k-prefixed names in F7) => "
    "hl_unit_multiplier=1 (1 HL contract = 1 underlying token); binance_symbol's baseAsset "
    "carries no '1000'-prefix naming convention => venue_unit_multiplier=1 (1 Binance contract "
    "= 1 underlying token). Checked-in, human-reviewed constant (D§16.3.4); not parsed from the "
    "symbol string at runtime, not inferred from any observed price ratio (M43)."
)

_K_PREFIX_CITATION = (
    "D§1 F7 (Hyperliquid meta.universe, retrieved 2026-08-16): hl_symbol is one of the 7 "
    "documented k-prefixed names, each quoting 1000 tokens per contract per Hyperliquid's own "
    "perpetuals documentation (hyperliquid.gitbook.io/hyperliquid-docs, perpetuals section, "
    "k-prefix convention) => hl_unit_multiplier=1000. D§1 F14 (Binance GET /fapi/v1/exchangeInfo, "
    "retrieved 2026-08-16): binance_symbol's baseAsset carries Binance's own documented "
    "'1000<NAME>' contract-naming convention (binance-docs.github.io/apidocs/futures -- "
    "1000-prefixed bases denote a 1000-unit-per-contract instrument) => venue_unit_multiplier=1000. "
    "Both multipliers independently verified from EACH VENUE'S OWN naming-convention "
    "documentation, NOT from the other venue's price or from any observed price ratio "
    "(D§16.3.4 M43); checked-in, human-reviewed constant, reviewed 2026-08-16."
)

SYMBOL_MAPPINGS: tuple = (
    SymbolMapping(hl_symbol='BTC', binance_symbol='BTCUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ETH', binance_symbol='ETHUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ATOM', binance_symbol='ATOMUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MATIC', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason='renamed on Hyperliquid to POL; Binance perp is POLUSDT under the new name (POLUSDT exists on Binance, MATICUSDT does not) - rename mapping prohibited (D6.4/D16.3.3)'),
    SymbolMapping(hl_symbol='DYDX', binance_symbol='DYDXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SOL', binance_symbol='SOLUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AVAX', binance_symbol='AVAXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BNB', binance_symbol='BNBUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='APE', binance_symbol='APEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='OP', binance_symbol='OPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LTC', binance_symbol='LTCUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ARB', binance_symbol='ARBUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='DOGE', binance_symbol='DOGEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='INJ', binance_symbol='INJUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SUI', binance_symbol='SUIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='kPEPE', binance_symbol='1000PEPEUSDT', hl_unit_multiplier=1000, venue_unit_multiplier=1000, verified_by=_K_PREFIX_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CRV', binance_symbol='CRVUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LDO', binance_symbol='LDOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LINK', binance_symbol='LINKUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='STX', binance_symbol='STXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='RNDR', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason='renamed on Hyperliquid to RENDER; Binance perp is RENDERUSDT under the new name (RENDERUSDT exists on Binance, RNDRUSDT does not) - rename mapping prohibited (D6.4/D16.3.3)'),
    SymbolMapping(hl_symbol='CFX', binance_symbol='CFXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='FTM', binance_symbol='FTMUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='GMX', binance_symbol='GMXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SNX', binance_symbol='SNXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='XRP', binance_symbol='XRPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BCH', binance_symbol='BCHUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='APT', binance_symbol='APTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AAVE', binance_symbol='AAVEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='COMP', binance_symbol='COMPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MKR', binance_symbol='MKRUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='WLD', binance_symbol='WLDUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='FXS', binance_symbol='FXSUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='HPOS', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'HPOSUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='RLB', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'RLBUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='UNIBOT', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'UNIBOTUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='YGG', binance_symbol='YGGUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TRX', binance_symbol='TRXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='kSHIB', binance_symbol='1000SHIBUSDT', hl_unit_multiplier=1000, venue_unit_multiplier=1000, verified_by=_K_PREFIX_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='UNI', binance_symbol='UNIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SEI', binance_symbol='SEIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='RUNE', binance_symbol='RUNEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='OX', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'OXUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='FRIEND', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'FRIENDUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='SHIA', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'SHIAUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='CYBER', binance_symbol='CYBERUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ZRO', binance_symbol='ZROUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BLZ', binance_symbol='BLZUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='DOT', binance_symbol='DOTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BANANA', binance_symbol='BANANAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TRB', binance_symbol='TRBUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='FTT', binance_symbol='FTTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LOOM', binance_symbol='LOOMUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='OGN', binance_symbol='OGNUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='RDNT', binance_symbol='RDNTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ARK', binance_symbol='ARKUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BNT', binance_symbol='BNTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CANTO', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'CANTOUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='REQ', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'REQUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='BIGTIME', binance_symbol='BIGTIMEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='KAS', binance_symbol='KASUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ORBS', binance_symbol='ORBSUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BLUR', binance_symbol='BLURUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TIA', binance_symbol='TIAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BSV', binance_symbol='BSVUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ADA', binance_symbol='ADAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TON', binance_symbol='TONUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MINA', binance_symbol='MINAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='POLYX', binance_symbol='POLYXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='GAS', binance_symbol='GASUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PENDLE', binance_symbol='PENDLEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='STG', binance_symbol='STGUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='FET', binance_symbol='FETUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='STRAX', binance_symbol='STRAXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='NEAR', binance_symbol='NEARUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MEME', binance_symbol='MEMEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ORDI', binance_symbol='ORDIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BADGER', binance_symbol='BADGERUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='NEO', binance_symbol='NEOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ZEN', binance_symbol='ZENUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='FIL', binance_symbol='FILUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PYTH', binance_symbol='PYTHUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SUSHI', binance_symbol='SUSHIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ILV', binance_symbol='ILVUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='IMX', binance_symbol='IMXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='kBONK', binance_symbol='1000BONKUSDT', hl_unit_multiplier=1000, venue_unit_multiplier=1000, verified_by=_K_PREFIX_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='GMT', binance_symbol='GMTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SUPER', binance_symbol='SUPERUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='USTC', binance_symbol='USTCUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='NFTI', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'NFTIUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='JUP', binance_symbol='JUPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='kLUNC', binance_symbol='1000LUNCUSDT', hl_unit_multiplier=1000, venue_unit_multiplier=1000, verified_by=_K_PREFIX_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='RSR', binance_symbol='RSRUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='GALA', binance_symbol='GALAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='JTO', binance_symbol='JTOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='NTRN', binance_symbol='NTRNUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ACE', binance_symbol='ACEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MAV', binance_symbol='MAVUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='WIF', binance_symbol='WIFUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CAKE', binance_symbol='CAKEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PEOPLE', binance_symbol='PEOPLEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ENS', binance_symbol='ENSUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ETC', binance_symbol='ETCUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='XAI', binance_symbol='XAIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MANTA', binance_symbol='MANTAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='UMA', binance_symbol='UMAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ONDO', binance_symbol='ONDOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ALT', binance_symbol='ALTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ZETA', binance_symbol='ZETAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='DYM', binance_symbol='DYMUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MAVIA', binance_symbol='MAVIAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='W', binance_symbol='WUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PANDORA', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'PANDORAUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='STRK', binance_symbol='STRKUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PIXEL', binance_symbol='PIXELUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AI', binance_symbol='AIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TAO', binance_symbol='TAOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AR', binance_symbol='ARUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MYRO', binance_symbol='MYROUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='kFLOKI', binance_symbol='1000FLOKIUSDT', hl_unit_multiplier=1000, venue_unit_multiplier=1000, verified_by=_K_PREFIX_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BOME', binance_symbol='BOMEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ETHFI', binance_symbol='ETHFIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ENA', binance_symbol='ENAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MNT', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'MNTUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='TNSR', binance_symbol='TNSRUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SAGA', binance_symbol='SAGAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MERL', binance_symbol='MERLUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='HBAR', binance_symbol='HBARUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='POPCAT', binance_symbol='POPCATUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='OMNI', binance_symbol='OMNIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='EIGEN', binance_symbol='EIGENUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='REZ', binance_symbol='REZUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='NOT', binance_symbol='NOTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TURBO', binance_symbol='TURBOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BRETT', binance_symbol='BRETTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='IO', binance_symbol='IOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ZK', binance_symbol='ZKUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BLAST', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'BLASTUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='LISTA', binance_symbol='LISTAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MEW', binance_symbol='MEWUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='RENDER', binance_symbol='RENDERUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='kDOGS', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named '1000DOGSUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='POL', binance_symbol='POLUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CATI', binance_symbol='CATIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CELO', binance_symbol='CELOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='HMSTR', binance_symbol='HMSTRUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SCR', binance_symbol='SCRUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='NEIROETH', binance_symbol='NEIROETHUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='kNEIRO', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named '1000NEIROUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='GOAT', binance_symbol='GOATUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MOODENG', binance_symbol='MOODENGUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='GRASS', binance_symbol='GRASSUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PURR', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'PURRUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='PNUT', binance_symbol='PNUTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='XLM', binance_symbol='XLMUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CHILLGUY', binance_symbol='CHILLGUYUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SAND', binance_symbol='SANDUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='IOTA', binance_symbol='IOTAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ALGO', binance_symbol='ALGOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='HYPE', binance_symbol='HYPEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ME', binance_symbol='MEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MOVE', binance_symbol='MOVEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='VIRTUAL', binance_symbol='VIRTUALUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PENGU', binance_symbol='PENGUUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='USUAL', binance_symbol='USUALUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='FARTCOIN', binance_symbol='FARTCOINUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AI16Z', binance_symbol='AI16ZUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AIXBT', binance_symbol='AIXBTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ZEREBRO', binance_symbol='ZEREBROUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BIO', binance_symbol='BIOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='GRIFFAIN', binance_symbol='GRIFFAINUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SPX', binance_symbol='SPXUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='S', binance_symbol='SUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MORPHO', binance_symbol='MORPHOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TRUMP', binance_symbol='TRUMPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MELANIA', binance_symbol='MELANIAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ANIME', binance_symbol='ANIMEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='VINE', binance_symbol='VINEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='VVV', binance_symbol='VVVUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='JELLY', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'JELLYUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='BERA', binance_symbol='BERAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='TST', binance_symbol='TSTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LAYER', binance_symbol='LAYERUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='IP', binance_symbol='IPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='OM', binance_symbol='OMUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='KAITO', binance_symbol='KAITOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='NIL', binance_symbol='NILUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PAXG', binance_symbol='PAXGUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PROMPT', binance_symbol='PROMPTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='BABY', binance_symbol='BABYUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='WCT', binance_symbol='WCTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='HYPER', binance_symbol='HYPERUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ZORA', binance_symbol='ZORAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='INIT', binance_symbol='INITUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='DOOD', binance_symbol='DOODUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LAUNCHCOIN', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'LAUNCHCOINUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='NXPC', binance_symbol='NXPCUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SOPH', binance_symbol='SOPHUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='RESOLV', binance_symbol='RESOLVUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SYRUP', binance_symbol='SYRUPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PUMP', binance_symbol='PUMPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='PROVE', binance_symbol='PROVEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='YZY', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'YZYUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='XPL', binance_symbol='XPLUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='WLFI', binance_symbol='WLFIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LINEA', binance_symbol='LINEAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SKY', binance_symbol='SKYUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ASTER', binance_symbol='ASTERUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AVNT', binance_symbol='AVNTUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='STBL', binance_symbol='STBLUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='0G', binance_symbol='0GUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='HEMI', binance_symbol='HEMIUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='APEX', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'APEXUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
    SymbolMapping(hl_symbol='2Z', binance_symbol='2ZUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ZEC', binance_symbol='ZECUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MON', binance_symbol='MONUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MET', binance_symbol='METUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='MEGA', binance_symbol='MEGAUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CC', binance_symbol='CCUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='ICP', binance_symbol='ICPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AERO', binance_symbol='AEROUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='STABLE', binance_symbol='STABLEUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='FOGO', binance_symbol='FOGOUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='LIT', binance_symbol='LITUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='XMR', binance_symbol='XMRUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AXS', binance_symbol='AXSUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='DASH', binance_symbol='DASHUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='SKR', binance_symbol='SKRUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='AZTEC', binance_symbol='AZTECUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CHIP', binance_symbol='CHIPUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='GRAM', binance_symbol='GRAMUSDT', hl_unit_multiplier=1, venue_unit_multiplier=1, verified_by=_STANDARD_1_TO_1_CITATION, status='mapped', reason=None),
    SymbolMapping(hl_symbol='CASHCAT', binance_symbol=None, hl_unit_multiplier=None, venue_unit_multiplier=None, verified_by=None, status='unmatched', reason="no Binance USDⓈ-M PERPETUAL/USDT contract named 'CASHCATUSDT' exists (F14); likely Hyperliquid-native or Binance-unlisted token"),
)

_BY_HL_SYMBOL = {m.hl_symbol: m for m in SYMBOL_MAPPINGS}

if len(_BY_HL_SYMBOL) != len(SYMBOL_MAPPINGS):
    raise AssertionError("duplicate hl_symbol entries in SYMBOL_MAPPINGS")

MAPPED_COUNT = sum(1 for m in SYMBOL_MAPPINGS if m.status == "mapped")
UNMATCHED_COUNT = sum(1 for m in SYMBOL_MAPPINGS if m.status == "unmatched")


def get_mapping(hl_symbol: str) -> Optional[SymbolMapping]:
    """Returns the `SymbolMapping` for a Hyperliquid symbol, or `None` if the
    symbol is not present in the table at all (e.g. listed on Hyperliquid
    AFTER this table was generated -- a stale-table condition the caller
    should treat as "no proxy available", never silently guessed at).
    """
    return _BY_HL_SYMBOL.get(hl_symbol)


def mapped_symbols() -> tuple:
    return tuple(m.hl_symbol for m in SYMBOL_MAPPINGS if m.status == "mapped")


def unmatched_symbols() -> tuple:
    return tuple(m.hl_symbol for m in SYMBOL_MAPPINGS if m.status == "unmatched")
