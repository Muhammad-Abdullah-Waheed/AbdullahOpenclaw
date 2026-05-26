"""Watch ``.env`` and ``skills/**`` for edits; debounce; signal main thread (Stage 9).

Uses ``watchdog`` like ``build-your-own-openclaw/08-config-hot-reload``. The interpreter
runs ``input()`` on the main thread, so mutations to skills / session prompts happen
**between turns** when ``reload_before_next_turn`` is set — never mid-LLM-call.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from abdullah_openclaw.workspace.hot_reload_signals import request_skill_env_reload
from abdullah_openclaw.workspace.skills import workspace_dir
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _Debouncer:
    def __init__(self, bounce_s: float) -> None:
        self._bounce_s = max(0.05, bounce_s)
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def schedule(self) -> None:
        def fire() -> None:
            logger.info("skill_reload_watcher: debounced reload signal")
            request_skill_env_reload()

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._bounce_s, fire)
            self._timer.daemon = True
            self._timer.start()


class _SkillsTreeHandler(FileSystemEventHandler):
    def __init__(self, debouncer: _Debouncer) -> None:
        self._debouncer = debouncer

    def on_modified(self, event) -> None:  # noqa: ANN001
        if getattr(event, "is_directory", False):
            return
        self._debouncer.schedule()

    def on_created(self, event) -> None:  # noqa: ANN001
        if getattr(event, "is_directory", False):
            return
        self._debouncer.schedule()

    def on_moved(self, event) -> None:  # noqa: ANN001
        self._debouncer.schedule()


class _DotEnvOnlyHandler(FileSystemEventHandler):
    """Non-recursive watch on project root → only ``.env`` filename."""

    def __init__(self, debouncer: _Debouncer, env_name: str = ".env") -> None:
        self._debouncer = debouncer
        self._env_name = env_name

    def _match(self, path: str) -> bool:
        return Path(path).name == self._env_name

    def on_modified(self, event) -> None:  # noqa: ANN001
        if getattr(event, "is_directory", False) or not self._match(event.src_path):
            return
        logger.info("skill_reload_watcher: .env touched")
        self._debouncer.schedule()

    def on_created(self, event) -> None:  # noqa: ANN001
        if getattr(event, "is_directory", False) or not self._match(event.src_path):
            return
        self._debouncer.schedule()

    def on_moved(self, event) -> None:  # noqa: ANN001
        dest = getattr(event, "dest_path", "")
        if self._match(dest):
            self._debouncer.schedule()


class SkillEnvReloader:
    def __init__(self, *, project_root: Path, debounce_s: float | None = None) -> None:
        import os

        raw = (os.environ.get("HOT_RELOAD_DEBOUNCE_SECONDS") or "").strip()
        try:
            d = float(raw) if raw else (debounce_s if debounce_s is not None else 0.35)
        except ValueError:
            d = 0.35
        self._debouncer = _Debouncer(d)
        self._project_root = project_root.resolve()
        self._skills_root = workspace_dir().resolve() / "skills"
        self._observer = Observer()
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        if self._skills_root.exists():
            self._observer.schedule(_SkillsTreeHandler(self._debouncer), str(self._skills_root), recursive=True)
            logger.info("skill_reload_watcher: watching %s", self._skills_root)
        else:
            logger.warning("skill_reload_watcher: skills dir missing %s", self._skills_root)

        self._observer.schedule(_DotEnvOnlyHandler(self._debouncer), str(self._project_root), recursive=False)

        self._observer.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._observer.stop()
        try:
            self._observer.join(timeout=3.0)
        except RuntimeError:
            pass
        self._started = False


def skill_hot_reload_enabled() -> bool:
    import os

    raw = (os.environ.get("SKILL_HOT_RELOAD") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def clear_pending_reload() -> None:
    """Drop any queued signal (e.g. during controlled shutdown)."""
    import hot_reload_signals as hrs

    hrs.reload_before_next_turn.clear()
