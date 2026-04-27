"""The agent's runtime: Session + a provider call loop that supports function calling (tools).

Stage 2 / Lesson 4: add a second tool and introduce a small "tool registry" (dispatch table),
instead of growing `if name == ...` forever.
"""

from __future__ import annotations

import ast
import copy
import json
import logging
import os
import re
import time
from typing import Any, Callable

from litellm import completion

# --- logging (Lesson 5: hygiene) ---

logger = logging.getLogger("abdullah_openclaw")

if not logging.getLogger().handlers:
    # Reasonable default for a CLI tool; can be tuned via $LOG_LEVEL
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

# --- tuning knobs (you can play with these) ---

# Model id (litellm route). You can override at runtime with $LLM_MODEL.
# Examples we used in this project:
# - gemini/gemini-flash-latest
# - groq/... (requires GROQ_API_KEY)
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini/gemini-flash-latest").strip()

# Hard safety cap: one *user* line can only trigger this many *LLM* completion calls
# in the inner tool loop (tool hops can chain).
MAX_LLM_HOPS = 8
# Tool choice policy:
# - "auto" is the normal default: model may answer normally OR call tool(s)
# - "required" *forces* a tool on the *first* hop (good for demos, annoying for chitchat)
FIRST_HOP_TOOL_CHOICE: str = "auto"
LATER_HOP_TOOL_CHOICE: str = "auto"

# A ToolHandler is: parsed JSON args object -> a string result for the model to read
ToolHandler = Callable[[dict[str, Any]], str]


# --- tool schemas (OpenAI "tools" format, passed to litellm) ---

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate a safe arithmetic expression using + - * /, **, parentheses, "
                "and unary +/-. The expression must be a single line of numbers and "
                "operators only (no names, no function calls, no semicolons)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Arithmetic expression, e.g. '2 + 2' or '(3+4)*5' or '2**20'.",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "word_count",
            "description": (
                "Count words in a text. A 'word' is a run of Unicode letters/digits/underscores "
                "(implementation uses a simple regex; not a full linguistics tokenizer)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to count words in.",
                    }
                },
                "required": ["text"],
            },
        },
    },
]


def _env_float(name: str, default: str) -> float:
    raw = os.environ.get(name, default).strip()
    return float(raw)


def get_llm_api_key() -> str:
    """Pick an API key that matches the configured model/provider.

    This prevents the common foot-gun: the model is Groq but the env var is still only GEMINI_API_KEY.
    You can also set LLM_API_KEY to force a single key regardless of model.
    """
    forced = (os.environ.get("LLM_API_KEY") or "").strip()
    if forced:
        return forced

    model = LLM_MODEL.lower()
    if model.startswith("groq/"):
        key = (os.environ.get("GROQ_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set, but LLM_MODEL is a Groq model.")
        return key

    # Default path for the Gemini Google AI Studio route used earlier in the tutorial.
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment")
    return key


# --- provider call ---

def llm_complete(messages: list[dict], *, tool_choice: str):
    api_key = get_llm_api_key()
    timeout_s = _env_float("LLM_TIMEOUT_SECONDS", "60")
    logger.info("llm_complete: messages=%s tool_choice=%s timeout_s=%s", len(messages), tool_choice, timeout_s)
    return completion(
        model=LLM_MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice=tool_choice,
        temperature=0.0,
        api_key=api_key,
        timeout=timeout_s,
    )


# --- message normalization (objects -> wire-shaped dicts) ---

def _as_dict(obj: Any) -> dict:
    """Best-effort: litellm/pydantic objects -> plain dicts we can re-send in `messages`."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj

    for meth_name in ("model_dump", "to_dict", "dict"):
        meth = getattr(obj, meth_name, None)
        if callable(meth):
            try:
                return meth()  # type: ignore[no-untyped-call]
            except TypeError:
                pass

    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return dict(obj.__dict__)

    raise TypeError(f"Cannot convert {type(obj)} to dict")


def tool_call_to_dict(tc: Any) -> dict:
    """Normalize a tool call into a dict with stringified `function.arguments` JSON (wire format)."""
    tc = _as_dict(tc)
    fn = _as_dict(tc.get("function"))
    if "arguments" in fn and not isinstance(fn["arguments"], str):
        fn["arguments"] = json.dumps(fn["arguments"])
    tc["function"] = fn
    return tc


def message_to_dict(msg: Any) -> dict:
    d = _as_dict(msg)
    if "role" not in d:
        d["role"] = "assistant"

    tcs = d.get("tool_calls")
    if tcs:
        d["tool_calls"] = [tool_call_to_dict(tc) for tc in tcs]

    return d


# --- tools: safe calculator + a second "boring" tool (word count) ---

class UnsafeExpressionError(ValueError):
    pass

def eval_arithmetic(expr: str) -> int | float:
    """Safely evaluate + - * /, **, parentheses, and unary +/- using Python's ast."""
    tree = ast.parse(expr, mode="eval")

    def _ev(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)

        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow):
                return _ev(node.left) ** _ev(node.right)

            left = _ev(node.left)
            right = _ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            raise UnsafeExpressionError(f"Unsupported binary op: {type(node.op)}")

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -_ev(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            return +_ev(node.operand)

        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value

        raise UnsafeExpressionError(f"Unsupported node: {type(node).__name__}")

    return _ev(tree)


def handle_calculator(args: dict[str, Any]) -> str:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        return "ERROR: missing expression"
    try:
        result = eval_arithmetic(expression)
    except (UnsafeExpressionError, SyntaxError, TypeError, ZeroDivisionError) as e:
        return f"ERROR: {type(e).__name__}: {e}"

    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


_WORD_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)


def handle_word_count(args: dict[str, Any]) -> str:
    text = str(args.get("text", ""))
    if len(text) > 50_000: return "ERROR: text too large"
    # Count "words" as letter/digit/underscore runs (simple and predictable).
    return str(len(_WORD_RE.findall(text)))


# Registry: the only place to add a new "real" tool implementation.
#
# The JSON tool schema still lives in `TOOLS` (provider-facing), but the Python behavior lives here.
# Keeping names aligned is a discipline you'll later formalize (tests, OpenAPI, etc.).
HANDLERS: dict[str, ToolHandler] = {
    "calculator": handle_calculator,
    "word_count": handle_word_count,
}


def run_tool(name: str, arguments_json: str) -> str:
    """Execute one tool. MUST return a string (tool `content` is always a string on the wire)."""
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"ERROR: invalid tool arguments JSON: {e}; raw={arguments_json!r}"

    if not isinstance(args, dict):
        return f"ERROR: tool arguments must be a JSON object, got {type(args).__name__}"

    handler = HANDLERS.get(name)
    if handler is None:
        return f"ERROR: unknown tool {name!r}"

    # Never let a tool crash the whole turn: tool failures are *observations* for the next model hop.
    try:
        return handler(args)
    except Exception as e:  # noqa: BLE001 (intentional: last-resort tool firewall)
        return f"ERROR: {type(e).__name__}: {e}"


class Session:
    def __init__(self, system_prompt: str) -> None:
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    @classmethod
    def from_transcript(cls, messages: list[dict]) -> Session:
        """Restore a session from a saved message list (must start with a system message)."""
        if not messages or messages[0].get("role") != "system":
            raise ValueError("Transcript must be non-empty and start with role=system.")
        s = cls.__new__(cls)
        s.messages = copy.deepcopy(messages)
        return s

    def set_system_prompt(self, system_prompt: str) -> None:
        """Replace the first system message in-place (keeps conversation history intact).

        Stage 3 uses this to attach/detach skills per user turn without resetting `messages`.
        """
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = system_prompt
            return

        # Defensive fallback: if the transcript doesn't start with system for some reason,
        # prepend a new system message.
        self.messages.insert(0, {"role": "system", "content": system_prompt})

    def chat(self, user_input: str) -> str:
        """Run one *user* turn, which may require multiple *model* calls if tools are used."""
        self.messages.append({"role": "user", "content": user_input})

        for hop in range(MAX_LLM_HOPS):
            tool_choice = FIRST_HOP_TOOL_CHOICE if hop == 0 else LATER_HOP_TOOL_CHOICE
            response = llm_complete(self.messages, tool_choice=tool_choice)

            raw_msg = response.choices[0].message
            assistant = message_to_dict(raw_msg)
            self.messages.append(assistant)

            tool_calls = assistant.get("tool_calls")
            if not tool_calls:
                if assistant.get("refusal") is not None:
                    return str(assistant["refusal"])

                content = assistant.get("content")
                if content is None:
                    raise RuntimeError("Model returned an empty assistant message (no text, no tools).")
                return str(content)

            for tc in tool_calls:
                try:
                    fn = tc["function"]
                    name = fn["name"]
                    arguments = fn["arguments"]
                except (KeyError, TypeError) as e:
                    raise RuntimeError(f"Malformed tool call object: {tc!r} ({e})") from e

                tool_call_id = str(tc.get("id") or "")
                if not tool_call_id:
                    tool_call_id = f"missing_id_{name}"

                t0 = time.perf_counter()
                tool_text = run_tool(name, str(arguments))
                dt_ms = (time.perf_counter() - t0) * 1000
                logger.info("tool_done: name=%s id=%s ms=%.1f err=%s", name, tool_call_id, dt_ms, tool_text.startswith("ERROR:"))

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": name,
                        "content": tool_text,
                    }
                )

        raise RuntimeError(
            f"Exceeded MAX_LLM_HOPS={MAX_LLM_HOPS} without a final assistant text response."
        )
