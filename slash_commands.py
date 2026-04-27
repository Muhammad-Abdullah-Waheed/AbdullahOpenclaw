"""Slash commands — local control plane (Stage 5).

What this is
------------
In a chat REPL, most lines are *user content* sent to the model. **Slash commands** are
lines the **host program** handles first. They start with ``/`` (e.g. ``/help``, ``/new``).

Why separate from the LLM?
---------------------------
1. **Reliability** — The model should not be the only way to "start over" or "switch
   workspace"; those are host responsibilities with precise semantics.
2. **Cost & latency** — Meta-actions should not burn tokens or API calls.
3. **Safety** — Destructive or structural actions stay in your code, not in prompt soup.

This is the same idea as slash commands in Cursor, Discord bots, IRC, etc.: a **prefix
convention** plus a **local dispatch table** (like your tool registry, but for the CLI).

How it works here
-----------------
``main.py`` reads a line. If it looks like a command, we parse ``/name arg1 arg2``,
dispatch to a handler, and **do not** call ``session.chat``. Otherwise the line is normal
user input and goes to the model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent import Session
from compaction import maybe_compact
from session_store import (
    list_session_paths,
    load_transcript,
    sanitize_session_id,
    save_transcript,
    session_path,
)

logger = logging.getLogger(__name__)

Outcome = Literal["continue", "break"]


def parse_slash_line(line: str) -> tuple[str, list[str]] | None:
    """If ``line`` is a slash command, return ``(name_lower, args)``; else ``None``."""
    s = line.strip()
    if not s.startswith("/"):
        return None
    inner = s[1:].strip()
    if not inner:
        return "help", []
    parts = inner.split()
    return parts[0].lower(), parts[1:]


@dataclass
class ReplState:
    """Mutable REPL state slash handlers can update."""

    session: Session
    sid: str
    spath: Path
    initial: str
    """System prompt with base + *always* skills (same as startup `initial` in main)."""


def handle_slash(
    name: str,
    args: list[str],
    *,
    state: ReplState,
    persist: bool,
) -> Outcome:
    if name in {"help", "h", "?"}:
        _print_help()
        return "continue"

    if name in {"quit", "exit", "q"}:
        return "break"

    if name == "new":
        state.session = Session(system_prompt=state.initial)
        if persist:
            save_transcript(state.spath, state.session.messages)
        print(f"[New chat in session {state.sid!r} — transcript reset.]\n")
        return "continue"

    if name == "session":
        if not args:
            print("Usage: /session <id>  — switch saved transcript (loads if exists).\n")
            return "continue"
        raw = " ".join(args)
        new_sid = sanitize_session_id(raw)
        new_path = session_path(new_sid)
        state.sid = new_sid
        state.spath = new_path
        loaded = load_transcript(new_path) if persist else None
        if loaded:
            state.session = Session.from_transcript(loaded)
            state.session.set_system_prompt(state.initial)
            print(f"[Switched to session {new_sid!r}, loaded {len(state.session.messages)} messages.]\n")
            logger.info("slash: session switch load id=%s messages=%s", new_sid, len(state.session.messages))
        else:
            state.session = Session(system_prompt=state.initial)
            print(f"[Switched to session {new_sid!r}, new empty chat.]\n")
            logger.info("slash: session switch fresh id=%s", new_sid)
        return "continue"

    if name in {"sessions", "ls"}:
        paths = list_session_paths()
        if not paths:
            print("(no saved sessions yet)\n")
            return "continue"
        print("Saved sessions:")
        for p in paths:
            mark = " <-- current" if p.resolve() == state.spath.resolve() else ""
            print(f"  - {p.stem}{mark}")
        print()
        return "continue"

    if name == "save":
        if not persist:
            print("[Persistence is off (SESSION_PERSIST=0); nothing to save.]\n")
            return "continue"
        save_transcript(state.spath, state.session.messages)
        print(f"[Saved {len(state.session.messages)} messages to {state.spath.name}.]\n")
        return "continue"

    if name == "reload":
        if not persist:
            print("[Persistence is off; /reload has no effect.]\n")
            return "continue"
        loaded = load_transcript(state.spath)
        if not loaded:
            print(f"[No file on disk for session {state.sid!r}; starting fresh.]\n")
            state.session = Session(system_prompt=state.initial)
            return "continue"
        state.session = Session.from_transcript(loaded)
        state.session.set_system_prompt(state.initial)
        print(f"[Reloaded {len(state.session.messages)} messages from disk.]\n")
        return "continue"

    if name == "compact":
        if maybe_compact(state.session.messages, force=True):
            print(f"[Compacted transcript: {len(state.session.messages)} messages.]\n")
            if persist:
                save_transcript(state.spath, state.session.messages)
        else:
            print("[No compaction performed (already small, disabled, or nothing safe to drop).]\n")
        return "continue"

    print(f"Unknown command {name!r}. Type /help for a list.\n")
    return "continue"


def _print_help() -> None:
    print(
        """\
Slash commands (control plane — not sent to the model):
  /help              Show this list
  /new               Clear in-memory chat; same session id; save if persistence on
  /session <id>      Switch session file (load if present, else empty)
  /sessions          List saved session ids
  /save              Write current transcript to disk now
  /reload            Discard in-memory history; load from disk for current session
  /compact           Force one compaction pass (see compaction.py / Stage 6)
  /quit              Exit the program (same as typing quit without slash)

"""
    )
