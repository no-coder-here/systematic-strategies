# Deferred items / TODO

Tracked, deliberately deferred work. Nothing here blocks the research pipeline.

---

## DEFERRED-001 — Hyperliquid `event_price` funding via `asset_ctxs`

**Status:** deferred by user decision, 2026-08-16. Not a blocker.
**Spec:** `docs/data_contract.md` v1.4, D§5.5.1 (rule 2), D§12 item 8.
**Code:** `src/data/hyperliquid/asset_ctxs_archive.py`, `src/data/hyperliquid/oracle.py`,
`scripts/asset_ctxs_ingest.py`. Tests: `tests/data/test_asset_ctxs_archive.py`,
`tests/data/test_oracle.py`.

### What is true today

1. **`event_price` funding is supported by the architecture but has NOT been live-backfilled or
   validated.** The extraction pipeline is implemented and unit-tested against mocked segments —
   streaming per segment, bounded raw footprint, 2-minute gap cap, persisted high-water mark,
   incremental refresh, alignment reporting. It has **never been run against the real
   Requester-Pays S3 archive**, so actual egress, processed size, real alignment statistics, and
   the archive's schema stability across its full span are all **unverified**.

2. **Current research and backtests MUST use `funding_notional_basis="period_start"` explicitly.**
   This is the operative default. Its error is bounded by `|rate| × (max intra-period price move)`
   — backtest contract §7.6 records a −6.98% misstatement of a day's funding on a +15% intra-day
   move. This is not negligible and MUST be stated in research outputs.

3. **Do NOT claim `event_price` funding provenance unless the oracle dataset actually exists.**
   A missing compact artifact leaves `notional_price` NaN and the basis `period_start`. It must
   never be silently reported as `event_price`, and no placeholder artifact may be fabricated.
   (Verified at DATA PASS: `oracle_price_lookup` defaults to `None`; no artifact exists under
   `data/`; no default claims `event_price` support.)

4. **A future task may perform the compact `asset_ctxs` extraction and validation.** Requires fresh
   explicit authorization — the archive is Requester Pays and egress bills to our AWS account.

### If it is ever picked up

- Measured cost basis: archive is **1169 files / 9.63 GB / 8.24 MB per day**, ≈ **$0.87** egress,
  ≈ 4.3 h sequential at the measured 13.4 s/day. Retained output ≈ 50–100 MB.
- Raw segments must be discarded as processed; peak local raw footprint ≈ one segment.
- **Unverified assumption to check first:** archive segments are UTC calendar days. Confirmed for
  exactly one sampled segment (`20250601`: 198 symbols, 284,922 rows, BTC 00:00→23:59 with 1,439
  rows — one minute absent), **not** across the three-year span.
- Repair D3 (persisting cross-day carry state in the high-water mark) fixed a defect that only
  manifests in the real incremental usage pattern. Related edge cases in this module are
  correspondingly under-exercised, since none of it has run on real data.

---

## DEFERRED-002 — Instrument-lineage mapping (renames/migrations)

Deferred by user DECISION 2 (2026-08-16). `MATIC`/`POL`, `RNDR`/`RENDER`, `FTM`/`S` and similar stay
**unmapped**; migrations must never be inferred from symbol similarity. A reviewed, explicit lineage
mapping layer is future work. Until then these assets carry native-only history.
See `docs/data_contract.md` D§16.3.3, D§6.4.

---

## DEFERRED-003 — `verified_by` citation granularity

`src/data/symbol_map.py` uses one boilerplate citation reused across ~195 "standard 1:1" entries
rather than per-instrument evidence. No live impact today — no mapped Binance symbol carries a scale
prefix other than the four `1000*` k-tokens — but it becomes a rubber stamp if the table is extended
with an unusual contract. Flagged by the independent audit; not blocking.

---

## DEFERRED-004 — Full-universe live validation

`get_universe()` inference has not been exercised against all 232 symbols at live scale (BTC/ETH
scoped only). Likewise the 1h→4h/1d aggregation fallback for Hyperliquid-native data is
real-data-unexercised, since native 4h/1d reach further back than 1h (contract F2).

---

## Known limitation (not deferred work — a standing property of the data)

Native Hyperliquid **1h** OHLCV reaches back only ~208 days via `candleSnapshot`, and trade-level
archives bottom out at 2025-03-22. This is why Binance USDⓈ-M 1h is the canonical long-history price
series (proxy-labelled). 22 of 232 Hyperliquid assets have no Binance perp and are limited to native
history — universe depth therefore correlates with "is this a Binance-listed major", which is a
selection effect to keep in mind in cross-sectional research.

---

## DEFERRED-005 — `CODE_SCOPE_PATTERNS` covers only `*.py` + `pyproject.toml`

**Status:** deferred by Research Lead, 2026-08-17 (QR-INFRA-002 spec v1.2 R§20.9.2, accepted
limitation D13). Not a blocker **today**, with a hard trigger.

The experiment registry's `code_fingerprint` (and therefore `exact_hash`) hashes only
`src/**/*.py`, `strategies/**/*.py`, `experiments/**/*.py`, `scripts/**/*.py`, `conftest.py`,
`pyproject.toml`. A strategy driven by a JSON/YAML/TOML parameter file, an environment variable or
a CLI argument would be **invisible to experiment identity** unless the caller mirrors it into
`strategy.params`. Two genuinely different computations would then share an `exact_hash` and be
compared as `REPRODUCED`/`DIVERGED` — a real difference reported as a determinism defect, or (if
the numbers happen to match) as a clean rerun.

Deferred because no config-file-driven strategy exists yet, and widening the scope now would churn
every experiment's identity for zero present benefit while adding an untested data-file hashing
path. The cheap half was adopted immediately: `runtime_env` is captured in every record's
`run_facts`.

**MUST be closed before the first config-file-driven strategy is registered.** Extend
`CODE_SCOPE_PATTERNS` to `*.yaml|*.yml|*.json|*.toml` under `strategies/**` and `experiments/**`.

---

## QR-INFRA-002-A — Experiment registry test hardening (**CLOSED** 2026-08-17)

**Status: CLOSED — REGISTRY PASS WITH WARNINGS.** Independent focused re-audit: all 4 production
defects closed by measurement, 14/14 per-field identity mutations and 13/13 known survivors RED,
**zero survivors remain**. Registry suite 245 -> 292 tests; full suite 823 passed, 6 skipped.
Residual non-blocking items moved to QR-INFRA-002-B below. Original scope, for the record: QR-INFRA-002 returned **REGISTRY FAIL** after its one permitted
repair cycle; the work order's cycle budget is exhausted and a follow-up needs authorization.

The registry is **functionally correct as delivered** — the migration produces correct records,
`verify_registry()` is clean, and every integrity mechanism was demonstrated working when
exercised. What failed is **regression detection**: the final audit ran ~120 mutations and 20
non-vacuous ones SURVIVED, meaning ~20 specific behaviours have no test that can fail if they
break. Spec v1.2 R§18.2 makes `SURVIVED` blocking.

Scope (bounded — mostly tests, four small code fixes):

1. **Four code defects.** (a) `_recorded_via` and `_logged_at_override` are ordinary keywords of
   the public `record_experiment`, so an external caller can set `recorded_via="adapter"` in one
   keyword and bypass every R§20.3 guard — this reconstitutes the proxy-as-native exploit with no
   rendered caveat. Move the adapter path behind a capability token. (b) `render_summary` renders
   the immutable `r.notes`, not the folded `fe.notes`, so a later `annotate()` correction never
   appears. (c) `manual_results_justification` is recorded but never rendered (R§20.3.2 requires
   both). (d) R§20.2.2's static registration check blanket-excludes `registry_migration/`, an
   undisclosed second exemption.
2. **~20 discriminating tests**, one per surviving mutation. The five most serious: every hashed
   `DatasetRef` field (`native_or_proxy`, `processing_version`, `data_start/end`, `eval_start/end`,
   `content_hash`) is individually deletable from the `semantic_hash` payload with the whole suite
   green — the only test varies two of them *together*, so it detects only the conjunction.
3. **R§20.8.5 fixture at the right layer** — the zero-warm-up boundary test goes through
   `record_experiment`/`DatasetRef.__post_init__` and never reaches
   `_check_window_containment`, so the adapter's `data_start <=` bound is still unprotected.
4. **Cross-process byte-identity test** for record files (unsorted record-level warnings survive
   today because the fixture writes both records in one process; `tuple(set)` ordering differs
   across `PYTHONHASHSEED`).
5. Spec **v1.3** amendment to legitimise stripping `frozen_spec_sha256` from `config_family_hash`
   (currently a code comment deviating from a frozen normative list) and to record the residual
   OOS evasion: an overlapping prior in-sample record is not flagged if the OOS record declares a
   different `search_space_id` and an unrelated parent.

Until this closes, treat registry records as **trustworthy but not regression-protected**: a future
edit to the hashing or warning logic can silently change experiment identity.


---

## QR-INFRA-002-B — Registry polish (OPEN, non-blocking)

Residual WARNINGS from the QR-INFRA-002-A focused re-audit. None affects identity, provenance,
lineage, persistence or research-integrity semantics; the auditor graded each non-blocking. Cheap
enough to fold into whatever work next touches this code.

1. **`_ADAPTER_CAPABILITY` confinement has no regression test.** Production is currently correct
   (`src/registry/backtest_adapter.py` is the sole holder, and it is absent from `__all__`), but
   nothing stops a future module adding `from registry.store import _ADAPTER_CAPABILITY` and
   self-granting adapter trust — measured to yield `recorded_via="adapter"` with hand-typed metrics
   and no caveat. Fix: a static AST test asserting only `backtest_adapter.py` may name it. The
   pattern already exists in `tests/registry/test_registry_layering.py`.
2. **The mandatory-registration AST scan is evaded by an aliased import.** Measured GREEN for
   `from backtest.engine import run_backtest as _rb; _rb(...)`. Outside R§21.3.2's literal scope
   (which was about comment mentions), but a one-line fix: also inspect `ast.alias.asname`.
3. **`run_and_register` mutates the caller's dict** (`record_kwargs.pop("dataset_windows")`), so a
   caller reusing one dict across two runs loses it on the second. Fails loudly, no silent
   corruption. Fix: `dict(record_kwargs)`.
4. **A28 is protected only conjunctively.** Record-level warnings are sorted twice
   (`store.py:511` and `models.py:722`); removing either alone is undetectable, removing both goes
   RED. A vacuous mutation, explicitly permitted by spec R§18.2 — not a code defect.
5. **Two tests write into the live `experiments/` tree** (the R§21.3.3-mandated rogue drivers, and
   `test_migration.py`'s scratch root). Both clean up in `finally` and byte-identical restoration was
   verified, but a hard kill mid-test leaves artifacts. Consider a session-scoped cleanup fixture.
6. **`ArtifactRef.__init__` remains permissive** — a forged `sha256` on an existing file is caught by
   `verify_artifacts` (`MODIFIED`) and `verify_registry`, but a forged hash on a *missing* file is
   reported `MISSING`/`UNVERIFIABLE`, so the fabrication itself goes undetected in that case. The
   `from_dict` round-trip genuinely blocks re-hashing in `__post_init__`; the capability-token
   pattern would gate construction instead.

**Not in this item** — spec R§21.10 D13/D17/D18/D19 are deferred to the research-methodology /
protected-OOS layer: config files outside the code fingerprint, unrecorded experiments, misleading
`search_space_id`, and full OOS-contamination enforcement. **D17 (mandatory workflow integration)
is a prerequisite for alpha research**, not for registry closure.
