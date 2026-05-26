"""Typed events for the REPL runtime (Stage 8).

OpenClaw’s reference stack (steps such as ``build-your-own-openclaw/10-websocket``, ``09-channels``,
and ``08-config-hot-reload``) uses an
:class:`EventBus` so *channels*, *cron*, and the *CLI* can all feed the agent through the
same pipeline. Events are immutable facts (“something happened”); subscribers react.

This repo uses **synchronous** dispatch first so the CLI stays one thread with no asyncio
requirements. You can swap the bus backend later for ``asyncio.Queue`` + Workers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplStarted:
    """Fired once when the CLI loop boots with skill/session configuration."""

    session_id: str
    skill_attach_mode: str
    always_skill_ids: tuple[str, ...]
    profile_tags: tuple[str, ...]
    agent_id: str = "pickle"


@dataclass(frozen=True)
class UserTurnReceived:
    """A non-slash line of user input is about to be processed."""

    session_id: str
    text: str
    auto_skill_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssistantReplyFinished:
    """The model returned final text for this turn (after any tool hops)."""

    session_id: str
    reply: str
    message_count: int


@dataclass(frozen=True)
class SlashCommandHandled:
    """A host-side slash command was executed (control plane)."""

    session_id: str
    name: str
    args: tuple[str, ...] = ()
    outcome: str = "continue"


@dataclass(frozen=True)
class ConfigReloaded:
    """``.env`` and/or workspace skills changed; prompts were rebuilt before next input."""

    session_id: str
    reason: str


@dataclass(frozen=True)
class SessionEnding:
    """User quit or /quit; loop is exiting."""

    session_id: str
    reason: str


def payload_preview(text: str, limit: int = 200) -> str:
    t = text.replace("\n", "\\n").strip()
    return t if len(t) <= limit else f"{t[:limit]}..."
