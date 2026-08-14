---
description: Standard procedure for validating systematic trading backtests. Use when evaluating or auditing a strategy backtest.
---

# Backtest Validation Procedure

Always inspect:

## Signal availability

For every signal determine:

signal_timestamp
information_available_timestamp
execution_timestamp

Require:

information_available_timestamp <= execution_timestamp

## Returns

Verify position lag explicitly.

For close-to-close strategy:

position[t] must be constructed using only information available before
the price used for execution.

## Transaction costs

Calculate:

gross_pnl
fees
spread
slippage
funding
net_pnl

Report both gross and net.

## Metrics

Always report:

annualized return
annualized volatility
Sharpe
Sortino
maximum drawdown
Calmar
turnover
gross exposure
net exposure
number of trades

## Robustness

Never report only the best parameter.

Report the neighborhood around the selected parameter.
