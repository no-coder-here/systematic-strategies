---
name: ops-subagent-worktree-isolation
description: Subagents run in isolated git worktrees that lack gitignored data (data/processed absent), and their background jobs outlive them — so live-data work orders must be run in the main tree
metadata:
  type: feedback
---

Subagents in this project run in **isolated git worktrees**, even when `isolation` is not
requested. A fresh worktree contains only tracked files, so **everything gitignored is absent** —
including `data/processed/**` (the ~206 MB Binance corpus and the Hyperliquid parquet archive) and
`src/research/**` once it is still untracked.

**Why:** measured 2026-08-18 during QR-PREP-001. I launched three agents assuming a shared working
tree. Consequences, all real:
- The Binance re-ingest agent found no corpus to compare against, so its before/after byte-identity
  evidence was impossible to produce in-place.
- The Hyperliquid agent ran its backfill in a worktree with no pre-existing `BTC.parquet`, so the
  accretion union had nothing to union against and its report claimed BTC starts `2026-01-22`.
  **The main tree correctly starts `2026-01-20T11:00`** — re-running in the main tree is the only
  reason two days of irreplaceable rolling-window history survived.
- Full-suite runs inside a worktree produce dozens of spurious `FileNotFoundError` failures
  (18 failed / 70 errors observed) purely because market data is absent. Do not read those as
  regressions.

**Also:** background jobs an agent launches **outlive the agent and its worktree**. Two orphan
ingests kept writing after their agents ended; one had its cwd inside a worktree I deleted mid-run,
which made `git rev-parse HEAD` fail and silently wrote `code_version: null` into all 210 Binance
provenance sidecars. `ps aux` needs `dangerouslyDisableSandbox`.

**How to apply:**
- Delegate **code and offline/mocked tests** to subagents; run **anything touching live data or the
  persisted corpus yourself in the main tree**, or instruct the agent to pass an absolute
  `--data-dir` pointing at the main checkout.
- Tell auditors explicitly to read the main checkout by absolute path when the target is untracked,
  and to exclude `data/` from `git status` baselines when another job is writing.
- Before destructive worktree cleanup, check for live processes whose cwd is inside the target.
- There is no channel to message a running subagent (no SendMessage tool). Harvest its files from
  the worktree afterwards and finish the live steps yourself. See [[project-qr-data-001]] for the
  related "never revise a frozen spec while an agent runs" rule.

## Overnight/crash protection must not live in $TMPDIR

`$TMPDIR` is wiped by a sleep/wake or reboot cycle. I snapshotted the untracked work there to
guard against an agent dying mid-mutation; the machine slept and **the snapshot evaporated**,
while the untracked source files (`src/research/runner.py` etc.) have no copy in git at all.

**Why it matters:** an auditor mid-mutation-test leaves production code in a deliberately broken
state. If it dies there (both an API 403 and a machine-sleep killed agents on 2026-08-18/19), the
only good copy of an untracked file can be lost outright.

**How to apply:** snapshot to a gitignored directory **inside the repo** (`artifacts/` is already
in `.gitignore`), record sha256, and verify against it after any agent crash. Also: an auditor's
reported before/after hash is the reference to check a restore against — ask for it in the report.
