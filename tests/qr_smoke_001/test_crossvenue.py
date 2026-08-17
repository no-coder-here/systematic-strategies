"""spec §4.4 — Binance proxy vs Hyperliquid native (Window C = Window A).
Any failure of the alignment assertion is an automatic SMOKE FAIL (spec §6).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.qr_smoke_001.crossvenue import (
    assert_alignment,
    assert_identical_bar_index,
    compare_signals,
    run_window_c,
)
from strategies.qr_smoke_001 import compute_sma, compute_signal


@pytest.fixture(scope="module")
def window_c():
    return run_window_c(compute_counterfactual=False)


def test_identical_bar_index_rule_3(window_c):
    hl_run, binance_run = window_c
    assert_identical_bar_index(hl_run, binance_run)  # raises AssertionError on failure


def test_alignment_is_falsifiable_and_passes(window_c):
    """spec §4.4 — EXACT `argmax_l rho(l) == 0`, and `rho(0) >= 0.99`. A
    failure here is an automatic SMOKE FAIL, never a divergence to discuss."""
    hl_run, binance_run = window_c
    result = assert_alignment(
        hl_run.raw_close.loc[hl_run.frame_index], binance_run.raw_close.loc[binance_run.frame_index]
    )
    assert result.argmax_lag == 0
    assert result.rho_0 >= 0.99


def test_alignment_would_fail_on_shuffled_data():
    """Proves `assert_alignment` can actually discriminate: a deliberately
    shuffled series must fail the rho(0) >= 0.99 assertion."""
    import numpy as np
    import pandas as pd

    idx = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
    rng = np.random.default_rng(0)
    close_a = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 200)), index=idx)
    shuffled = close_a.sample(frac=1.0, random_state=1)
    shuffled.index = idx  # same index, scrambled VALUES -> should NOT align
    with pytest.raises(AssertionError):
        assert_alignment(close_a, shuffled)


def test_signal_comparison_reports_agreement_rate_and_transitions(window_c):
    """D3 fix (v1.1 repair cycle 2): the previous version of this test
    asserted only RANGES (`0.0 <= rate <= 1.0`, `entries >= 0`, `exits >= 0`),
    every one of which is trivially true for any dataset and any signal
    direction (spec §4.3.1's vacuity rule: a count check must be able to
    fail). It is now pinned against the MEASURED values on real Window A/C
    data, independently re-derived (not copied from a prior report):

        n_bars=4512, n_differing=9, agreement_rate=(4512-9)/4512
        hl_transitions:      entries=106, exits=106
        binance_transitions: entries=107, exits=107

    Per spec §4.1.1: `n_differing`, and the entry/exit counts are integer
    EVENT COUNTS -> EXACT (`==`). `agreement_rate` is a RATIO of those exact
    counts (`(n_bars - n_differing) / n_bars`), computed via
    `numpy.ndarray.mean()` over a fixed-length boolean array -- deterministic
    but reached by a different arithmetic path (a running sum/divide) than a
    plain fraction, so it is classified TOLERANCE here (analogous to the
    §4.1.1 'metrics' row, `rtol=1e-10, atol=1e-12`) rather than bitwise `==`.
    """
    hl_run, binance_run = window_c
    cmp = compare_signals(hl_run, binance_run)
    n_bars = len(hl_run.frame_index)

    # EXACT integer counts (spec §4.1.1: "trade count, event counts ... EXACT").
    assert cmp["n_differing"] == 9
    assert cmp["n_differing"] == len(cmp["differing_timestamps"])
    assert cmp["hl_transitions"] == {"entries": 106, "exits": 106}
    assert cmp["binance_transitions"] == {"entries": 107, "exits": 107}

    # TOLERANCE ratio-of-exact-counts (spec §4.1.1 "metrics" row).
    expected_agreement_rate = (n_bars - cmp["n_differing"]) / n_bars
    assert cmp["agreement_rate"] == pytest.approx(expected_agreement_rate, rel=1e-10, abs=1e-12)

    # Real BTC data over Window A/C is known (measured) to disagree on a
    # small number of bars between the two venues — reported, not hidden.
    print(f"\nspec §4.4: signal agreement_rate={cmp['agreement_rate']:.6f} "
          f"n_differing={cmp['n_differing']} "
          f"hl_transitions={cmp['hl_transitions']} binance_transitions={cmp['binance_transitions']}")


def test_signal_transition_direction_matches_measured_M8_discriminator(window_c):
    """Strengthens D3 beyond count pinning (v1.1 repair cycle 2 finding).

    `compare_signals` calls the SAME `compute_signal` for BOTH venues, so
    `agreement_rate`/`n_differing` are mathematically INVARIANT under a
    simultaneous full negation of both signals (`agree(NOT a, NOT b) ==
    agree(a, b)` is a boolean identity, true for ANY data), and on real
    Window A/C data the entries/exits counts (106/106, 107/107) happen ALSO
    to be symmetric (the frame both starts and ends flat), so a `>` -> `<`
    negation merely SWAPS the entries<->exits labels without changing either
    count. Verified empirically: mutating `strategy.py`'s `>` to `<` leaves
    `test_signal_comparison_reports_agreement_rate_and_transitions` GREEN.
    Per spec §4.3.1 this is reported, not hidden.

    What a full negation CANNOT preserve is WHICH TIMESTAMP is the first
    entry vs the first exit -- entries and exits strictly alternate starting
    from a flat state, so the first entry always precedes the first exit;
    negating the signal makes the (formerly second) first EXIT timestamp
    become the new first ENTRY timestamp instead, a different, measured, bar.
    This test pins those first-transition timestamps directly (independent
    of `compare_signals`, computed the same way it is internally, since the
    object under test is `compute_signal` itself, not adapter independence)
    so that M8 is caught by design.
    """
    hl_run, binance_run = window_c
    hl_sig = compute_signal(hl_run.raw_close, compute_sma(hl_run.raw_close)).loc[hl_run.frame_index]
    bn_sig = compute_signal(binance_run.raw_close, compute_sma(binance_run.raw_close)).loc[binance_run.frame_index]

    def first_entry_exit(sig: pd.Series):
        arr = sig.to_numpy()
        d = np.diff(arr.astype(int))
        entries = sig.index[1:][d == 1]
        exits = sig.index[1:][d == -1]
        return entries[0], exits[0]

    hl_first_entry, hl_first_exit = first_entry_exit(hl_sig)
    bn_first_entry, bn_first_exit = first_entry_exit(bn_sig)

    # EXACT (spec §4.1.1: timestamps, discrete boolean state).
    assert hl_first_entry == pd.Timestamp("2026-01-27 02:00:00", tz="UTC")
    assert hl_first_exit == pd.Timestamp("2026-01-27 04:00:00", tz="UTC")
    assert bn_first_entry == pd.Timestamp("2026-01-27 02:00:00", tz="UTC")
    assert bn_first_exit == pd.Timestamp("2026-01-27 03:00:00", tz="UTC")


def test_confound_is_stated_both_runs_use_own_venue_data(window_c):
    """spec §4.4 rule 1 — each run derives its OWN signal from its OWN
    venue's closes and executes at its OWN venue's opens; this confounds
    signal and execution effects (stated, not resolved, by design)."""
    hl_run, binance_run = window_c
    assert hl_run.dataset_provenance[0].source_venue == "Hyperliquid"
    assert binance_run.dataset_provenance[0].source_venue == "Binance"
    assert binance_run.dataset_provenance[0].native_or_proxy == "proxy"


def test_both_runs_charge_same_hl_native_funding(window_c):
    """spec §4.4 rule 2 — both runs charge the SAME Hyperliquid-native
    funding events (labelled, since Binance-priced notional applies to
    native HL funding)."""
    hl_run, binance_run = window_c
    hl_funding_prov = [p for p in hl_run.dataset_provenance if p.field_type == "funding_rate"][0]
    bn_funding_prov = [p for p in binance_run.dataset_provenance if p.field_type == "funding_rate"][0]
    assert hl_funding_prov.source_venue == "Hyperliquid"
    assert bn_funding_prov.source_venue == "Hyperliquid"  # same native HL funding, both runs
