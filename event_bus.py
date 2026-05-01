"""Minimal in-process publish/subscribe bus (Stage 8).

**Publish** pushes a typed dataclass instance. **Subscribers** are callables keyed by exact
event type (not inherited lookup). Exceptions in one subscriber do not suppress others.

This is intentionally tiny: one stepping stone toward OpenClaw’s async ``EventBus`` +
``Worker`` model with persisted outbound events.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

E = TypeVar("E")


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[type, list[Callable[..., None]]] = defaultdict(list)

    def subscribe(self, event_cls: type[E], handler: Callable[[E], None]) -> None:
        self._subs[event_cls].append(handler)  # type: ignore[list-item]
        logger.debug(
            "event_bus: subscribe %s -> %s",
            event_cls.__name__,
            getattr(handler, "__name__", repr(handler)),
        )

    def publish(self, event: object) -> None:
        cls = type(event)
        handlers = list(self._subs.get(cls, ()))
        if not handlers:
            logger.debug("event_bus: publish %s (no subscribers)", cls.__name__)
            return
        for h in handlers:
            try:
                h(event)
            except Exception:
                logger.exception(
                    "event_bus: handler %s failed on %s",
                    getattr(h, "__name__", h),
                    cls.__name__,
                )
