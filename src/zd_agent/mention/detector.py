"""MentionDetector: 检测 Zendesk 评论是否包含 @mention 触发词"""

from __future__ import annotations


def is_mention(comment: dict, trigger: str) -> bool:
    """检测评论是否触发 @mention。

    只处理 is_public=False（internal note）的评论，大小写不敏感。
    """
    if comment.get("is_public", True):
        return False
    body = comment.get("body", "")
    return trigger.lower() in body.lower()
