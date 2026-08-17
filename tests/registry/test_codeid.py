"""R§18.1(4) — dirty worktree / code identity capture (R§6).

Covers M11 (dirty_worktree without --untracked-files=all) and M12 (git
failure / missing -C repo_root isolation).
"""
from __future__ import annotations

import subprocess

import pytest

from registry.codeid import capture_code_identity
from registry.models import RegistryError

from _factories import CONTRACT_VERSIONS, make_git_repo


def _live_repo_head() -> str:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    out = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_clean_repo_is_not_dirty(tmp_path):
    root = tmp_path / "repo"
    make_git_repo(root, files={"src/foo.py": "x = 1\n"})
    ident = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    assert ident.dirty_worktree is False
    assert ident.git_available is True
    assert ident.untracked_code_files == 0
    assert ident.code_fingerprint_n_files == 1


def test_modified_tracked_file_is_dirty_and_changes_fingerprint(tmp_path):
    root = tmp_path / "repo"
    make_git_repo(root, files={"src/foo.py": "x = 1\n"})
    clean = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    (root / "src" / "foo.py").write_text("x = 2\n")
    dirty = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    assert dirty.dirty_worktree is True
    assert dirty.dirty_summary.get("M") == 1
    assert dirty.code_fingerprint != clean.code_fingerprint


def test_M11_untracked_in_scope_file_is_dirty(tmp_path):
    """M11 target: requires `--untracked-files=all`. Measured (self-guarding
    below): dropping that flag is VACUOUS against an untracked file sitting
    in an ALREADY-tracked directory, because git's default "normal"
    untracked mode still lists such a file individually — only a file
    inside a WHOLLY NEW (never-tracked) directory gets collapsed to a
    single `?? <dir>/` line under the default mode, which then fails to
    match any individual scoped file and produces a false-clean result.
    This fixture therefore puts the untracked file in a brand-new
    subdirectory with no tracked sibling file."""
    root = tmp_path / "repo"
    make_git_repo(root, files={"src/foo.py": "x = 1\n"})
    (root / "src" / "newmod").mkdir()
    (root / "src" / "newmod" / "bar.py").write_text("y = 1\n")  # untracked, in a wholly-new dir

    # Self-guard: confirm the default ("normal") untracked mode really does
    # collapse this to a single directory line, distinct from the
    # individual-file line `--untracked-files=all` produces.
    import subprocess

    default_mode = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    assert "?? src/newmod/\n" in default_mode
    assert "?? src/newmod/bar.py" not in default_mode

    ident = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    assert ident.dirty_worktree is True
    assert ident.untracked_code_files == 1
    assert ident.dirty_summary.get("??") == 1
    assert ident.code_fingerprint_n_files == 2


def test_out_of_scope_untracked_file_does_not_dirty(tmp_path):
    root = tmp_path / "repo"
    make_git_repo(root, files={"src/foo.py": "x = 1\n"})
    (root / "README.md").write_text("not in scope\n")
    ident = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    assert ident.dirty_worktree is False


def test_M12_git_dashC_isolation_fixture_differs_from_live_head(tmp_path):
    """M12 target: without `-C <repo_root>`, a fixture-repo capture would
    silently inherit the LIVE repo's HEAD. Prove isolation by asserting the
    fixture's captured commit differs from the live repo's actual HEAD."""
    root = tmp_path / "repo"
    fixture_commit = make_git_repo(root, files={"src/foo.py": "x = 1\n"})
    ident = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    assert ident.git_commit == fixture_commit
    assert ident.git_commit != _live_repo_head()


def test_git_unavailable_forces_dirty_and_no_commit(tmp_path):
    root = tmp_path / "not_a_repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "foo.py").write_text("x = 1\n")
    ident = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    assert ident.git_available is False
    assert ident.git_commit is None
    assert ident.dirty_worktree is True
    assert ident.dirty_summary == {"git_unavailable": 1}


def test_zero_file_scope_raises(tmp_path):
    root = tmp_path / "empty_repo"
    make_git_repo(root, files={"README.md": "x\n"})
    with pytest.raises(RegistryError):
        capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))


def test_capture_from_live_repo_asserts_only_structural_properties():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    ident = capture_code_identity(repo_root, contract_versions=dict(CONTRACT_VERSIONS))
    assert len(ident.code_fingerprint) == 64
    assert all(c in "0123456789abcdef" for c in ident.code_fingerprint)
    assert ident.code_fingerprint_n_files > 0


def test_changing_scope_patterns_changes_fingerprint(tmp_path):
    """R§5.2.1 — a test MUST assert that changing the scope patterns changes
    the fingerprint."""
    root = tmp_path / "repo"
    make_git_repo(root, files={"src/foo.py": "x = 1\n", "scripts/bar.py": "y = 1\n"})
    a = capture_code_identity(root, scope_patterns=("src/**/*.py",), contract_versions=dict(CONTRACT_VERSIONS))
    b = capture_code_identity(root, scope_patterns=("src/**/*.py", "scripts/**/*.py"), contract_versions=dict(CONTRACT_VERSIONS))
    assert a.code_fingerprint != b.code_fingerprint
    assert a.code_scope_patterns != b.code_scope_patterns
