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
    out: dict[str, str] = {}
    for message in customer_msgs:
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
        break
    return out


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


def _messaging_customer_messages(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        author = event.get("author") or {}
        if author.get("type") != "end-user":
            continue
        content = event.get("content") or {}
        content_type = content.get("type", "")
        body = ""
        image_urls: list[str] = []
        if content_type == "text":
            body = content.get("text", "") or ""
            image_urls.extend(extract_inline_image_urls(body))
        elif content_type == "html":
            raw_body = content.get("body", "") or ""
            body = _strip_html(raw_body)
            image_urls.extend(extract_inline_image_urls(raw_body))
        elif content_type == "formResponse":
            body = content.get("text_fallback") or ""
            if not body:
                fields = content.get("fields") or []
                parts = []
                for field in fields:
                    label = field.get("label", "") or field.get("name", "")
                    value = (
                        field.get("value", "")
                        or field.get("text", "")
                        or field.get("email", "")
                    )
                    if label or value:
                        parts.append(f"{label}: {value}".strip(": "))
                body = "\n".join(parts)
            image_urls.extend(extract_inline_image_urls(body))
        elif content_type == "image":
            alt = content.get("alt_text") or "image"
            url = content.get("media_url", "")
            body = f"[客户附图 / Customer attached image: {alt}]"
            if url:
                body += f"\nURL: {url}"
                image_urls.append(url)
        elif content_type in ("file", "video"):
            alt = content.get("alt_text") or content.get("type")
            url = content.get("media_url", "")
            body = f"[客户附件 / Customer attached {content_type}: {alt}]"
            if url:
                body += f"\nURL: {url}"
        else:
            body = f"[unsupported message content type: {content_type}]"
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
        })
    return out
