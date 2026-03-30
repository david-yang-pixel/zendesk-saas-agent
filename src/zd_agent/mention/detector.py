"""MentionDetector: 检测 Zendesk 评论是否包含 @mention 触发词"""

from __future__ import annotations


def is_mention(comment: dict, trigger: str) -> bool:
    """检测评论是否触发 @mention。

    只处理 is_public=False（internal note）的评论，大小写不敏感。
    支持两种格式：
      - 原始文字：@support-agent
      - Zendesk API 返回的 markdown 格式：**support-agent**
    """
    if comment.get("is_public", True):
        return False
    body = comment.get("body", "").lower()
    if trigger.lower() in body:
        return True
    # Zendesk 将 @mention 转为 **username** 格式
    username = trigger.lstrip("@")
    return f"**{username.lower()}**" in body
