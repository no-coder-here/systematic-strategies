---
name: project-qr-prep-001
description: QR-PREP-001 pre-alpha-research cleanup; spec docs/qr_prep_001_spec.md v1.0 FROZEN; PRE-RESEARCH FAIL on the runner after 3 audits; corrected HL venue facts; accreting-archive defect class
metadata:
  type: project
---

Sixth work order, 2026-08-18/19. Small cleanup pass before alpha research. Spec
`docs/qr_prep_001_spec.md` **v1.0 FROZEN** (P§1 HL 1h persistence, P§2 Binance re-ingest,
P§3 housekeeping, P§4 minimal runner, P§5 seal bootstrap OOS). Sits on
[[project-qr-methodology-001]], [[project-qr-infra-002]], [[project-qr-data-001]].

**Outcome: PRE-RESEARCH FAIL.** P§2/P§3 PASS. P§1 substantially done. **P§4 FAILED three
independent audits** and its repair budget is exhausted. P§5 never executed. Nothing committed.

## P§4 runner — the blocking item, and why it kept failing

`src/research/runner.py` (untracked). The mechanism is good and heavily mutation-killed (34
mutations, 27 killed); what failed three times is that **its docstring stated a guarantee that is
false**. The lesson generalises: for a *mandatory* path, the stated guarantee IS the deliverable.

Measured holes, all "engine runs, metrics computed, ZERO records":
1. `datasets` absent → `record_experiment(datasets=())` raises **inside** `record_run`'s
   `except BaseException`, so the FAILED branch never completes and the researcher's exception is
   demoted to `__context__`. Success path is unaffected because datasets are derived from
   `result.provenance` — so the hole is invisible until something fails.
2. Guard fixed as truthiness-only → `("str",)`, `(None,)`, `5`, `{"a":1}` all reconstitute (1).
3. `dataset_windows` unvalidated → **success** path registers nothing (worse: real numbers lost).
   Runner checks `datasets ⊆ windows` only; `_build_datasets` demands exact set equality vs
   `result.provenance`, so extra/mismatched keys still yield zero records.
4. **N1, still open:** `hypothesis_id`/`search_space_id` are unconditionally mandatory *because*
   the runner forces `experiment_type="alpha_research"`; omitting either gives zero records on
   BOTH paths. This is the ordinary caller shape.
5. The post-hoc `list_experiments()` before/after net makes gaps loud, but: false-negative under a
   concurrent writer (it compares id *sets*, not "is my run present"); can itself raise
   `RegistryIntegrityError` and replace the original exception; converts `KeyboardInterrupt` into a
   `RuntimeError` subclass; and costs **two** full scans per call, quadratic in registry size
   (measured 2.9s at 400 records, ~46s at 1,600).

**Escalations pending user decision:** whether P§4.8 may be narrowed to permit *presence* checks on
`hypothesis_id`/`search_space_id` and exact `datasets`↔`dataset_windows` set equality; and whether
to add an ids-only method to the frozen registry (`src/registry/store.py`) for the cost problem.

## Corrected Hyperliquid venue facts (measured 2026-08-19 — I reported these WRONG first)

- **There is NO bar-finality lag.** Two back-to-back `candleSnapshot` calls for one window agree
  exactly (6 symbols, 0 disagreements). Bars aged 24h/72h/168h match persisted values exactly on
  open/high/low/close/volume/trade_count (10 symbols). Closed bars are final in the RECENT region.
- **But `live` is NOT authoritative at the LEFT EDGE of the rolling window — this is the big one.**
  Measured on SKR 2026-08-19: 18 of its earliest persisted hours hold real trading (e.g. vol
  30,297,040 / 5,695 trades) while the API now returns **`volume=0, trade_count=0`** for those same
  timestamps, and 2 bars have vanished from live entirely. As the ~209-day window rolls forward the
  oldest bars degrade to zero-volume placeholders before dropping off. **Existing-wins protected
  genuine data from being zeroed.** Corollary: any "reconcile persisted against live" tool MUST
  refuse to replace a real bar with a zero-volume/absent one, and should only operate on the recent
  region. Do not generalise "live is authoritative" — it is true only near the right edge.
  I relayed an engineer's "systemic finality drift, volume/trade_count drifts for tens of minutes"
  to the user before measuring it. It was our own corruption (below). See [[measure-dont-cite]].
- **`fundingHistory` is NOT rolling** — it paginates *forward* from genesis, 500 records/call
  (BTC/ETH/SOL back to 2023-05-12, HYPE to 2024-12-05). So funding can be backfilled any time and
  is not at risk of loss; only `candleSnapshot` is urgent.
- `candleSnapshot` 1h reaches back ~209 days only. **38 of 232 symbols now have ZERO native 1h
  history** (delisted/renamed: MATIC, RNDR, FTM, …) because their window expired before we ever
  persisted. **Permanently unrecoverable** — a realised instance of the loss P§1 existed to stop,
  and a survivorship consideration for cross-sectional work.

## Reusable defect class: accreting archive + existing-wins + multi-pass ingest

An archive over a rolling endpoint must keep "existing rows win" or history walks forward. But that
same rule **freezes any wrong value you write first**. Writing the *current, unclosed* bar therefore
poisons it permanently: measured old-vs-true PYTH volume 3,873 vs 1,050,239 (271×), PUMP 26.2M vs
502.4M. Fix is `_drop_unclosed_bars` (`timestamp + 1h <= now`) BEFORE accretion.
**And a trailing-row-only repair is insufficient** — bars poisoned at an earlier pass's boundary get
buried by later appends and become invisible. Detect corpus-wide with a `--force-full-backfill`
pass, which counts conflicts without fixing them (51 interior bars across 51 symbols found this way,
after 148 trailing bars had already been repaired).

## P§2 result worth keeping

Binance re-ingest to `qr-data-001-v1.3`: **all 210 parquet byte-identical** to the v1.2-era files, so
the v1.2→v1.3 delta was provenance-only (the `unit_multiplier` split, never applied to values).
`PROCESSING_VERSION` only ever existed as v1.3 in committed history — v1.2 came from an uncommitted
working tree. Residual: `code_version` is `null` in all 210 sidecars because an orphan ingest ran with
its cwd inside a worktree I had deleted, so `git rev-parse HEAD` failed; needs one clean re-ingest,
currently blocked because `list_available_months` (data.vision bucket listing) returns truncated
chunked responses and does **not** retry `IncompleteRead`.
