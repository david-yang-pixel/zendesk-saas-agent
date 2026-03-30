"""结构化日志配置"""

from __future__ import annotations

import logging

import structlog

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


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
