"""Rate-limit cooldown tracking and fallback resolution."""

from __future__ import annotations

import time
from collections.abc import Callable

from foundry.config.schemas import ModelsConfig


class RateLimitRouter:
    """Tracks per-provider 429 cooldowns and resolves healthy fallback profiles."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._cooldown_until: dict[str, float] = {}

    def mark_cooldown(self, provider_id: str, cooldown_seconds: int) -> None:
        self._cooldown_until[provider_id] = self._clock() + max(1, cooldown_seconds)

    def is_cooling_down(self, provider_id: str) -> bool:
        until = self._cooldown_until.get(provider_id)
        if until is None:
            return False
        if self._clock() >= until:
            del self._cooldown_until[provider_id]
            return False
        return True

    def clear_cooldown(self, provider_id: str) -> None:
        self._cooldown_until.pop(provider_id, None)

    def candidate_profiles(
        self,
        primary_profile: str,
        models_cfg: ModelsConfig,
    ) -> list[str]:
        """Return ordered candidate profile names respecting cooldowns and benchmark pinning."""
        if models_cfg.policies.benchmark_pin_models:
            return [primary_profile]

        ordered: list[str] = [primary_profile]
        seen = {primary_profile}
        queue = list(models_cfg.profiles[primary_profile].fallbacks)
        while queue:
            name = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
            queue.extend(models_cfg.profiles[name].fallbacks)

        healthy = [
            name for name in ordered if not self.is_cooling_down(models_cfg.profiles[name].provider)
        ]
        return healthy if healthy else ordered
