"""Pytest fixtures for the QR-INFRA-002 registry test suite.

Plain factory helpers live in `_factories.py` (a regular, non-conftest
module) so test modules can `from _factories import ...` them directly —
`conftest.py` itself is loaded specially by pytest and is not a reliable
import target from sibling test modules.

R§18.3 / CLAUDE.md workspace integrity — every fixture here writes to
`tmp_path` only, never to the real `experiments/registry/`.
"""
from __future__ import annotations

import pytest

from _factories import ExperimentRegistry


@pytest.fixture
def registry(tmp_path) -> ExperimentRegistry:
    return ExperimentRegistry(tmp_path / "registry")
