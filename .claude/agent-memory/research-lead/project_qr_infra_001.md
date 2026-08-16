---
name: project-qr-infra-001
description: QR-INFRA-001 — common backtesting engine; spec v1.5.1 FROZEN and implementation CODE PASS as of 2026-08-15; engine lives in src/backtest/, 157 tests
metadata:
  type: project
---

QR-INFRA-001 is the first work order on this platform: build and independently validate the
common backtesting engine that all future systematic strategies will use. Explicitly **not**
alpha research, and explicitly scoped to stop when the engine is validated — no market-data
work, no strategy research follows it without a new work order.

**Why:** Every future strategy is evaluated through this engine's accounting, so a defect
here silently contaminates all downstream research. The user wants one shared accounting
implementation so strategies are comparable and cannot self-report PnL.

**Status 2026-08-15: COMPLETE.** `docs/backtest_contract.md` v1.5.1 FROZEN (6 spec audit
rounds); implementation in `src/backtest/` + `tests/backtest/`, 157 tests, **CODE PASS**
after 4 implementation/audit rounds. Committed (`26d784e`). QR-DATA-001 started 2026-08-16 —
see [[project-qr-data-001]].

**How to apply:**
- The engine must contain no alpha logic. Strategies emit target weights only.
- Pipeline discipline: an agent that designs or implements may never certify. Held across
  all 10 rounds (6 spec + 4 code); never violated.
- Read `docs/backtest_contract.md` rather than trusting any summary — it is normative.
- The frozen doc may NOT be edited in place. Any change needs a new numbered revision, a
  snapshot in `docs/spec_history/`, and a fresh audit.

## The one lesson that generalises: defects live at boundaries and in the test suite

Across all 10 rounds, **not a single defect was ever found in the central accounting
sequence.** It was verified at 9.9e-17, 3.56e-16, then bitwise, then against two independent
from-scratch reimplementations. Every defect lay at a boundary, in a validation rule, or in
the tests. Spend effort there, not on re-deriving the algebra.

Code rounds 2 and 3 **both failed on inert mandatory tests and nothing else** — tests that
looked correct, asserted the right-sounding thing, and could not fail under the defect they
targeted. Examples: an S3 fixture whose rebalance was unexecuted so the branch never ran; an
F2 boundary test on a period holding zero quantity; an "X8a near-ruin" whose NAV_end was
1_000_000.1; N3t whose fixture round-tripped bitwise so its EXACT anti-second-NAV-path
assertion could never fire; `pre_trade_weights`, `fee_basis_notional` and
`liquidation_modelled` referenced by zero tests.

**Therefore: mutation proof is mandatory, not optional.** Require every agent to mutate the
source, confirm the test goes RED, restore, and report the table. Then have the auditor redo
the mutations independently — the round-3 auditor ran 63 and found 4 survivors the
implementer's own table had missed. "143/143 passing" told us almost nothing on its own.

## Recurring failure modes to check first on any new spec or implementation

1. **Inert tests** (above). Acceptance criterion for any assertion: *does this discriminate?*
2. **Float `==` on TOLERANCE-classified values.** Appeared in **six consecutive rounds**,
   including inside the revision that introduced the tolerance policy, and once as an
   over-tightening (round 3 turned a correct `approx` into an `==`). Pattern-matching on the
   last instance never works; re-derive every numeric assertion individually.
3. **Cross-section contradictions** — a rule gains a precondition and a test elsewhere trips
   it. When a rule changes, re-check every test touching the quantity it governs.
4. **Fixtures that inherit undefaulted config.** Pin the full config with every fixture.
5. **A normative rule with no code path** — v1.5.1 §4.2 (`P = open`/`P = close` per
   execution_mode) was simply not implemented for a whole round: the engine took one price
   frame and `execution_mode` only moved `T_i`. A caller passing close prices under
   `next_open` got a free bar of hindsight. Test E2 could not catch it because it fed two
   *different* frames to two runs. **Check that each normative rule has a code path AND that
   its test could fail.**

## Techniques worth reusing

- **Poison the unused input.** Filling the unselected open/close frame with NaN made all
  ~150 fixtures actively prove the unselected series is never read. Converted one narrow
  test into a suite-wide invariant; mutations that previously survived now cause 91 failures.
- **Self-guarding fixtures.** N3t now asserts *in the fixture* that its prices do not
  round-trip bitwise, so it cannot silently go inert if someone edits the prices later.
- **Differential vs an independent reimplementation**, plus randomized runs (600–4000) with
  a SHA-256 over the whole result surface. This is what actually cleared the accounting core.

## Adjudications I made that a future revision should honour or revisit

- `MissingPriceError` subclasses `InvalidPriceError` — the only reading satisfying §5.5,
  §11.2 and §18.9 S2 simultaneously.
- §10 `target_weights (as supplied)`: the explicit per-field qualifier beats the group's
  `n_periods`-rows header. Result exposes the unmodified supplied frame; the resolved
  execution-indexed frame is `resolved_target_weights`. **§10 is genuinely ambiguous here.**
- §6.0 Step 0's EXITING price validation is **unreachable via `run_backtest`** (any symbol
  with `q_prev != 0` had `P[i]` validated as `P[i+1]` at the prior period's Step 5). Code
  keeps it; tested via the `_step_period` helper under §21 B1's precedent.
- §9.2 drag preconditions 3–4 are **structurally implied** by 1 and 2. Confirmed independently
  by two agents and by 4000 randomized runs. Kept as defence-in-depth.
- Do NOT add an `initial_capital > 0` guard — §21 B2 is accepted frozen debt and the fix is
  not in the frozen text. It currently raises `ZeroDivisionError`, which §11.2 does not permit.

## Open items for a v1.6 spec revision (none block use of the engine)

§21 B1–B8 as frozen, plus: `initial_capital = 0` raising `ZeroDivisionError`; §12.3's
`annualized_volatility == 0` exact float trigger; §9.2's redundant preconditions; §10's
`target_weights` row-count contradiction; `funding_events_excluded` not counting
out-of-universe symbols; §10's `counterfactual_status` `__repr__` MUST has no covering test.
