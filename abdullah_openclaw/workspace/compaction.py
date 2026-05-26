"""Context compaction (Stage 6) — staying inside the model’s finite attention budget.

================================================================================
CONCEPTS (read this like a mini chapter; the code below is one possible design)
================================================================================

1) What is a “context window”?
------------------------------
Modern LLMs do not read your whole hard drive. Each API call sends a **finite list of
messages** (system / user / assistant / tool). The provider allocates a **context window**
— a maximum number of **tokens** those messages may occupy. If you exceed it, you get a
hard error (request rejected) or, on some stacks, silent truncation by the provider.

A **token** is not a word; it is a chunk from a tokenizer (often sub-word). Rule of thumb
for English prose: ~4 characters ≈ 1 token, but code, JSON, and rare words deviate. For
production you eventually call the provider’s tokenizer or `tiktoken`; here we use a
**character budget on JSON‑serialized messages** as a cheap, debuggable proxy.

2) Why compaction exists (the engineering problem)
----------------------------------------------------
Long transcripts grow without bound: every user line, assistant reply, tool call, and
tool result stays in `messages` unless you **delete**, **summarize**, or **externalize**
older content. Without a policy, you eventually:

- hit context limits,
- pay more per request (many providers bill by input tokens),
- slow down inference,
- and sometimes **dilute** the model’s attention (“lost in the middle” phenomenon in
  long prompts — important facts buried in old turns get under-weighted).

Compaction is the **host’s policy** for shrinking `messages` while keeping the chat usable.

3) Strategies (general patterns you can reuse anywhere)
-------------------------------------------------------

**A. Sliding window (keep last K turns)**  
Drop the oldest messages. Simple and fast. **Risk:** you must not break provider rules
(e.g. a `tool` message must follow an `assistant` message that issued matching
`tool_calls`). This module keeps a **suffix that starts at a `user` message** and walks
left until it does, so you do not begin the kept region with a dangling `tool` fragment.

**B. Middle summarization (MemGPT-style “core memory” sketch)**  
Keep: `[system] + [summary of old middle] + [recent verbatim tail]`. The summary is
lossy but preserves *some* long-horizon state. Optional second model call costs tokens
but buys coherence.

**C. Hierarchical memory**  
Maintain separate stores: “working transcript” (short) + “episodic log” (append-only) +
“semantic memory” (vector DB). The model only sees the working set; retrieval tools pull
facts when needed. This is how many production agents scale.

**D. Retrieval (RAG) instead of stuffing history**  
Don’t paste all old docs into the prompt; **embed** them and fetch top‑k chunks relevant
to the latest user query. Orthogonal to compaction: RAG handles *knowledge*; compaction
handles *dialogue length*.

**E. Structured state**  
For task agents, store JSON state (shopping cart, ticket fields) in your DB and inject a
short snapshot into system each turn — instead of relying on full chat history.

4) What THIS module implements
------------------------------
A **practical baseline**: when the serialized transcript exceeds `COMPACTION_CHAR_BUDGET`,
replace the **oldest middle** (after `system`) with **one synthetic `user` message**
carrying either a static note or an **optional LLM‑generated summary** of the removed
segment, then keep a **verbatim tail** of the most recent messages.

This is intentionally small, testable, and close to patterns used in real agents — not a
full memory architecture.

5) Pitfalls to remember in your future projects
-----------------------------------------------

- **Tool call integrity:** never delete an `assistant` tool-call block without also deleting
  its `tool` results (or vice versa). Providers validate message graphs.
- **Lossy compression:** summaries hallucinate or omit constraints; expose that to users or
  downstream tools when safety matters.
- **System prompt drift:** if you compact poorly, you might duplicate or contradict system
  instructions; keep `system` message #0 stable and authoritative.
- **Evaluation:** measure task success before/after compaction on your workloads; cheap
  heuristics can silently break multi-step workflows.

================================================================================
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_MAX_COMPACTION_PASSES = 256

_SUMMARY_PREFIX = (
    "[Context compaction: earlier messages were replaced with this summary to save space. "
    "If something important is missing, ask the user.]\n\n"
)


def compaction_enabled() -> bool:
    raw = (os.environ.get("COMPACTION_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def messages_wire_size(messages: list[dict[str, Any]]) -> int:
    """Rough size proxy: UTF-8 length of JSON (correlates with token load)."""
    return len(json.dumps(messages, ensure_ascii=False))


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _safe_tail_start(messages: list[dict[str, Any]], keep_last: int) -> int:
    """Index of first message in a kept suffix; suffix is ``messages[start:]``.

    Ensures ``messages[start]`` is a ``user`` message when possible by extending backward,
    so we do not keep a fragment starting with ``tool`` or a stray ``assistant`` turn.
    """
    if len(messages) <= 1:
        return 1
    keep_last = max(1, min(keep_last, len(messages) - 1))
    start = max(1, len(messages) - keep_last)
    while start > 1 and messages[start].get("role") != "user":
        start -= 1
    return start


def _static_summary() -> str:
    return (
        "Earlier conversation omitted. Continue using the visible tail and the system "
        "instructions. Ask the user if you need a detail that may have been dropped."
    )


def _middle_to_linear_text(middle: list[dict[str, Any]], *, max_chars: int = 12_000) -> str:
    """Flatten middle messages into a readable block for summarization prompts."""
    parts: list[str] = []
    for m in middle:
        role = m.get("role", "?")
        if role == "tool":
            tid = m.get("tool_call_id", "")
            name = m.get("name", "")
            content = m.get("content", "")
            parts.append(f"tool[{name} id={tid}]: {content}")
        elif role == "assistant" and m.get("tool_calls"):
            parts.append(f"assistant [called tools]: {m.get('tool_calls')}")
        else:
            c = m.get("content")
            parts.append(f"{role}: {c if isinstance(c, str) else ''}")
    blob = "\n".join(parts)
    if len(blob) > max_chars:
        return blob[: max_chars // 2] + "\n...[truncated for summarizer input]...\n" + blob[-max_chars // 2 :]
    return blob


def _llm_summarize_middle(middle: list[dict[str, Any]]) -> str:
    """Optional second call: compress ``middle`` into short prose (may cost tokens)."""
    from litellm import completion

    from abdullah_openclaw.core.agent import LLM_MODEL, get_llm_api_key

    model = (os.environ.get("COMPACTION_SUMMARY_MODEL") or LLM_MODEL).strip()
    timeout_s = float((os.environ.get("LLM_TIMEOUT_SECONDS") or "60").strip())
    api_key = get_llm_api_key()
    blob = _middle_to_linear_text(middle)
    prompt = (
        "Summarize the following chat fragment for continuing a dialogue.\n"
        "Output tight bullet points: facts, decisions, open tasks, user preferences, errors.\n"
        "Do not invent details; if unsure, say unknown.\n\n"
        f"---\n{blob}\n---"
    )
    response = completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        api_key=api_key,
        timeout=timeout_s,
    )
    raw_msg = response.choices[0].message
    text = getattr(raw_msg, "content", None) or (raw_msg.get("content") if isinstance(raw_msg, dict) else None)
    return str(text).strip() if text else ""


def _make_summary(middle: list[dict[str, Any]]) -> str:
    use_llm = (os.environ.get("COMPACTION_SUMMARIZE") or "0").strip().lower() in {"1", "true", "yes", "on"}
    if use_llm:
        try:
            out = _llm_summarize_middle(middle)
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            logger.warning("compaction: LLM summarization failed (%s); using static note", e)
    return _static_summary()


def maybe_compact(messages: list[dict[str, Any]], *, force: bool = False) -> bool:
    """Shrink ``messages`` in place if over budget (or once if ``force``).

    Returns ``True`` if the list was mutated.
    """
    if not messages or messages[0].get("role") != "system":
        return False
    if not force and not compaction_enabled():
        return False

    budget = _env_int("COMPACTION_CHAR_BUDGET", 120_000)
    keep = _env_int("COMPACTION_KEEP_LAST_MESSAGES", 24)
    min_keep = _env_int("COMPACTION_MIN_KEEP_MESSAGES", 6)

    if not force and messages_wire_size(messages) <= budget:
        return False

    changed = False
    passes = 0
    while True:
        passes += 1
        if passes > _MAX_COMPACTION_PASSES:
            logger.warning("compaction: stopped after %s passes (safety cap)", _MAX_COMPACTION_PASSES)
            break
        start = _safe_tail_start(messages, keep)
        if start <= 1:
            logger.warning("compaction: cannot find removable middle (tail consumes almost all messages)")
            break
        middle = messages[1:start]
        if not middle:
            break

        summary_text = _make_summary(middle)
        summary_msg: dict[str, Any] = {"role": "user", "content": _SUMMARY_PREFIX + summary_text}
        tail = messages[start:]
        new_list = [messages[0], summary_msg] + tail
        messages[:] = new_list
        changed = True
        logger.info(
            "compaction: removed %s messages; new_size=%s keep_target=%s",
            len(middle),
            messages_wire_size(messages),
            keep,
        )

        if force:
            break
        keep = max(min_keep, int(keep * 0.75))
        if messages_wire_size(messages) <= budget:
            break

    return changed
