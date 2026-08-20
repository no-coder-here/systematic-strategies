---
name: project-qr-methodology-001
description: QR-METHODOLOGY-001 research methodology, CLOSED at v1.6 PASS WITH WARNINGS; why formal anti-gaming enforcement was withdrawn for judgement+visibility, 17 reusable defect classes, and the bootstrap-OOS reserve available to seal
metadata:
  type: project
---

Fifth work order: define the workflow governing alpha research. Doc: `docs/research_methodology.md`
**v1.6 — CLOSED, METHODOLOGY PASS WITH WARNINGS.** No code was written; the runner
(`src/research/runner.py`) and `research/` tree are specified but NOT built. Sits above the frozen
[[project-qr-infra-002]] registry, [[project-qr-infra-001]] engine and [[project-qr-data-001]] data
layer.

**Route to closure (read the RESOLUTION section below first).** Route: v1.0 → 2 independent reviews (FAIL 15
blocking / OOS FAIL 10 blocking) → v1.1 repairs → **user authorised E1–E4** (bootstrap historical
protected OOS + immutable snapshots; multiple-testing accounting without a universal method;
robustness as hard gate; dependency buffer) encoded as v1.2 → narrow scoped re-review (both FAIL:
5 blocking E1/E4, 3 blocking E3) → v1.3 targeted repair → re-check: **OOS PASS WITH WARNINGS, E3
FAIL.** Repair budget exhausted; stopped and reported → QR-METHODOLOGY-001-A closure attempt (N1
closed, M§7 FAIL again, all seven attacks) → **user simplified the robustness stage** → v1.6 PASS
WITH WARNINGS.

**The OOS regime (M§9/M§10/M§11) is sound and MUST NOT be reopened** — accepted after two reviews.
N1 is closed: hash the canonical **long-form** dataset sliced to the exact sealed span, upstream of
`to_engine_frame` (the engine frame is a wide pivot plus `FundingEvent` objects, so
`hash_dataframe_content` raises on it), never a whole source file — measured, three different Binance
spans share hash `64c05d5bf73f` under whole-file hashing.

**Bootstrap capacity, measured 2026-08-18:** blocked set is empty (zero
`alpha_research`/`robustness`/`replication` records), so the sealable reserve is the whole persisted
corpus — HL-native BTC 1h 2026-01-20→2026-08-16 (~208d, the binding constraint on native windows),
HL BTC funding 2023-05-12→2026-08-16 (28,056 events), Binance proxy 1h 210 symbols to 2026-07-31
(BTC ~2,404d). All five existing records intersect ⇒ all need `prior_infrastructure_use` disclosure.
**Nothing is sealed yet; `research/` does not exist.**

## The architectural constraint that drove everything

The registry schema is frozen at `qr-infra-002-v1.3` **with five live records**, so a schema bump
re-hashes and orphans them. The methodology therefore had to be a layer *above*, reusing existing
fields plus adjacent files. **Corollary I got wrong and the auditor caught:** you also cannot invent
a new record-level warning token — `RECORD_WARNING_PREFIXES` is a closed vocabulary and
`_assemble_warnings` raises `RegistryError` on anything else. Methodology signals must travel via
`tags`, `notes`, `annotate()` or a side ledger.

## Evasion classes worth reusing on any future governance spec

These are the shapes both reviewers found, independent of this document:

1. **A guard that is forward-looking only.** My OOS purity guard refused future runs into a sealed
   window but permitted *sealing a window over already-explored data*. Any "protected region" rule
   needs a **retroactive scan at declaration time** as well.
2. **The guarded quantity was caller-declared.** I claimed the window guard was immune because it
   didn't use grouping labels — but `data_start`/`data_end` come straight from the caller's
   `dataset_windows` dict and are never derived from the loaded frame, and `eval_*` is pinned to the
   equity curve only for `field_type=="ohlcv"`. **Derive the guarded window from the actual data
   passed to the engine, and re-check post-run.**
3. **Keying a ledger on an identifier instead of the underlying resource.** Burn keyed on
   `window_id` → re-declare the same period one hour offset under a new id, `reveal_count` resets.
   **Key on time-intersection.** Same class: keying reuse on `hypothesis_id` → a fresh id with
   `supersedes: null` walks past. Add a `config_family_hash` match that ignores declared labels.
4. **Blocking modification but not selection.** My burn rule fired on "modified because of the
   results". Freezing ten candidates *before* any reveal and evaluating serially defeats it with
   every statement true. **Enumerate and commit the candidate set before the first reveal.**
5. **An exemption keyed on a field orthogonal to the gates.** I exempted drivers by
   `experiment_type` while every gate keys on `research_stage`, so `experiment_type="replication"` +
   `research_stage="out_of_sample"` bypassed the runner entirely. Also `experiment_type` is a
   runtime string, undecidable by a static AST check (`experiment_type=CFG["type"]`).
6. **An unenforceable mental-state predicate.** "modified *because of* those results",
   "justifiable without reference to the sign", "research shows it should be restated". Replace with
   machine-checkable ones — the good fix here was: a BUGFIX re-run is permitted iff
   `status ∈ {FAILED, INVALID}` **and** `results is None` (no number was produced).
7. **A counter that feeds no decision.** I built careful multiple-testing accounting and nothing
   consumed it: a candidate with 4,000 configs faced the same bar as one with 3. **Decorative.**
8. **A pre-declaration with no anchor.** "Declared before running" is worthless on an uncommitted
   working-tree file. I had applied the git-committed-blob pattern (R§20.6.2) to the freeze manifest
   only; it needed to apply to hypothesis, search space, robustness plan and promotion criteria too.
9. **A stage label that gates nothing.** My ROBUSTNESS stage required an ancestor record at that
   stage — satisfiable by one record whose only robustness content was its `research_stage` string.
   Also, ancestor-at-stage-X does not mean *the same configuration* was checked: add
   `config_family_hash` continuity across stage edges.
10. **An enumerated deny-list instead of a property.** Rejecting `tbd, TBD, todo, ?, n/a` accepts
    `TODO`, `N/A`, `-`, `none`, `to be determined`. Normalise, then impose a *content floor*.
    Same error as the five-literal test that "tests the list, not the property".

## Measure-don't-cite, fifth and sixth instances

Both reviewers independently caught wrong literals of mine — see [[measure-dont-cite]]:
- `total_is_lower_bound` → real key is `n_configs_evaluated_total_is_lower_bound`.
- `EXPERIMENT_TYPES` listed 4 of **6**; the omitted `robustness` *was* the exemption hole.
- Cited `_r21_3_offenders` as a pytest node; it is a module-level helper.
- Said `KeyboardInterrupt`/`SystemExit` register `INVALID`; they register **`FAILED`** with
  `ABORTED:` prefix. `INVALID` is the normal-exit-without-result path.
- Cited `find_experiments` as having an anti-survivorship default; **R§13.2 is about
  `list_experiments()`**, and `find_experiments(status="COMPLETED")` *is* the survivorship bug —
  my instruction would have produced the harm it forbade.
- **L7 funding, which I copied from my own memory file:** HL BTC funding starts **2023-05-12**, not
  2024-08-15. The 2024-08-15T14:00Z date is the start of the final contiguous segment after 84
  gaps > 90 min, binds only under `funding_mode="required"`, and was measured **for BTC only**.
  I had it in memory as an unconditional universe-wide floor. Memory is not measurement.

Also worth keeping: **half of one stated blocking gap did not exist.** I claimed the AST check
missed both `asname` and the attribute form; the attribute form was already covered. Verify both
halves of a "gap" before mandating work against it.

## Three more evasion/defect classes from the v1.2→v1.3 round (all self-inflicted)

11. **A predicate keyed on the wrong field makes a branch structurally unreachable.** I keyed the
    OOS seal scan on `research_stage` "regardless of `experiment_type`", intending to permit
    infrastructure use. Measured: `RESEARCH_STAGES` has **no infrastructure-neutral value** and the
    field is required, so every infra record must carry a research stage — and all five real records
    are `pipeline_validation` + `exploratory`, tripping the refusal. The permit branch was dead code
    and **zero days were sealable**; the authorised bootstrap could not run at all. Fix was keying
    on `experiment_type` (the two sets are disjoint and exhaustive). **Before writing a two-branch
    predicate, enumerate the real records and check both branches are reachable.**
12. **An "identity must match" requirement can invert its own purpose.** I required robustness
    evidence to have a `config_family_hash` matching the candidate. But `config_family_payload`
    strips only window fields — it **retains** `strategy` and `backtest_config`, and `fee_bps`/
    `slippage_bps` are `BacktestConfig` fields. So every genuine perturbation run (2x fees, 2x
    slippage, parameter neighbour, leave-one-out, ±1 bar) hashes *differently* and was refused as
    evidence, while the unperturbed baseline — which discharges nothing — matched and could be cited
    for all ten checks. **When requiring hash equality as proof, ask what the hash covers and
    whether the thing you want to prove necessarily changes it.**
13. **Anti-vacuity catches the unfalsifiable, not the weak.** My `fails_if:` rule rejected criteria
    no outcome could fail — but "2x-fee Sharpe > 0" is perfectly falsifiable *and* perfectly
    fitted to an observed worst case of 0.55. And a timing anchor that is disclosure-only is not an
    anchor: M§2.3 forces an in-sample ancestor, so IS results are **always** visible when robustness
    criteria are written, which makes the "revision" branch permanently operative. A strength
    constraint ("no weaker than a pre-registered criterion") is the missing half — the same lesson
    already learned for promotion criteria in M§12.3 and not carried across to M§7.

## QR-METHODOLOGY-001-A (closure attempt, 2026-08-18): FAIL. Four more classes.

N1 verified sound but I left the **blocking M§9.5 gate quoting the superseded wording**, pointing at
the rule it contradicted — sixth instance of [[measure-dont-cite]]. **When changing a rule, grep for
every restatement of the old wording.** Fixed; N1 closed. M§7 returned FAIL with all seven attacks
succeeding.

14. **A containment rule must be able to *observe* the dimension it constrains.** My table compared
    `strategy.params` / `backtest_config` / `universe_policy` — but window fields and the resolved
    universe live on **`DatasetRef`** (`models.py:225-229`), and `universe_policy` is a **bare
    `str`** (`:599`), so "compare field-by-field" is inapplicable. Measured: subperiod and time-LOO
    records share the baseline `config_family_hash` **exactly** (`b4345ce87656`), so unperturbed
    baseline re-runs pose as items 2/6 evidence. Conversely a genuine leave-one-out changes
    `datasets[].symbols` while leaving the policy string untouched, so my MUST-differ row **refused
    legitimate evidence** — the inverted-rule failure in reverse, in the very clause written to
    replace an inverted rule.
15. **An allowance satisfiable only through an unverified channel is a laundering channel.** My
    compound-run exemption needed "separately named metric fields", but the engine emits a fixed
    10-key metrics dict (`metrics.py:79-90`), so those fields can only exist in caller-typed
    `custom` or on a `recorded_via="manual"` record. The exemption therefore *required* the
    hand-typed path M§4.2.3 exists to prevent, and my own test certified it. **Before writing an
    exemption, check whether the sanctioned path can satisfy it.**
16. **A "no weakening" rule needs to be closed under the ways strength can drop.** I enumerated
    threshold-lowering, perturbation-softening and removal. Two moves evade all three: **metric
    substitution** (`sharpe_fee2x` → `sortino_fee2x`, same number) and **evidence rebinding**
    (pre-declare neighbours {19,21}; 19 fails; narrow the item's binding to 21 — the check is not
    removed, just no longer bound to the failing record). Fix shape: any edit to the criterion
    triple creates a revision unless provably stronger, and the evidence set is **monotone**, with
    the verdict evaluated over *every* plan-item record, worst case binding.
17. **A gate needs a resolution key, an error class, and a named enforcement point.** My candidate
    id `CAND-nnn` existed only as a filename component; no registry field carries it, so a
    validation/OOS runner cannot resolve *which* plan governs a run. The gate was also absent from
    both enumerated enforcement checklists (M§8.5, M§9.5) and named no error class — so "robustness
    blocks promotion" had no subject. **Contrast M§8.4.4, which correctly mandates its counter into
    `run_facts`.** Cheap fix, no schema bump: mandate `run_facts["candidate_id"]`.

Also learned: **a pre-registration lock is defeated by re-running.** Robustness runs are diagnostic
and repeatable, and a repeat is *tagged, never refused*. So peek → fit criteria → commit → obtain
lock → re-run identical configs post-lock → bind those. Even a `logged_at > lock_commit` ordering
check falls to the re-run. Any "declared before results were known" claim must state this limit
rather than assert independence.

## RESOLUTION (2026-08-18): simplified to v1.6, METHODOLOGY PASS WITH WARNINGS

**The user's call, and it was the right one: the robustness stage was over-engineered.** After three
rounds of formal anti-gaming machinery being defeated, the scope was reset to **protect against
accidental bad research, not a researcher gaming their own process.** All of it — evidence
distinctness, `config_family_hash` matching, dimension containment, criterion triples, the
pre-evidence plan lock, 20 test rows — was withdrawn and replaced with: register the experiments,
retain the failures, have `robustness-reviewer` inspect the *actual registered records*, and make a
`FAIL` verdict mechanically block promotion.

**The generalisable lesson, worth more than anything else in this file:** decide up front which
failure modes can *silently corrupt a result* and hard-enforce only those — registration,
provenance, search-space accounting, failed-result retention, lookahead/accounting, protected OOS.
Everything else is judgement, and judgement is protected by **visibility** (registered + retained +
independently inspected), not by proof. I spent three rounds building proofs that a determined
researcher walked through anyway while they rejected honest work. **Formal enforcement that cannot
succeed is worse than a documented human control**, because it produces false confidence plus real
friction.

Two structural refinements worth reusing:
- **Separate the verdict from its effect.** The reviewer's *judgement* is human; the *effect of a
  recorded FAIL* must be mechanical, or the gate is advisory. My first simplification said "nothing
  else in M§7 is machine-enforced" and thereby licensed building the gate as documentation.
- **Every soft/disclosed rule needs a named carrier and a test that it is emitted.** An unrendered
  disclosure is an absent one — the same lesson as QR-INFRA-002's unrendered honesty notes.

Also: **when replacing a section, grep the mandated-test table.** My v1.5 edit left `T6` demanding
the exact hard rule the edit withdrew, and inserted four rows whose ids collided with existing
protected-OOS rows (T21–T24 each named two behaviours), so an auditor tracking "T23 BROKE" could
have silently dropped a hard-column behaviour.

## The finding that gates the next work order

**Protected OOS capacity is ~0 days today.** Windows are forward-only, so with a two-window reserve
rule the earliest first legal OOS evaluation is ~2026-11-15 and steady state is roughly **one
protected evaluation per six months**. Related hazard: a sealed forward window must be ingested and
persisted *before it ages out* of the ~209-day rolling `candleSnapshot`, or it silently degrades
from native to Binance-proxy and invalidates the freeze manifest after the fact. Escalated as
M§17-E1 rather than decided alone; it shapes the whole QR-RESEARCH-001 timeline.

## Process note

Pairing `robustness-reviewer` (research design) with `oos-evaluator` (OOS protocol) on **disjoint
section ranges, in parallel** reproduced the QR-INFRA-002 success: largely disjoint findings, but
they **independently converged on five identical defects**, which made those five high-confidence
immediately. Reuse the disjoint-scope parallel pairing, and treat cross-reviewer convergence as a
priority signal.
