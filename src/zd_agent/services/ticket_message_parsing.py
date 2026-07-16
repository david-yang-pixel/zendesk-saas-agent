from __future__ import annotations

import re
from typing import Any

from zd_agent.services.ticket_images import (
    collect_image_urls_from_attachments,
    extract_inline_image_urls,
)


def _message_created_at_sort_key(message: dict[str, Any]) -> str:
    value = message.get("created_at")
    return value if isinstance(value, str) else ""


def extract_form_data_from_msgs(
    customer_msgs: list[dict[str, Any]],
) -> dict[str, str]:
    for message in reversed(customer_msgs):
        form_data = message.get("form_data")
        if isinstance(form_data, dict):
            normalized = {
                key: str(value).strip()
                for key, value in form_data.items()
                if key in {"name", "email", "workspace_id"}
                and str(value).strip()
            }
            if normalized:
                return normalized

    for message in reversed(customer_msgs):
        out: dict[str, str] = {}
        body = message.get("body") or ""
        if "Name:" not in body and "Workspace ID:" not in body:
            continue

        for line in body.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue
            if key == "name":
                out["name"] = value
            elif key == "email":
                out["email"] = value
            elif key in ("workspace id", "wid"):
                out["workspace_id"] = value
        if out:
            return out
    return {}


def _customer_public_comments(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requester_id = ticket.get("requester_id")
    out: list[dict[str, Any]] = []
    for comment in comments:
        if not comment.get("public"):
            continue
        if comment.get("author_id") != requester_id:
            continue
        image_urls = collect_image_urls_from_attachments(comment.get("attachments"))
        for field in ("body", "html_body", "plain_body"):
            image_urls.extend(extract_inline_image_urls(comment.get(field)))
        out.append({**comment, "image_urls": image_urls})
    out.sort(key=_message_created_at_sort_key)
    return out


def _strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _form_field_value(field: dict[str, Any]) -> str:
    for key in ("value", "text", "email"):
        value = field.get(key)
        if value not in (None, ""):
            return str(value).strip()
    selected = field.get("select") or []
    if isinstance(selected, list):
        return ", ".join(
            str(item.get("label") or item.get("value") or "").strip()
            for item in selected
            if isinstance(item, dict)
            and (item.get("label") or item.get("value"))
        )
    return ""


def _form_data(fields: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for field in fields:
        label = str(field.get("label") or "").strip()
        name = str(field.get("name") or "").strip()
        value = _form_field_value(field)
        if not value:
            continue
        normalized = label.lower()
        if normalized == "name" or name.endswith("requester.name"):
            out["name"] = value
        elif normalized == "email" or name.endswith("requester.email"):
            out["email"] = value
        elif normalized in {"workspace id", "wid"}:
            out["workspace_id"] = value
    return out


def normalize_messaging_content(content: dict[str, Any]) -> dict[str, Any]:
    """Normalize one Zendesk Messaging content object exactly once."""
    content_type = str(content.get("type") or "")
    body = ""
    image_urls: list[str] = []
    form_data: dict[str, str] = {}
    if content_type == "text":
        body = str(content.get("text") or "")
        image_urls.extend(extract_inline_image_urls(body))
    elif content_type == "html":
        raw_body = str(content.get("body") or "")
        body = _strip_html(raw_body)
        image_urls.extend(extract_inline_image_urls(raw_body))
    elif content_type in {"form", "formResponse"}:
        fields = [
            field for field in (content.get("fields") or [])
            if isinstance(field, dict)
        ]
        form_data = _form_data(fields)
        body = str(content.get("text_fallback") or "")
        if not body:
            body = "\n".join(
                f"{field.get('label') or field.get('name')}: "
                f"{_form_field_value(field)}".rstrip()
                for field in fields
                if field.get("label") or field.get("name")
            )
        if content_type == "form" and body:
            body = "[表单]\n" + body
        image_urls.extend(extract_inline_image_urls(body))
    elif content_type == "image":
        alt = content.get("alt_text") or "image"
        url = str(content.get("media_url") or "")
        body = f"[客户附图 / Customer attached image: {alt}]"
        if url:
            body += f"\nURL: {url}"
            image_urls.append(url)
    elif content_type in {"file", "video"}:
        alt = content.get("alt_text") or content_type
        url = str(content.get("media_url") or "")
        body = f"[客户附件 / Customer attached {content_type}: {alt}]"
        if url:
            body += f"\nURL: {url}"
    else:
        body = f"[unsupported message content type: {content_type}]"
    return {
        "content_type": content_type,
        "body": body.strip(),
        "form_data": form_data,
        "image_urls": image_urls,
    }


def conversation_has_pending_form(events: list[dict[str, Any]]) -> bool:
    """Return whether the latest blocking Answer Bot form is unanswered."""
    pending = False
    for event in events:
        content = event.get("content") or {}
        content_type = content.get("type")
        if (
            content_type == "form"
            and content.get("block_chat_input") is True
            and content.get("submitted") is False
        ):
            pending = True
        elif content_type == "formResponse":
            pending = False
    return pending


def _messaging_customer_messages(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        author = event.get("author") or {}
        if author.get("type") != "end-user":
            continue
        content = event.get("content") or {}
        normalized = normalize_messaging_content(content)
        content_type = normalized["content_type"]
        body = normalized["body"]
        image_urls = list(normalized["image_urls"])
        image_urls.extend(collect_image_urls_from_attachments(event.get("attachments")))
        body = body.strip()
        if not body and image_urls:
            body = "[客户附图 / Customer attached image]"
        if not body:
            continue
        out.append({
            "id": event.get("id"),
            "author_id": author.get("zen:support:user_id"),
            "created_at": event.get("created_at"),
            "body": body,
            "public": True,
            "image_urls": image_urls,
            "content_type": content_type,
            "form_data": normalized["form_data"],
        })
    return out
