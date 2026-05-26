"""Route ``EventSource`` strings to agent ids (Stage 12 / ``11-multi-agent-routing``).

Bindings are anchored regex patterns (match full string ``^pattern$``) sorted by *tier*
(more specific first), then YAML file order inside a tier — same idea as the reference
:class:`RoutingTable`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern
from typing import Any

import yaml

from abdullah_openclaw.workspace.skills import workspace_dir


@dataclass
class Binding:
    agent: str
    value: str
    tier: int = field(init=False)
    pattern: Pattern[str] = field(init=False)

    def __post_init__(self) -> None:
        self.pattern = re.compile(f"^{self.value}$")
        self.tier = self._compute_tier()

    def _compute_tier(self) -> int:
        specials = frozenset(r".*+?[]()|^$\\")
        if not any(c in self.value for c in specials):
            return 0
        if r".*" in self.value:
            return 2
        return 1


def routing_yaml_path(root: Path | None = None) -> Path:
    return (root or workspace_dir()) / "routing.yaml"


def _default_routing_dict() -> dict[str, Any]:
    return {"default_agent": "pickle", "bindings": []}


def ensure_routing_file(path: Path) -> Path:
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(_default_routing_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def load_routing_raw(path: Path | None = None) -> dict[str, Any]:
    p = routing_yaml_path() if path is None else Path(path)
    ensure_routing_file(p)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"routing YAML must be a mapping: {p}")
    bindings = data.get("bindings", [])
    if not isinstance(bindings, list):
        raise ValueError("routing.bindings must be a YAML list")
    return data


def save_routing_doc(data: dict[str, Any], path: Path | None = None) -> None:
    p = routing_yaml_path() if path is None else Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


_bindings_cache: tuple[int, tuple[Binding, ...]] | None = None


def _invalidate_cache() -> None:
    global _bindings_cache  # noqa: PLW0603 — module-level LRU-style cache reset
    _bindings_cache = None


def invalidate_routing_bindings_cache() -> None:
    """Call after edits to routing.yaml (or before tests)."""
    _invalidate_cache()


def _normalized_bindings(rows: Any) -> list[tuple[str, str]]:
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, str]] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"bindings[{i}] must be a mapping with agent/value")
        a = raw.get("agent")
        v = raw.get("value")
        if not isinstance(a, str) or not a.strip():
            raise ValueError(f"bindings[{i}].agent must be non-empty string")
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"bindings[{i}].value must be non-empty regex pattern")
        out.append((a.strip(), v.strip()))
    return out


def bindings_from_disk(path: Path | None = None) -> tuple[Binding, ...]:
    global _bindings_cache
    raw = load_routing_raw(path)
    rows_t = tuple(_normalized_bindings(raw.get("bindings")))
    hv = hash((raw.get("default_agent", "pickle"), rows_t))

    cached = _bindings_cache
    if cached is not None and cached[0] == hv:
        return cached[1]

    with_order = [(Binding(agent=a, value=v), i) for i, (a, v) in enumerate(rows_t)]
    with_order.sort(key=lambda x: (x[0].tier, x[1]))
    out = tuple(b for b, _ in with_order)
    _bindings_cache = (hv, out)
    return out


def default_agent(path: Path | None = None) -> str:
    raw = load_routing_raw(path)
    d = raw.get("default_agent", "pickle")
    return d.strip() if isinstance(d, str) and d.strip() else "pickle"


def resolve_agent_for_source(source: str, *, path: Path | None = None) -> str:
    """Return agent id matched by bindings, else ``default_agent``."""
    s = source.strip()
    for b in bindings_from_disk(path):
        try:
            if b.pattern.match(s):
                return b.agent
        except re.error:
            continue
    return default_agent(path)


def append_binding(source_pattern: str, agent_id: str, *, path: Path | None = None) -> None:
    p = routing_yaml_path() if path is None else Path(path)
    ensure_routing_file(p)
    raw = load_routing_raw(p)
    rows = raw.get("bindings", [])
    if not isinstance(rows, list):
        rows = []
    rows.append({"agent": agent_id, "value": source_pattern.strip()})
    raw["bindings"] = rows
    save_routing_doc(raw, p)
    _invalidate_cache()
