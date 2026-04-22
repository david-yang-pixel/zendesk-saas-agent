"""CLI 入口：zd-agent 命令行工具"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from zd_agent import __version__
from zd_agent.config import get_config
from zd_agent.log import setup_logging

app = typer.Typer(
    name="zd-agent",
    help="Zendesk 工单自动处理 Agent",
    no_args_is_help=True,
)

repos_app = typer.Typer(help="源码仓库管理")
app.add_typer(repos_app, name="repos")

tool_app = typer.Typer(help="工具管理与测试")
app.add_typer(tool_app, name="tool")

console = Console()


@app.callback()
def main(
    json_log: bool = typer.Option(False, "--json-log", help="输出 JSON 格式日志"),
) -> None:
    """Zendesk 工单自动处理 Agent"""
    from zd_agent.config import EnvSettings
    env = EnvSettings()
    setup_logging(json_output=json_log, log_level=env.zd_log_level)


@app.command()
def version() -> None:
    """显示版本号"""
    console.print(f"zd-agent v{__version__}")


@app.command()
def run(
    ticket_id: int = typer.Argument(..., help="工单 ID"),
    hint: Optional[str] = typer.Option(None, "--hint", "-h", help="额外提示信息"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分析不发送回复"),
) -> None:
    """处理指定工单"""
    from zd_agent.runtime.runner import run_agent
    from zd_agent.session.agent_session import AgentSession

    console.print(f"[bold]处理工单 #{ticket_id}[/bold]")
    if hint:
        console.print(f"  提示: {hint}")
    if dry_run:
        console.print("  [yellow]Dry Run 模式：不发送回复[/yellow]")

    session = AgentSession(ticket_id=ticket_id, hint=hint or "", dry_run=dry_run)
    asyncio.run(run_agent(session))

    # 输出结果摘要
    status_style = {
        "completed": "bold green",
        "failed": "bold red",
        "timeout": "bold yellow",
    }.get(session.status.value, "bold")

    console.print(f"\n{'=' * 60}")
    console.print(f"[{status_style}]处理完成 — 状态: {session.status.value}[/{status_style}]")
    console.print(f"  轮次: {session.round_count}")
    console.print(f"  Token: {session.token_usage.total_tokens}")
    console.print(f"  回复已发送: {'是' if session.reply_sent else '否'}")
    console.print(f"  已归档: {'是' if session.archived else '否'}")

    if session.sandbox_dir and session.sandbox_dir.exists():
        console.print(f"  Sandbox 保留: {session.sandbox_dir}")

    if session.analysis:
        console.print(f"\n{'=' * 60}")
        console.print("[bold cyan]Agent 分析结果:[/bold cyan]\n")
        console.print(session.analysis.strip())

    if session.reply_draft:
        console.print(f"\n{'=' * 60}")
        console.print("[bold green]回复草稿:[/bold green]\n")
        console.print(session.reply_draft.strip())
    elif session.analysis and not session.reply_sent:
        console.print(f"\n[yellow]提示：Agent 没有调用 zendesk_internal_reply 生成正式回复。[/yellow]")
        console.print("[yellow]上方的分析结果即为 Agent 的最终输出。[/yellow]")


@app.command()
def batch(
    ticket_ids: list[int] = typer.Argument(..., help="工单 ID 列表"),
    hint: Optional[str] = typer.Option(None, "--hint", "-h", help="额外提示信息"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分析不发送回复"),
) -> None:
    """批量处理多个工单（自动并发控制）"""
    from zd_agent.session.manager import SessionManager

    cfg = get_config()

    console.print(f"[bold]批量处理 {len(ticket_ids)} 个工单[/bold]  并发上限: {cfg.agent.max_concurrent_sessions}")
    for tid in ticket_ids:
        console.print(f"  - #{tid}")
    if dry_run:
        console.print("  [yellow]Dry Run 模式：不发送回复[/yellow]")

    async def _run_batch() -> list[dict[str, object]]:
        mgr = SessionManager()
        sessions = await mgr.dispatch_batch(
            ticket_ids, hint=hint or "", dry_run=dry_run,
        )
        await mgr.wait_all()
        results = []
        for s in sessions:
            results.append({
                "ticket_id": s.ticket_id,
                "status": s.status.value,
                "rounds": s.round_count,
                "tokens": s.token_usage.total_tokens,
                "reply_sent": s.reply_sent,
            })
        return results

    results = asyncio.run(_run_batch())

    # 输出汇总表
    table = Table(title="批量处理结果")
    table.add_column("Ticket", style="cyan", justify="right")
    table.add_column("Status")
    table.add_column("Rounds", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Reply", justify="center")

    for r in results:
        status_style = {
            "completed": "green", "failed": "red", "timeout": "yellow",
        }.get(str(r["status"]), "")
        table.add_row(
            f"#{r['ticket_id']}",
            f"[{status_style}]{r['status']}[/{status_style}]",
            str(r["rounds"]),
            f"{r['tokens']:,}",
            "✓" if r["reply_sent"] else "-",
        )
    console.print(f"\n{'=' * 60}")
    console.print(table)


@app.command()
def serve(
    poll_interval: Optional[int] = typer.Option(
        None, "--interval", "-i", help="轮询间隔（秒），覆盖 config.yaml"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分析不发送回复"),
) -> None:
    """启动 SaaS Webhook 服务（接收 Zendesk + 飞书推送，自动处理工单）"""
    from zd_agent.entry.saas_app import run_saas_app
    from zd_agent.session.manager import SessionManager
    from zd_agent.tools.registry import create_tool_registry

    cfg = get_config()
    console.print("[bold green]Starting zd-agent SaaS service...[/bold green]")
    console.print(f"  Webhook host: {cfg.webhook.listen_host}:{cfg.webhook.listen_port}")
    console.print(f"  Zendesk path: {cfg.webhook.zendesk_path}")
    console.print(f"  Feishu path: {cfg.webhook.feishu_path}")

    registry = create_tool_registry()
    manager = SessionManager(
        registry=registry,
        max_concurrent=cfg.agent.max_concurrent_sessions,
    )
    run_saas_app(manager)


@app.command()
def webhook(
    host: str = typer.Option("0.0.0.0", "--host", help="监听地址"),
    port: int = typer.Option(8080, "--port", "-p", help="监听端口"),
) -> None:
    """启动 Webhook HTTP 服务器（接收外部系统推送）"""
    from zd_agent.entry.webhook import run_webhook_server
    from zd_agent.session.manager import SessionManager

    cfg = get_config()
    console.print(f"[bold green]Webhook 服务器启动[/bold green]  {host}:{port}")
    console.print("  POST /webhook/zendesk — 接收工单推送")
    console.print("  GET  /health — 健康检查")
    console.print("  GET  /status — 处理状态")
    if cfg.repos.auto_fetch:
        console.print(f"  仓库定时 fetch: {cfg.repos.fetch_cron}")
    console.print()

    manager = SessionManager()

    async def _run() -> None:
        await run_webhook_server(host=host, port=port, manager=manager)

        # 启动仓库定时 fetch（复用 scheduler 中的 fetch 逻辑）
        if cfg.repos.auto_fetch:
            from apscheduler import AsyncScheduler
            from apscheduler.triggers.cron import CronTrigger

            from zd_agent.entry.scheduler import fetch_repos_task

            scheduler = AsyncScheduler()
            await scheduler.__aenter__()
            await scheduler.add_schedule(
                fetch_repos_task,
                CronTrigger.from_crontab(cfg.repos.fetch_cron),
                id="repo_fetch",
            )
            asyncio.create_task(fetch_repos_task())
            console.print("[dim]仓库定时 fetch 已启动[/dim]")

        stop = asyncio.Event()

        import signal
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        await stop.wait()
        console.print("\n[yellow]Webhook 服务器停止[/yellow]")

    asyncio.run(_run())


@app.command()
def clean(
    ticket_id: Optional[int] = typer.Argument(None, help="指定工单 ID 的 sandbox，不指定则清理全部"),
    force: bool = typer.Option(False, "--force", "-f", help="强制清理，不提示确认"),
) -> None:
    """清理保留的 sandbox 目录"""
    import shutil
    from pathlib import Path

    cfg = get_config()
    base = Path(cfg.sandbox.base_dir)

    if not base.exists():
        console.print("[dim]无 sandbox 目录需要清理[/dim]")
        return

    if ticket_id:
        target = base / str(ticket_id)
        if not target.exists():
            console.print(f"[yellow]sandbox 不存在: {target}[/yellow]")
            return
        targets = [target]
    else:
        targets = [p for p in base.iterdir() if p.is_dir()]

    if not targets:
        console.print("[dim]无 sandbox 目录需要清理[/dim]")
        return

    if not force:
        console.print(f"即将清理 {len(targets)} 个 sandbox 目录:")
        for t in targets:
            console.print(f"  - {t}")
        if not typer.confirm("确认清理？"):
            return

    for t in targets:
        shutil.rmtree(t, ignore_errors=True)
        console.print(f"  [green]已清理[/green] {t}")

    console.print(f"[bold green]共清理 {len(targets)} 个 sandbox[/bold green]")


@app.command()
def status() -> None:
    """查看 sandbox 目录中保留的 session 状态"""
    from pathlib import Path

    cfg = get_config()
    base = Path(cfg.sandbox.base_dir)

    if not base.exists():
        console.print("[dim]无活跃或保留的 sandbox[/dim]")
        return

    sandboxes = [p for p in base.iterdir() if p.is_dir()]
    if not sandboxes:
        console.print("[dim]无活跃或保留的 sandbox[/dim]")
        return

    table = Table(title=f"Sandbox 状态（{base}）")
    table.add_column("Ticket", style="cyan", justify="right")
    table.add_column("文件数", justify="right")
    table.add_column("附件", justify="right")
    table.add_column("Worktree", justify="center")
    table.add_column("归档", justify="center")

    for sb in sorted(sandboxes, key=lambda p: p.name):
        ticket_id = sb.name

        file_count = sum(1 for _ in sb.rglob("*") if _.is_file())

        att_dir = sb / "attachments"
        att_count = sum(1 for _ in att_dir.rglob("*") if _.is_file()) if att_dir.exists() else 0

        wt_dir = sb / "worktree"
        has_wt = "✓" if wt_dir.exists() and any(wt_dir.iterdir()) else "-"

        archive_dir = Path(cfg.knowledge.archive_dir) / ticket_id
        has_archive = "✓" if archive_dir.exists() else "-"

        table.add_row(f"#{ticket_id}", str(file_count), str(att_count), has_wt, has_archive)

    console.print(table)


@app.command()
def log(
    ticket_id: int = typer.Argument(..., help="工单 ID"),
) -> None:
    """查看工单归档的分析记录"""
    from pathlib import Path

    cfg = get_config()

    # 先查归档目录
    archive_dir = Path(cfg.knowledge.archive_dir) / str(ticket_id)
    if archive_dir.exists():
        console.print(f"[bold]工单 #{ticket_id} 归档记录[/bold]  ({archive_dir})\n")
        for f in sorted(archive_dir.iterdir()):
            if f.is_file():
                console.print(f"[bold cyan]── {f.name} ──[/bold cyan]")
                content = f.read_text(encoding="utf-8", errors="replace")
                if len(content) > 5000:
                    content = content[:5000] + "\n\n... (截断)"
                console.print(content)
                console.print()
        return

    # 再查 sandbox
    sandbox_dir = Path(cfg.sandbox.base_dir) / str(ticket_id)
    if sandbox_dir.exists():
        console.print(f"[bold]工单 #{ticket_id} Sandbox 内容[/bold]  ({sandbox_dir})\n")
        for f in sorted(sandbox_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(sandbox_dir)
                console.print(f"  {rel}")
        return

    console.print(f"[yellow]未找到工单 #{ticket_id} 的记录（归档和 sandbox 均不存在）[/yellow]")


@app.command()
def stats(
    last: str = typer.Option("7d", "--last", "-l", help="统计时间范围（如 7d, 30d）"),
) -> None:
    """查看处理统计（基于归档目录）"""
    import json
    import re
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    cfg = get_config()
    archive_base = Path(cfg.knowledge.archive_dir)

    if not archive_base.exists():
        console.print("[dim]暂无归档数据[/dim]")
        return

    # 解析时间范围
    m = re.match(r"^(\d+)([dhm])$", last)
    if not m:
        console.print("[red]时间格式错误，支持: 7d, 30d, 24h[/red]")
        return

    amount, unit = int(m.group(1)), m.group(2)
    delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}[unit]
    cutoff = datetime.now(timezone.utc) - delta

    total = 0
    completed = 0
    failed = 0
    total_tokens = 0
    total_rounds = 0
    reply_sent_count = 0

    for ticket_dir in archive_base.iterdir():
        if not ticket_dir.is_dir():
            continue

        meta_file = ticket_dir / "meta.json"
        if not meta_file.exists():
            # 没有 meta.json 就用目录修改时间
            dir_mtime = datetime.fromtimestamp(ticket_dir.stat().st_mtime, tz=timezone.utc)
            if dir_mtime < cutoff:
                continue
            total += 1
            continue

        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            total += 1
            continue

        created = meta.get("created_at", "")
        if created:
            try:
                ts = datetime.fromisoformat(created)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except ValueError:
                pass

        total += 1
        status_val = meta.get("status", "")
        if status_val == "completed":
            completed += 1
        elif status_val in ("failed", "timeout"):
            failed += 1

        total_tokens += meta.get("total_tokens", 0)
        total_rounds += meta.get("rounds", 0)
        if meta.get("reply_sent"):
            reply_sent_count += 1

    console.print(f"[bold]过去 {last} 处理统计[/bold]  (截止: {cutoff.strftime('%Y-%m-%d %H:%M UTC')})\n")

    table = Table(show_header=False, box=None)
    table.add_column("指标", style="cyan", justify="right")
    table.add_column("值", justify="left")

    table.add_row("处理工单总数", str(total))
    table.add_row("成功", f"[green]{completed}[/green]")
    table.add_row("失败 / 超时", f"[red]{failed}[/red]")
    table.add_row("回复已发送", str(reply_sent_count))
    table.add_row("总 Token", f"{total_tokens:,}")
    table.add_row("总轮次", str(total_rounds))
    if completed > 0:
        table.add_row("平均 Token / 工单", f"{total_tokens // completed:,}")
        table.add_row("平均轮次 / 工单", f"{total_rounds / completed:.1f}")

    console.print(table)


# ── 工具管理子命令 ──────────────────────────────────────────


@tool_app.command("list")
def tool_list() -> None:
    """列出所有注册的工具"""
    from zd_agent.tools.registry import create_tool_registry

    registry = create_tool_registry()
    table = Table(title=f"已注册工具（共 {len(registry)} 个）")
    table.add_column("#", style="dim", justify="right")
    table.add_column("工具名", style="cyan")
    table.add_column("说明")

    for i, tool in enumerate(registry.list_tools(), 1):
        table.add_row(str(i), tool.name, tool.description)

    console.print(table)


@tool_app.command("schema")
def tool_schema(
    tool_name: str = typer.Argument(..., help="工具名"),
) -> None:
    """查看工具的 JSON Schema 定义"""
    import json

    from zd_agent.tools.registry import create_tool_registry

    registry = create_tool_registry()
    tool = registry.get(tool_name)
    if tool is None:
        console.print(f"[red]未找到工具: {tool_name}[/red]")
        raise typer.Exit(1)

    schema = tool.to_openai_schema()
    console.print_json(json.dumps(schema, ensure_ascii=False))


# ── 仓库管理子命令 ──────────────────────────────────────────


@repos_app.command("fetch")
def repos_fetch(
    repo_names: Optional[list[str]] = typer.Argument(None, help="指定仓库名，不指定则 fetch 全部"),
    clone: bool = typer.Option(True, "--clone/--no-clone", help="仓库不存在时是否自动 bare clone"),
) -> None:
    """手动 fetch（或初始 clone）仓库"""
    from zd_agent.repos import clone_repo, fetch_repo

    cfg = get_config()
    entries = cfg.repos.repositories

    if repo_names:
        entries = [r for r in entries if r.name in repo_names]
        not_found = set(repo_names) - {r.name for r in entries}
        if not_found:
            console.print(f"[red]配置中未找到仓库: {', '.join(not_found)}[/red]")

    if not entries:
        console.print("[yellow]没有需要操作的仓库[/yellow]")
        return

    console.print(f"[bold]操作 {len(entries)} 个仓库[/bold]\n")

    success_count = 0
    for entry in entries:
        with console.status(f"  {entry.name} ..."):
            if clone:
                ok, msg = clone_repo(entry)
            else:
                ok, msg = fetch_repo(entry)

        if ok:
            console.print(f"  [green]✓[/green] [cyan]{entry.name}[/cyan]  {msg}")
            success_count += 1
        else:
            console.print(f"  [red]✗[/red] [cyan]{entry.name}[/cyan]  [red]{msg}[/red]")

    console.print(f"\n[bold]完成: {success_count}/{len(entries)} 成功[/bold]")


@repos_app.command("status")
def repos_status() -> None:
    """查看仓库状态"""
    from zd_agent.repos import get_repo_status

    cfg = get_config()
    table = Table(title="源码仓库状态")
    table.add_column("仓库", style="cyan")
    table.add_column("类型", justify="center")
    table.add_column("Tags", justify="right")
    table.add_column("Branches", justify="right")
    table.add_column("最后 Fetch", justify="right")
    table.add_column("状态", justify="center")

    for entry in cfg.repos.repositories:
        status = get_repo_status(entry)
        if status.cloned:
            mode = "bare" if status.is_bare else "normal"
            fetch_time = status.last_fetch.strftime("%m-%d %H:%M") if status.last_fetch else "-"
            table.add_row(
                status.name, mode,
                str(status.tag_count), str(status.branch_count),
                fetch_time, "[green]✓[/green]",
            )
        else:
            table.add_row(status.name, "-", "-", "-", "-", "[red]✗ 未克隆[/red]")

    console.print(table)


@repos_app.command("tags")
def repos_tags(
    repo_name: str = typer.Argument(..., help="仓库名"),
    limit: int = typer.Option(30, "--limit", "-n", help="显示最近 N 个 tag"),
) -> None:
    """列出仓库的版本 tag（按版本号降序）"""
    from zd_agent.repos import list_tags, resolve_repo

    if resolve_repo(repo_name) is None:
        console.print(f"[red]仓库未找到或未克隆: {repo_name}[/red]")
        raise typer.Exit(1)

    tags = list_tags(repo_name)
    if not tags:
        console.print(f"[yellow]仓库 {repo_name} 没有 tag[/yellow]")
        return

    total = len(tags)
    shown = tags[:limit]
    console.print(f"[bold]仓库 {repo_name} 的 tag（共 {total} 个，显示最近 {len(shown)} 个）[/bold]\n")
    for tag in shown:
        console.print(f"  {tag}")
    if total > limit:
        console.print(f"\n  [dim]... 还有 {total - limit} 个[/dim]")


if __name__ == "__main__":
    app()
