"""Versioned role prompt helpers."""

from foundry.agents.prompts.module2 import (
    LEAD_SYSTEM_PROMPTS,
    ORCHESTRATOR_SYSTEM_PROMPT,
    build_architecture_messages,
    build_consistency_review_messages,
    build_lead_contract_messages,
    build_requirements_messages,
    parse_json_object,
)

__all__ = [
    "LEAD_SYSTEM_PROMPTS",
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "build_architecture_messages",
    "build_consistency_review_messages",
    "build_lead_contract_messages",
    "build_requirements_messages",
    "parse_json_object",
]
