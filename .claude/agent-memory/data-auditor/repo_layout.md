---
name: repo-layout
description: Where data-layer specs, implementer reports, code, and real ingested data live in this repo
metadata:
  type: reference
---

Systematic-strategies repo, data layer (QR-DATA-001):

- Normative spec: `docs/data_contract.md` (frozen, versioned; older versions snapshotted in
  `docs/spec_history/data_contract_v1.X.md`). Downstream: `docs/backtest_contract.md` (frozen,
  QR-INFRA-001).
- Implementer self-report: `docs/reports/qr_data_001_implementation.md` — check its **mtime**
  against the mtimes of `src/data/**` files before trusting it; it can be stale relative to later
  code changes (see [[qr_data_001_findings]] for a concrete case where it predated ~half the
  shipped modules).
- Code: `src/data/base.py, schemas.py, provenance.py, storage.py, validation.py, universe.py,
  symbol_map.py, segments.py, aggregation.py, rate_limit.py`, plus `src/data/hyperliquid/**` and
  `src/data/binance/**`. Providers must not import each other; restricted core modules must not
  import either provider package (`tests/data/test_layering.py`, AST-based, self-tests its own
  discrimination — this is a well-built test).
- Real ingested data (not synthetic): `data/processed/binance/ohlcv/1h/*.parquet` (210 symbols,
  ~206MB) + `data/metadata/binance/*.json` sidecars + `data/metadata/_crossvenue_report.json` +
  `data/metadata/binance/_ingest_summary.json`. None of this is git-tracked (whole `docs/`,
  `src/data/`, `tests/data/`, `data/` tree was untracked/uncommitted at audit time 2026-08-16) —
  `git diff`/`git checkout` are NOT usable as a mutation-testing safety net here; back up files to
  the scratchpad before mutating and restore with `cp`, then diff against the backup to confirm a
  clean revert.
- Root `conftest.py` gates `@pytest.mark.integration` tests behind `--run-integration`; this is the
  only skip mechanism in the data test suite (verified no other skip/xfail/env-guard patterns
  exist across `tests/data/*.py`).
