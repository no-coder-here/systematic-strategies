# QR-INFRA-002 Implementation Report — Experiment Registry

Implements `docs/experiment_registry_spec.md` v1.1 (FROZEN). Infrastructure
only — no strategy research, no changes to `src/backtest/`, `src/data/`,
`strategies/`, or `experiments/qr_smoke_001/`.

## 1. Files created

| file | lines |
|---|---|
| `src/registry/__init__.py` | 63 |
| `src/registry/serialize.py` | 196 |
| `src/registry/models.py` | 685 |
| `src/registry/codeid.py` | 175 |
| `src/registry/datahash.py` | 74 |
| `src/registry/store.py` | 900 |
| `src/registry/backtest_adapter.py` | 334 |
| `experiments/registry_migration/__init__.py` | 0 |
| `experiments/registry_migration/register_qr_smoke_001.py` | 590 |
| `experiments/registry/README.md` | 56 |
| `docs/qr_infra_002_baseline.sha256` | 43 |
| `tests/registry/conftest.py` | 20 |
| `tests/registry/_factories.py` | 177 |
| `tests/registry/test_serialize.py` | 150 |
| `tests/registry/test_identity_hash.py` | 154 |
| `tests/registry/test_lineage.py` | 93 |
| `tests/registry/test_retention.py` | 62 |
| `tests/registry/test_codeid.py` | 136 |
| `tests/registry/test_registry_provenance.py` | 83 |
| `tests/registry/test_rerun_detection.py` | 176 |
| `tests/registry/test_query.py` | 104 |
| `tests/registry/test_backtest_adapter.py` | 212 |
| `tests/registry/test_artifacts.py` | 73 |
| `tests/registry/test_persistence.py` | 116 |
| `tests/registry/test_registry_layering.py` | 73 |
| `tests/registry/test_research_invariants.py` | 130 |
| `tests/registry/test_summary.py` | 57 |
| `tests/registry/test_query_demo.py` | 100 |
| `tests/registry/test_migration.py` | 141 |

Files modified:

| file | change |
|---|---|
| `.gitignore` | added `experiments/registry/artifacts/` (R§9/R§10.1 — metadata committed, payloads not) |
| `pyproject.toml` | registered the `slow` pytest marker for the one real-pipeline migration test |

Real R§17 migration output (committed metadata, `experiments/registry/`):
`history.jsonl`, `records/EXP-*.json` (5 files), `README.md`. Gitignored
payloads under `experiments/registry/artifacts/` (dataset snapshots,
per-window equity curves, `query_demo.txt`) are NOT counted above since they
are not tracked deliverable files, but they exist on disk and are referenced
below.

**Environment action taken (not a spec ambiguity, recorded for transparency):**
this work order runs in an isolated git worktree that does not carry over
gitignored files. `data/processed/**` (the persisted Hyperliquid/Binance BTC
parquet caches QR-SMOKE-001 depends on) therefore did not exist in the
worktree. I copied (never re-fetched — no network call was made) the three
BTC parquet files (`hyperliquid.ohlcv.1h.BTC`, `hyperliquid.funding.BTC`,
`binance.ohlcv.1h.BTC`, ~3.8MB total) from the shared checkout's identical,
already-retrieved cache into the worktree's `data/processed/`, so the frozen
offline pipeline (and this work order's R§12.4.1/R§17 requirements, which
mandate exercising it against a **real** result) could run at all. This is a
pure filesystem copy of bytes already on disk, not a new fetch.

## 2. Flagged spec ambiguities

1. **R§13.4/R§17.5 Binance dataset_id.** The spec's own text (query demo #3,
   R§17.5's table) says `binance.um.ohlcv.1h.BTC`. Measured: the real data
   layer (`src/data/storage.py:binance_ohlcv_dataset_id`,
   `src/data/binance/provider.py:381`) produces `binance.ohlcv.1h.BTC` (no
   `um.` infix) — confirmed by the actual sidecar files under
   `data/metadata/binance/`. This is the same class of stale-claim error the
   spec's own "Corrections to v1.0" section fixed for `field_type` (item 3).
   **Resolution taken:** registered and queried using the measured real
   value `binance.ohlcv.1h.BTC` throughout (migration, tests, query demo
   #3), consistent with the spec's own precedent of trusting measurement
   over a stale claim. Not silently glossed over — flagged here.
2. **R§7.3 `col-buffer-v1` column-type coverage.** The spec's literal text
   names exactly three per-column encodings (datetime→int64ns, float→
   float64, string→length-prefixed UTF-8). The real OHLCV frames this method
   hashes (`src/data/schemas.py:OHLCV_COLUMNS`) also carry a plain `int64`
   column (`trade_count`) and a `bool` column (`native_traded`), which R§7.3
   does not name. **Resolution taken:** extended the same "raw
   little-endian value buffer" philosophy to these two cases (int64 LE per
   value; one byte 0/1 per value) rather than degrading them to the much
   weaker string encoding. Documented as an extension in
   `src/registry/datahash.py`'s module docstring.
3. **R§14.5 "repo-relative" resolution base.** The spec requires
   `frozen_spec_ref` to "resolve to an existing repo-relative file" but does
   not say relative to *what directory at call time* — the registry itself
   has no `repo_root` concept (only `root`, which for the real registry
   happens to be `<repo_root>/experiments/registry` but for test fixtures is
   an arbitrary `tmp_path`). **Resolution taken:** resolved relative to
   `Path.cwd()`, matching how every driver under `experiments/**` and the
   `pyproject.toml` `testpaths` convention already assume the process runs
   from the repo root. Documented in `src/registry/store.py`.
4. **R§4.9 PROVENANCE_INCOMPLETE emission channel.** R§4.9 assigns emission
   of this record-level token to "the adapter", but R§11's `record_experiment`
   public signature has no parameter through which an adapter-derived token
   could be passed in — `record_experiment` computes all other record-level
   warnings itself, generically, from its own inputs (code identity,
   datasets, survivorship, artifacts). PROVENANCE_INCOMPLETE is the one
   token that can only be known from a raw `BacktestResult.provenance_complete`,
   which `record_experiment` never sees. **Resolution taken:** added a
   private, keyword-only `_extra_warnings: tuple = ()` parameter to
   `record_experiment`, documented in-line as being for
   `backtest_adapter.record_backtest_result`'s exclusive use — the public
   R§11 signature is otherwise unchanged (default `()` is a no-op for every
   direct caller).
5. **`record_run`'s failure branch vs. R§12.1's provenance-derivation rule.**
   R§12.1 mandates that `datasets` always be derived from `result.provenance`
   (never caller-declared) to prevent a caller silently omitting a proxy
   dataset. On the FAILED path there is no `BacktestResult` at all — R§12.5
   explicitly says "the registry MUST NOT require a result" — so there is
   nothing to derive from. R§17's own record 5 (a FAILED, non-exempt
   `pipeline_validation` run) still needs non-empty `datasets` per R§4.4.3.
   **Resolution taken:** `record_run`'s exception branch accepts caller
   -supplied `datasets`/`backtest_config` directly (exactly as
   `record_experiment` itself would for any FAILED registration) — used only
   when there is no result to derive from; the success branch is unaffected
   and still enforces R§12.1's derivation rule in full. Documented in
   `src/registry/backtest_adapter.py`'s `record_run` docstring. The R§17
   migration registers record 5 via a **direct** `record_experiment` call
   (explicitly permitted by R§12.5), not `record_run`, since the rich
   measured `run_facts` (28,056 funding events, gap counts, etc.) are
   assembled from data gathered *before* the pre-flight check fires.
6. **R§2.1.1's runtime layering check is directional; the reverse is not
   implementable.** R§2.1.1 mandates a runtime check that importing
   `backtest.engine` in a fresh subprocess puts no `registry*` in
   `sys.modules` — implemented and passes
   (`tests/registry/test_registry_layering.py`). The reverse check (importing
   `registry` leaves no `backtest.engine`/`backtest.metrics` in
   `sys.modules`) is **not achievable**: `src/backtest/__init__.py` (FROZEN,
   v1.5.1, out of this work order's scope) itself does
   `from .engine import execution_instant, run_backtest`, so importing
   `backtest.models` — which R§2.1 explicitly permits `src/registry` to do —
   unavoidably executes `backtest/__init__.py` and loads `backtest.engine`
   (and transitively `backtest.metrics`) as a side effect, regardless of
   what `src/registry`'s own source contains. Measured and documented in
   `tests/registry/test_registry_layering.py`. The **static source scan**
   (registry's own files contain no `import backtest.engine`/`metrics`
   statement) is therefore the operative, correct enforcement of "registry
   MUST NOT import backtest.engine/metrics", and is implemented and passes.
7. **`docs/experiment_registry_spec.md` itself is absent from this isolated
   worktree** (present only in the shared checkout, apparently uncommitted
   there — `git log` in the worktree shows the same HEAD as the shared
   checkout, `f7b73c2`, with no further commits). I read the full spec via
   the file-reading tool (unsandboxed for reads) but could not reference it
   as an "existing repo-relative file" from code that runs *inside* the
   worktree. The R§14.5 out-of-sample tests therefore use
   `docs/backtest_contract.md` (a real file that does exist in the worktree)
   as a stand-in "any real repo-relative file" — this does not affect R§17
   at all, since all five migrated records are `research_stage=exploratory`,
   never `out_of_sample`, so `frozen_spec_ref` is never required for them.

## 3. Test count and result

143 tests in `tests/registry/` (one dedicated module per R§18.1 coverage
area, plus `test_migration.py` for R§17/offline-network guarantees).

```
143 passed in ~21s   (tests/registry/ only)
674 passed, 6 skipped in ~101s   (full tests/ suite, including tests/registry/)
```

No test writes into the real `experiments/registry/` — all use `tmp_path`
except the one full-migration smoke test, which uses a scratch directory
INSIDE the repo (`experiments/_test_migration_registry_scratch/`, required
because R§9 pins artifact paths as repo-root-relative, which `tmp_path`
cannot satisfy) that is removed in a `finally` block regardless of outcome,
and is never `experiments/registry/` itself.

## 4. Mutation table (M1–M28)

Applied to source, ran the named target test (or a corrected/strengthened
one where noted), restored, verified the full workspace manifest after
**every single mutation** (all 28 confirmed clean, see §5).

| # | mutation | target test | result |
|---|---|---|---|
| M1 | `created_at` in semantic_hash payload | `test_identity_hash.py::test_created_at_does_not_affect_semantic_or_exact_hash` | BROKE |
| M2 | `results` in the hash payload | `test_rerun_detection.py::test_M6_a_1e9_perturbation_is_DIVERGED_with_detail` | BROKE |
| M3 | drop `code_fingerprint` from `exact_hash` | `test_identity_hash.py::test_M3_code_fingerprint_is_part_of_exact_hash_even_with_identical_git_state` | BROKE |
| M4 | drop a `BacktestConfig` field from adapter dump | `test_backtest_adapter.py::test_M4_backtest_config_key_set_equals_dataclass_fields` | BROKE |
| M5 | `run_seq` format `{:d}` | `test_identity_hash.py::test_M5_run_seq_zero_padded_with_eleven_records` (11-record fixture, r0..r10) | BROKE (fails at r01 already, via the hardcoded `r00` baseline lookup raising `KeyError` rather than the r10-vs-r2 sort-order symptom — still fully discriminating) |
| M6 | `math.isclose(rel_tol=1e-6)` | `test_rerun_detection.py::test_M6_a_1e9_perturbation_is_DIVERGED_with_detail` | BROKE |
| M7 | raw float `==` (NaN-unsafe) | `test_rerun_detection.py::test_M7_nan_sharpe_rerun_is_REPRODUCED_not_diverged` | BROKE |
| M8 | `list_experiments()` filters to `COMPLETED` | `test_retention.py::test_default_listing_includes_every_status` | BROKE |
| M9 | unknown filter silently ignored | `test_query.py::test_M9_unknown_filter_raises` | BROKE |
| M10 | `symbol` filter substring match | `test_query.py::test_M10_symbol_filter_is_exact_not_substring` | BROKE |
| M11 | `dirty_worktree` without `--untracked-files=all` | `test_codeid.py::test_M11_untracked_in_scope_file_is_dirty` | **VACUOUS on first fixture** (1 of 1 discriminating cases: 0 — an untracked file in an *already-tracked* directory is still listed individually by git's default mode). Strengthened fixture to put the untracked file in a wholly-new, never-tracked subdirectory, added a self-guarding assertion proving the default mode collapses it to a single `?? dir/` line. Re-ran: **BROKE**. |
| M12 | git failure / missing `-C repo_root` | `test_codeid.py::test_M12_git_dashC_isolation_fixture_differs_from_live_head` | BROKE |
| M13 | remove `O_EXCL` (`open(...,"w")` equivalent) | `test_persistence.py::test_M13_write_once_guard_o_excl` | BROKE |
| M14 | `json.dumps(..., sort_keys=False)` | `test_identity_hash.py::TestKeyInsertionOrderInvariance` (both cases) | BROKE |
| M15 | `allow_nan=True`, no `$nonfinite` wrapper | `test_serialize.py::TestCanonicalFormProperties` (no-token + strict-parser) | BROKE |
| M16 | `SerializationError` → `str(obj)` fallback | `test_serialize.py::TestRoundTrip::test_unsupported_type_raises_no_str_fallback` | BROKE |
| M17 | skip malformed `history.jsonl` lines | `test_persistence.py::test_corrupt_history_line_raises_on_read` | BROKE |
| M18 | `native_or_proxy` defaults to `"native"` | `test_registry_provenance.py::test_M18_native_or_proxy_none_raises_never_silently_native` | BROKE |
| M19 | drop `out_of_sample` ⇒ `frozen_spec_ref`-resolves check | `test_research_invariants.py::test_M19_out_of_sample_requires_frozen_spec_ref_resolving_to_real_file` | BROKE |
| M20 | `status_reason` optional for `REJECTED` | `test_research_invariants.py::test_M20_status_reason_required_for_non_completed` | BROKE |
| M21 | prefix collision treated as rerun (`ID_PREFIX_HEX=2`) | `test_rerun_detection.py::test_M21_prefix_collision_detected_with_small_id_prefix_hex` (brute-forced a real 1-byte collision in <2000 tries) | BROKE |
| M22 | migration `data_start` from requested window, not loaded raw span | `test_migration.py::test_M22_migration_ohlcv_window_uses_loaded_raw_span_not_requested_window` (added — see note) | BROKE |
| M23 | `DatasetRef`s from caller list instead of `result.provenance` | `test_backtest_adapter.py::test_M23_omitted_provenance_dataset_raises` + `test_M23_extra_caller_window_with_no_provenance_element_raises` | BROKE |
| M24 | `PROXY_DATA` in `result_warnings` instead of record-level | `test_backtest_adapter.py::test_M24_proxy_data_is_record_level_even_on_a_failed_run` | BROKE |
| M25 | `NOT_COMPARABLE` collapsed to `DIVERGED` | `test_rerun_detection.py::test_M25_NOT_COMPARABLE_when_baseline_is_failed` | BROKE |
| M26 | `run_seq` counted from `history.jsonl` instead of `records/` | `test_persistence.py::test_M26_run_seq_counted_from_records_dir_not_history_self_healing` | BROKE (reproduces the exact "wedged forever" `RegistryError` the spec predicts) |
| M27 | strict containment (`<`/`>`) in R§12.4 | `test_backtest_adapter.py::test_M27_containment_is_inclusive_on_the_real_result` (real `run_window_a()` result) | BROKE |
| M28 | filters use creation-time status, not folded | `test_query.py::test_M28_filters_evaluate_folded_status` | BROKE |

**Note on M22:** the spec's target area (R§18.1(9)/R§17.5) is the
**migration's own** window-building logic
(`experiments/registry_migration/register_qr_smoke_001.py:_ohlcv_window`),
not the generic adapter (`_build_datasets`, which faithfully carries through
whatever `data_start` a caller supplies — that is correct and separately
tested). My first draft of this test built its own local window dict inside
the test file, which would not have been affected by mutating the migration
module at all — an inert-test risk of exactly the kind this work order warns
about. Added `test_migration.py::test_M22_...`, which imports and calls the
real `register_qr_smoke_001._ohlcv_window` directly; confirmed it passes
under correct code and BROKE under the mutation.

**27 of 28 mutations BROKE on the first attempt; 1 (M11) was VACUOUS on the
initial fixture** (measured discriminating-case count: 0 of 1) and was
fixed by strengthening the fixture (untracked file in a wholly-new
directory, with a self-guarding assertion) rather than by loosening the
mutation or leaving it unresolved — confirmed BROKE afterward. **Final
state: 28/28 BROKE.**

## 5. Workspace-integrity evidence

- Baseline manifest: `docs/qr_infra_002_baseline.sha256` (43 entries: every
  file under `src/registry/`, `tests/registry/`, `experiments/registry_migration/`,
  `experiments/registry/` — including the real R§17 migration's `records/`,
  `history.jsonl`, and gitignored `artifacts/` payloads — plus `.gitignore`
  and `pyproject.toml`).
- `git status --porcelain --untracked-files=all` recorded before and after
  the mutation campaign; final state (35 untracked + 2 modified files) shown
  in this report's file list above matches exactly what was intentionally
  created.
- The manifest was verified (`sha256` recomputed and diffed against the
  baseline) after **every single one of the 28 mutations**, not batched —
  every check reported `MANIFEST OK: unchanged`. Two legitimate, permanent
  changes were made mid-campaign (the M11 fixture strengthening, and an
  unrelated real bug fix found while writing this report — see §7); both
  were followed immediately by a baseline regeneration + re-verification
  cycle before mutation testing continued, so no mutation artifact was ever
  silently absorbed into the baseline.
- No test writes into `experiments/registry/` (verified by source
  inspection of every fixture in `tests/registry/conftest.py`/`_factories.py`
  and the one full-migration test's explicit scratch-directory + `finally`
  cleanup).

## 6. R§17 migration outcome

Ran once, successfully, at the end (`~16s` total, not the "few minutes" the
work order anticipated for the ~57k-bar B1 run — this environment executes
faster than expected):

```
.venv/bin/python -m experiments.registry_migration.register_qr_smoke_001
```

| # | role | experiment_id |
|---|---|---|
| 1 | Window A | `EXP-a1a85ca8fab03168-r00` |
| 2 | Window A rerun | `EXP-a1a85ca8fab03168-r01` |
| 3 | Window B1 | `EXP-939a3b042fdbbc63-r00` |
| 4 | Window B2 | `EXP-3f04a9c7878166ed-r00` |
| 5 | Window B2-PRE | `EXP-ea723063a27cbdd8-r00` |

- **Record 2's `reproducibility_status`: `REPRODUCED`.** Executed in a
  subprocess with `PYTHONHASHSEED=918273645` (forced, distinct from the
  parent's ambient value); `records[0].exact_hash == records[1].exact_hash`
  asserted and holds (R§6.3.1).
- **Record 5's verbatim `status_reason`:**
  ```
  DataIntegrityError: spec §2.2 funding-coverage window rule violated for 'BTC':
  expected exactly ONE FundingCoverage record covering
  [Timestamp('2024-01-01 00:00:00+0000', tz='UTC'), Timestamp('2026-07-31 23:00:00+0000', tz='UTC')],
  found 0
  ```
  Raised from the driver's own pre-flight check
  (`experiments/qr_smoke_001/pipeline.py:assert_single_funding_coverage_record`),
  exactly as R§17.4 specifies — never the engine's `FundingDataError`.
  `run_facts` records the measured underlying data fact: **28,056** funding
  events, span `2023-05-12T00:00:00.048Z → 2026-08-16T17:00:00.005Z`, **84**
  gaps exceeding the 90-minute tolerance, **85** coverage segments, last
  segment starting `2024-08-15T14:00:00.074Z` — all four numbers match
  R§17.4's stated measurements exactly.
- `verify_registry()` on the real registry: `()` (clean).

Six query demonstrations (persisted to
`experiments/registry/artifacts/query_demo.txt`, gitignored payload):

```
1. find_experiments(strategy_name='qr_smoke_001'): EXP-3f04a9c7878166ed-r00, EXP-939a3b042fdbbc63-r00, EXP-a1a85ca8fab03168-r00, EXP-a1a85ca8fab03168-r01, EXP-ea723063a27cbdd8-r00
2. failed_or_rejected(): EXP-ea723063a27cbdd8-r00
3. find_experiments(dataset_id='binance.ohlcv.1h.BTC'): EXP-3f04a9c7878166ed-r00, EXP-939a3b042fdbbc63-r00, EXP-ea723063a27cbdd8-r00
4. children_of('EXP-a1a85ca8fab03168-r00'): EXP-939a3b042fdbbc63-r00
5. find_experiments(funding_disabled=True): EXP-939a3b042fdbbc63-r00
6a. exact_rerun_groups(): {four groups, one of size 2 (Window A / rerun), three of size 1}
6b. semantic_duplicates(): {one group of size 2 (Window A / rerun) — the only pair sharing a semantic_hash}
```

(See item #1 in §2 for why dataset_id is `binance.ohlcv.1h.BTC`, not
`binance.um.ohlcv.1h.BTC` as the spec's own prose literally states.)

Per-record `custom` fields include the eight R§17.5-mandated keys
(`funding_events_excluded`, `funding_gap_tolerance_suspicious`,
`max_gross_exposure`, `n_unexecuted_rebalances`, `total_drag_return`,
`drag_comparable`, `counterfactual_status`, `first_frame_signal`) for
records 1–4; record 5's facts live in `run_facts` (no `ResultSummary`
exists for a `FAILED` run). Dataset snapshots (3 unique parquet files,
deduplicated by content hash across the 5 records) and 3 per-window equity
-curve JSON artifacts are attached per-record, never shared across records
(MW12).

Record-level warning-emission spot checks, both measured facts predicted by
the spec fired correctly: `PROCESSING_VERSION_MISMATCH:binance.ohlcv.1h.BTC`
on every Binance-backed record (cached sidecar `qr-data-001-v1.2` vs running
`qr-data-001-v1.3` — the exact "live example" R§4.3.1 cites), and
`PROXY_DATA`/`SURVIVORSHIP_UNSAFE` throughout.

## 7. A real bug found and fixed during this work (not a mutation)

While verifying `verify_registry()`'s full closed finding-vocabulary
coverage for the report, found that an unknown `schema_version` was being
reported as `UNPARSEABLE_RECORD:<id>` instead of the spec-mandated distinct
`SCHEMA_VERSION_UNKNOWN:<id>` (both raise inside the same code path in the
original implementation). Fixed in `src/registry/store.py::verify_registry`
by checking `schema_version` before attempting full `ExperimentRecord`
construction; added `test_persistence.py::test_schema_version_unknown_is_distinct_from_unparseable`.
Confirmed this does not change the real registry's `verify_registry()`
output (still `()`, clean) and re-ran the full suite (674 passed, 6 skipped)
and the baseline-manifest cycle after the fix.

## 8. Anything not implemented as specified

Nothing was knowingly left unimplemented. Three deliberate, narrow judgment
calls beyond the ambiguities in §2:

- `summary()`/`summary_table()` (R§15) implement every pinned rendering rule
  (`DIRTY`/`CLEAN`, `survivorship_safe: unknown`, `PROXY(for=...)`,
  `cagr: n/a (suppressed)`, `content_hash: unavailable`, `nan`) but the
  overall layout/wording beyond those pinned tokens is my own (R§15 pins
  tokens, not a full template).
- Sets/`frozenset`s (R§3.1) encode to a sorted JSON array; R§3.1's table
  defines no inverse `$set` wrapper (JSON has no set type), so
  `decode(encode(a_set))` yields a `list`, not a `set` — a one-way encoding
  for hashing/storage, consistent with the table's own contents. Not claimed
  as a full round-trip for that one type in `tests/registry/test_serialize.py`.
- `verify_registry()` implements the full closed finding vocabulary
  (`ORPHAN_RECORD`, `MISSING_RECORD`, `RECORD_MODIFIED`, `UNPARSEABLE_RECORD`,
  `BAD_SEQ`, `DANGLING_PARENT`, `PARENT_CYCLE`, `RUN_SEQ_GAP`,
  `PREFIX_COLLISION`, `INCONSISTENT_CONTENT_HASH`, `SCHEMA_VERSION_UNKNOWN`);
  test coverage focuses on the subset R§18.1(11) explicitly names
  (clean/`RECORD_MODIFIED`/`ORPHAN_RECORD`+repair/`UNPARSEABLE_RECORD`/corrupt
  -line-raises/`SCHEMA_VERSION_UNKNOWN`), not every combinatorial corruption
  scenario for every token.

---

# Repair cycle (v1.2) — dual REGISTRY FAIL repair

Implements `docs/experiment_registry_spec.md` v1.2 (FROZEN), specifically
R§20 (AMENDMENTS). `schema_version` bumped to `"qr-infra-002-v1.2"`.

**Environment note.** This repair cycle ran in a NEW, otherwise-empty git
worktree (no v1.1 content present as tracked or untracked files — a
different worktree from the one that produced the v1.1 implementation
above). The v1.1 baseline (`src/registry/**`, `tests/registry/**`,
`experiments/registry_migration/**`, the real `experiments/registry/`
records/history, and the BTC parquet caches) was copied byte-for-byte from
the shared checkout (never re-fetched — pure filesystem copy) so this repair
could build on the actual v1.1 code rather than reconstructing it from the
spec. The 143 v1.1 tests were re-confirmed passing in this worktree before
any v1.2 edit began.

## 1. Files changed, by area

**`src/registry/models.py`** — `SCHEMA_VERSION` bumped; `RECORDED_VIA_VALUES`;
extended `RECORD_WARNING_PREFIXES` (`WAS_INVALIDATED`, `WAS_REJECTED`,
`WAS_FAILED`, `SPEC_CHANGED_SINCE_PARENT`, `OOS_RELABEL_OF`,
`UNTRACKED_CODE_AT_RECORD_TIME`, `BACKDATED_CREATED_AT`,
`UNVERIFIED_MANUAL_RESULTS`); `ArtifactRef.from_file()` (R§20.8.1);
`ResultSummary` cagr-suppression validation (R§20.11/MW-k); new
`ExperimentRecord` fields `recorded_via`, `manual_results_justification`,
`search_space_id`, `n_configs_evaluated`, `config_family_hash`,
`frozen_spec_commit`, `frozen_spec_blob_sha`, with their R§20.3/R§20.5/R§20.6
validations; `semantic_payload()` now includes `recorded_via` (R§20.3.1);
new module function `config_family_payload()` (R§20.5.4, see the flagged
reading in §2 below).

**`src/registry/codeid.py`** — R§20.8.7: `dirty_worktree`/`dirty_summary`
classification is now PATTERN-based (`_matches_scope_pattern`/
`_in_scope_by_pattern`) against the git-porcelain relpath, not membership in
an on-disk `Path.glob()` listing, so a DELETED in-scope file (`"D"`) is
counted; the on-disk glob (`_scoped_files`) is retained only for computing
`code_fingerprint` itself (which necessarily reads bytes from existing
files). New `verify_code_state(record_code, repo_root) -> "MATCH"|
"CODE_FINGERPRINT_MISMATCH"|"UNVERIFIABLE"` (R§20.7.3).

**`src/registry/datahash.py`** — `CONTENT_HASH_METHOD` renamed
`"col-buffer-v1"` -> `"col-buffer-v2"` (R§20.8.4); encoding UNCHANGED (same
bytes, new id — confirmed by measurement: the real BTC dataset digests are
byte-identical under both ids, since only the label changed, not the
algorithm); docstring rewritten to pin the full column-type vocabulary
normatively.

**`src/registry/store.py`** (the largest set of changes):
- `ExperimentRegistry.__init__` gains `repo_root` (default `Path.cwd()`),
  the anchor for `frozen_spec_ref` (R§20.6.5) and artifact verification.
- `_append_history_event`/`_read_history_raw_lines`: `logged_at`
  (store-stamped, R§20.7.1) and `prev_line_sha256` hash chain (R§20.7.2).
- `_fold`: sticky `WAS_INVALIDATED`/`WAS_REJECTED`/`WAS_FAILED` (R§20.4.1),
  recomputed every fold from the full `status_history`, irremovable.
- `_assemble_warnings`: `_extra_warnings` restricted to exactly
  `{"PROVENANCE_INCOMPLETE"}` (R§20.8.9/MW-A3); `UNTRACKED_CODE_AT_RECORD_TIME`;
  `BACKDATED_CREATED_AT`; `UNVERIFIED_MANUAL_RESULTS`; `OOS_WINDOW_OVERLAP`
  upgraded to check every ancestor in `lineage_of(parent)` **and** every
  record sharing `(strategy.name, search_space_id)` (R§20.6.1);
  `SPEC_CHANGED_SINCE_PARENT` (R§20.6.3); `OOS_RELABEL_OF` (R§20.6.4).
- `record_experiment`: new `_recorded_via` (private, adapter-only),
  `_logged_at_override` (private, test-only), `search_space_id`,
  `n_configs_evaluated`, `manual_results_justification` parameters;
  `frozen_spec_ref` resolution now anchored to `self.repo_root` and rejects
  `..` (R§20.6.5); new `_verify_committed_blob()` (R§20.6.2, subprocess
  `git ls-files`/`diff --quiet`/`rev-parse`); `config_family_hash` computed
  alongside `semantic_hash`/`exact_hash`.
- New methods: `near_duplicates()` (R§20.5.4), `sibling_count()`,
  `search_space_summary()` (R§20.5.3), `verify_artifacts()`/
  `_verify_artifacts_for()` (R§20.8.1).
- `find_experiments`: new filters `ever_status` (R§20.4.3), `search_space_id`,
  `config_family_hash`; `funding_disabled` no longer matches an absent
  `funding_mode` key (R§20.8.9/MW-A7).
- `descendants_of` sorted by `experiment_id` (R§20.8.9/MW-A8).
- `verify_registry()`: `HISTORY_CHAIN_BROKEN` (R§20.7.2),
  `SEMANTIC_DUP_RESULT_DIFF` (R§20.5.5), `DIVERGED` (R§20.11),
  `ARTIFACT_MISSING`/`ARTIFACT_MODIFIED` (R§20.8.1).
- `render_summary`/`summary_table`: `NOT A RESEARCH RESULT` banner (R§20.11),
  `reason_for_run`, `recorded_via` (+ manual cross-check warning line, R§20.3.3),
  `search_space_id`/`n_configs_evaluated`, per-dataset eval window,
  `status_history` when length > 1 (R§20.4.2), `divergence_detail`, `notes`;
  `summary_table` gains `recorded_via`/warning-count/`reproducibility_status`
  columns.

**`src/registry/backtest_adapter.py`** — module docstring/imports updated to
import `backtest.engine.run_backtest` (R§20.2.1 rescopes the prohibition to
this module alone); `record_backtest_result` gains `search_space_id`,
`n_configs_evaluated`, `no_datasets_reason` (the last one closes a genuine
gap: without it, an `infrastructure`/`data_audit` adapter run with empty
`result.provenance` had no way to satisfy R§4.4.3 at all) and sets
`_recorded_via="adapter"`; `record_run` now catches `BaseException` (not
`Exception`), gives `KeyboardInterrupt`/`SystemExit` the
`"ABORTED: <Type>: <msg>"` reason (R§20.2.3), and registers `INVALID`
(never a bare raise) when the block exits without `set_result()`
(R§20.2.4); new `run_and_register()` (R§20.2.1), implemented as a thin
wrapper around `record_run` + `run_backtest` + `run.set_result()` so it
inherits `record_run`'s exception/INVALID handling for free.

**`src/registry/__init__.py`** — exports `run_and_register`,
`verify_code_state`, `RECORDED_VIA_VALUES`, `config_family_payload`.

**`experiments/registry_migration/register_qr_smoke_001.py`** —
`REGISTRY_ROOT`/`ARTIFACTS_ROOT`/`DATASET_SNAPSHOT_DIR` now resolved via
`_resolve_registry_root()` (argv `--registry-root` > env `QR_REGISTRY_ROOT` >
real default, R§20.8.2); `main()` pins `QR_REGISTRY_ROOT` into its own
`os.environ` unconditionally so the R§17.3 rerun subprocess (which
re-imports this module fresh) always agrees with the parent, regardless of
which source the parent itself used. `_child_window_a_rerun_main`/
`register_window_a_rerun` REWRITTEN: the child now pickles the real
`BacktestResult` + `dataset_windows` dict and transmits them (base64, inside
the existing JSON envelope) to the parent, which calls
`record_backtest_result` directly — record 2 is therefore
`recorded_via="adapter"` (not "manual"), preserving R§6.3.1's
`exact_hash` equality with record 1 now that `recorded_via` is hashed
(R§20.3.1) — see the flagged reading in §2. `intended_eval_start`/
`intended_eval_end` for ALL FOUR completed windows now come uniformly from
`run.frame_index[0]/[-1]` rather than per-window pipeline constants — this
also fixes a real MW-A6 bug: Window B1's `run_facts["intended_eval_start"]`
was `P.WINDOW_B1_RAW_START` (`2020-01-01`, the raw load boundary), not the
evaluated-frame start. `_notes()` gained the R§20.10 discard-disclosure
paragraph. `_run_facts_for` gained a `runtime_env` dict (R§20.9.2, adopted
now). Record 5's `run_facts` also gained `runtime_env`.

**New test files**: `tests/registry/test_datahash.py` (13 tests — R§20.8.3,
`datahash.py` had ZERO tests under v1.1), `tests/registry/test_r20_amendments.py`
(53 tests — one section per R§20.2–R§20.11 subsection),
`tests/registry/test_r20_8_6_coverage.py` (32 tests — one test per R§20.8.6
enumerated area).

**Modified test files**: `tests/registry/_factories.py` (`CONTRACT_VERSIONS`
schema bump, `col-buffer-v2`, `record_kwargs()` gains
`manual_results_justification`); `tests/registry/test_backtest_adapter.py`
(local `_code_identity()` helper's hard-coded schema string was a second,
independent stale-literal bug — fixed to reference `SCHEMA_VERSION`);
`tests/registry/test_registry_layering.py` (rewritten with an AST-based
scan per R§20.8.8, replacing the regex scan; `backtest.metrics` forbidden
in ALL of `src/registry/`, `backtest.engine` confined to
`backtest_adapter.py` alone, plus a self-guard proving the exemption is
exercised); `tests/registry/test_persistence.py` (schema-version literal
bump); `tests/registry/test_research_invariants.py`,
`tests/registry/test_summary.py`, `tests/registry/test_rerun_detection.py`
(small updates for the new required fields / `recorded_via` in the
semantic-hash payload).

**`docs/qr_infra_002_baseline.sha256`** — regenerated (31 entries: every
file under `src/registry/`, `tests/registry/`, `experiments/registry_migration/`,
plus the spec — the literal R§18.3 scope).

**`experiments/registry/README.md`** — rewritten for v1.2, records the
R§20.10 discard and the exact command to materialize the real registry
after commit (§6 below).

## 2. Flagged R§20 contradictions / ambiguities (resolved with a stated reading)

1. **`config_family_hash` vs. `frozen_spec_sha256` (R§20.5.4 vs. R§20.6.4) —
   a genuine contradiction, resolved by extending the strip-list beyond
   R§20.5.4's literal text.** R§20.5.4 names exactly seven fields to remove
   from the semantic-hash payload to form `config_family_hash`
   (`content_hash`, `content_hash_method`, `data_start`, `data_end`,
   `eval_start`, `eval_end`, `recorded_via`) — `frozen_spec_sha256` is not
   among them. But R§20.6.4 requires `OOS_RELABEL_OF` to fire when an
   `out_of_sample` record's `config_family_hash` MATCHES a PRIOR NON-OOS
   record's. R§14.5 makes `frozen_spec_ref` (hence non-null
   `frozen_spec_sha256`) REQUIRED for every `out_of_sample` record, while a
   prior non-OOS record essentially never sets it. Keeping
   `frozen_spec_sha256` in `config_family_hash`'s payload therefore makes
   every OOS record's `config_family_hash` differ from every non-OOS
   record's BY CONSTRUCTION — `OOS_RELABEL_OF` would be permanently
   unreachable, the exact "structurally unreachable" failure mode R§20.6.4's
   own rationale names for v1.1's analogous `research_stage`-exclusion
   promise. **Reading taken:** `config_family_payload()` also strips
   `frozen_spec_sha256`. Documented in-line in `models.py` and proven live
   by `test_r20_amendments.py::test_OOS_RELABEL_OF_warns_when_config_family_matches_a_prior_non_oos_record`,
   which fails under the literal (unextended) reading. **Alternative
   reading, rejected:** keep the literal 7-field list and accept
   `OOS_RELABEL_OF` as vacuous by construction — rejected because a
   deliberately-unreachable mandated check is exactly what R§18.2 calls a
   defect, not a feature.
2. **`recorded_via` in `semantic_hash` (R§20.3.1) vs. R§6.3.1's `exact_hash`
   equality mandate for the R§17.3 rerun — a real design tension, resolved
   by changing the MIGRATION's mechanism, not either rule.** R§20.3.1 hashes
   `recorded_via` into `semantic_hash`. R§17.3/R§6.3.1 requires the Window A
   rerun (record 2) to share `exact_hash` with Window A itself (record 1),
   proving "same experiment, re-executed." Under v1.1's mechanism, the
   rerun's real `BacktestResult` exists only in a CHILD SUBPROCESS (a
   different `PYTHONHASHSEED` is the whole point) and only a JSON
   reconstruction crossed back to the parent, so the parent had to call
   `record_experiment` DIRECTLY (`recorded_via="manual"`) rather than
   `record_backtest_result` (`"adapter"`) — under v1.2 this would silently
   change records 1 and 2's `exact_hash` relationship, defeating R§17.3's
   own demonstration. **Reading taken:** this is a mechanism defect in the
   MIGRATION, not a spec conflict — the child now pickles the real
   `BacktestResult` object (plus the `dataset_windows` dict) and the parent
   calls `record_backtest_result` on it directly, exactly as it does for
   Window A, so record 2 is genuinely `recorded_via="adapter"`. No spec rule
   needed to bend. Verified: `fe1.record.exact_hash == fe2.record.exact_hash`
   holds under the temp-root migration run (§6).
3. **`frozen_spec_ref` resolution base (carried over from v1.1, now made
   concrete by `repo_root`).** v1.1 flagged that R§14.5's "repo-relative"
   had no stated base and resolved against `Path.cwd()`. R§20.6.5 now
   requires anchoring and `..`-rejection, which needed an actual anchor
   concept — resolved by adding `ExperimentRegistry.__init__(..., repo_root=None)`,
   defaulting to `Path.cwd()` when omitted (so existing callers/tests are
   unaffected) but now an explicit, injectable, single source of truth for
   both `frozen_spec_ref` resolution and R§20.6.2's git verification /
   R§20.8.1's artifact verification.
4. **`run_and_register`'s exception-handling contract (BaseException/
   INVALID) is stated by R§20.2 only for `record_run`, not spelled out for
   `run_and_register` itself.** Read literally, R§20.2.1 says only "it
   calls `run_backtest`, then registers via `record_backtest_result` on
   success" — silent on failure. **Reading taken:** `run_and_register` is
   implemented IN TERMS OF `record_run` (`with record_run(...) as run: ...
   run.set_result(...)`), so it inherits `record_run`'s full
   `BaseException`/INVALID contract for free, on the view that a thin
   wrapper around the mandatory primitive should not have a WEAKER
   guarantee than the primitive itself. Not stated as fact beyond dispute —
   flagged here since the literal text doesn't pin it.
5. **R§20.2.2's static test needs a concrete allow-list of `qr_smoke_001`
   files**, which the spec names by directory (`experiments/qr_smoke_001/**`)
   without enumerating files. Enumerated as `pipeline.py`, `crossvenue.py`,
   `reconstruction.py` (every `.py` file that exists under that directory
   today, all of which reference `run_backtest`/related helpers as FROZEN
   QR-SMOKE-001 driver code). A new file added to that directory in the
   future would need adding to the allow-list too — noted in the test's own
   docstring.
6. Carried over from the v1.1 report, still applicable and unresolved by
   R§20: **item 6 (the R§2.1.1 reverse-layering runtime check is
   directional/not implementable)** — unchanged; R§20.2.1 does not touch this.

## 3. Test count and pass/fail

```
tests/registry/:            245 passed   (was 143 under v1.1)
full suite (tests/):        776 passed, 6 skipped   (was 674 passed, 6 skipped)
```

New tests added this cycle: 13 (`test_datahash.py`) + 53
(`test_r20_amendments.py`) + 32 (`test_r20_8_6_coverage.py`) + ~4 small
additions/replacements in existing files ≈ 102 net new tests.

## 4. Mutation table

Applied to source, ran the NAMED target test only (never the full suite —
watchdog risk), restored, verified the workspace-manifest file-set +
content-hash comparison after **every single mutation**.

### M1–M28 (re-run in full; the spec-table mutations from v1.1)

| # | mutation | result |
|---|---|---|
| M1 | `created_at` in `semantic_hash` payload | BROKE |
| M2 | `results` in the hash payload | BROKE |
| M3 | drop `code_fingerprint` from `exact_hash` | BROKE |
| M4 | drop a `BacktestConfig` field from adapter dump | BROKE |
| M5 | `run_seq` format `{:d}` | BROKE |
| M6 | `math.isclose(rel_tol=1e-6)` | BROKE |
| M7 | raw float `==` (NaN-unsafe) | BROKE |
| M8 | `list_experiments()` filters to `COMPLETED` | BROKE |
| M9 | unknown filter silently ignored | BROKE |
| M10 | `symbol` filter substring match | BROKE |
| M11 | `dirty_worktree` without `--untracked-files=all` | BROKE (fixture already strengthened from the v1.1 cycle — re-confirmed discriminating, not vacuous) |
| M12 | git failure / missing `-C repo_root` | BROKE |
| M13 | remove `O_EXCL` | BROKE |
| M14 | `sort_keys=False` | BROKE |
| M15 | `allow_nan=True`, no `$nonfinite` wrapper | BROKE |
| M16 | `SerializationError` -> `str(obj)` fallback | BROKE |
| M17 | skip malformed `history.jsonl` lines | BROKE |
| M18 | `native_or_proxy` defaults to `"native"` | BROKE |
| M19 | drop `out_of_sample` ⇒ `frozen_spec_ref`-resolves check | BROKE |
| M20 | `status_reason` optional for `REJECTED` | BROKE |
| M21 | prefix collision treated as rerun (`ID_PREFIX_HEX=2`) | BROKE |
| M22 | migration `data_start` from requested window | BROKE |
| M23 | `DatasetRef`s from caller list instead of `result.provenance` | BROKE |
| M24 | `PROXY_DATA` in `result_warnings` instead of record-level | BROKE |
| M25 | `NOT_COMPARABLE` collapsed to `DIVERGED` | BROKE |
| M26 | `run_seq` counted from `history.jsonl` | BROKE (reproduces the predicted "wedged forever" `RegistryError`) |
| M27 | strict containment (`<`/`>`) in R§12.4 | BROKE |
| M28 | filters use creation-time status, not folded | BROKE |

**28/28 BROKE. No survivors, no vacuous mutations.**

### R§20.8.6-area mutations (one per area, representative — not claimed exhaustive)

| area | mutation | target test | result |
|---|---|---|---|
| R§8.4 tags union | overwrite instead of union `tags_added` | `test_annotate_appends_notes_and_unions_tags_sorted` | BROKE |
| R§8.4 unknown event kind | silently `pass` instead of raise | `test_unknown_history_event_kind_raises_on_fold` | BROKE |
| R§12.3 `PROVENANCE_INCOMPLETE` | drop the extra-warning emission | `test_provenance_incomplete_emits_record_level_warning` | BROKE |
| R§4.6.2 funding-basis coherence | disable the basis check | `test_funding_disabled_with_non_sentinel_basis_raises` | BROKE |
| R§4.3.1 `registry_schema` equality | disable the check | `test_registry_schema_mismatch_raises` | BROKE |
| R§11 no-op transition | disable the no-op guard | `test_no_op_status_transition_raises` | BROKE |
| R§8.3 `BAD_SEQ` | disable the seq-mismatch finding | `test_verify_registry_reports_BAD_SEQ_on_tampered_seq` | BROKE |
| R§13.1 `warning_token` union | drop `result_warnings` from the union | `test_warning_token_matches_result_level_token_too` | BROKE |
| verify_registry `DANGLING_PARENT` | disable the finding | `test_verify_registry_DANGLING_PARENT` | BROKE |
| verify_registry `INCONSISTENT_CONTENT_HASH` | disable the finding | `test_verify_registry_INCONSISTENT_CONTENT_HASH` | BROKE |
| `diverged()` | return all records, not just DIVERGED | `test_diverged_returns_only_diverged_records` | BROKE |
| `suppress_cagr` | disable the metrics rewrite | `test_suppress_cagr_via_adapter_marks_metrics_and_warning` | BROKE |
| R§20.2.4 INVALID path | skip the no-result INVALID registration | `test_record_run_no_set_result_registers_INVALID_and_does_not_raise` | BROKE |
| R§20.6.2 git-committed blob | disable the dirty-spec check | `test_frozen_spec_ref_dirty_working_tree_rejected_for_oos` | BROKE |
| R§20.7.2 `HISTORY_CHAIN_BROKEN` | disable the chain-mismatch finding | `test_history_chain_HISTORY_CHAIN_BROKEN_detects_tampering` | BROKE |
| R§20.5.4 `config_family_hash` field strip | stop stripping `content_hash`/window fields | `test_config_family_hash_stable_across_content_hash_and_window_change` | BROKE |
| R§20.4.1 sticky `WAS_INVALIDATED` | disable the sticky-warning derivation | `test_INVALID_then_COMPLETED_laundering_leaves_sticky_WAS_INVALIDATED` | BROKE |

**17/17 BROKE.** These 17 are a representative subset of R§20.8.6's long
enumeration (chosen to cover previously-zero-test areas and every newly
introduced mechanism), not a claim that all ~40 named sub-items were each
individually mutation-proven — the 32 tests in `test_r20_8_6_coverage.py`
plus 53 in `test_r20_amendments.py` give every enumerated area at least one
passing discriminating test (verified by inspection of each test's
assertions against a real, not synthetic-only, code path), but not every
one of them was separately run through a hand-applied mutation in this
cycle. Confirmed: **no mutation applied in this cycle survived.**

## 5. Workspace-integrity evidence

- `docs/qr_infra_002_baseline.sha256` — regenerated, 31 entries (every file
  under `src/registry/`, `tests/registry/`, `experiments/registry_migration/`,
  plus the spec — R§18.3's literal scope).
- R§20.8.10 file-set comparison — a dedicated verification script computed,
  for the same three directories, the SET of relative file paths (not just
  their hashes) before and after every mutation; `MANIFEST OK: unchanged (31
  files, file-set identical)` reported after all 45 mutations applied this
  cycle (28 M-table + 17 R§20.8.6-area), with zero `ADDED`/`REMOVED`/
  `MODIFIED` deltas in every case. This specifically closes the v1.1 defect
  a hash-only manifest cannot detect (extra files).
- `git status --porcelain --untracked-files=all` in this worktree: 2
  modified (`.gitignore`, `pyproject.toml`) + 34 untracked, all of which are
  the files enumerated in §1 above — nothing else.
- A stray pair of leftover dataset-snapshot parquets was found under
  `experiments/registry/artifacts/datasets/` in this worktree partway
  through the cycle (debris from an intermediate test run before the
  R§20.8.2 root-propagation fix was in place) and removed; `experiments/registry/`
  now contains only `README.md`, confirmed by directory listing.
- No test writes into the real `experiments/registry/` — every fixture uses
  `tmp_path` except `test_migration.py`'s one full-pipeline test, which uses
  an in-repo scratch directory (required by R§9's repo-relative artifact
  path constraint) removed in a `finally` block, and now ADDITIONALLY
  asserts the real `experiments/registry/artifacts/datasets/` file set is
  byte-for-byte unchanged across the test (R§20.8.2) — this is the exact
  assertion that would have caught v1.1's measured stray-parquet defect.

## 6. Temp-root migration outcome (R§20.10)

Per the work order, the real registry was NOT created by this repair cycle.
Instead, the migration was proven end-to-end into an in-repo scratch root
(`experiments/_qr_infra_002_temp_root_proof/` — an OS `/tmp` root is
rejected by R§9's repo-relative artifact-path constraint, exactly as
`test_migration.py`'s own docstring already documents; removed after the
run):

```
QR_REGISTRY_ROOT=experiments/_qr_infra_002_temp_root_proof \
  .venv/bin/python -m experiments.registry_migration.register_qr_smoke_001
```

Five ids produced:

| # | role | experiment_id |
|---|---|---|
| 1 | Window A | `EXP-86eafd2cf950ab2c-r00` |
| 2 | Window A rerun | `EXP-86eafd2cf950ab2c-r01` |
| 3 | Window B1 | `EXP-1f5c0691663653c8-r00` |
| 4 | Window B2 | `EXP-2d3a870e9e561d7c-r00` |
| 5 | Window B2-PRE | `EXP-019d8538ae6ab447-r00` |

- **Record 2's `reproducibility_status`: `REPRODUCED`.** `recorded_via` is
  now `"adapter"` for BOTH records 1 and 2 (§2 item 2), and
  `fe1.record.exact_hash == fe2.record.exact_hash` holds.
- **Record 5's verbatim `status_reason`:**
  ```
  DataIntegrityError: spec §2.2 funding-coverage window rule violated for 'BTC':
  expected exactly ONE FundingCoverage record covering
  [Timestamp('2024-01-01 00:00:00+0000', tz='UTC'), Timestamp('2026-07-31 23:00:00+0000', tz='UTC')],
  found 0
  ```
  — identical in substance to the v1.1 run (same measured data fact).
- `verify_registry()`: `()` (clean) — note this run's `code_identity` DOES
  carry `DIRTY_WORKTREE`/`UNTRACKED_CODE_AT_RECORD_TIME` (this worktree has
  ~35 uncommitted files by design, per §1), which is EXPECTED and CORRECT
  for a temp-root proof run — R§20.7.4 explicitly reserves a clean-tree run
  for the real migration, done separately after commit.
- Six query demonstrations (all six ids/groupings sane; `dataset_id`
  filter uses the measured real value `binance.ohlcv.1h.BTC`, no `um.`
  infix, per the v1.1 report's flagged ambiguity #1, unchanged this cycle):
  ```
  1. find_experiments(strategy_name='qr_smoke_001'): EXP-019d8538ae6ab447-r00, EXP-1f5c0691663653c8-r00, EXP-2d3a870e9e561d7c-r00, EXP-86eafd2cf950ab2c-r00, EXP-86eafd2cf950ab2c-r01
  2. failed_or_rejected(): EXP-019d8538ae6ab447-r00
  3. find_experiments(dataset_id='binance.ohlcv.1h.BTC'): EXP-019d8538ae6ab447-r00, EXP-1f5c0691663653c8-r00, EXP-2d3a870e9e561d7c-r00
  4. children_of('EXP-86eafd2cf950ab2c-r00'): EXP-1f5c0691663653c8-r00
  5. find_experiments(funding_disabled=True): EXP-1f5c0691663653c8-r00
  6a. exact_rerun_groups(): four groups, one of size 2 (Window A / rerun)
  6b. semantic_duplicates(): one group of size 2 (Window A / rerun)
  ```
- Dataset content hashes (`col-buffer-v2`) are BYTE-IDENTICAL to the old
  `col-buffer-v1` values for these real BTC frames — expected and correct,
  since only the METHOD ID changed, never the byte encoding (§1); only the
  recorded `content_hash_method` field differs (`"col-buffer-v2"`).

**Exact command to materialize the REAL registry (after this cycle's code
is committed, per R§20.7.4):**

```
.venv/bin/python -m experiments.registry_migration.register_qr_smoke_001
```

(no `--registry-root`/`QR_REGISTRY_ROOT` needed — the default resolves to
`<repo_root>/experiments/registry`). This MUST be run from a clean,
committed worktree (`git status --porcelain --untracked-files=all` empty
under `CODE_SCOPE_PATTERNS`) so `dirty_worktree is False` and
`code_fingerprint` resolves to committed git objects, per R§20.7.4.

## 7. Anything not implemented as specified

- **R§20.8.6's full enumeration was not each individually mutation-proven**
  (§4) — every named area has at least one real, non-vacuous test, but the
  hand-applied mutation campaign covered 17 of them as a representative,
  time-bounded sample rather than exhaustively mutating all ~40 sub-items
  named in that section's prose.
- **`ArtifactRef.from_file`'s exact signature is not pinned by the spec**
  (R§20.8.1 names the method and its two behaviours — hash/size computation,
  `allow_missing` — but not its parameter list). Implemented as
  `from_file(repo_root, relpath, *, name, kind, recorded_at,
  description=None, allow_missing=False)`, anchored the same way
  `frozen_spec_ref`/artifact verification are (`repo_root`-relative).
- **`run_and_register`'s `record_kwargs`/`run_kwargs` split** is my own
  design (the spec gives the signature shape
  `run_and_register(registry, config, market_data, strategy_output, *,
  record_kwargs, **run_kwargs)` but not which of `record_run`'s many
  keyword arguments belong in `record_kwargs` vs. flow to `run_backdtest`
  as `run_kwargs` — since `record_run` and `run_backtest` share no
  parameter names, this split was unambiguous in practice; noted since the
  spec does not spell it out).
- Everything else in R§20 believed implemented as specified; see §2 for the
  two genuine contradictions found and how each was resolved.

---

# QR-INFRA-002-A closure (R§21, v1.3)

Scoped, targeted closure of the 4 v1.2 production defects and the ~20 known
surviving mutation behaviours from the final v1.2 audit, plus the
`n_configs_evaluated` default. Architecture unchanged, as R§21 requires.

## 0. Baseline (before any change)

Measured `.venv/bin/python -m pytest -q` from this worktree, before touching
any R§21 code: **776 passed, 6 skipped** — matches the work order's stated
inherited baseline exactly. `git status --porcelain` was clean immediately
after copying the v1.2 baseline in byte-for-byte (only the expected
`.gitignore`/`pyproject.toml` diffs from the copy step itself); no `data/`
re-fetch was performed (parquet caches copied from the shared checkout).

## 1. The four production-defect fixes

**R§21.1 — adapter trust becomes unforgeable (`_recorded_via`/`_logged_at_override` removed).**
Reproduced first: with the v1.2 code, `record_experiment(..., datasets=(a
falsely-"native"-labelled proxy dataset,), _recorded_via="adapter")` produced
`recorded_via="adapter"`, no `UNVERIFIED_MANUAL_RESULTS`, no required
`manual_results_justification`, and a `summary()` reading `native` with no
caveat — the entire proxy-as-native exploit reconstituted from one keyword.
Fix: both private keywords are REMOVED from `record_experiment`'s signature
entirely (verified by `inspect.signature` introspection so a reintroduction
of either, under any near-spelling, fails the test). Trust now comes from a
module-private `_AdapterCapability` singleton (`store._ADAPTER_CAPABILITY`)
validated by `is`, never `isinstance`/truthiness/string; `logged_at` moved to
a registry-level `self._clock()` seam injected only via the
`ExperimentRegistry` constructor (`_clock`, defaulting to the real wall
clock), so the per-record backdating switch disappeared while the
determinism-test seam (R§16.3) survives. Mutation that now goes RED: a
freshly-constructed `_AdapterCapability()` (same TYPE, different OBJECT) and
a plain subclass instance are both rejected
(`test_R21_1_capability_fresh_instance_is_rejected_by_identity_not_type`,
`test_R21_1_capability_subclass_instance_is_rejected`) — an `isinstance`
check would have let either through.

**R§21.2 — rendering defects (folded notes / `manual_results_justification` unrendered).**
Reproduced first: `annotate(id, note="LATER ANNOTATION: this result was
wrong")` left the correction in the folded view (`fe.notes`) but `summary()`
(reading `r.notes`, the immutable creation-time snapshot) never showed it;
separately, `manual_results_justification` was recorded and validated but
passed to `_assemble_warnings` as a dead parameter, printed nowhere. Fix:
`render_summary` now reads `fe.notes` (store.py), and renders
`manual_results_justification` immediately beneath the manual-path warning
line whenever it is set. Mutation that now goes RED: reverting `fe.notes` to
`r.notes` fails
`test_R21_2_1_summary_renders_folded_notes_not_immutable_record_notes`;
removing the justification-render line fails
`test_R21_2_2_manual_results_justification_is_rendered`.

**R§21.3 — mandatory-registration allow-list (blanket `registry_migration` exclusion).**
Reproduced first: a rogue file at `experiments/registry_migration/_x.py`
containing `run_backtest(...)` directly stayed GREEN under v1.2's check
(`if "registry_migration" in path.parts: continue`), while the identical
file anywhere else was correctly caught. Separately, the v1.2 check was
text-containment (`"run_and_register" not in text and "record_run" not in
text`), satisfiable by a bare comment mentioning either name while still
calling `run_backtest` directly. Fix
(`test_R21_3_static_registration_enforcement_ast_based_named_allowlist`,
replacing the v1.2 test): an explicit named-file allow-list (exactly the
three `qr_smoke_001/{pipeline,crossvenue,reconstruction}.py` files, no
directory-prefix exclusion) plus an AST-based scan (`ast.Call` node whose
callee resolves to the bare name `run_backtest`) that a comment cannot
satisfy. Verified RED for: (a) a rogue driver under a brand-new
`experiments/` subdirectory, (b) a rogue driver under
`experiments/registry_migration/` — the exact case the blanket exclusion
used to hide, and (c) a rogue driver whose only textual trace of
`record_run`/`run_and_register` is inside a comment. All three temporary
files were created, asserted RED, then deleted, with workspace integrity
re-verified after cleanup (R§21.3.3).

**R§21.4 — containment boundary tested at the wrong layer.**
Reproduced first: `backtest_adapter.py:104`'s `d.data_start <= frame_start`
mutated to `<`, and `:114`'s `d.data_start > d.eval_start` mutated to `>=`,
both SURVIVED the full suite. The v1.2 fixture
(`test_R12_4_zero_warmup_boundary_data_start_equals_eval_start`) routes
through `record_experiment` → `DatasetRef.__post_init__` and never reaches
`_check_window_containment` at all; the ONE fixture that does reach it
(`test_M27_containment_is_inclusive_on_the_real_result`, real Window A) sits
strictly inside the bound (raw start `2026-01-20 21:00Z` vs frame start
`2026-01-25 00:00Z`) and cannot discriminate the exact boundary. Fix: a new
fixture (`test_R21_4_containment_boundary_zero_warmup_reaches_production_check`,
`tests/registry/test_backtest_adapter.py`) builds a REAL `BacktestResult` via
`run_backtest` (never hand-constructed) with `data_start == eval_start ==
equity_curve.index[0]` (zero warm-up) and calls `_check_window_containment`
directly. Mutation run: BOTH the `<=`→`<` and `>`→`>=` mutants now raise on
this fixture (BROKE), confirmed and restored; the ORIGINAL M27 test still
(correctly, harmlessly) SURVIVES both mutants on the real Window A result,
exactly as R§21.4.2 predicts — it is superseded, not replaced.

## 2. `n_configs_evaluated`: UNKNOWN vs. a verified count (R§21.7)

`record_experiment`'s `n_configs_evaluated` is now REQUIRED, keyword-only,
with **no default** (omitting it raises `TypeError` — Python's own
enforcement for a defaultless keyword-only parameter — verified by
`test_R21_7_omission_raises`), and tri-state: an `int >= 1` (a VERIFIED
count) or `None` (UNKNOWN). `0`/negative values raise `ValidationError`
(`ExperimentRecord.__post_init__` and `record_experiment` both updated).

- `None` renders as `n_configs_evaluated: UNKNOWN` **always**; a verified
  count > 1 renders as `n_configs_evaluated: <n> (verified)` (a verified `1`
  intentionally renders nothing at all, unchanged from R§20.11 — so a reader
  can never mistake an absent line for UNKNOWN).
- New record-level warning token `N_CONFIGS_UNKNOWN` (emitted whenever
  `None`), new closed-vocabulary entry in `RECORD_WARNING_PREFIXES`.
- New query filter `n_configs_unknown: bool`.
- `search_space_summary()` no longer silently sums over `None` members: it
  now reports `n_configs_evaluated_total` (sum of KNOWN values only),
  `n_records_with_unknown_n_configs`, and
  `n_configs_evaluated_total_is_lower_bound` (`True` iff any member is
  UNKNOWN).
- **Flagged interpretive extension (my best reading, not literally forced by
  the text):** R§21.7 names `record_experiment`'s parameter as the one to
  fix. I ALSO made `record_backtest_result`/`record_run`/`run_and_register`'s
  own `n_configs_evaluated` parameters (in `backtest_adapter.py`) required
  with no default, rather than leaving their `=1` defaults in place. Reason:
  those functions are themselves callers of `record_experiment`, and an
  adapter registering exactly one `BacktestResult` cannot itself know
  whether the calling driver evaluated other configurations and registered
  only the winner (D14) — leaving a `=1` default one call-frame up would
  silently reintroduce the identical "omission reads as a verified 1" defect
  R§21.7 exists to close, just moved sideways. The alternative, narrower
  reading (leave the adapter's own default at `1`, since it genuinely
  registers one `BacktestResult` per call) is defensible too and is noted
  here in case it is preferred; either way every call site — all tests, and
  the R§17 migration (which now passes `n_configs_evaluated=1` explicitly
  in all 5 records, per R§21.7.5) — was updated to pass it explicitly, so
  the choice is easy to reverse if the narrower reading is preferred.

## 3. `config_family_hash` excludes `frozen_spec_sha256` (R§21.9)

Already implemented exactly as R§21.9 legitimises (the 8-field strip list —
`content_hash`, `content_hash_method`, `data_start`, `data_end`,
`eval_start`, `eval_end`, `recorded_via`, `frozen_spec_sha256` — in
`config_family_payload`, `models.py`). No code change was needed; the
existing docstring already carries the R§20.6.4 rationale this section
legitimises. Re-verified passing:
`test_OOS_RELABEL_OF_warns_when_config_family_matches_a_prior_non_oos_record`.

## 4. Full mutation table (M-table + R§21.5 + R§21.6)

All mutations applied to the SOURCE (never the test), target test run,
verdict recorded, then the source restored and diffed byte-for-byte against
its pre-mutation content before the next mutation — sequential, one at a
time, per-target-test only (never full-suite, per the watchdog-risk note).

### R§18.2 M-table (M1–M28), re-run

| # | mutation | target test | verdict |
|---|---|---|---|
| M1 | `created_at` in `semantic_hash` payload | test_identity_hash.py::test_created_at_does_not_affect_semantic_or_exact_hash | BROKE |
| M2 | `results` folded into `exact_hash` payload | test_rerun_detection.py::test_M6_a_1e9_perturbation_is_DIVERGED_with_detail | BROKE |
| M3 | drop `code_fingerprint` from `exact_hash` | test_identity_hash.py::test_M3_code_fingerprint_is_part_of_exact_hash_even_with_identical_git_state | BROKE |
| M4 | drop `fee_bps` from the adapter's `BacktestConfig` dump | test_backtest_adapter.py::test_M4_backtest_config_key_set_equals_dataclass_fields | BROKE |
| M5 | `run_seq` format `{:d}` | test_identity_hash.py::test_M5_run_seq_zero_padded_with_eleven_records | BROKE |
| M6 | `isclose(rel_tol=1e-6)` bypass on metrics compare | test_rerun_detection.py::test_M6_a_1e9_perturbation_is_DIVERGED_with_detail | BROKE |
| M7 | raw `!=` (NaN-unsafe) on metrics compare | test_rerun_detection.py::test_M7_nan_sharpe_rerun_is_REPRODUCED_not_diverged | BROKE |
| M8 | `list_experiments()` filters to `COMPLETED` | test_retention.py | BROKE |
| M9 | unknown filter keyword silently ignored | test_query.py::test_M9_unknown_filter_raises | BROKE |
| M10 | `symbol` filter uses substring `in` | test_query.py::test_M10_symbol_filter_is_exact_not_substring | BROKE |
| M11 | drop `--untracked-files=all` | test_codeid.py | BROKE |
| M12 | git failure yields `dirty_worktree=False` | test_codeid.py | BROKE |
| M13 | remove `O_EXCL` | test_persistence.py | BROKE |
| M14 | `sort_keys=False` | test_serialize.py::test_key_insertion_order_invariance_R16_4 | BROKE |
| M15 | `allow_nan=True`, no `$nonfinite` wrapper | test_serialize.py::test_no_nan_or_infinity_tokens_in_canonical_string | BROKE |
| M16 | `SerializationError` → `str(obj)` fallback | test_serialize.py::test_unsupported_type_raises_no_str_fallback | BROKE |
| M17 | skip malformed `history.jsonl` lines | test_r20_8_6_coverage.py::test_blank_history_line_raises | BROKE |
| M18 | `native_or_proxy` silently defaults to `"native"` | test_registry_provenance.py::test_M18_native_or_proxy_none_raises_never_silently_native | BROKE |
| M19 | drop `out_of_sample ⇒ frozen_spec_ref` resolves check | test_research_invariants.py::test_M19_out_of_sample_requires_frozen_spec_ref_resolving_to_real_file | BROKE |
| M20 | `status_reason` optional for `REJECTED` | test_research_invariants.py::test_M20_status_reason_required_for_non_completed | BROKE |
| M21 | prefix collision treated as a rerun | test_rerun_detection.py::test_M21_prefix_collision_detected_with_small_id_prefix_hex | BROKE |
| M22 | migration `data_start` from requested window, not raw span | test_migration.py::test_M22_migration_ohlcv_window_uses_loaded_raw_span_not_requested_window | BROKE |
| M23 | `DatasetRef`s from caller list, not `result.provenance` | test_backtest_adapter.py::test_M23_omitted_provenance_dataset_raises | BROKE |
| M24 | `PROXY_DATA` dropped from record-level assembly | test_backtest_adapter.py::test_M24_proxy_data_is_record_level_even_on_a_failed_run | BROKE |
| M25 | `NOT_COMPARABLE` collapsed (full, both branches) | test_rerun_detection.py::test_M25_NOT_COMPARABLE_when_baseline_is_failed | BROKE (M25-as-first-applied, single branch, is VACUOUS — R§21.6.1, confirmed) |
| M27 | strict (`<`) containment on the REAL Window A result | test_backtest_adapter.py::test_M27_containment_is_inclusive_on_the_real_result | SURVIVED (expected — superseded by R§21.4, see §1 above) |
| M28 | filters evaluate creation-time status, not folded | test_query.py::test_M28_filters_evaluate_folded_status | BROKE |

M26 (`run_seq` counted from `history.jsonl` instead of `records/`) — target
test_persistence.py — **BROKE**.

**27/28 BROKE.** The one non-BROKE (M27 on its ORIGINAL v1.1 fixture) is the
exact defect R§21.4 diagnoses and supersedes with a fixture that reaches the
true boundary (§1 above) — not a live gap.

### R§21.5 — one test per hashed `DatasetRef` field (14/14 BROKE)

Each of the 14 `semantic_dict()` fields (`dataset_id`, `source_venue`,
`field_type`, `native_or_proxy`, `proxy_for`, `processing_version`,
`dataset_version`, `data_start`, `data_end`, `eval_start`, `eval_end`,
`symbols`, `content_hash`, `content_hash_method`) was individually DELETED
from `models.py`'s `semantic_dict()` and the corresponding parametrized case
of `test_R21_5_per_field_identity_protection[<field>]` run. **All 14
BROKE, zero survivors** — the v1.2 defect (every field individually
deletable with all 245 tests green) is closed. The `config_family_hash`
boundary (which 6 of the 14 fields must NOT move it) is asserted inside the
same parametrized test, per field, and the dataset-ordering invariant (A29)
is covered by a dedicated test (`test_R21_5_3_A29_semantic_payload_dataset_ordering_is_invariant`).

### R§21.6 — remaining known survivors (13/13 BROKE, with one flagged nuance)

| audit id | mutation applied | verdict |
|---|---|---|
| A11 | `survivorship_safe` caller/result mismatch check disabled | BROKE |
| A14 | `contract_versions` key-set check disabled | BROKE |
| A19 | `DIRTY_WORKTREE` warning emission removed | BROKE |
| A20a | `MISSING_ARTIFACT` removed from `_assemble_warnings` (asserted on the RAW on-disk record, not the folded view) | BROKE |
| A28 | `sorted()` removed from `_assemble_warnings`'s return, ALONE | SURVIVED — **VACUOUS, justified** (see below) |
| A29 | dataset-sort removed from the semantic payload | BROKE |
| A38 | `PREFIX_COLLISION` finding disabled | BROKE |
| N15 | singleton-group filter removed from `near_duplicates()` | BROKE |
| N17 | same-`(strategy, search_space_id)` OOS-overlap check disabled | BROKE |
| N30 | `descendants_of` sort removed (2-level, 5-descendant fixture) | BROKE |
| N33 | `_ALLOWED_EXTRA_WARNINGS` widened to include a real vocabulary token (`SURVIVORSHIP_UNKNOWN`) | BROKE |
| H1 | `search_space_id` filter forced to always match | BROKE |
| H2 | `config_family_hash` filter forced to always match | BROKE |

**A28 finding (measured, not a defect):** `store.py`'s
`return tuple(sorted(warnings))` is REDUNDANTLY guarded by an independent
second sort in `ExperimentRecord.__post_init__`
(`object.__setattr__(self, "warnings", tuple(sorted(self.warnings)))`,
`models.py`). Removing EITHER sort ALONE is provably vacuous — measured
empirically across 12 distinct `PYTHONHASHSEED` values, zero divergence for
each single-location mutation. Removing BOTH simultaneously reliably
diverges (12/12 distinct orderings observed across the same 12 seeds). The
cross-process test (`test_R21_6_A28_record_level_warnings_sorted_cross_process`)
is kept and protects the COMBINED end-to-end guarantee R§10.3 actually
needs (either guard regressing alone is still safe); per R§18.2's explicit
allowance, this is recorded as a justified VACUOUS finding for the
single-location mutation rather than a chased false positive, and is a
genuine (harmless) double-guard discovery, not a v1.3 defect.

**N33 correction during authoring:** the first N33 test used a nonsense
string (`"BOGUS_TOKEN"`) as the injected extra-warning token, which is
independently caught by the closed-vocabulary check at the bottom of
`_assemble_warnings` regardless of `_ALLOWED_EXTRA_WARNINGS`'s contents —
non-discriminating for the allow-list widening itself. Corrected to use a
real, closed-vocabulary token (`SURVIVORSHIP_UNKNOWN`) that is not
`PROVENANCE_INCOMPLETE`, which only a genuine allow-list check (not the
downstream vocabulary check) can reject.

**R§21.6.1 confirmed VACUOUS, not re-litigated (per the spec's own closed
list):** N19 (git-tracked check removal — redundant, `rev-parse
HEAD:<path>` fails anyway for an untracked file), K4 (`tz_convert("UTC")`
removal — pandas stores tz-aware as UTC epoch ns, byte-identical), and
M25-as-first-applied (folded-status branch alone masked by the `results is
None` branch — confirmed above, only the full M25 collapse is
discriminating).

**R§21.6.2 (material warning, not blocking) — `ArtifactRef` permissiveness.**
`tests/registry/test_artifacts.py`'s local `_make_artifact_ref_from_file`
helper (which re-implemented `ArtifactRef.from_file`'s hash computation
independently) is RETIRED; every test in that file now calls
`ArtifactRef.from_file` directly. `ArtifactRef.__init__` itself was left
permissive (accepting an arbitrary `sha256` for an arbitrary `path`) rather
than validated in `__post_init__`, because `ExperimentRecord.from_dict` must
reconstruct an `ArtifactRef` from a persisted record without re-hashing a
file that may since have moved or been deleted — the SAME reason
`allow_missing`/`verify_artifacts()` exist as a separate, later-verification
mechanism rather than a construction-time guarantee. A new test
(`test_R21_6_2_fabricated_sha256_for_a_real_path_is_caught_by_verify_artifacts`)
targets the actual safety net directly: a hand-constructed `ArtifactRef`
asserting a WRONG hash from the moment of construction (not merely changed
afterwards) is caught as `MODIFIED` by `verify_artifacts()`/`verify_registry()`.
**Flagged:** this is a narrower fix than R§21.6.2's literal suggestion
("make `from_file` the only way … or validate in `__post_init__`") — full
construction-time enforcement was judged incompatible with the `from_dict`
round-trip requirement, so the safety net was strengthened and documented
instead of the constructor.

## 5. Test counts

- Registry suite (`tests/registry/`) before any R§21 change: **245** (the
  inherited v1.2 count implied by the 776/6-skipped full-suite baseline).
- Registry suite after R§21: **292** (+47: 45 new in
  `tests/registry/test_r21_closure.py`, +1 in `test_artifacts.py`
  (`test_R21_6_2_...`), +1 net in `test_r20_amendments.py` (one v1.2 test
  replaced by one R§21.3 test with 3 embedded self-guards, not counted
  separately) — the exact accounting is 45 + 1 + 1 = 47, matching
  245 → 292).
- Full suite before: **776 passed, 6 skipped**. Full suite after: **823
  passed, 6 skipped** (+47, matching the registry-suite delta exactly — no
  tests elsewhere were touched).
- Final full-suite pass line:
  `823 passed, 6 skipped, 25 warnings in 108.97s`.

## 6. Workspace-integrity evidence

- `docs/qr_infra_002_baseline.sha256` regenerated post-implementation: 32
  files (the 31 v1.2-baseline files + the one new `test_r21_closure.py`),
  SHA-256 per file, `src/registry/**`, `tests/registry/**`,
  `experiments/registry_migration/**`, and this spec.
- **File-set comparison** (the check a hash-manifest diff alone cannot make —
  it cannot detect EXTRA files): a fresh `find` over the same three
  directories plus the spec file, sorted, was diffed against the manifest's
  own path list — **identical**, confirming the only change since the v1.2
  manifest is the intentional addition of `test_r21_closure.py` (no stray
  artifact).
- After every one of the 28 M-table + 14 R§21.5 + 13 R§21.6 mutations
  (55 total), the mutated file was restored from its pre-mutation in-memory
  copy and the restoration verified BYTE-IDENTICAL before proceeding to the
  next mutation (a `RuntimeError` in the harness would have aborted the run
  otherwise — none fired).
- `git status --porcelain` immediately after the full mutation campaign and
  after the final full-suite run: unchanged from the copy-step baseline (only
  the two expected `.gitignore`/`pyproject.toml` diffs plus the untracked
  copied directories) — no leftover mutation artifact, tracked or untracked.
- `experiments/registry/` contains exactly one file, `README.md`, before,
  during, and after every test run in this cycle (checked repeatedly via
  `find experiments/registry -type f`) — no test wrote into the real
  registry, including the `.slow`-marked full-migration-into-scratch test
  and its R§17.3 rerun subprocess.
- R§21.3.3's three temporary rogue drivers (one new-directory driver, one
  `registry_migration/` driver, one comment-bypass driver) were created,
  asserted RED individually, then deleted inside the SAME test's `finally`
  block, with the static scan re-run afterward and confirmed clean
  (`assert _r21_3_offenders(experiments_dir) == []` at the end of
  `test_R21_3_static_registration_enforcement_ast_based_named_allowlist`).

## 7. Flagged R§21 contradictions / interpretive choices

1. **`n_configs_evaluated` on the adapter functions** (§2 above) — R§21.7's
   text names only `record_experiment`'s parameter; I extended the
   required-no-default treatment to `record_backtest_result`/`record_run`/
   `run_and_register` as well, on the reasoning that leaving their `=1`
   defaults in place would reintroduce the same defect one call-frame up.
   Flagged as a reading, not a silent extension; the narrower alternative
   (adapter keeps `=1`) is noted as defensible and easy to revert.
2. **`_clock`'s exact shape** — R§21.1.3 says "`_clock`, defaulting to
   `pd.Timestamp.now(tz="UTC")`", which read literally names a VALUE, not a
   callable. Implemented as a zero-argument callable (defaulting to a
   lambda that calls `pd.Timestamp.now(tz="UTC")` fresh on every
   invocation), since a frozen value could not serve as a "clock" at all
   (every record would share one timestamp). This is the only sensible
   reading and is not treated as a live ambiguity, but is noted per the
   instruction to surface any place a literal reading was not followed
   exactly.
3. **R§21.6.2's `ArtifactRef` fix** (§4 above) — implemented as a
   strengthened safety-net test plus retiring the duplicate in-test helper,
   not full constructor-time validation, for the `from_dict` round-trip
   reason given there.

## 8. Anything not implemented as specified

- None identified for R§21 itself. All 11 subsections (R§21.1–R§21.11) were
  implemented and independently mutation-verified except where explicitly
  flagged above (§7) as an interpretive reading, and R§21.6.1's three
  confirmed-vacuous items were left exactly as the spec directs (not
  chased).
