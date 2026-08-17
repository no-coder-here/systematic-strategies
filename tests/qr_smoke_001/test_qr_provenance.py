"""spec §4.5 — provenance survival, incl. serialization to the artifact
(BD-A), and cross-validation of the ENGINE-FED price frame against the
declared venue, bitwise, over ALL bars (BD-B).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.models import DataIntegrityError, DatasetProvenance, MarketData
from experiments.qr_smoke_001.run_all import summarize


def test_window_a_hl_native_provenance_flags(window_a):
    r = window_a.result
    assert r.provenance_supplied is True
    assert r.provenance_complete is True
    assert r.uses_proxy_data is False  # Window A is HL-native price only
    assert r.survivorship_safe is False


def test_window_b1_binance_proxy_provenance_flags(window_b1):
    r = window_b1.result
    assert r.uses_proxy_data is True
    assert r.funding_modelled is False
    assert r.survivorship_safe is False
    prov = window_b1.dataset_provenance[0]
    assert prov.native_or_proxy == "proxy"
    assert prov.source_venue == "Binance"
    assert prov.proxy_for  # non-empty (contract §13.1 obligation 4)


def test_window_b2_mixed_provenance_flags(window_b2):
    r = window_b2.result
    assert r.uses_proxy_data is True  # Binance price
    assert r.funding_modelled is True  # HL-native funding
    price_prov = [p for p in window_b2.dataset_provenance if p.field_type == "ohlcv"][0]
    funding_prov = [p for p in window_b2.dataset_provenance if p.field_type == "funding_rate"][0]
    assert price_prov.source_venue == "Binance"
    assert price_prov.native_or_proxy == "proxy"
    assert funding_prov.source_venue == "Hyperliquid"
    assert funding_prov.native_or_proxy == "native"


def test_proxy_without_proxy_for_raises():
    """contract §13.1 obligation 4 — `native_or_proxy == 'proxy'` with empty
    `proxy_for` MUST raise `DataIntegrityError`."""
    with pytest.raises(DataIntegrityError):
        DatasetProvenance(source_venue="Binance", field_type="ohlcv", native_or_proxy="proxy", proxy_for=None)


def test_binance_provenance_survives_serialization_to_artifact(window_b1):
    """spec §4.5 — 'Prove this survives serialization to the artifact, not
    merely that it is set in memory.' Serializes the summary artifact
    exactly as `run_all.py` does and re-parses it."""
    summary = summarize(window_b1)
    payload = json.dumps(summary, default=str)
    loaded = json.loads(payload)
    assert loaded["uses_proxy_data"] is True
    assert loaded["survivorship_safe"] is False
    assert loaded["funding_modelled"] is False


def test_a_binance_history_backtest_is_never_presentable_as_hl_native(window_b1):
    """spec §4.5 — 'A Binance-history backtest must never be presentable as
    having used Hyperliquid-native prices.'"""
    for prov in window_b1.dataset_provenance:
        if prov.field_type == "ohlcv":
            assert prov.source_venue != "Hyperliquid"
            assert prov.native_or_proxy == "proxy"


def test_survivorship_safe_never_defaults_true(window_a, window_b1, window_b2):
    for run in (window_a, window_b1, window_b2):
        assert run.result.survivorship_safe is False
        assert run.universe_provenance.survivorship_safe is False


# ---------------------------------------------------------------------------
# BD-A (v1.1 repair) — provenance MUST survive serialization to the WRITTEN
# artifact file, read back FROM DISK, not merely asserted in memory. Covers
# the full v1.1 §4.5 field list: source_venue, native_or_proxy, proxy_for,
# dataset_id, processing_version, target_execution_venue,
# funding_notional_basis, funding_modelled, uses_proxy_data, and universe
# provenance including survivorship_safe.
# ---------------------------------------------------------------------------


def _find_dataset(datasets, field_type):
    matches = [d for d in datasets if d["field_type"] == field_type]
    assert len(matches) == 1, f"expected exactly one {field_type!r} dataset, found {len(matches)}"
    return matches[0]


def test_artifact_on_disk_names_target_execution_venue(written_summary_artifact):
    """spec §4.5 v1.1 — `target_execution_venue` MUST exist in the
    serialized artifact. In v1.0's implementation it existed NOWHERE in the
    codebase."""
    for key in ("window_a", "window_b1", "window_b2"):
        prov = written_summary_artifact[key]["provenance"]
        assert prov["target_execution_venue"] == "Hyperliquid"


def test_artifact_on_disk_names_hyperliquid_and_binance_explicitly(written_summary_artifact):
    """spec §4.5 v1.1 — the artifact MUST name the actual source venues.
    v1.0's implementation named NEITHER Binance nor Hyperliquid anywhere in
    `summary.json`."""
    a_datasets = written_summary_artifact["window_a"]["provenance"]["datasets"]
    assert _find_dataset(a_datasets, "ohlcv")["source_venue"] == "Hyperliquid"
    assert _find_dataset(a_datasets, "ohlcv")["native_or_proxy"] == "native"
    assert _find_dataset(a_datasets, "funding_rate")["source_venue"] == "Hyperliquid"

    b1_datasets = written_summary_artifact["window_b1"]["provenance"]["datasets"]
    b1_price = _find_dataset(b1_datasets, "ohlcv")
    assert b1_price["source_venue"] == "Binance"
    assert b1_price["native_or_proxy"] == "proxy"
    assert b1_price["proxy_for"]  # non-empty, per contract §13.1 obligation 4

    b2_datasets = written_summary_artifact["window_b2"]["provenance"]["datasets"]
    assert _find_dataset(b2_datasets, "ohlcv")["source_venue"] == "Binance"
    assert _find_dataset(b2_datasets, "funding_rate")["source_venue"] == "Hyperliquid"


def test_artifact_on_disk_carries_dataset_id_and_processing_version(written_summary_artifact):
    """spec §4.5 v1.1 — `dataset_id` and `processing_version` MUST survive
    to the artifact for every dataset."""
    for key in ("window_a", "window_b1", "window_b2"):
        datasets = written_summary_artifact[key]["provenance"]["datasets"]
        assert len(datasets) >= 1
        for d in datasets:
            assert d["dataset_id"], f"{key}: dataset_id missing/empty for {d!r}"
            assert d["processing_version"], f"{key}: processing_version missing/empty for {d!r}"


def test_artifact_on_disk_carries_funding_and_proxy_flags(written_summary_artifact):
    """spec §4.5 v1.1 — `funding_notional_basis`, `funding_modelled`,
    `uses_proxy_data` MUST survive to the artifact."""
    a = written_summary_artifact["window_a"]["provenance"]
    assert a["funding_notional_basis"] == "period_start"
    assert a["funding_modelled"] is True
    assert a["uses_proxy_data"] is False

    b1 = written_summary_artifact["window_b1"]["provenance"]
    assert b1["funding_notional_basis"] == "not_modelled"
    assert b1["funding_modelled"] is False
    assert b1["uses_proxy_data"] is True

    b2 = written_summary_artifact["window_b2"]["provenance"]
    assert b2["funding_notional_basis"] == "period_start"
    assert b2["funding_modelled"] is True
    assert b2["uses_proxy_data"] is True


def test_artifact_on_disk_carries_universe_provenance_survivorship_safe(written_summary_artifact):
    """spec §4.5 v1.1 — universe provenance including `survivorship_safe`
    MUST survive to the artifact and MUST NOT default to `True` (contract
    §13.2)."""
    for key in ("window_a", "window_b1", "window_b2"):
        universe = written_summary_artifact[key]["provenance"]["universe"]
        assert universe is not None
        assert universe["survivorship_safe"] is False


def test_artifact_on_disk_b1_records_funding_not_modelled(written_summary_artifact):
    """spec §2.2 / §4.5 v1.1 — B1 additionally MUST record
    `funding_modelled = False`."""
    b1 = written_summary_artifact["window_b1"]
    assert b1["funding_modelled"] is False
    assert b1["provenance"]["funding_modelled"] is False


def test_artifact_on_disk_records_differing_signal_detail(written_summary_artifact):
    """spec §4.4 v1.1 (W4) — the differing-signal analysis MUST be written
    to the artifact: the timestamps plus close/SMA100/relative-margin on
    BOTH venues, not merely a count."""
    cv = written_summary_artifact["crossvenue"]
    n = cv["n_differing_signals"]
    detail = cv["differing_signal_detail"]
    assert len(detail) == n
    assert n > 0, "Window C is known (measured) to have differing signals; a detail list of length 0 would be suspicious"
    for row in detail:
        assert "timestamp" in row
        for venue in ("hl", "binance"):
            assert set(row[venue].keys()) >= {"close", "sma100", "relative_margin"}
            assert isinstance(row[venue]["close"], float)
            assert isinstance(row[venue]["sma100"], float)
            assert 0.0 <= row[venue]["relative_margin"] < 1.0  # marginal crossings only, not a structural blowup


# ---------------------------------------------------------------------------
# BD-B (v1.1 repair) — the price data ACTUALLY FED TO THE ENGINE (the
# `MarketData` frame handed to `run_backtest`, i.e. `WindowRun.frame_md`)
# MUST be cross-validated against the declared venue's persisted parquet,
# BITWISE, over ALL bars, for BOTH `open` and `close`. A five-bar,
# single-column check on an upstream provider frame (as in v1.0) does not
# close this gap: a half-splice (HL for part of the frame, Binance for the
# rest) passed the entire v1.0 suite.
# ---------------------------------------------------------------------------

SYMBOL = "BTC"


def _load_full_venue_open_close(base_dir: str, venue: str, symbol: str = SYMBOL):
    if venue == "hyperliquid":
        path = Path(base_dir) / "processed" / "hyperliquid" / "ohlcv" / "1h" / f"{symbol}.parquet"
    elif venue == "binance":
        path = Path(base_dir) / "processed" / "binance" / "ohlcv" / "1h" / f"{symbol}.parquet"
    else:
        raise ValueError(f"unknown venue {venue!r}")
    df = pd.read_parquet(path, columns=["timestamp", "symbol", "open", "close"])
    df = df.loc[df["symbol"] == symbol].sort_values("timestamp").set_index("timestamp")
    return df["open"], df["close"]


def _assert_engine_fed_frame_matches_venue_bitwise(frame_md: MarketData, venue: str, base_dir: str = "data", symbol: str = SYMBOL) -> None:
    """Compares the `MarketData` frame ACTUALLY HANDED TO `run_backtest`
    (not an upstream provider frame) against the declared venue's parquet,
    bitwise, over EVERY bar and BOTH `open` and `close`."""
    venue_open, venue_close = _load_full_venue_open_close(base_dir, venue, symbol)
    frame_open = frame_md.open[symbol]
    frame_close = frame_md.close[symbol]

    missing_open = frame_open.index.difference(venue_open.index)
    missing_close = frame_close.index.difference(venue_close.index)
    assert len(missing_open) == 0 and len(missing_close) == 0, (
        f"engine-fed frame contains timestamps absent from the {venue!r} parquet"
    )

    expected_open = venue_open.loc[frame_open.index].to_numpy()
    expected_close = venue_close.loc[frame_close.index].to_numpy()
    np.testing.assert_array_equal(
        frame_open.to_numpy(), expected_open,
        err_msg=f"engine-fed OPEN frame does not match the {venue!r} parquet bitwise over all bars (§4.5 v1.1)",
    )
    np.testing.assert_array_equal(
        frame_close.to_numpy(), expected_close,
        err_msg=f"engine-fed CLOSE frame does not match the {venue!r} parquet bitwise over all bars (§4.5 v1.1)",
    )


def test_window_a_engine_fed_frame_matches_hyperliquid_bitwise_all_bars(window_a):
    _assert_engine_fed_frame_matches_venue_bitwise(window_a.frame_md, "hyperliquid")


def test_window_b1_engine_fed_frame_matches_binance_bitwise_all_bars(window_b1):
    _assert_engine_fed_frame_matches_venue_bitwise(window_b1.frame_md, "binance")


def test_window_b2_engine_fed_frame_matches_binance_bitwise_all_bars(window_b2):
    _assert_engine_fed_frame_matches_venue_bitwise(window_b2.frame_md, "binance")


@pytest.fixture(scope="module")
def window_c_for_provenance():
    from experiments.qr_smoke_001.crossvenue import run_window_c

    return run_window_c(compute_counterfactual=False)


def test_window_c_hl_run_engine_fed_frame_matches_hyperliquid_bitwise_all_bars(window_c_for_provenance):
    hl_run, _binance_run = window_c_for_provenance
    _assert_engine_fed_frame_matches_venue_bitwise(hl_run.frame_md, "hyperliquid")


def test_window_c_binance_run_engine_fed_frame_matches_binance_bitwise_all_bars(window_c_for_provenance):
    _hl_run, binance_run = window_c_for_provenance
    _assert_engine_fed_frame_matches_venue_bitwise(binance_run.frame_md, "binance")


def test_bitwise_check_catches_a_half_splice_M18(window_c_for_provenance):
    """spec §4.5 v1.1 / M18 discrimination proof — the auditor demonstrated
    that a HALF-SPLICE (HL for the first half of the frame, Binance for the
    rest, fed to a run declared 'HL-native') passed v1.0's entire suite,
    because that suite only checked the first 5 bars of a PROVIDER frame's
    close column. This test proves the NEW check (all bars, open AND close,
    on the actual engine-fed `frame_md`) catches exactly that defect.
    """
    hl_run, binance_run = window_c_for_provenance
    assert hl_run.frame_index.equals(binance_run.frame_index)  # Window C = Window A, identical index

    split = len(hl_run.frame_index) // 2
    spliced_open = hl_run.frame_md.open.copy()
    spliced_close = hl_run.frame_md.close.copy()
    # Splice in Binance prices for the SECOND half only -- mimicking a
    # defect where a run labelled "HL-native" is partially fed Binance data.
    spliced_open.iloc[split:] = binance_run.frame_md.open.iloc[split:].to_numpy()
    spliced_close.iloc[split:] = binance_run.frame_md.close.iloc[split:].to_numpy()
    spliced_md = MarketData(open=spliced_open, close=spliced_close)

    with pytest.raises(AssertionError):
        _assert_engine_fed_frame_matches_venue_bitwise(spliced_md, "hyperliquid")


def test_bitwise_check_catches_full_binance_substitution_M18(window_c_for_provenance):
    """spec §4.5 v1.1 / M18 discrimination proof — full substitution of the
    Binance frame for the 'HL-native' run's engine input (the mutation the
    v1.0 M18 test itself was fitted to and could catch, kept here as a
    regression check against the NEW, stronger assertion)."""
    hl_run, binance_run = window_c_for_provenance
    with pytest.raises(AssertionError):
        _assert_engine_fed_frame_matches_venue_bitwise(binance_run.frame_md, "hyperliquid")
