"""Chat-history helpers: turn prior turns into a compact transcript string.

The LLM clients take a single `prompt` string, so multi-turn memory is achieved
by rendering earlier turns into the prompt rather than a messages array. This
keeps the Ollama / OpenAI service layer unchanged.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol


class Turn(Protocol):
    role: str
    content: str


def format_history(
    history: Sequence[Turn] | Iterable[Turn] | None,
    *,
    max_turns: int = 12,
    max_chars: int = 6000,
) -> str:
    """Render prior turns as a `User:/Assistant:` transcript.

    Keeps only the last `max_turns` turns and trims to the last `max_chars`
    characters so a long conversation can't blow past the model's context.
    Returns "" when there is nothing to include.
    """
    if not history:
        return ""
    turns = [t for t in history if getattr(t, "content", "").strip()]
    turns = turns[-max_turns:]
    lines: list[str] = []
    for t in turns:
        label = "User" if t.role == "user" else "Assistant"
        lines.append(f"{label}: {t.content.strip()}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text
