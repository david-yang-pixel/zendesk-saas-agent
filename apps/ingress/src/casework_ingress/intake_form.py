"""Recognize the plain-text rendering of the Zendesk contact form."""

import re

_FIELD = re.compile(r"^(name|email|workspace id|plan)\s*:\s*([^\n]+)$", re.I)


def is_contact_form_only(body: str) -> bool:
    fields: set[str] = set()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _FIELD.fullmatch(line)
        if match is None:
            return False
        field = match[1].casefold()
        if field in fields:
            return False
        if field == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+", match[2]):
            return False
        fields.add(field)
    return {"name", "email"} <= fields
