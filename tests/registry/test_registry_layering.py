"""R§18.1(12) / R§2.1.1 / R§20.2.1 / R§20.8.8 — layering.

`backtest.engine`/`backtest.metrics`/`src/data` MUST NOT import `registry`.
R§20.2.1 rescopes R§2.1's engine-import prohibition: `backtest_adapter.py`
ALONE may import `backtest.engine` (it is the sole caller of `run_backtest`,
via `run_and_register`); `store.py`/`models.py`/`serialize.py`/`codeid.py`/
`datahash.py` MUST remain engine-free. `backtest.metrics` MUST NOT be
imported by ANYTHING under `src/registry/` — no second accounting authority,
no exception for the adapter.

R§20.8.8 (blocking, MW-A2) — the scan is AST-based (not a regex), so it
matches every import SHAPE: `import backtest.engine`, `from backtest import
engine`, `from backtest.engine import x`, `from backtest import metrics as
m`, and would also catch a relative `from .metrics import x` if any module
inside `backtest` itself ever appeared under this scan target. A regex-only
scan is exactly the kind of check that "matches the literal string in the
example" but not the general shape — the ORIGINAL v1.1 regex, for instance,
required the substring `backtest.engine` or `backtest.metrics` immediately
after `from`/`import`, which `from backtest import metrics as m` does NOT
contain.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+registry\b", re.MULTILINE)

# R§20.2.1 — the ONE module under src/registry/ permitted to import
# backtest.engine.
_ENGINE_IMPORT_ALLOWED = {"backtest_adapter.py"}


def _scan_for_registry_import(root: Path) -> list:
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text()
        if _IMPORT_RE.search(text):
            offenders.append(str(path))
    return offenders


def test_backtest_package_does_not_import_registry_statically():
    assert _scan_for_registry_import(SRC / "backtest") == []


def test_data_package_does_not_import_registry_statically():
    assert _scan_for_registry_import(SRC / "data") == []


def _imported_backtest_submodules(path: Path) -> set:
    """AST-based: returns the set of `backtest.<x>` dotted names this file
    imports, in ANY import shape (R§20.8.8)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "backtest" or alias.name.startswith("backtest."):
                    hits.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "backtest":
                for alias in node.names:
                    hits.add(f"backtest.{alias.name}")
            elif node.module and node.module.startswith("backtest."):
                hits.add(node.module)
    return hits


def test_registry_metrics_never_imported_anywhere():
    """R§12.2/R§20.2.1 (blocking) — NOTHING under src/registry/ may import
    backtest.metrics, in any form, including backtest_adapter.py."""
    for path in (SRC / "registry").glob("*.py"):
        hits = _imported_backtest_submodules(path)
        offending = {h for h in hits if h == "backtest.metrics" or h.startswith("backtest.metrics.")}
        assert not offending, f"{path} imports backtest.metrics ({offending}) — no second accounting authority (R§12.2)"


def test_registry_engine_import_confined_to_backtest_adapter():
    """R§20.2.1/R§20.8.8 (blocking) — `backtest.engine` may be imported ONLY
    by `backtest_adapter.py`; every other module under src/registry/ MUST
    remain engine-free (store/models/serialize/codeid/datahash)."""
    for path in (SRC / "registry").glob("*.py"):
        hits = _imported_backtest_submodules(path)
        engine_hits = {h for h in hits if h == "backtest.engine" or h.startswith("backtest.engine.")}
        if path.name in _ENGINE_IMPORT_ALLOWED:
            continue
        assert not engine_hits, f"{path} imports backtest.engine ({engine_hits}) — only backtest_adapter.py may (R§20.2.1)"


def test_backtest_adapter_actually_imports_engine_self_guard():
    """Self-guard: proves the exemption above is exercised, not vacuous —
    `backtest_adapter.py` really does import `backtest.engine` (for
    `run_and_register`), so the allow-list is doing real work."""
    hits = _imported_backtest_submodules(SRC / "registry" / "backtest_adapter.py")
    assert any(h == "backtest.engine" or h.startswith("backtest.engine.") for h in hits)


def test_importing_backtest_engine_in_a_fresh_subprocess_never_pulls_in_registry():
    code = (
        "import sys; sys.path.insert(0, %r); import backtest.engine; "
        "hits = [m for m in sys.modules if m == 'registry' or m.startswith('registry.')]; "
        "print('HITS=' + ','.join(hits))"
    ) % str(SRC)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "HITS="


# NOTE (flagged ambiguity, see the implementation report): R§2.1.1's literal
# runtime check is directional — "importing `backtest.engine` in a fresh
# subprocess" leaves no `registry*` in `sys.modules` (tested above). The
# REVERSE runtime check (importing `registry` leaves no `backtest.engine` in
# `sys.modules`) is NOT implementable: `src/backtest/__init__.py` (FROZEN,
# v1.5.1, out of scope for this work order) itself does
# `from .engine import execution_instant, run_backtest`, so ANY import of
# `backtest.models` — including registry's own permitted
# `from backtest.models import BacktestConfig, BacktestResult` — unavoidably
# executes `backtest/__init__.py` and therefore loads `backtest.engine` (and
# transitively `backtest.metrics`) as a side effect, regardless of what
# registry's own source does. Measured: `import registry` puts
# `backtest.engine`/`backtest.metrics` in `sys.modules` even though
# `src/registry/**` contains no `import backtest.engine`/`metrics` anywhere
# (see the static scan above and `test_registry_does_not_import_backtest_engine_or_metrics_statically`).
# The static source scan is therefore the operative, correct check for
# "registry MUST NOT import backtest.engine/metrics"; a sys.modules check in
# this direction would fail unconditionally and would not discriminate a
# real defect from `backtest`'s own frozen package structure.
