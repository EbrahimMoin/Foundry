"""Human-in-the-loop binary decisions, presets, and ADR recording."""

from foundry.decisions.service import (
    PRESET_MATRIX,
    DecisionCategory,
    DecisionResolution,
    DecisionService,
    Reversibility,
    is_auto_resolved,
    resolve_decision_mode,
)

__all__ = [
    "PRESET_MATRIX",
    "DecisionCategory",
    "DecisionResolution",
    "DecisionService",
    "Reversibility",
    "is_auto_resolved",
    "resolve_decision_mode",
]
