"""Root conftest: ensure `src/` is importable even without an editable install."""
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def pytest_addoption(parser):
    # D§11.3 (QR-DATA-001) — live network validation tests are marked
    # `@pytest.mark.integration` and MUST be skipped by default, never
    # required by the unit suite.
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.integration (live Hyperliquid network calls).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(reason="integration test skipped by default; pass --run-integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
