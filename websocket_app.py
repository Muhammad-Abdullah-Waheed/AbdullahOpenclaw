"""Thin shim. Prefer: ``uv run openclaw-ws``."""

import logging
import os

from abdullah_openclaw.apps.websocket import main


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
