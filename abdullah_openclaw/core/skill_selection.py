"""Per-turn skill list for user text (CLI and channels).

Shared between ``main.py`` and async channel fronts (Stage 10) so behavior matches OpenClaw’s
idea: one agent pipeline with multiple **EventSources** feeding the same selection rules.
"""

from __future__ import annotations

from abdullah_openclaw.workspace.skill_runtime import SkillRuntime
from abdullah_openclaw.workspace.skills import Skill, select_skills_for_user_text


def skills_for_turn(rt: SkillRuntime, user_text: str) -> tuple[list[Skill], tuple[str, ...]]:
    """Attach-always/auto skills, dedupe preserving order (always-first), plus auto-selected ids."""
    active = list(rt.always_skills)
    if rt.mode in {"auto", "both"}:
        active.extend(
            select_skills_for_user_text(
                user_text,
                pool_ids=rt.pool_ids,
                active_profiles=rt.active_profiles or None,
            )
        )

    seen: set[str] = set()
    deduped: list[Skill] = []
    for s in active:
        if s.id in seen:
            continue
        seen.add(s.id)
        deduped.append(s)

    always_ids_set = {s.id for s in rt.always_skills}
    auto_skill_ids = tuple(s.id for s in deduped if s.id not in always_ids_set)
    return deduped, auto_skill_ids
