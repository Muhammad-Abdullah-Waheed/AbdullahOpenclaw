"""Shared base text prepended before each agent's persona block (Stage 12).

Keeps one canonical string so CLI / Telegram / WebSocket stay aligned."""

SHARED_BASE_PROMPT = """You are Pickle's workspace assistant shell.
Stay helpful, honest, and clear. Respect the persona block below when present.
"""
