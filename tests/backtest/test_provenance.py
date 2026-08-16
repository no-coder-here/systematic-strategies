"""§18.10 — Provenance: PR1-PR6.

Config: initial_capital=1_000_000, frequency="1d", execution_mode="next_open",
execution_lag=1, fee_bps=0, slippage_bps=0, funding_mode="disabled",
annualization_factor=365, compute_counterfactual=False. A minimal 3-bar
single-symbol run; no numeric result is asserted.
"""

import numpy as np
import pytest

from backtest.engine import run_backtest
from backtest.models import (
    BacktestConfig,
    DataIntegrityError,
    DatasetProvenance,
    StrategyOutput,
    UniverseProvenance,
)

from helpers import dates, mask_series, md, single_symbol_frame


def _fixture():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        execution_mode="next_open",
        execution_lag=1,
        fee_bps=0,
        slippage_bps=0,
        funding_mode="disabled",
        annualization_factor=365,
        compute_counterfactual=False,
    )
    return cfg, prices, so


def test_PR1_supplied_provenance_appears_unmodified():
    cfg, prices, so = _fixture()
    prov = DatasetProvenance(
        source_venue="hyperliquid",
        field_type="ohlcv",
        time_range=(prices.index[0], prices.index[-1]),
        native_or_proxy="native",
    )
    res = run_backtest(cfg, md(prices), so, dataset_provenance=[prov])
    assert res.provenance[0] is prov
    assert res.provenance[0].field_type == "ohlcv"
    assert res.provenance[0].time_range == (prices.index[0], prices.index[-1])


def test_PR2_absent_provenance_supplied_false():
    cfg, prices, so = _fixture()
    res = run_backtest(cfg, md(prices), so)
    assert res.provenance_supplied is False


def test_PR3_proxy_data_sets_uses_proxy_data_and_surfaced_in_repr():
    cfg, prices, so = _fixture()
    prov = DatasetProvenance(
        source_venue="binance", field_type="ohlcv",
        time_range=(prices.index[0], prices.index[-1]),
        native_or_proxy="proxy", proxy_for="hyperliquid perp OHLCV",
    )
    res = run_backtest(cfg, md(prices), so, dataset_provenance=[prov])
    assert res.uses_proxy_data is True
    assert "uses_proxy_data=True" in repr(res)


def test_PR4_proxy_without_proxy_for_raises():
    with pytest.raises(DataIntegrityError):
        DatasetProvenance(source_venue="binance", field_type="ohlcv", time_range=(0, 1), native_or_proxy="proxy")


def test_PR5_all_none_provenance_gives_provenance_complete_false():
    cfg, prices, so = _fixture()
    prov = DatasetProvenance()
    res = run_backtest(cfg, md(prices), so, dataset_provenance=[prov])
    assert res.provenance_complete is False


def test_BD15_pr5_multi_object_every_supplied_object_must_be_complete():
    """BD-15 (§13.1 obligation 5) — untested with more than one provenance
    object. §13.1.5: "`provenance_complete = True` only when EVERY supplied
    object has non-`None` `source_venue`, `field_type`, `time_range` and
    `native_or_proxy`". A single-object PR5 case cannot discriminate
    `all(...)` from `any(...)` at the aggregation site, because
    `all([False]) == any([False])`. This requires >= 2 objects, one
    complete and one incomplete, where `all([True, False]) == False` but
    `any([True, False]) == True`.
    """
    cfg, prices, so = _fixture()
    complete = DatasetProvenance(
        source_venue="hyperliquid",
        field_type="ohlcv",
        time_range=(prices.index[0], prices.index[-1]),
        native_or_proxy="native",
    )
    incomplete = DatasetProvenance()  # all-None
    res = run_backtest(cfg, md(prices), so, dataset_provenance=[complete, incomplete])
    # Must fail if the aggregation is `any(...)` instead of `all(...)`.
    assert res.provenance_complete is False

    res_both_complete = run_backtest(
        cfg, md(prices), so, dataset_provenance=[complete, complete]
    )
    assert res_both_complete.provenance_complete is True


def test_PR6_universe_provenance_passthrough_and_survivorship_defaults():
    cfg, prices, so = _fixture()

    up_unsupplied_result = run_backtest(cfg, md(prices), so)
    assert up_unsupplied_result.survivorship_safe is None

    up = UniverseProvenance(universe_source="manual", survivorship_safe=None)
    res = run_backtest(cfg, md(prices), so, universe_provenance=up)
    assert res.universe_provenance is up
    assert res.survivorship_safe is None
    assert "survivorship_safe=None" in repr(res)

    up_false = UniverseProvenance(universe_source="manual", survivorship_safe=False)
    res_false = run_backtest(cfg, md(prices), so, universe_provenance=up_false)
    assert res_false.survivorship_safe is False
    assert "survivorship_safe=False" in repr(res_false)

    up_true = UniverseProvenance(universe_source="manual", survivorship_safe=True)
    res_true = run_backtest(cfg, md(prices), so, universe_provenance=up_true)
    assert res_true.survivorship_safe is True
