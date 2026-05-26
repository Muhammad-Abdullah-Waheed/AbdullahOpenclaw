"""Telegram frontend for the agent (Stage 10 channels + Stage 12 routing).

Each routed agent + chat resolves to transcript stem ``{agent}.telegram.{chat_id}``.
Source string ``platform-telegram:<uid>:<chat>`` matches rules in ``routing.yaml``.

Run (after copying ``.env.example`` → ``.env``)::

    TELEGRAM_ENABLED=1 TELEGRAM_BOT_TOKEN=… uv run python telegram_app.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from abdullah_openclaw.core.agent import Session
from abdullah_openclaw.agents.runtime import get_skill_runtime_for_agent
from abdullah_openclaw.channels.telegram_channel import (
    TelegramEventSource,
    send_text_chunks,
    telegram_allowed_user_ids_from_env,
    telegram_user_allowed,
)
from abdullah_openclaw.core.event_bus import EventBus
from abdullah_openclaw.core.events import AssistantReplyFinished, UserTurnReceived
from abdullah_openclaw.repl.observers import wire_default_observers
from abdullah_openclaw.routing.session_stems import telegram_transcript_stem
from abdullah_openclaw.routing.table import resolve_agent_for_source
from abdullah_openclaw.workspace.session_store import load_transcript, persistence_enabled, session_path
from abdullah_openclaw.workspace.skill_runtime import PROJECT_ROOT
from abdullah_openclaw.core.skill_selection import skills_for_turn
from abdullah_openclaw.workspace.skills import build_system_prompt
from abdullah_openclaw.repl.slash import ReplState

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)


def telegram_enabled_from_env() -> bool:
    raw = (os.environ.get("TELEGRAM_ENABLED") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def bot_token_or_none() -> str | None:
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() or None


def _load_cached_session(
    sid_full: str,
    cache: dict[str, Session],
    *,
    rt,
    spath: Path,
) -> Session:
    sid_key = sid_full
    if sid_key in cache:
        return cache[sid_key]
    loaded = load_transcript(spath) if persistence_enabled() else None
    if loaded:
        sess = Session.from_transcript(loaded)
        sess.set_system_prompt(rt.initial)
        logger.info("telegram session: resumed id=%s messages=%s", sid_key, len(sess.messages))
    else:
        sess = Session(system_prompt=rt.initial)
        logger.info("telegram session: new id=%s", sid_key)
    cache[sid_key] = sess
    return cache[sid_key]


_SID_LOCK_FACTORY: dict[str, asyncio.Lock] = {}
_LOCK_INIT = asyncio.Lock()


async def _lock_for_sid(session_stem: str) -> asyncio.Lock:
    async with _LOCK_INIT:
        if session_stem not in _SID_LOCK_FACTORY:
            _SID_LOCK_FACTORY[session_stem] = asyncio.Lock()
        return _SID_LOCK_FACTORY[session_stem]


def main() -> None:
    dotenv_ok = PROJECT_ROOT / ".env"
    if dotenv_ok.is_file():
        from dotenv import load_dotenv

        load_dotenv(dotenv_ok)

    token = bot_token_or_none()
    if not telegram_enabled_from_env():
        logger.error("telegram: TELEGRAM_ENABLED is off; refusing to start")
        raise SystemExit(2)
    if not token:
        logger.error(
            "telegram: set TELEGRAM_BOT_TOKEN in .env "
            "(see https://telegram.me/BotFather — same as Step 09)"
        )
        raise SystemExit(2)

    allowed = telegram_allowed_user_ids_from_env()

    chat_session_cache: dict[str, Session] = {}

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg or not msg.text or not update.effective_chat or not msg.from_user:
            return
        user_id = str(msg.from_user.id)
        chat_id = str(update.effective_chat.id)
        source = TelegramEventSource(user_id=user_id, chat_id=chat_id)
        if not telegram_user_allowed(source, allowed):
            logger.info("telegram: ignoring non-whitelisted user_id=%s", user_id)
            return

        text = msg.text

        agent_id = resolve_agent_for_source(str(source))
        rt = get_skill_runtime_for_agent(agent_id)
        sid = telegram_transcript_stem(agent_id, chat_id)
        spath = session_path(sid)

        lock = await _lock_for_sid(sid)
        async with lock:
            session = _load_cached_session(sid, chat_session_cache, rt=rt, spath=spath)
            state = ReplState(session=session, sid=sid, spath=spath, initial=rt.initial)
            bus = EventBus()
            wire_default_observers(bus, state)

            deduped, auto_skill_ids = skills_for_turn(rt, text)
            bus.publish(UserTurnReceived(sid, text, auto_skill_ids))
            session.set_system_prompt(build_system_prompt(rt.foundation_prompt, deduped))

            reply = await asyncio.to_thread(session.chat, text)

            logger.info(
                "telegram: agent=%s reply chat=%s len=%s sid=%s",
                agent_id,
                chat_id,
                len(reply),
                sid,
            )
            await send_text_chunks(context.bot, chat_id=int(chat_id), content=reply)
            bus.publish(AssistantReplyFinished(sid, reply, len(session.messages)))

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info(
        "telegram: starting polling — transcripts use {agent}.telegram.{chat}; "
        "match ``platform-telegram:…`` in routing.yaml",
    )

    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("telegram: shutting down")


if __name__ == "__main__":
    main()
