---
name: measure-dont-cite
description: Never write a factual claim about repo/data state into a spec from memory — measure it first; stale premises become load-bearing rationale and inert tests
metadata:
  type: feedback
---

When a specification's *rationale* rests on a fact about the repository, the data layer, or an
API's actual values, **measure the fact before writing it down**. Do not cite it from memory or
from a previous work order's report.

**Why:** On QR-INFRA-002 the spec audit found four of my own factual claims false, and each one
was load-bearing:
- "most implementation code is untracked" — was true during QR-SMOKE-001, false after `f7b73c2`;
  I had used it as the entire justification for a mechanism, and it also would have made a
  mandatory test inert (the live repo had **0** untracked in-scope files, so the test could not
  discriminate).
- `field_type` values `ohlcv_1h`/`funding` — invented; the real values are `ohlcv`/`funding_rate`.
  A containment check scoped to the invented literal would have been **permanently inert on real
  records while still passing on synthetic fixtures**.
- `dataset_id` `binance.um.ohlcv.1h.BTC` — real value is `binance.ohlcv.1h.BTC` (the implementer
  caught this one; I had made the same class of error a third time).
- "raises `FundingDataError`" — measured, the driver's own pre-flight raises `DataIntegrityError`
  first, so a test asserting the spec's claim would have **failed on correct code**.

The pattern: a wrong literal in a spec does not cause a loud error. It creates a test that either
cannot fail or cannot pass, and the implementer then loosens it until green.

**How to apply:** Before freezing any spec, grep/execute for every literal string, field name,
metric key, exception type and count it asserts. Prefer `dataclasses.fields(X)` enumerations over
hand-written field lists in both the spec and the tests, so a future rename cannot silently drop
a field. Ask the auditor explicitly to *derive/measure the spec's own algebra and literals rather
than accept them* — that instruction is what produced the best findings on both QR-SMOKE-001 and
QR-INFRA-002. See [[project-qr-infra-002]].
