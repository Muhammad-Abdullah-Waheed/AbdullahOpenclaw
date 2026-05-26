"""Glue routing + transcripts for CLI / channels (Stage 12)."""

from __future__ import annotations

from abdullah_openclaw.routing.table import invalidate_routing_bindings_cache, resolve_agent_for_source
from abdullah_openclaw.workspace.session_store import sanitize_session_id

CLI_SOURCE_STRING = "platform-cli"


def effective_cli_agent_id(cli_override: str | None) -> str:
    """If ``cli_override`` is set it wins over ``routing.yaml`` rules for CLI source only."""
    if cli_override is not None and cli_override.strip():
        return sanitize_session_id(cli_override.strip())
    return resolve_agent_for_source(CLI_SOURCE_STRING)


def cli_transcript_stem(*, cli_agent_override: str | None, session_label: str) -> str:
    """Deterministic filesystem id: ``{agent}.cli.{label}`` (sanitized as one slug)."""
    agent_id = effective_cli_agent_id(cli_agent_override)
    label = sanitize_session_id(session_label.strip() or "default")
    stem = sanitize_session_id(f"{agent_id}.cli.{label}")
    return stem


def telegram_transcript_stem(agent_id: str, chat_id: str) -> str:
    chat_id_clean = sanitize_session_id(chat_id.strip())
    return sanitize_session_id(f"{agent_id}.telegram.{chat_id_clean}")


def websocket_transcript_stem(agent_id: str, ws_key: str) -> str:
    """``ws_key`` is already sanitized from ``websocket_session_id``."""
    ws_key_clean = sanitize_session_id(ws_key.strip())
    return sanitize_session_id(f"{agent_id}.websocket.{ws_key_clean}")


def clear_routing_hot_cache() -> None:
    invalidate_routing_bindings_cache()
