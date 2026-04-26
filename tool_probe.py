import json
import os
import pprint
from dotenv import load_dotenv
from litellm import completion

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("Missing GEMINI_API_KEY in .env")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate a numeric arithmetic expression like '2 + 2' or '3 * 7.5'. "
                "Use only digits, + - * / ( ) and spaces."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A single arithmetic expression to evaluate.",
                    }
                },
                "required": ["expression"],
            },
        },
    }
]

# One-message conversation — keeping it small so the response is legible in the terminal
messages: list[dict] = [
    {
        "role": "user",
        "content": (
            "Do NOT do arithmetic yourself. You MUST use the `calculator` tool. "
            "Compute exactly: 1234567 * 7654321"
        ),
    }
]

response = completion(
    model="gemini/gemini-flash-latest",
    messages=messages,
    tools=TOOLS,
    tool_choice="required",  # <— forces a tool call (if the provider allows it)
    temperature=0.0,         # <— reduces creative refusal / wandering
    api_key=api_key,
)

print("=" * 60)
print("Full response object (repr) — scroll through once, your eyes will adjust:")
pprint.pprint(response)

print("\n" + "=" * 60)
msg = response.choices[0].message
print("message.content:          ", repr(msg.content))  # often None / empty on tool-calls
print("message.tool_calls is None:", msg.tool_calls is None)

if msg.tool_calls:
    tc0 = msg.tool_calls[0]
    print("tool call id:   ", tc0.id)
    print("function name:  ", tc0.function.name)
    print("raw arguments:  ", tc0.function.arguments)  # stringified JSON, per OpenAI spec
    print("parsed args:    ", json.loads(tc0.function.arguments))
else:
    print("No tool_calls in response. Print the model text instead:")
    print(msg.content)