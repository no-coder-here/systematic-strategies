---
name: project-qr-infra-002
description: QR-INFRA-002 experiment registry; spec v1.3 frozen, REGISTRY PASS WITH WARNINGS after the QR-INFRA-002-A closure; the two/three-hash identity design and why broad-but-thin test coverage failed twice
metadata:
  type: project
---

**FINAL STATUS 2026-08-17: REGISTRY PASS WITH WARNINGS**, after work order QR-INFRA-002-A (spec
v1.3 R§21, a deliberately narrow closure). All 4 production defects closed by independent
measurement; 14/14 per-field identity mutations and 13/13 known survivors go RED; **zero survivors
remain.** Registry suite 245 → 292; full suite 823 passed, 6 skipped. Residual non-blocking items
are QR-INFRA-002-B in `docs/TODO.md`.

**COMMITTED and migrated** (`607e4cd` code, `e0a2a5c` the five QR-SMOKE-001 records) in the
correct order — code first, then registration, per R§20.7.4.

> **SCHEMA IS NOW EXPENSIVE TO CHANGE.** `SCHEMA_VERSION = "qr-infra-002-v1.3"` and **five records
> exist** under `experiments/registry/records/`. The earlier ruling that a version bump was "safe
> because the registry was empty" is **no longer true** — a bump now re-hashes and orphans all
> five. Any future work needing new per-experiment fields should layer *above* the registry using
> existing fields (`hypothesis_id`, `search_space_id`, `n_configs_evaluated`, `frozen_spec_ref`,
> `parent_experiment_id`, `research_stage`, `run_facts`) plus adjacent files, not amend R§4.

Fourth work order: a local, auditable experiment registry so every research run is reproducible and
permanently recorded. Infrastructure only. Spec: `docs/experiment_registry_spec.md` **v1.3 FROZEN**
(v1.0–v1.2 snapshots in `docs/spec_history/`); `schema_version = "qr-infra-002-v1.3"`. Code:
`src/registry/**`, `experiments/registry_migration/`, `tests/registry/**`.

**Route to the verdict:** 1 spec audit (SPEC FAIL) → implementation → 2 parallel independent audits
(both REGISTRY FAIL) → 1 repair cycle → re-audit (REGISTRY FAIL, 20 survivors) → **stopped at the
work order's cycle limit and reported** → user authorized the narrow QR-INFRA-002-A closure → 1
focused re-audit (**PASS WITH WARNINGS**, zero survivors).

**At the FAIL stage the registry was already functionally correct but not regression-protected** —
the migration produced correct records, `verify_registry()` was clean, and every integrity mechanism
worked *when exercised*; ~20 behaviours simply had no test that could fail. Keep that distinction
available: "FAIL" on a mutation-coverage criterion does not mean "broken", and saying so plainly is
what let the user scope a one-cycle closure instead of a rewrite.

## The design decision worth keeping: two hashes, not one

`semantic_hash` (data + universe + strategy + backtest config, **no code**) answers *"have we
tested this configuration before?"* — multiple testing. `exact_hash` (= semantic + code state)
answers *"is this the identical computation?"* — reproducibility, and governs `experiment_id`.

I first wrote a single code-inclusive hash. The auditor showed it defeats duplicate detection
outright: editing *any* in-scope file — including the registry's own `store.py` — changes the
identity of every subsequent run, so `duplicate_groups()` is empty in practice, which is the normal
state of a research repo. Excluding code entirely is equally wrong: two runs of identical
parameters on different engine code are not the same computation. **A caller-declared "relevant
code scope" was rejected as gameable and unverifiable** — under deadline pressure the researcher
declares a narrow scope and the hash stops noticing what mattered.

Later the integrity reviewer found the *third* hash I had missed: after any data re-ingest the
`content_hash` changes, so **every previously-tested configuration reads as untested** — deflating
the multiple-testing count in the safe-looking direction. Hence `config_family_hash` (semantic
minus content hash, minus windows, minus `recorded_via`) for `near_duplicates()`.

## Why "broad but thin" coverage failed twice — the reusable diagnostic

Round 1: 143 tests, all 28 spec-table mutations BROKE. The auditor then ran **44 of its own and 29
survived.** Round 2 (repair): 245 tests, 28/28 plus 17 hand-picked areas BROKE — the auditor ran
~120 and **20 survived**, including 5 the amendment had named explicitly.

The shape: one test per mandated *area* passes an area checklist while leaving most *behaviours*
inside it undetectable. Two concrete mechanisms to watch for, both measured here:

1. **A test that varies two fields together detects only the conjunction.** Every hashed
   `DatasetRef` field was individually deletable from the payload with the suite green, because the
   single covering test changed `content_hash` **and** `eval_start` at once.
2. **A fixture can test the wrong layer.** The zero-warm-up boundary fixture went through
   `record_experiment` → `DatasetRef.__post_init__` and never reached
   `_check_window_containment`, so the adapter bound it was written to protect stayed unprotected.

**Do not accept "N areas, N tests" as coverage. Require one mutation per *behaviour*, and require
the implementer to state which mutations they did NOT run** — this implementer honestly declined to
claim exhaustive coverage (17 of ~40 sub-items), and the un-mutated areas were exactly where the
survivors were. That honesty is what made the gap findable; reward it.

## Registry-specific integrity lessons

- **Hiding a failure never requires deletion — only *not calling the API*.** Measured under v1.1:
  `record_run` caught `Exception`, not `BaseException`, so **Ctrl-C on a slow backtest left zero
  records and no complaint**. That is the accidental path, and it matters more than the fraudulent
  one. Fix: `BaseException` + register `INVALID` when the block exits without a result.
- **A guard that keys off a flag is only as strong as the flag's settability.** `recorded_via`
  gates every provenance cross-check — and remained settable by an external caller in one keyword
  (`_recorded_via="adapter"`), reconstituting proxy-as-native with no rendered caveat. Private-by-
  underscore is not private.
- **`INVALID → COMPLETED` laundering** left no rendered trace; folded-status filtering (added to
  stop the opposite laundering) enabled it. Fix: sticky irremovable `WAS_INVALIDATED`.
- **An unrendered field is an absent field.** Honesty notes, `status_history`, `divergence_detail`
  and `manual_results_justification` all existed and were printed nowhere; `summary()` showed
  Window B1's Sharpe with no window, no bar count and no caveat.
- **Sequencing error I own:** the first migration ran from an uncommitted worktree, so all five
  records pinned a `code_fingerprint` matching no state that existed anywhere, one day later.
  **Commit the code, then register.** A record whose code state cannot be resolved is worse than no
  record — which is why discarding and regenerating them (R§20.10, disclosed in the spec) was right
  and was *not* suppression of a failed experiment.

## Process notes

- Subagents launched with `isolation: worktree` land in a **fresh worktree at HEAD**, so untracked
  prior work is absent and they cannot commit. Both engineer rounds copied the baseline in by hand
  and I integrated with `rsync -a --delete`. Budget a integration step, and expect the spec file
  itself to be missing there if it is untracked.
- Running the two independent reviews **in parallel** (code auditor + integrity reviewer) produced
  almost disjoint finding sets from the same artifact — the code auditor found inert tests and
  absent APIs, the integrity reviewer found bypassable mechanisms via working exploit scripts.
  Neither would have found the other's. Reuse this pairing.
- Asking the integrity reviewer to *demonstrate* each claim with a throwaway script, and stating
  "unsupported assertions are not findings", is what made its report actionable.

## What the targeted closure (QR-INFRA-002-A) taught, beyond the earlier lessons

**A narrow, measured work order closed in ONE cycle what two broad cycles could not.** The
difference was that the spec section (R§21) was written as a *checklist with file:line locations and
the measured evidence for each defect*, and it declared the survivor list **CLOSED** with an explicit
"do not invent new mutation classes to inflate a count". Prefer this shape for any repair order.

- **`is`, not `isinstance`, for a capability check.** Adapter trust was gated on a private singleton;
  `isinstance` would be forgeable by subclassing. Worth stating in the spec because it is exactly the
  kind of detail an implementer "simplifies". The auditor forged ten variants (string, fresh
  instance, subclass, duck-type with `__eq__`/`__hash__`/`__bool__`, `copy`, `deepcopy`, the class
  object) — all rejected.
- **Underscore-private keywords are not private.** `_recorded_via="adapter"` in one public keyword
  bought full adapter trust. The fix was removing them from the signature entirely and adding an
  `inspect.signature` test asserting no parameter name contains `recorded_via`/`logged_at`, so a
  reintroduction fails a test rather than a review.
- **A per-call override of a wall clock is a backdating switch.** Determinism tests need an
  injectable clock; give it at the *constructor* level, never per record.
- **Tri-state beats a default for anything a reader will trust.** `n_configs_evaluated = 1` by
  default meant the registry invented multiple-testing information. Required-with-no-default plus
  `None` = UNKNOWN, rendered *always* (unlike a verified count), is the honest shape. Corollary:
  aggregates must report `total_is_lower_bound` rather than silently summing over unknowns.
- **A hash manifest is a change detector only if you regenerate it deliberately.** After I edited 5
  files it flagged exactly those 5 — which is the proof that no stray edit rode along.
- **Two spec self-contradictions of mine surfaced only under implementation:** R§21.7.1 demanded
  "REQUIRED with no default" *and* "omission raises ValidationError" (impossible in Python —
  `TypeError` is forced, and is stronger); and the schema-version string was stated three different
  ways in one document (v1.1 in R§4, v1.2 in code, v1.3 in the header). **When bumping a spec
  version, grep the document for every occurrence of the version literal** — this is the same
  stale-literal class as [[measure-dont-cite]], now four instances deep.
- **Post-audit changes need a cost check.** Reconciling `SCHEMA_VERSION` changes every record hash,
  so it was only safe because the registry was empty and no test pinned a literal `experiment_id`. I
  measured that (1 constant + 4 test literals) *before* deciding. Had records existed, the ruling
  would have been the other direction.
