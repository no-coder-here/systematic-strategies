# Research Lead Memory

- [User role](user_role.md) — platform owner, sophisticated quant, reviews specs line-by-line before approving implementation
- [Honest technical disagreement](feedback_honest_technical_disagreement.md) — user asks for my opinion on their own reviews and wants real pushback, not agreement
- [Spec before implementation](feedback_spec_before_implementation.md) — freeze and audit the written spec as a paper artifact before delegating any code
- [QR-INFRA-001](project_qr_infra_001.md) — common backtesting engine; spec v1.5.1 frozen + implementation CODE PASS; recurring failure modes and adjudications
- [Mutation proof required](feedback_mutation_proof_required.md) — passing test counts are not evidence; require mutation proof and have the auditor redo it
- [QR-DATA-001](project_qr_data_001.md) — Hyperliquid data layer; spec v1.1 frozen; venue traps, archive coverage/cost, why probing the API first paid off
- [QR-SMOKE-001](project_qr_smoke_001.md) — end-to-end pipeline test; HL rolling-window + funding-seam limits on all research windows; VACUOUS mutations
- [Measure, don't cite](feedback_measure_dont_cite.md) — never write a repo/data fact into a spec from memory; stale literals build inert tests
- [QR-INFRA-002](project_qr_infra_002.md) — experiment registry; v1.3 PASS WITH WARNINGS, committed+migrated; schema now costly to bump; two-hash identity; broad-but-thin coverage
- [Coverage is per-behaviour](feedback_coverage_per_behaviour.md) — "one test per mandated area" is not coverage; require one mutation per behaviour and ask what wasn't mutated
- [QR-METHODOLOGY-001](project_qr_methodology_001.md) — research workflow v1.6 PASS WITH WARNINGS; hard-enforce only silent-corruption paths, judgement elsewhere; bootstrap OOS authorised, nothing sealed
- [Subagent worktree isolation](ops_subagent_worktree_isolation.md) — agents get worktrees without gitignored data; run live-data steps yourself in the main tree
- [QR-PREP-001](project_qr_prep_001.md) — PRE-RESEARCH PASS; OOS-001 sealed+spent (BTC-funding-only); procedural protection; base-class pytest.raises trap
