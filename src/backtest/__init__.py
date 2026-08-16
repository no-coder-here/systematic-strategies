"""Common backtesting engine for QR-INFRA-001 (frozen contract v1.5.1).

See docs/backtest_contract.md for the normative specification. This package
contains no alpha logic: it consumes target weights and market data and
produces accounting (§1).
"""

from .models import (
    AccountingError,
    BacktestConfig,
    BacktestResult,
    ConfigError,
    DataIntegrityError,
    DatasetProvenance,
    FundingCoverage,
    FundingDataError,
    FundingEvent,
    InvalidPriceError,
    MissingPriceError,
    StrategyOutput,
    UniverseProvenance,
)
from .engine import execution_instant, run_backtest

__all__ = [
    "AccountingError",
    "BacktestConfig",
    "BacktestResult",
    "ConfigError",
    "DataIntegrityError",
    "DatasetProvenance",
    "FundingCoverage",
    "FundingDataError",
    "FundingEvent",
    "InvalidPriceError",
    "MissingPriceError",
    "StrategyOutput",
    "UniverseProvenance",
    "execution_instant",
    "run_backtest",
]
