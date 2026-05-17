"""Agent reply modify-rate measurement (item 4).

测量 SE 是否真的把 agent 起草的 Suggested Reply forward 给客户：
- 拉同工单的所有 comments
- 找最后一条 author=agent_user_id + public=False 且 body 含 🤖 AI-generated 的（agent 草稿）
- 找之后第一条 author=agent_user_id + public=True 的（SE 真发的客户回复）
- 用 difflib.SequenceMatcher 算相似度

输出 ``MetricSample`` 后可以聚合统计 modify rate（agent 改动率）：
- as_is (≥0.85):   SE 几乎原文 forward
- minor (0.5-0.85): 改了一些（措辞 / 删段）
- major (<0.5):    SE 重写了
- no_reply:        SE 没发 public reply（draft 被弃用）

调用方式：
- CLI: ``python -m zd_agent.observability.modify_rate <ticket_id>``
- 程序: ``await measure_ticket_modify_rate(ticket_id, agent_user_id)``
- 批量: ``await measure_recent_modify_rate(ticket_ids, agent_user_id)``
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from zd_agent.log import get_logger
from zd_agent.tools.zendesk import _get_client

logger = get_logger(module="observability.modify_rate")

# 提取 agent 草稿正文里的 Suggested Reply 段（去掉 banner / Internal Analysis）
# 草稿格式由 html_renderer 渲染，正文 markdown 里有 "## Suggested Reply" / "## 客户回复建议" 等标题
_SUGGESTED_REPLY_HEADERS = [
    "Suggested Reply", "客户回复建议",
    "Customer Reply", "Reply Suggestion",
    "建议客户回复",
]

_AGENT_DRAFT_MARKER = "🤖 AI-generated reply"


@dataclass
class ModifyRateSample:
    """单工单 modify-rate 测量结果。"""
    ticket_id: int
    classification: str  # "as_is" | "minor" | "major" | "no_reply" | "no_draft"
    similarity: float | None  # 0.0-1.0，no_reply/no_draft 时 None
    agent_draft_chars: int = 0
    se_reply_chars: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "classification": self.classification,
            "similarity": (
                round(self.similarity, 3) if self.similarity is not None else None
            ),
            "agent_draft_chars": self.agent_draft_chars,
            "se_reply_chars": self.se_reply_chars,
        }


def _classify_similarity(sim: float) -> str:
    if sim >= 0.85:
        return "as_is"
    if sim >= 0.5:
        return "minor"
    return "major"


def _extract_suggested_reply(body: str) -> str:
    """从 agent 草稿 markdown 里抽 Suggested Reply 段正文（不含标题）。

    草稿格式：``## 💬 Suggested Reply\n\n<reply>\n\n## 🔍 Internal Analysis\n``
    用宽松的 regex 找到 reply 段并截掉下一个 ## 之前的内容。
    """
    for header in _SUGGESTED_REPLY_HEADERS:
        # 匹配 ## [emoji ]Header（emoji 可选）
        pattern = re.compile(
            r"##\s+(?:[^\w\n]+\s+)?" + re.escape(header) + r"\s*\n+(.*?)(?=\n##\s|\Z)",
            re.DOTALL,
        )
        m = pattern.search(body)
        if m:
            return m.group(1).strip()
    return ""


def _strip_for_compare(text: str) -> str:
    """规范化文本，便于 diff：去 markdown 标记 + 空白合并。"""
    # 移除常见 markdown
    t = re.sub(r"\*+|_+|`+", "", text)
    # 合并空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


async def measure_ticket_modify_rate(
    ticket_id: int,
    agent_user_id: int,
) -> ModifyRateSample:
    """对单个工单计算 modify rate。

    返回 ModifyRateSample；任何步骤失败也会返回 sample（classification=no_draft），
    上层可以聚合不需要捕获异常。
    """
    try:
        client = _get_client()
        comments_resp = await asyncio.to_thread(
            client.get_ticket_comments, ticket_id,
        )
    except Exception as e:
        logger.warning(
            "modify_rate_fetch_failed", ticket_id=ticket_id, error=str(e)[:200],
        )
        return ModifyRateSample(ticket_id=ticket_id, classification="no_draft",
                                similarity=None)

    comments = (comments_resp or {}).get("comments") or comments_resp or []
    if not isinstance(comments, list):
        return ModifyRateSample(ticket_id=ticket_id, classification="no_draft",
                                similarity=None)

    # 找 agent 草稿（最早的 internal note + 含 🤖 marker）
    draft_idx = -1
    draft_body = ""
    for i, c in enumerate(comments):
        author = c.get("author_id")
        public = c.get("public")
        body = c.get("body") or ""
        if (
            not public
            and author == agent_user_id
            and _AGENT_DRAFT_MARKER in body
        ):
            draft_idx = i
            draft_body = body
            break

    if draft_idx == -1:
        return ModifyRateSample(ticket_id=ticket_id, classification="no_draft",
                                similarity=None)

    # 在草稿之后找第一条 SE public reply（author = agent_user_id 因为 yonggang
    # 自己发的 public reply 也是 agent_user_id；区别在 public=True）
    se_reply = ""
    for c in comments[draft_idx + 1:]:
        if c.get("public") and c.get("author_id") == agent_user_id:
            se_reply = c.get("body") or ""
            break

    suggested = _extract_suggested_reply(draft_body)
    if not suggested:
        return ModifyRateSample(ticket_id=ticket_id, classification="no_draft",
                                similarity=None,
                                agent_draft_chars=len(draft_body))

    if not se_reply:
        return ModifyRateSample(ticket_id=ticket_id, classification="no_reply",
                                similarity=None,
                                agent_draft_chars=len(suggested))

    # 计算相似度
    a = _strip_for_compare(suggested)
    b = _strip_for_compare(se_reply)
    sim = SequenceMatcher(None, a, b).ratio()
    cls = _classify_similarity(sim)

    return ModifyRateSample(
        ticket_id=ticket_id,
        classification=cls,
        similarity=sim,
        agent_draft_chars=len(suggested),
        se_reply_chars=len(se_reply),
    )


async def measure_recent_modify_rate(
    ticket_ids: list[int],
    agent_user_id: int,
    max_concurrent: int = 5,
) -> list[ModifyRateSample]:
    """批量测量。最大并发 5（避免 Zendesk rate limit）。"""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _one(tid: int) -> ModifyRateSample:
        async with semaphore:
            return await measure_ticket_modify_rate(tid, agent_user_id)

    return await asyncio.gather(*[_one(t) for t in ticket_ids])


def summarize_samples(samples: list[ModifyRateSample]) -> dict[str, Any]:
    """聚合统计：各 classification 占比 + avg similarity。"""
    total = len(samples)
    if total == 0:
        return {"total": 0}
    by_cls: dict[str, int] = {}
    sim_values: list[float] = []
    for s in samples:
        by_cls[s.classification] = by_cls.get(s.classification, 0) + 1
        if s.similarity is not None:
            sim_values.append(s.similarity)
    avg_sim = sum(sim_values) / len(sim_values) if sim_values else None
    return {
        "total": total,
        "by_classification": by_cls,
        "avg_similarity": round(avg_sim, 3) if avg_sim is not None else None,
        "as_is_rate": round(by_cls.get("as_is", 0) / total, 3),
        "no_reply_rate": round(by_cls.get("no_reply", 0) / total, 3),
    }
