"""Slash commands — local control plane (Stage 5).

What this is
------------
In a chat REPL, most lines are *user content* sent to the model. **Slash commands** are
lines the **host program** handles first. They start with ``/`` (e.g. ``/help``, ``/new``).

Why separate from the LLM?
---------------------------
1. **Reliability** — The model should not be the only way to "start over" or "switch
   workspace"; those are host responsibilities with precise semantics.
2. **Cost & latency** — Meta-actions should not burn tokens or API calls.
3. **Safety** — Destructive or structural actions stay in your code, not in prompt soup.

This is the same idea as slash commands in Cursor, Discord bots, IRC, etc.: a **prefix
convention** plus a **local dispatch table** (like your tool registry, but for the CLI).

How it works here
-----------------
``main.py`` reads a line. If it looks like a command, we parse ``/name arg1 arg2``,
dispatch to a handler, and **do not** call ``session.chat``. Otherwise the line is normal
user input and goes to the model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from abdullah_openclaw.core.agent import Session
from abdullah_openclaw.agents.loader import AgentMissingError, discover_agent_ids, load_agent, validate_agent_exists
from abdullah_openclaw.agents.runtime import get_skill_runtime_for_agent
from abdullah_openclaw.workspace.compaction import maybe_compact
from abdullah_openclaw.workspace.hot_reload_signals import request_skill_env_reload
from abdullah_openclaw.routing.session_stems import CLI_SOURCE_STRING, cli_transcript_stem, effective_cli_agent_id
from abdullah_openclaw.routing.table import Binding, append_binding, bindings_from_disk, default_agent, load_routing_raw
from abdullah_openclaw.workspace.session_store import (
    list_session_paths,
    load_transcript,
    sanitize_session_id,
    save_transcript,
    session_path,
)

logger = logging.getLogger(__name__)

Outcome = Literal["continue", "break"]


def parse_slash_line(line: str) -> tuple[str, list[str]] | None:
    """If ``line`` is a slash command, return ``(name_lower, args)``; else ``None``."""
    s = line.strip()
    if not s.startswith("/"):
        return None
    inner = s[1:].strip()
    if not inner:
        return "help", []
    parts = inner.split()
    return parts[0].lower(), parts[1:]


@dataclass
class ReplState:
    """Mutable REPL state slash handlers can update."""

    session: Session
    sid: str
    spath: Path
    initial: str
    """System prompt with base + *always* skills (same as startup `initial` in main)."""

    cli_session_label: str = ""
    """Logical SESSION_ID-ish label separate from routed agent."""

    cli_route_override: str | None = None
    """`/agent`; when unset, YAML routes ``platform-cli``."""



def cli_rehome(state: ReplState, *, persist: bool) -> None:
    """Recompute transcript path + persona after /agent, /session, or /route."""

    label = state.cli_session_label or "default"
    aid = effective_cli_agent_id(state.cli_route_override)
    stem = cli_transcript_stem(cli_agent_override=state.cli_route_override, session_label=label)
    state.sid = stem
    state.spath = session_path(stem)

    rt = get_skill_runtime_for_agent(aid)
    state.initial = rt.initial

    loaded = load_transcript(state.spath) if persist else None
    if loaded is None and persist and stem == "pickle.cli.default":
        loaded = load_transcript(session_path("default"))

    if loaded:
        state.session = Session.from_transcript(loaded)
        state.session.set_system_prompt(state.initial)
        logger.info(
            "cli_rehome: resumed agent=%s id=%s messages=%s path=%s",
            aid,
            stem,
            len(state.session.messages),
            state.spath.name,
        )
    else:
        state.session = Session(system_prompt=state.initial)
        logger.info("cli_rehome: fresh session agent=%s id=%s", aid, stem)


def handle_slash(
    name: str,
    args: list[str],
    *,
    state: ReplState,
    persist: bool,
) -> Outcome:
    if name in {"help", "h", "?"}:
        _print_help()
        return "continue"

    if name in {"quit", "exit", "q"}:
        return "break"

    if name == "new":
        state.session = Session(system_prompt=state.initial)
        if persist:
            save_transcript(state.spath, state.session.messages)
        print(f"[New chat in session {state.sid!r} — transcript reset.]\n")
        return "continue"

    if name == "session":
        if not args:
            print("Usage: /session <id>  — switch logical session label (see Stage 12 routing).\n")
            return "continue"
        raw = " ".join(args)
        state.cli_session_label = sanitize_session_id(raw)
        cli_rehome(state, persist=persist)
        n = len(state.session.messages)
        print(
            f"[Switched transcript label → {state.cli_session_label!r} "
            f"(disk stem {state.sid!r}, {n} messages loaded or new).]\n"
        )
        logger.info(
            "slash: session label=%s stem=%s agent=%s",
            state.cli_session_label,
            state.sid,
            effective_cli_agent_id(state.cli_route_override),
        )
        return "continue"

    if name in {"sessions", "ls"}:
        paths = list_session_paths()
        if not paths:
            print("(no saved sessions yet)\n")
            return "continue"
        print("Saved sessions:")
        for p in paths:
            mark = " <-- current" if p.resolve() == state.spath.resolve() else ""
            print(f"  - {p.stem}{mark}")
        print()
        return "continue"

    if name == "save":
        if not persist:
            print("[Persistence is off (SESSION_PERSIST=0); nothing to save.]\n")
            return "continue"
        save_transcript(state.spath, state.session.messages)
        print(f"[Saved {len(state.session.messages)} messages to {state.spath.name}.]\n")
        return "continue"

    if name in {"reload", "reload-config"}:
        request_skill_env_reload(reason="slash")
        print("[Skills and .env will reload before your next message.]\n")
        return "continue"

    if name in {"reload-session", "reload-disk"}:
        if not persist:
            print("[Persistence is off; /reload-session has no effect.]\n")
            return "continue"
        loaded = load_transcript(state.spath)
        if not loaded:
            print(f"[No file on disk for session {state.sid!r}; starting fresh.]\n")
            state.session = Session(system_prompt=state.initial)
            return "continue"
        state.session = Session.from_transcript(loaded)
        state.session.set_system_prompt(state.initial)
        print(f"[Reloaded {len(state.session.messages)} messages from disk.]\n")
        return "continue"

    if name == "compact":
        if maybe_compact(state.session.messages, force=True):
            print(f"[Compacted transcript: {len(state.session.messages)} messages.]\n")
            if persist:
                save_transcript(state.spath, state.session.messages)
        else:
            print("[No compaction performed (already small, disabled, or nothing safe to drop).]\n")
        return "continue"

    if name == "agents":
        ids = discover_agent_ids()
        if not ids:
            print("(No agents yet — create default_workspace/agents/<id>/AGENT.md).\n")
            return "continue"
        print("Workspace agents:")
        for aid in ids:
            try:
                dfn = load_agent(aid)
            except (AgentMissingError, OSError):
                continue
            print(f"  - {aid}: {dfn.name} — {dfn.description or '(no description)'}")
        print()
        return "continue"

    if name == "bindings":
        load_routing_raw()
        print(f"default_agent: {default_agent()!r}")
        print("bindings (first match wins within tie tier):")
        rows = bindings_from_disk()
        if not rows:
            print("  (empty — only default_agent is used)\n")
        else:
            for b in rows:
                print(f"  - {b.value!r} → {b.agent!r}  (tier {b.tier})")
            print()
        return "continue"

    if name == "route":
        if len(args) < 2:
            print(
                "Usage: /route <regex> <agent_id>\n"
                f"  Example: /route {CLI_SOURCE_STRING} cookie\n"
                "  Anchored match: pattern must match the *entire* source string (^…$).\n"
            )
            return "continue"
        agent_word = args[-1].strip()
        pattern = " ".join(args[:-1]).strip()
        try:
            Binding(agent=agent_word, value=pattern)
        except re.error as e:
            print(f"[Bad regex pattern: {e}]\n")
            return "continue"
        try:
            validate_agent_exists(agent_word)
        except AgentMissingError:
            print(f"[Unknown agent {agent_word!r} — put AGENT.md under agents/{agent_word}/]\n")
            return "continue"
        append_binding(pattern, agent_word)
        print(f"[Saved route {pattern!r} → {agent_word!r} in routing.yaml]\n")
        cli_rehome(state, persist=persist)
        return "continue"

    if name == "agent":
        if not args:
            eff = effective_cli_agent_id(state.cli_route_override)
            ovr = state.cli_route_override or "(none — routing.yaml chooses platform-cli)"
            print(f"[CLI source string: {CLI_SOURCE_STRING!r}]")
            print(f"[effective agent: {eff!r}; `/agent` override: {ovr}]\n")
            return "continue"

        verb = args[0].lower()
        if verb in {"reset", "clear", "routing"}:
            state.cli_route_override = None
            cli_rehome(state, persist=persist)
            print("[Cleared `/agent` override — `routing.yaml` controls CLI again.]\n")
            return "continue"

        pick = sanitize_session_id(" ".join(args))
        try:
            validate_agent_exists(pick)
        except AgentMissingError:
            print(f"[Unknown agent {pick!r} — create agents/{pick}/AGENT.md]\n")
            return "continue"
        state.cli_route_override = pick
        cli_rehome(state, persist=persist)
        print(f"[CLI now uses agent {pick!r} regardless of YAML (until `/agent reset`).]\n")
        return "continue"

    print(f"Unknown command {name!r}. Type /help for a list.\n")
    return "continue"


def _print_help() -> None:
    print(
        """\
Slash commands (control plane — not sent to the model):
  /help              Show this list
  /new               Clear in-memory chat; same transcript stem; save if persistence on
  /session <id>      Switch transcript label ({agent}.cli.{label} Stage 12 layout)
  /sessions          List saved session stems
  /save              Write current transcript to disk now
  /reload-session    Discard in-memory history; load transcript from disk (was /reload)
  /reload (or /reload-config)
                     Re-read .env and skill files — applies before next user message (Stage 9)
  /agents            List agents from workspace agents/*/AGENT.md (Stage 12)
  /bindings          Print routing.yaml: default_agent + bindings (regex → agent)
  /route <regex> <agent>
                     Append binding (^regex$ anchored on full EventSource strings)
  /agent             Show who answers ``platform-cli``; override or reset persona
  /compact           Force one compaction pass (see compaction.py / Stage 6)
  /quit              Exit the program (same as typing quit without slash)
"""
    )
