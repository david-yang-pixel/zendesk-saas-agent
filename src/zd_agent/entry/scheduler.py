"""Scheduler：定时轮询 Zendesk 新工单 + 定时仓库 fetch

使用 APScheduler 4.x 异步调度器，两个定时任务：
1. 轮询 Zendesk 新工单 → 自动派发给 SessionManager
2. 定时 fetch 仓库（按 cron 配置）
"""

from __future__ import annotations

import asyncio
import signal

from rich.console import Console

from zd_agent.config import get_config
from zd_agent.log import get_logger

logger = get_logger(module="scheduler")
console = Console(highlight=False)

# 模块级状态（由 run_scheduler 初始化）
_seen_ticket_ids: set[int] = set()
_manager: SessionManager | None = None  # type: ignore[name-defined]  # noqa: F821
_dry_run: bool = False


async def poll_zendesk_task() -> None:
    """定时任务：轮询 Zendesk 搜索新工单（APScheduler 注册用，必须是模块级函数）"""
    from zd.client import ZendeskClient

    if _manager is None:
        return

    cfg = get_config()
    query = cfg.zendesk.poll_query

    try:
        client = ZendeskClient()
        data = await asyncio.to_thread(client.search_tickets, query)
        results = data.get("results", [])

        new_tickets = []
        for ticket in results:
            tid = ticket.get("id")
            if tid and tid not in _seen_ticket_ids:
                new_tickets.append(tid)

        if not new_tickets:
            logger.debug("poll_no_new_tickets", query=query, total_results=len(results))
            return

        logger.info(
            "poll_new_tickets_found",
            count=len(new_tickets),
            ticket_ids=new_tickets,
            query=query,
        )

        console.print(
            f"[bold]发现 {len(new_tickets)} 个新工单:[/bold] "
            + ", ".join(f"#{tid}" for tid in new_tickets)
        )

        for tid in new_tickets:
            _seen_ticket_ids.add(tid)
            await _manager.dispatch(tid, dry_run=_dry_run)

    except Exception as e:
        logger.error("poll_error", error=str(e), exc_info=True)
        console.print(f"[red]轮询出错: {e}[/red]")


async def fetch_repos_task() -> None:
    """定时任务：fetch 所有仓库（APScheduler 注册用，必须是模块级函数）"""
    from zd_agent.repos import fetch_repo

    cfg = get_config()
    logger.info("scheduled_repo_fetch_start", repo_count=len(cfg.repos.repositories))
    console.print("[dim]开始定时仓库 fetch...[/dim]")

    success = 0
    for entry in cfg.repos.repositories:
        try:
            ok, msg = await asyncio.to_thread(fetch_repo, entry)
            if ok:
                logger.info("repo_fetched", repo=entry.name, message=msg)
                success += 1
            else:
                logger.warning("repo_fetch_failed", repo=entry.name, error=msg)
        except Exception as e:
            logger.error("repo_fetch_error", repo=entry.name, error=str(e))

    total = len(cfg.repos.repositories)
    console.print(f"[dim]仓库 fetch 完成: {success}/{total} 成功[/dim]")
    logger.info("scheduled_repo_fetch_done", success=success, total=total)


async def run_scheduler(
    poll_interval: int | None = None,
    dry_run: bool = False,
) -> None:
    """启动调度器主循环

    Args:
        poll_interval: 轮询间隔（秒），None 则用 config.yaml 的值
        dry_run: 是否为 dry-run 模式（不发送回复）
    """
    global _manager, _dry_run

    from apscheduler import AsyncScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from zd_agent.session.manager import SessionManager

    cfg = get_config()
    interval = poll_interval or cfg.zendesk.poll_interval_seconds

    _manager = SessionManager()
    _dry_run = dry_run

    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("shutdown_signal_received")
        console.print("\n[yellow]收到停止信号，等待活跃 session 完成...[/yellow]")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    console.print(
        f"[bold green]Scheduler 启动[/bold green]\n"
        f"  轮询间隔: {interval}s\n"
        f"  搜索条件: [cyan]{cfg.zendesk.poll_query}[/cyan]\n"
        f"  并发上限: {_manager.max_concurrent}\n"
        f"  仓库 fetch cron: {cfg.repos.fetch_cron}\n"
        f"  Dry Run: {'是' if dry_run else '否'}\n"
    )

    async with AsyncScheduler() as scheduler:
        await scheduler.add_schedule(
            poll_zendesk_task,
            IntervalTrigger(seconds=interval),
            id="zendesk_poll",
        )

        if cfg.repos.auto_fetch:
            await scheduler.add_schedule(
                fetch_repos_task,
                CronTrigger.from_crontab(cfg.repos.fetch_cron),
                id="repo_fetch",
            )

        # 启动时立即执行一次轮询
        logger.info("initial_poll")
        console.print("[dim]执行首次轮询...[/dim]")
        await poll_zendesk_task()

        # 启动时异步执行一次仓库 fetch
        if cfg.repos.auto_fetch:
            asyncio.create_task(fetch_repos_task())

        console.print("[bold green]Scheduler 运行中，按 Ctrl+C 停止[/bold green]\n")

        # 等待停止信号
        await stop_event.wait()

    # 优雅关闭：等待所有活跃 session 完成
    if _manager.active_count > 0:
        console.print(f"[yellow]等待 {_manager.active_count} 个活跃 session 完成...[/yellow]")
        await _manager.wait_all()

    console.print("[bold green]Scheduler 已停止[/bold green]")
