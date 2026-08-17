"""QR-SMOKE-001 v1.0 FROZEN — end-to-end pipeline smoke-test strategy.

Deliberately trivial. See docs/qr_smoke_001_spec.md. NOT alpha research.
"""

from .strategy import (
    SMA_WINDOW,
    build_strategy_output_for_frame,
    compute_sma,
    compute_signal,
    compute_target_weights,
)

__all__ = [
    "SMA_WINDOW",
    "compute_sma",
    "compute_signal",
    "compute_target_weights",
    "build_strategy_output_for_frame",
]
