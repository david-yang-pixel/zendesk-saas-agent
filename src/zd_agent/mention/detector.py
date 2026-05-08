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

    防自触发策略（人 + agent 共用 yonggang@dify.ai 账号场景）：
      - 不能简单按 ``author_id == self_id`` 过滤，否则人在 UI 写 @yonggang
        也会被当成 agent 自己写的而忽略
      - 改用 ``via.channel`` 区分：人在 UI 写 → ``web``；agent 通过 API
        调 reply_ticket → ``api`` / ``web_service`` / ``rule``
      - 只过滤 ``via.channel`` 表明是 API 渠道写入的评论；UI 渠道的评论
        即使 author_id 与 agent 相同，也认定为人类操作，应触发

    支持两种 mention 格式：
      - 原始文字：``@yonggang``
      - Zendesk API 返回的 markdown 格式：``**yonggang**``
    """
    # Zendesk Comments API 字段名是 ``public``（不是 ``is_public``）。
    # 之前用 ``is_public`` 永远拿默认 True → 所有 mention 被当成 public 而过滤掉。
    # 兼容两种命名：先看 ``public``，没有再 fallback ``is_public``。
    public_flag = comment.get("public")
    if public_flag is None:
        public_flag = comment.get("is_public", True)
    if public_flag:
        return False

    # 防自触发：agent 用的是 yonggang@dify.ai 本人账号，所以单靠 author_id ==
    # self_id 会误伤"yonggang 自己在 UI 写 @yonggang"的合理触发场景。
    # 改用 via.channel + author_id 联合判断：
    #   - human channels (web / mobile / chat / email) → 是人在操作，触发
    #   - API channels (api / web_service / rule / system / trigger) + author_id 匹配
    #     → agent 自己写的，跳过
    via = comment.get("via") or {}
    channel = (via.get("channel") or "").lower()
    human_channels = {"web", "mobile", "chat", "email", "voice", "messaging"}

    if channel not in human_channels:
        # 非人类渠道（API / rule / 缺 via 字段等）→ 走 author_id 自触发兜底
        author_id = comment.get("author_id")
        if author_id is not None:
            self_id = get_self_user_id()
            if self_id is not None and author_id == self_id:
                logger.debug(
                    "skip_self_comment",
                    author_id=author_id,
                    channel=channel,
                )
                return False

    body = comment.get("body", "").lower()
    if trigger.lower() in body:
        return True
    # Zendesk 将 @mention 转为 **username** 格式
    username = trigger.lstrip("@")
    return f"**{username.lower()}**" in body
