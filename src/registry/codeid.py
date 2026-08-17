"""R§6 — code identity capture (the `code_fingerprint` / git dirty-state logic).

Deliberately independent of git for the fingerprint itself (R§6.1): a commit
hash describes the index, not the working tree, and untracked in-scope files
are invisible to `git diff`-based checks. `code_fingerprint` is computed from
file *contents*, hashed independently of git, and is REQUIRED, never `None`.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

from .models import CodeIdentity, RegistryError
from .serialize import canonical_json

__all__ = ["CODE_SCOPE_PATTERNS", "capture_code_identity", "verify_code_state"]

# R§6.3 — module constant. Changing this changes `code_fingerprint` for
# every subsequent run anyway (R§5.2.1), so identity is never silently
# reused when the scope itself changes.
CODE_SCOPE_PATTERNS = (
    "src/**/*.py",
    "strategies/**/*.py",
    "experiments/**/*.py",
    "scripts/**/*.py",
    "conftest.py",
    "pyproject.toml",
)

_EXCLUDE_SUBSTRINGS = ("/__pycache__/", "/.venv/", "/artifacts/", "/.git/")
# R§6.3 — the registry's own storage is excluded, otherwise the fingerprint
# would be self-referential (writing a record would change the fingerprint
# used to write the NEXT record) and unreproducible.
_EXCLUDE_PREFIX = "experiments/registry/"


def _in_scope(relpath: str) -> bool:
    if relpath.endswith(".pyc"):
        return False
    if any(sub in f"/{relpath}/" for sub in _EXCLUDE_SUBSTRINGS):
        return False
    if relpath.startswith(_EXCLUDE_PREFIX):
        return False
    return True


def _scoped_files(repo_root: Path, scope_patterns) -> list:
    files = set()
    for pattern in scope_patterns:
        for p in repo_root.glob(pattern):
            if not p.is_file():
                continue
            relpath = p.resolve().relative_to(repo_root.resolve()).as_posix()
            if _in_scope(relpath):
                files.add(relpath)
    # R§6.2/R§16.2 — glob output sorted before use; no filesystem-enumeration
    # order may leak into the fingerprint.
    return sorted(files)


def _run_git(repo_root: Path, args: list, timeout: float) -> Optional[subprocess.CompletedProcess]:
    try:
        # R§6.2 (MW1) — `-C <repo_root>` is MANDATORY: omitting it would make
        # a fixture-repo capture silently inherit the LIVE repo's HEAD,
        # rendering the R§6.4 test-isolation guarantee inert.
        return subprocess.run(
            ["git", "-C", str(repo_root)] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None


def _matches_scope_pattern(relpath: str, pattern: str) -> bool:
    """R§20.8.7 (blocking, MW-A1) — matches `relpath` against a
    `CODE_SCOPE_PATTERNS`-shaped glob PATTERN, never against an on-disk
    listing. Measured defect this replaces: `_scoped_files()` is a
    `Path.glob()` over files that EXIST NOW, so a DELETED in-scope file is
    invisible to it — `git status` reports it (`"D"`), but the old
    `in_scope_set = set(_scoped_files(...))` membership test silently
    excluded it, making `dirty_worktree` read `False` with an empty
    `dirty_summary` for a worktree with a staged/unstaged deletion.

    Only the two pattern shapes this module's constant actually uses need
    supporting: an exact literal path (`"conftest.py"`, `"pyproject.toml"`)
    and a `"<dir>/**/*.<ext>"` recursive-glob shape.
    """
    if "**" not in pattern:
        return relpath == pattern
    prefix, _, suffix = pattern.partition("**")
    if not relpath.startswith(prefix):
        return False
    rest = relpath[len(prefix):]
    if suffix.startswith("/"):
        suffix = suffix[1:]
    if suffix.startswith("*"):
        return rest.endswith(suffix[1:])
    return rest == suffix


def _in_scope_by_pattern(relpath: str, scope_patterns) -> bool:
    if relpath.endswith(".pyc"):
        return False
    if any(sub in f"/{relpath}/" for sub in _EXCLUDE_SUBSTRINGS):
        return False
    if relpath.startswith(_EXCLUDE_PREFIX):
        return False
    return any(_matches_scope_pattern(relpath, p) for p in scope_patterns)


def _parse_porcelain(output: str, repo_root: Path, scope_patterns) -> tuple:
    """Returns (dirty_summary: dict, untracked_code_files: int, any_in_scope: bool).

    R§20.8.7 — classification is by PATTERN MATCH against `relpath`, not by
    membership in an on-disk file listing, so a deleted in-scope file (git
    status code `"D"`, which no longer exists to be glob'd) is still counted.
    """
    dirty_summary: dict = {}
    untracked = 0
    any_in_scope = False
    for line in output.splitlines():
        if not line:
            continue
        # porcelain v1: "XY <path>" (rename lines carry " -> "); take the
        # path after the status code and, for renames, the destination path.
        code = line[:2]
        path_part = line[3:]
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        relpath = path_part.strip().strip('"')
        if not _in_scope_by_pattern(relpath, scope_patterns):
            continue
        any_in_scope = True
        stripped = code.strip()
        dirty_summary[stripped] = dirty_summary.get(stripped, 0) + 1
        if stripped == "??":
            untracked += 1
    return dirty_summary, untracked, any_in_scope


def capture_code_identity(
    repo_root: Path,
    *,
    scope_patterns=CODE_SCOPE_PATTERNS,
    contract_versions: dict,
    git_timeout: float = 5.0,
) -> CodeIdentity:
    """R§6.4 — captures `CodeIdentity` for `repo_root`.

    `contract_versions` MUST already be assembled by the caller per R§4.3.1
    (backtest_contract / data_contract / registry_schema / the imported
    `data.provenance.PROCESSING_VERSION` constant) — this function does not
    import `backtest`/`data` so as to keep R§2.1's layering one-directional;
    it only fingerprints files and reads git state.
    """
    repo_root = Path(repo_root)
    files = _scoped_files(repo_root, scope_patterns)
    if not files:
        raise RegistryError(
            f"code_fingerprint scope produced zero files under {repo_root!r} with patterns "
            f"{scope_patterns!r} (R§6.2) — a zero-file fingerprint would be constant"
        )

    hasher = hashlib.sha256()
    pairs = []
    for relpath in files:
        content = (repo_root / relpath).read_bytes()
        pairs.append([relpath, hashlib.sha256(content).hexdigest()])
    fingerprint = hashlib.sha256(canonical_json(pairs).encode("utf-8")).hexdigest()

    proc = _run_git(repo_root, ["rev-parse", "HEAD"], git_timeout)
    if proc is None or proc.returncode != 0:
        return CodeIdentity(
            git_commit=None,
            git_available=False,
            dirty_worktree=True,
            dirty_summary={"git_unavailable": 1},
            untracked_code_files=0,
            code_fingerprint=fingerprint,
            code_fingerprint_n_files=len(files),
            code_scope_patterns=tuple(scope_patterns),
            contract_versions=dict(contract_versions),
        )
    git_commit = proc.stdout.strip()

    status_proc = _run_git(repo_root, ["status", "--porcelain", "--untracked-files=all"], git_timeout)
    if status_proc is None or status_proc.returncode != 0:
        return CodeIdentity(
            git_commit=git_commit,
            git_available=False,
            dirty_worktree=True,
            dirty_summary={"git_unavailable": 1},
            untracked_code_files=0,
            code_fingerprint=fingerprint,
            code_fingerprint_n_files=len(files),
            code_scope_patterns=tuple(scope_patterns),
            contract_versions=dict(contract_versions),
        )

    dirty_summary, untracked, any_in_scope = _parse_porcelain(status_proc.stdout, repo_root, scope_patterns)
    return CodeIdentity(
        git_commit=git_commit,
        git_available=True,
        dirty_worktree=any_in_scope,
        dirty_summary=dirty_summary,
        untracked_code_files=untracked,
        code_fingerprint=fingerprint,
        code_fingerprint_n_files=len(files),
        code_scope_patterns=tuple(scope_patterns),
        contract_versions=dict(contract_versions),
    )


def verify_code_state(record_code: CodeIdentity, repo_root: Path) -> str:
    """R§20.7.3 (blocking) — recomputes the fingerprint at `repo_root` under
    `record_code.code_scope_patterns` and compares it against
    `record_code.code_fingerprint`. Returns one of:

    - `"MATCH"` — the fingerprint recomputes identically; the code state the
      record claims genuinely exists at `repo_root` right now.
    - `"CODE_FINGERPRINT_MISMATCH"` — `repo_root` is a real, scoped-file-
      producing checkout, but the recomputed fingerprint differs. Measured
      finding this exists to catch: all five v1.1 records pinned a
      `code_fingerprint` matching no state that existed anywhere, one day
      later, with `DIRTY_WORKTREE` present but nothing telling the reader the
      fingerprint itself was ALREADY unresolvable.
    - `"UNVERIFIABLE"` — the scope produced zero files, the path does not
      exist, or fingerprinting otherwise raised. Distinct from a mismatch: it
      means "cannot even attempt the comparison", not "attempted and failed".
    """
    try:
        files = _scoped_files(Path(repo_root), record_code.code_scope_patterns)
        if not files:
            return "UNVERIFIABLE"
        pairs = []
        for relpath in files:
            content = (Path(repo_root) / relpath).read_bytes()
            pairs.append([relpath, hashlib.sha256(content).hexdigest()])
        fingerprint = hashlib.sha256(canonical_json(pairs).encode("utf-8")).hexdigest()
    except OSError:
        return "UNVERIFIABLE"
    return "MATCH" if fingerprint == record_code.code_fingerprint else "CODE_FINGERPRINT_MISMATCH"
