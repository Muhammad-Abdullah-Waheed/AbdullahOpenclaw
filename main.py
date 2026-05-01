import logging
import os

from dotenv import load_dotenv

load_dotenv()

from agent import Session
from session_store import (
    load_transcript,
    persistence_enabled,
    save_transcript,
    session_id_from_env,
    session_path,
)
from events import (
    AssistantReplyFinished,
    ReplStarted,
    SessionEnding,
    SlashCommandHandled,
    UserTurnReceived,
)
from event_bus import EventBus
from repl_observers import wire_default_observers
from skills import (
    Skill,
    build_system_prompt,
    discover_skill_ids,
    load_skills,
    select_skills_for_user_text,
)

from slash_commands import ReplState, handle_slash, parse_slash_line

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are Pickle, a friendly cat assistant.
You help with daily tasks, coding, questions, and creative work.
Occasionally throw in a cat-themed flourish ("Meow!", "*purrs*"), but stay useful.
When you don't know something, admit it honestly.
"""

def _csv(name: str, default: str = "") -> list[str]:
    raw = (os.environ.get(name) or default).strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _skill_attach_mode() -> str:
    # always | auto | both
    return (os.environ.get("SKILL_ATTACH_MODE") or "always").strip().lower()


def _apply_skill_ids_legacy(mode: str, always_ids: list[str], pool_ids: list[str]) -> tuple[list[str], list[str]]:
    """Back-compat: SKILL_IDS fills SKILL_ALWAYS / SKILL_POOL when those are unset."""
    legacy = _csv("SKILL_IDS")
    if not legacy:
        return always_ids, pool_ids

    if mode == "always":
        if not always_ids:
            always_ids = list(legacy)
        return always_ids, pool_ids

    # auto | both
    if not pool_ids:
        pool_ids = list(legacy)
    return always_ids, pool_ids


def main() -> None:
    mode = _skill_attach_mode()
    always_ids = _csv("SKILL_ALWAYS")
    pool_ids = _csv("SKILL_POOL")
    always_ids, pool_ids = _apply_skill_ids_legacy(mode, always_ids, pool_ids)

    if mode in {"auto", "both"} and not pool_ids:
        pool_ids = discover_skill_ids()

    active_profiles = tuple(_csv("SKILL_PROFILES"))

    always_skills = load_skills(always_ids)
    initial = build_system_prompt(BASE_SYSTEM_PROMPT, always_skills)

    sid = session_id_from_env()
    spath = session_path(sid)
    loaded = load_transcript(spath) if persistence_enabled() else None
    if loaded:
        session = Session.from_transcript(loaded)
        session.set_system_prompt(initial)
        logger.info(
            "session: resumed id=%s path=%s messages=%s",
            sid,
            spath,
            len(session.messages),
        )
    else:
        session = Session(system_prompt=initial)
        logger.info("session: new id=%s path=%s", sid, spath)

    state = ReplState(session=session, sid=sid, spath=spath, initial=initial)

    bus = EventBus()
    wire_default_observers(bus, state)
    bus.publish(
        ReplStarted(
            session_id=sid,
            skill_attach_mode=mode,
            always_skill_ids=tuple(s.id for s in always_skills),
            profile_tags=tuple(active_profiles),
        )
    )

    logger.info(
        "skills: mode=%s always=%s pool=%s profiles=%s",
        mode,
        [s.id for s in always_skills],
        pool_ids,
        list(active_profiles),
    )

    while True:
        user_input = input("You: ").strip()
        
        if not user_input or user_input.lower() in {"quit", "exit", "q"}:
            bus.publish(SessionEnding(state.sid, "eof_or_quit"))
            break

        parsed = parse_slash_line(user_input)
        if parsed is not None:
            name, args = parsed
            outcome = handle_slash(name, args, state=state, persist=persistence_enabled())
            bus.publish(SlashCommandHandled(state.sid, name, tuple(args), outcome))
            if outcome == "break":
                bus.publish(SessionEnding(state.sid, "slash_quit"))
                break
            continue

        active = list(always_skills)
        if mode in {"auto", "both"}:
            active.extend(
                select_skills_for_user_text(
                    user_input,
                    pool_ids=pool_ids,
                    active_profiles=active_profiles or None,
                )
            )

        # De-dupe while preserving order: always-skills first, then auto-selected.
        seen: set[str] = set()
        deduped: list[Skill] = []
        for s in active:
            if s.id in seen:
                continue
            seen.add(s.id)
            deduped.append(s)

        always_ids_set = {s.id for s in always_skills}
        auto_skill_ids = tuple(s.id for s in deduped if s.id not in always_ids_set)
        bus.publish(UserTurnReceived(state.sid, user_input, auto_skill_ids))

        state.session.set_system_prompt(build_system_prompt(BASE_SYSTEM_PROMPT, deduped))
        reply = state.session.chat(user_input)
        print("Assistant:", reply, "\n")
        bus.publish(
            AssistantReplyFinished(state.sid, reply, len(state.session.messages)),
        )


if __name__ == "__main__":
    main()