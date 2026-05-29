"""结构化日志配置"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import structlog

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _make_jsonl_sink(log_dir: str):
    """返回一个 structlog processor：把 event_dict 以 JSON 追加到
    <log_dir>/events-<UTC date>.jsonl，然后**原样返回 event_dict**（不消费，
    让后续 ConsoleRenderer 继续渲染 stdout）。写文件失败时静默降级。
    """
    def _sink(logger: Any, method_name: str, event_dict: dict) -> dict:
        try:
            os.makedirs(log_dir, exist_ok=True)
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = os.path.join(log_dir, f"events-{date}.jsonl")
            line = json.dumps(event_dict, ensure_ascii=False, default=str)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            # 日志 sink 绝不能拖垮主流程：写失败就只剩 stdout
            pass
        return event_dict

    return _sink


def setup_logging(*, json_output: bool = False, log_level: str = "INFO") -> None:
    """初始化 structlog

    log_level: DEBUG / INFO / WARNING / ERROR（大小写不敏感）
    """
    level = _LEVEL_MAP.get(log_level.upper(), logging.INFO)

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**kwargs: object) -> structlog.stdlib.BoundLogger:
    """获取带上下文的 logger"""
    return structlog.get_logger(**kwargs)
