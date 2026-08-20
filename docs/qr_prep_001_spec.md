# QR-PREP-001 — Pre-research preparation work order (FROZEN v1.0, 2026-08-18)

Scope: mechanical preparation before QR-RESEARCH-001. **This document adds no methodology.**
It implements a subset of `docs/research_methodology.md` M§4 and M§9.1.6 mechanics already
authorised there, plus two housekeeping items. No frozen spec is amended.

Frozen upstream artifacts (MUST NOT be modified by this work order):
`docs/backtest_contract.md`, `docs/data_contract.md`, `docs/experiment_registry_spec.md`,
`docs/research_methodology.md`, `experiments/registry/**` (records, history.jsonl).

---

## P§1 Hyperliquid native 1h persistence + incremental refresh

**Problem (measured 2026-08-18).** `candleSnapshot` serves a rolling ~209-day window and
silently returns only the most RECENT ~5000 bars of an over-large request
(`data_contract.md` D§1 F1). Only `hyperliquid.ohlcv.1h.BTC` is persisted
(2026-01-20T11:00Z → 2026-08-16T17:00Z). Every day not persisted is lost permanently.

- **P§1.1 Universe.** All symbols returned by `HyperliquidProvider.get_universe()`, delisted
  included. Frequency `1h` only; 4h/1d stay derived (D§ aggregation rule).
- **P§1.2 Source restriction (blocking).** `candleSnapshot` via the existing
  `HyperliquidProvider.get_ohlcv` path ONLY. MUST NOT read or reconstruct from `node_trades`,
  `node_fills`, `node_fills_by_block`, L2 book, or `asset_ctxs`. Fills are not trades
  (D§14.7); reconstruction would create a regime break at the source seam.
- **P§1.3 Accreting archive semantics (blocking).** The persisted dataset is an accreting
  archive of a rolling source, NOT a mirror of the current API response. On every write:
  1. Load the existing persisted frame if present.
  2. Union with the freshly fetched frame, keyed `(symbol, timestamp)`.
  3. **Existing persisted rows win on conflict.** A conflicting value for an
     already-persisted key MUST be counted and reported, never silently overwritten.
  4. **Refuse the write** (raise, non-zero exit, leave prior file untouched) if the union
     would reduce row count, or move `min(timestamp)` later, for any symbol.
     This is the defect that would silently walk native history forward and, per
     methodology M§9.9, invalidate a sealed OOS window.
- **P§1.4 Provenance.** `processing_version` = `src.data.provenance.PROCESSING_VERSION`
  (currently `qr-data-001-v1.3`). `start_timestamp`/`end_timestamp` describe the UNION span.
  `retrieved_at` is the latest fetch. `notes` MUST state that the dataset accretes across
  refreshes from a rolling endpoint and therefore may extend earlier than any single
  API response.
- **P§1.5 Incremental refresh.** One entry point, two modes: full backfill (fetch the whole
  currently-available rolling window) and refresh (fetch from
  `last_persisted_timestamp - 3 bars` to now, then union per P§1.3). Refresh MUST be safe to
  run repeatedly and MUST be a no-op on data content when no new bars exist.
- **P§1.6 Summary artifact.** `data/metadata/hyperliquid/_ingest_summary.json`, per symbol:
  `start`, `end`, `rows`, and `conflicts` count. Committed (metadata is not market data).
- **P§1.7 Tests (offline, mocked client).** One test per behaviour, each independently
  mutation-provable: older rows retained when the fetch window starts later; conflict
  counted and existing value preserved; row-count regression refused; `min(timestamp)`
  regression refused; second identical run leaves parquet bytes unchanged; provenance
  version stamped from the constant, not a literal; source-restriction test asserting the
  ingest module does not import any trade/fill/L2/asset_ctxs module.

## P§2 Binance corpus re-ingest at the current processing version

Persisted Binance provenance is `qr-data-001-v1.2`; running code is `v1.3`, so
`read_binance_ohlcv_parquet` emits `ProvenanceVersionMismatchWarning` and every future
registry record would carry `PROCESSING_VERSION_MISMATCH`.

- **P§2.1** Re-run `scripts/binance_bulk_ingest.py` with current code over the full mapped
  symbol set. Provenance MUST end at `v1.3` from the constant.
- **P§2.2 Byte-identity report (blocking evidence, not a pass condition).** Record the
  per-symbol parquet sha256 before and after. Report counts of identical vs changed, and for
  any changed symbol the row-count and span delta. A change is not automatically a defect,
  but an unexplained content change under a version bump MUST be reported, not absorbed.
- **P§2.3 Historical records are immutable.** MUST NOT modify, delete or re-hash anything
  under `experiments/registry/**`. Existing records legitimately continue to state `v1.2` —
  that is what was true when they ran (M§14).
- **P§2.4** `checksum_manifest_entries` (upstream Binance zip hashes) MUST be preserved.
- **P§2.5** No stray artifacts: any pre-ingest backup used for P§2.2 is removed before
  reporting, and `git status --porcelain` is compared against the pre-work baseline.

## P§3 Housekeeping

- **P§3.1** Remove stale `.claude/worktrees/agent-*` worktrees and their
  `worktree-agent-*` branches, only after confirming each has no uncommitted or unmerged
  work. Handled by Research Lead directly.
- **P§3.2** `git rm --cached .DS_Store`. `.gitignore` already excludes it.

## P§4 Minimal research runner (M§4 subset)

Purpose: make "every alpha run is registered, pass or fail" true, and nothing else.

- **P§4.1 Location/signature.** `src/research/runner.py`:
  `run_research_experiment(*, registry, config, market_data, strategy_output,
  record_kwargs, research_root, **run_kwargs) -> BacktestResult`.
- **P§4.2** Delegates to the frozen `registry.backtest_adapter.run_and_register`. The runner
  MUST NOT compute, adjust, reinterpret or round any performance number, and MUST NOT
  re-implement any engine or registry behaviour.
- **P§4.3** `record_kwargs` MUST be copied, not mutated (closes `docs/TODO.md`
  QR-INFRA-002-B item 3).
- **P§4.4 Uniform typing (M§4.4).** `experiment_type` is set to `alpha_research`. A caller
  supplying any other value is refused with a clear error — relabelling defeats
  `near_duplicates()` and `OOS_RELABEL_OF`.
- **P§4.5 Registration on failure (the point of the work order).** A record MUST exist for
  every invocation: `COMPLETED` on success, `FAILED` on exception (re-raised, never
  swallowed), `INVALID` on normal exit without a result. This behaviour lives in the frozen
  registry; the runner's job is to not defeat it.
- **P§4.6 `research_root`.** Required parameter (M§4 mandates it for testability).
  Its ONLY current use is validation that the directory exists. This MUST be stated in the
  docstring so it is not mistaken for enforcement.
- **P§4.7 Honest-limit docstring (M§4.5, blocking).** The module docstring MUST state that
  D17 is mitigated, not closed — hiding a run requires only not calling this function — and
  MUST state that no M§5, M§7, M§8, M§9 or M§10 gate is implemented. No stronger claim.
- **P§4.8 Out of scope (MUST NOT be built).** Hypothesis/search-space/robustness-plan
  *content* validation, ledgers, protected-window guards, pre-OOS gates, stage-transition checks,
  AST-scan changes, CLI, config loader. That is a later work order.
  > **AMENDMENT v1.1 (user-authorised 2026-08-20).** P§4.8 does NOT prohibit *registration
  > preconditions*: the runner MUST require and validate the presence of `hypothesis_id` and
  > `search_space_id` (non-empty strings) and exact `datasets` <-> `dataset_windows` set
  > consistency, before the engine runs. Rationale: both fields are unconditionally mandatory in
  > `registry.models` *because* the runner forces `experiment_type="alpha_research"`, so omitting
  > either produced a measured ZERO-record run on both the success and FAILED paths. Verbatim user
  > authorisation: "I authorize narrowing the frozen methodology wording as needed for these
  > registration preconditions. This is not considered prohibited methodology enforcement."
  > Presence/consistency only — no content, file or semantic validation.
- **P§4.9 Tests.** One per behaviour: success path registers `COMPLETED` and returns the
  result unmodified; exception registers `FAILED` and re-raises the original exception;
  no-result registers `INVALID`; caller's `record_kwargs` dict unchanged after the call;
  non-`alpha_research` `experiment_type` refused; missing `research_root` refused;
  metrics on the record equal the engine's (runner is pass-through).

## P§5 Bootstrap protected-OOS reserve — seal only (M§9.1.6 subset)

Executed only after P§1 and P§2 land, so the sealed snapshots carry the final processing
version and the full native span.

- **P§5.1 In scope:** define the reserve intervals; persist the exact sliced datasets as
  write-once immutable snapshots under `research/oos/snapshots/`; record `dataset_id`,
  `content_hash`, `content_hash_method`, `processing_version`, source venue, native/proxy,
  `covers_start`/`covers_end`; run the M§9.1.2 prior-use scan and record
  `prior_infrastructure_use`; write and commit `research/oos/protected_windows.json`.
- **P§5.2 Hashing (M§9.9.1, blocking).** Hash the canonical normalized **long-form** frame
  sliced to each dataset's own `[covers_start, covers_end]`, inclusive both ends, upstream of
  `to_engine_frame`. MUST NOT hash a whole source file (three Binance spans measured sharing
  hash `64c05d5bf73f`) and MUST NOT hash the wide engine frame (raises).
- **P§5.3 Snapshots are untracked** (`data_contract.md` D§8.2 forbids committing market
  data). Integrity rests on the hashes inside the committed `protected_windows.json`.
- **P§5.4 Inaccessibility.** Snapshots live only under `research/oos/snapshots/`, which the
  ingest pipeline never writes to and which normal research loaders never read. The reserved
  spans must be excluded from research loads; enforcement code is deferred, so the exclusion
  is recorded in writing in `research/oos/README.md` and in the work-order report.
- **P§5.5 Intervals require user sign-off.** Which spans become permanently unavailable to
  research is a one-time irreversible methodology decision (M§9.1.6 "The bootstrap is
  one-time"; CLAUDE.md escalation criteria 2, 3, 6). Research Lead proposes; the user decides.
- **P§5.6 Deferred (explicitly NOT built here):** burn ledger, freeze manifests, pre-OOS
  gate, reveal accounting, runner-side protected-window guards.
