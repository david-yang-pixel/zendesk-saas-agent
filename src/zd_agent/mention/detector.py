"""MentionDetector: 检测 Zendesk 评论是否包含 @mention 触发词"""

from __future__ import annotations

import re
from functools import cache

from zd_agent.log import get_logger

logger = get_logger(module="mention.detector")

# Zendesk may serialize ``via.channel`` as either a readable type or an integer
# via id. Keep only the ids whose human-vs-automation classification matters to
# the mention guard; unknown ids use the compatibility path below.
_VIA_ID_CHANNELS = {
    0: "web",
    4: "email",
    5: "web_service",
    8: "rule",
    15: "mobile",
    17: "web",
    20: "web",
    27: "web",
    29: "chat",
    31: "api",
    36: "api",
    39: "api",
    40: "api",
    44: "api",
    45: "api",
    46: "api",
    48: "web",
    49: "mobile",
    56: "mobile",
    62: "rule",
    66: "api",
    75: "messaging",
    87: "api",
}


def _normalize_via_channel(raw_channel: object) -> str:
    if isinstance(raw_channel, bool):
        return ""
    if isinstance(raw_channel, int):
        return _VIA_ID_CHANNELS.get(raw_channel, str(raw_channel))
    channel = str(raw_channel or "").strip().lower()
    if channel.isdecimal():
        return _VIA_ID_CHANNELS.get(int(channel), channel)
    return channel


def configured_mention_triggers(zendesk_cfg: object) -> list[str]:
    """Return primary mention trigger plus aliases, preserving order."""
    raw_aliases = getattr(zendesk_cfg, "mention_aliases", [])
    if isinstance(raw_aliases, str):
        aliases = [raw_aliases]
    elif isinstance(raw_aliases, (list, tuple, set)):
        aliases = list(raw_aliases)
    else:
        aliases = []

    triggers: list[str] = []
    seen: set[str] = set()
    for raw in [getattr(zendesk_cfg, "mention_trigger", ""), *aliases]:
        trigger = str(raw).strip()
        if not trigger:
            continue
        key = trigger.lower()
        if key in seen:
            continue
        seen.add(key)
        triggers.append(trigger)
    return triggers


def is_any_mention(comment: dict, triggers: list[str]) -> str | None:
    """Return the matched trigger, or None when no configured trigger matches."""
    for trigger in triggers:
        if is_mention(comment, trigger):
            return trigger
    return None


def _contains_raw_mention(body: str, trigger: str) -> bool:
    pattern = rf"(?<![\w@.-]){re.escape(trigger)}(?![\w-])"
    return re.search(pattern, body, flags=re.IGNORECASE) is not None


@cache
def get_self_user_id() -> int | None:
    """获取 agent 自身的 Zendesk user_id（来自配置，首次调用时缓存）。"""
    try:
        from zd_agent.config import get_config

        configured_id = int(get_config().zendesk.agent_user_id or 0)
        self_user_id = configured_id or None
        if self_user_id is None:
            logger.warning("self_user_id_unconfigured")
        else:
            logger.info("self_user_id_resolved", user_id=self_user_id)
        return self_user_id
    except Exception as e:
        logger.warning("self_user_id_failed", error=str(e))
        return None


def is_mention(comment: dict, trigger: str) -> bool:
    """检测评论是否触发 @mention。

    只处理 is_public=False（internal note）的评论，大小写不敏感。

    防自触发策略（人 + agent 共用 agent@example.com 账号场景）：
      - 不能简单按 ``author_id == self_id`` 过滤，否则人在 UI 写 @saas-agent
        也会被当成 agent 自己写的而忽略
      - 改用 ``via.channel`` 区分：人在 UI 写 → ``web``；agent 通过 API
        调 reply_ticket → ``api`` / ``web_service`` / ``rule``
      - 只过滤 ``via.channel`` 表明是 API 渠道写入的评论；UI 渠道的评论
        即使 author_id 与 agent 相同，也认定为人类操作，应触发

    支持两种 mention 格式：
      - 原始文字：``@saas-agent``
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

    body = str(comment.get("body", ""))
    username = trigger.lstrip("@")
    contains_mention = (
        _contains_raw_mention(body, trigger)
        or f"**{username.lower()}**" in body.lower()
    )
    if not contains_mention:
        return False

    # 防自触发：agent 用的是 agent@example.com 本人账号，所以单靠 author_id ==
    # self_id 会误伤"yonggang 自己在 UI 写 @saas-agent"的合理触发场景。
    # 改用 via.channel + author_id 联合判断：
    #   - human channels (web / mobile / chat / email) → 是人在操作，触发
    #   - API channels (api / web_service / rule / system / trigger) + author_id 匹配
    #     → agent 自己写的，跳过
    #   - 无法解析 self_id 时，明确的非人工渠道必须 fail-closed；否则 Agent
    #     自动写出的失败备注会再次唤醒自身（#3501）。
    via = comment.get("via") or {}
    channel = _normalize_via_channel(via.get("channel"))
    human_channels = {
        "web",
        "web_form",
        "mail",
        "mobile",
        "mobile_sdk",
        "iphone",
        "chat",
        "email",
        "voice",
        "messaging",
        "native_messaging",
    }
    non_human_channels = {
        "api",
        "web_service",
        "rule",
        "rule_revision",
        "system",
        "trigger",
        "ticket_sharing",
        "answer_bot_api",
        "sunshine_conversations_api",
    }

    if channel in non_human_channels:
        # 明确的非人工渠道必须能证明作者不是自己；配置或作者缺失时
        # fail-closed，避免 Agent 生成的 mention 形成自触发循环。
        author_id = comment.get("author_id")
        if author_id is None:
            logger.debug(
                "skip_non_human_mention_without_author",
                channel=channel,
            )
            return False
        self_id = get_self_user_id()
        if self_id is None:
            logger.debug(
                "skip_non_human_mention_without_self_user_id",
                author_id=author_id,
                channel=channel,
            )
            return False
        if author_id == self_id:
            logger.debug(
                "skip_self_comment",
                author_id=author_id,
                channel=channel,
            )
            return False
    elif channel not in human_channels:
        # 兼容缺少 via.channel 的旧评论格式：只有在 self_id 可解析且作者
        # 明确匹配时才判定为自写。明确的 API 渠道不走此兼容分支。
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

    return True
