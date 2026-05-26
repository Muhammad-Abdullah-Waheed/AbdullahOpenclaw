"""Telegram types + helpers (aligned with ``09-channels/src/mybot/channel/telegram_channel.py``).

The runnable ``Application.run_polling()`` wiring lives in ``telegram_app.py`` so shutdown
follows python-telegram-bot’s supported path on all platforms.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# Telegram Bot API UTF-16 limit for a single outbound text message (~4096 code units).
_TEXT_CHUNK = int((os.environ.get("TELEGRAM_CHUNK_CHARS") or "4096").strip() or "4096")
_TEXT_CHUNK = max(256, min(_TEXT_CHUNK, 4096))


@dataclass(frozen=True)
class TelegramEventSource:
    """Serialized as ``platform-telegram:<user_id>:<chat_id>`` (OpenClaw EventSource naming)."""

    user_id: str
    chat_id: str

    def __str__(self) -> str:
        return f"platform-telegram:{self.user_id}:{self.chat_id}"

    @classmethod
    def from_string(cls, s: str) -> TelegramEventSource:
        _, uid, cid = s.split(":", 2)
        return cls(user_id=uid, chat_id=cid)

    @property
    def platform_name(self) -> str:
        return "telegram"


def telegram_session_id(chat_id: str) -> str:
    from abdullah_openclaw.workspace.session_store import sanitize_session_id

    return sanitize_session_id(f"telegram-{chat_id}")


def telegram_user_allowed(source: TelegramEventSource, allowed_user_ids: frozenset[str] | None) -> bool:
    if not allowed_user_ids:
        return True
    return source.user_id in allowed_user_ids


def split_telegram_text(text: str, max_chars: int = _TEXT_CHUNK) -> list[str]:
    if len(text) <= max_chars:
        return [text] if text else [""]
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + max_chars])
        i += max_chars
    return chunks


def telegram_allowed_user_ids_from_env() -> frozenset[str] | None:
    raw = (os.environ.get("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    if not raw:
        return None
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


async def send_text_chunks(bot, *, chat_id: int, content: str) -> None:
    """Chunk long assistant replies similar to DeliveryWorker slicing in Step 09."""
    for chunk in split_telegram_text(content):
        await bot.send_message(chat_id=chat_id, text=chunk)
