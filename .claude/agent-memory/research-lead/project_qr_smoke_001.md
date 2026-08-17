---
name: project-qr-smoke-001
description: QR-SMOKE-001 — end-to-end pipeline smoke test (trivial BTC SMA100); SMOKE PASS WITH WARNINGS at spec v1.1; HL rolling-window and funding-seam traps that constrain ALL future research windows
metadata:
  type: project
---

Third work order: validate the COMPLETE pipeline end-to-end on real data using a deliberately
trivial BTC 1h strategy (`close > SMA(100)`, long-or-flat, w ∈ {0, 1}). Explicitly NOT alpha
research — profitability is irrelevant, timing and accounting correctness are the deliverable.
Spec: `docs/qr_smoke_001_spec.md` **v1.1 FROZEN** (v1.0 snapshot in `docs/spec_history/`). Joins
[[project-qr-infra-001]] (engine) and [[project-qr-data-001]] (data layer).

**Status 2026-08-17: SMOKE PASS WITH WARNINGS.** 1 spec-audit round (SPEC FAIL) + 2
implementation/audit rounds (SMOKE FAIL → pass). 85 smoke tests, 531 full suite. Code in
`strategies/qr_smoke_001/`, `experiments/qr_smoke_001/`, `tests/qr_smoke_001/`. **The pipeline is
validated and usable for research** subject to the standing limitations below.

**Why:** the engine and the data layer each passed their own audits, but nothing had ever
verified the *seam* between them on real data. A defect at the handoff is invisible to both
prior work orders.

## Two data facts that constrain EVERY future backtest window (verify before reusing)

1. **Hyperliquid `candleSnapshot` serves a ROLLING ~208-day window (5000 bars), not fixed
   history.** At 2026-08-16 it began 2026-01-20 11:00Z. This is distinct from the known F1
   truncation trap — the data genuinely does not exist earlier via this endpoint. Consequence:
   **any run depending on HL-native candles must read a persisted local snapshot offline and
   must never re-fetch**, or the start date silently walks forward and eats the warm-up. Our
   Window A had only 9 hours of buffer. Determinism claims are void if a run re-fetches.
2. **HL BTC funding has 84 gaps exceeding the 90-min `max_funding_gap` before
   2024-08-15 14:00Z, and is contiguous after.** Backtest contract §7.7.2 condition 2 requires a
   funding-accruing period to sit inside a *single* coverage record, so **any funding-enabled run
   starting before 2024-08-15 raises `FundingDataError`.** This is a hard boundary on
   funding-realistic research, not a tunable.

Corollary adopted here: a long-history run must be **split** — full Binance history with
`funding_mode="disabled"` (labelled: Hyperliquid did not exist for most of it, so there is no
funding to charge; fabricating one violates CLAUDE.md), plus a funding-enabled run over the
contiguous-coverage window. Do not reach for a single window that quietly drops funding.

Also verified empirically: Binance and HL both label bars from the bar-**open** ms; hourly
return correlation 0.9995; cross-correlation argmax at lag 0 (collapses to ~0 at ±1h), which is
the test that actually rules out a half-bar labelling offset. **Requiring `argmax ρ(l) == 0` is
worth reusing** — a reported correlation number alone is not a test, since ρ=0.02 would satisfy
a report-only deliverable while indicating a serious defect.

## The spec audit returned SPEC FAIL, and three of the blocking defects were mine

Worth remembering because they are *classes* of error, not one-offs:

- **Off-by-one invariance boundary.** I wrote "mutate data strictly after T, assert periods
  through T unchanged". But period `k` legitimately earns `P[k]→P[k+1]`, so mutating index `k+1`
  changes period `k` on a *correct* engine. The test goes RED on correct code, the implementer
  loosens it until green — **that is the mechanism by which inert lookahead tests get built.**
  Correct boundary is index `>= k+2` (contract §18.1 E4).
- **Importing §17's EXACT permissions into a cross-implementation comparison.** "Carried-forward
  quantity" and "same stored double" are scoped to *within one run*. Across two implementations
  every value is a different double on a different path, so `==` fails at ~1e-16. This is the
  over-tightening half of the float-tolerance error — the same error my own spec text was
  warning about two paragraphs above. **Re-derive per field; a per-field EXACT/TOLERANCE table
  is the only auditable form.** Non-obvious instance: `pandas.rolling(n).mean()` uses an online
  running sum and is NOT bitwise equal to `np.mean` over each slice, so SMA is TOLERANCE.
- **A tripwire that fires on 1 ulp.** `max_gross_leverage = 1.0` on a nominally 1x book breaches
  on 31/199 periods at *zero cost and zero funding*, because `q→w→q` is not bitwise stable and
  §6.8 tests strict `>`. Any test against it is inert both ways. Set tripwires strictly off the
  degenerate value (used 1.05).

The auditor **measured** these against the real engine rather than reasoning about them, and
also corrected an unsupported magnitude claim in my own rationale (I called `rebalance_every_bar`
micro-trades "economically meaningless"; measured, it is a 0.2% cost difference). Asking an
auditor to *derive/measure the spec's own algebra rather than accept it* is what produced the
best findings.

## Structural lesson: a mutation can be VACUOUS, which is neither pass nor fail

`>` vs `>=` in a signal differs only where `close` is bitwise equal to the SMA — on real data,
essentially never. Contract §7.5's half-open funding boundary differs only for an event landing
exactly on a bar boundary — HL timestamps jitter, so likely no such event exists. Both mutations
sail through green and get recorded as passes. **Added a third mutation outcome, VACUOUS, and
required the discriminating-case count to be reported.** Extend the BROKE/SURVIVED table to
BROKE/SURVIVED/VACUOUS on all future work orders.

Related: half the mutations in my first draft mutated *frozen already-audited engine code*, not
the new code under test. Effective coverage was 2 of 10. When writing a mutation table, check
which layer each mutation actually perturbs.

**Three VACUOUS cases were confirmed empirically, two of them counter-intuitive.** All three are
properties of this dataset, so re-measure rather than assume:
1. `close == SMA100` bitwise: **0 bars** — the `>`/`>=` boundary is untestable. Expected.
2. Funding half-open boundary: **64 boundary-coincident events** — NOT vacuous. I had predicted
   vacuity from timestamp jitter and was **wrong**; jitter tightened to sub-second over time.
3. A `+1h` shift of every funding timestamp leaves `funding_events_excluded` at **exactly 23545**
   — I verified this myself from the parquet. Exactly one event enters at `T_0` and one leaves at
   `T_last`, netting to zero. **Count-based identities cannot detect a uniform time shift**;
   only value-based comparison (full-path `funding_pnl_cash`) catches it.

## The single most valuable structural lesson: assert the full path, not selected indices

The independent reconstruction was genuine and *could* disagree — but was asserted at **3 indices
out of 4511**. Consequence, measured: a funding-boundary mutation moving equity on 3973/4511
periods **passed all 63 tests**. Promoting the comparison to a full-path assertion closed four
separate findings at once (M3, M19, M20, and the funding-boundary gap). Selected indices are for
human-readable reporting; the assertion must cover every period and every field.

Corollary: a *correct instrument wired to almost nothing* is the most dangerous shape of test,
because the instrument's sophistication is mistaken for coverage.

Two more reusable specifics:
- **A one-sided price probe can be inert.** The close-side lookahead probe detects `m=1,2,3` only
  in the ×0.001 direction; ×1000 misses all three, because a real close may already sit on the
  flipped side of the comparison. **Always two-sided.**
- **Symmetric counts hide sign errors.** Inverting the signal (`>`→`<`) left agreement rate and
  entry/exit counts unchanged (106/106, 107/107 — the frame starts and ends flat, and
  `agree(¬a,¬b) == agree(a,b)` identically). Only pinning the **first entry/exit timestamps**
  discriminated.

## Process failure worth not repeating: a killed subagent silently contaminated the tree

A re-audit agent was killed by a watchdog mid-mutation and left a live half-splice in
`pipeline.py`. Because `strategies/`, `experiments/` and `tests/qr_smoke_001/` were **untracked
at that moment**, `git diff HEAD -- src docs` — the check I ran and trusted — is structurally
incapable of seeing it. A later audit caught it only by re-running the tests and finding 4
failures.

> **STALE-CLAIM WARNING (corrected 2026-08-17).** Those paths were committed in `f7b73c2`, so
> "most implementation code is untracked" is **no longer true**. I carried the stale claim
> forward into the QR-INFRA-002 spec as a load-bearing rationale and the auditor measured it
> false. The *procedural* lesson (a tracked-only diff is not a workspace-integrity check, and
> untracked files must be covered by a hash manifest) survives; the *factual* claim does not.
> Re-measure `git ls-files` / `git status --porcelain --untracked-files=all` before ever citing
> tracked-vs-untracked state again.

**Fix adopted: `docs/qr_smoke_001_baseline.sha256`, a SHA-256 manifest of all 19 implementation
files, verified after every single mutation cycle rather than batched at the end.** Require this
for any mutation work on untracked paths, and prefer running long mutation tables per-target-test
rather than per-full-suite (the watchdog kill was caused by ~20 × 77s suite runs).

Also: the auditor blamed the implementer for the stray mutation. **It was wrong** — my own
529-passing measurement taken right after delivery proved the tree was clean then. Keep a
timestamped baseline measurement after each delivery; it is what let me exonerate the engineer.
