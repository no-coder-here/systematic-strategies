"""spec §4.3.1 — vacuity rule (BD7, W9). Asserts (does not merely report)
the MEASURED counts that determine whether the `>` vs `>=` boundary and the
funding half-open boundary are vacuous on THIS data.

v1.1 correction (W3): the original docstring here predicted the funding
boundary check would be vacuous, extrapolating from HL timestamp jitter.
Measured on Window A, that prediction is WRONG: 64/4511 in-window funding
events land exactly on an hourly bar boundary, so contract §7.5's half-open
rule is genuinely exercised here, NOT vacuous. `assert count >= 0` (true for
any dataset) has been replaced with assertions against the measured values,
and dedicated M20 coverage (the `<` -> `<=` mutation this non-vacuity implies
must be caught) is added below.
"""
from __future__ import annotations

import numpy as np

from strategies.qr_smoke_001 import compute_sma


def test_close_equals_sma_bitwise_count_reported(window_a, capsys):
    """spec §4.3.1 — `>` vs `>=` in the signal differs only where
    `close[t]` is bitwise equal to `SMA100[t]`. MEASURED over Window A: 0 —
    i.e. this boundary is genuinely VACUOUS on this data (M8 correctly uses
    `>` -> `<` instead, per spec, precisely because of this)."""
    sma_full = compute_sma(window_a.raw_close)
    close_aligned = window_a.raw_close.loc[window_a.frame_index]
    sma_aligned = sma_full.loc[window_a.frame_index]
    count = int((close_aligned.to_numpy() == sma_aligned.to_numpy()).sum())
    verdict = "VACUOUS" if count == 0 else "NOT VACUOUS"
    print(f"\nspec §4.3.1: count of bars where close==SMA100 bitwise over Window A: {count} ({verdict})")
    # v1.1 (W3): assert against the MEASURED value, not `>= 0` (inert — true
    # for every possible dataset). A change here is a finding to re-examine,
    # not something to silently re-loosen.
    assert count == 0, (
        f"expected the >/>= boundary to be VACUOUS (count==0) on Window A; measured {count} — "
        "the fixture's discriminating character changed and M8's substitution should be re-examined"
    )


def test_funding_boundary_coincident_count_reported(window_a, capsys):
    """spec §4.3.1 v1.1 CORRECTION — contract §7.5's half-open funding
    boundary (`T_i <= e.timestamp < T_{i+1}`) differs under `<` -> `<=`
    only if an event lands EXACTLY on an hourly bar boundary. v1.0 predicted
    this would be vacuous (extrapolating from HL timestamp jitter); MEASURED
    on Window A this is WRONG: 64/4511 in-window events land exactly on the
    hour, so the rule is genuinely exercised (NOT VACUOUS) and mutation M20
    (`<` -> `<=` at engine.py's funding aggregation upper boundary) MUST be
    caught — see `test_funding_boundary_full_path_matches_reconstruction_M20`
    below, which is the dedicated §4.3.1 funding-boundary test M20 targets.
    """
    from experiments.qr_smoke_001.pipeline import load_full_hl_funding

    events, _coverage = load_full_hl_funding("data")
    frame_start, frame_end = window_a.frame_index[0], window_a.frame_index[-1]
    in_window = [e for e in events if frame_start <= e.timestamp <= frame_end]
    on_boundary = [
        e for e in in_window
        if e.timestamp == e.timestamp.floor("h")
    ]
    verdict = "VACUOUS" if len(on_boundary) == 0 else "NOT VACUOUS"
    print(
        f"\nspec §4.3.1: count of funding events landing exactly on an hourly bar "
        f"boundary within Window A: {len(on_boundary)} / {len(in_window)} total ({verdict})"
    )
    # v1.1 (W3): assert against MEASURED values, not `>= 0`.
    assert len(in_window) == 4511
    assert len(on_boundary) == 64, (
        f"expected 64 boundary-coincident funding events (measured, v1.1 correction); got {len(on_boundary)} — "
        "if the underlying funding data changed, the §7.5 boundary's vacuity status should be re-examined"
    )


def test_funding_boundary_full_path_matches_reconstruction_M20(manual_periods, window_a):
    """spec §4.3.1 / mutation table M20 — 'funding aggregation `<` -> `<=`
    at the §7.5 upper boundary' MUST break this test. The independent
    reconstruction (`reconstruction.py`) implements the correct half-open
    upper boundary with a strict `<` in its funding-event loop. Because 64
    events in Window A land exactly on an hourly boundary (measured above,
    NOT vacuous), a `<` -> `<=` mutation at `engine.py`'s funding aggregation
    (around `if e.timestamp >= T_ip1: break`) reassigns those events to the
    WRONG period, which propagates through NAV and diverges
    `funding_pnl_cash` / `net_return` from this independent reconstruction
    over the full path (measured by the auditor: moves equity on 3973/4511
    periods). This is the dedicated funding-boundary discrimination test the
    mutation table designates for M20 (kept independent of the general
    full-path assertion in test_manual_verification.py so removing/loosening
    one cannot silently drop M20 coverage).
    """
    r = window_a.result
    n = len(manual_periods)
    funding_pnl_recon = np.array([p.funding_pnl_cash for p in manual_periods], dtype=float)
    np.testing.assert_allclose(
        r.funding_pnl_cash.to_numpy()[:n], funding_pnl_recon, rtol=1e-12, atol=1e-9,
        err_msg="funding_pnl_cash diverges from the independent reconstruction over the full Window A path (M20)",
    )
    net_return_recon = np.array([p.net_return for p in manual_periods], dtype=float)
    np.testing.assert_allclose(
        r.net_return.to_numpy()[:n], net_return_recon, rtol=1e-12, atol=1e-15,
        err_msg="net_return diverges from the independent reconstruction over the full Window A path (M20)",
    )
