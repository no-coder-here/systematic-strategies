"""R§18.1(8) — query/filter behaviour (R§13).

Covers M9 (unknown filter silently ignored), M10 (symbol substring match),
M28 (filters evaluate creation-time status instead of folded)."""
from __future__ import annotations

import pandas as pd
import pytest

from registry.models import ValidationError

from _factories import mk_dataset_ref, mk_strategy_ref, record_kwargs


def test_M9_unknown_filter_raises(registry):
    registry.record_experiment(**record_kwargs())
    with pytest.raises(ValidationError):
        registry.find_experiments(strategy_versoin="1.0")  # typo'd keyword


def test_M10_symbol_filter_is_exact_not_substring(registry):
    btc = registry.record_experiment(**record_kwargs(datasets=(mk_dataset_ref(symbols=("BTC",)),)))
    btcdom = registry.record_experiment(
        **record_kwargs(
            reason_for_run="btcdom",
            datasets=(mk_dataset_ref(dataset_id="hyperliquid.ohlcv.1h.BTCDOM", symbols=("BTCDOM",)),),
        )
    )
    got = registry.find_experiments(symbol="BTC")
    ids = {fe.record.experiment_id for fe in got}
    assert ids == {btc.record.experiment_id}
    assert btcdom.record.experiment_id not in ids


def test_M28_filters_evaluate_folded_status(registry):
    fe = registry.record_experiment(**record_kwargs())
    registry.set_status(fe.record.experiment_id, "REJECTED", "bad Sharpe on reflection")
    completed = registry.find_experiments(status="COMPLETED")
    rejected = registry.find_experiments(status="REJECTED")
    assert fe.record.experiment_id not in {x.record.experiment_id for x in completed}
    assert fe.record.experiment_id in {x.record.experiment_id for x in rejected}


def test_and_across_filters_or_within_collection(registry):
    btc = registry.record_experiment(**record_kwargs(datasets=(mk_dataset_ref(symbols=("BTC",)),)))
    eth = registry.record_experiment(
        **record_kwargs(reason_for_run="eth", datasets=(mk_dataset_ref(dataset_id="hyperliquid.ohlcv.1h.ETH", symbols=("ETH",)),))
    )
    sol = registry.record_experiment(
        **record_kwargs(reason_for_run="sol", datasets=(mk_dataset_ref(dataset_id="hyperliquid.ohlcv.1h.SOL", symbols=("SOL",)),))
    )
    got = registry.find_experiments(symbol=["BTC", "ETH"])
    ids = {fe.record.experiment_id for fe in got}
    assert ids == {btc.record.experiment_id, eth.record.experiment_id}

    got2 = registry.find_experiments(symbol=["BTC", "ETH"], experiment_type="pipeline_validation")
    assert {fe.record.experiment_id for fe in got2} == ids


def test_funding_disabled_filter(registry):
    disabled = registry.record_experiment(
        **record_kwargs(backtest_config={"funding_mode": "disabled", "funding_notional_basis": "not_modelled"})
    )
    required = registry.record_experiment(
        **record_kwargs(reason_for_run="funded", backtest_config={"funding_mode": "required", "funding_notional_basis": "period_start"})
    )
    got = registry.find_experiments(funding_disabled=True)
    assert {fe.record.experiment_id for fe in got} == {disabled.record.experiment_id}


def test_survivorship_safe_tristate_and_unknown_literal(registry):
    unknown = registry.record_experiment(**record_kwargs(survivorship_safe=None))
    unsafe = registry.record_experiment(**record_kwargs(reason_for_run="unsafe", survivorship_safe=False))
    got_unknown = registry.find_experiments(survivorship_safe="unknown")
    assert {fe.record.experiment_id for fe in got_unknown} == {unknown.record.experiment_id}
    got_unknown_none = registry.find_experiments(survivorship_safe=None)
    assert {fe.record.experiment_id for fe in got_unknown_none} == {unknown.record.experiment_id}
    got_unsafe = registry.find_experiments(survivorship_safe=False)
    assert {fe.record.experiment_id for fe in got_unsafe} == {unsafe.record.experiment_id}


def test_created_after_before_inclusive_and_require_tz_aware(registry):
    fe = registry.record_experiment(**record_kwargs(created_at=pd.Timestamp("2026-06-01", tz="UTC")))
    got = registry.find_experiments(created_after=pd.Timestamp("2026-06-01", tz="UTC"))
    assert fe.record.experiment_id in {x.record.experiment_id for x in got}
    got2 = registry.find_experiments(created_before=pd.Timestamp("2026-06-01", tz="UTC"))
    assert fe.record.experiment_id in {x.record.experiment_id for x in got2}
    with pytest.raises(ValidationError):
        registry.find_experiments(created_after=pd.Timestamp("2026-06-01"))


def test_warning_token_prefix_match(registry):
    fe = registry.record_experiment(
        **record_kwargs(datasets=(mk_dataset_ref(native_or_proxy="proxy", proxy_for="Hyperliquid"),))
    )
    got = registry.find_experiments(warning_token="PROXY")
    assert fe.record.experiment_id in {x.record.experiment_id for x in got}


def test_tag_filter(registry):
    fe = registry.record_experiment(**record_kwargs(tags=("smoke", "windowA")))
    got = registry.find_experiments(tag="windowA")
    assert fe.record.experiment_id in {x.record.experiment_id for x in got}
    assert registry.find_experiments(tag="nope") == ()
