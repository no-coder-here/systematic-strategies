---
name: project-qr-infra-001
description: QR-INFRA-001 — build and independently validate the common backtesting engine; spec v1.1 under audit as of 2026-08-13, no alpha research until complete
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

**How to apply:**
- The engine must contain no alpha logic. Strategies emit target weights only.
- Pipeline discipline: an agent that designs or implements a strategy may never certify it.
  strategy-engineer implements, backtest-auditor audits independently.
- **Status 2026-08-14: `docs/backtest_contract.md` v1.5.1 is FROZEN.** Six audit rounds:
  v1.1 FAIL (12 blocking) -> v1.2 FAIL (7) -> v1.3 FAIL (4) -> v1.4 FAIL (1) -> v1.5 PASS WITH
  WARNINGS (0 blocking, 5) -> v1.5.1 PASS WITH WARNINGS (0 blocking/runtime-correctness, 8
  editorial B1-B8, recorded in §21 of the frozen doc). Implementation NOT started —
  strategy-engineer has never been invoked. Requires the owner's explicit go-ahead.
- The frozen doc may NOT be edited in place. Any change needs a new numbered revision, a
  preserved snapshot in `docs/spec_history/`, and a fresh audit.
- Accounting core survived six independent numerical attacks (9.9e-17, 3.56e-16, then full
  bitwise fixture reproduction). **Every defect across all six rounds lay at a boundary, in a
  validation rule, or in the test suite — never in the central accounting sequence.** That is
  the single most useful prior for the next infrastructure spec.
- Spec snapshots are preserved under `docs/spec_history/` before each replacement (owner
  instruction, 2026-08-14). Never overwrite a historical snapshot.
- **Second recurring failure mode: cross-section contradictions.** v1.4's only blocking defect
  (E1) was §9.2 and test CF2 each being correct alone but contradictory together — I added a
  gating condition in one section while binding a test to a fixture that trips it. Distinct
  from the float-equality class. When a rule gains a new precondition, re-check every test that
  depends on the quantity that rule governs.
- **Recurring failure mode to watch: asserting `==` on floating-point values.** It has now
  appeared in four consecutive revisions (v1.1 R4, v1.2 N5/§8, v1.3 X6) — including once in
  the same document that introduced a normative tolerance policy forbidding it. Pattern-matching
  on the previous instance is not enough; every new numeric assertion must be re-derived and
  checked against the tolerance policy individually.
- **Pin the full config with every test fixture.** v1.3's X1 and CF7 fixtures were computed at
  `execution_lag = 0` but published without saying so, making them unreproducible under the
  config default of 1. Disclose every parameter a fixture depends on, not just the interesting
  ones.
- **Keep versioned spec snapshots** (e.g. `docs/backtest_contract_v1.2.md`). Overwriting
  `docs/backtest_contract.md` each revision meant the round-3 auditor could not verify B1-B12
  verbatim — it could only check the changelog's summary of them, and said so as a stated
  limitation of its audit.
- Do NOT copy a formula an auditor suggests into the spec without independently verifying it.
  In v1.2 I adopted the auditor's own round-1 replacement identity
  `sortino/sharpe == sqrt(2)*sqrt((n-1)/n)`; at round 2 the same auditor showed it was
  inverted AND impossible under its own premise (symmetric series => mean 0 => 0/0). An
  auditor's suggested fix carries no more authority than any other claim.
- Lesson worth keeping: v1.1's §5 accounting *core* was provably correct (net-return/NAV
  identity verified to 1e-17). Every blocking defect was at a **boundary** — undefined
  funding window instant per execution_mode, undefined equity-curve index, undefined
  terminal bar, undefined column alignment on universe exit, self-contradicting ruin path —
  or in the **test suite itself** (three mandatory tests were worthless/impossible/flaky).
  When specifying an engine, spend the effort on boundaries and on making tests falsifiable,
  not on re-deriving the central algebra.
- Key frozen conventions live in docs/backtest_contract.md — read it rather than trusting
  this summary, as it is the normative artifact and will have moved on.
