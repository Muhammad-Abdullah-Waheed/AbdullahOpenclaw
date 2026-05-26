"""In-process HTTP fetch cache with optional ETag / Last-Modified revalidation.

Process-local only (not shared across workers). For multi-process deployments use Redis
or a shared object store with the same key scheme.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("abdullah_openclaw.web_cache")

_lock = threading.Lock()
_store: OrderedDict[str, dict[str, Any]] = OrderedDict()


def cache_enabled() -> bool:
    import os

    raw = (os.environ.get("WEBFETCH_CACHE_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def normalize_cache_key(url: str) -> str:
    """Stable key: drop fragment, lowercase scheme + host, keep path + query."""
    p = urlparse(url.strip())
    if not p.scheme or not p.netloc:
        return url.strip()
    netloc = p.netloc.lower()
    path = p.path or "/"
    return urlunparse((p.scheme.lower(), netloc, path, p.params, p.query, ""))


def _touch_lru(key: str) -> None:
    if key in _store:
        _store.move_to_end(key)


def cache_get(key: str) -> dict[str, Any] | None:
    with _lock:
        if key not in _store:
            return None
        _touch_lru(key)
        return dict(_store[key])


def cache_set(key: str, payload: dict[str, Any]) -> None:
    import os

    max_n = _env_int("WEBFETCH_CACHE_MAX_ENTRIES", 128)
    with _lock:
        _store[key] = payload
        _store.move_to_end(key)
        while len(_store) > max_n:
            _store.popitem(last=False)


def memory_hit_seconds() -> int:
    """If age < this, return cached body without opening a socket."""
    return _env_int("WEBFETCH_CACHE_MEMORY_SECONDS", 120)


def try_return_fresh_memory(url_key: str) -> str | None:
    if not cache_enabled():
        return None
    ent = cache_get(url_key)
    if not ent:
        return None
    age = time.monotonic() - float(ent.get("mono", 0.0))
    if age < memory_hit_seconds():
        logger.info("web_cache: memory hit key=%s age=%.1fs", url_key[:80], age)
        return str(ent.get("display", ""))
    return None


def load_entry_for_revalidate(url_key: str) -> dict[str, Any] | None:
    if not cache_enabled():
        return None
    return cache_get(url_key)


def store_success(
    url_key: str,
    *,
    display: str,
    etag: str | None,
    last_modified: str | None,
) -> None:
    if not cache_enabled():
        return
    cache_set(
        url_key,
        {
            "display": display,
            "etag": etag,
            "last_modified": last_modified,
            "mono": time.monotonic(),
        },
    )
