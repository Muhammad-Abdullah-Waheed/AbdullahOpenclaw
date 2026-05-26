"""Thin CLI shim. Prefer: ``python -m abdullah_openclaw`` or ``uv run openclaw``."""

from abdullah_openclaw.apps.cli import main

if __name__ == "__main__":
    main()
