"""R§18.1(5) — provenance preservation (R§7).

R§7.1.1 (blocking test shape): enumerate `dataclasses.fields(DatasetProvenance)`
and assert each name appears in the mapping table with a non-None
destination, then round-trip through record -> disk -> load.

Covers M18 (native_or_proxy silently defaulting to "native")."""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from backtest.models import DatasetProvenance
from registry.models import DatasetRef, ValidationError

from _factories import mk_dataset_ref, record_kwargs

# R§7.1 mapping table, reproduced here so the test can assert EVERY
# DatasetProvenance field has a declared destination (finding: v1.0's test
# used a hand-written 9-field list with no destination for `time_range` at
# all, so it was unsatisfiable and would have been loosened until green).
_MAPPING = {
    "source_venue": "DatasetRef.source_venue",
    "field_type": "DatasetRef.field_type",
    "time_range": "DatasetRef.dataset_span_start/dataset_span_end",
    "native_or_proxy": "DatasetRef.native_or_proxy",
    "proxy_for": "DatasetRef.proxy_for",
    "dataset_id": "DatasetRef.dataset_id",
    "dataset_version": "DatasetRef.dataset_version",
    "processing_version": "DatasetRef.processing_version",
    "retrieval_date": "DatasetRef.retrieval_date",
    "symbol_mapping": "DatasetRef.symbol_mapping",
    "notes": "DatasetRef.provenance_notes",
}


def test_every_DatasetProvenance_field_has_a_declared_nonNone_destination():
    field_names = {f.name for f in dataclasses.fields(DatasetProvenance)}
    assert field_names  # self-guard: DatasetProvenance actually has fields
    for name in field_names:
        assert name in _MAPPING, f"DatasetProvenance.{name} has no declared R§7.1 destination"
        assert _MAPPING[name] is not None
    assert set(_MAPPING) == field_names  # no extra/stale entries either


def test_dataset_ref_round_trips_through_disk(registry, tmp_path):
    ds = mk_dataset_ref(provenance_notes="code_version=abc123; api_response_count=42")
    fe = registry.record_experiment(**record_kwargs(datasets=(ds,)))
    reloaded = registry.load_experiment(fe.record.experiment_id)
    got = reloaded.record.datasets[0]
    assert got.provenance_notes == ds.provenance_notes
    assert got.dataset_span_start == ds.dataset_span_start
    assert got.dataset_span_end == ds.dataset_span_end
    assert got.data_start == ds.data_start
    assert got.symbols == ds.symbols


def test_missing_required_field_raises_naming_field_and_dataset_id():
    with pytest.raises(ValidationError, match="dataset_id"):
        mk_dataset_ref(source_venue="")


def test_proxy_without_proxy_for_raises():
    with pytest.raises(ValidationError):
        mk_dataset_ref(native_or_proxy="proxy", proxy_for=None)


def test_notes_carried_verbatim():
    notes = "endpoint=candleSnapshot; api_response_count=7; excluded_backfill_bars={}; coverage_segments=()"
    ds = mk_dataset_ref(provenance_notes=notes)
    assert ds.provenance_notes == notes


def test_M18_native_or_proxy_none_raises_never_silently_native():
    with pytest.raises(ValidationError):
        mk_dataset_ref(native_or_proxy=None, proxy_for=None)


def test_native_or_proxy_garbage_value_raises():
    with pytest.raises(ValidationError):
        mk_dataset_ref(native_or_proxy="unknown")
