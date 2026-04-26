# AbdullahOpenclaw

A from-scratch agent system, built **one file at a time**, to learn agent architecture
by feeling the pain of growth firsthand.

Inspired by [Build Your Own OpenClaw](https://github.com/openclaw/build-your-own-openclaw),
but we evolve our code organically: start with a single script, refactor only when complexity
demands it.

See `LEARNING_LOG.md` for what each stage covered and **why**.

## Setup (run on your real terminal, not in the IDE sandbox)

```bash
# 1. Install uv (one-time, system-wide)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. From this folder
uv sync                       # creates .venv, installs deps from pyproject.toml
cp .env.example .env          # then edit .env and paste your GEMINI_API_KEY

# 3. Run (after Stage 1 code exists)
uv run python main.py
```

## Current state

Stage 0 (architecture mental model) — done.
Stage 1 (the simplest possible chat with an LLM) — in progress.
Everything else — pending. We will not declare structure ahead of need.