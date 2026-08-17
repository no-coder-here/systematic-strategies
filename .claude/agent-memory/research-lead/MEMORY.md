# Research Lead Memory

- [User role](user_role.md) — platform owner, sophisticated quant, reviews specs line-by-line before approving implementation
- [Honest technical disagreement](feedback_honest_technical_disagreement.md) — user asks for my opinion on their own reviews and wants real pushback, not agreement
- [Spec before implementation](feedback_spec_before_implementation.md) — freeze and audit the written spec as a paper artifact before delegating any code
- [QR-INFRA-001](project_qr_infra_001.md) — common backtesting engine; spec v1.5.1 frozen + implementation CODE PASS; recurring failure modes and adjudications
- [Mutation proof required](feedback_mutation_proof_required.md) — passing test counts are not evidence; require mutation proof and have the auditor redo it
- [QR-DATA-001](project_qr_data_001.md) — Hyperliquid data layer; spec v1.1 frozen; venue traps, archive coverage/cost, why probing the API first paid off
- [QR-SMOKE-001](project_qr_smoke_001.md) — end-to-end pipeline test; HL rolling-window + funding-seam limits on all research windows; VACUOUS mutations
- [Measure, don't cite](feedback_measure_dont_cite.md) — never write a repo/data fact into a spec from memory; stale literals build inert tests
- [QR-INFRA-002](project_qr_infra_002.md) — experiment registry; spec v1.2 frozen, REGISTRY FAIL on test hardening; two-hash identity; why broad-but-thin coverage failed twice
- [Coverage is per-behaviour](feedback_coverage_per_behaviour.md) — "one test per mandated area" is not coverage; require one mutation per behaviour and ask what wasn't mutated
