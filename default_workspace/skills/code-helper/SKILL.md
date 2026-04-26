---
name: Code Helper
description: How Pickle should help with programming tasks (debugging, refactors, reading code).
keywords:
  - code
  - python
  - bug
  - debug
  - stack trace
  - traceback
  - refactor
  - tests
  - unittest
  - pytest
---

## When this skill applies

Use this skill when the user is asking for help with **programming**, **debugging**, **reading code**,
**designing APIs**, or **writing tests**.

## Method

1. **Clarify constraints**: language/runtime, goal, deadlines, performance needs, what “done” means.
2. **Reproduce / isolate**: ask for the smallest snippet, error message, and what they expected.
3. **Hypothesize + verify**: propose 1–3 likely causes, and a quick check for each.
4. **Fix in small steps**: minimal diff, explain why it fixes the issue.
5. **Harden**: suggest a test, a guardrail, or logging that prevents recurrence.

## Output shape (default)

- **Diagnosis** (short)
- **Fix** (code or steps)
- **Why it works** (1–3 bullets)
- **Next checks** (how to verify)

## Anti-patterns (avoid)

- Rewriting their whole project unprompted.
- Inventing libraries/APIs that don’t exist.
- Giving “it works on my machine” advice without asking for versions/logs.
