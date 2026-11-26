"""Shared DaemonState container bound to FastAPI `app.state`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from foundry.blackboard.events import EventDispatcher, EventRelay
from foundry.blackboard.repository import BlackboardRepository
from foundry.config.loader import CONFIG_DIR_DEFAULT
from foundry.decisions.service import DecisionService


@dataclass
class DaemonState:
    """In-process dependencies for Foundry HTTP API, WebSockets, and Dashboard v0.5."""

    repo: BlackboardRepository
    dispatcher: EventDispatcher
    relay: EventRelay
    decisions: DecisionService
    config_dir: Path = CONFIG_DIR_DEFAULT
    project_dir: Path = field(default_factory=Path.cwd)
    dashboard_dir: Path | None = None
    listen_dsn: str | None = None
    preview_urls: dict[str, dict[str, str]] = field(default_factory=dict)
    default_local_preview_url: str = "http://127.0.0.1:5173"
    start_relay_in_lifespan: bool = False
