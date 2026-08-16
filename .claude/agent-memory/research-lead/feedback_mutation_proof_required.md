---
name: mutation-proof-required
description: Require mutation proof for every test on this platform, and have the auditor redo the mutations independently — passing test counts are not evidence
metadata:
  type: feedback
---

Never accept "N/N tests passing" as evidence that an implementation is correct. Require the
implementer to mutate the source, confirm the target test goes RED, restore, diff-verify, and
report a mutation table. Then require the independent auditor to **redo the mutations itself**
rather than accept that table.

**Why:** On QR-INFRA-001 ([[project-qr-infra-001]]) two consecutive code audits returned FAIL
purely on *inert mandatory tests* — tests that looked correct and could not fail under the
defect they were written to catch. A 143/143 green suite hid an S3 fixture whose rebalance was
never executed, an F2 boundary test on a zero-quantity period, an "X8a near-ruin" whose NAV was
1_000_000.1, and three result-surface fields referenced by zero tests. The round-3 auditor ran
63 independent mutations and found 4 survivors the implementer's own table had missed. A test
that cannot fail is worse than no test: it manufactures false confidence.

**How to apply:**
- Put the mutation-proof requirement in the work order itself, marked non-negotiable, and quote
  the exact mutations to use so implementer and auditor results are comparable.
- State the acceptance criterion for every assertion as *"does this discriminate?"*, not
  *"does this pass?"*
- When an auditor reports "mutation X survives the full suite", treat it as a finding of the
  same severity as a wrong number, even when the engine is provably correct.
- Two techniques worth reusing: **poison unused inputs** (fill an unread data path with NaN so
  every fixture proves it is never read), and **self-guarding fixtures** (assert in the fixture
  that it still has the property that makes the test discriminating).
