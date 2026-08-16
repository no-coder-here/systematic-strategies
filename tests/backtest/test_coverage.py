"""§18.11 — Coverage requirements.

Explicit tests for the config-validation surface (§15/§11.2) and other
cross-cutting requirements not already exercised by a dedicated §18.x test:
every BacktestConfig validation raises ConfigError, both execution_mode
values are exercised somewhere in the suite, both AccountingError sites are
distinct, and StrategyOutput's named constructors behave correctly.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.models import BacktestConfig, ConfigError, StrategyOutput


def _valid_kwargs(**overrides):
    kwargs = dict(
        initial_capital=1_000_000,
        frequency="1d",
        fee_bps=1.0,
        slippage_bps=1.0,
        execution_mode="next_open",
        execution_lag=1,
        funding_mode="disabled",
        annualization_factor=365,
        compute_counterfactual=False,
    )
    kwargs.update(overrides)
    return kwargs


def test_missing_frequency_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**{k: v for k, v in _valid_kwargs().items() if k != "frequency"})


def test_missing_fee_bps_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**{k: v for k, v in _valid_kwargs().items() if k != "fee_bps"})


def test_missing_slippage_bps_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**{k: v for k, v in _valid_kwargs().items() if k != "slippage_bps"})


def test_missing_funding_mode_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**{k: v for k, v in _valid_kwargs().items() if k != "funding_mode"})


def test_missing_annualization_factor_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**{k: v for k, v in _valid_kwargs().items() if k != "annualization_factor"})


def test_funding_notional_basis_required_when_funding_required():
    with pytest.raises(ConfigError):
        BacktestConfig(**_valid_kwargs(funding_mode="required", funding_notional_basis=None))
    # But fine when explicitly supplied.
    BacktestConfig(**_valid_kwargs(funding_mode="required", funding_notional_basis="period_start"))


def test_execution_lag_below_1_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**_valid_kwargs(execution_lag=0))


def test_bad_frequency_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**_valid_kwargs(frequency="2h"))


def test_bad_execution_mode_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**_valid_kwargs(execution_mode="prev_close"))


def test_bad_funding_mode_raises_configerror():
    with pytest.raises(ConfigError):
        BacktestConfig(**_valid_kwargs(funding_mode="sometimes"))


def test_valid_config_constructs_for_all_three_frequencies():
    for freq in ("1h", "4h", "1d"):
        cfg = BacktestConfig(**_valid_kwargs(frequency=freq))
        assert cfg.delta == pd.Timedelta(freq.replace("1d", "1D"))


def test_rebalance_mask_is_required_no_default():
    weights = pd.DataFrame({"A": [1.0]})
    with pytest.raises(TypeError):
        StrategyOutput(target_weights=weights)  # missing rebalance_mask -> constructor error


def test_step3_finiteness_guard_fires_distinctly_from_step10():
    """§18.11 — both AccountingError sites (§6.0 Steps 3 and 10) must be
    covered. X11 (test_ruin.py) covers Step 10 (non-finite NAV_end). This
    covers Step 3 (non-finite NAV_after_cost) independently: an infinite
    fee_bps blows up NAV_after_cost before Step 6 ever sizes a quantity, so
    no asset PnL or funding is ever computed for this period."""
    from backtest.engine import _step_period
    from backtest.models import AccountingError

    idx = pd.date_range("2026-01-01", periods=2, freq="1D", tz="UTC")
    with pytest.raises(AccountingError, match="NAV_after_cost"):
        _step_period(
            i=0,
            rebalance=True,
            w_target=np.array([1.0]),
            quantity_prev=np.array([0.0]),
            P_i=np.array([100.0]),
            P_ip1=np.array([100.0]),
            NAV_pre=1_000_000.0,
            fee_bps=float("inf"),
            slippage_bps=0.0,
            symbols=["A"],
            timestamp_i=idx[0],
            timestamp_ip1=idx[1],
        )
