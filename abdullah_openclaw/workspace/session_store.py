"""Persist chat transcripts (Stage 4).

Stores `Session.messages` as JSON under `<workspace>/sessions/<id>.json`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from abdullah_openclaw.workspace.skills import workspace_dir

_SESSION_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def persistence_enabled() -> bool:
    raw = (os.environ.get("SESSION_PERSIST") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def sanitize_session_id(raw: str) -> str:
    """Make a safe session id for filenames (used by /session and $SESSION_ID)."""
    cleaned = _SESSION_ID_RE.sub("_", raw.strip()).strip("._-") or "default"
    return cleaned[:120]


def session_id_from_env() -> str:
    raw = (os.environ.get("SESSION_ID") or "default").strip()
    if not raw:
        return "default"
    return sanitize_session_id(raw)


def sessions_dir(root: Path | None = None) -> Path:
    d = (root or workspace_dir()) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_path(session_id: str | None = None, root: Path | None = None) -> Path:
    sid = session_id if session_id is not None else session_id_from_env()
    return sessions_dir(root) / f"{sid}.json"


def load_transcript(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, list) or not raw:
        return None
    for m in raw:
        if not isinstance(m, dict):
            return None
        if not isinstance(m.get("role"), str):
            return None
    if raw[0].get("role") != "system":
        return None
    return raw


def save_transcript(path: Path, messages: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(messages, ensure_ascii=False, indent=2)
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def list_session_paths(root: Path | None = None) -> list[Path]:
    """Return sorted session transcript paths (`*.json` under sessions/)."""
    d = sessions_dir(root)
    paths = sorted(d.glob("*.json"), key=lambda p: p.name.lower())
    return paths
