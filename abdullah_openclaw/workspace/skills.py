"""Skills loading (Stage 3).

A "skill" is reusable, versioned *prompt knowledge* stored as data (usually `SKILL.md`),
distinct from "tools" which are executable functions.

This mirrors the pattern used across OpenClaw / Cursor-style workspaces:
YAML frontmatter + markdown body.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from abdullah_openclaw.paths import REPO_ROOT


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    version: str
    tags: tuple[str, ...]
    profiles: tuple[str, ...]
    priority: int
    infer_keywords: bool
    keywords: tuple[str, ...]
    body: str


def default_workspace_dir() -> Path:
    """Workspace root on disk (``<repo>/default_workspace`` unless WORKSPACE_DIR is set)."""
    return (REPO_ROOT / "default_workspace").resolve()


def workspace_dir() -> Path:
    raw = (os.environ.get("WORKSPACE_DIR") or "").strip()
    return Path(raw).expanduser().resolve() if raw else default_workspace_dir()


def parse_frontmatter_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Parse optional YAML frontmatter delimited by --- ... ---.

    If no frontmatter is present, returns ({}, full_text).
    """
    # Normalize Windows newlines so delimiter checks stay simple and reliable.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text

    front = text[4:end]
    body = text[end + 5 :]
    meta = yaml.safe_load(front) or {}
    if not isinstance(meta, dict):
        raise ValueError("Skill frontmatter must parse to a YAML mapping (dict).")
    return meta, body


def _normalize_str_list(raw: Any) -> tuple[str, ...]:
    """Normalize YAML list-or-string fields into lowercased strings.

    Used for `keywords`, `tags`, and `profiles` so authors can write either:
    - a YAML list: [a, b, c]
    - a comma-separated string: \"a, b, c\"
    - a single scalar
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
        return tuple(p.lower() for p in parts if p)
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            s = str(item).strip()
            if s:
                out.append(s.lower())
        return tuple(out)
    s = str(raw).strip()
    return (s.lower(),) if s else ()


def _infer_keywords_from_text(*parts: str) -> tuple[str, ...]:
    """Very small keyword inference: extract 3+ letter tokens from strings.

    This is intentionally conservative: it only exists for skills that opt in via `infer_keywords: true`.
    """
    blob = " ".join(p for p in parts if p).lower()
    toks = re.findall(r"[a-z]{3,}", blob)
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in toks:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return tuple(out)


def load_skill(skill_id: str, root: Path | None = None) -> Skill:
    root = root or workspace_dir()
    path = root / "skills" / skill_id / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    return load_skill_from_text(skill_id, raw)


def load_skill_from_text(skill_id: str, text: str) -> Skill:
    meta, body = parse_frontmatter_markdown(text)

    name = str(meta.get("name") or skill_id)
    description = str(meta.get("description") or "")
    version = str(meta.get("version") or "").strip() or "0"
    tags = _normalize_str_list(meta.get("tags"))
    profiles = _normalize_str_list(meta.get("profiles"))

    priority_raw = meta.get("priority", 0)
    try:
        priority = int(priority_raw)
    except Exception:
        priority = 0

    infer_keywords = bool(meta.get("infer_keywords", False))

    keywords = _normalize_str_list(meta.get("keywords"))
    if infer_keywords and not keywords:
        keywords = _infer_keywords_from_text(name, description, " ".join(tags), " ".join(profiles))

    return Skill(
        id=skill_id,
        name=name.strip(),
        description=description.strip(),
        version=version,
        tags=tags,
        profiles=profiles,
        priority=priority,
        infer_keywords=infer_keywords,
        keywords=keywords,
        body=body.strip(),
    )


def load_skills(skill_ids: list[str], root: Path | None = None) -> list[Skill]:
    out: list[Skill] = []
    for sid in skill_ids:
        sid = sid.strip()
        if not sid:
            continue
        out.append(load_skill(sid, root=root))
    return out


def discover_skill_ids(root: Path | None = None) -> list[str]:
    """Return skill ids by scanning default_workspace/skills/*/SKILL.md."""
    root = root or workspace_dir()
    skills_dir = root / "skills"
    if not skills_dir.exists():
        return []

    ids: list[str] = []
    for child in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if (child / "SKILL.md").exists():
            ids.append(child.name)
    return ids


def select_skills_for_user_text(
    user_text: str,
    *,
    pool_ids: list[str],
    active_profiles: tuple[str, ...] | None = None,
    root: Path | None = None,
) -> list[Skill]:
    """Pick skills from a candidate pool using simple keyword matching.

    This is a deliberately dumb baseline (fast, debuggable). Production systems often replace this with:
    embeddings, a small classifier, or an explicit user toggle (/skill ...).
    """
    hay = user_text.lower()
    prof = tuple(p.lower() for p in (active_profiles or ()) if p)

    matches: list[Skill] = []
    for sid in pool_ids:
        path = (root or workspace_dir()) / "skills" / sid / "SKILL.md"
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        skill = load_skill_from_text(sid, raw)

        # Profile gating:
        # - if SKILL_PROFILES is empty: do not gate (skills apply broadly)
        # - if skill has no profiles: it is eligible in any profile mode
        # - else: require intersection
        if prof and skill.profiles and not (set(skill.profiles) & set(prof)):
            continue

        if not skill.keywords:
            continue
        if any(k in hay for k in skill.keywords):
            matches.append(skill)

    matches.sort(key=lambda s: (-s.priority, pool_ids.index(s.id)))
    return matches


def render_skills_block(skills: list[Skill]) -> str:
    """Render skills as a single markdown section suitable for the system prompt."""
    if not skills:
        return ""

    parts: list[str] = []
    parts.append("## Attached skills")
    parts.append(
        "The following skills are active. If a skill conflicts with generic advice, "
        "follow the skill (it is domain-specific guidance)."
    )
    parts.append("")

    for s in skills:
        parts.append(f"### Skill: {s.name} (`{s.id}`)")
        meta_bits: list[str] = []
        if s.version and s.version != "0":
            meta_bits.append(f"v{s.version}")
        if s.tags:
            meta_bits.append("tags: " + ", ".join(s.tags))
        if s.profiles:
            meta_bits.append("profiles: " + ", ".join(s.profiles))
        if s.priority != 0:
            meta_bits.append(f"priority: {s.priority}")
        if meta_bits:
            parts.append("_" + " · ".join(meta_bits) + "_")
            parts.append("")
        if s.description:
            parts.append(f"_{s.description}_")
            parts.append("")
        parts.append(s.body.strip())
        parts.append("")

    return "\n".join(parts).strip() + "\n"


def build_system_prompt(base_system_prompt: str, skills: list[Skill]) -> str:
    block = render_skills_block(skills)
    if not block:
        return base_system_prompt

    return (base_system_prompt.rstrip() + "\n\n" + block).strip() + "\n"
