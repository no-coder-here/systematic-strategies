# Experiment Registry — SPECIFICATION v1.2 (QR-INFRA-002)

**Status: FROZEN — 2026-08-17, Research Lead.**
`schema_version = "qr-infra-002-v1.2"`.
v1.1 was frozen after a spec audit of v1.0 returned SPEC FAIL; **v1.1's implementation was then
audited twice, independently, and BOTH returned REGISTRY FAIL.** v1.2 amends the frozen contract
to close those findings. **Read R§20 (AMENDMENTS) together with every section below — where R§20
conflicts with R§1–R§19, R§20 governs.**
Snapshots: `docs/spec_history/experiment_registry_spec_v1.0.md`, `…_v1.1.md`.
Owner: Research Lead. Normative file.
Depends on: `docs/backtest_contract.md` v1.5.1 (FROZEN), `docs/data_contract.md` v1.4 (FROZEN).

## Revision history

| ver | date | cause |
|---|---|---|
| v1.2 | 2026-08-17 | **Dual REGISTRY FAIL repair (R§20).** Code audit: 2 mandated APIs absent with inert tests standing in for them (`verify_artifacts`, artifact capture), `datahash.py` untested, a mandated test writing into the real registry, and **29 of 44 independent mutations SURVIVED**. Integrity review: `record_run` had **no production caller**, caught `Exception` not `BaseException` (Ctrl-C ⇒ no record), `INVALID → COMPLETED` laundering left no rendered trace, and proxy data could be recorded **and rendered** as native via the public manual path. Materially new: `recorded_via`, mandatory `run_and_register`, sticky `WAS_INVALIDATED`, `logged_at` wall-clock witness, `history.jsonl` hash chain, multiple-testing grouping (`search_space_id`/`n_configs_evaluated`/`near_duplicates`), `verify_code_state`, `col-buffer-v2`. |
| v1.0 | 2026-08-17 | initial draft for spec audit |
| v1.1 | 2026-08-17 | **SPEC FAIL repair.** Adjudicates BD1–BD16, MW1–MW15, and the 15 inert-test findings. Materially new: the **two-hash identity split** (R§5, semantic vs exact — v1.0's single code-inclusive hash made duplicate detection structurally useless); datasets derived **from `result.provenance`** rather than caller-declared (v1.0 let a proxy run be recorded as native); **record-level vs result-level warnings** split; raw-span **and** evaluated-frame windows both recorded and hashed; `run_facts` for failed runs; `NOT_COMPARABLE` reproducibility state; `record_run` context manager so an exception cannot leave a run unregistered; the lock file, `.tmp`/`os.replace` layer and 7 low-value query filters removed as over-engineering; the mutation table repaired (8 of 22 mutations were inert, vacuous, or mis-targeted). |

### Corrections to v1.0's own factual claims (recorded, not silently overwritten)

1. **v1.0 R§6.1 asserted that `strategies/`, `experiments/` and most of `tests/` are untracked.
   This was false** at the time of writing: commit `f7b73c2` tracks 48 files under `tests/`, 25
   under `src/`, 6 under `experiments/`, 3 under `strategies/`, 3 under `scripts/`, and
   `git status --porcelain --untracked-files=all` had 2 entries. The claim was true earlier in
   QR-SMOKE-001 and was carried forward stale. The content-fingerprint mechanism is retained, but
   on the **durability** rationale of R§6.1 below, not on a false description of the tree.
2. **v1.0 R§17.2 asserted record 5 raises `FundingDataError`. Measured: it raises
   `DataIntegrityError`** from the driver's own pre-flight check
   (`experiments/qr_smoke_001/pipeline.py:187-205`), which pre-empts the engine. See R§17.4.
3. **v1.0 R§4.4/R§17.3 used `field_type` values `ohlcv_1h` / `funding`. The real values are
   `ohlcv` / `funding_rate`** (`src/data/provenance.py:114,211`).
4. **v1.0 R§4.7 omitted `downside_dev_ann` and `calmar`**, two metrics the engine actually
   returns; and v1.0 R§5.4's "nothing is exempted" was therefore false.

---

## R§1 Scope

### R§1.1 Purpose

Every meaningful research or backtest run creates a **permanent, immutable experiment record**
answering: what was tested, why, on what data, with what configuration, from what code, with what
result, and derived from which prior experiment.

The registry exists to defeat three specific failure modes:

1. **Survivorship bias in our own research process.** A rejected strategy that disappears from
   the record is re-discovered later and treated as new. Every rejected/failed/invalid run
   therefore remains permanently visible, and the *default* listing includes it (R§13.3).
2. **Irreproducible results.** A reported number whose data window, parameters, or code state
   cannot be recovered is not a research result. Identity is content-derived (R§5), and code
   state is fingerprinted even when uncommitted (R§6).
3. **Unrecorded multiple testing.** Duplicate and near-duplicate configurations must be
   *detectable* so that "we tried this already" and "we tried 40 variants before this one" are
   answerable from the record rather than from memory. This is what the R§5 two-hash split
   exists for.

### R§1.2 Non-goals (out of scope, MUST NOT be built)

- No database (no SQLite/Postgres). Plain files only.
- No web dashboard, no server, no daemon, no scheduler, no lock manager.
- No experiment orchestration, no parameter sweeps, no optimizer, no hyperparameter search.
- No MLflow/W&B-style tracking abstraction, no plugin system, no remote artifact store.
- No mutation of the backtest engine. The registry sits **above** the engine (R§2).
- No strategy research. No change to `strategies/qr_smoke_001` behaviour.
- No schema-migration machinery (R§19 D8).

### R§1.3 Terminology

- **record** — one experiment's immutable creation snapshot (R§4).
- **event** — an append-only fact about a record, created after the record (R§8.3).
- **folded view** — record + its events applied in `seq` order (R§8.4). This is what every API
  method returns, and what every query filter evaluates against (R§13.1).
- **semantic hash** — content hash of *what was tested*, excluding code (R§5.1).
- **exact hash** — semantic hash + code state (R§5.2). Governs `experiment_id`.
- **run_seq** — ordinal of a record within its exact hash (R§5.3).

---

## R§2 Layering and module boundary

### R§2.1 Direction of dependency

```
   experiments/*  (drivers)  ──uses──▶  src/registry  ──reads──▶  src/backtest.models
                                                      ──reads──▶  src/data.provenance
   src/backtest.engine  ──MUST NOT import──▶  src/registry
   src/data            ──MUST NOT import──▶  src/registry
```

`src/registry` MAY import `src/backtest/models.py` and `src/data/provenance.py` (read-only, for
type adaptation). `src/registry` MUST NOT import `src/backtest/engine.py` or
`src/backtest/metrics.py` (R§12.2).

**R§2.1.1 (blocking, testable).** A test MUST assert that no module under `src/backtest/` or
`src/data/` contains an import of `registry` (static source scan **and** a runtime check that
`sys.modules` contains no `registry*` entry after importing `backtest.engine` in a fresh
subprocess). Rationale: coupling the engine to the registry would make every accounting test
depend on registry state, and would let a registry defect corrupt accounting.

### R§2.2 Files

| path | contents |
|---|---|
| `src/registry/__init__.py` | public API re-exports |
| `src/registry/serialize.py` | R§3 canonical serialization |
| `src/registry/models.py` | R§4 schema dataclasses + validation |
| `src/registry/codeid.py` | R§6 code identity capture |
| `src/registry/datahash.py` | R§7.3 dataset content hashing |
| `src/registry/store.py` | R§8/R§10/R§11/R§13 persistence + query |
| `src/registry/backtest_adapter.py` | R§12 `BacktestResult` → record, `record_run` |
| `experiments/registry_migration/register_qr_smoke_001.py` | R§17 |
| `tests/registry/*` | R§18 |

---

## R§3 Canonical serialization

All hashing, comparison and persistence flow through one encoder. Two output forms exist; both
consume the identical encoded value tree:

- **canonical form** — `json.dumps(tree, sort_keys=True, separators=(",", ":"),
  ensure_ascii=True, allow_nan=False)`. Used for hashing and for equality comparison.
- **stored form** — `json.dumps(tree, sort_keys=True, indent=2, ensure_ascii=True,
  allow_nan=False)` plus one trailing `"\n"`. Used for files on disk (human-readable).

`allow_nan=False` is **mandatory**, so any non-finite float that escaped the encoder raises
`ValueError` instead of emitting the non-standard `NaN` / `Infinity` tokens that other JSON
parsers reject.

### R§3.1 Type encoding (normative table)

| Python input | encoded as | notes |
|---|---|---|
| `None`, `bool`, `int`, `str` | unchanged | `bool` checked **before** `int` (`isinstance(True, int)` is `True`) |
| finite `float` | JSON number | Python's shortest round-tripping repr; **no rounding, no formatting** |
| `float('nan')` | `{"$nonfinite": "nan"}` | |
| `float('inf')` | `{"$nonfinite": "inf"}` | |
| `float('-inf')` | `{"$nonfinite": "-inf"}` | |
| `numpy` integer | `int(x)` | |
| `numpy` floating | `float(x)` then float rules | |
| `numpy.bool_` | `bool(x)` | not JSON-serializable natively |
| `pd.Timestamp` | `{"$ts": "<isoformat>"}` | MUST be tz-aware (R§3.2) |
| `datetime.date` | `{"$date": "YYYY-MM-DD"}` | |
| `pd.Timedelta` | `{"$td_ns": <int nanoseconds>}` | |
| `dict` | JSON object | every key MUST already be `str`, else `SerializationError` |
| `list`, `tuple` | JSON array | order preserved as given |
| `set`, `frozenset` | JSON array of encoded elements, **sorted by their canonical form** | R§16.2 |
| dataclass instance | its `to_dict()` if defined, else `dataclasses.asdict` then encoded | |
| anything else | `SerializationError` | **never** `str(x)` fallback |

**R§3.1.1 (blocking).** There MUST be no `str(obj)` catch-all fallback. Rationale: a silent
`str()` fallback turns an unsupported type into a stable-looking string, so a parameter object
whose `__repr__` includes a memory address (`<obj at 0x7f...>`) would produce a *different hash
on every process*, and a parameter object with a lossy `__repr__` would produce the *same* hash
for genuinely different parameters. Both defects are invisible in a passing test suite.

**R§3.1.2.** A dict key collision with the reserved wrapper keys (`$nonfinite`, `$ts`, `$date`,
`$td_ns`) at any depth MUST raise `SerializationError` on encode. Rationale: without this, a
user-supplied parameter dict `{"$ts": "hello"}` decodes into a `pd.Timestamp` and silently
corrupts the record on round-trip.

**R§3.1.3 (blocking, was inert as M15 in v1.0).** Two properties of the canonical form MUST be
asserted directly, not merely via round-trip:
1. the canonical string contains none of the tokens `NaN`, `Infinity`, `-Infinity`;
2. it parses under a **strict** parser that rejects JSON extensions
   (`json.loads(s, parse_constant=_raise)`).
Rationale, measured: `json.dumps({"x": nan})` emits `{"x": NaN}` and `json.loads` accepts it,
so a round-trip test alone passes under `allow_nan=True` with no `$nonfinite` wrapper — i.e. the
obvious test cannot fail under the defect it exists to catch.

### R§3.2 Timestamps

Every `pd.Timestamp` in a record MUST be tz-aware. A tz-naive timestamp raises
`SerializationError`. Rationale: a naive timestamp is ambiguous between UTC and local time; the
platform's data layer is UTC throughout and a silent local-time record would misstate a data
window by hours. `isoformat()` preserves nanosecond precision; decode uses `pd.Timestamp(s)` and
MUST reproduce the input exactly (round-trip test required, R§18).

### R§3.3 Round-trip requirement (blocking)

`decode(encode(x))` MUST equal `x` exactly for every supported type, where "exactly" means:
- floats: bitwise identical (`struct.pack` comparison; NaN compared via `math.isnan`);
- timestamps: equal value **and** equal `tz` **and** equal nanosecond field;
- containers: same structure, element-wise exact.

---

## R§4 Record schema (`schema_version = "qr-infra-002-v1.1"`)

`ExperimentRecord` is a frozen dataclass. **REQUIRED** means `ValidationError` if absent/empty at
construction. A reader encountering an unknown `schema_version` MUST raise, never guess.

### R§4.1 Identity

| field | type | req | notes |
|---|---|---|---|
| `schema_version` | str | auto | constant above |
| `experiment_id` | str | auto | R§5.3; caller MUST NOT supply |
| `semantic_hash` | str | auto | R§5.1, 64-hex |
| `exact_hash` | str | auto | R§5.2, 64-hex |
| `run_seq` | int | auto | R§5.3 |
| `created_at` | tz-aware ts | REQUIRED | injectable (R§16.3) |
| `run_executed_at` | tz-aware ts or None | optional | when the computation actually ran, if different from `created_at` (R§17) |
| `status` | enum | REQUIRED | R§8.1 |
| `status_reason` | str or None | conditional | REQUIRED non-empty unless `status == COMPLETED` |
| `experiment_type` | enum | REQUIRED | R§4.1.1 |

**R§4.1.1 `experiment_type`** ∈ `{"pipeline_validation", "infrastructure", "data_audit",
"alpha_research", "robustness", "replication"}`. Unknown value → `ValidationError` (a typo'd
type silently creates a new bucket that queries then miss).

### R§4.2 Research lineage

| field | type | req | notes |
|---|---|---|---|
| `hypothesis_id` | str or None | conditional | REQUIRED non-empty when `experiment_type == "alpha_research"` (R§14.6) |
| `parent_experiment_id` | str or None | optional | MUST already exist in the registry (R§14.1) |
| `reason_for_run` | str | REQUIRED | non-empty after `strip()` |
| `change_from_parent` | str or None | conditional | REQUIRED non-empty when `parent_experiment_id` is set (R§14.3) |
| `research_stage` | enum | REQUIRED | `{"exploratory","in_sample","robustness","validation","out_of_sample"}` |
| `frozen_spec_ref` | str or None | conditional | REQUIRED when `research_stage == "out_of_sample"`; MUST resolve to an existing repo-relative file (R§14.5) |
| `frozen_spec_sha256` | str or None | auto | computed from `frozen_spec_ref` when present; hashed (R§5.1) |
| `tags` | tuple[str,...] | optional | free-form, sorted on store |
| `notes` | str or None | optional | |

### R§4.3 Code (`CodeIdentity`, see R§6)

| field | type | req |
|---|---|---|
| `git_commit` | 40-hex str or None | REQUIRED field; value MAY be `None` if not a git repo |
| `git_available` | bool | REQUIRED |
| `dirty_worktree` | bool | REQUIRED |
| `dirty_summary` | dict[str,int] | REQUIRED — counts keyed by porcelain status code, e.g. `{"M":1,"??":37}` |
| `untracked_code_files` | int | REQUIRED |
| `code_fingerprint` | 64-hex str | REQUIRED (never `None`) |
| `code_fingerprint_n_files` | int | REQUIRED, MUST be `> 0` |
| `code_scope_patterns` | tuple[str,...] | REQUIRED |
| `contract_versions` | dict[str,str] | REQUIRED — R§4.3.1 |

**R§4.3.1 `contract_versions`.** Keys `backtest_contract`, `data_contract`, `registry_schema`,
`data_processing_version`. `registry_schema` MUST equal `schema_version`, and
`data_processing_version` MUST be read from `data.provenance.PROCESSING_VERSION` (the imported
constant), not passed as a literal. Rationale (MW2): a caller-declared version is a rubber stamp
— a live example already exists, where the Binance BTC sidecar carries `qr-data-001-v1.2` while
the constant is `qr-data-001-v1.3`. A mismatch between the constant and any dataset's own
`processing_version` MUST emit the record-level warning `PROCESSING_VERSION_MISMATCH:<dataset_id>`
(R§4.9); it is **not** an error, because the older sidecar is genuine historical data.

### R§4.4 Data (`datasets: tuple[DatasetRef, ...]`)

| field | type | req | notes |
|---|---|---|---|
| `dataset_id` | str | REQUIRED | |
| `source_venue` | str | REQUIRED | |
| `field_type` | str | REQUIRED | pinned vocabulary, R§4.4.1 |
| `native_or_proxy` | `"native"`/`"proxy"` | REQUIRED | no default permitted (R§7.2) |
| `proxy_for` | str or None | conditional | REQUIRED non-empty when `native_or_proxy == "proxy"` |
| `processing_version` | str | REQUIRED | the **dataset's own**, from its sidecar |
| `dataset_version` | str or None | optional | |
| `retrieval_date` | date or None | optional | recorded, **not hashed** (R§5.1.2) |
| `dataset_span_start` | tz-aware ts or None | REQUIRED field | full span of the stored dataset (`DatasetProvenance.time_range[0]`) |
| `dataset_span_end` | tz-aware ts or None | REQUIRED field | `time_range[1]` |
| `data_start` | tz-aware ts | REQUIRED | first timestamp **actually read by the run**, warm-up inclusive (R§4.4.2) |
| `data_end` | tz-aware ts | REQUIRED | last timestamp actually read, inclusive; MUST be `>= data_start` |
| `eval_start` | tz-aware ts or None | conditional | first timestamp of the **evaluated frame**; REQUIRED for `field_type == "ohlcv"` |
| `eval_end` | tz-aware ts or None | conditional | last timestamp of the evaluated frame |
| `symbols` | tuple[str,...] | REQUIRED | non-empty, sorted on store |
| `symbol_mapping` | str or None | optional | |
| `content_hash` | str or None | conditional | REQUIRED non-`None` for any file-backed dataset (R§7.3); hashed |
| `content_hash_method` | str or None | conditional | REQUIRED when `content_hash` is set; the pinned method id of R§7.3 |
| `provenance_notes` | str or None | optional | verbatim `DatasetProvenance.notes` (R§7.1) |

**R§4.4.1 `field_type` vocabulary (pinned, blocking).** Permitted: `"ohlcv"`, `"funding_rate"`,
`"asset_ctx"`, `"universe"`, `"other"`. A **price dataset** is defined normatively as
`field_type == "ohlcv"`. Rationale (BD3): v1.0 invented `ohlcv_1h`/`funding`, which do not exist
in the data layer, so the R§12 containment check (scoped to "price datasets") and the
`field_type` query filter would both have been permanently inert on real records while still
passing on synthetic fixtures. Bar frequency is carried by `strategy.frequency` and the
`dataset_id` (e.g. `hyperliquid.ohlcv.1h.BTC`), not by `field_type`.

**R§4.4.2 `data_start`/`data_end` mean *read*, not *evaluated* (blocking).** A run that computes
`SMA(100)` genuinely reads 99 bars before its evaluated frame, and a funding-enabled run is
handed the full funding history. Recording only the evaluated frame understates the data
dependency — for QR-SMOKE-001 Window A by 99 bars, and for its funding dataset by ~15 months —
and would make the R§12.4 containment check ambiguous. Therefore:
- `data_start`/`data_end` = the span of data actually **passed into** the computation;
- `eval_start`/`eval_end` = the evaluated frame handed to the engine;
- **both pairs are hashed** (R§5.1). A change in warm-up length is a different experiment.

Record-level data fields:

| field | type | req | notes |
|---|---|---|---|
| `universe_policy` | str | REQUIRED | e.g. `"single_symbol_fixed:BTC"`; the as-of rule in words |
| `survivorship_safe` | bool or None | REQUIRED field | `None` MUST be permitted and means *unknown*, which is materially different from `False`; `None` MUST render as `unknown`, never as safe |
| `uses_proxy_data` | bool | auto | derived from `datasets`; cross-checked against `result.uses_proxy_data` (R§12.3) |
| `no_datasets_reason` | str or None | conditional | REQUIRED non-empty when `datasets` is empty (R§4.4.3) |

**R§4.4.3 Empty datasets (MW5).** `datasets` MUST be non-empty **except** when
`experiment_type ∈ {"infrastructure", "data_audit"}`, where an empty tuple is permitted with a
non-empty `no_datasets_reason`. `backtest_config` MAY likewise be `{}` for those two types only.
Rationale: forcing a fabricated `DatasetRef` onto a run that used no dataset is precisely the
"silent default" R§7.2 forbids. A query filter keyed on an absent config key does not match
(R§13.1).

### R§4.5 Strategy (`StrategyRef`)

| field | type | req |
|---|---|---|
| `name` | str | REQUIRED |
| `version` | str | REQUIRED |
| `params` | dict | REQUIRED (MAY be `{}`) — must be encodable under R§3 |
| `frequency` | str | REQUIRED |
| `target_execution_venue` | str | REQUIRED |

### R§4.6 Backtest configuration

`backtest_config: dict` — REQUIRED (see R§4.4.3 for the two exempt types). When produced by the
adapter it is the **complete** field dump of `BacktestConfig`.

**R§4.6.1 (blocking).** The adapter MUST derive this dict by enumerating
`dataclasses.fields(BacktestConfig)`, not by listing field names literally, and a test MUST
assert the dumped key set equals `{f.name for f in dataclasses.fields(BacktestConfig)}`.
Rationale: if the frozen contract is ever amended with a new config field, a hand-written list
silently drops it from the hash, and two experiments differing only in that field collide as
"reruns".

**R§4.6.2 Funding-basis coherence (blocking, replaces v1.0 R§12.1).** If
`backtest_config["funding_mode"] == "disabled"` then `backtest_config["funding_notional_basis"]`
MUST be in the sentinel set `{None, "not_modelled"}`, else `ValidationError`. The check is
performed against **`result.config.funding_notional_basis`** (the caller's config), *not*
`result.funding_notional_basis`. Rationale (BD4), measured: the engine hard-codes
`result.funding_notional_basis = "not_modelled"` whenever funding is not modelled
(`src/backtest/engine.py:1016-1017`), so a check against the *result* field can never fire; but
`BacktestConfig` validates the basis only when `funding_mode == "required"`
(`src/backtest/models.py:118-127`), so `BacktestConfig(funding_mode="disabled",
funding_notional_basis="event_price")` is legal and would be hashed into the record asserting
event-price funding on a run with no funding at all. Both `None` (the dataclass default) and
`"not_modelled"` (what `pipeline.py:328` passes) are legal sentinels and both MUST be accepted.

### R§4.7 Results

`results: ResultSummary or None`. `None` is REQUIRED to be permitted (a FAILED run has no
results). When present:

| field | type | notes |
|---|---|---|
| `metrics` | dict | **`result.metrics` verbatim**, R§4.7.1 |
| `n_periods` | int | `len(result.net_return)`; MUST be `>= 1` |
| `rebalance_count` | int | executed rebalances, `int(result.rebalance_flag.sum())` |
| `ruined` | bool | |
| `custom` | dict | R§4.7.2 |
| `result_warnings` | tuple[str,...] | result-derived only, R§4.9 |

**R§4.7.1 `metrics` is verbatim (blocking, BD13).** The engine returns exactly
`{total_return, cagr, annualized_volatility, sharpe, downside_dev_ann, sortino, max_drawdown,
calmar, avg_turnover, annualized_turnover}` (`src/backtest/metrics.py:79-90`). v1.0 named eight
of these as fields and silently dropped `downside_dev_ann` and `calmar`, so two runs of one
configuration differing *only* in `calmar` would have been reported `REPRODUCED`. The record
therefore persists the dict verbatim, R§3-encoded (NaN/inf-safe), and a test MUST assert the
persisted key set equals `compute_metrics(...)`'s key set. Named accessors on `ResultSummary`
(`total_return`, `sharpe`, …) are read-only conveniences over this dict.
`cagr` MAY be recorded as `None` **only** via an explicit `suppress_cagr=True`, which MUST also
add `CAGR_SUPPRESSED` to `result_warnings`; the raw engine value stays in
`metrics["cagr_raw_suppressed"]` so nothing is destroyed.

**R§4.7.2 `custom`.** Strategy-specific metrics live here. Adding one MUST NOT require a schema
change. Keys MUST be `str`; values MUST be R§3-encodable. `custom` IS persisted and IS compared
for reproducibility (R§5.5) but is NOT part of either hash (results never are).

### R§4.8 `run_facts` (record-level, new in v1.1 — BD6)

`run_facts: dict` — facts about the run that exist **even when there is no result**: bar counts,
intended window boundaries, exception context, provider settings. R§3-encodable, persisted,
**not** hashed, and **not** part of the reproducibility comparison (a failed run's traceback
detail may legitimately vary). Rationale: v1.0 required `custom` metrics on a record whose
`results` is `None`, which had nowhere to live.

### R§4.9 Warnings — two levels (blocking, BD10)

| field | scope | contents |
|---|---|---|
| `warnings` | **record-level, always present even when `results is None`** | facts about code/data/provenance |
| `results.result_warnings` | result-level, only when `results is not None` | facts derived from the result surface |

Record-level tokens (closed list; the adapter MUST emit each when true and MUST NOT invent
others): `PROXY_DATA`, `SURVIVORSHIP_UNKNOWN`, `SURVIVORSHIP_UNSAFE`, `PROVENANCE_INCOMPLETE`,
`DIRTY_WORKTREE`, `GIT_UNAVAILABLE`, `PROCESSING_VERSION_MISMATCH:<dataset_id>`,
`MISSING_ARTIFACT:<name>`, `CONTENT_HASH_UNAVAILABLE:<dataset_id>`, `OOS_WINDOW_OVERLAP:<parent_id>`.

Result-level tokens (closed list): `RUINED`, `LEVERAGE_BREACH`, `FUNDING_GAP_SUSPICIOUS`,
`FUNDING_NOT_MODELLED`, `COUNTERFACTUAL_<status>`, `UNEXECUTED_REBALANCES:<n>`,
`DRAG_NOT_COMPARABLE`, `CAGR_SUPPRESSED`.

Rationale: v1.0 put `PROXY_DATA` and `DIRTY_WORKTREE` inside `ResultSummary`, so a `FAILED`
record could never carry them and the query "show every proxy-data experiment" would silently
miss every failure. The split also removes v1.0's self-contradiction (R§9 mandated a
`MISSING_ARTIFACT` warning that R§4.7.2's closed list forbade) and keeps
**artifact/environment-dependent** tokens out of the R§5.5 zero-tolerance comparison, which
would otherwise make `REPRODUCED` depend on whether a gitignored file happens to be present.
Both lists are sorted. Every token begins with a stable machine prefix so `warning_token`
queries match on the token, not on prose.

### R§4.10 Artifacts

`artifacts: tuple[ArtifactRef, ...]` — MAY be empty. See R§9.

### R§4.11 Derived identity fields

| field | type | notes |
|---|---|---|
| `rerun_of` | str or None | `experiment_id` of the `run_seq == 0` record with the same `exact_hash`; `None` iff `run_seq == 0` |
| `reproducibility_status` | enum | `UNIQUE` / `REPRODUCED` / `DIVERGED` / `NOT_COMPARABLE` (R§5.5) — **recomputed in the folded view**, never frozen at creation |
| `divergence_detail` | tuple[str,...] | non-empty **iff** `DIVERGED`; sorted differing keys |

---

## R§5 Deterministic identity — the two-hash split

### R§5.0 Why two hashes (the material design decision of v1.1)

v1.0 used a single hash that included the repository code fingerprint. The audit demonstrated
this defeats R§1.1's third purpose outright: editing *any* in-scope file — including the
registry's own `store.py` — changes the identity of every subsequent run of an unchanged
strategy, so `duplicate_groups()` is empty in practice and "have we tested this configuration
before?" becomes unanswerable across code states, which is the normal state of a research repo.
Excluding code entirely is equally wrong in the opposite direction: two runs of identical
parameters on different engine code are *not* the same computation, and collapsing them would
let an accounting change hide inside a "rerun".

Both questions are legitimate and they are different questions, so there are two hashes:

| hash | includes | answers |
|---|---|---|
| `semantic_hash` | data + universe + strategy + backtest config | **"Have we tested this configuration before?"** — multiple testing, duplicate search |
| `exact_hash` | `semantic_hash` + code state | **"Is this the identical computation?"** — reproducibility, `experiment_id` |

Alternative considered and rejected: a per-experiment caller-declared "relevant code scope".
Rejected because the declaration is itself gameable and unverifiable — a researcher under
pressure declares a narrow scope and the hash stops noticing the change that mattered.

### R§5.1 `semantic_hash`

`semantic_hash = sha256(canonical_json(payload)).hexdigest()` over **exactly** these keys:

```
{ "schema_version", "experiment_type", "data", "universe_policy", "survivorship_safe",
  "strategy", "backtest_config", "frozen_spec_sha256" }
```

- `data` = list of per-dataset dicts **sorted by `(dataset_id, field_type, source_venue)`**, each
  containing exactly `{dataset_id, source_venue, field_type, native_or_proxy, proxy_for,
  processing_version, dataset_version, data_start, data_end, eval_start, eval_end, symbols,
  content_hash, content_hash_method}`.
- `strategy` = `{name, version, params, frequency, target_execution_venue}`.
- `backtest_config` = the full dict of R§4.6.
- `frozen_spec_sha256` is included so an "OOS" claim against a *modified* spec is a different
  experiment (closes the MW7 loophole).

**R§5.1.1 Every hashed field is REQUIRED-or-explicitly-nullable.** `None` and absent MUST NOT be
distinguishable: the payload builder MUST emit every key above, using `None` where the value is
absent. Rationale: otherwise the same experiment recorded by two code paths — one omitting a key,
one setting it to `None` — yields two different hashes, which is exactly the duplicate-detection
failure the hash exists to prevent. A test MUST assert the payload key set is fixed and
independent of which optional fields were supplied.

**R§5.1.2 Excluded, and why (normative).** Recorded but never hashed by either hash:
`created_at`, `run_executed_at`, `experiment_id`, `run_seq`, `status`, `status_reason`, `results`,
`run_facts`, `artifacts`, `warnings`, `notes`, `tags`, `hypothesis_id`, `parent_experiment_id`,
`reason_for_run`, `change_from_parent`, `research_stage`, `frozen_spec_ref` (the *path*;
its content hash IS hashed), `retrieval_date`, `dataset_span_start/end`, `provenance_notes`,
`uses_proxy_data`, `no_datasets_reason`.

Rationales for the non-obvious ones:
- **`results`** — a rerun reproducing to 1e-16 would otherwise get a different hash, destroying
  the duplicate detection the hash exists for.
- **`created_at`** — an ID depending on wall-clock time cannot detect duplicates at all. This is
  the explicit requirement of the work order.
- **`research_stage`** — the hash answers *"has this computation already been performed?"*; the
  stage is a label about interpretation. Deliberate consequence: relabelling an identical
  computation `in_sample → out_of_sample` is flagged as a **rerun**, not as a new experiment.
  That is the informative outcome — an "OOS" run byte-identical to a prior in-sample run is not
  an out-of-sample test, and the registry should say so loudly.
- **`dataset_span_start/end`** — the full stored span changes whenever the snapshot is refreshed,
  even for the untouched sub-window a run actually used. Hashing it would manufacture a new
  experiment on every ingest. The *used* window (`data_start/end`) and the content hash are what
  identify the data.
- **`uses_proxy_data`** — derived from `data`, already hashed; hashing both would let an
  inconsistent pair produce a distinct hash.
- **`retrieval_date`** — see R§5.1.3.

**R§5.1.3 Data identity: content, not fetch time.** `retrieval_date` is excluded so that
re-downloading byte-identical data does not manufacture a new experiment. The risk this creates
is specific and real: **Hyperliquid `candleSnapshot` serves a rolling ~208-day window**, so "the
same `dataset_id` over the same window" can be *different data* at two points in time.
Mitigations, both normative: (1) `data_start`/`data_end`/`eval_start`/`eval_end` are the windows
actually used, taken from the loaded/evaluated objects, not from the requested arguments
(R§4.4.2); (2) `content_hash` is REQUIRED for every file-backed dataset (R§7.3) and is hashed.

### R§5.2 `exact_hash`

```
exact_hash = sha256(canonical_json({
    "semantic_hash": <hex>,
    "code": {git_commit, dirty_worktree, code_fingerprint, contract_versions},
})).hexdigest()
```

`git_available`, `dirty_summary`, `untracked_code_files`, `code_fingerprint_n_files` and
`code_scope_patterns` are recorded but not hashed — they describe the same code state that
`code_fingerprint` already pins (R§5.2.1).

**R§5.2.1** `code_scope_patterns` is a module constant. Changing it changes `code_fingerprint`
for every subsequent run anyway, so identity is never silently reused; it is recorded so an old
fingerprint stays *interpretable*. A test MUST assert that changing the scope patterns changes
the fingerprint.

### R§5.3 `experiment_id`

```
experiment_id = "EXP-" + exact_hash[:ID_PREFIX_HEX] + "-r" + f"{run_seq:02d}"
ID_PREFIX_HEX = 16          # module-level constant, injectable for tests (R§18.2 M21)
run_seq       = len(sorted(glob("records/EXP-<same prefix>-r*.json")))  # R§5.3.1
```

- Contains **no** time component.
- `run_seq` is registry-state-dependent, so `experiment_id` is **local to one registry
  directory**; `exact_hash` and `semantic_hash` are the portable identities.
- `run_seq > 99` raises `RegistryError` rather than widening the format (which would break the
  lexicographic-equals-numeric property).

**R§5.3.1 Single source of truth (blocking, BD12).** `run_seq` is derived from
`sorted(glob(records/*.json))` — the record **files** — and never from `history.jsonl`.
Rationale: R§10.2 accepts that a crash can leave a record file with no `created` event
(`ORPHAN_RECORD`). If `run_seq` were counted from the event log, the next call would recompute
the same `run_seq`, hit the existing path, and raise forever — permanently wedging that
configuration, with no delete or repair API to escape. Counting from files makes an orphan
self-healing for future writes, and R§11.9's `created_backfilled` recovery event repairs the log
append-only.

**R§5.3.2 Prefix collision (blocking, and made testable).** Before writing, the store compares
the full `exact_hash` of the existing `run_seq == 0` record sharing the id prefix; a mismatch
raises `RegistryError`. `ID_PREFIX_HEX` is a module constant and the store accepts an injectable
`hash_fn`, specifically so a test can set `ID_PREFIX_HEX = 2`, brute-force a 1-byte prefix
collision in a few hundred attempts, and assert the error. Rationale: at 16 hex (64 bits) no
fixture can construct a collision, so v1.0's check was untestable and would have shipped inert.

### R§5.4 The rule: rerun vs. new experiment (normative, quotable)

> **Same `exact_hash` ⇒ the same experiment, re-executed.** It gets a new `experiment_id` (next
> `run_seq`), `rerun_of` → the `run_seq == 0` record, and a `reproducibility_status` of
> `REPRODUCED` / `DIVERGED` / `NOT_COMPARABLE`.
>
> **Same `semantic_hash`, different `exact_hash` ⇒ the same configuration tested on different
> code.** A genuinely new experiment with a new id, surfaced by `semantic_duplicates()`. This is
> the multiple-testing signal.
>
> **Different `semantic_hash` ⇒ a genuinely new experiment.** Any change to dataset
> identity/used-window/symbols/content, universe policy, survivorship flag, strategy
> name/version/params/frequency/venue, frozen-spec content, or any `BacktestConfig` field.
>
> Reruns and semantic duplicates are recorded, never suppressed or deduplicated away.

### R§5.5 Reproducibility comparison

Applies only between records sharing an `exact_hash`, comparing a record against the
`run_seq == 0` record of that hash.

- **Operand:** per-top-level-key canonical form of `{metrics, n_periods, rebalance_count, ruined,
  custom, result_warnings}` — i.e. the whole `ResultSummary`. Nothing inside `ResultSummary` is
  exempted, including `result_warnings`: every token there is a deterministic function of the
  result surface under a hash that already pins code and data, so a token that changes between
  two runs of one configuration is a real divergence (e.g. `COUNTERFACTUAL_FAILED` appearing) and
  must not be masked. Record-level `warnings`, `run_facts` and `artifacts` are **excluded** — they
  can depend on the filesystem/environment (R§4.9).
- **Per-key comparison, not one whole-tree string (MW9):** the verdict may short-circuit on a
  whole-tree byte comparison, but `divergence_detail` requires per-key comparison, plus
  `custom.<key>` and `metrics.<key>` granularity. A single canonical string cannot name the
  differing keys.
- **`NOT_COMPARABLE` (new in v1.1, BD11):** if either side's **folded** status is not
  `COMPLETED`, or either side's `results is None`, the status is `NOT_COMPARABLE` — never
  `DIVERGED`. Rationale: v1.0 labelled "baseline FAILED, rerun succeeded" as `DIVERGED`, i.e. as
  evidence of a determinism defect, when it is evidence of a *fixed data problem*. This case is
  live: R§17's record 5 is a `FAILED` `run_seq == 0`.
- **Recomputed in the folded view (BD11):** because a later `set_status` can change either side's
  status, `reproducibility_status` is a derived property of the folded view, not a value frozen
  into the immutable record.
- **Tolerance is exactly zero.** The `exact_hash` pins code fingerprint and data window, and
  backtest contract §16 mandates bitwise determinism for identical inputs. A tolerance here would
  silently absorb the exact class of defect this comparison exists to surface.
- **NaN (trap, pinned):** comparison is on the canonical form, in which NaN is the literal
  `{"$nonfinite":"nan"}`, so `NaN` vs `NaN` compares **equal** — whereas naive float `==` would
  report `DIVERGED` on every degenerate Sharpe. A test MUST cover a NaN-Sharpe rerun reporting
  `REPRODUCED`. (Reachable: `sharpe` is NaN whenever `annualized_volatility == 0.0` or
  `n_periods < 2`, `src/backtest/metrics.py:44-57`.)
- `DIVERGED` is **not** an error. It is recorded and queryable. Escalation is a human decision.

---

## R§6 Code identity and the dirty worktree

### R§6.1 Rationale (restated — v1.0's premise was false)

v1.0 claimed most implementation code in this repository is untracked. **That was false** (see
Corrections, item 1). The content-fingerprint mechanism is retained on a *durability* argument
instead:

1. A commit hash describes the *index*, not the working tree. A result produced from edited,
   uncommitted code is fully described by neither.
2. Untracked in-scope files have existed in this repository before, and a `git diff`-based check
   is structurally incapable of seeing them — this contaminated a prior work order.
3. A content hash is verifiable years later without git history, from the files alone.

**R§6.1.1 (blocking).** `code_fingerprint` is REQUIRED, never `None`, is computed from file
contents independently of git, and is part of `exact_hash`. `git_commit` alone MUST NEVER be
presented as fully describing a run (R§15).

### R§6.2 Field derivation

- `git_commit`: `git -C <repo_root> rev-parse HEAD`, full 40-hex. **`-C <repo_root>` is
  mandatory** (MW1/finding 9): the existing precedent in this repo omits `cwd`
  (`src/data/provenance.py:48-62`), which would make a fixture-repo capture silently inherit the
  *live* repo's HEAD and render the whole R§6.4 test isolation inert. A test MUST assert a
  fixture repo's captured commit differs from the live repo's HEAD.
- On non-zero exit / missing git / timeout: `git_commit = None`, `git_available = False`,
  `dirty_worktree = True`, `dirty_summary = {"git_unavailable": 1}`, and record-level warning
  `GIT_UNAVAILABLE`. Unknown VCS state MUST default to the *unsafe* value.
- `dirty_worktree`: `True` iff `git -C <repo_root> status --porcelain --untracked-files=all`
  yields at least one entry inside the code scope, **or** `git_available is False`. Untracked
  in-scope files count as dirty.
- `dirty_summary`: counts of in-scope entries keyed by the stripped porcelain XY code (`"??"`,
  `"M"`, `"A"`, `"D"`, `"R"`).
- `untracked_code_files`: count of in-scope `??` entries.
- `code_fingerprint`: `sha256(canonical_json([[relpath, sha256(bytes)], ...]))` over all in-scope
  files, `relpath` POSIX relative to repo root, list sorted by `relpath`, bytes hashed raw (no
  newline normalisation). `glob` output sorted before use (R§16.2).
- `code_fingerprint_n_files` MUST be `> 0`, else `RegistryError` — a zero-file fingerprint is the
  constant hash of `[]`, which would make every experiment's code identity identical.

### R§6.3 Code scope (module constant `CODE_SCOPE_PATTERNS`)

Include: `src/**/*.py`, `strategies/**/*.py`, `experiments/**/*.py`, `scripts/**/*.py`,
`conftest.py`, `pyproject.toml`.
Exclude: any path containing `/__pycache__/`, `/.venv/`, `/artifacts/`, `/.git/`, `*.pyc`, and
`experiments/registry/**` (the registry's own storage — otherwise the fingerprint is
self-referential and unreproducible).

`tests/**` is **deliberately excluded**: a test-only edit does not change what a backtest
computes, and including tests would churn every experiment's identity during ordinary test
development. Judgement call, recorded in R§19 D1.

**R§6.3.1 Capture once per process (blocking, MW3).** `CodeIdentity` MUST be captured **once**,
before any record is written, and reused for every record produced by that process. Rationale:
`experiments/registry_migration/**` is itself in scope, and a multi-minute migration that
captures per-record can straddle an editor save, giving R§17's records 1 and 2 different
`exact_hash` values — silently destroying the rerun demonstration they exist to prove. The
migration MUST assert `records[0].exact_hash == records[1].exact_hash`.

### R§6.4 Injectability

`capture_code_identity(repo_root: Path, *, scope_patterns=CODE_SCOPE_PATTERNS) -> CodeIdentity`
(contract versions assembled internally per R§4.3.1). `record_experiment` accepts a fully-formed
`CodeIdentity`, so tests construct one over a temporary `git init`ed fixture repo in `tmp_path`
(no network needed) and never depend on the live repository's state. A test that captures from
the live repo MUST assert only *structural* properties (64-hex, `n_files > 0`), never a literal
hash.

---

## R§7 Provenance preservation

### R§7.1 Total-coverage mapping (blocking, BD2)

`DatasetProvenance` has exactly 11 fields (`src/backtest/models.py:262-274`). Every one MUST have
a declared destination or an explicit, reasoned exclusion:

| `DatasetProvenance` field | destination |
|---|---|
| `source_venue` | `DatasetRef.source_venue` |
| `field_type` | `DatasetRef.field_type` |
| `time_range` | `DatasetRef.dataset_span_start` / `dataset_span_end` |
| `native_or_proxy` | `DatasetRef.native_or_proxy` |
| `proxy_for` | `DatasetRef.proxy_for` |
| `dataset_id` | `DatasetRef.dataset_id` |
| `dataset_version` | `DatasetRef.dataset_version` |
| `processing_version` | `DatasetRef.processing_version` |
| `retrieval_date` | `DatasetRef.retrieval_date` |
| `symbol_mapping` | `DatasetRef.symbol_mapping` |
| `notes` | `DatasetRef.provenance_notes` (verbatim) |

`notes` MUST be carried verbatim because the data layer hides real content there —
`code_version`, `api_response_count`, `excluded_backfill_bars`, `coverage_segments`
(`src/data/provenance.py:121-126, 222-226`).

**R§7.1.1 (blocking test shape).** The provenance test MUST enumerate
`dataclasses.fields(DatasetProvenance)` and assert each name appears in this mapping table with a
non-`None` destination, then assert a full round-trip through record → disk → load. Rationale:
v1.0 asserted "every field survives" against a hand-written 9-field list with no destination for
`time_range` at all, so the test was unsatisfiable and would have been loosened until green —
the documented mechanism by which inert tests get built on this platform.

### R§7.2 No silent defaults (blocking)

Any dataset whose provenance lacks a REQUIRED R§4.4 field raises `ValidationError` naming both
the field and the `dataset_id`. Defaulting `native_or_proxy` to `"native"` when unknown is
FORBIDDEN — that records proxy data as native, which CLAUDE.md prohibits.
`UniverseProvenance.survivorship_safe` maps to the record's `survivorship_safe`, preserving
`None` as *unknown*.

### R§7.3 `content_hash` (blocking, BD14)

Required non-`None` for every dataset backed by one or more local files. Method id
`"col-buffer-v1"`, defined as: for the dataset's dataframe, take columns in sorted name order;
for each, append the column name UTF-8 bytes, then the raw little-endian value buffer
(`int64` nanoseconds for datetimes, `float64` for floats, UTF-8 with a length prefix for
strings), rows in the frame's stored order after sorting by `(timestamp, symbol)`;
`sha256` the concatenation.

Rationale: hashing the parquet *file bytes* would let a newer `pyarrow` rewriting identical data
manufacture a new experiment; hashing the values makes identity a property of the data.
`content_hash_method` is recorded so a future method can be distinguished rather than confused.
When a dataset genuinely has no local file backing, `content_hash` is `None` and the record-level
warning `CONTENT_HASH_UNAVAILABLE:<dataset_id>` is emitted. `verify_registry()` reports
`INCONSISTENT_CONTENT_HASH:<dataset_id>` when two records claim different content hashes for the
same `(dataset_id, data_start, data_end)`.

---

## R§8 Status, lifecycle, append-only history

### R§8.1 Statuses

`COMPLETED`, `FAILED`, `REJECTED`, `INVALID`.

| status | meaning |
|---|---|
| `COMPLETED` | the run executed and its result is a valid research observation |
| `FAILED` | the run did not produce a usable result (exception, data error, aborted) |
| `REJECTED` | the run completed but the strategy/idea is rejected on the evidence |
| `INVALID` | the run completed but the result is not trustworthy (defect, bad data, methodology error) — MUST NOT be cited as evidence |

`status_reason` is REQUIRED non-empty for `FAILED`, `REJECTED`, `INVALID`.

### R§8.2 Retention (blocking)

There is **no** delete, purge, prune, archive-away, truncate or overwrite API at any level. A
record file is write-once, enforced by a single guard: `os.open(path, O_CREAT|O_EXCL|O_WRONLY)`,
whose `FileExistsError` is translated to `RegistryError`.

**R§8.2.1 One guard only (BD12 / M13).** v1.0 also mandated a redundant "if the path exists,
raise" pre-check, which made the `O_EXCL` mutation survivable — the pre-check still raised, so
the test could not detect the loss of the real guard. The pre-check is removed; `O_EXCL` is
authoritative.

**R§8.2.2 Test shape (finding 12).** "No delete API exists" MUST NOT be asserted by scanning
public attribute names — any `_unlink_record` helper passes that. The test MUST additionally scan
`src/registry/**` source for `os.remove`, `os.unlink`, `shutil.rmtree`, `Path.unlink`,
`truncate`, and `open(..., "w")`/`"w+"`/`"wb"` targeting `records/` or `history.jsonl`.

### R§8.3 Event log

`history.jsonl`, append-only, one JSON object per line (canonical form + `"\n"`):

```
{ "seq": int, "at": {"$ts": ...}, "event": str, "experiment_id": str, "payload": {...} }
```

`seq` = `1 + (number of existing lines)`. Events:

| event | payload |
|---|---|
| `created` | `{"semantic_hash", "exact_hash", "run_seq", "status", "experiment_type", "record_sha256"}` |
| `status_change` | `{"from", "to", "reason"}` |
| `artifact_added` | the encoded `ArtifactRef` |
| `annotation` | `{"note"}` and/or `{"tags_added": [...]}` |
| `created_backfilled` | `{"record_sha256", "recovered_from": "ORPHAN_RECORD"}` — R§11.9 |

A malformed or truncated line MUST raise `RegistryIntegrityError` on read, never be skipped:
skipping silently resurrects a deleted-looking experiment or loses a status change.

### R§8.4 Folded view

`load_experiment(id)` = the immutable record with events applied in ascending `seq`:
`status`/`status_reason` take the last `status_change`; artifacts append; notes append; tags
union (sorted); `reproducibility_status` recomputed (R§5.5). It also exposes
`status_history: tuple[(at, from, to, reason), ...]`, beginning with the creation status. The
on-disk record is never mutated.

---

## R§9 Artifacts

`ArtifactRef`: `{name, kind, path, sha256, size_bytes, recorded_at, description}`.

- `kind` ∈ `{"equity_curve","weights","trades","metrics","log","notes","report","dataset_snapshot","other"}`.
- `path` is **repo-root-relative, POSIX**. An absolute path raises `ValidationError` (it makes the
  record non-portable and can leak a home directory into a committed file).
- At record time the file MUST exist; `sha256` and `size_bytes` are computed from it.
  `allow_missing=True` permits `sha256 = None` and adds the record-level warning
  `MISSING_ARTIFACT:<name>`.
- Large payloads MUST NOT be required in git. Payloads live under `experiments/**/artifacts/` and
  `experiments/registry/artifacts/`; the latter MUST be added to `.gitignore`. Registry
  *metadata* (`records/`, `history.jsonl`) IS committed.
- `verify_artifacts(id) -> dict[name, "OK"|"MISSING"|"MODIFIED"|"UNVERIFIABLE"]`.
- Artifacts are per-record. A shared multi-run artifact MUST NOT be attached to several records
  (MW12) — that misattributes evidence; reference it from one `infrastructure` record instead.

---

## R§10 Storage layout and persistence

### R§10.1 Layout

```
experiments/registry/
  records/EXP-<prefix>-r<NN>.json     # write-once, stored form
  history.jsonl                       # append-only
  artifacts/                          # gitignored payloads
  README.md                           # hand-written
```

Default root `<repo_root>/experiments/registry`; every API takes an explicit `root` so tests use
`tmp_path`. A test writing into the real `experiments/registry/` is a defect (R§18.3).

### R§10.2 Concurrency and atomicity (simplified from v1.0 — MW14, BD12)

No lock file. No `.tmp` + `os.replace` layer. The record is written directly into the descriptor
opened `O_CREAT|O_EXCL|O_WRONLY`, then `flush` + `os.fsync` + `close`; the `created` event line is
appended afterwards with a single `open(..., "a")` write of one complete line.

Rationale: v1.0's advisory lock with bounded retry and no auto-break bought nothing over
write-once `O_EXCL` for a single local user, while introducing a wedge state (R§19 D5 in v1.0)
that required manual intervention; and the `.tmp`/`os.replace` layer combined with an `O_EXCL`
*reservation* could leave a zero-byte record that no `verify_registry` finding covered.

Accepted consequence: a crash between the two writes leaves an `ORPHAN_RECORD` (record file, no
`created` event). This is *reported*, never papered over, and is repairable append-only via
`created_backfilled` (R§11.9). Concurrent multi-process writing is out of scope and documented
(R§19 D5).

### R§10.3 Deterministic persistence (blocking)

Given identical inputs (including an injected `created_at`), two writes into two fresh roots MUST
produce **byte-identical** record files. Requires `sort_keys=True` everywhere; sorted
symbols/tags/warnings/datasets/glob results; no `id()`/`repr()`-derived content; no set-iteration
order (R§16.2); no locale or timezone dependence.

---

## R§11 API

Class `ExperimentRegistry(root: Path, *, hash_fn=hashlib.sha256)`. All methods return folded
views (R§8.4).

1. `record_experiment(*, experiment_type, research_stage, reason_for_run, code_identity,
   datasets, universe_policy, survivorship_safe, strategy, backtest_config, status,
   status_reason=None, results=None, run_facts=None, artifacts=(), parent_experiment_id=None,
   hypothesis_id=None, change_from_parent=None, frozen_spec_ref=None, tags=(), notes=None,
   no_datasets_reason=None, created_at, run_executed_at=None) -> ExperimentRecord`
2. `load_experiment(experiment_id) -> ExperimentRecord` — `KeyError` if absent.
3. `list_experiments() -> tuple[...]` — **all** records, all statuses, **no parameters at all**
   (R§13.3).
4. `find_experiments(**filters) -> tuple[...]` — R§13.
5. `set_status(experiment_id, status, reason) -> ExperimentRecord` — appends `status_change`;
   `reason` REQUIRED non-empty for non-`COMPLETED`; a no-op transition raises `ValidationError`.
6. `add_artifact(experiment_id, artifact) -> ExperimentRecord`
7. `annotate(experiment_id, *, note=None, tags=()) -> ExperimentRecord`
8. `children_of(id)`, `descendants_of(id)`, `lineage_of(id)` (root→self; MUST raise on a cycle,
   never loop), `exact_rerun_groups() -> dict[exact_hash, tuple[ids]]`,
   `semantic_duplicates() -> dict[semantic_hash, tuple[ids]]` (groups of size ≥ 2 only),
   `diverged()`, `failed_or_rejected()`.
9. `verify_registry() -> tuple[str, ...]` — empty means consistent. Finding vocabulary (closed):
   `ORPHAN_RECORD:<id>`, `MISSING_RECORD:<id>`, `RECORD_MODIFIED:<id>`,
   `UNPARSEABLE_RECORD:<id>`, `BAD_SEQ:<n>`, `DANGLING_PARENT:<id>`, `PARENT_CYCLE:<id>`,
   `RUN_SEQ_GAP:<exact_hash>` (a missing `r<NN>` between 0 and max), `PREFIX_COLLISION:<ids>`,
   `INCONSISTENT_CONTENT_HASH:<dataset_id>`, `SCHEMA_VERSION_UNKNOWN:<id>`.
   `repair_orphan(experiment_id)` appends `created_backfilled`; it is the **only** repair
   operation and it adds information, never removes any.
10. `summary(experiment_id) -> str`, `summary_table(records) -> str` — R§15.

### R§11.1 `record_run` context manager (blocking, MW15)

In `backtest_adapter.py`:

```python
with record_run(registry, **record_kwargs) as run:
    result = run_backtest(...)
    run.set_result(result)
```

On normal exit it registers `COMPLETED` from the `BacktestResult`; on exception it registers
`FAILED` with `status_reason = f"{type(exc).__name__}: {exc}"`, `results=None`, the traceback tail
in `run_facts`, and **re-raises**.

Rationale: the registry's largest residual weakness is that failure hiding requires no deletion —
it requires only *not calling the API*, and the most common way that happens is an exception on
an unpromising run. Every driver under `experiments/**` MUST use `record_run`. A test MUST assert
that an exception inside the block produces a `FAILED` record and re-raises.

---

## R§12 `BacktestResult` adapter

`record_backtest_result(registry, result, *, strategy, dataset_windows, universe_policy, ...)`.

### R§12.1 Datasets are derived from `result.provenance`, not declared (blocking, BD1)

The adapter builds one `DatasetRef` **per element of `result.provenance`** (the engine's own
tuple, `src/backtest/engine.py:1206`), taking every provenance field per R§7.1. The caller
supplies only what provenance cannot know, keyed by `dataset_id`: `data_start`, `data_end`,
`eval_start`, `eval_end`, `symbols`, `content_hash`. A caller key with no matching provenance
element, or a provenance element with no caller entry, raises `ValidationError`.

Rationale: in v1.0 both `datasets` and the derived `uses_proxy_data` came from the caller, so
R§14.7's "contradiction is rejected" compared the caller against itself. Omitting the Binance
`DatasetRef` from a Window-B2 registration would have produced `uses_proxy_data=False`, no
`PROXY_DATA` warning and no `proxy_for` — precisely what CLAUDE.md forbids.

### R§12.2 No recomputation (blocking)

The adapter copies; it MUST NOT recompute any metric, and `src/registry/**` MUST NOT import
`backtest.metrics` (asserted by test). A second metric implementation above the engine is a
second accounting authority, which CLAUDE.md forbids.

### R§12.3 Cross-checks against the result surface (blocking)

- `derived uses_proxy_data == result.uses_proxy_data`, else `ValidationError`.
- `survivorship_safe == result.survivorship_safe` (a caller override must match).
- `provenance_complete is False` ⇒ record-level `PROVENANCE_INCOMPLETE`.
- R§4.6.2 funding-basis coherence, evaluated on `result.config`.

### R§12.4 Window containment (blocking; predicate stated literally — BD7)

For every dataset with `field_type == "ohlcv"`:

```
data_start <= result.equity_curve.index[0]  and  data_end >= result.equity_curve.index[-1]
```

Both bounds **inclusive**. Measured facts this must respect: `equity_curve` has `n_periods + 1`
elements and spans the evaluated frame inclusively — `equity_curve.index[0] == frame.index[0]`
and `equity_curve.index[-1] == frame.index[-1]` (`src/backtest/engine.py:687,796`; verified for
Window A: 4512 vs 4511, `2026-01-25 00:00Z` → `2026-07-31 23:00Z`). A strict-containment
implementation would therefore make **every real record raise** and the whole R§17 migration
fail. Additionally assert `eval_start == equity_curve.index[0]` and
`eval_end == equity_curve.index[-1]`, and `data_start <= eval_start`.

**R§12.4.1 (test shape, finding 13).** The containment test MUST be exercised against a **real**
`run_window_a()` result, not only a hand-built fixture, because a synthetic fixture passes even
when the `field_type` classifier never matches a real record.

### R§12.5 Failed runs

Registered via `record_run` (R§11.1) or `record_experiment` directly with `status="FAILED"`,
`results=None`, `status_reason` = exception type + message, and context in `run_facts`. The
registry MUST NOT require a result.

---

## R§13 Query semantics

### R§13.1 Filters (`find_experiments`) — trimmed per MW14

`semantic_hash`, `exact_hash`, `experiment_type`, `status`, `research_stage`, `strategy_name`,
`hypothesis_id`, `parent_experiment_id`, `dataset_id`, `source_venue`, `field_type`,
`native_or_proxy`, `symbol`, `uses_proxy_data`, `survivorship_safe`, `funding_mode`,
`funding_disabled`, `tag`, `warning_token`, `reproducibility_status`, `created_after`,
`created_before`.

(v1.0's `experiment_id`, `strategy_version`, `execution_mode`, `ruined`, `has_artifacts` are
removed: each duplicated `load_experiment` or a one-line inspection of the returned records,
while each carried its own mandatory test — the largest test cost in v1.0 for the least research
value.)

Semantics:
- Filters combine with **AND**; a collection value means **OR** within that filter.
- **Every filter evaluates the FOLDED value (blocking, MW6).** A record created `COMPLETED` and
  later set `REJECTED` MUST NOT match `status="COMPLETED"`. A test MUST cover exactly this.
- `dataset_id`/`source_venue`/`field_type`/`native_or_proxy`/`symbol` match if **any** dataset
  matches. `symbol` matches exact string membership in `symbols` — **no substring matching**,
  which would make `BTC` match `BTCDOM`.
- `funding_disabled=True` ≡ `backtest_config.get("funding_mode") == "disabled"`. A filter on a
  key absent from `backtest_config` (R§4.4.3 empty config) does not match.
- `warning_token=X` matches if any token in the **union** of record-level `warnings` and
  `results.result_warnings` starts with `X`.
- `survivorship_safe` is tri-state; the literal `"unknown"` matches `None`.
- An unknown filter keyword raises `ValidationError`. A silently-ignored typo returns a
  *superset*, which is the worst possible failure for a query used to claim "we never tested
  this".
- `created_after`/`created_before` are **inclusive**; both must be tz-aware.

### R§13.2 Anti-survivorship default (blocking)

`list_experiments()` takes **no** parameters and returns every record regardless of status. There
MUST be no `include_failed`-style flag anywhere in the API and no default filter excluding any
status. A default that hides failures reintroduces exactly the survivorship bias the registry
exists to prevent — and a default is what gets used in a hurry. A test MUST assert a `REJECTED`
and an `INVALID` record both appear in `list_experiments()`.

### R§13.3 Ordering

All list-returning methods sort by `experiment_id` ascending, which is deterministic and
independent of wall-clock ties; `run_seq` is zero-padded to 2 digits so lexicographic order
equals numeric order. Because `experiment_id` begins with a hash prefix this order is
*pseudo-random*, so `summary_table` MUST sort by `(created_at, experiment_id)` for human reading
(MW8) — chronology is what "what did we try, in order" needs.

### R§13.4 Mandated query demonstrations (blocking, BD8)

The migration script (R§17) MUST execute and persist all six to
`experiments/registry/artifacts/query_demo.txt`, and a test MUST assert each returns the expected
ids:

1. all experiments for a strategy — `find_experiments(strategy_name="qr_smoke_001")`
2. failed/rejected/invalid — `failed_or_rejected()`
3. experiments using a dataset — `find_experiments(dataset_id="binance.um.ohlcv.1h.BTC")`
4. children of X — `children_of(<Window A id>)`
5. funding disabled — `find_experiments(funding_disabled=True)`
6. identical configurations / reruns — `exact_rerun_groups()` **and**
   `semantic_duplicates()`

---

## R§14 Research-integrity invariants (blocking; `ValidationError` unless noted)

1. `parent_experiment_id`, when set, MUST resolve to an existing record → else `RegistryError`.
2. Self-parenting rejected. A cycle (only creatable by a corrupted registry) is reported as
   `PARENT_CYCLE`; `lineage_of` MUST raise rather than loop.
3. `parent_experiment_id` set ⇒ `change_from_parent` non-empty.
4. `reason_for_run` non-empty, always.
5. `research_stage == "out_of_sample"` ⇒ `parent_experiment_id` set, `frozen_spec_ref` set **and
   resolving to an existing repo-relative file** (its sha256 recorded and hashed), and
   `parent.created_at <= created_at`. If the record's evaluated window
   (`eval_start`..`eval_end`, any dataset) **intersects** the parent's, emit record-level
   `OOS_WINDOW_OVERLAP:<parent_id>` — a warning, not an error, because legitimate combined-sample
   runs exist. Rationale (MW7): v1.0's gate was satisfiable by `frozen_spec_ref="tbd"`, and the
   substantive anti-self-deception check — that OOS data is not the in-sample data — was absent.
6. `experiment_type == "alpha_research"` ⇒ `hypothesis_id` non-empty.
7. `uses_proxy_data` derived and cross-checked against the engine (R§12.3).
8. Funding-basis coherence (R§4.6.2).
9. `status != "COMPLETED"` ⇒ `status_reason` non-empty.
10. `results is not None` ⇒ `results.n_periods >= 1`.
11. `native_or_proxy == "proxy"` ⇒ `proxy_for` non-empty.
12. `data_end >= data_start`; `eval_end >= eval_start`; `data_start <= eval_start`;
    `eval_end <= data_end`.
13. `datasets` empty ⇒ `experiment_type ∈ {"infrastructure","data_audit"}` and
    `no_datasets_reason` non-empty (R§4.4.3).

---

## R§15 Human-readable summary rendering

`summary(id)` MUST render, and MUST NOT omit: `experiment_id`, folded `status` (+reason),
`experiment_type`, `research_stage`, strategy name/version, the code line, one line per dataset,
funding mode, headline metrics, **both** warning levels, lineage, `reproducibility_status`.

Pinned rendering rules:
- code line: `commit <sha7|NONE> (CLEAN|DIRTY) fingerprint <fp12> (<n> files)`. When
  `dirty_worktree` is `True` the token `DIRTY` MUST appear; a summary showing only the commit for
  a dirty run is a spec violation.
- `survivorship_safe is None` → `survivorship_safe: unknown`, never `False`/`no`.
- proxy datasets → `PROXY(for=<x>)`.
- `metrics["cagr"] is None` → `cagr: n/a (suppressed)`, never `0`.
- `content_hash is None` → `content_hash: unavailable`.
- `NaN` metrics → `nan`, never `0` or blank.

---

## R§16 Determinism requirements

- **R§16.1** Two `record_experiment` calls with identical inputs into two fresh roots produce
  byte-identical record files and identical `experiment_id`.
- **R§16.2** No output may depend on `set`/`dict` iteration order, `PYTHONHASHSEED`, locale, the
  process timezone, or filesystem enumeration order. Sets sorted by canonical form; `glob`
  results sorted.
- **R§16.3** `created_at` is an explicit REQUIRED parameter — the registry never calls
  `datetime.now()` for a record's `created_at`. Callers in `experiments/` pass
  `pd.Timestamp.now(tz="UTC")`; tests pass fixed timestamps. An internal clock call makes
  determinism tests unwriteable, which is how determinism tests end up inert.
- **R§16.4 (new, MW11)** Both hashes MUST be invariant to dict **key insertion order** in
  `strategy.params`, `backtest_config`, `custom` and `run_facts`. Test with `{"a":1,"b":2}` vs
  `{"b":2,"a":1}`. This is the property `sort_keys=True` actually defends, and without this test
  the `sort_keys` mutation is vacuous (finding 2).

---

## R§17 QR-SMOKE-001 retrospective registration

### R§17.1 Method (honesty constraint)

The original Window A/B1/B2 executions (2026-08-16/17) predate the registry and their original
code state is **not recoverable**. The migration therefore **re-executes** the frozen
QR-SMOKE-001 pipeline offline at registration time and records the code identity *of the
re-execution*, with `run_executed_at` set accordingly.

Each record's `notes` MUST state, in words:
1. the record was created by re-executing the frozen QR-SMOKE-001 pipeline offline on the
   registration date;
2. the original executions' git commit and worktree state are **not recoverable and are not
   claimed**;
3. Hyperliquid `candleSnapshot` serves a rolling ~208-day window, so the persisted local snapshot
   is the data of record; `retrieval_date`, `processing_version` and dataset span come from the
   persisted provenance sidecar.

The migration MUST construct every provider `offline=True`, MUST NOT re-fetch, and a test MUST
assert the module contains no `offline=False` and performs no network call (monkeypatched network
guard).

### R§17.2 Dataset payload preservation (blocking, BD15)

`data/processed/**` and `*.parquet` are gitignored, and the HL BTC 1h snapshot currently spans
`2026-01-20 11:00Z → 2026-08-16 17:00Z` while Window A's raw load starts `2026-01-20 21:00Z` —
**about ten hours of margin.** One `candleSnapshot` refresh would delete Window A's warm-up and
make records 1 and 2 permanently non-re-executable, with no copy anywhere in git.

The migration MUST therefore copy each backing parquet to
`experiments/registry/artifacts/datasets/<content_hash>.parquet` and attach it as an
`ArtifactRef` of kind `dataset_snapshot`. R§19 D7 states plainly that these payloads are outside
version control: loss is *detectable* via the hash, but not *recoverable*.

### R§17.3 Records to create (exactly five)

| # | role | type | status | stage | parent | change_from_parent |
|---|---|---|---|---|---|---|
| 1 | Window A | `pipeline_validation` | COMPLETED | `exploratory` | — | — |
| 2 | Window A rerun | `pipeline_validation` | COMPLETED | `exploratory` | — | same `exact_hash` → `run_seq=1`, `rerun_of=#1`, expected `REPRODUCED` |
| 3 | Window B1 | `pipeline_validation` | COMPLETED | `exploratory` | #1 | "Binance USDⓈ-M proxy prices for full 2020–2026 history; funding_mode=disabled — Hyperliquid did not exist for most of the window, so there is no funding to charge" |
| 4 | Window B2 | `pipeline_validation` | COMPLETED | `exploratory` | #3 | "funding re-enabled with Hyperliquid-native funding over the contiguous-coverage window starting 2024-08-15" |
| 5 | Window B2-PRE | `pipeline_validation` | **FAILED** | `exploratory` | #4 | "same configuration as B2 with the evaluated frame starting 2024-01-01, before contiguous Hyperliquid funding coverage" |

`research_stage = exploratory` for all five; `experiment_type = pipeline_validation` is the
discriminator marking these as *not* research observations. Their profitability is meaningless
and MUST NOT be presented as a result of interest.

**Record 2 (MW4)** MUST be executed in a **subprocess with a different `PYTHONHASHSEED`**. An
in-process re-execution with identical inputs is a tautology; the failure modes worth catching
(hash-seed-dependent iteration order, dict/set ordering, thread-count-dependent BLAS reductions)
only appear across processes. If it reports `DIVERGED`, the migration MUST still record it and
the work order MUST report the divergence as a material finding.

### R§17.4 Record 5 is a genuine, measured failure

**Measured:** that configuration raises

```
backtest.models.DataIntegrityError: spec §2.2 funding-coverage window rule violated for 'BTC':
expected exactly ONE FundingCoverage record covering [2024-01-01 00:00Z, 2026-07-31 23:00Z], found 0
```

from the driver's own pre-flight check (`experiments/qr_smoke_001/pipeline.py:187-205`), which
**pre-empts** the engine's `FundingDataError`. v1.0 claimed `FundingDataError`; that was wrong,
and a test asserting it would have failed on correct code.

**Decision (methodology):** register the failure **exactly as the real driver path produces it** —
`DataIntegrityError` from the pre-flight — and do **not** bypass the pre-flight to manufacture the
engine's error. Rationale: the recorded fact should be what our pipeline actually does, not a
path we would never run. `status_reason` is the verbatim exception type and message.

`run_facts` MUST additionally record the underlying data fact, measured: Hyperliquid BTC funding
has 28,056 events spanning `2023-05-12 00:00:00.048Z → 2026-08-16 17:00:00.005Z`, with 84 gaps
exceeding the 90-minute tolerance (83 strictly before `2024-08-15 14:00Z`; the 84th terminates
*at* `2024-08-15 14:00:00.074Z`), yielding 85 coverage segments of which the last,
`2024-08-15 14:00:00.074Z → 2026-08-16 17:00:00.005Z`, is the only one usable for a
funding-enabled run. Permanently recording this is the point: it is a hard data boundary that
must not be re-attempted.

Because record 5 has `results = None`, its bar counts and intended window live in `run_facts`
(R§4.8), not in `custom` — v1.0 required `custom` metrics on a record with no `ResultSummary`.

### R§17.5 Data recorded per window

| window | datasets |
|---|---|
| 1, 2 (A) | `hyperliquid.ohlcv.1h.BTC` (`ohlcv`, native), `hyperliquid.funding.BTC` (`funding_rate`, native) |
| 3 (B1) | `binance.um.ohlcv.1h.BTC` (`ohlcv`, **proxy** for Hyperliquid BTC perp price) |
| 4, 5 (B2, B2-PRE) | `binance.um.ohlcv.1h.BTC` (`ohlcv`, proxy), `hyperliquid.funding.BTC` (`funding_rate`, native) |

Per R§4.4.2: for `ohlcv` datasets `data_start`/`data_end` = the **loaded raw span**
(warm-up inclusive; e.g. Window A `2026-01-20 21:00Z`, Window B2 `2024-08-11 12:00Z`) and
`eval_start`/`eval_end` = the **evaluated frame** (Window A `2026-01-25 00:00Z` →
`2026-07-31 23:00Z`). For the `funding_rate` dataset, `data_start`/`data_end` = the span of
funding events actually passed to the engine (the full loaded history,
`2023-05-12 … → 2026-08-16 …`), which is what "actually read" means
(`experiments/qr_smoke_001/pipeline.py:169-184`); `eval_start`/`eval_end` are `None` (not a price
dataset). `universe_policy = "single_symbol_fixed:BTC"`; `survivorship_safe` from
`UniverseProvenance`.

`custom` for records 1–4 MUST include, demonstrating R§4.7.2 without a schema change:
`funding_events_excluded`, `funding_gap_tolerance_suspicious`, `max_gross_exposure`,
`n_unexecuted_rebalances`, `total_drag_return`, `drag_comparable`, `counterfactual_status`,
`first_frame_signal`.
`run_facts` for all five: `n_raw_bars`, `n_frame_bars`, intended window boundaries, `offline=True`.

Artifacts (per-record only, MW12): the equity-curve series written by the migration, and the
dataset snapshots of R§17.2. The shared `experiments/qr_smoke_001/artifacts/summary.json` MUST
NOT be attached to all five records.

---

## R§18 Mandatory tests and mutation proof

### R§18.1 Coverage areas (each MUST have at least one *discriminating* test)

1. **Deterministic ID** (R§5.1–5.3) — `created_at` does not affect `semantic_hash`/`exact_hash`;
   a second identical record has the **same id prefix with `run_seq` incremented** (v1.0's
   wording "`created_at` does not affect `experiment_id`" was literally unsatisfiable, finding
   11); each hashed field changed one at a time changes the hash; key-insertion-order invariance
   (R§16.4); `semantic_hash` unchanged when only code changes, `exact_hash` changed.
2. **Lineage** — children, descendants, chain, dangling parent rejected, self-parent rejected,
   cycle raises rather than loops.
3. **Failed-experiment retention** — `FAILED`/`REJECTED`/`INVALID` in the default listing; no
   delete path, asserted per R§8.2.2 (source scan, not attribute names).
4. **Dirty worktree** (R§6) — in a `git init`ed `tmp_path` fixture: clean vs dirty vs untracked
   in-scope file give different `dirty_worktree`/fingerprints; `git -C` isolation proven by
   asserting the fixture commit ≠ live HEAD; `git_available=False` forces `dirty_worktree=True`
   and `GIT_UNAVAILABLE`; a dirty record's `summary()` contains `DIRTY`; zero-file scope raises.
5. **Provenance preservation** — the R§7.1.1 field enumeration; full round-trip; missing REQUIRED
   field raises naming field + dataset_id; proxy without `proxy_for` raises; `notes` verbatim.
6. **Parameter serialization** (R§3) — round-trip of NaN/±inf, tz-aware ts, `Timedelta`, nested
   containers, numpy scalars, `set`; tz-naive raises; unsupported type raises (no `str`
   fallback); reserved-key collision raises; **plus the R§3.1.3 strict-parser and no-`NaN`-token
   assertions**.
7. **Duplicate/rerun detection** — rerun → `run_seq=1`/`rerun_of`; `REPRODUCED` on identical
   results including a NaN Sharpe; `DIVERGED` with detail on a changed metric **and** on a
   changed `custom` sub-key; `NOT_COMPARABLE` when the baseline is `FAILED`;
   `exact_rerun_groups()`; `semantic_duplicates()` non-empty for same-config-different-code;
   `run_seq > 99` raises; the R§5.3.2 prefix-collision test with `ID_PREFIX_HEX = 2`.
8. **Query/filter behaviour** — every remaining filter; AND across / OR within; unknown filter
   raises; `symbol` does not substring-match (`BTC` vs `BTCDOM`); inclusive date bounds;
   **folded-status filtering** (created `COMPLETED`, set `REJECTED`, must not match
   `status="COMPLETED"`).
9. **`BacktestResult` registration** — against a **real** `run_window_a()` result (R§12.4.1):
   `backtest_config` key set equals the dataclass field names; datasets derived from
   `result.provenance` with a caller-omitted dataset raising; `uses_proxy_data` cross-check
   raising on mismatch; containment predicate inclusive at both ends; persisted `metrics` key set
   equals `compute_metrics`'s; warnings split correctly across the two levels; `record_run`
   registering `FAILED` and re-raising on exception.
10. **Artifact references** — hash/size captured; `MODIFIED` after a byte change; `MISSING` after
    deletion; absolute path raises; `allow_missing` emits the record-level warning.
11. **Deterministic persistence** — byte-identical files across two fresh roots;
    `verify_registry()` clean; `RECORD_MODIFIED` after editing a record file; `ORPHAN_RECORD`
    detected and `repair_orphan` fixing it append-only; `UNPARSEABLE_RECORD`; corrupt
    `history.jsonl` line raises.
12. **Layering** (R§2.1.1) — engine/data import no registry, in a fresh subprocess.
13. **Query demonstrations** (R§13.4) — all six return the expected ids.

### R§18.2 Mutation proof (non-negotiable)

For each mutation: apply to the **source**, run the named target test, record
`BROKE` / `SURVIVED` / `VACUOUS`, restore, verify against the baseline manifest (R§18.3).

- **`SURVIVED` is a blocking defect in the test suite** (not in the mutation) — MW13.
- **`VACUOUS`** means the mutation provably cannot change behaviour on the fixture. It MUST be
  reported with the measured discriminating-case count, and the fixture MUST then be strengthened
  until the mutation discriminates, or the vacuity justified in writing.

The v1.0 table had 8 defective entries; all are repaired below and the reason is kept visible.

| # | mutation | target test | note |
|---|---|---|---|
| M1 | include `created_at` in the `semantic_hash` payload | R§18.1(1) | |
| M2 | include `results` in the hash payload | R§18.1(7) **`DIVERGED`** sub-case | v1.0 targeted the `REPRODUCED` test, where identical results make this vacuous |
| M3 | drop `code_fingerprint` from the `exact_hash` payload | R§18.1(1) code-only-change test | fixture pair MUST be identical in `git_commit`, `dirty_worktree`, `contract_versions`, differing only in one file's bytes; otherwise vacuous |
| M4 | drop one `BacktestConfig` field from the adapter dump | R§18.1(9) field-set test | |
| M5 | `run_seq` format `{:d}` | R§18.1(1) ordering | fixture MUST contain **11** records sharing one `exact_hash` (r0…r10): measured, `{:d}` and `{:02d}` order identically for `run_seq <= 9` |
| M6 | compare results with `math.isclose(rel_tol=1e-6)` | R§18.1(7) `DIVERGED` with a **1e-9** perturbation | verified discriminating: `isclose(1.0, 1+1e-9, rel_tol=1e-6)` is `True`, so the mutant wrongly reports `REPRODUCED` |
| M7 | compare with raw float `==` (NaN-unsafe) | R§18.1(7) NaN-Sharpe `REPRODUCED` | reachable: NaN Sharpe when `annualized_volatility == 0.0` |
| M8 | `list_experiments()` filters to `COMPLETED` | R§18.1(3) | |
| M9 | unknown filter keyword silently ignored | R§18.1(8) | |
| M10 | `symbol` filter uses substring `in` | R§18.1(8) `BTC`/`BTCDOM` | |
| M11 | `dirty_worktree` computed without `--untracked-files=all` | R§18.1(4) | requires the `git init` fixture repo — the live repo has **0** untracked in-scope files, so a live-repo test is inert |
| M12 | `git` failure yields `dirty_worktree=False` | R§18.1(4) | requires `git -C <repo_root>`; without it the fixture inherits live HEAD and the test is inert |
| M13 | remove `O_EXCL` (open `"w"`) | R§18.1(11) write-once | discriminating only because R§8.2.1 deleted v1.0's redundant path-exists pre-check |
| M14 | `json.dumps(..., sort_keys=False)` | R§18.1(1) **key-insertion-order** test (R§16.4) | v1.0 targeted byte-identical persistence, which is vacuous: `json.dumps` preserves insertion order deterministically, so two dumps of the *same* tree match without `sort_keys` |
| M15 | `allow_nan=True`, no `$nonfinite` wrapper | R§18.1(6) **strict-parser / no-`NaN`-token** assertions | v1.0's round-trip target survives: `json.dumps(nan)` → `NaN` and `json.loads('NaN')` → `nan` |
| M16 | `SerializationError` → `str(obj)` fallback | R§18.1(6) | |
| M17 | skip malformed `history.jsonl` lines | R§18.1(11) | |
| M18 | `native_or_proxy` defaults to `"native"` when absent | R§18.1(5) | |
| M19 | drop the `out_of_sample` ⇒ `frozen_spec_ref`-resolves check | R§14.5 test | |
| M20 | `status_reason` optional for `REJECTED` | R§14.9 test | |
| M21 | prefix collision treated as a rerun (skip the full-hash check) | R§18.1(7) with `ID_PREFIX_HEX = 2` | at 16 hex no fixture can construct a collision — v1.0's check was untestable |
| M22 | migration records `data_start` from the **requested** window instead of the loaded raw span | R§18.1(9)/R§17.5 test asserting `recorded.data_start == run.raw_index[0]` and `!= run.frame_index[0]` | v1.0 targeted `src/registry`, which never derives windows; discriminating values measured: Window A raw `2026-01-20 21:00Z` vs frame `2026-01-25 00:00Z` |
| M23 | build `DatasetRef`s from the caller list instead of `result.provenance` | R§18.1(9) omitted-proxy-dataset test | new in v1.1 (BD1) |
| M24 | put `PROXY_DATA` in `result_warnings` instead of record-level | R§18.1(9) warning-split test, asserted on a `FAILED` record | new in v1.1 (BD10) |
| M25 | `NOT_COMPARABLE` collapsed to `DIVERGED` | R§18.1(7) failed-baseline test | new in v1.1 (BD11) |
| M26 | `run_seq` counted from `history.jsonl` instead of `records/` | R§18.1(11) orphan test | new in v1.1 (BD12) |
| M27 | strict containment (`<` instead of `<=`) in R§12.4 | R§18.1(9) on the real Window A result | new in v1.1 (BD7) |
| M28 | filters evaluate creation-time status instead of folded | R§18.1(8) folded-status test | new in v1.1 (MW6) |

### R§18.3 Workspace integrity (repository rule, blocking)

Before any mutation cycle, write `docs/qr_infra_002_baseline.sha256` — a SHA-256 manifest of
**every** file in scope, **tracked and untracked** (`src/registry/**`, `tests/registry/**`,
`experiments/registry_migration/**`, this spec) — and record
`git status --porcelain --untracked-files=all`. Verify the manifest **after every single
mutation**, not batched at the end. Prefer per-target-test runs over full-suite runs during
mutation work. An auditor MUST NOT return PASS if the workspace-integrity check fails. Registry
data written by tests MUST go to `tmp_path`.

---

## R§19 Open decisions and accepted limitations

| # | decision | accepted consequence |
|---|---|---|
| D1 | `tests/**` excluded from `code_fingerprint` (R§6.3) | a test-only change does not create a new experiment identity; a *test* defect is not visible in identity |
| D2 | `research_stage` excluded from both hashes | relabelling an identical computation as OOS is flagged as a rerun — intended |
| D3 | `experiment_id` depends on registry state via `run_seq` | ids are local to one registry root; the hashes are the portable keys |
| D4 | `exact_hash` truncated to 16 hex in the id | prefix collision explicitly checked and testable via `ID_PREFIX_HEX` (R§5.3.2) |
| D5 | no lock file; single-writer assumption (R§10.2) | concurrent multi-process writes are unsupported and undetected; a crash can leave an `ORPHAN_RECORD`, repairable append-only |
| D6 | `retrieval_date` and `dataset_span_*` not hashed (R§5.1.2/5.1.3) | mitigated by used-window fields + required `content_hash` |
| D7 | registry metadata committed; artifact **and dataset** payloads not | dataset payloads are outside version control: loss is detectable via `content_hash` but **not recoverable**; the HL 1h snapshot is ~10h from rolling past Window A's warm-up, which is why R§17.2 copies it |
| D8 | no schema-migration machinery | a `schema_version` bump requires a written migration note; readers MUST raise `SCHEMA_VERSION_UNKNOWN` rather than guess |
| D9 | **the registry only knows what it is told** | hiding a failure requires no deletion — only *not calling the API*. `record_run` (R§11.1) is the structural mitigation and is mandatory for every driver under `experiments/**`; an in-process parameter search inside a strategy still produces one record and the registry cannot see the search |
| D10 | repo-global `code_fingerprint`, no per-experiment "relevant scope" | any in-scope edit changes `exact_hash`; this is why `semantic_hash` exists (R§5.0). A caller-declared narrow scope was rejected as gameable and unverifiable |
| D11 | `content_hash` method `col-buffer-v1` hashes values, not file bytes (R§7.3) | pyarrow rewrites do not manufacture new experiments; a future method needs a new id, not a redefinition |

---

# R§20 AMENDMENTS (v1.2) — dual REGISTRY FAIL repair

**Status of this section: NORMATIVE AND GOVERNING.** Where R§20 conflicts with R§1–R§19, R§20
governs. Each amendment cites the audit finding that caused it, so a rejected design stays
visible rather than being quietly overwritten.

## R§20.0 Adjudication summary

Two independent audits of the v1.1 implementation both returned **REGISTRY FAIL**:
- **Code audit** — reproduced all 28 spec-table mutations (27 BROKE; M27 half-inert), then ran 44
  of its own and found **29 SURVIVORS**. R§18.2 defines `SURVIVED` as a blocking defect *in the
  test suite*, so this alone is blocking. Also: two mandated APIs absent, `datahash.py` with zero
  tests, and a mandated test writing into the real registry.
- **Integrity review** — demonstrated, with working scripts, that four of five anti-bias
  mechanisms are bypassable through the same public API the reference migration itself uses.

Both verdicts are accepted in full. Two findings are **rejected**, with reasons, in R§20.9.

## R§20.1 Corrections to v1.1's own factual claims

5. **v1.1 R§13.4/R§17.5 used `dataset_id` `binance.um.ohlcv.1h.BTC`. The real value is
   `binance.ohlcv.1h.BTC`** — no `um.` infix (`src/data/storage.py:72-76`; every sidecar under
   `data/metadata/binance/`). Caught by the implementer, confirmed by the code audit. This is the
   **third** stale-literal error in this spec's history (see Corrections 2 and 3), which is why
   R§20.7 makes literal-measurement a mandatory pre-freeze step.

## R§20.2 Registration must not be optional (integrity BI-1, BI-2)

The registry's largest weakness was never deletion — it was that a run need only *not call the
API*. v1.1 named `record_run` as the mitigation; measured, **`record_run` had no production caller
anywhere in the repository**, and the smoke drivers did not reference the registry at all.

- **R§20.2.1 (blocking).** `src/registry/backtest_adapter.py` MUST expose
  `run_and_register(registry, config, market_data, strategy_output, *, record_kwargs, **run_kwargs)`
  which is the **only** sanctioned way a driver obtains a `BacktestResult`. It calls
  `run_backtest`, then registers via `record_backtest_result` on success. Note this makes the
  *adapter module* (not `src/registry` as a whole) import the engine; R§2.1's prohibition is
  hereby scoped to `store.py`/`models.py`/`serialize.py`/`codeid.py`/`datahash.py`, which MUST
  remain engine-free. `backtest_adapter.py` MAY import `backtest.engine`; it still MUST NOT import
  `backtest.metrics` (R§12.2 — no second accounting authority).
- **R§20.2.2 (blocking).** A test MUST statically assert that every module under `experiments/**`
  that references `run_backtest` obtains it through `run_and_register`/`record_run`. The one
  permitted exemption is `experiments/qr_smoke_001/**`, which is FROZEN and out of scope; the test
  MUST carry that exemption as an explicit, named allow-list of exactly those files, so a *new*
  unregistered driver fails the test.
- **R§20.2.3 (blocking).** `record_run` MUST catch `BaseException`, not `Exception`, and re-raise
  unchanged. `KeyboardInterrupt`/`SystemExit` register `status="FAILED"` with
  `status_reason="ABORTED: <ExcType>: <msg>"`. Measured under v1.1: Ctrl-C and `sys.exit` each
  produced **zero records and no complaint** — the accidental survivorship path.
- **R§20.2.4 (blocking).** If the block exits normally without `set_result`, `record_run` MUST
  register `status="INVALID"`, `status_reason="NO_RESULT: run_and_register/set_result was never
  called"` — and MUST NOT merely raise. Measured under v1.1: catching the exception inside the
  block and the complaint outside produced zero records.

## R§20.3 `recorded_via` — provenance of the record itself (integrity BI-4)

Measured under v1.1: via the public `record_experiment` path, proxy data was recorded **and
rendered by `summary()` as `native`**, a funding-free run was recorded as `funding_mode=required`,
and metrics were hand-typed — with **no field indicating which path produced the record.** R§12.1's
derivation cross-check guarded only the adapter path, while `record_experiment` is documented as
the primary API and is what the reference migration uses for 2 of its 5 records.

- **R§20.3.1 (blocking).** New REQUIRED record field `recorded_via ∈ {"adapter", "manual"}`, set
  to `"adapter"` **only** by `record_backtest_result`, never settable by an external caller.
  Hashed into `semantic_hash` (a hand-asserted result is not the same experiment as a
  cross-checked one).
- **R§20.3.2 (blocking).** On the `manual` path, `results is not None` is REJECTED unless the
  caller passes `manual_results_justification` (non-empty), which is recorded and rendered. A
  manual record carrying results also carries the record-level warning
  `UNVERIFIED_MANUAL_RESULTS`.
- **R§20.3.3 (blocking).** `summary()` MUST render `recorded_via`, and for `manual` MUST render
  the literal line `WARNING: provenance/metrics NOT cross-checked against a BacktestResult`.
- **R§20.3.4.** `record_run`'s failure branch keeps caller-supplied datasets (no result exists to
  derive from — the implementer's reasoning was correct), but the resulting record is
  `recorded_via="manual"`, and a FAILED registration whose datasets include a proxy MUST still
  emit `PROXY_DATA`. Recorded as accepted limitation D12.

## R§20.4 Status laundering must leave a permanent, rendered trace (integrity BI-3)

Measured: `set_status(INVALID)` then `set_status(COMPLETED)` made an invalid result citable, with
the only evidence in `status_history`, which nothing rendered and no filter matched.

- **R§20.4.1 (blocking).** Sticky record-level warnings, derived in the folded view and
  **irremovable**: `WAS_INVALIDATED` if any `status_change` ever set `INVALID`; `WAS_REJECTED` for
  `REJECTED`; `WAS_FAILED` for `FAILED`.
- **R§20.4.2 (blocking).** `summary()` MUST render the full `status_history` whenever its length
  exceeds 1.
- **R§20.4.3.** New filter `ever_status` matching any status the record has ever held.

## R§20.5 Multiple testing — R§1.1(3) was not actually met (integrity MW-d, MW-e)

Measured: 40 trivial `sma_window` variants produced 40 unlinked records, `semantic_duplicates()`
`{}`, no shared parent, no `hypothesis_id`. Worse, after any data re-ingest the `content_hash`
changes, so **every previously-tested configuration reads as untested** — silently deflating the
count in the safe-looking direction.

- **R§20.5.1 (blocking).** New REQUIRED field `search_space_id: str` — a stable identifier for the
  *idea* being tested, shared by every variant of it. REQUIRED (non-empty) for
  `experiment_type ∈ {alpha_research, robustness, validation, replication}`; optional for
  `pipeline_validation`/`infrastructure`/`data_audit`. **Not hashed** (it is a grouping label, not
  configuration).
- **R§20.5.2 (blocking).** New REQUIRED field `n_configs_evaluated: int` (`>= 1`) — how many
  configurations were evaluated in the process that produced this record. A driver that sweeps
  in-process and registers one winner MUST report the true count. This cannot be *enforced*, only
  *asked for and rendered*; an unregistered in-process sweep remains D9. `summary()` MUST render
  it whenever `> 1`.
- **R§20.5.3 (blocking).** `sibling_count(record)` and `search_space_summary(search_space_id) ->
  {n_records, n_configs_evaluated_total, statuses, best_and_worst_sharpe}`. The point is that the
  denominator of any reported Sharpe is retrievable.
- **R§20.5.4 (blocking).** `near_duplicates()` grouping on a **`config_family_hash`**: the
  `semantic_hash` payload with `content_hash`, `content_hash_method`, `data_start`, `data_end`,
  `eval_start`, `eval_end` and `recorded_via` removed. This is what answers "have we tested this
  configuration before?" across data re-ingests and window nudges. Recorded on the record and
  filterable.
- **R§20.5.5 (blocking).** `verify_registry()` MUST report
  `SEMANTIC_DUP_RESULT_DIFF:<id_a>:<id_b>` when two records share a `semantic_hash` but differ in
  `metrics`. Measured under v1.1: the same configuration on different code produced Sharpe 0.4 vs
  2.4, both `UNIQUE`, `diverged()` empty, `verify_registry()` clean — an accounting change hiding
  in plain sight as two records nobody compared.

## R§20.6 The out-of-sample gate (integrity MW-a, MW-b, MW-c)

- **R§20.6.1 (blocking).** The `OOS_WINDOW_OVERLAP` check MUST be computed against **every
  ancestor** in `lineage_of(parent)` and every record sharing `(strategy.name, search_space_id)` —
  not the direct parent alone. Measured: pointing `parent_experiment_id` at an unrelated record,
  or at a grandparent-once-removed, suppressed the warning entirely.
- **R§20.6.2 (blocking).** `frozen_spec_ref` MUST resolve to a **git-committed blob**; the record
  stores `(frozen_spec_commit, frozen_spec_blob_sha)`. An uncommitted or dirty spec file is
  REJECTED for `research_stage == "out_of_sample"`. Rationale: v1.1's content hash caught later
  edits to an already-recorded hash, but not the sequence that matters — look at the OOS result,
  edit the spec, then register. A committed blob establishes prior commitment; a working-tree file
  does not.
- **R§20.6.3.** Warn `SPEC_CHANGED_SINCE_PARENT` when a child's `frozen_spec_blob_sha` differs
  from its parent's.
- **R§20.6.4 (blocking).** Because `frozen_spec_sha256` is hashed **and** required for OOS
  records, v1.1's promised "relabelling an identical computation as OOS is flagged as a rerun"
  (R§5.1.2 / D2) was structurally unreachable — measured `UNIQUE`, not a rerun. The
  `config_family_hash` of R§20.5.4 excludes it, so the check now fires: warn
  `OOS_RELABEL_OF:<id>` when an `out_of_sample` record's `config_family_hash` matches a prior
  non-OOS record.
- **R§20.6.5.** `frozen_spec_ref` MUST be anchored to the repo root and MUST reject any path
  escaping it (`..`), mirroring R§9's absolute-path guard (code audit MW-A4).

## R§20.7 Chronology, tamper-evidence and code recoverability (integrity MW-f, MW-g, MW-l)

- **R§20.7.1 (blocking).** Each `history.jsonl` line MUST carry `logged_at` — a **store-stamped**
  `pd.Timestamp.now(tz="UTC")`, excluded from every hash and from every comparison. R§16.3's
  injectable `created_at` stays (determinism tests require it), but v1.1 stamped the history line
  with the *caller's* value too, so nothing recorded when the registry was actually told. Warn
  `BACKDATED_CREATED_AT` when `|logged_at - created_at| > 1h`. Measured: a `created_at` of
  `2024-01-01` was accepted, rendered in `summary_table`, and `verify_registry()` stayed clean.
- **R§20.7.2 (blocking).** Each history line MUST carry `prev_line_sha256` (the SHA-256 of the
  previous line's exact bytes; `null` for `seq == 1`), giving an append-only hash chain.
  `verify_registry()` reports `HISTORY_CHAIN_BROKEN:<seq>`. Measured: deleting a record file plus
  its history line and renumbering `seq` left **no residual evidence** and a clean
  `verify_registry()`. This is deliberate fraud rather than accident, but v1.1 presented "there is
  no delete API" as retention *enforcement* when the real guarantee was only "the API will not do
  it for you".
- **R§20.7.3 (blocking).** `verify_code_state(record, repo_root) -> "MATCH" | "CODE_FINGERPRINT_MISMATCH"
  | "UNVERIFIABLE"`, and a record-level warning `UNTRACKED_CODE_AT_RECORD_TIME` when
  `untracked_code_files > 0`. Rationale, measured: **all five v1.1 records pinned a
  `code_fingerprint` matching no state that existed anywhere**, one day later — 47 files of which
  10 were dirty/untracked. `DIRTY` told the reader the state was uncommitted; nothing told them
  the fingerprint was already unresolvable.
- **R§20.7.4 (blocking, Research-Lead obligation).** Registry metadata (`records/`,
  `history.jsonl`) MUST be committed, and **the R§17 migration MUST be executed from a clean,
  committed worktree** so that `dirty_worktree is False` and `code_fingerprint` resolves to git
  objects. Sequencing v1.1's migration before the commit was the error that made every one of its
  records unverifiable.

## R§20.8 Code-audit conformance repairs

- **R§20.8.1 (blocking, BD-A1/BD-A2/BI-5).** Implement R§9 properly **inside `src/registry`**:
  `ArtifactRef.from_file(path, *, allow_missing=False)` computing `sha256`/`size_bytes` and raising
  when the file is absent, and `ExperimentRegistry.verify_artifacts(id)` returning the four-state
  vocabulary. `verify_registry()` gains `ARTIFACT_MISSING:<id>:<name>` and
  `ARTIFACT_MODIFIED:<id>:<name>`. The R§18.1(10) tests MUST target these functions; v1.1's tests
  validated a **helper defined inside the test file**, so no registry defect could break them, and
  an `ArtifactRef` could assert an arbitrary hash for an arbitrary path.
- **R§20.8.2 (blocking, BD-A3).** No test may write into the real `experiments/registry/`. v1.1's
  `test_migration.py` monkeypatched the module globals, but the R§17.3 rerun **subprocess**
  re-imported the module fresh and wrote real dataset snapshots (proven: two stray parquets). The
  migration MUST accept its registry root, artifacts root and snapshot dir from argv/env and pass
  them to the child; the test MUST additionally assert the real snapshot directory's file set is
  unchanged across the test.
- **R§20.8.3 (blocking, BD-A4).** `datahash.py` MUST have discriminating tests: column name in the
  digest, sorted column order, `(timestamp, symbol)` row sort, plus a pinned golden digest for a
  small fixed frame. Three mutations of it survived the entire 143-test suite.
- **R§20.8.4 (blocking, R§7.3 / ambiguity 2).** The method id becomes **`col-buffer-v2`**, with the
  full column-type vocabulary pinned normatively: datetime→`int64` ns LE; float→`float64` LE;
  integer→`int64` LE; bool→one byte `0x00`/`0x01`; string→`uint32` LE length prefix + UTF-8.
  Rationale: the implementer's extension to `int64`/`bool` columns was the right encoding choice,
  but it *redefined* `col-buffer-v1` while keeping the id, which D11 forbids. A new id is the
  honest form. All `content_hash` values change; the registry is regenerated (R§20.10).
- **R§20.8.5 (blocking, BD-A5).** R§12.4's `data_start <= frame_start` bound needs a fixture where
  `data_start == eval_start` (a zero-warm-up run). Mutating `<=` → `<` survived the full suite.
- **R§20.8.6 (blocking, BD-A6/MW-A10).** Add one discriminating test for **each** survivor the
  audit reported. At minimum: R§8.4 fold order / `notes` append / `tags_added` union / unknown
  event kind / blank line; R§5.5 `result_warnings` in the operand and folded recomputation; R§12.3
  `uses_proxy_data` cross-check and `PROVENANCE_INCOMPLETE`; R§4.6.2 funding-basis coherence;
  R§4.3.1 `PROCESSING_VERSION_MISMATCH` and `registry_schema` equality; `SURVIVORSHIP_UNKNOWN`,
  `CONTENT_HASH_UNAVAILABLE`, `GIT_UNAVAILABLE`; R§13.1 `warning_token` union; R§11 no-op
  transition; R§8.3 `seq` numbering; R§4/D8 unknown `schema_version`; R§10.3 sorted
  warnings/symbols/datasets; strict-parser record reads; R§14.5 parent chronology; the 8 uncovered
  `verify_registry` findings; the 11 uncovered query filters; and each remaining hashed field of
  R§18.1(1) individually (v1.1 covered 5 of ~20 — mutations dropping `content_hash`,
  `eval_start/end`, `data_start/end`, and `native_or_proxy`/`processing_version` from the payload
  all SURVIVED). Also untested at all: `annotate()`, `summary_table()`, `diverged()`,
  `status_history`, `suppress_cagr`.
- **R§20.8.7 (blocking, MW-A1).** `dirty_worktree` MUST match porcelain paths against the scope
  **patterns**, not an on-disk file listing — a **deleted** in-scope file was dropped entirely, so
  `dirty_worktree` read `False` with an empty `dirty_summary`, contradicting R§6.2's own `"D"` code.
- **R§20.8.8 (blocking, MW-A2).** The layering static scan is the *only* enforcement of R§12.2
  (ambiguity 6 is correct that the runtime direction is unimplementable). It MUST match
  `backtest.metrics` / `backtest.engine` in any form, including `from backtest import metrics [as
  m]` and relative `from .metrics import …`.
- **R§20.8.9.** `_extra_warnings` restricted to exactly `{"PROVENANCE_INCOMPLETE"}` (MW-A3);
  `funding_disabled=False` MUST NOT match a record with no `funding_mode` key (MW-A7);
  `descendants_of` sorted by `experiment_id` (MW-A8); `run_facts["intended_eval_start"]` on B1
  must be the evaluated-frame start, not the raw start (MW-A6).
- **R§20.8.10 (blocking, MW-A9).** The workspace baseline MUST pair its hash manifest with a
  **file-set** comparison for every in-scope directory. v1.1's manifest reported CLEAN after every
  mutation while stray files were being added, because a hash list cannot detect *extra* files.

## R§20.9 Findings REJECTED, with reasons

1. **MW-h's proposal to render a `NOT A RESEARCH RESULT` banner is ACCEPTED, but its framing that
   R§15 is at fault is rejected.** R§15 pins required tokens, not a template, and the
   implementation complied. The defect is in R§15's *sufficiency*, so R§20.11 extends it rather
   than recording an implementation defect.
2. **MW-m's proposal to extend `CODE_SCOPE_PATTERNS` to `*.yaml|*.json|*.toml` under
   `strategies/**` and `experiments/**` is DEFERRED, not adopted now.** The finding is correct in
   principle — a config-file-driven strategy would be invisible to both `code_fingerprint` and
   `strategy.params` — but the reviewer states it is not live (no such strategy exists), and
   widening the scope now would churn identity for zero present benefit while adding a data-file
   hashing path with no fixture to test it against. Recorded as **DEFERRED-005** in `docs/TODO.md`
   and as accepted limitation D13: **this MUST be closed before the first config-file-driven
   strategy is registered.** A `runtime_env` dict in `run_facts` IS adopted now (cheap, no
   identity impact).

## R§20.10 The v1.1 registry is discarded and regenerated

The five v1.1 records are **deleted before any commit** and the migration re-run from a clean
committed tree. This does not violate R§8.2 (retention) and is not the suppression of a failed
experiment:
1. all five are `experiment_type=pipeline_validation` re-executions of a frozen smoke pipeline —
   they contain **no research observation** and none was ever cited;
2. their `code_fingerprint` matches no state that exists anywhere, so they are unverifiable
   (R§20.7.3) — a record whose code state cannot be resolved is worse than no record;
3. their `content_hash` values use the superseded `col-buffer-v1` (R§20.8.4);
4. they were never committed, so nothing is being rewritten.

The regenerated records MUST carry a `notes` line stating that an earlier unverifiable
registration attempt was discarded, and why. **This paragraph is the permanent record of the
discard** — the deletion is disclosed here rather than left invisible.

## R§20.11 Rendering extensions (integrity MW-h, MW-i, MW-k)

`summary()` MUST additionally render: `reason_for_run`; `notes` in full; `recorded_via`
(R§20.3.3); the evaluated window per dataset alongside the metrics; `status_history` when
length > 1 (R§20.4.2); `divergence_detail` when `DIVERGED`; `n_configs_evaluated` when > 1;
`search_space_id`; and — for `experiment_type ∈ {pipeline_validation, infrastructure}` — the
literal banner `NOT A RESEARCH RESULT (experiment_type=<t>)` **above** any metric line.

Rationale, measured on the live records: `summary()` printed Window B1's `total_return: 0.9844`
and `sharpe: 0.4594` **with no window, no bar count and no caveat**, while the three mandatory
R§17.1 honesty statements lived only in `notes`, which nothing rendered.

`summary_table()` MUST gain warning-count, `recorded_via` and `reproducibility_status` columns.
`verify_registry()` MUST report `DIVERGED:<id>` — under v1.1 a `DIVERGED` verdict appeared in no
file, no table and no verification output.

`ResultSummary` MUST validate that `metrics["cagr"] is None` implies both `CAGR_SUPPRESSED ∈
result_warnings` and `"cagr_raw_suppressed" ∈ metrics` (MW-k: the renderer otherwise asserts a
suppression nobody declared).

## R§20.12 New accepted limitations

| # | limitation |
|---|---|
| D12 | `record_run`'s FAILED branch cannot cross-check datasets (no `BacktestResult` exists). Such records are `recorded_via="manual"` and carry `UNVERIFIED_MANUAL_RESULTS` if they carry results at all (R§20.3.4). |
| D13 | `CODE_SCOPE_PATTERNS` covers only `*.py` + `pyproject.toml`. A config-file-driven strategy would be invisible to identity. DEFERRED-005; MUST be closed before the first such strategy is registered (R§20.9.2). |
| D14 | `n_configs_evaluated` is self-reported and unenforceable. It converts an invisible omission into a visible lie, which is the most this architecture can do about an in-process sweep (D9). |
| D15 | The history hash chain (R§20.7.2) makes tampering *detectable*, not *impossible*. A determined actor who rewrites the whole chain still defeats it; git commits of `records/` are the second witness, which is why R§20.7.4 requires them. |
