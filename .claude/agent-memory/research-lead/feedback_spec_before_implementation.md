---
name: feedback-spec-before-implementation
description: Freeze and independently audit the written specification before any code is written; the auditor reviews the spec as a paper artifact first
metadata:
  type: feedback
---

For infrastructure work, write the full normative specification first, have the user
approve it, and have backtest-auditor attack the spec **mathematically before any
implementation exists**. Only then delegate to strategy-engineer.

**Why:** On QR-INFRA-001 the user halted my delegation to strategy-engineer, rejected spec
v1.0, and required a v1.1 revision plus a spec-only audit returning
SPEC PASS / SPEC PASS WITH WARNINGS / SPEC FAIL. Their reasoning is sound: accounting
defects (NAV normalization, funding aggregation, implicit rebalancing) are cheap to fix in
prose and expensive to find in a passing test suite, because a wrong-but-self-consistent
engine produces plausible equity curves.

**How to apply:** Resist the pull to start coding. Pin every ambiguous choice in the spec
with an explicit rationale — sign conventions, annualization, units, interval half-openness,
what raises vs what proceeds. Maintain an explicit "open design decisions" section and ask
the auditor to rule on each. Keep a changelog of spec revisions with the cause of each
change, so rejected designs stay visible rather than being quietly overwritten.
