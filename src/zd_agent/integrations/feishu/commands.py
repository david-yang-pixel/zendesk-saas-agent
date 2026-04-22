"""Feishu command parsing: '/cmd arg1 arg2' → Command object."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


_KNOWN_COMMANDS = {
    "branch",
    "fetch-all",
    "reprocess",
    "process",
    "status",
    "pause",
    "resume",
    "relogin",
    "help",
}


@dataclass
class Command:
    name: str
    args: list[str] = field(default_factory=list)
    raw: str = ""


def parse_command(text: str) -> Optional[Command]:
    """Parse '/cmd arg1 arg2' string.

    Returns:
        Command with name == "unknown" if the prefix '/' was present but the
            command isn't in _KNOWN_COMMANDS (the original name goes into args[0]).
        Command with parsed name + args for known commands.
        None if the text doesn't start with '/'.
    """
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text[1:].split()
    if not parts:
        return None
    name = parts[0].lower()
    args = parts[1:]
    if name not in _KNOWN_COMMANDS:
        return Command(name="unknown", args=[name, *args], raw=text)
    return Command(name=name, args=args, raw=text)
