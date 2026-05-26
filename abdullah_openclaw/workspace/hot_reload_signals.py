"""Shared flags for config / skill hot reload (Stage 9).

Debounced watchdog callbacks and ``/reload`` both call ``request_skill_env_reload``.
The main thread clears the flag and applies updates **before** the next blocking ``input()``,
so we never mutate prompts mid-completion.
"""

from __future__ import annotations

import threading

reload_before_next_turn = threading.Event()

# Single-element container so watchers can mutate without ``global``.
_last_reload_reason: list[str] = ["debounced_watch"]


def request_skill_env_reload(*, reason: str = "debounced_watch") -> None:
    """Signal the REPL main thread to re-read ``.env`` and reload skills."""
    _last_reload_reason[0] = reason
    reload_before_next_turn.set()


def take_reload_reason() -> str:
    """Consume reason for telemetry (caller should clear reload event separately)."""
    return _last_reload_reason[0]
