---
name: user-role
description: User is the principal/owner of a systematic crypto perp research platform (Hyperliquid); deep quant background, reviews specs line-by-line before approving implementation
metadata:
  type: user
---

The user owns this systematic crypto perpetual-futures research platform and acts as the
final approver on research infrastructure. I operate as Head of Quantitative Research
under them.

Demonstrated expertise — assume a sophisticated quant counterparty:

- Caught a NAV-normalization inconsistency in a draft spec (drift denominator using gross
  return while NAV compounded on net return) from reading prose alone, before any code.
- Knew Hyperliquid funds hourly and immediately spotted that a "one funding event per bar"
  design breaks for 1d/4h bars.
- Pushed back on implicit rebalancing semantics — recognized that target weights without
  an explicit rebalance signal cause unintended drift-correcting turnover every bar.
- Thinks in terms of unit discipline (USD cash vs NAV-relative returns) and venue realism
  (liquidation modelling scope).

How to work with them:

- Do not over-explain quant fundamentals. Lead with the decision and the tradeoff.
- They want disagreement when warranted — see [[feedback-honest-technical-disagreement]].
- They review specs rigorously *before* implementation, so invest heavily in the frozen
  spec rather than iterating on code. See [[feedback-spec-before-implementation]].
