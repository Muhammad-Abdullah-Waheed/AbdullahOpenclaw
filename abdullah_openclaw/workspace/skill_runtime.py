"""Skill + attach-mode configuration loaded from `.env` and workspace (Stage 9).

Separated from ``main.py`` so the same loader runs at startup *and* on hot reload after
``.env`` or ``SKILL.md`` files change — same idea as OpenClaw reloading ``config*.yaml``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from abdullah_openclaw.paths import REPO_ROOT
from abdullah_openclaw.workspace.skills import Skill, build_system_prompt, discover_skill_ids, load_skills

# Historical name used by watchers + channel apps.
PROJECT_ROOT = REPO_ROOT


def _csv(name: str, default: str = "") -> list[str]:
    raw = (os.environ.get(name) or default).strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _skill_attach_mode() -> str:
    return (os.environ.get("SKILL_ATTACH_MODE") or "always").strip().lower()


def _apply_skill_ids_legacy(mode: str, always_ids: list[str], pool_ids: list[str]) -> tuple[list[str], list[str]]:
    legacy = _csv("SKILL_IDS")
    if not legacy:
        return always_ids, pool_ids

    if mode == "always":
        if not always_ids:
            always_ids = list(legacy)
        return always_ids, pool_ids

    if not pool_ids:
        pool_ids = list(legacy)
    return always_ids, pool_ids


@dataclass
class SkillRuntime:
    """Snapshot used by the REPL for skill routing and ``state.initial``."""

    foundation_prompt: str
    """Base system text before always-skills are stitched in."""

    mode: str
    always_skills: list[Skill]
    pool_ids: list[str]
    active_profiles: tuple[str, ...]
    initial: str


def rebuild_skill_runtime(*, base_system_prompt: str, dotenv_path: Path | None = None) -> SkillRuntime:
    """Reload ``os.environ`` from ``.env`` (override), then rebuild skills.

    Mirrors the skill-related logic that previously lived only in ``main.main``.
    """
    env_path = dotenv_path or (PROJECT_ROOT / ".env")
    if env_path.is_file():
        load_dotenv(env_path, override=True)

    mode = _skill_attach_mode()
    always_ids = _csv("SKILL_ALWAYS")
    pool_ids = _csv("SKILL_POOL")
    always_ids, pool_ids = _apply_skill_ids_legacy(mode, always_ids, pool_ids)

    if mode in {"auto", "both"} and not pool_ids:
        pool_ids = discover_skill_ids()

    active_profiles = tuple(_csv("SKILL_PROFILES"))

    always_skills = load_skills(always_ids)
    initial = build_system_prompt(base_system_prompt, always_skills)
    return SkillRuntime(
        foundation_prompt=base_system_prompt.strip(),
        mode=mode,
        always_skills=list(always_skills),
        pool_ids=list(pool_ids),
        active_profiles=active_profiles,
        initial=initial,
    )
