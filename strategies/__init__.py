"""Strategy packages. Each subpackage returns ONLY `StrategyOutput` via the
platform interface (`backtest.models.StrategyOutput`) and MUST NOT compute
PnL, NAV, fees, slippage or funding (CLAUDE.md; backtest_contract.md §1).
"""
