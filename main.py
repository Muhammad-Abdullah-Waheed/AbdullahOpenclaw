import logging
import os

from dotenv import load_dotenv

load_dotenv()

from agent import Session
from skills import (
    Skill,
    build_system_prompt,
    discover_skill_ids,
    load_skills,
    select_skills_for_user_text,
)

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
    session = Session(system_prompt=initial)

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
            break

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

        session.set_system_prompt(build_system_prompt(BASE_SYSTEM_PROMPT, deduped))
        reply = session.chat(user_input)
        print("Assistant:", reply, "\n")


if __name__ == "__main__":
    main()