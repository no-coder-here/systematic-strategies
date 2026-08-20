# Research Methodology — QR-METHODOLOGY-001

**Status: v1.6 — robustness stage simplified per user decision of 2026-08-18.**
v1.5 was reviewed (`robustness-reviewer`); v1.6 applies that review's three text-coherence
corrections and six warning fixes. The reviewer endorsed v1.5's substance — "the hard-enforced
column is intact", no new mechanism required. The corrections themselves are post-review.

**Enforcement philosophy (read this first).** Rules here are hard-enforced **only where a defect can
silently corrupt a research result**: registration, provenance, search-space accounting,
failed-result retention, lookahead/accounting correctness, and the protected-OOS rules. Everything
else — above all, whether a strategy is genuinely robust — is decided by **independent review of the
registered experiments**, which is visible and challengeable rather than mechanically proven. This
platform protects against **accidental bad research, not a researcher deliberately gaming their own
process** (M§7.1, M§16 L12).

Route: v1.0 → two independent reviews (`robustness-reviewer` METHODOLOGY FAIL 15 blocking;
`oos-evaluator` OOS PROTOCOL FAIL 10 blocking) → v1.1 repairs → user's authorised decisions of
2026-08-18 on **E1** (bootstrap historical protected OOS + immutable snapshots), **E2** (multiple
testing), **E3** (robustness as hard gate), **E4** (dependency buffer) encoded as v1.2 → narrow
scoped re-review (both FAIL: 5 blocking on E1/E4, 3 on E3) → **v1.3 targeted repair**.

The v1.2 → v1.3 repairs, all measured rather than reasoned:
- **M§9.1.2 keyed on `experiment_type`, not `research_stage`.** The stage-keyed version made the
  infrastructure PERMIT branch dead code — all five existing records are `pipeline_validation` +
  `exploratory` — so **zero days were sealable and the bootstrap could not run at all**.
- **M§9.9.1 recomputes the content hash from the canonical long-form sealed slice.** It was a
  caller-supplied string (`backtest_adapter.py:68`), so a re-fetched frame carrying the sealed hash
  passed every check. This was the one fail-**open** path.
- **M§9.9.2 total coverage** + **M§9.6.4 immutable `dependency_start`**, closing a second read of a
  burnt window warm-started from unsealed data.
- **M§9.6.10 `funding_coverage_end`** — E4 had implemented only the left side of a two-sided rule,
  leaving every funding-enabled OOS run unrunnable at its right boundary.
- **M§9.1.7** refuses any bootstrap once any `alpha_research` record exists.
- **M§7** was later simplified in v1.5 (see M§7.1): the formal anti-gaming machinery is withdrawn
  in favour of independent review of the registered experiments.

**Applies to:** all alpha research from QR-RESEARCH-001 onward, once frozen.

This document is normative. Where it says MUST, a violation is a defect, not a style preference.

---

## M§1 Scope

### M§1.1 Purpose

Define the workflow governing alpha research, and make the following hard by construction:

| failure mode | instrument |
|---|---|
| hide a failed experiment | mandatory registered path (M§4), append-only registry, retention (M§14) |
| understate configurations tested | search-space ledger, committed pre-declaration (M§5) |
| peek at protected OOS repeatedly | window guard + time-keyed burn ledger (M§9, M§10) |
| redefine a hypothesis after results | substantive-field hash, compared per run (M§3.4) |
| silently change dataset/universe | `config_family_hash` continuity across stage edges (M§2.4) |
| present exploratory results as OOS | stage model + config-continuity gate (M§2) |
| promote a fragile strategy | independent robustness review with a binding FAIL (M§7.3, M§7.4) |

### M§1.2 Non-goals (MUST NOT be built)

- No optimizer, sweep orchestrator, scheduler, dashboard or database. Files only.
- **No change to the frozen registry schema.** `SCHEMA_VERSION` is `qr-infra-002-v1.3` and five
  records exist under `experiments/registry/records/`; bumping it re-hashes and orphans all five
  (`src/registry/models.py:626` — readers raise on an unknown version). This methodology uses only
  fields the frozen schema already has, plus adjacent files.
  **Corollary (v1.0 defect):** no new record-level warning token may be invented.
  `RECORD_WARNING_PREFIXES` is a closed vocabulary and `store._assemble_warnings`
  (`src/registry/store.py:507-510`) raises `RegistryError` on an out-of-vocabulary token. Methodology
  signals travel via `tags`, `notes`, `annotate()` and the M§5 ledger.
- No security system. M§9's access control is procedural protection against *accidental* peeking.
  It does not defend against a determined repository owner.
- No universal numerical performance thresholds (M§12.2). Acceptable degradation is judged per
  strategy by the M§7.3 reviewer; v1.5 removed the procedural numeric minima entirely.

### M§1.3 Relationship to frozen artifacts

Sits **above** and does not amend `docs/backtest_contract.md`, `docs/data_contract.md`,
`docs/experiment_registry_spec.md`. Where this document appeared to redefine a frozen field's
semantics (v1.0 M§5.2 vs R§20.5.2), the frozen meaning wins — see M§5.2.

It mitigates registry deferred items R§21.10 **D17**, **D18**, **D19**. It does not close
D13/DEFERRED-005 (config files outside `CODE_SCOPE_PATTERNS`). See M§16.

### M§1.4 Terminology (defined once — v1.0 used two senses of "lineage")

- **hypothesis** — an economic claim, implementation-independent. `research/hypotheses/`.
- **configuration** — one concrete parameterisation of one implementation.
- **candidate** — an identified (implementation, parameters, universe policy) triple. Identifier
  format `CAND-[0-9]{3}`, declared in `research/candidates/`, unique program-wide.
- **hypothesis lineage** — **all** records with that `hypothesis_id`, i.e.
  `find_experiments(hypothesis_id=H)`, unioned over the supersession chain. This is the sense used
  in M§5.3, M§8.3, M§10 and M§14.
- **ancestor chain** — the `lineage_of` parent chain only (`src/registry/store.py:886`). Used
  **only** in M§2.3. v1.0 conflated the two; a researcher registering 40 sibling runs has an
  ancestor chain of length 2 while the hypothesis lineage is 41.
- **freeze** — an immutable committed manifest fully determining a candidate (M§9.2).
- **seal / reveal / burn** — M§9.1, M§10.

---

## M§2 The stage model

### M§2.1 Stages

| conceptual stage | registry representation |
|---|---|
| HYPOTHESIS | *no record* — a hypothesis permits no runs; it is a file (M§3) |
| EXPLORATORY / IN_SAMPLE / ROBUSTNESS / VALIDATION / OUT_OF_SAMPLE | the five `RESEARCH_STAGES` values |
| REJECTED / ACCEPTED | **a decision, not a run** — `research/decisions/` (M§12.4), plus `status="REJECTED"` for a rejected run. There is no `ACCEPTED` status in `STATUSES` and none may be invented. |

Measured `RESEARCH_STAGES` = `{exploratory, in_sample, robustness, validation, out_of_sample}`.

### M§2.2 What each stage permits

**HYPOTHESIS.** Reading, reasoning, aggregate market-structure inspection. No backtest of the
proposed signal. No contact with validation or protected windows.

**EXPLORATORY.** Free-form investigation on the IS window. Registration required for any run
producing a strategy performance number (M§4.3). Not required for pure data description.

**IN_SAMPLE.** Backtests, parameter search, variant comparison, filter selection. M§5 accounting
mandatory.

**ROBUSTNESS.** The M§7 protocol. Diagnostic. If a robustness result causes a change, M§5.4
applies.

**VALIDATION.** One evaluation per frozen candidate against criteria committed beforehand (M§8).

**OUT_OF_SAMPLE.** One evaluation by `oos-evaluator` of a frozen candidate (M§9–M§11).

### M§2.3 Stage transitions — ancestor requirement (blocking)

A run at stage S MUST have in its **ancestor chain** at least one `COMPLETED` record at each
prerequisite stage, all with the same non-empty `hypothesis_id`:

| stage | required ancestor stages |
|---|---|
| `exploratory` | none |
| `in_sample` | none |
| `robustness` | `in_sample` |
| `validation` | `in_sample` **and** `robustness` |
| `out_of_sample` | `in_sample` **and** `robustness` **and** `validation` |

A candidate MUST NOT go from exploratory directly to out_of_sample. This is strictly stronger than
R§14.5, which requires a parent, a resolving `frozen_spec_ref`, `parent.created_at <= created_at`,
and emits `OOS_WINDOW_OVERLAP` — but does not constrain the parent's *stage*.

`hypothesis_id=None` NEVER satisfies a prerequisite (M§4.5 requires it non-empty at every governed
stage).

### M§2.4 Stage transitions — configuration continuity (blocking; v1.0 gap)

The ancestor requirement alone is satisfiable by robustness-checking config A and then validating
config B.

- **Hard (blocking):** an `out_of_sample` run's `config_family_hash` MUST equal that of the
  `validation` ancestor **and** match the freeze manifest (M§9.2). Otherwise
  `StageTransitionError`. This is a protected-OOS rule and is enforced mechanically.
- **Soft (disclosed, reviewer-adjudicated):** where a `validation` run's `config_family_hash`
  matches no `COMPLETED` `robustness` ancestor, the divergence is recorded as the record tag
  `ROBUSTNESS_CONFIG_DIVERGENCE` **and** in the decision record, and surfaced to the M§7.3 reviewer,
  who decides whether what was stressed is the thing being validated. (Carrier named explicitly
  because an unrendered disclosure is an absent one.)

> **Why the validation edge is not hard (v1.5).** `config_family_hash` covers `strategy.params`,
> `backtest_config` and `universe_policy` (`src/registry/models.py:823-874`), so a *legitimate*
> robustness perturbation — 2x fees, a parameter neighbour, leave-one-out, a delay shift —
> **necessarily** produces a different hash. Requiring equality would have been satisfiable only by
> subperiod/time-LOO records (whose window fields are stripped from the payload) or by an
> unperturbed re-run, i.e. by the records that stress nothing. Measured: baseline, subperiod and
> time-LOO all hash `b4345ce87656`, while 2x-fee is `7bef389727d5`. A hard rule here would have
> rewarded the empty perturbations and rejected the real ones.

---

## M§3 Hypothesis record

### M§3.1 Location, identity, committedness

`research/hypotheses/<hypothesis_id>.md`, `hypothesis_id` matching `^HYP-[0-9]{3}$`.

R§14.6 requires `hypothesis_id` for `experiment_type="alpha_research"` but does not validate it.
This methodology adds: the runner MUST verify the file exists **and is git-committed**, via the same
mechanism the registry already uses for freeze manifests — R§20.6.2 `_verify_committed_blob`
(`src/registry/store.py:517-556`). It records `(commit, blob_sha)` and
`hypothesis_substantive_sha256` (M§3.4) in `run_facts`. Unresolvable or dirty ⇒ `HypothesisError`.

> **v1.0 defect (F3/B3):** v1.0 tested committedness via `git_available and not dirty_worktree`.
> Measured, `dirty_worktree` is computed only over `CODE_SCOPE_PATTERNS` (`*.py` + `pyproject.toml`,
> `src/registry/codeid.py:24-31`). Every `research/**` artifact is outside that scope, so the stated
> check was **permanently inert**. `_verify_committed_blob` is the real mechanism.

### M§3.2 Required content

YAML frontmatter; all keys required. Placeholder detection MUST **normalise** (casefold, strip
punctuation and whitespace) before comparison — v1.0's five-literal list rejected `tbd` but accepted
`TODO`, `N/A`, `-`, `none`, `to be determined`. Minimum-content floors apply:

```yaml
hypothesis_id: HYP-001
title: <one line>
created_at: <YYYY-MM-DD>
status: ACTIVE | SUPERSEDED | RETIRED
supersedes: <hypothesis_id or null>
economic_rationale: <>= 200 chars; MUST name who is on the other side and why they pay>
asset_universe: <universe *policy*, not a symbol list>
holding_horizon: <e.g. 4h-3d>
signal_concept: <>= 100 chars; conceptual, NOT final parameter values>
expected_direction: <sign, and on what>
failure_modes: <>= 2 list items>
evaluation_metrics: <primary metric first; the success criterion that binds M§12.3>
oos_attempt_budget: <integer; max protected-OOS evaluations for this hypothesis, M§10.5>
intended_stage: <the stage this record authorises next>
```

### M§3.3 Hypothesis is NOT implementation

No final parameter values; exact parameters MUST NOT be required before exploratory work. Many
configurations and implementations may share one `hypothesis_id`.

### M§3.4 Immutability and supersession (anti-HARKing)

Once any registered run cites a `hypothesis_id`, the **substantive fields**
(`economic_rationale`, `signal_concept`, `expected_direction`, `asset_universe`,
`holding_horizon`, `evaluation_metrics`) are frozen.

Enforcement (v1.0 recorded a hash but never compared it, and hashed the whole file so every typo
fix tripped it): the runner computes `hypothesis_substantive_sha256` over a **canonicalised subset
of exactly those six fields** and **refuses** (`HypothesisError`) when it differs from the value
recorded by the earliest run citing that id. Title and prose body may be edited freely.

Restatement requires a **new** `HYP-` record with `supersedes: <old_id>`, old set to `SUPERSEDED`.
The new id inherits the cumulative search count (M§5.5) and the burn history (M§10.2).

**Known residual (M§16 L9):** a researcher may author a fresh `HYP-` with `supersedes: null` that is
substantively the same idea, resetting the counters. `config_family_hash` matching (M§5.5.4,
M§10.2) is the only mechanical detector and it catches identical *configurations*, not identical
*ideas*.

---

## M§4 Mandatory registration — the research runner (mitigates D17)

### M§4.1 The sanctioned path

```
hypothesis + search space + robustness plan (all committed)
    -> research driver (experiments/<work_order>/...)
    -> run_research_experiment()          [src/research/runner.py]
    -> registry.run_and_register()        [frozen]
    -> run_backtest()                     [frozen]
    -> BacktestResult -> ExperimentRecord -> experiments/registry/
```

The runner MUST NOT compute, adjust or reinterpret any performance number. Its job is to **refuse**
runs violating M§2, M§3, M§5, M§7, M§8 or M§9, and to append to the M§5 and M§10 ledgers.

**Signature (required for testability — M§15):**
`run_research_experiment(..., research_root: Path)`. Production callers pass the repo `research/`.
Without an injectable root, the mandated tests must either write into the real tree (forbidden) or
monkeypatch module globals (which stops testing the production resolver).

### M§4.2 Enforcement (blocking)

The existing AST check (helper `_r21_3_offenders`, `tests/registry/test_r20_amendments.py:162`;
test node `test_R21_3_static_registration_enforcement_ast_based_named_allowlist:211` — v1.0 cited
the helper as a pytest node, which errors) is extended:

- **M§4.2.1** Scope widens from `experiments/**` to `experiments/**` + `strategies/**`.
- **M§4.2.2** Resolve `ast.alias.asname`: `from backtest.engine import run_backtest as _rb; _rb(...)`
  is measured GREEN today (`docs/TODO.md` QR-INFRA-002-B item 2). *Correction to v1.0:* the
  attribute form `engine.run_backtest(...)` is **already covered**
  (`tests/registry/test_r20_amendments.py:203-205`); only `asname` is uncovered.
- **M§4.2.3** Flag **any** direct call from `experiments/**` or `strategies/**` to
  `run_and_register`, `record_run`, `record_backtest_result` **or** `ExperimentRegistry.record_experiment`.
  > **v1.0 defect (both reviewers).** v1.0 exempted by `experiment_type`, which (a) omitted
  > `robustness` from a six-member `EXPERIMENT_TYPES` (`src/registry/models.py:73-75`), (b) is
  > orthogonal to the gates, which key on `research_stage` — so a driver declaring
  > `experiment_type="replication"` with `research_stage="out_of_sample"` bypassed the runner
  > entirely, and (c) is a runtime string undecidable statically (`experiment_type=CFG["type"]`).
  > Exempting by type is abandoned. `record_experiment` matters most: it needs no `BacktestResult`,
  > so hand-typed metrics from an unregistered session could be laundered in.
- **M§4.2.4** The only exemption is the R§21.3.1 **named-file** allow-list. Directory-prefix
  exclusions remain forbidden.
- **M§4.2.5** Assert that only `src/registry/backtest_adapter.py` names `_ADAPTER_CAPABILITY`
  (`docs/TODO.md` QR-INFRA-002-B item 1): importing it yields `recorded_via="adapter"` with
  hand-typed metrics and **no** `UNVERIFIED_MANUAL_RESULTS` caveat.

### M§4.3 What must be registered

Any run producing a **strategy performance number** — return, Sharpe, drawdown, hit rate,
turnover-adjusted PnL, equity curve — citable in any conclusion, at any stage, including
exploratory and including runs expected to fail.

Not required: data description producing no strategy performance number (`data_audit`).

**Registering only the winners is the most damaging possible act here**, because every honesty
mechanism downstream depends on the count in M§5.

### M§4.4 Uniform typing (v1.0 defect)

Every run in a hypothesis lineage MUST use `experiment_type="alpha_research"`; stage is expressed
by `research_stage`. v1.0's "the registry permits both" latitude for robustness runs was measured to
disable both relabelling detectors: `experiment_type` is inside the `config_family_hash` payload and
is not stripped (`src/registry/models.py:830, 867-874`), so identical configs typed
`alpha_research` vs `robustness` hash differently (`599907f6…` vs `c33a2614…`). That blinds
`near_duplicates()` (M§5.5.4) **and** `OOS_RELABEL_OF` (`src/registry/store.py:502-505`).

`alpha_research` also forces `hypothesis_id` non-empty (`src/registry/models.py:640`), which M§2.3
depends on.

### M§4.5 Honest limit (MUST be restated wherever the runner is documented)

D17 is **mitigated, not closed**. Hiding a run requires only not calling the API — an ephemeral
session leaves no trace. The AST checks cover committed code only. This reduces the accidental case
to near zero and makes the deliberate case leave a diff. No document may claim more.

---

## M§5 Search-space and multiple-testing accounting (mitigates D18)

### M§5.1 Declaration before search, committed

`research/search_spaces/<search_space_id>.md`, `^SS-[0-9]{3}$`, **git-committed before the first run
it governs**, `(commit, blob_sha)` recorded in `run_facts` (M§3.1 mechanism). v1.0's "declared in
advance" had no anchor: an uncommitted working-tree file can be written at any time.

```yaml
search_space_id: SS-001
hypothesis_id: HYP-001
predecessor_search_space_id: <SS-id or null>
declared_at: <YYYY-MM-DD>
declared_grid: <grid or variant list>
declared_n_configs: <integer or UNKNOWN>
```

Every `in_sample` and `robustness` run MUST carry a resolving `search_space_id`, else
`SearchSpaceError`.

### M§5.2 `n_configs_evaluated` semantics — frozen meaning preserved (v1.0 defect)

**v1.0 redefined this field as cumulative. That was an amendment to a frozen artifact (R§20.5.2:
"how many configurations were evaluated in the process that produced this record" — per-record) and
it broke the registry aggregation v1.0 then told reports to quote: `search_space_summary` *sums*
members (`src/registry/store.py:963-967`), so a 12-config sweep registering 1,2,…,12 would report
78.**

Therefore:
- The registry field keeps its **per-record** meaning.
- The **cumulative** figure is computed in the M§5 ledger, never in the registry field.
- Manual variants count. A configuration inspected and discarded without registration still counts.
- The field is required-with-no-default and tri-state; **`None` = UNKNOWN MUST be preserved, never
  coerced to 1.**
- Aggregates MUST reproduce the key `n_configs_evaluated_total_is_lower_bound`
  (`src/registry/store.py:967`) — v1.0 named it `total_is_lower_bound`, which does not exist.

**Ordering (v1.0 defect).** Any monotonicity or sequencing check MUST order by the store-stamped
`logged_at`, not the caller-supplied `created_at`. Backdating `created_at` yields only a
`BACKDATED_CREATED_AT` warning (R§20.7.1), so v1.0's check was defeated by registering the winner
first with a past `created_at` and `n_configs_evaluated=1`. The runner additionally **refuses** a
backdated `created_at`.

### M§5.3 Which count goes in a report

Every conclusion citing a backtest MUST cite the **cumulative count for the whole hypothesis
lineage** (M§1.4 sense), not the winning run's search space alone. A Sharpe reported without its
search count is an unfalsifiable claim.

### M§5.4 Two counters, so robustness is not penalised (v1.0 defect)

v1.0 said "every combination run counts" *and* "robustness is free when it diagnoses" — a
contradiction, since M§7's mandated cost/slippage stress changes `backtest_config` and therefore
`config_family_hash`. As written it made doing the mandated robustness work inflate the number the
researcher must report: a direct incentive against robustness.

- `n_configs_selected` — configurations from which a choice was or could be made. Multiplicity.
- `n_configs_diagnostic` — mandated M§7 perturbations of an already-chosen config, where no
  selection occurs.

Both reported. **If a diagnostic run leads to a change, it is reclassified as selected** and
`n_configs_selected` increments. Robustness that *selects* is optimisation wearing a lab coat.

### M§5.5 Search-space lineage — a new id MUST NOT erase history

- **M§5.5.1** A new `search_space_id` for a hypothesis that already has one MUST set
  `predecessor_search_space_id`. A second `null` for the same `hypothesis_id` ⇒ `SearchSpaceError`.
- **M§5.5.2** Cumulative hypothesis count = sum over the predecessor chain (and any superseded
  hypothesis chain) of each search space's `n_configs_selected`, computed **in the ledger**. Any
  UNKNOWN link ⇒ report a **lower bound**, never a number.
- **M§5.5.3** A cycle in `predecessor_search_space_id` ⇒ `SearchSpaceError`.
- **M§5.5.4** The runner MUST call `registry.near_duplicates()` and, when the run's
  `config_family_hash` matches a record under a different `search_space_id`, record
  `CONFIG_FAMILY_REPEAT:<other_id>` **as a record `tag` and in the search-space ledger**.
  > **v1.0 defect:** v1.0 specified this as a record-level *warning*. Measured,
  > `CONFIG_FAMILY_REPEAT` is not in the closed `RECORD_WARNING_PREFIXES`; emission raises
  > `RegistryError`, and `_extra_warnings` accepts only `PROVENANCE_INCOMPLETE`
  > (`src/registry/store.py:49, 410-415, 507-510`). The likely "repair" would have been a schema
  > bump, orphaning all five records. `tags`/`notes` are free-form parameters and carry it safely.

### M§5.6 Honest limit

D18 stands. `n_configs_evaluated` is self-reported; M§5.5.4 detects repeated *configurations*, not
unregistered ones.

---

## M§6 Lineage

### M§6.1 When a child is required (blocking)

Any change made **because of an observed result** MUST be a child: `parent_experiment_id` set,
`change_from_parent` non-empty (R§14.3), `reason_for_run` non-empty (R§14.4). Records are never
overwritten or deleted (R§8, append-only).

### M§6.2 `change_from_parent` must be specific

MUST name the change: `"lookback 48 -> 72"`, `"added 24h realised-vol filter, 80th pct"`. The
runner rejects a stop-list including `tuning`, `improvements`, `v2`, `update`, `fix`, `better`
(normalised comparison), and any value under 12 characters.

### M§6.3 `reason_for_run` taxonomy (enforced)

MUST start with one of `INITIAL:`, `SEARCH:`, `RESULT_DRIVEN:`, `ROBUSTNESS:`, `BUGFIX:`,
`RERUN:`, `VALIDATION:`, `OOS:`, else `LineageError`. `RESULT_DRIVEN:` is the honest label for
"we changed it because we saw the backtest", and is the one that must not quietly become `INITIAL:`.

---

## M§7 Robustness protocol

### M§7.1 Principle, and what is enforced how

Robustness asks whether the effect is **structurally stable**, not whether a point estimate is
large. A candidate that survives only at one parameter value, in one subperiod, at zero cost, with
one asset carrying the PnL, has not been shown to exist.

**Design decision (v1.5, authorised): robustness is adjudicated by independent judgement, not by
formal proof.** v1.2–v1.4 tried to make mislabelling a robustness experiment *mechanically
impossible* — evidence-distinctness maps, `config_family_hash` matching, per-check
perturbation-dimension containment tables, machine-evaluated criterion triples, a pre-evidence plan
lock. Three consecutive independent reviews defeated every version, and two of those mechanisms were
measured to **refuse legitimate evidence** while admitting the thing they targeted. That approach is
withdrawn.

The reason is a scoping judgement, and it should be stated plainly rather than rediscovered later:
**this platform protects against accidental bad research, not against a researcher deliberately
gaming their own process.** Formal anti-gaming machinery in the robustness stage bought no real
protection — a determined researcher satisfied every version — while imposing rules a careful,
honest researcher would trip over.

So the split is:

| enforced mechanically (silent corruption is possible) | adjudicated by review (judgement, visible either way) |
|---|---|
| registration of every substantive run (M§4) | which stresses are *relevant* to this strategy |
| provenance, dataset and universe identity (M§9.2, CLAUDE.md) | whether a stress was *severe enough* |
| search-space / multiple-testing accounting (M§5) | whether a pass criterion is economically meaningful |
| failed-result retention (M§14) | whether asset/period concentration is acceptable |
| lookahead and accounting correctness (M§13, backtest contract) | whether the surviving effect is structurally stable |
| protected-OOS rules (M§9, M§10, M§11) | the overall robustness verdict |

Anything in the left column can corrupt a research result **without anyone noticing**. Anything in
the right column is a judgement an independent reviewer can see and challenge, precisely because the
experiments are all registered and retained.

### M§7.1.1 The four hard requirements (blocking)

1. **Every robustness experiment is registered** through the normal path (M§4), with
   `research_stage="robustness"`, a resolving `hypothesis_id`, and `reason_for_run` prefixed
   `ROBUSTNESS:` (M§6.3). An unregistered stress test does not exist.
2. **Failed robustness experiments stay visible.** Never deleted, never overwritten, never omitted
   from a summary (M§14). A stress that made the candidate look worse is retained and reported
   exactly like one that did not. This is the most important requirement in M§7: the reviewer's
   judgement is only as good as the evidence it can see.
3. **Search accounting still applies.** A robustness run that leads to a change is a *selection* and
   increments `n_configs_selected` (M§5.4); purely diagnostic runs count under
   `n_configs_diagnostic`.

4. **A recorded `FAIL` verdict mechanically blocks promotion** (M§7.4). The *verdict itself* is
   judgement — that is the whole point of M§7.3 — but once recorded, its **effect** is enforced by
   the runner, not by anyone's diligence. An advisory gate is not a gate.

No other part of M§7 is machine-enforced: not which stresses are relevant, not whether a
perturbation was severe enough, not whether a criterion is meaningful.

### M§7.1.2 Robustness plan (lightweight)

Before robustness work, the candidate declares in `research/robustness/<candidate>.md`, committed:
which stresses will be run and why those are the relevant ones, and for each, roughly what outcome
would count as a failure and the reasoning behind it.

This is a **research document written for a reviewer**, not a machine-checked schema. No required
encoding, no criterion triple, no `fails_if` grammar, no pre-evidence lock review. Writing the
expected failure mode down before running is good practice and makes the reviewer's job possible; it
is not independently enforced, and this document does not pretend otherwise (M§16 L12).

### M§7.2 Stresses to test

The relevant stresses for most strategies, to be tested where applicable:

1. **Nearby parameter values.** Neighbours of every free parameter. Report the surface, not the peak.
2. **Subperiods and market regimes.** Contiguous subperiods, and regimes distinguished by an ex-ante
   observable. Regime cut-points must be trailing, not full-sample — a full-sample tercile boundary
   is the leak M§13.2.4 forbids.
3. **Higher transaction costs.** Report the cost level at which the effect vanishes; that number is
   more informative than a pass/fail.
4. **Higher slippage.** Same, for the slippage model.
5. **Execution delay.** Shift signal application later. An effect that dies under realistic delay is
   not tradeable. A large *improvement* under an *earlier* shift is evidence of lookahead and MUST
   be escalated as a suspected defect under M§13, not reported as a result — that half crosses into
   the hard column.
6. **Universe and individual-asset dependence**, where applicable. Leave-one-out; report the share
   of PnL from the largest contributor. For rule-selected universes, perturb the selection rule.
7. **Concentration in time.** Drop the best few windows and re-report. An effect living in three
   weeks is an event, not an edge.

Also expected where meaningful: an uncertainty estimate on the primary metric (block bootstrap for
autocorrelated returns) — M§11.1's `OOS INCONCLUSIVE` is defined in terms of a confidence interval,
so something must produce one; realised net exposure and beta to the market, so a "market-neutral"
candidate that is quietly long beta is visible; a sample-adequacy statement; and for
rolling/multi-asset strategies, estimation-window and pair/cluster perturbation (M§13.3).

**Not every stress applies to every strategy.** A single-asset strategy has no universe to perturb.
The plan states which apply and which do not, with reasons. Judging whether those reasons are good
is the reviewer's job (M§7.3), not a schema's.

### M§7.3 Independent review (blocking)

`robustness-reviewer` **independently inspects the actual registered experiments** — not a plan, not
a researcher-written summary — and returns **PASS / PASS WITH WARNINGS / FAIL**.

The reviewer:
- reads the registered robustness records directly, including the failed ones;
- checks that the stresses run are relevant and severe enough to be informative;
- may reject a pass criterion as economically meaningless or non-discriminating;
- may find that a relevant, applicable stress was never run;
- may find the effect fragile even when every declared criterion passed.

The reviewer is not the researcher and did not design or implement the strategy (standing rule: no
agent certifies its own work).

**Carrier (blocking).** The verdict, its reasoning and any warnings are recorded in a
**git-committed** decision record (M§12.4) carrying an explicit
`robustness_verdict: PASS | PASS_WITH_WARNINGS | FAIL` field, and the reviewing agent's identity.
The runner records the `(commit, blob_sha)` in `run_facts["robustness_verdict_ref"]` for every
`validation` and `out_of_sample` run. A gate the runner must enforce cannot read from an
unspecified location.

**An absent verdict is not a pass (blocking).** A candidate with no recorded robustness verdict does
not advance; M§2.3's `COMPLETED` robustness *record* is necessary but not sufficient.

### M§7.4 Robustness must be able to reject (blocking)

**A `FAIL` verdict blocks promotion to validation and to protected OOS.** Not a warning, not a note
— the candidate does not advance.

`PASS WITH WARNINGS` advances with the warnings recorded in the decision record and reproduced in
every subsequent report on the candidate.

Overriding a `FAIL` requires a written decision record **and** explicit user authorisation. It is
not the researcher's call, and not the Research Lead's alone.

**No universal Sharpe or drawdown thresholds** (M§12.2). Acceptable degradation is judged per
strategy, in context, by the reviewer.
## M§8 Validation

### M§8.1 Three windows, program-level

Windows are declared program-wide in `research/windows.json`, **not per candidate** — v1.0's
per-candidate windows let candidate B declare candidate A's IS region as B's validation window.

| region | who may see it | when |
|---|---|---|
| IS / exploratory | anyone, freely, repeatedly | exploratory → robustness |
| **validation** | research team, once per frozen candidate | validation |
| **protected OOS** | `oos-evaluator` only, once, after freeze | OOS |

Validation is deliberately weaker than OOS: it may be examined and may drive the final accept/reject
decision. It is not a second in-sample window.

### M§8.2 Before the validation run

Commit the criteria — primary metric, pass threshold, minimum evidence, intended decision under
each outcome — to `research/candidates/<CAND-nnn>.md` before the run. *(v1.0 said "the
freeze-candidate record", an artifact that does not exist until after validation in M§12.1's order.)*

### M§8.3 No retroactive validation windows (blocking; v1.0 gap)

v1.0's guard was purely forward-looking, so a team could explore 2024-01→2025-06 freely and then
declare 2025-01→2025-06 as "validation". At **both** window-declaration time and validation-run
time the runner MUST scan the hypothesis lineage for any non-validation record whose window
intersects the declared validation window, and refuse.

### M§8.4 Changing the candidate after seeing validation results

1. New child with `RESULT_DRIVEN:` (M§6.1).
2. Returns to **robustness** stage.
3. `n_configs_selected` increments (M§5.4).
4. The window is **partially observed** for this lineage. Every subsequent report MUST state
   `validation_evaluations` — a counter **maintained in `research/candidates/<CAND-nnn>.md` and
   recorded in `run_facts`** (v1.0 named the counter but gave it no producer or storage).
5. **The validation label MUST NOT be silently retained.**

### M§8.5 Runner enforcement

Any run at `exploratory`, `in_sample` or `robustness` whose window intersects a declared validation
window is refused (`ProtectedWindowError`). Window semantics per M§9.6.

---

## M§9 Protected out-of-sample

### M§9.0 What "protected" means (authorised definition — E1)

**Protected OOS means the data was not used for hypothesis development, parameter selection,
robustness selection, or validation of the strategy being evaluated.**

It does **not** mean the underlying market prices were never downloaded, observed, or used for
infrastructure or data-quality testing. Future-only OOS is **not** required. Live/forward OOS may
later serve as an additional, stronger validation layer, but it is not a precondition for a
protected-OOS claim.

Consequence (blocking, and the load-bearing limit on the bootstrap): **a strategy materially
derived from results already observed on a period MUST NOT claim that period as protected OOS.**
This is the M§10.1 burn rule generalised to observation that predates the seal.

### M§9.1 Window declaration and sealing

`research/oos/protected_windows.json`, append-only. Each entry:

```json
{"window_id":"OOS-001","evaluation_start":"<ISO-8601 UTC>","evaluation_end":"<ISO-8601 UTC>",
 "dependency_start":"<ISO-8601 UTC>","funding_coverage_end":"<ISO-8601 UTC>",
 "origin":"BOOTSTRAP|FORWARD",
 "sealed_at":"<ISO>","status":"SEALED","reveal_count":0,"reveals":[],
 "enumerated_freezes":["FREEZE-001"],"max_reveals":<int>,
 "expected_trades_statement":"<power/adequacy statement>",
 "prior_infrastructure_use":["EXP-... (experiment_type, stage, span)"],
 "snapshot":[{"dataset_id":"<id>","content_hash":"<sha256>",
              "content_hash_method":"<method>","processing_version":"<ver>",
              "source_venue":"<venue>","native_or_proxy":"<native|proxy>",
              "proxy_for":"<or null>","retrieval_date":"<YYYY-MM-DD>",
              "covers_start":"<ISO>","covers_end":"<ISO>",
              "path":"<repo-relative WRITE-ONCE artifact under research/oos/snapshots/>"}]}
```

- **M§9.1.1 No overlap (blocking).** A declaration overlapping any existing window is refused.
  Overlap is assessed on `[dependency_start, evaluation_end]`. v1.0 keyed everything on
  `window_id`, so re-declaring the same period one hour offset reset `reveal_count` to zero and
  manufactured unlimited "reserve" windows.
- **M§9.1.2 Sealing over prior research is refused; sealing over prior infrastructure use is
  permitted and disclosed (blocking; keyed on `experiment_type`).** At declaration the runner MUST
  scan **all** records via `list_experiments()`, using the record-side interval
  `[data_start, data_end]` across all its datasets (**not** `[eval_start, eval_end]`, which may be
  `None` and which `src/registry/store.py:471-472` silently skips), and:
  - **REFUSE** if any intersecting record has `experiment_type ∈ {alpha_research, robustness,
    replication}` **and** `research_stage ∈ {exploratory, in_sample, robustness, validation}`.
    This is the substantive protection: it stops a team exploring 2026-02→2026-08 and then sealing
    2026-04→2026-07.
  - **PERMIT, RECORD AND DISCLOSE** intersecting records whose `experiment_type ∈ {infrastructure,
    data_audit, pipeline_validation}`, **regardless of `research_stage`**. Those records MUST remain
    in the registry (never deleted — M§14) and MUST be enumerated in the window's
    `prior_infrastructure_use` and reproduced in every OOS report on that window.
  - A **strategy** whose development was materially informed by any intersecting result — of any
    experiment type — cannot use the window (M§9.0). Affirmed in writing in the freeze manifest;
    not machine-checkable (M§16 L10).

  > **Why keyed on type, not stage (corrects v1.2).** v1.2 refused on `research_stage` "regardless
  > of `experiment_type`", which made the PERMIT branch **dead code**. Measured: `RESEARCH_STAGES`
  > has no infrastructure-neutral value and `research_stage` is required, so every infrastructure
  > record must carry one of five stages — and all five existing records are
  > `pipeline_validation` + `exploratory`, satisfying the refusal *and* the permit, with refusal
  > written to dominate. Consequence, measured: **zero days were sealable and the bootstrap could
  > not be executed at all**, while T44 demanded contradictory outcomes for the same record class.
  > With this fix the blocked set is empty (no `alpha_research`/`robustness`/`replication` record
  > exists) and the reserve is the full persisted corpus.
- **M§9.1.3 Status transitions to `REVEALED` on first reveal.**
- **M§9.1.4** `enumerated_freezes` and `max_reveals` are committed at seal time (M§9.7).
- **M§9.1.5** Windows MUST carry a power/adequacy statement.

### M§9.1.6 Bootstrap reserve (one-time, authorised E1)

Because no alpha research has begun, a **one-time historical protected-OOS reserve** is authorised.
It MUST be established **before QR-RESEARCH-001 begins**, and MUST, in this order:

1. Define the reserve intervals (`origin: "BOOTSTRAP"`), each with its dependency buffer (M§9.6).
2. **Persist the exact underlying datasets locally as immutable snapshots** (M§9.9).
3. Record `dataset_id`, `content_hash`, `content_hash_method`, `processing_version`, source venue
   and native/proxy provenance for every snapshot.
4. Run the M§9.1.2 scan and record `prior_infrastructure_use`.
5. Seal the intervals and commit `protected_windows.json` (M§9.8).

After the bootstrap, further windows are created **forward** by waiting, or from data never
previously inspected. `origin` distinguishes the two, and every OOS report MUST state which.

**M§9.1.7 Hard precondition (blocking).** The runner MUST refuse **any** `origin="BOOTSTRAP"`
declaration — first or subsequent — if `list_experiments()` contains a record with
`experiment_type="alpha_research"`, whether or not it intersects the proposed window.

> **Why:** v1.2's legitimacy rested on the prose "Because no alpha research has begun", guarded only
> against a *second* bootstrap. Worked evasion: run six research work orders through 2027 on IS
> windows, registering everything honestly, then declare the **first** BOOTSTRAP over an untouched
> 2024 interval — clean scan, no test fires, affirmation signed, and the period had in fact been
> examined for months without registration. Measured, **zero `alpha_research` records exist today**,
> so adopting this costs nothing now and converts the load-bearing prose into a hard gate. It is
> also what makes M§9.0's unmechanisable affirmation (L10) acceptable: it forces the seal to precede
> all alpha research.

**The bootstrap is one-time.** A second bootstrap is a methodology change requiring user
authorisation (CLAUDE.md criterion 3) *in addition to* M§9.1.7.

### M§9.2 Freeze manifest (blocking, before any OOS run)

`research/freezes/FREEZE-<id>.md`, git-committed **before** the OOS run. `frozen_spec_ref` points at
it; R§20.6.2 verifies it as a **git-committed blob** and stores `frozen_spec_commit` /
`frozen_spec_blob_sha` (`src/registry/models.py:591-592`). *(v1.0 cited only R§14.5's sha256, which
alone does not make a post-hoc edit detectable — the spec says so at R§20.6.2's rationale. Citing
the weaker rule invites the weaker implementation.)*

MUST pin: implementation module + `git_commit` + `code_fingerprint`; every parameter; universe
policy (rule, resolved symbol set, as-of date, survivorship handling); signal definitions including
all estimation windows and normalisations; execution assumptions (fees, slippage, funding mode and
`funding_notional_basis`, currently `period_start` per DEFERRED-001); dataset/provenance policy with
native-vs-proxy per field; primary metrics and M§12.3 promotion criteria; `oos_window_id`;
`config_family_hash`; cumulative search count including UNKNOWN links.

Additionally, per E1/E4: `dependency_start` and the declared maximum lookback justifying it
(M§9.6.4); the snapshot `dataset_id`/`content_hash` set the run must verify against (M§9.9); and a
signed **M§9.0 affirmation** — an explicit written statement by the freezing researcher that this
strategy was not materially derived from any result already observed on the window's interval,
naming the `prior_infrastructure_use` records they are aware of. This affirmation is the human half
of a rule that cannot be fully mechanised (M§16 L10).

### M§9.3 Purity requirement (blocking)

The protected window MUST NOT have been used for hypothesis generation, parameter selection,
filtering, robustness selection, or validation.

Enforced in **both** time directions — v1.0 had only the forward half, which is why it did not
actually close D19:
- **Forward:** any non-`out_of_sample` run whose window intersects a SEALED window is refused.
- **Backward:** M§9.1.2's declaration-time scan, plus a pre-OOS scan over `list_experiments()`
  refusing (not warning) if any non-OOS record intersects the OOS run's window. This is
  grouping-label-free, unlike R§20.6.1, which inspects only ancestors and same-`search_space_id`
  records and is measured evadable (D19).

### M§9.4 Access control (procedural, default-deny)

- OOS results are written **only** to `research/oos/results/`.
- **Default-deny:** `oos-evaluator` is the *only* agent permitted to read `research/oos/results/**`
  or protected-window data. v1.0 named three forbidden agents, so any agent added later — and the
  human — were permitted by default.
- `burn_log.jsonl` carries **no outcome field** (M§10.2). v1.0 put `OOS PASS|FAIL|INCONCLUSIVE` in a
  file outside the restricted path that M§10.3 *requires* researchers to read — a direct leak, and
  with M§9.7's multiple candidates, a multi-bit read of the sealed window.
- Research Lead does not relay OOS numbers into a research thread except as the terminal decision.

### M§9.5 Pre-OOS gate

Verify: M§2.3 ancestors and M§2.4's hard OOS config-continuity edge; the M§7.3 robustness verdict is present, committed and not `FAIL` (M§7.4); `frozen_spec_ref` committed via R§20.6.2;
`protected_windows.json` and `burn_log.jsonl` **committed and blob-hashed, with both sha256 recorded
in `run_facts`** (M§9.8); every M§9.9 snapshot hash verifies **by recomputation over the canonical long-form sealed slice,
upstream of `to_engine_frame`** (M§9.9.1 — NOT over the wide engine frame) and every loaded timestamp is covered by a `snapshot[]` entry (M§9.9.2); `oos_window_id`
resolves and is `SEALED`; **the evaluated span == the declared `[evaluation_start, evaluation_end]`
exactly** — not `⊆`, which permitted twelve serial one-month peeks at one 12-month window — and the
loaded span == `[dependency_start, funding_coverage_end]` with `dependency_start` equal to the
window's sealed value (M§9.6.4, no freeze may extend it); the M§9.0 non-derivation affirmation is present in the freeze
manifest; hypothesis lineage consistency; M§10 burn checks; M§10.5 attempt budget.

### M§9.6 Dependency window vs evaluation window (blocking; authorised E4)

v1.0 left "window" undefined and v1.1 guarded `[min(data_start), max(data_end)]`, which made every
funding-enabled run at a seal boundary unrunnable — `docs/qr_smoke_001_spec.md:199-201` (rule W6)
*requires* funding to be loaded strictly wider than the price window on both sides. E4 resolves this
by splitting the concept.

**Two distinct windows, both sealed together:**

| | definition | who may touch it | metrics |
|---|---|---|---|
| **dependency window** `[dependency_start, evaluation_start)` | data a frozen strategy legitimately needs to *initialise* | frozen strategy only, at OOS time | **contributes none** |
| **evaluation window** `[evaluation_start, evaluation_end]` | the protected result | frozen strategy only, at OOS time | **all** OOS metrics |

- **M§9.6.1** Legitimate dependency uses are exhaustively: signal warm-up, rolling-statistic
  initialisation, hedge-ratio and covariance estimation, state initialisation, and
  funding/accounting initialisation (which is what W6 needs).
- **M§9.6.2 (blocking) OOS performance metrics begin strictly at `evaluation_start`.** No PnL,
  return, trade, drawdown or turnover from before `evaluation_start` may enter any reported OOS
  metric. The engine's evaluation frame MUST start at `evaluation_start`; the dependency buffer
  feeds indicator state only.
- **M§9.6.3 (blocking)** The dependency buffer MUST NOT be used for parameter selection, threshold
  choice, universe selection, or any strategy modification. It is consumed by an **already frozen**
  strategy, whose parameters were fixed in a committed manifest (M§9.2) before the buffer was ever
  read.
- **M§9.6.4 `dependency_start` is a sealed, immutable window field (blocking).** It is fixed at seal
  time and **MUST NOT be extended by any freeze, ever.** A strategy needing a longer buffer needs a
  different window. A freeze declaring a `dependency_start` earlier than the window's is refused.
  > **Why (corrects v1.2).** v1.2 said it was sealed *and* that "extending it is a new freeze and a
  > new OOS attempt", which licensed the extension it meant to forbid. Worked evasion: after an
  > `OOS FAIL`, author a fresh `HYP-` with `supersedes: null` (L9) and FREEZE-002 with
  > `max_lookback=150d`, pulling five extra months live. That span has no `snapshot[]` entry, was
  > never scanned by M§9.1.2, and is not guarded by M§9.6.5 — a second read of the same protected
  > window, warm-started from unsealed, unhashed, unscanned data. Note that a buffer change *alone*
  > cannot buy this: `config_family_payload` strips the window fields
  > (`src/registry/models.py:869-873`), so M§10.2 clause 2 refuses. The extension licence was the
  > enabling step.
- **M§9.6.5** The buffer is sealed *with* the window: `[dependency_start, funding_coverage_end]` is
  the interval used for M§9.1.1 overlap, M§9.1.2 prior-research scanning, M§9.9 snapshotting and the
  M§9.3 guards. Research workflows are excluded from the buffer exactly as from the evaluation
  window — otherwise the buffer becomes a legal side channel into sealed data.
- **M§9.6.10 Right-side funding allowance — `funding_coverage_end` (blocking; completes E4).**
  E4 supplied only a left-side buffer, but the W6 rule it was meant to dissolve is two-sided:
  `docs/qr_smoke_001_spec.md` requires funding loaded **strictly wider than the price window on
  both sides**, verified as `coverage_start <= T_0` **and** `coverage_end >= T_{n-1}` within one
  coverage record. Measured jitter is real (HL funding events land at `…:00.048`, `…:00.005`; price
  bars at `…:00.000`), so a terminal period fails as a matter of course otherwise.
  With only a left buffer and M§9.5's span equality pinned to `evaluation_end`, **every
  funding-enabled OOS run at the right boundary was unrunnable**: load to `evaluation_end` and W6
  fails; load one event further and `ProtectedWindowError` fires.
  Therefore `funding_coverage_end >= evaluation_end` is a sealed window field, bounded to one
  funding interval plus a stated jitter allowance. It is usable **only** for W6 coverage
  verification and terminal-period funding accounting — never for signal, selection, or any
  reported metric. Metrics still end at `evaluation_end` (M§9.6.2).

**Guard mechanics (retained from v1.1, and still necessary). These are M§9.6.6–M§9.6.9** — the E4
restructuring renumbered this section, so cite these identifiers, not the bare list positions:

- **M§9.6.6** The guarded span for *non-OOS* runs is `[min(data_start), max(data_end)]` across all datasets —
   **not** the eval window. Measured: `data_start`/`data_end` come straight from the caller's
   `dataset_windows` dict (`src/registry/backtest_adapter.py:81-82`) and are never derived from the
   loaded frame; `eval_start`/`eval_end` are pinned to the equity curve only for
   `field_type=="ohlcv"` (`src/registry/models.py:275-281`), may be `None` otherwise, and the
   existing overlap check skips `None` (`src/registry/store.py:471-472`). Without this, a research
   run normalises or vol-targets over sealed data while its equity curve stops at the boundary.
- **M§9.6.7** `data_start`/`data_end` MUST be **derived by the runner from the actual `market_data`
  index** passed to the engine, not accepted from the caller.
- **M§9.6.8** Mandatory **post-run** re-check against `result.equity_curve.index`; on intersection
  the run is recorded `INVALID` and raises. For an OOS run this check asserts the curve begins at
  `evaluation_start` (M§9.6.2), not merely that it lies inside the window.
- **M§9.6.9** Interval semantics are **closed on both ends**, matching the registry
  (`s <= pe and ps <= e`, `src/registry/store.py:481`; R§12.4 "BOTH bounds inclusive"), except
  `[dependency_start, evaluation_start)`, which is half-open by construction.

### M§9.7 Enumerate the candidate set before the first reveal (blocking; v1.0 gap)

All `freeze_id`s to be evaluated on a window MUST be enumerated and committed **before the first
reveal**, with a `max_reveals` budget. All enumerated candidates MUST be evaluated and **reported
together**.

Rationale: v1.0's burn rule fired on *modification*, but the leak is **selection**. Pre-declare ten
"distinct" hypotheses that are ten parameter regions of one signal, freeze all ten before any
reveal, evaluate serially, report the winner — no candidate was "modified because of" anything, and
every statement is true. Multiplicity adjustment across the enumerated set is M§17-E2.

### M§9.8 The seal files must be verifiable

`protected_windows.json` and `burn_log.jsonl` MUST be git-committed and verified via
`_verify_committed_blob`, with both sha256 recorded in every OOS record's `run_facts`. This needs no
schema change (`run_facts` is free-form).

Without it — v1.0's state — these were the only artifacts in the document neither hashed nor
verified: after an `OOS FAIL` one could delete the `reveals[]` entry, reset `reveal_count`, re-run
against a "pristine" window, and commit both edits as a single net-zero round trip. L4's "it leaves
a diff" was true and useless.

### M§9.9 Sealed data must be an immutable persisted snapshot (blocking; authorised E1)

A sealed OOS window's data MUST be a **locally persisted immutable snapshot**, sealed by
`dataset_id`, `content_hash`, `content_hash_method`, `processing_version` and source/provenance
(M§9.1's `snapshot[]`).

**A sealed OOS run MUST NOT re-fetch a rolling endpoint.** Hyperliquid `candleSnapshot` serves a
rolling ~209-day window, so a re-fetch silently changes the data underneath a sealed window — the
start date walks forward, warm-up is eaten, and native history degrades to Binance proxy, which
would falsify the freeze manifest's provenance section (M§9.2 item 6) *after the fact*.

- **M§9.9.1 Hash the canonical long-form slice, upstream of the engine frame (blocking).**
  The sealed `content_hash` and the OOS-time verification hash are BOTH computed at exactly one
  point in the pipeline:

  ```
  canonical normalized long-form dataset      (timestamp + symbol columns)
      -> slice to the exact sealed span [dependency_start, funding_coverage_end]
      -> hash_dataframe_content()             <-- the seal / the verification
      -> to_engine_frame()                    <-- wide pivot, AFTER hashing
  ```

  - **MUST NOT** call `hash_dataframe_content` on the wide engine frame. Measured: it requires
    long-format `timestamp`+`symbol` columns, but the object handed to the engine is a wide pivot
    (`src/data/base.py:214-215`) plus `Sequence[FundingEvent]`/`FundingCoverage`
    (`src/backtest/engine.py:484-485`). Literally implemented, v1.3 raised `ValueError` and **no OOS
    run could pass** — fail-closed, but the mechanism did not exist.
  - The hash MUST cover the **exact sealed slice**, never a whole larger source file. Measured
    hazard: today's registration hashes the entire persisted file
    (`experiments/registry_migration/register_qr_smoke_001.py:110-120`), which is why three
    different Binance spans share hash `64c05d5bf73f`. A whole-file hash cannot distinguish sealed
    windows and would silently certify the wrong data.
  - **Funding/event datasets** are sealed the same way: hashed in their canonical normalized
    representation over the exact sealed dependency/evaluation coverage, not as engine-side event
    objects and not as a whole file.
  - Snapshot artifacts (M§9.9.3) are therefore the **sliced window files**, so the artifact, the
    seal and the verification all denote the same bytes.
  - **Per-dataset span, inclusive endpoints.** Each dataset is sliced to **its own** sealed
    `[covers_start, covers_end]` from `snapshot[]` (M§9.9.2), **not** to the single window span —
    price data does not extend to `funding_coverage_end`. Endpoints are inclusive on both ends,
    matching M§9.6.9; this matters because funding timestamps carry sub-second offsets
    (e.g. `2023-05-12T00:00:00.048Z`).
  - **`FundingCoverage` is not sealed separately.** It is derived deterministically from the
    retrieved rows plus the pinned `MAX_FUNDING_GAP`
    (`src/data/hyperliquid/provider.py:479`), so hashing the sliced funding frame pins coverage
    too.
  - **A caller-supplied `content_hash` is never trusted for an OOS run** — `content_hash =
    window.get("content_hash")` (`src/registry/backtest_adapter.py:68`) is a string copied verbatim
    onto the record and never derived from data. Without recomputation a driver could hash the
    sealed artifact, then feed the engine a fresh rolling-endpoint pull while passing the sealed
    hash along: every check green on data that is not the sealed data, with
    `INCONSISTENT_CONTENT_HASH` silent because it sees one hash. M§9.6.7 applies this same
    "derive, don't accept" rule to `data_start`/`data_end`.
- **M§9.9.2 Total coverage (blocking).** Every timestamp of every dataset loaded by an OOS run MUST
  fall inside a verified `snapshot[]` entry (`covers_start`..`covers_end`) of the resolved window.
  **Uncovered data refuses.** Verifying the listed entries is not the same as requiring that
  everything loaded is listed.
- **M§9.9.3 Write-once storage.** Snapshot artifacts live under `research/oos/snapshots/`, a
  write-once location the ingest pipeline never writes to; a snapshot MUST NOT point at a live
  processed path (e.g. `data/processed/**`), which routine re-ingest rewrites in place — that would
  brick a sealed window irrecoverably once the ~209-day rolling source has aged.
  **They are NOT committed:** `docs/data_contract.md:476` D§8.2 (blocking) states "No market-data
  download may ever be committed" and `.gitignore` enforces it, so v1.2's "committed or otherwise
  integrity-pinned" was in direct conflict with a frozen contract. Integrity is pinned by the
  `content_hash` recorded in the committed `protected_windows.json` (M§9.8) plus M§9.9.1's
  recomputation. Because the artifacts are untracked, a `git diff` cannot evidence their integrity —
  the hash is the evidence (CLAUDE.md audit-integrity rule).
- **M§9.9.5** `processing_version` is verified at run time alongside `content_hash`; a mismatch
  refuses. Sealed datasets currently span `qr-data-001-v1.2` and `v1.3`, so drift is live, and an
  unverified sealed field is decoration.
- **M§9.9.4** `native_or_proxy` and `proxy_for` are pinned per dataset at seal time. A window sealed
  on native data may not be evaluated on proxy data, or the OOS claim silently changes venue
  (CLAUDE.md data-source policy).

---

## M§10 OOS reuse — the burn rule (critical)

### M§10.1 Reveal burns the period, permanently

Once protected-OOS results are revealed they are **observed data**.

**If the candidate is then modified because of those results, that period MUST NOT be treated as
protected OOS for the modified candidate — ever.** Not after further robustness work, not after a
rewrite, not under a new `search_space_id`, not under a new or superseded hypothesis id. It may be
used only as observed historical data, labelled `OBSERVED (revealed <date>)`.

### M§10.2 Burn ledger, keyed on time and configuration (v1.0 defect)

The runner appends to `research/oos/burn_log.jsonl`:

```json
{"window_id":"OOS-001","start":"<ISO>","end":"<ISO>","revealed_at":"<ISO>",
 "freeze_id":"FREEZE-001","hypothesis_id":"HYP-001","supersession_chain":["HYP-001"],
 "config_family_hash":"<hash>","strategy_name":"<name>","experiment_id":"EXP-..."}
```

**No outcome field** (M§9.4). Outcomes live only in `research/oos/results/`.

Refusal is keyed on **time-intersection with any previously revealed interval**, not on
`window_id`, and matches on **any** of:
- `hypothesis_id` ∈ any prior reveal's supersession chain;
- `config_family_hash` ∈ any prior reveal on an intersecting interval — **regardless of
  `hypothesis_id`**.

The second clause is the only reuse check in this document that does not rest on a self-declared
label. It is necessary because a fresh `HYP-` with `supersedes: null` walks past the chain check,
and measured, no registry warning catches it: `OOS_RELABEL_OF` fires only when the matching record
has `research_stage != "out_of_sample"` (`src/registry/store.py:502-505`), so two OOS records with
identical `config_family_hash` on the same window produce **no warning at all**.

### M§10.3 Program-level degradation

A window revealed to hypothesis A is not worthless for an unrelated hypothesis B, but it is no
longer pristine at the *program* level.

- `reveal_count > 0` MUST be disclosed in every subsequent claim on that window.
- A **usable reserve** MUST be maintained: at least one SEALED, unrevealed, non-overlapping window
  whose `evaluation_end <= now` **and** whose snapshot verifies under M§9.9.1.
  *(v1.0 required only a JSON entry with `reveal_count == 0`, satisfiable by declaring a window in
  2030 — which made the escalation branch permanently unreachable. A window whose data is not
  persisted and hash-verified is not a reserve.)*
- Consuming the last usable reserve requires **user escalation** (CLAUDE.md criterion 3).

### M§10.4 How new windows are created (revised per E1)

**Future-only OOS is NOT required.** Windows come from two sources, both `origin`-labelled:

- `BOOTSTRAP` — the one-time historical reserve of M§9.1.6, legitimate because the period was never
  used for hypothesis development, parameter selection, robustness selection or validation
  (M§9.0). Prior infrastructure/data-quality use does not disqualify it and is disclosed.
- `FORWARD` — created by waiting, or carved from data never previously inspected.

Both are enforced by M§9.1.1, M§9.1.2 and M§9.9, not by prose. A forward/live window may later
provide an additional, stronger validation layer on top of a bootstrap result; it is not a
precondition.

### M§10.5 Per-hypothesis OOS attempt budget (v1.0 gap)

v1.0 refused a second OOS run *on that window*, and nothing counted attempts across windows. So the
easiest evasion was to change nothing at all: `OOS FAIL` on OOS-001, wait for OOS-002 to elapse,
evaluate the byte-identical candidate, and report "OOS PASS, never previously revealed" — every
statement true, sequential multiple testing with zero accounting.

- The ledger is queried by hypothesis lineage across **all** windows.
- Every OOS report MUST state `oos_attempts` for the lineage.
- `oos_attempt_budget` is declared in the hypothesis record (M§3.2); exceeding it is refused.

---

## M§11 OOS result

### M§11.1 Outcomes

`OOS PASS` | `OOS FAIL` | `OOS INCONCLUSIVE`, judged against criteria frozen in M§9.2, using the
M§7.2 confidence interval. `OOS INCONCLUSIVE` is honest when the window is too short, trades too
few, or the CI spans the decision boundary — it MUST NOT be used to soften a `FAIL`. Note that
INCONCLUSIVE still burns the window (M§10.2), which is why M§9.1.5 requires a power statement at
seal time.

### M§11.2 The evaluator does not repair (blocking)

`oos-evaluator` MUST NOT modify the strategy, re-tune, re-run with different parameters, extend the
window, or re-run after a change. **A rerun-until-pass loop is the failure mode this document
exists to prevent.**

**BUGFIX exemption, objectively bounded (v1.0 defect).** v1.0's "justifiable without reference to
the sign of the result" was unenforceable — the *decision to go looking* is post-reveal and
sign-conditioned, and the engine hands the evaluator a menu of defensible pretexts on every run
(`FUNDING_GAP_SUSPICIOUS`, `CONTENT_HASH_UNAVAILABLE:<id>`, `PROCESSING_VERSION_MISMATCH:<id>`, …).
It also flatly contradicted M§10.2, which refuses *any* second OOS run on a burnt window, with no
exemption clause.

Replacement, machine-checkable: **a BUGFIX re-run is permitted iff the original record has
`status ∈ {FAILED, INVALID}` and `results is None`** — i.e. no number was produced. If any metric
was produced, the result stands. This is stated as an explicit exemption to M§10.2 and both branches
are tested.

### M§11.3 Permanence

Recorded permanently: registry record retained (`status="REJECTED"`, `status_reason` non-empty),
burn-ledger entry retained, decision record written. **Failed strategies are never deleted and never
omitted from a summary.**

> **v1.0 factual error.** v1.0 cited "the anti-survivorship default in `find_experiments`
> (R§13.2)". Measured, R§13.2 concerns `list_experiments()`, which takes no parameters;
> `find_experiments` (`src/registry/store.py:1094`) is a pure filter with **no** anti-survivorship
> property — `find_experiments(status="COMPLETED")` *is* the survivorship bug. The instruction
> pointed at the wrong function and would have produced the harm it forbade. Reporting MUST
> enumerate via `list_experiments()` and filter explicitly, disclosing what was excluded.

---

## M§12 Promotion

### M§12.1 The path

```
hypothesis -> exploratory/IS -> robustness -> validation -> freeze -> protected OOS -> decision
```

### M§12.2 No universal thresholds

No global Sharpe or drawdown bar: different strategy families have different achievable economics,
and a universal number either excludes good strategies or invites fitting to the number. This does
**not** license the absence of *any* criterion: the applicable M§7.2 stresses must be run, their
expected failure modes written down for the reviewer (M§7.1.2), and the M§7.3 verdict binds under
M§7.4. M§12.3 governs promotion criteria. Universality attaches to the *obligation to declare and be
judged*, never to the number.

### M§12.3 Pre-declared criteria, anchored (v1.0 defect)

v1.0 required only that a criterion be declared "before the run whose result it judges" — satisfied
by writing it after all IS and robustness results were known. Worked evasion: IS Sharpe 1.4, worst
robustness subperiod 0.6, declare "validation passes if Sharpe > 0.5". Compliant, zero discriminating
power.

- Criteria are **git-committed** before the run they judge (M§5.1 mechanism).
- A promotion criterion MUST be **no weaker than** the hypothesis record's frozen
  `evaluation_metrics` success criterion (M§3.2), or be logged as an explicit revision naming which
  results were already visible when it was written.
- **M§12.3 does not apply after an OOS reveal.** OOS criteria are frozen in M§9.2 and are not
  revisable, or M§11.1 is meaningless.
- **Classification of the search is recorded.** Whenever `n_configs_selected > 1`, the decision
  record (M§12.4) states whether the search is treated as **substantial** and justifies that
  classification. No universal number and no method is imposed (E2); what is required is that the
  judgement is written down and reviewable rather than made silently.
- **Multiple-testing accounting is mandatory; the correction method is not universal (E2).** All
  inspected configurations remain reflected through `search_space_id`, `n_configs_evaluated` and the
  M§5 lineage/search ledgers. Where more than one configuration was inspected, the
  robustness/selection assessment MUST explicitly account for the search burden; for **substantial**
  searches an appropriate multiple-testing-aware assessment is required (deflated Sharpe, White's
  reality check, block-bootstrap max-statistic, or an equivalent suited to the search structure —
  the choice may depend on the strategy and search geometry, and MUST be stated and justified).
  Reported as a lower bound when any link is UNKNOWN.
- **Never present the selected winner as though it were the only configuration tested.** This is the
  binding obligation; without it the entire M§5 apparatus is decorative — v1.0 built the count and
  let nothing consume it, so a candidate with 4,000 configurations faced the same bar as one with 3.

### M§12.4 Decision records

`research/decisions/<date>-<CAND-nnn>.md`: candidate, stage, evidence, criteria as committed,
outcome, decision (`PROMOTE` | `REJECT` | `RESEARCH FURTHER`), reasoning, dissent.

Every candidate reaching robustness gets one, **including every rejected one**. The rejected records
keep the search count honest.

---

## M§13 Multi-asset, stat-arb and rolling estimation

### M§13.1 The general rule

**Every estimate used at time T MUST use only information available by T under the backtest
contract's execution timing.** No exceptions, including for selection steps.

### M§13.2 Specific lookahead surfaces

1. **Pair / universe selection** — rolling point-in-time; record the selected set at each T.
2. **Cointegration testing** — test window ends at or before T.
3. **Hedge ratios / betas** — trailing only. A full-sample OLS hedge ratio is lookahead even when
   the signal is not.
4. **Normalisation / z-scoring** — trailing mean and standard deviation only. Most frequently missed,
   because it looks like preprocessing rather than modelling. Includes regime cut-points (M§7.2 item 2).
5. **Cross-sectional ranking** — within the cross-section available at T, using only assets listed
   and tradeable at T.
6. **Universe membership** — assets enter at their real listing date. 22 of 232 Hyperliquid assets
   have no Binance perp (`docs/TODO.md`), so universe depth correlates with "is this a
   Binance-listed major" — a selection effect that MUST be disclosed in every cross-sectional
   result, with `survivorship_safe` set accordingly.
7. **Covariance / risk-model estimation** — trailing only, including vol targeting and risk parity.

### M§13.3 Required evidence

Every multi-asset candidate's robustness plan MUST state how each of the seven surfaces is handled,
or why it does not apply. A generic "no lookahead" is not evidence.

**Note the failure mode M§13 does *not* cover:** these rules bind the *backtest*. M§9.6 exists
because the *research process* can consume future data without any backtest violating M§13.

No stat-arb strategy is implemented under this work order.

---

## M§14 Failed-result retention

1. No registry record is ever deleted or edited (R§8, append-only).
2. Every report presenting a candidate MUST state the number of registered runs in its **hypothesis
   lineage** (M§1.4) and the cumulative search count, including failures.
3. A summary listing only surviving candidates is non-compliant.
4. Abandoned hypotheses get `status: RETIRED` and a decision record; they are not deleted.
5. An interrupted run is still recorded: `record_run` registers **`FAILED`** with
   `status_reason="ABORTED: <Type>: <msg>"` on `KeyboardInterrupt`/`SystemExit`
   (`src/registry/backtest_adapter.py:328-336`), and `INVALID` when the block exits normally without
   a result (`:368-384`). *(v1.0 attributed both to `INVALID`.)* Interrupting a losing run does not
   erase it.

---

## M§15 Mandatory tests and mutation proof

One mutation per **behaviour**, not per area. All tests use the injected `research_root` (M§4.1);
none may write into the real `research/**` or `experiments/registry/`.

| # | behaviour |
|---|---|
| T1 | each M§2.3 row refused when its ancestor is missing — **one test per row** |
| T2 | `exploratory` → `out_of_sample` directly refused |
| T3 | right-stage ancestor with a different `hypothesis_id` does not satisfy the prerequisite |
| T4 | right-stage ancestor with `status != COMPLETED` does not satisfy it |
| T5 | `hypothesis_id=None` never satisfies a prerequisite |
| T6 | M§2.4 **soft edge**: a validation run whose `config_family_hash` matches no `COMPLETED` robustness ancestor is **ACCEPTED and the divergence disclosed** (tag + decision record, M§2.4) — a *refusal* implementation MUST go RED, because a legitimate fee/parameter/delay/LOO perturbation necessarily changes the hash while an unperturbed re-run does not |
| T7 | M§2.4: OOS run whose hash differs from the validation ancestor / freeze manifest refused |
| T8 | unresolvable `hypothesis_id` refused; **uncommitted** hypothesis file refused; committed one records `(commit, blob_sha)` |
| T9 | M§3.2 placeholders refused **after normalisation** — `TODO`, `N/A`, `-`, `none`, `to be determined`; and a 3-char `economic_rationale` refused (tests the property, not a five-literal list) |
| T10 | M§3.4: substantive-field edit after first run refused; **prose-body edit permitted** |
| T11 | M§5.2: `n_configs_evaluated=None` reaches the record as `None`, never 1 |
| T12 | M§5.2: sequencing evaluated on `logged_at`; a backdated `created_at` refused |
| T13 | M§5.5: second `null` predecessor refused; predecessor cycle refused |
| T14 | M§5.5.2: cumulative count with an UNKNOWN link reported as a lower bound |
| T15 | M§5.5.4: `CONFIG_FAMILY_REPEAT` recorded **as a tag and in the ledger**, and `record_experiment` does **not** raise `RegistryError` |
| T16 | M§5.4: a diagnostic run that causes a change is reclassified to `n_configs_selected` |
| T17 | M§6.2/M§6.3: stop-list `change_from_parent` and a bad `reason_for_run` prefix refused |
| T18 | M§7.1.1 item 1: a robustness run registered without `research_stage="robustness"`, without a resolving `hypothesis_id`, or without the `ROBUSTNESS:` `reason_for_run` prefix is refused |
| T19 | M§7.1.1 item 2 **retention**: a failed/adverse robustness record is retained, is returned by an unfiltered enumeration, and appears in the candidate's summary — a reporting path that enumerates via `find_experiments(status="COMPLETED")` (the survivorship bug, M§11.3) MUST go RED |
| T20 | M§7.1.1 item 3: a robustness run that causes a candidate change increments `n_configs_selected`, not `n_configs_diagnostic` (M§5.4) |
| T57 | M§7.2 item 5, lookahead half (hard column): a large improvement under an *earlier* signal shift escalates as a suspected defect under M§13 and is not reportable as a result |
| T58 | M§7.3: the robustness verdict is recorded in the decision record with its reasoning, and the reviewer identity is not the implementing agent (no self-certification) |
| T59 | M§7.4 **the gate bites**: a recorded `FAIL` verdict blocks promotion to validation AND to protected OOS — assert the promotion attempt is refused, not merely that the verdict was written down |
| T60 | M§7.4: `PASS WITH WARNINGS` advances, and the warnings are reproduced in the candidate's subsequent report; an override of a `FAIL` without both a decision record and user authorisation is refused |
| T61 | M§7.3 carrier: a `validation`/`out_of_sample` run with **no** recorded robustness verdict refused; an uncommitted verdict record refused; `run_facts["robustness_verdict_ref"]` records `(commit, blob_sha)` |
| T62 | M§2.4 soft edge: the `ROBUSTNESS_CONFIG_DIVERGENCE` tag is actually emitted when the validation hash matches no robustness ancestor — an implementation that adjudicates silently MUST go RED |
| T63 | M§5.1 (hard column, pre-existing gap): an `in_sample` or `robustness` run with no resolving `search_space_id` raises `SearchSpaceError` |
| T64 | M§11.1: an OOS verdict of PASS on a window with no confidence interval or below the declared power/adequacy statement is refused or forced to `INCONCLUSIVE` |
| T21 | M§8.3: validation window declared over already-explored data refused (retroactive scan) |
| T22 | M§8.5: IS run intersecting a validation window refused |
| T23 | M§9.1.1: overlapping window declaration refused |
| T24 | M§9.1.2: sealing a window over already-explored data refused |
| T25 | M§9.1.3: `status` becomes `REVEALED` on first reveal |
| T26 | M§9.6.9 / M§9.3 forward: non-OOS run intersecting a SEALED window refused; one bar outside accepted — **closed-interval semantics per M§9.6.9, both sides tested** |
| T27 | M§9.3 backward: OOS run refused when a prior non-OOS record intersects, **with an unrelated parent and a different `search_space_id`** (the measured D19 evasion) |
| T28 | M§9.6.6: run whose `eval` window is clear but whose `data` span reaches into a seal refused |
| T29 | M§9.6.7: caller-supplied `data_start`/`data_end` narrower than the actual `market_data` index refused |
| T30 | M§9.6.8: post-run equity-curve intersection records `INVALID` and raises |
| T31 | M§9.5: run window strictly inside the declared window refused (`==` required) |
| T32 | M§9.5/M§9.8: uncommitted or modified `protected_windows.json` / `burn_log.jsonl` refused; both sha256 in `run_facts` |
| T33 | M§9.2: `frozen_spec_ref` uncommitted refused (R§20.6.2, **not** `dirty_worktree`) |
| T34 | M§10.2: reuse refused via supersession chain; **and** via `config_family_hash` under an unrelated fresh `HYP-` with `supersedes: null` |
| T35 | M§10.2: reuse refused on a **re-declared overlapping window with a new `window_id`** |
| T36 | M§10.5: attempts counted across windows; budget exceeded refused |
| T37 | M§10.3: reserve must be elapsed **and** ingested — a 2030 window does not satisfy it; consuming the last usable reserve refused with an escalation instruction |
| T38 | M§10.2: ledger appended exactly once per OOS run, including on `OOS FAIL`; and `burn_log.jsonl` contains **no** outcome field |
| T39 | M§11.2: BUGFIX re-run permitted iff `status ∈ {FAILED, INVALID}` **and** `results is None`; refused when any metric was produced |
| T40 | M§4.2.2: aliased import flagged; M§4.2.1: rogue driver under `strategies/**` flagged |
| T41 | M§4.2.3: direct `record_experiment` / `record_backtest_result` from `experiments/**` flagged regardless of declared `experiment_type` |
| T42 | M§4.2.5: a module other than `backtest_adapter.py` naming `_ADAPTER_CAPABILITY` flagged |
| T43 | M§12.3: promotion criterion weaker than the frozen hypothesis criterion refused unless logged as a revision |
| T44 | M§9.1.2 keyed on **type**: sealing refused over an intersecting `alpha_research`/`robustness`/`replication` record at each of the four research stages (one row per stage); sealing **accepted** over an `infrastructure`/`data_audit`/`pipeline_validation` record **at any stage, including `exploratory`** — use the five real records as the fixture, and record `prior_infrastructure_use`. A stage-keyed implementation makes the accept branch unreachable and MUST go RED. |
| T45 | M§9.1.2: `prior_infrastructure_use` is non-empty when the seal permitted intersecting infrastructure records, and is emitted in the ledger's OOS disclosure block (the same carrier as T56 — no report system exists to refuse, so the obligation attaches to the emitted block) |
| T46 | M§9.1.7: BOOTSTRAP accepted while **zero** `alpha_research` records exist; refused once **any** exists — **first and second bootstrap alike**, intersecting or not. (v1.2 guarded only the second, and as a conjunction, so a second bootstrap with zero alpha_research records was permitted.) |
| T47 | M§9.9.1: hash recomputed from the **canonical long-form slice upstream of `to_engine_frame`** — a frame differing from the snapshot while carrying the sealed hash string MUST be refused (the discriminating case; verifying only the on-disk artifact passes it and MUST go RED). Matching slice accepted. `processing_version` mismatch refused (M§9.9.5). |
| T47a | M§9.9.1 **slice discrimination (deterministic)**: two different sealed time slices taken from the **same larger source file** MUST receive **different** `content_hash` values. A whole-file hash makes this RED — measured precedent: three different Binance spans currently share hash `64c05d5bf73f`. |
| T47b | M§9.9.1: calling `hash_dataframe_content` on the **wide engine frame** raises (long-format `timestamp`+`symbol` required), proving the hash is taken upstream of `to_engine_frame` and not on the engine object |
| T47c | M§9.9.1: a **funding/event** dataset is sealed and verified in its canonical normalized long form over the exact sealed coverage — whole-file and engine-side-event-object hashing both refused (assert **raises**, not a specific message — a `Sequence[FundingEvent]` has no `.columns` and raises `AttributeError`, not the `col-buffer-v2` guard) |
| T48 | M§9.9.2 total coverage: an OOS run loading any timestamp outside every `snapshot[]` entry refused — this is the testable form of "no re-fetch" (v1.2's "did not call the fetcher" is an implementation-shape assertion, not a behaviour) |
| T49 | M§9.9.4: window sealed `native` but evaluated on `proxy` data refused |
| T50 | M§9.6.2: OOS equity curve starting before `evaluation_start` refused; metrics computed from a curve starting exactly at `evaluation_start` accepted — **both sides of the boundary** |
| T51 | M§9.6.2: no PnL/trade/return from `[dependency_start, evaluation_start)` enters any reported OOS metric — asserted on the **full path**, not selected indices |
| T52 | M§9.6.4: a freeze declaring `dependency_start` earlier than the window's sealed value refused — **absolutely**, with no new-freeze/new-attempt path |
| T52b | M§9.6.10: funding loaded to `funding_coverage_end` accepted and satisfies W6 at the terminal period; loading beyond it refused; **no funding after `evaluation_end` affects any reported metric** — full-path assertion, the right-edge twin of T51 |
| T53 | M§9.6.5: a **non-OOS** research run intersecting the dependency buffer refused exactly as for the evaluation window |
| T56 | M§12.3/E2: the ledger emits a **search-burden block** that every report MUST embed verbatim; assert it is non-empty, states `n_configs_selected` for the whole hypothesis lineage (M§5.3), and renders "lower bound" on any UNKNOWN link. (v1.2's "a report is refused" named no refusing subject — M§1.2 forbids building a report system, so the obligation must attach to the emitted block.) |
| T56b | M§12.3: `n_configs_selected > 1` with no substantial/not-substantial classification in the decision record refused |

Outcomes reported **BROKE / SURVIVED / VACUOUS**. `SURVIVED` is blocking. The implementer MUST state
which mutations were **not** run.

Workspace integrity per CLAUDE.md: baseline covering tracked **and** untracked files, hash manifest
paired with a file-set comparison, verified after every mutation.

---

## M§16 Accepted limitations (MUST NOT be overstated)

| # | limitation |
|---|---|
| L1 | **D17 mitigated, not closed.** An unregistered run in an ephemeral session is undetectable. |
| L2 | **D18 stands.** `n_configs_evaluated` is self-reported; M§5.5.4 detects repeated config families, not unregistered configurations. |
| L3 | **D13 / DEFERRED-005 open.** Config files are outside `CODE_SCOPE_PATTERNS`; MUST close before the first config-file-driven strategy. |
| L4 | The seal files are ordinary committed files. M§9.8 makes tampering *detectable* (blob-verified, sha in `run_facts`); it does not make it impossible. |
| L5 | Validation reuse is permitted-with-disclosure (M§8.4), not prevented; `validation_evaluations` is the only signal. |
| L6 | `funding_notional_basis="period_start"` misstates funding by up to `|rate| x` intra-period move (backtest contract §7.6: −6.98% on a +15% day). Every funding-sensitive result MUST state this. |
| L7 | **Corrected from v1.0.** Measured, HL BTC funding starts **2023-05-12T00:00:00.048Z**, not 2024-08-15. The 2024-08-15T14:00Z date is the start of the final contiguous coverage segment after 84 gaps exceeding the 90-minute `max_funding_gap`; it binds only under `funding_mode="required"` and was measured **for BTC only**. Stated as a universe-wide floor (as v1.0 did) it would size windows against a boundary that does not generalise. Native 1h OHLCV depth (~208–209 days, rolling) is the constraint that actually binds forward windows. |
| L8 | **Rolling native retention — mitigated, not eliminated.** M§9.9.1 recomputes the hash over the canonical long-form sealed slice (upstream of `to_engine_frame`) and M§9.9.3 mandates write-once storage, which closes the fail-open path. Two fail-closed paths remain: a window must be snapshotted before its data ages out of the ~209-day rolling `candleSnapshot` (nothing forces the timing), and snapshot artifacts are necessarily **untracked** (D§8.2 forbids committing market data), so loss or replacement leaves no `git diff` and is unrecoverable once the source has aged. The recorded `content_hash` is the only evidence of integrity. |
| L9 | A fresh `HYP-` with `supersedes: null` that is substantively the same idea resets M§5 counters. `config_family_hash` catches identical configurations, not identical ideas. |
| L10 | **M§9.0's core predicate is not machine-checkable.** "Materially derived from results already observed on that period" is a written affirmation by the freezing researcher (M§9.1.2), backed by the registry scan for research-stage records. The scan catches *registered* contamination; it cannot catch a judgement formed from an unregistered glance. This is the price of the bootstrap and it should be stated in every bootstrap OOS report. |
| L11 | The dependency buffer is a genuine, authorised channel into pre-evaluation data. Its safety rests on the strategy being **frozen** before the buffer is read (M§9.6.3) and on the buffer being length-bounded (M§9.6.4). An unbounded or post-freeze-extended buffer would re-admit the sample. |
| L12 | **The robustness stage is not gaming-resistant, by design (v1.5).** A researcher who wants to mislabel a stress test, run a deliberately weak perturbation, or write an unfalsifiable pass criterion can do so; M§7 will not mechanically stop them. The defences are that every experiment is registered and retained (M§7.1.1) and that an independent reviewer inspects the actual records (M§7.3). Three prior attempts at formal enforcement were each defeated, and two of them measurably refused legitimate evidence — so the machinery bought no protection while penalising honest work. This limitation is accepted deliberately and MUST NOT be described as solved. |
| L13 | **A pre-declared robustness criterion is not independently verified as pre-declared.** Robustness runs are diagnostic and repeatable, and repeats are tagged rather than refused, so criteria written after seeing results and then committed are indistinguishable from criteria written before. The reviewer's judgement of whether a criterion is *meaningful* (M§7.3) is the operative defence, not its timestamp. |

---

## M§17 RESOLVED — authorised decisions (user, 2026-08-18)

All four escalations are resolved by user decision. Recorded here because the *rationale* for each
constraint must survive, and because a future reader must be able to tell an authorised design
choice from an oversight.

**E1 — RESOLVED: bootstrap historical protected OOS is authorised.** Future-only OOS is **not**
required. Protected means "not used for hypothesis development, parameter selection, robustness
selection, or validation of the strategy being evaluated" (M§9.0) — not "never downloaded".
Infrastructure and data-quality use does not consume a period, must remain recorded, and is
disclosed. One-time reserve, established before QR-RESEARCH-001, per M§9.1.6. Implemented in M§9.0,
M§9.1.2, M§9.1.6, M§10.4.
*Consequence:* the "~0 days of protected data / first OOS ~2026-11-15" constraint reported at v1.1
is **withdrawn — but only because M§9.1.2 keys the seal scan on `experiment_type`.** Measured: under
v1.2's stage-keyed scan the constraint was *worse* than v1.1 reported (zero sealable days, because
all five existing records are `pipeline_validation` + `exploratory`). With the type-keyed scan the
blocked set is empty and the reserve is the full persisted corpus — HL-native BTC 1h
2026-01-20→2026-08-16 (209 days), HL-native BTC funding 2023-05-12→2026-08-16, Binance-proxy 1h
across 210 symbols to 2026-07-31 — subject to `prior_infrastructure_use` disclosure of those five
records. Capacity is bounded by history, not by waiting.

**E2 — RESOLVED: mandatory accounting, non-universal method.** M§12.3. Correction technique may
depend on strategy and search geometry; what is mandatory is that the search burden is accounted for
and that the winner is never presented as the only configuration tested.

**E3 — RESOLVED, then SIMPLIFIED (v1.5).** Robustness remains a hard gate — a `FAIL` blocks
promotion (M§7.4) — but the gate is an **independent reviewer verdict over the registered
experiments** (M§7.3), not a machine-checked criterion schema. The formal apparatus of v1.2–v1.4
(evidence distinctness, dimension containment, criterion triples, plan lock) is withdrawn: three
reviews defeated every version, and two of its mechanisms measurably refused legitimate evidence.
Scope decision: protect against accidental bad research, not a researcher gaming their own process.
No universal Sharpe/DD numbers.

**E4 — RESOLVED: dependency buffer, sealed with the window.** M§9.6. Metrics begin strictly at
`evaluation_start`; the buffer initialises a frozen strategy only and may never inform selection.
This dissolves the W6 funding conflict without admitting pre-OOS performance into the result.

**Status of this document.** v1.2 encodes the above. It has been re-reviewed narrowly against these
decisions (see the work-order report). Any *further* change of these four rules is a methodology
change requiring user authorisation.
