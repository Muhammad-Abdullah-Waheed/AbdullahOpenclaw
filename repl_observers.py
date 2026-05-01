"""Wire default ``EventBus`` subscribers for the interactive REPL (Stage 8)."""

from __future__ import annotations

import logging

from compaction import maybe_compact
from events import (
    AssistantReplyFinished,
    ReplStarted,
    SessionEnding,
    SlashCommandHandled,
    UserTurnReceived,
)
from event_bus import EventBus
from session_store import persistence_enabled, save_transcript
from slash_commands import ReplState

logger = logging.getLogger(__name__)


def wire_default_observers(bus: EventBus, state: ReplState) -> None:
    """Register logging + persistence side effects."""

    def on_started(ev: ReplStarted) -> None:
        logger.info(
            "event: ReplStarted session=%s mode=%s always=%s profiles=%s",
            ev.session_id,
            ev.skill_attach_mode,
            ev.always_skill_ids,
            ev.profile_tags,
        )

    def on_user_turn(ev: UserTurnReceived) -> None:
        logger.info(
            "event: UserTurnReceived session=%s auto_skills=%s text_preview=%s",
            ev.session_id,
            ev.auto_skill_ids,
            ev.text[:120] + ("..." if len(ev.text) > 120 else ""),
        )

    def on_slash(ev: SlashCommandHandled) -> None:
        logger.info(
            "event: SlashCommandHandled session=%s /%s %s outcome=%s",
            ev.session_id,
            ev.name,
            ev.args,
            ev.outcome,
        )

    def on_assistant(ev: AssistantReplyFinished) -> None:
        if persistence_enabled():
            save_transcript(state.spath, state.session.messages)
        if maybe_compact(state.session.messages):
            logger.info(
                "compaction: transcript shortened session=%s messages=%s",
                ev.session_id,
                len(state.session.messages),
            )
            if persistence_enabled():
                save_transcript(state.spath, state.session.messages)

    def on_session_ending(ev: SessionEnding) -> None:
        logger.info("event: SessionEnding session=%s reason=%s", ev.session_id, ev.reason)

    bus.subscribe(ReplStarted, on_started)
    bus.subscribe(UserTurnReceived, on_user_turn)
    bus.subscribe(SlashCommandHandled, on_slash)
    bus.subscribe(AssistantReplyFinished, on_assistant)
    bus.subscribe(SessionEnding, on_session_ending)
