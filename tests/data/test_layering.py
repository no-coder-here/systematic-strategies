"""D§2.1 — NORMATIVE dependency-direction test, by STATIC AST import scan.

    `src/backtest/**` MUST NOT import `src/data/**`.
    `src/data/base.py`, `schemas.py`, `storage.py`, `validation.py`,
    `universe.py`, `provenance.py`, `segments.py`, `aggregation.py`,
    `rate_limit.py` and `symbol_map.py` MUST NOT import
    `src/data/hyperliquid/**` or `src/data/binance/**`.
    `src/data/hyperliquid/**` and `src/data/binance/**` MUST NOT import
    each other (AMENDMENT B, D§2.1) — cross-venue logic lives in
    `symbol_map.py` and the validation layer.

A runtime `sys.modules` check would pass vacuously if the module was never
imported by the current test run; this MUST be a static source scan (M16).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

BACKTEST_FILES = sorted((SRC / "backtest").glob("*.py"))
RESTRICTED_DATA_FILES = [
    SRC / "data" / "base.py",
    SRC / "data" / "schemas.py",
    SRC / "data" / "storage.py",
    SRC / "data" / "validation.py",
    SRC / "data" / "universe.py",
    SRC / "data" / "provenance.py",
    SRC / "data" / "segments.py",
    SRC / "data" / "aggregation.py",
    SRC / "data" / "rate_limit.py",
    SRC / "data" / "symbol_map.py",
]
HYPERLIQUID_FILES = sorted((SRC / "data" / "hyperliquid").glob("*.py"))
BINANCE_FILES = sorted((SRC / "data" / "binance").glob("*.py"))


def _imported_module_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # relative imports (node.level > 0) resolve within `data`
                # itself; represent them with their dotted level prefix so a
                # `from .hyperliquid import x` inside `data/__init__.py`-style
                # code would still be caught if ever introduced at the
                # restricted-file level.
                prefix = "." * node.level
                names.add(f"{prefix}{node.module}")
            elif node.level:
                names.add("." * node.level)
    return names


@pytest.mark.parametrize("path", BACKTEST_FILES, ids=lambda p: p.name)
def test_backtest_never_imports_data(path: Path):
    names = _imported_module_names(path)
    offending = [n for n in names if n == "data" or n.startswith("data.") or n == "src.data" or n.startswith("src.data.")]
    assert not offending, f"{path} imports data/**: {offending} (D§2.1, M16)"


@pytest.mark.parametrize("path", RESTRICTED_DATA_FILES, ids=lambda p: p.name)
def test_restricted_data_modules_never_import_hyperliquid(path: Path):
    names = _imported_module_names(path)
    offending = [
        n for n in names
        if n == "hyperliquid" or n.startswith("hyperliquid.")
        or n == ".hyperliquid" or n.startswith(".hyperliquid.")
        or n == "data.hyperliquid" or n.startswith("data.hyperliquid.")
    ]
    assert not offending, f"{path} imports data/hyperliquid/**: {offending} (D§2.1)"


@pytest.mark.parametrize("path", RESTRICTED_DATA_FILES, ids=lambda p: p.name)
def test_restricted_data_modules_never_import_binance(path: Path):
    names = _imported_module_names(path)
    offending = [
        n for n in names
        if n == "binance" or n.startswith("binance.")
        or n == ".binance" or n.startswith(".binance.")
        or n == "data.binance" or n.startswith("data.binance.")
    ]
    assert not offending, f"{path} imports data/binance/**: {offending} (D§2.1, AMENDMENT B)"


@pytest.mark.parametrize("path", HYPERLIQUID_FILES, ids=lambda p: p.name)
def test_hyperliquid_package_never_imports_binance(path: Path):
    names = _imported_module_names(path)
    offending = [
        n for n in names
        if n == "binance" or n.startswith("binance.")
        or n == "data.binance" or n.startswith("data.binance.")
    ]
    assert not offending, f"{path} imports data/binance/**: {offending} (D§2.1, AMENDMENT B: venues MUST NOT import each other)"


@pytest.mark.parametrize("path", BINANCE_FILES, ids=lambda p: p.name)
def test_binance_package_never_imports_hyperliquid(path: Path):
    names = _imported_module_names(path)
    offending = [
        n for n in names
        if n == "hyperliquid" or n.startswith("hyperliquid.")
        or n == "data.hyperliquid" or n.startswith("data.hyperliquid.")
        or (n.startswith(".") and "hyperliquid" in n)
    ]
    assert not offending, f"{path} imports data/hyperliquid/**: {offending} (D§2.1, AMENDMENT B: venues MUST NOT import each other)"


def test_ast_scan_actually_detects_a_violation_self_test():
    # Self-guarding: proves the scanner discriminates, by scanning a
    # synthetic in-memory violation via a temp file rather than trusting the
    # scanner's own claim of correctness.
    import tempfile

    src = "from data import base\nimport data.hyperliquid.provider\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        tmp_path = Path(f.name)
    try:
        names = _imported_module_names(tmp_path)
        offending = [n for n in names if n == "data" or n.startswith("data.")]
        assert offending, "self-test fixture must be detected as a violation"
    finally:
        tmp_path.unlink()
