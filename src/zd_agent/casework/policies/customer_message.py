from __future__ import annotations

import re


_NON_SUBSTANTIVE_MESSAGE_RE = re.compile(
    r"^(?:"
    r"(?:hi|hello|hey|greetings)(?:\s+(?:there|everyone|team|support|dify))?|"
    r"good\s+(?:morning|afternoon|evening)|"
    r"(?:thanks|thank\s+you)(?:\s+(?:a\s+lot|very\s+much|so\s+much))?|"
    r"much\s+appreciated|sounds\s+good|got\s+it|understood|"
    r"ok(?:ay)?|alright|k|thx|ty|"
    r"any\s+update|still\s+there|you\s+there|"
    r"谢谢(?:了)?|好的?|嗯嗯?|在吗|急+|请问还在吗|您还在吗|"
    r"[👍👌✅🥲🙏😅]"
    r")$",
    re.IGNORECASE,
)


def is_definitely_non_substantive_message(body: str) -> bool:
    """Recognize only standalone greeting, acknowledgement, or nudge text."""
    normalized = " ".join(str(body or "").split()).strip()
    normalized = normalized.strip(" \t\r\n.!?,。！？，:;\"'“”‘’")
    return bool(normalized and _NON_SUBSTANTIVE_MESSAGE_RE.fullmatch(normalized))
