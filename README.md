# Abdullah OpenClaw

<div align="center">

**A production-minded, educational agent host built from first principles**

*Inspired by [Build Your Own OpenClaw](https://github.com/openclaw/build-your-own-openclaw)*

Python · Multi-channel · Skills · Tools · Routing

</div>

---

## Why this repository exists

This project is a **hands-on study in agent system engineering**: not a thin wrapper around a chat API, but a **real host** that owns the conversation loop, tools, configuration, persistence, channels, and routing—similar in spirit to how serious agent platforms separate **workspace data** from **runtime code**.

It demonstrates that you can reason about **architecture** (events, sessions, providers, workspace) while shipping **working software** (CLI, Telegram, WebSocket, hot reload, multi-agent routing). The design log in [`LEARNING_LOG.md`](LEARNING_LOG.md) explains the *why* behind early stages; this README summarizes *what* ships today and what comes next.

---

## What is implemented (Stages 0–12)

| Stage | Capability |
|------|------------|
| **0–1** | Mental model + **LLM chat loop** via LiteLLM (`Session`, message history, configurable model/timeouts). |
| **2** | **Function calling**: tool protocol, multi-step tool rounds, registry-style dispatch (web search + fetch). |
| **3** | **`SKILL.md`** workspace skills: YAML frontmatter, keyword-based auto-attach, profiles, `SKILL_ATTACH_MODE` (`always` / `auto` / `both`). |
| **4** | **Transcript persistence**: JSON sessions under the workspace, configurable `SESSION_ID` / `SESSION_PERSIST`. |
| **5** | **Slash commands** in the REPL: `/help`, `/new`, `/session`, `/save`, compaction and config reload hooks, etc. |
| **6** | **Compaction**: budget-based transcript shrinking to stay within practical context limits. |
| **7** | **Web tools**: HTTP fetch with caching/ETag semantics; optional **Crawl4AI** rendering via optional extra `[crawl]`; **Tavily** web search. |
| **8** | **Event-driven internals**: `EventBus` + observers decouple logging, persistence side effects, and lifecycle events from the raw readline loop. |
| **9** | **Hot reload**: Watch `.env` and skill files; debounced reload before the next user turn (aligned with “config changes without restart”). |
| **10** | **Telegram channel**: Bot app with allowlists, chunked outbound messages, routing-aware transcripts. |
| **11** | **WebSocket + HTTP** (FastAPI/Uvicorn): JSON protocol, session stems, shared agent loop patterns with the CLI. |
| **12** | **Multi-agent routing**: `default_workspace/agents/<id>/AGENT.md` personas, **`routing.yaml`** regex bindings on full source strings, CLI `/agent` and `/route` control plane, per-agent skill runtime cache. |

The codebase is packaged as **`abdullah-openclaw`** (installable with `uv` / pip) under the `abdullah_openclaw/` tree—**`default_workspace/`** holds skills, agents, sessions, and routing data you can edit without touching Python.

---

## Architecture (high level)

- **Apps** (`apps/`) — Entrypoints: CLI REPL, Telegram, WebSocket server.  
- **Core** (`core/`) — `Session` / provider loop, domain events, skill selection glue.  
- **Workspace** (`workspace/`) — Skills runtime, compaction, session store, reload signals, watchers.  
- **Routing** (`routing/`) — Parse `routing.yaml`, resolve `EventSource` strings → agent id.  
- **Agents** (`agents/`) — Load `AGENT.md` definitions and compose per-agent foundation prompts.  
- **Integrations** (`integrations/`) — Web fetch, search, crawl helpers.  
- **REPL** (`repl/`) — Slash command dispatch and observers.  
- **Channels** (`channels/`) — Telegram / WebSocket source typing and helpers.

Repository root keeps **`default_workspace/`**, **`.env`**, and thin **`main.py`** shims for familiarity; canonical runs use **`python -m abdullah_openclaw`** or the console scripts below.

---

## Tech stack

| Area | Choice |
|------|--------|
| Language | Python **3.11+** |
| LLM access | **LiteLLM** (multi-provider; e.g. Gemini via `GEMINI_API_KEY`) |
| HTTP / WS | **FastAPI**, **Uvicorn**, **Starlette** |
| Telegram | **python-telegram-bot** ≥ 20 |
| Config / data | **`python-dotenv`**, **PyYAML**, workspace markdown + YAML |
| Search / fetch | **Tavily** (optional key), urllib + optional **crawl4ai** |
| Packaging | **Hatchling**, **`uv`** recommended |

Optional heavy rendering: `uv sync --extra crawl` (then follow Crawl4AI/Playwright setup as needed).

---

## Repository layout

```text
AbdullahOpenclaw/
├── pyproject.toml              # Package metadata, dependencies, console scripts
├── README.md                   # This file
├── LEARNING_LOG.md             # Deeper notes for Stages 0–3 (conceptual history)
├── .env.example                # Documented environment variables (copy → .env)
├── default_workspace/          # Skills, agents, routing.yaml, sessions (runtime data)
│   ├── skills/
│   ├── agents/
│   ├── routing.yaml
│   └── sessions/
├── abdullah_openclaw/          # Main Python package
│   ├── paths.py                # REPO_ROOT vs package root (.env + workspace here)
│   ├── apps/                   # CLI, Telegram, WebSocket
│   ├── core/
│   ├── workspace/
│   ├── routing/
│   ├── agents/
│   ├── integrations/
│   ├── repl/
│   └── channels/
├── main.py                     # Shim → `abdullah_openclaw.apps.cli`
├── telegram_app.py             # Shim → Telegram app
└── websocket_app.py            # Shim → WebSocket app
```

---

## Quick start

```bash
# Install uv (once): https://docs.astral.sh/uv/

cd AbdullahOpenclaw
uv sync                              # editable install + virtualenv
cp .env.example .env                 # add GEMINI_API_KEY (or your provider keys)

# CLI REPL
uv run openclaw                      # recommended
# or: uv run python -m abdullah_openclaw
# or: uv run python main.py

# Optional services (configure in .env — see .env.example)
uv run openclaw-telegram
uv run openclaw-ws
```

**Configuration:** Most behavior is driven by environment variables. **`.env.example`** is the single source of truth for stages, defaults, and comments—prefer updating that file when you add new toggles so collaborators (and hiring managers cloning the repo) can orient quickly.

---

## Roadmap — remaining stages (13–18)

Stages **0–12** are implemented in this repository as described above. The following stages describe **planned** work aligned with extending an OpenClaw-style host toward fuller autonomy and scale. Each paragraph is intentionally one stage.

**Stage 13 — Cron & heartbeat (autonomy)** introduces time-based and periodic drivers so the agent is not purely reactive: scheduled jobs (cron-like), heartbeat ticks, and workspace-defined schedules wired into the existing event/host model so maintenance, reminders, or background checks can run without a user message.

**Stage 14 — Multi-layer prompts** formalizes prompt composition—foundation persona, agent body, skill blocks, channel-specific overlays, tool instructions—with explicit layering order and regression-friendly tests so growing prompt surface area stays understandable and avoids silent conflicts between layers.

**Stage 15 — Post-message-back (proactive messaging)** lets the host push outbound turns to users or channels without an inbound stimulus—task completion, proactive summaries, or async worker results—while honoring the same session, routing, and transcript rules as conversational replies.

**Stage 16 — Agent dispatch & sub-agents** adds structured delegation so one “lead” agent can spawn or hand off to specialized workers, aggregate outputs, and manage sub-task lifecycles without collapsing everything into one linear chat transcript where it does not belong.

**Stage 17 — Concurrency control** hardens overlapping traffic: per-session sequencing, locking or queueing strategies across Telegram/WebSocket/future interfaces, and safe tool execution so parallel user actions cannot corrupt transcripts or double-apply side effects.

**Stage 18 — Long-term memory** moves beyond JSON transcripts to durable, retrievable memory—embeddings or structured stores, RAG-style retrieval into the prompt, explicit remember/forget policies, and workspace-level configuration—so useful facts survive session boundaries without naive “dump everything in context.”

---

## For employers & reviewers

- **Clarity of boundaries:** Workspace (data) vs package (code); routing vs agent definitions vs skills.  
- **Operability:** Hot reload, persistence, slash control plane, multiple interfaces.  
- **Honest scope:** Stages 13–18 are roadmap items—this README states that explicitly.  
- **Read next:** [`LEARNING_LOG.md`](LEARNING_LOG.md) for architectural narrative on early lessons.

---

## Contributing & license

Issues and PRs are welcome if you extend the workspace model or add tests. This repository does not yet include a default **LICENSE** file—add one (e.g. MIT, Apache-2.0) before publishing if you want clear reuse terms.

---

## Publishing to GitHub (you run these locally)

This README is written for a public audience; **pushing** is a separate step from editing docs:

1. Add a **LICENSE** if you want standard open-source expectations.  
2. Ensure **`.env`** is **gitignored** and never committed (keep **`.env.example`** only).  
3. `git add README.md LEARNING_LOG.md abdullah_openclaw/ pyproject.toml default_workspace/ ...`  
4. `git commit -m "docs: professional README and package overview"`  
5. `git push origin <your-branch>`  

If you use **GitHub Actions** later, add a small CI workflow (lint, `uv sync`, `python -m compileall`) and optional badges in this README.
