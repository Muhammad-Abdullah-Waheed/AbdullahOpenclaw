"""Load workspace agent definitions from ``agents/<id>/AGENT.md`` (Stage 12).

YAML frontmatter + Markdown body — same shaping idea as ``skills.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from abdullah_openclaw.workspace.skills import workspace_dir, parse_frontmatter_markdown


@dataclass(frozen=True)
class AgentDef:
    id: str
    name: str
    description: str
    body: str


class AgentMissingError(KeyError):
    pass


class AgentFrontmatterError(ValueError):
    pass


def agents_dir(root: Path | None = None) -> Path:
    d = (root or workspace_dir()) / "agents"
    return d.resolve()




def load_agent(agent_id: str, *, workspace: Path | None = None) -> AgentDef:
    aid = agent_id.strip()
    path = agents_dir(workspace) / aid / "AGENT.md"
    if not path.is_file():
        raise AgentMissingError(aid)

    meta, body = parse_frontmatter_markdown(path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise AgentFrontmatterError("YAML frontmatter did not yield a mapping")
    # parse_frontmatter already merged yaml into meta dict
    name = meta.get("name") or aid
    if not isinstance(name, str):
        raise AgentFrontmatterError("`name` in frontmatter must be a string")
    desc_raw = meta.get("description") or ""
    description = desc_raw.strip() if isinstance(desc_raw, str) else ""

    return AgentDef(
        id=aid,
        name=name.strip(),
        description=description,
        body=body.strip(),
    )


def discover_agent_ids(workspace: Path | None = None) -> list[str]:
    root = agents_dir(workspace)
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and (p / "AGENT.md").is_file():
            out.append(p.name)
    return out


def validate_agent_exists(agent_id: str, workspace: Path | None = None) -> None:
    load_agent(agent_id, workspace=workspace)
