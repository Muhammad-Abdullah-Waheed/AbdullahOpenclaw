"""Per-agent SkillRuntime caches (Stage 12).

Each agent overlays its ``AGENT.md`` body onto :data:`repl_base_prompt.SHARED_BASE_PROMPT`
before applying skill attach rules inside :func:`skill_runtime.rebuild_skill_runtime`.
"""

from __future__ import annotations

from pathlib import Path

from abdullah_openclaw.agents.loader import load_agent

from abdullah_openclaw.workspace.repl_base_prompt import SHARED_BASE_PROMPT

from abdullah_openclaw.workspace.skill_runtime import PROJECT_ROOT, SkillRuntime, rebuild_skill_runtime


_rt_by_agent: dict[str, SkillRuntime] = {}


def compose_base_system_prompt_for_agent(agent_id: str) -> str:
    definition = load_agent(agent_id)
    body = definition.body.strip()
    if body:
        return f"{SHARED_BASE_PROMPT.strip()}\n\n{body}"
    return SHARED_BASE_PROMPT.strip()


def get_skill_runtime_for_agent(agent_id: str, *, reload: bool = False) -> SkillRuntime:
    if reload:
        _rt_by_agent.pop(agent_id, None)
    cached = _rt_by_agent.get(agent_id)
    if cached is None:
        base = compose_base_system_prompt_for_agent(agent_id)
        dot = PROJECT_ROOT / ".env"
        cached = rebuild_skill_runtime(
            base_system_prompt=base,
            dotenv_path=dot if dot.is_file() else None,
        )
        _rt_by_agent[agent_id] = cached
    return cached


def clear_skill_runtime_agent_cache() -> None:
    _rt_by_agent.clear()
