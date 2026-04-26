# AbdullahOpenclaw — Learning Log

A first-principles re-build of an agent system, inspired by **Build Your Own OpenClaw**.
Goal: internalize the *why* behind every design decision so I can build production-grade
agent systems independently. Approach: **organic** — the architecture emerges as pain
demands it, not by upfront declaration.

---

## Stage 0 — Architectural Mental Model

### The agent loop (kernel)
`perceive → think → act → remember → repeat.`

An LLM is a stateless `text → text` function. An *agent* is the **harness** that supplies
what the LLM lacks: continuity (memory), senses (inputs), hands (tools), skills, identity,
and reach (multi-agent).

### Six-layer architecture
1. **Interface** — CLI, channels, websocket, cron, HTTP.
2. **Event bus / dispatcher** — routes events to sessions and agents.
3. **Orchestration** — `Agent`, `AgentSession`, the chat/tool loop.
4. **Capability** — tools, skills, web tools.
5. **State / memory** — `SessionState`, persistence, long-term memory.
6. **Provider** — `LLMProvider` (litellm), web providers; the only place vendor APIs live.

Plus a declarative **Workspace** plane (configs, AGENT.md, SKILL.md, CRON.md, sessions/).

### Six design patterns
1. **Provider abstraction** (ports & adapters) — vendor swap is a config change.
2. **Definition-as-code** — YAML frontmatter + markdown body.
3. **Session = behavior, State = plain data** — swappable memory backends.
4. **Workspace pattern** — runtime ships separately from user-editable world.
5. **Loop → event-driven pivot** — happens later; the most important architectural shift.
6. **Composition over god-classes** — `Agent` stays small; capabilities compose in.

---

## Stage 1 — From a script to a runtime (8 lessons, single-file evolution)

### What was built
- `main.py` (the runner): persona constant + entry-point that wires up a Session and runs the loop.
- `agent.py` (the runtime): `llm_chat()` provider call + `Session` class that owns its `messages` list.

### Concepts internalized
1. **The messages list IS the memory.** The LLM is stateless; replay history every call.
2. **The system prompt rides along on every call.** Persona is not a server-side setting; it's `messages[0]` re-sent every turn.
3. **Provider abstraction in action.** Switching `gemini-2.0-flash` → `gemini-flash-latest` was one string change. Vendor risk lives in one place.
4. **Refactor by smelling seams.** First extraction (`llm_chat`) felt the *provider* boundary.
   Second extraction (`chat_turn`) felt the *orchestration* boundary.
   The class (`Session`) was born when two parameters refused to stop traveling together.
5. **Files = namespaces.** Splitting forces hidden dependencies to become explicit (the api_key closure surfaced when we moved `llm_chat`). Modules encapsulate at a higher granularity than classes do.
6. **`if __name__ == "__main__":`** — protects scripts from running on import.

### Production-relevant lessons hit along the way
- **Smoke tests first.** Prove the wire works before building structure.
- **Read errors layer-by-layer.** `RateLimitError → VertexAIError → MaskedHTTPStatusError`; bottom-up reading is a transferable skill.
- **`limit: 0` is structural, not transient.** Tells you a model is deprecated or your project lacks allotment.
- **Models deprecate; pin in production but always have a runbook for migration.**
- **Never paste secrets in chat/logs.** Rotate immediately if leaked.
- **Delta isolation:** when curl works and code doesn't, the bug is the difference between them.
- **Prompt injection is a soft, learned preference of the model — not a hardware boundary.** Captain-Pickle outcomes are typical; defense-in-depth is the only real mitigation.

### Mapping our code to OpenClaw concepts
| Our code | OpenClaw concept |
|---|---|
| `agent.py::llm_chat` | `LLMProvider.chat()` |
| `agent.py::Session` | `AgentSession` + `SessionState` |
| `main.py::SYSTEM_PROMPT` | `AgentDef.agent_md` (loaded from `AGENT.md`) |
| `main.py::main()` loop | `ChatLoop.run()` |

### Smells we left intact (deferred, not forgotten)
- **`Session.chat` mutates `self.messages`** — fine until parallel sessions need to share/snapshot state.
- **`SYSTEM_PROMPT` is hardcoded in `main.py`** — will become workspace data when we add multi-agent.
- **`Session` knows nothing about tools** — Stage 2 fixes this.
- **No persistence** — Stage 4 fixes this.

---

## Stage 2 — Tools (function calling)
*(completed in this project: end-to-end tool loop + registry + operability/hygiene.)*

### Lesson 1 — the protocol
- Tooling is: LLM may emit a `tool_calls` assistant message, your code runs the function, you append
  a `{"role": "tool", "tool_call_id": ..., "content": ...}` message, then you call the LLM again to get
  a natural-language `content` for the user.
- One user input can require *multiple* LLM calls in a `while` loop.
- The messages list is still the only memory; new roles: assistant-with-tool-calls, tool.

### Lesson 2 — the probe
- A throwaway `tool_probe.py` that passes `tools=[...]`, `tool_choice="required"`, `temperature=0.0`, and
  `pprint`s the `response` so you can *see* `message.tool_calls` and the double-encoded `arguments` string.
- Goal: *observe* a tool call before *executing* one — the same pedagogical order as the smoke test in
  Stage 1.

## Stage 3 — Skills (SKILL.md)

### What a skill is (vs a tool)
- A **tool** is an action with an observation (`calculator`, HTTP calls, DB queries, file IO).
- A **skill** is **promptable knowledge** stored as data: playbooks, checklists, domain tone, and “how to
  behave in situation X”.

### Lesson 1 — load `SKILL.md` and inject into the system prompt
- Added `default_workspace/skills/study-tutor/SKILL.md` (frontmatter + body).
- Added `skills.py` to parse frontmatter, load skills by id, and render a single markdown block appended to
  the base system prompt in `main.py` via `SKILL_IDS=...` (comma-separated).
- Added dependency: `pyyaml` (frontmatter parsing).

### Lesson 2 — prompt budgeting: always-on vs auto-attached skills
- Skills cost tokens (they become part of the system layer). Shipping every skill on every turn is usually
  wrong once you have more than a handful.
- Added `keywords:` to skill frontmatter and a baseline matcher: substring match against the user text.
- Added `SKILL_ATTACH_MODE` (`always|auto|both`) plus `SKILL_ALWAYS` and `SKILL_POOL`, with backward
  compatibility for `SKILL_IDS`.
- Added `Session.set_system_prompt(...)` so `main.py` can update the system message **per turn** without
  resetting conversation history.
