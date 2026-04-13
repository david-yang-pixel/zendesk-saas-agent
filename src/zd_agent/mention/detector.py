"""MentionDetector: 检测 Zendesk 评论是否包含 @mention 触发词"""

from __future__ import annotations

from zd_agent.log import get_logger

logger = get_logger(module="mention.detector")

_self_user_id: int | None = None


def get_self_user_id() -> int | None:
    """获取 agent 自身的 Zendesk user_id（首次调用时查询并缓存）。"""
    global _self_user_id
    if _self_user_id is not None:
        return _self_user_id
    try:
        from zd_agent.tools.zendesk import _get_client
        client = _get_client()
        resp = client.get_current_user()
        _self_user_id = resp.get("user", {}).get("id")
        logger.info("self_user_id_resolved", user_id=_self_user_id)
        return _self_user_id
    except Exception as e:
        logger.warning("self_user_id_failed", error=str(e))
        return None


def is_mention(comment: dict, trigger: str) -> bool:
    """检测评论是否触发 @mention。

    只处理 is_public=False（internal note）的评论，大小写不敏感。
    agent 自己发的评论（author_id 匹配）会被跳过，防止自触发。
    支持两种格式：
      - 原始文字：@support-agent
      - Zendesk API 返回的 markdown 格式：**support-agent**
    """
    if comment.get("is_public", True):
        return False

    # 跳过 agent 自己发的评论
    author_id = comment.get("author_id")
    if author_id is not None:
        self_id = get_self_user_id()
        if self_id is not None and author_id == self_id:
            logger.debug("skip_self_comment", author_id=author_id)
            return False

    body = comment.get("body", "").lower()
    if trigger.lower() in body:
        return True
    # Zendesk 将 @mention 转为 **username** 格式
    username = trigger.lstrip("@")
    return f"**{username.lower()}**" in body
