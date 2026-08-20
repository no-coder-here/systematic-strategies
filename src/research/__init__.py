"""QR-PREP-001 P§4 — the minimal research runner package.

See `research.runner` for the sanctioned entry point,
`run_research_experiment`, and its honest-limit docstring (P§4.7/M§4.5:
D17 is mitigated, not closed, by this module).
"""

from .runner import ResearchRunnerError, run_research_experiment

__all__ = ["ResearchRunnerError", "run_research_experiment"]
