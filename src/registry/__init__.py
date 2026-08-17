"""QR-INFRA-002 — the experiment registry (`docs/experiment_registry_spec.md` v1.2, FROZEN).

R§2.1/R§20.2.1 (blocking, testable): `src/registry/store.py`, `models.py`,
`serialize.py`, `codeid.py` and `datahash.py` MAY import `backtest.models`
and `data.provenance` only, and MUST remain engine-free. R§20.2.1 rescopes
the prohibition to those five modules — `backtest_adapter.py` alone MAY
import `backtest.engine` (it is the sole caller of `run_backtest` on a
driver's behalf, via `run_and_register`); NOTHING under `src/registry/` may
import `backtest.metrics` (no second accounting authority). See
`tests/registry/test_registry_layering.py`.
"""

from .backtest_adapter import record_backtest_result, record_run, run_and_register
from .codeid import CODE_SCOPE_PATTERNS, capture_code_identity, verify_code_state
from .datahash import CONTENT_HASH_METHOD, hash_dataframe_content
from .models import (
    ArtifactRef,
    CodeIdentity,
    DatasetRef,
    EXPERIMENT_TYPES,
    ExperimentRecord,
    FIELD_TYPES,
    NATIVE_OR_PROXY_VALUES,
    RECORDED_VIA_VALUES,
    REPRODUCIBILITY_STATUSES,
    RESEARCH_STAGES,
    RegistryError,
    RegistryIntegrityError,
    ResultSummary,
    SCHEMA_VERSION,
    STATUSES,
    StrategyRef,
    ValidationError,
    config_family_payload,
)
from .serialize import SerializationError, canonical_json, decode, encode, stored_json
from .store import ExperimentRegistry, FoldedExperiment, ID_PREFIX_HEX

__all__ = [
    "ExperimentRegistry",
    "FoldedExperiment",
    "ID_PREFIX_HEX",
    "record_backtest_result",
    "record_run",
    "run_and_register",
    "capture_code_identity",
    "verify_code_state",
    "CODE_SCOPE_PATTERNS",
    "hash_dataframe_content",
    "CONTENT_HASH_METHOD",
    "ArtifactRef",
    "CodeIdentity",
    "DatasetRef",
    "ExperimentRecord",
    "ResultSummary",
    "StrategyRef",
    "SCHEMA_VERSION",
    "EXPERIMENT_TYPES",
    "STATUSES",
    "RESEARCH_STAGES",
    "FIELD_TYPES",
    "NATIVE_OR_PROXY_VALUES",
    "RECORDED_VIA_VALUES",
    "REPRODUCIBILITY_STATUSES",
    "ValidationError",
    "RegistryError",
    "RegistryIntegrityError",
    "SerializationError",
    "encode",
    "decode",
    "canonical_json",
    "stored_json",
    "config_family_payload",
]
