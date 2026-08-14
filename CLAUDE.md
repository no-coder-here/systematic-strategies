# # Market and Venue Scope

## Primary Venue

The primary execution venue for this research platform is Hyperliquid.

The primary research universe is Hyperliquid perpetual futures.

Strategies should ultimately be executable on Hyperliquid unless explicitly

marked as exploratory research for another venue.

## Data Source Policy

Use Hyperliquid data as the preferred source whenever available.

External exchange data such as Binance, Bybit, OKX, Coinbase, or other

sources may be used when Hyperliquid historical data is unavailable,

provided that using the external data is economically and temporally valid.

External data must never silently substitute for Hyperliquid-specific data.

Examples:

Allowed:

- Binance or Bybit OHLCV as a historical signal input when equivalent

  Hyperliquid price history is unavailable.

- External market-wide volume, open interest, price, basis, or other

  information when the strategy intentionally uses it as an observable signal.

- BTC or ETH historical data before sufficient Hyperliquid history exists,

  for preliminary hypothesis research.

Not allowed without explicit justification:

- Binance funding used as if it were Hyperliquid funding cost.

- Binance spreads used as Hyperliquid execution spreads.

- Binance liquidity used as Hyperliquid execution capacity.

- Current Hyperliquid listings retrospectively assumed to have existed

  throughout historical periods.

- External execution prices silently presented as Hyperliquid execution prices.

Every dataset must record provenance.

At minimum record:

- source venue

- field/source type

- time range

- retrieval date

- symbol mapping

- whether the data is native or proxy data

- what the proxy is intended to represent

If proxy data is used, research results must clearly state this.

Final strategy validation should use Hyperliquid-native execution,

funding and market data wherever those data are available.

## Research Scope

Initial strategy research should focus on:

- perpetual futures

- systematic strategies

- hourly to daily horizons

- multi-asset strategies

- market-neutral strategies where economically appropriate

- cross-sectional signals

- time-series signals

- relative-value strategies

The infrastructure should not be limited to market-neutral strategies,

but should support them as a first-class use case.

## Backtesting Principle

Strategies generate TARGET PORTFOLIO WEIGHTS.

Strategies do NOT calculate their own PnL.

The common backtesting engine is solely responsible for:

- execution timing

- position lagging

- turnover

- transaction fees

- slippage

- funding

- gross PnL

- net PnL

- equity curve

- leverage

- gross exposure

- net exposure

- performance statistics

This rule exists so every strategy is evaluated using the same accounting logic.

## Execution Safety

This repository is a research environment.

Do not place live orders.

Do not request private keys.

Do not implement withdrawals or transfers.

Do not connect research agents to live trading permissions unless explicitly

added as a separate future project.