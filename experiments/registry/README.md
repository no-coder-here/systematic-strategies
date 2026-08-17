# Experiment Registry (QR-INFRA-002)

This directory is the persistent, append-only store for the experiment
registry defined by `docs/experiment_registry_spec.md` (v1.3, FROZEN;
R§21 governs where it conflicts with R§1–R§20).

It is populated exclusively by `src/registry` (via `ExperimentRegistry`) and
by the drivers under `experiments/**` that call it (e.g.
`experiments/registry_migration/register_qr_smoke_001.py`). No test may
write here — tests use `tmp_path` (see `tests/registry/conftest.py`).

## R§21.10.1 — what this registry does and does NOT guarantee

**This registry records what it is told. It is a bookkeeping tool, not an
enforcement mechanism.** Reading a clean, well-populated registry is
evidence of good practice by whoever ran the experiments, not proof that no
misconduct occurred. In particular, stated plainly so no future reader
mistakes the registry's guarantees for more than they are (R§20.12/R§21.10):

- **It cannot prevent an unrecorded experiment.** Hiding a run requires only
  *not calling the API*. `run_and_register`/`record_run` make registration
  the path of least resistance for a cooperating driver — they are
  mitigations, not enforcement (D17). Nothing in this codebase currently
  stops a strategy from being backtested and evaluated entirely outside the
  registry.
- **It cannot verify a `search_space_id` grouping is honest.** Grouping
  records under a shared `search_space_id` (and the `n_configs_evaluated`
  count reported for each) is self-reported by the caller and unenforceable
  by the registry — a misleading label is not detectable from the data
  alone (D14/D18).
- **It does not fully enforce out-of-sample discipline.** R§20.6.1's
  `OOS_WINDOW_OVERLAP` check inspects ancestors (`lineage_of(parent)`) and
  every record sharing `(strategy_name, search_space_id)`, but an
  out-of-sample record that declares a *different* `search_space_id` **and**
  an unrelated parent evades this check entirely; only `OOS_RELABEL_OF`
  catches the identical-configuration variant of the same evasion (D19).
  Real enforcement of OOS discipline requires a protected-OOS layer this
  registry does not implement.

These are accepted, documented limitations of the current architecture, not
oversights to silently work around. Closing them is explicitly the subject
of future, separate work (a mandatory workflow-integration layer and a
protected-OOS layer), not this registry.

## R§20.10 — this directory is currently EMPTY of records

The five v1.1 records that previously lived here (`col-buffer-v1` content
hashes, `code_fingerprint` matching no committed state) were discarded
before any commit per R§20.10 of the v1.2 amendments: they contained no
research observation (`experiment_type=pipeline_validation`), used the
superseded `col-buffer-v1` content-hash method (R§20.8.4 bumps this to
`col-buffer-v2`), and were never git-committed, so nothing is being
rewritten. This paragraph, together with R§20.10 itself, is the disclosed
record of that discard.

**R§20.7.4 requires the R§17 migration to run from a clean, committed
worktree** so that `dirty_worktree is False` and `code_fingerprint`
resolves to git objects that exist. The real registry is therefore
materialized in a SEPARATE step, after this repair cycle's code changes are
committed, by running:

```
.venv/bin/python -m experiments.registry_migration.register_qr_smoke_001
```

from a clean, committed repo root (no uncommitted changes under the
`CODE_SCOPE_PATTERNS` scope). The migration accepts its registry root from
`--registry-root PATH` (argv) or `QR_REGISTRY_ROOT` (env) — see R§20.8.2 —
defaulting to this directory (`<repo_root>/experiments/registry`) when
neither is given, which is the correct invocation for materializing the
real registry.

## Layout

```
experiments/registry/
  records/EXP-<exact_hash prefix>-r<NN>.json   one immutable record per run, write-once
  history.jsonl                                append-only event log (status changes, artifacts, annotations)
  artifacts/                                    gitignored payloads (dataset snapshots, equity curves, ...)
  README.md                                     this file
```

`records/` and `history.jsonl` are **committed metadata** — small, human
-readable JSON, safe to diff and review. `artifacts/` is **gitignored**
(large/binary payloads such as copied dataset parquet snapshots and
per-record equity-curve series); those files are reproducible by re-running
the driver that produced them, or are lost-but-detectable via their
recorded `sha256` if that driver's source data has since changed (see
`docs/experiment_registry_spec.md` R§19 D7).

## What this is not

- Not a database. Plain files only (R§1.2).
- Not a place to hand-edit. Every record is write-once
  (`O_CREAT|O_EXCL`); the only way to change what a record says about its
  own status/artifacts/tags/notes is through `ExperimentRegistry`'s
  append-only API (`set_status`, `add_artifact`, `annotate`), which never
  rewrites the original record file.
- Not a substitute for the backtest engine's own accounting. This registry
  copies `BacktestResult` fields; it never recomputes a metric
  (`src/registry/backtest_adapter.py`, R§12.2).

## How to query it

```python
from pathlib import Path
from registry.store import ExperimentRegistry

reg = ExperimentRegistry(Path("experiments/registry"), repo_root=Path("."))
reg.list_experiments()                              # every record, every status (R§13.2)
reg.find_experiments(strategy_name="qr_smoke_001")   # R§13.1 filters
reg.failed_or_rejected()                             # survivorship-bias guard
reg.near_duplicates()                                # R§20.5.4 near-duplicate configs
reg.summary("EXP-xxxxxxxxxxxxxxxx-r00")
```

See `docs/experiment_registry_spec.md` (v1.3, R§21 governs where it
conflicts with R§1–R§20) for the full contract, and
`experiments/registry_migration/register_qr_smoke_001.py` for the
retrospective registration of the QR-SMOKE-001 Window A/B1/B2 runs (R§17).
