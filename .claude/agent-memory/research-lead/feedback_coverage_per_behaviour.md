---
name: coverage-per-behaviour
description: "One test per mandated area" is not coverage — require one mutation per behaviour, and require the implementer to state which mutations they did NOT run
metadata:
  type: feedback
---

When writing a work order's test requirements, enumerate **behaviours**, not areas. An area
checklist ("deterministic ID / lineage / retention / …") is satisfied by one test per area while
leaving most behaviours inside each area undetectable.

**Why:** On QR-INFRA-002 this failed twice on the same artifact. Round 1: 143 tests, all 28
spec-table mutations BROKE — the auditor ran 44 of its own and **29 survived**. Round 2, after a
repair explicitly targeting the survivors: 245 tests, 28/28 plus 17 hand-picked areas BROKE — the
auditor ran ~120 and **20 survived**, five of them areas the amendment had named by hand. Both
rounds looked green and thorough.

Two measured mechanisms that produce this shape, worth checking for by name:

1. **A test that varies two fields together detects only the conjunction.** Every hashed dataset
   field was individually deletable from the identity payload with the whole suite green, because
   the one covering test changed two of them at once.
2. **A fixture can exercise the wrong layer.** A boundary fixture routed through the constructor's
   validation and never reached the adapter function the test was written to protect, so that
   function's bound stayed unguarded.

**How to apply:**
- State the acceptance criterion as *"no mutation in area X survives"*, not *"area X has a test"*.
- Require the implementer to report which mutations they did **not** run. On QR-INFRA-002 the
  implementer honestly declined to claim exhaustive coverage (17 of ~40 sub-items) and flagged the
  gap — that disclosure is what made the hole findable. Reward it rather than treating it as
  underdelivery.
- Require a **cross-process** determinism test wherever byte-identity or ordering is claimed:
  `tuple(set(...))` ordering differs across `PYTHONHASHSEED`, and a fixture writing both records in
  one process cannot see it.
- Pair the workspace hash manifest with a **file-set** comparison. A hash list cannot detect
  *extra* files, which is how stray artifacts evaded an auditor for a full round.

Related: [[mutation-proof-required]] (redo the mutations independently), [[measure-dont-cite]],
[[project-qr-infra-002]].
