# Experiment Registry — SPECIFICATION v1.0 (QR-INFRA-002)

**Status: DRAFT (pending spec audit).**
Owner: Research Lead. Normative file. History preserved under `docs/spec_history/`.
Depends on: `docs/backtest_contract.md` v1.5.1 (FROZEN), `docs/data_contract.md` v1.4 (FROZEN).

## Revision history

| ver | date | cause |
|---|---|---|
| v1.0 | 2026-08-17 | initial draft for spec audit |

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
3. **Unrecorded multiple testing.** Duplicate configurations must be *detectable* so that "we
   tried this already" and "we tried 40 variants before this one" are answerable from the record
   rather than from memory.

### R§1.2 Non-goals (out of scope, MUST NOT be built)

- No database (no SQLite/Postgres). Plain files only.
- No web dashboard, no server, no daemon, no scheduler.
- No experiment orchestration, no parameter sweeps, no optimizer, no hyperparameter search.
- No MLflow/W&B-style tracking abstraction, no plugin system, no remote artifact store.
- No mutation of the backtest engine. The registry sits **above** the engine (R§2).
- No strategy research. No change to `strategies/qr_smoke_001` behaviour.

### R§1.3 Terminology

- **record** — one experiment's immutable creation snapshot (R§4).
- **event** — an append-only fact about a record created after the record (R§8.3).
- **folded view** — record + its events, applied in `seq` order (R§8.4). This is what
  `load_experiment` returns.
- **config hash** — content hash of the semantic configuration (R§5.1).
- **run_seq** — ordinal of a record within its config hash (R§5.2).

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
type adaptation). `src/registry` MUST NOT import `src/backtest/engine.py`.

**R§2.1.1 (blocking, testable).** A test MUST assert that no module under `src/backtest/` or
`src/data/` contains an import of `registry` (static source scan **and** a runtime check that
`sys.modules` contains no `registry*` entry after importing `backtest.engine` in a fresh
interpreter). Rationale: coupling the engine to the registry would make every accounting test
depend on registry state, and would let a registry defect corrupt accounting.

### R§2.2 Files

| path | contents |
|---|---|
| `src/registry/__init__.py` | public API re-exports |
| `src/registry/serialize.py` | R§3 canonical serialization |
| `src/registry/models.py` | R§4 schema dataclasses + validation |
| `src/registry/codeid.py` | R§6 code identity capture |
| `src/registry/store.py` | R§8/R§10/R§11/R§13 persistence + query |
| `src/registry/backtest_adapter.py` | R§12 `BacktestResult` → record |
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
whose `__repr__` includes a memory address (`<obj at 0x7f...>`) would produce a *different config
hash on every process*, and a parameter object with a lossy `__repr__` would produce the *same*
hash for genuinely different parameters. Both defects are invisible in a passing test suite.

**R§3.1.2.** A dict key collision with the reserved wrapper keys (`$nonfinite`, `$ts`, `$date`,
`$td_ns`) at any depth MUST raise `SerializationError` on encode. Rationale: without this, a
user-supplied parameter dict `{"$ts": "hello"}` decodes into a `pd.Timestamp` and silently
corrupts the record on round-trip.

### R§3.2 Timestamps

Every `pd.Timestamp` in a record MUST be tz-aware. A tz-naive timestamp raises
`SerializationError`. Rationale: a naive timestamp is ambiguous between UTC and local time; the
platform's data layer is UTC throughout and a silent local-time record would misstate a data
window by hours. `isoformat()` preserves nanosecond precision; decode uses `pd.Timestamp(s)` and
MUST reproduce the input exactly (round-trip test required, R§18).

### R§3.3 Round-trip requirement (blocking)

`decode(encode(x))` MUST equal `x` exactly for every supported type, where "exactly" means:
- floats: bitwise identical (`struct.pack` comparison, or `x != x` handling for NaN — NaN
  round-trips to a NaN, compared via `math.isnan`);
- timestamps: equal value **and** equal `tz` **and** equal nanosecond field;
- containers: same structure, element-wise exact.

---

## R§4 Record schema (`schema_version = "qr-infra-002-v1.0"`)

`ExperimentRecord` is a frozen dataclass. Field groups below are normative; **REQUIRED** means a
`ValidationError` if absent/empty at construction.

### R§4.1 Identity

| field | type | req | notes |
|---|---|---|---|
| `schema_version` | str | auto | constant above |
| `experiment_id` | str | auto | R§5.2; caller MUST NOT supply |
| `config_hash` | str | auto | R§5.1, 64-hex |
| `run_seq` | int | auto | R§5.2 |
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
| `frozen_spec_ref` | str or None | conditional | REQUIRED non-empty when `research_stage == "out_of_sample"` (R§14.5) |
| `tags` | tuple[str,...] | optional | free-form, sorted on store |
| `notes` | str or None | optional | |

### R§4.3 Code (`CodeIdentity`, see R§6)

| field | type | req |
|---|---|---|
| `git_commit` | 40-hex str or None | REQUIRED field, value MAY be None if not a git repo |
| `git_available` | bool | REQUIRED |
| `dirty_worktree` | bool | REQUIRED |
| `dirty_summary` | dict[str,int] | REQUIRED | counts keyed by porcelain status code, e.g. `{"M":1,"??":37}` |
| `untracked_code_files` | int | REQUIRED |
| `code_fingerprint` | 64-hex str | REQUIRED (never None) |
| `code_fingerprint_n_files` | int | REQUIRED |
| `code_scope_patterns` | tuple[str,...] | REQUIRED |
| `contract_versions` | dict[str,str] | REQUIRED | e.g. `{"backtest_contract":"1.5.1","data_contract":"1.4","registry_schema":"qr-infra-002-v1.0","data_processing_version":"qr-data-001-v1.3"}` |

### R§4.4 Data (`tuple[DatasetRef, ...]`, ≥1 REQUIRED)

| field | type | req | notes |
|---|---|---|---|
| `dataset_id` | str | REQUIRED | |
| `source_venue` | str | REQUIRED | |
| `field_type` | str | REQUIRED | e.g. `ohlcv_1h`, `funding` |
| `native_or_proxy` | `"native"`/`"proxy"` | REQUIRED | |
| `proxy_for` | str or None | conditional | REQUIRED non-empty when `native_or_proxy == "proxy"` (mirrors `DatasetProvenance.__post_init__`) |
| `processing_version` | str | REQUIRED | |
| `dataset_version` | str or None | optional | |
| `retrieval_date` | date or None | optional | recorded, **not hashed** (R§5.1.2) |
| `data_start` | tz-aware ts | REQUIRED | first timestamp actually used |
| `data_end` | tz-aware ts | REQUIRED | last timestamp actually used, inclusive; MUST be `>= data_start` |
| `symbols` | tuple[str,...] | REQUIRED | non-empty, sorted on store |
| `symbol_mapping` | str or None | optional | |
| `content_hash` | str or None | optional | hash of the underlying snapshot when the caller can supply one; hashed when present (R§5.1.2) |
| `notes` | str or None | optional | |

Record-level data fields:

| field | type | req | notes |
|---|---|---|---|
| `universe_policy` | str | REQUIRED | e.g. `"single_symbol_fixed"`; the as-of rule in words |
| `survivorship_safe` | bool or None | REQUIRED field | `None` MUST be permitted and means *unknown*, which is materially different from `False`; a `None` MUST render as `unknown` in summaries, never as "safe" |
| `uses_proxy_data` | bool | auto | derived = any dataset is proxy; if the caller supplies a contradicting value → `ValidationError` (R§14.7) |

### R§4.5 Strategy (`StrategyRef`)

| field | type | req |
|---|---|---|
| `name` | str | REQUIRED |
| `version` | str | REQUIRED |
| `params` | dict | REQUIRED (MAY be empty `{}`) — must be encodable under R§3 |
| `frequency` | str | REQUIRED |
| `target_execution_venue` | str | REQUIRED |

### R§4.6 Backtest configuration

`backtest_config: dict` — REQUIRED. When produced by the adapter (R§12) it is the **complete**
field dump of `BacktestConfig`: `initial_capital, frequency, fee_bps, slippage_bps,
execution_mode, execution_lag, funding_mode, funding_notional_basis, annualization_factor,
risk_free_per_period, mar_per_period, max_gross_leverage, compute_counterfactual`.

**R§4.6.1 (blocking).** The adapter MUST derive this dict by enumerating
`dataclasses.fields(BacktestConfig)`, not by listing field names literally. Rationale: if the
frozen contract is ever amended with a new config field, a hand-written list silently drops it
from the config hash, and two experiments differing only in that field collide as "reruns". A
test MUST assert the dumped key set equals the dataclass field name set.

### R§4.7 Results (`ResultSummary` or None)

`None` is REQUIRED to be permitted (a FAILED run has no results). When present:

| field | type | notes |
|---|---|---|
| `total_return` | float | |
| `cagr` | float or None | `None` when suppressed as meaningless (smoke spec §4.9); do not fabricate |
| `annualized_volatility` | float | |
| `sharpe` | float | may be NaN (§12.3 degenerate) |
| `sortino` | float | may be NaN |
| `max_drawdown` | float | |
| `avg_turnover` | float | |
| `annualized_turnover` | float | |
| `n_periods` | int | |
| `rebalance_count` | int | executed rebalances |
| `ruined` | bool | |
| `warnings` | tuple[str,...] | R§4.7.2 |
| `custom` | dict | R§4.7.1 |

**R§4.7.1 `custom`.** Strategy-specific metrics live here. Adding one MUST NOT require a schema
change. Keys MUST be `str`; values MUST be R§3-encodable. `custom` **IS** persisted and IS
compared for reproducibility (R§5.4) but is **NOT** part of the config hash (results never are).

**R§4.7.2 `warnings`.** Machine-generated, deterministic, sorted. The adapter (R§12) MUST emit a
warning string for each of the following when true, and MUST NOT invent others:
`ruined`, `leverage_breach`, `funding_gap_tolerance_suspicious`, `uses_proxy_data`,
`survivorship_safe is None`, `survivorship_safe is False`, `provenance_complete is False`,
`counterfactual_status != "COMPLETED"`, `unexecuted_rebalances` non-empty (with the count),
`drag_comparable is False`, `funding_modelled is False`, `dirty_worktree`.
Each string MUST begin with a stable machine token, e.g. `"RUINED"`, `"LEVERAGE_BREACH"`,
`"PROXY_DATA"`, `"DIRTY_WORKTREE"`, so queries can match on the token rather than prose.

### R§4.8 Artifacts

`artifacts: tuple[ArtifactRef, ...]` — MAY be empty. See R§9.

### R§4.9 Derived identity fields

| field | type | notes |
|---|---|---|
| `rerun_of` | str or None | `experiment_id` of the `run_seq == 0` record with the same `config_hash`; `None` iff `run_seq == 0` |
| `reproducibility_status` | enum | `"UNIQUE"` (run_seq 0) / `"REPRODUCED"` / `"DIVERGED"` (R§5.4) |
| `divergence_detail` | tuple[str,...] | non-empty **iff** `reproducibility_status == "DIVERGED"`; names the differing keys |

---

## R§5 Deterministic identity

### R§5.1 `config_hash`

`config_hash = sha256(canonical_json(payload)).hexdigest()` where `payload` is a dict built from
**exactly** these keys, and no others:

```
{ "schema_version", "experiment_type", "code", "data", "universe_policy",
  "survivorship_safe", "strategy", "backtest_config" }
```

- `code` = `{git_commit, dirty_worktree, code_fingerprint, contract_versions}`.
  `git_available`, `dirty_summary`, `untracked_code_files`, `code_fingerprint_n_files` and
  `code_scope_patterns` are recorded but **not** hashed (they are descriptions of the same code
  state that `code_fingerprint` already pins; `code_scope_patterns` is excluded only because it
  is a module constant — see R§5.1.3).
- `data` = list of per-dataset dicts **sorted by `(dataset_id, field_type, source_venue)`**,
  each containing `{dataset_id, source_venue, field_type, native_or_proxy, proxy_for,
  processing_version, dataset_version, data_start, data_end, symbols, content_hash}`.
- `strategy` = `{name, version, params, frequency, target_execution_venue}`.
- `backtest_config` = the full dict of R§4.6.

**R§5.1.1 Excluded, and why (normative).** The following are recorded but MUST NOT enter the
hash: `created_at`, `run_executed_at`, `experiment_id`, `run_seq`, `status`, `status_reason`,
`results`, `artifacts`, `warnings`, `notes`, `tags`, `hypothesis_id`, `parent_experiment_id`,
`reason_for_run`, `change_from_parent`, `research_stage`, `frozen_spec_ref`,
`retrieval_date`, `uses_proxy_data`.

Rationales for the non-obvious ones:
- **`results`** — a rerun that reproduces to 1e-16 would otherwise get a *different* config hash,
  destroying the very duplicate detection the hash exists for.
- **`created_at`** — an ID that depends on wall-clock time cannot detect duplicates at all. This
  is the explicit requirement of the work order.
- **`research_stage`** — the hash answers *"has this exact computation already been performed?"*
  A stage is a label about interpretation, not about the computation. Consequence, accepted
  deliberately: relabelling an identical computation `in_sample → out_of_sample` is flagged as a
  **rerun** rather than as a new experiment. That is the informative outcome: an "OOS" run whose
  configuration is byte-identical to a prior in-sample run is *not* an out-of-sample test, and
  the registry should say so loudly.
- **`uses_proxy_data`** — derived from `data`, which is already hashed. Hashing both would let an
  inconsistent pair produce a distinct hash.
- **`retrieval_date`** — see R§5.1.2.

**R§5.1.2 Data identity: content, not fetch time.** `retrieval_date` is excluded so that
re-downloading byte-identical data does not manufacture a new experiment. The risk this creates
is real and specific to this platform: **Hyperliquid `candleSnapshot` serves a rolling ~208-day
window**, so "the same dataset_id over the same window" can be *different bytes* at two points in
time. Mitigations, both normative:
1. `data_start`/`data_end` are the timestamps **actually used by the run**, taken from the
   executed frame — not the requested window. A rolling-window shift that changes the usable
   window therefore changes the hash.
2. `content_hash` is hashed when supplied, and the R§17 migration MUST supply it for every
   dataset it registers. A caller who cannot compute one leaves it `None` rather than
   fabricating it; `ResultSummary.warnings` need not flag this, but R§15's summary renderer MUST
   render a `None` content hash as `content_hash: unavailable`.

**R§5.1.3 `code_scope_patterns` excluded from the hash, but pinned.** The patterns are a module
constant (R§6.3). If they are ever changed, `code_fingerprint` changes for every subsequent run
anyway, so identity is not silently reused. They are recorded so an old fingerprint remains
*interpretable*. A test MUST assert that changing the scope patterns changes the fingerprint.

### R§5.2 `experiment_id`

```
experiment_id = "EXP-" + config_hash[:16] + "-r" + f"{run_seq:02d}"
run_seq       = number of existing records in this registry with the same config_hash
```

- Contains **no** time component.
- `run_seq` is registry-state-dependent, therefore an `experiment_id` is **local to one registry
  directory**; `config_hash` is the portable identity. Documented limitation, accepted.
- `run_seq > 99` MUST raise `RegistryError` rather than produce a colliding/wider id. Rationale:
  100 reruns of one identical configuration is a process failure, not a research need; silently
  widening the format would break lexicographic ordering (R§13.4).
- If the computed record path already exists, `record_experiment` MUST raise `RegistryError` and
  write nothing.

**R§5.2.1 Truncation.** 16 hex chars = 64 bits. Collision probability across a local registry of
even 10^6 records is ~2.7e-8. `config_hash` is stored in full and is the authoritative key; a
prefix collision between two *different* config hashes MUST be detected at record time (compare
the full hash of the existing `run_seq==0` record with the same prefix; mismatch →
`RegistryError`). Rationale: without this check, a prefix collision would silently be recorded as
a rerun of an unrelated experiment.

### R§5.3 The rule: rerun vs. new experiment (normative, quotable)

> **Same `config_hash` ⇒ the same experiment, re-executed.** It receives a new
> `experiment_id` (next `run_seq`), `rerun_of` pointing at the `run_seq == 0` record, and a
> `reproducibility_status` of `REPRODUCED` or `DIVERGED`.
>
> **Different `config_hash` ⇒ a genuinely new experiment.** Any change to code fingerprint,
> git commit, dirty flag, contract versions, dataset identity/window/symbols, universe policy,
> survivorship flag, strategy name/version/params/frequency/venue, or any `BacktestConfig` field
> produces a different `config_hash`.
>
> Reruns are recorded, never suppressed or deduplicated away.

### R§5.4 Reproducibility comparison

For `run_seq > 0`, compare against the `run_seq == 0` record of the same `config_hash`:

- Comparison operand: `canonical_json(encode(results))` of both records — the **entire**
  `ResultSummary`, including `custom` **and** `warnings`. Nothing is exempted. Rationale: the
  tempting exemption is `warnings`, on the grounds that they are "just labels"; but every warning
  token is a deterministic function of the result surface under a config hash that already pins
  the code and data, so a warning that changes between two runs of the same configuration is a
  real divergence (e.g. `counterfactual_status` flipping to `FAILED`) and must not be masked.
- `None` results on either side: if exactly one side has `results is None` →
  `DIVERGED` with detail `"results_presence"`. If both are `None` → `REPRODUCED`.
- Byte-identical canonical forms → `REPRODUCED`. Otherwise `DIVERGED`, and
  `divergence_detail` MUST list the differing top-level keys (sorted), plus for `custom` the
  differing sub-keys as `custom.<key>`.
- **Tolerance is exactly zero.** Rationale: the config hash pins the code fingerprint and the
  data window, and backtest contract §16 mandates bitwise determinism for identical inputs.
  A non-zero tolerance here would silently absorb a real non-determinism defect, which is
  precisely the class of bug this comparison exists to surface.
- **NaN handling (trap, pinned).** Comparison is on the *canonical string*, in which NaN is the
  literal `{"$nonfinite":"nan"}`. Therefore `NaN` vs `NaN` compares **equal**, whereas a naive
  float `==` comparison would report `DIVERGED` on every degenerate Sharpe. A test MUST cover a
  NaN-Sharpe rerun reporting `REPRODUCED`.
- `DIVERGED` is **not** an error. It is recorded and queryable (R§13.2). Escalation is a human
  decision, not the registry's.

---

## R§6 Code identity and the dirty worktree

### R§6.1 Why this section is unusually strict here

In this repository most implementation code — `strategies/`, `experiments/`, and most of
`tests/` — is **untracked**. `git rev-parse HEAD` therefore describes almost nothing about the
code that produced a result, and a clean-looking `git status --porcelain` filtered to tracked
paths is structurally incapable of noticing it. A prior work order was contaminated by exactly
this blind spot. Consequently:

**R§6.1.1 (blocking).** `code_fingerprint` is REQUIRED and never `None`, is computed from file
contents independently of git, and is part of the config hash. `git_commit` alone MUST NEVER be
presented as fully describing a run.

### R§6.2 Field derivation

- `git_commit`: `git rev-parse HEAD`, full 40-hex. On non-zero exit / missing git / timeout:
  `git_commit = None`, `git_available = False`, and `dirty_worktree = True` with
  `dirty_summary = {"git_unavailable": 1}`. Rationale: unknown VCS state MUST default to the
  *unsafe* value, never to "clean".
- `dirty_worktree`: `True` iff `git status --porcelain --untracked-files=all` produces at least
  one entry whose path is inside the code scope (R§6.3), **or** `git_available is False`.
  Untracked in-scope files count as dirty.
- `dirty_summary`: counts of in-scope entries keyed by the two-character porcelain XY code,
  stripped (`"??"`, `"M"`, `" M"` → `"M"`, `"A"`, `"D"`, `"R"`).
- `untracked_code_files`: count of in-scope `??` entries.
- `code_fingerprint`: `sha256(canonical_json([[relpath, sha256(bytes)], ...]))` over all
  in-scope files, `relpath` POSIX-style relative to repo root, list sorted by `relpath`. File
  bytes hashed raw (no newline normalisation).
- `code_fingerprint_n_files`: length of that list. MUST be `> 0`, else `RegistryError` — a
  zero-file fingerprint is the constant hash of `[]` and would make every experiment's code
  identity identical.

### R§6.3 Code scope (module constant `CODE_SCOPE_PATTERNS`)

Include: `src/**/*.py`, `strategies/**/*.py`, `experiments/**/*.py`, `scripts/**/*.py`,
`conftest.py`, `pyproject.toml`.
Exclude: any path containing `/__pycache__/`, `/.venv/`, `/artifacts/`, `/.git/`, `.pyc`,
and `experiments/registry/**` (the registry's own storage, which changes on every write and
would otherwise make the fingerprint self-referential and never reproducible).

`tests/**` is **deliberately excluded**. Rationale: a test-only edit does not change what a
backtest computes, and including tests would make every experiment's identity churn during
ordinary test development, destroying rerun detection. This is a judgement call and is recorded
as such in R§19.

### R§6.4 Injectability

`capture_code_identity(repo_root: Path, *, scope_patterns=CODE_SCOPE_PATTERNS,
contract_versions: dict) -> CodeIdentity`. `record_experiment` accepts a fully-formed
`CodeIdentity`, so tests construct one over a temporary fixture repo and never depend on the real
repository's git state. A test that captures identity from the live repo MUST assert only
*structural* properties (fingerprint is 64-hex, n_files > 0), never a literal hash.

---

## R§7 Provenance preservation

The adapter (R§12) MUST carry these fields from `DatasetProvenance` into `DatasetRef` without
loss: `source_venue, field_type, native_or_proxy, proxy_for, dataset_id, dataset_version,
processing_version, retrieval_date, symbol_mapping`. Any dataset whose provenance lacks a
REQUIRED R§4.4 field raises `ValidationError` naming the field and the dataset. Silent defaults
(e.g. `native_or_proxy = "native"` when unknown) are FORBIDDEN — that would let proxy data be
recorded as native, which CLAUDE.md prohibits.

`UniverseProvenance.survivorship_safe` maps to the record's `survivorship_safe`, preserving
`None` as *unknown*.

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

`status_reason` is REQUIRED (non-empty) for `FAILED`, `REJECTED`, `INVALID`.

### R§8.2 Retention (blocking)

There is **no** delete, purge, prune, archive-away or overwrite API, at any level. A record file
is write-once: `record_experiment` MUST open it with `O_CREAT | O_EXCL`. A defect or a caller
that attempts to rewrite an existing record path raises `RegistryError` before any write.
`verify_registry()` (R§11.9) detects post-hoc edits by comparing each record file's SHA-256
against the `record_sha256` captured in its `created` event.

### R§8.3 Event log

`history.jsonl`, append-only, one JSON object per line, stored form collapsed to a single line
(canonical form + `"\n"`). Line schema:

```
{ "seq": int, "at": {"$ts": ...}, "event": str, "experiment_id": str, "payload": {...} }
```

`seq` is `1 + (number of existing lines)`. Events:

| event | payload |
|---|---|
| `created` | `{"config_hash", "run_seq", "status", "experiment_type", "record_sha256"}` |
| `status_change` | `{"from", "to", "reason"}` |
| `artifact_added` | the encoded `ArtifactRef` |
| `annotation` | `{"note"}` or `{"tags_added": [...]}` |

Writes take an exclusive lock (R§10.2). A malformed/truncated final line MUST raise
`RegistryIntegrityError` on read, never be skipped. Rationale: skipping a corrupt line silently
resurrects a deleted-looking experiment or loses a status change.

### R§8.4 Folded view

`load_experiment(id)` = the immutable record with events applied in ascending `seq`:
`status`/`status_reason` take the last `status_change`; artifacts append; notes append;
tags union (sorted). The folded view also exposes `status_history: tuple[(at, from, to,
reason), ...]` beginning with the creation status. The on-disk record is never mutated.

---

## R§9 Artifacts

`ArtifactRef`: `{name, kind, path, sha256, size_bytes, recorded_at, description}`.

- `kind` ∈ `{"equity_curve","weights","trades","metrics","log","notes","report","other"}`.
- `path` is **repo-root-relative, POSIX**. An absolute path raises `ValidationError` (an absolute
  path makes the record non-portable and can leak a home directory into a committed file).
- At record time the file MUST exist; `sha256` and `size_bytes` are computed from it. Passing
  `allow_missing=True` permits a `None` sha256 and appends a `MISSING_ARTIFACT:<name>` warning.
- Large payloads MUST NOT be required in git. Artifacts written by our own drivers live under
  `experiments/**/artifacts/` (already effectively excluded from the registry's code scope) and
  under `experiments/registry/artifacts/`, which MUST be added to `.gitignore`. Registry
  *metadata* (`records/`, `history.jsonl`) IS committed.
- `verify_artifacts(id) -> dict[name, "OK"|"MISSING"|"MODIFIED"|"UNVERIFIABLE"]`. `MODIFIED`
  when the file exists and its hash differs; `UNVERIFIABLE` when `sha256 is None`.

---

## R§10 Storage layout and persistence

### R§10.1 Layout

```
experiments/registry/
  records/EXP-<hash16>-r<NN>.json     # write-once, stored form
  history.jsonl                       # append-only
  artifacts/                          # gitignored payloads (optional)
  README.md                           # what this directory is; regenerated by hand, not code
```

Default root: `<repo_root>/experiments/registry`. Every API takes an explicit `root` so tests use
`tmp_path`.

### R§10.2 Concurrency

All mutating operations acquire `experiments/registry/.lock` via
`os.open(..., O_CREAT | O_EXCL)`, released in a `finally`. On contention: retry a bounded number
of times, then raise `RegistryLockError`. A stale lock is **not** auto-broken (single local user;
auto-breaking risks interleaved writes to the append-only log). Documented limitation.

### R§10.3 Atomicity

The record file is written to `<name>.tmp` in the same directory, then `os.replace`d into place
after a successful `O_EXCL` reservation check. The `created` event line is appended **after** the
record file is durable. Consequence: a crash between the two leaves a record with no `created`
event — `verify_registry()` reports it as `ORPHAN_RECORD` rather than pretending consistency.

### R§10.4 Deterministic persistence (blocking)

Given identical inputs (including an injected `created_at`), two writes into two fresh registry
roots MUST produce **byte-identical** record files. Requires: `sort_keys=True` everywhere, sorted
symbols/tags/warnings/datasets, no `id()`/`repr()`-derived content, no set iteration order
(R§16.2), no locale/timezone dependence in formatting.

---

## R§11 API

Class `ExperimentRegistry(root: Path)`. All methods return records as folded views (R§8.4).

1. `record_experiment(*, experiment_type, research_stage, reason_for_run, code_identity,
   datasets, universe_policy, survivorship_safe, strategy, backtest_config, status,
   status_reason=None, results=None, artifacts=(), parent_experiment_id=None,
   hypothesis_id=None, change_from_parent=None, frozen_spec_ref=None, tags=(), notes=None,
   created_at, run_executed_at=None) -> ExperimentRecord`
2. `load_experiment(experiment_id) -> ExperimentRecord` — `KeyError` if absent.
3. `list_experiments() -> tuple[ExperimentRecord, ...]` — **all** records, all statuses,
   deterministic order (R§13.4). Takes no filter arguments at all (R§13.3).
4. `find_experiments(**filters) -> tuple[ExperimentRecord, ...]` — R§13.
5. `set_status(experiment_id, status, reason) -> ExperimentRecord` — appends
   `status_change`; `reason` REQUIRED non-empty for non-`COMPLETED`; a no-op transition
   (`from == to`) raises `ValidationError`.
6. `add_artifact(experiment_id, artifact) -> ExperimentRecord`
7. `annotate(experiment_id, *, note=None, tags=()) -> ExperimentRecord`
8. `children_of(experiment_id)`, `descendants_of(experiment_id)`, `lineage_of(experiment_id)`
   (root→self chain), `duplicate_groups() -> dict[config_hash, tuple[ids]]` (only groups of
   size ≥ 2), `diverged() -> tuple[...]`.
9. `verify_registry() -> tuple[str, ...]` — empty tuple means consistent; otherwise findings:
   `ORPHAN_RECORD:<id>`, `MISSING_RECORD:<id>`, `RECORD_MODIFIED:<id>`,
   `BAD_SEQ:<n>`, `DANGLING_PARENT:<id>`, `RUN_SEQ_GAP:<config_hash>`, `PREFIX_COLLISION:<...>`.
10. `summary(experiment_id) -> str` and `summary_table(records) -> str` — human-readable
    rendering (R§15).

`record_backtest_result(...)` — R§12, a module-level function in `backtest_adapter.py`.

---

## R§12 `BacktestResult` adapter

`record_backtest_result(registry, result, *, strategy, datasets, universe_policy, ...) ->
ExperimentRecord` extracts, with no manual field copying by the caller:

| record field | source |
|---|---|
| `backtest_config` | `dataclasses.fields(result.config)` dump (R§4.6.1) |
| `results.total_return` | `result.metrics["total_return"]` |
| `results.cagr` | `result.metrics["cagr"]`, unless `suppress_cagr=True` → `None` |
| `results.annualized_volatility/sharpe/sortino/max_drawdown/avg_turnover/annualized_turnover` | `result.metrics[...]` |
| `results.n_periods` | `len(result.net_return)` |
| `results.rebalance_count` | `int(result.rebalance_flag.sum())` |
| `results.ruined` | `result.ruined` |
| `results.warnings` | R§4.7.2, derived from the result surface + `code_identity.dirty_worktree` |
| `survivorship_safe` | `result.survivorship_safe` (caller override MUST match or raise) |
| dataset windows | caller-supplied `datasets`; the adapter MUST assert every dataset's `[data_start, data_end]` **contains** `result.equity_curve.index[0]`/`[-1]` for price datasets, else `ValidationError` |

**R§12.1.** `funding_notional_basis` recorded from `result.funding_notional_basis`; if
`result.funding_modelled is False` and the recorded basis is not the not-modelled sentinel, raise
`ValidationError` (R§14.8).

**R§12.2.** The adapter MUST NOT recompute any metric. It only copies. Rationale: a second
metric implementation above the engine is a second accounting authority, which CLAUDE.md
forbids. A test MUST assert that `registry`'s modules contain no arithmetic on returns/equity
(reviewable by inspection; the mechanical part is that `src/registry/**` imports no
`backtest.metrics`).

**R§12.3.** Registering a failed run: the caller uses `record_experiment` directly with
`status="FAILED"`, `results=None`, and `status_reason` = the exception type + message. There is
no `BacktestResult` to adapt. The registry MUST NOT require a result.

---

## R§13 Query semantics

### R§13.1 Filters (`find_experiments`)

`experiment_id`, `config_hash`, `experiment_type`, `status`, `research_stage`,
`strategy_name`, `strategy_version`, `hypothesis_id`, `parent_experiment_id`,
`dataset_id`, `source_venue`, `field_type`, `native_or_proxy`, `symbol`,
`funding_mode`, `funding_disabled` (bool), `execution_mode`, `uses_proxy_data`,
`survivorship_safe` (tri-state, `"unknown"` matches `None`), `ruined`, `tag`,
`warning_token`, `reproducibility_status`, `created_after`, `created_before`,
`has_artifacts`.

Semantics:
- Filters combine with **AND**.
- A filter value MAY be a scalar or a collection; a collection means **OR** within that filter.
- `dataset_id`/`source_venue`/`field_type`/`native_or_proxy`/`symbol` match if **any** dataset of
  the record matches. `symbol` matches exact string membership in `symbols` (no substring
  matching — substring matching would make `BTC` match `BTCDOM`).
- `funding_disabled=True` ≡ `backtest_config["funding_mode"] == "disabled"`.
- `warning_token=X` matches if any warning string starts with `X`.
- An unknown filter keyword raises `ValidationError`. Rationale: a typo'd filter that is silently
  ignored returns a *superset* silently — the worst possible failure mode for a query used to
  claim "we never tested this before".
- `created_after`/`created_before` are **inclusive** on both ends; both must be tz-aware.

### R§13.2 Convenience queries

`failed_or_rejected()` ≡ `status in {FAILED, REJECTED, INVALID}`. `duplicate_groups()`,
`diverged()`, `children_of()`, `descendants_of()`, `lineage_of()` per R§11.8.

### R§13.3 Anti-survivorship default (blocking)

`list_experiments()` accepts **no** parameters and returns every record regardless of status.
There MUST be no `include_failed`-style flag anywhere in the API, and no default filter that
excludes any status. Rationale: any default that hides failures reintroduces exactly the
survivorship bias the registry exists to prevent, and a default is what gets used in a hurry.
A test MUST assert that a `REJECTED` and an `INVALID` record both appear in
`list_experiments()`.

### R§13.4 Ordering

All list-returning methods sort by `experiment_id` ascending (lexicographic). This is
deterministic and independent of wall-clock ties. `run_seq` is zero-padded to 2 digits
specifically so that lexicographic order equals numeric order (R§5.2).

---

## R§14 Research-integrity invariants (all blocking, all `ValidationError` unless noted)

1. `parent_experiment_id`, when set, MUST resolve to an existing record → else `RegistryError`.
2. Self-parenting is rejected. A parent chain MUST terminate; a cycle (only creatable by a
   corrupted registry) is reported by `verify_registry()` as `DANGLING_PARENT`/cycle finding, and
   `lineage_of` MUST raise rather than loop forever.
3. `parent_experiment_id` set ⇒ `change_from_parent` non-empty.
4. `reason_for_run` non-empty, always.
5. `research_stage == "out_of_sample"` ⇒ `parent_experiment_id` set **and** `frozen_spec_ref`
   non-empty. Rationale: our process permits OOS examination only of a frozen specification
   derived from prior in-sample work.
6. `experiment_type == "alpha_research"` ⇒ `hypothesis_id` non-empty.
7. `uses_proxy_data` is derived; a contradicting caller value is rejected.
8. `backtest_config["funding_mode"] == "disabled"` ⇒ results/record MUST NOT assert funding was
   modelled (R§12.1).
9. `status != "COMPLETED"` ⇒ `status_reason` non-empty.
10. `results is not None` ⇒ `results.n_periods >= 1`.
11. Every `DatasetRef` with `native_or_proxy == "proxy"` ⇒ `proxy_for` non-empty.
12. `data_end >= data_start` for every dataset.

---

## R§15 Human-readable summary rendering

`summary(id)` MUST render, and MUST NOT omit: `experiment_id`, `status` (+reason),
`experiment_type`, `research_stage`, strategy name/version, the code line, the data lines,
funding mode, headline results, warnings, lineage, `reproducibility_status`.

Pinned rendering rules:
- code line: `commit <sha7|NONE> (CLEAN|DIRTY) fingerprint <fp12> (<n> files)`. When
  `dirty_worktree` is `True` the word `DIRTY` MUST appear; a summary that shows only the commit
  for a dirty run is a spec violation.
- `survivorship_safe is None` renders `survivorship_safe: unknown`, never `False`/`no`.
- proxy datasets render `PROXY(for=<x>)`.
- `cagr is None` renders `cagr: n/a (suppressed)`, never `0`.
- missing `content_hash` renders `content_hash: unavailable`.

---

## R§16 Determinism requirements

- **R§16.1** Two `record_experiment` calls with identical inputs into two fresh roots produce
  byte-identical record files and identical `experiment_id`.
- **R§16.2** No output may depend on `set`/`dict` iteration order, `PYTHONHASHSEED`, locale, the
  process's timezone, or file-system enumeration order. Sets are sorted by canonical form on
  encode; `glob` results are sorted before use.
- **R§16.3** `created_at` is an explicit, REQUIRED parameter — the registry never calls
  `datetime.now()` internally for a record's `created_at`. Callers in `experiments/` supply
  `pd.Timestamp.now(tz="UTC")`; tests supply fixed timestamps. Rationale: an internal clock call
  makes every determinism test unwriteable, which is how determinism tests end up inert.

---

## R§17 QR-SMOKE-001 retrospective registration

### R§17.1 Method (honesty constraint)

The original Window A/B1/B2 executions (2026-08-16/17) predate the registry. Their original code
state is **not** recoverable. Therefore the migration **re-executes** the frozen QR-SMOKE-001
pipeline offline at registration time and records the code identity *of the re-execution*, with
`run_executed_at` set to the re-execution time.

Each of the three records MUST carry a `notes` block stating, in words:
1. the record was created by re-executing the frozen QR-SMOKE-001 pipeline offline on the
   registration date;
2. the original executions' git commit and worktree state are not recoverable and are **not**
   claimed;
3. the Hyperliquid candle snapshot is a rolling window, so the persisted local snapshot is the
   data of record; `retrieval_date` and `content_hash` come from the persisted provenance
   sidecar / snapshot files.

The migration MUST construct providers `offline=True` and MUST NOT re-fetch. A test MUST assert
the migration module contains no `offline=False` and performs no network call (monkeypatched
network guard).

### R§17.2 Records to create (exactly five)

| # | id role | type | status | stage | parent | change_from_parent |
|---|---|---|---|---|---|---|
| 1 | Window A | `pipeline_validation` | COMPLETED | `exploratory` | — | — |
| 2 | Window A rerun | `pipeline_validation` | COMPLETED | `exploratory` | — | — (same config → `run_seq=1`, `rerun_of=#1`, expected `REPRODUCED`) |
| 3 | Window B1 | `pipeline_validation` | COMPLETED | `exploratory` | Window A | "Binance USDⓈ-M proxy prices for full 2020-2026 history; funding_mode=disabled (Hyperliquid did not exist for most of the window)" |
| 4 | Window B2 | `pipeline_validation` | COMPLETED | `exploratory` | Window B1 | "funding re-enabled with Hyperliquid-native funding over the contiguous-coverage window starting 2024-08-15" |
| 5 | Window B2-PRE | `pipeline_validation` | **FAILED** | `exploratory` | Window B2 | "same configuration as B2 with the evaluated frame starting 2024-01-01, before contiguous HL funding coverage" |

`research_stage` is `exploratory` for all five; `experiment_type = pipeline_validation` is the
discriminator that these are not research observations. Profitability of these runs is
meaningless and MUST NOT be presented as a result of interest.

**Record 5 is a genuine failure, executed for real**, not a fabricated example: a funding-enabled
run beginning before 2024-08-15 14:00Z raises `FundingDataError` because Hyperliquid BTC funding
has 84 gaps exceeding the 90-minute tolerance before that date, so no single `FundingCoverage`
record spans the frame. `status_reason` MUST be the actual exception type and message. Its
permanent presence in the registry is the point: it records a hard data boundary so it is not
re-attempted.

**Record 2 is a deliberate reproducibility rerun** and demonstrates R§5.3. Its expected outcome
is `REPRODUCED`; if it reports `DIVERGED`, the migration MUST still record it (never discard) and
the work order MUST report the divergence as a material finding.

### R§17.3 Data recorded per window

| window | datasets |
|---|---|
| A | HL `ohlcv_1h` BTC (native), HL `funding` BTC (native) |
| B1 | Binance UM `ohlcv_1h` BTC (**proxy** for Hyperliquid BTC-PERP price) |
| B2, B2-PRE | Binance UM `ohlcv_1h` BTC (proxy), HL `funding` BTC (native) |

`data_start`/`data_end` = the **evaluated frame's** first/last timestamps (for the failed record,
the intended frame boundaries, since no frame was evaluated — recorded as the requested window
with a note). `content_hash` = SHA-256 of the persisted parquet/snapshot file(s) backing each
dataset; if a dataset maps to multiple files, hash the canonical JSON list of
`[relpath, sha256]`. `universe_policy = "single_symbol_fixed:BTC"`;
`survivorship_safe` from `UniverseProvenance`.

`custom` metrics for these records MUST include: `funding_events_excluded`,
`funding_gap_tolerance_suspicious`, `max_gross_exposure`, `n_unexecuted_rebalances`,
`total_drag_return`, `drag_comparable`, `counterfactual_status`, `first_frame_signal`,
`n_raw_bars`, `n_frame_bars`. This demonstrates R§4.7.1 without a schema change.

Artifacts: each record references `experiments/qr_smoke_001/artifacts/summary.json` (kind
`metrics`) if present, plus a per-record equity-curve CSV/parquet written by the migration under
`experiments/registry/artifacts/` (gitignored).

---

## R§18 Mandatory tests and mutation proof

### R§18.1 Coverage areas (each MUST have at least one discriminating test)

1. deterministic ID behaviour (R§5.1, R§5.2) — including that `created_at` does **not** affect
   `config_hash`/`experiment_id`, and that each hashed field, changed one at a time, changes it.
2. parent/child lineage (R§11.8, R§14.1–3) — children, descendants, lineage chain, dangling
   parent rejected, self-parent rejected.
3. failed-experiment retention (R§8.2, R§13.3) — `FAILED`/`REJECTED`/`INVALID` present in the
   default listing; no delete API exists (assert by attribute scan of the public surface).
4. dirty-worktree handling (R§6) — dirty vs clean fingerprints differ; `git_available=False`
   forces `dirty_worktree=True`; a dirty record's summary contains `DIRTY`; zero-file scope
   raises.
5. provenance preservation (R§7) — every field survives a `DatasetProvenance` → record → disk →
   load round-trip; a missing REQUIRED provenance field raises and names the field; proxy
   without `proxy_for` raises.
6. parameter serialization (R§3) — full round-trip incl. NaN/±inf, tz-aware ts, `Timedelta`,
   nested dict/list, numpy scalars, `set`; tz-naive raises; unsupported type raises (no `str`
   fallback); reserved-key collision raises.
7. duplicate/rerun detection (R§5.2–5.4) — rerun gets `run_seq=1`/`rerun_of`; `REPRODUCED` on
   identical results incl. NaN Sharpe; `DIVERGED` with detail on a changed metric and on a
   changed `custom` sub-key; `duplicate_groups()`; `run_seq > 99` raises.
8. query/filter behaviour (R§13) — every filter exercised; AND across / OR within; unknown
   filter raises; `symbol` does not substring-match; inclusive date bounds.
9. `BacktestResult` registration (R§12) — real engine result registered; `backtest_config` key
   set equals `BacktestConfig` field names; warnings derived correctly; window-containment check
   raises when violated; failed run registered with `results=None`.
10. artifact references (R§9) — hash/size captured; `MODIFIED` detected after a byte change;
    `MISSING` after deletion; absolute path raises.
11. deterministic persistence (R§10.4, R§16) — byte-identical files across two fresh roots;
    `verify_registry()` clean; `RECORD_MODIFIED` detected after editing a record file; corrupt
    `history.jsonl` line raises.
12. layering (R§2.1.1) — engine/data do not import the registry.

### R§18.2 Mutation proof (non-negotiable)

For every mutation below: apply to the **source**, run the named target test, record
`BROKE` / `SURVIVED` / `VACUOUS`, restore, verify against the baseline manifest. `VACUOUS` means
the mutation provably cannot change behaviour on the fixture — it MUST be reported with the
measured discriminating-case count (e.g. "0 records with a NaN metric in the fixture"), and a
`VACUOUS` result requires the fixture to be strengthened until the mutation is discriminating,
or an explicit written justification.

| # | mutation | must break |
|---|---|---|
| M1 | include `created_at` in the `config_hash` payload | ID determinism test |
| M2 | include `results` in the `config_hash` payload | rerun-detection test |
| M3 | drop `code_fingerprint` from the `config_hash` payload | dirty-code identity test |
| M4 | drop one `BacktestConfig` field from the adapter's dump | R§4.6.1 field-set test |
| M5 | `run_seq` format `{:d}` instead of `{:02d}` | ordering test (R§13.4) |
| M6 | compare results with `math.isclose(rel_tol=1e-6)` instead of canonical bytes | `DIVERGED` test with a 1e-9 perturbation |
| M7 | compare results with raw float `==` (NaN-unsafe) | NaN-Sharpe `REPRODUCED` test |
| M8 | `list_experiments()` filters to `status == COMPLETED` | anti-survivorship test |
| M9 | unknown filter keyword silently ignored | unknown-filter test |
| M10 | `symbol` filter uses `in` substring matching | `BTC`/`BTCDOM` test |
| M11 | `dirty_worktree` computed without `--untracked-files=all` | untracked-code test |
| M12 | `git` failure yields `dirty_worktree=False` | git-unavailable test |
| M13 | record file opened `"w"` instead of `O_EXCL` | write-once test |
| M14 | `json.dumps` with `sort_keys=False` | byte-identical persistence test |
| M15 | `allow_nan=True` and no `$nonfinite` wrapper | NaN round-trip test |
| M16 | `SerializationError` replaced by `str(obj)` fallback | unsupported-type test |
| M17 | skip malformed `history.jsonl` lines instead of raising | corrupt-log test |
| M18 | `native_or_proxy` defaults to `"native"` when absent | provenance-required test |
| M19 | drop the `out_of_sample` ⇒ `frozen_spec_ref` check | R§14.5 test |
| M20 | `status_reason` optional for `REJECTED` | R§14.9 test |
| M21 | `config_hash` prefix collision treated as a rerun (skip full-hash check) | R§5.2.1 test |
| M22 | `data_start/data_end` taken from the requested window instead of the executed frame | R§5.1.2 test (rolling-window shift changes the hash) |

### R§18.3 Workspace integrity (repository rule, blocking)

Before any mutation cycle, write `docs/qr_infra_002_baseline.sha256` — a SHA-256 manifest of
**every** file in scope for this work order, **tracked and untracked**
(`src/registry/**`, `tests/registry/**`, `experiments/registry_migration/**`, this spec).
Record `git status --porcelain --untracked-files=all`. Verify the manifest **after every single
mutation**, not batched at the end. Prefer per-target-test runs over full-suite runs during
mutation work. An auditor MUST NOT return PASS if the workspace-integrity check fails.
Registry *data* written during tests MUST go to `tmp_path`; a test that writes into
`experiments/registry/` is a defect.

---

## R§19 Open decisions and accepted limitations

| # | decision | accepted consequence |
|---|---|---|
| D1 | `tests/**` excluded from `code_fingerprint` (R§6.3) | a test-only change does not create a new experiment identity; a *test* defect is therefore not visible in identity |
| D2 | `research_stage` excluded from `config_hash` (R§5.1.1) | relabelling an identical computation as OOS is flagged as a rerun — intended |
| D3 | `experiment_id` depends on registry state via `run_seq` | ids are local to one registry root; `config_hash` is the portable key |
| D4 | `config_hash` prefix truncated to 16 hex in the id | prefix collision explicitly checked (R§5.2.1) |
| D5 | stale lock not auto-broken (R§10.2) | a crashed process requires manual `.lock` removal |
| D6 | `retrieval_date` not hashed (R§5.1.2) | mitigated by executed-frame windows + `content_hash` |
| D7 | registry records committed to git; artifact payloads not | artifact availability is not guaranteed by git history — hashes make loss detectable |
| D8 | no schema migration machinery | a future `schema_version` bump requires a written migration note; readers MUST raise on an unknown `schema_version` rather than guess |
