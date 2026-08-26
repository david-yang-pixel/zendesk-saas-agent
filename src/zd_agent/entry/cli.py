"""CLI 入口：zd-agent 命令行工具"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Optional, ParamSpec, TypeVar

import typer
from click import get_current_context
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

_P = ParamSpec("_P")
_R = TypeVar("_R")
_RUNTIME_INITIALIZED = "zd_agent_runtime_initialized"


def _initialize_cli_runtime() -> None:
    """Load persisted configuration only for a command that will execute.

    Click invokes the root callback before rendering subcommand help. Keeping
    stateful bootstrap work out of that callback ensures commands such as
    ``zd-agent run --help`` remain read-only and work outside a production host.
    """

    context = get_current_context(silent=True)
    if context is None:
        # Direct Python calls in focused unit tests are not CLI invocations.
        return
    root = context.find_root()
    if root.meta.get(_RUNTIME_INITIALIZED):
        return

    from zd_agent.config import EnvSettings
    from zd_agent.services.app_bootstrap import apply_saved_admin_config

    env = EnvSettings()
    cfg = get_config()
    apply_saved_admin_config(cfg)
    jsonl_dir = cfg.logging.log_dir if cfg.logging.jsonl_enabled else None
    setup_logging(
        json_output=bool(root.params.get("json_log", False)),
        log_level=env.zd_log_level,
        jsonl_dir=jsonl_dir,
    )
    root.meta[_RUNTIME_INITIALIZED] = True


def _runtime_command(command: Callable[_P, _R]) -> Callable[_P, _R]:
    """Initialize runtime state immediately before a command body executes."""

    @wraps(command)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        _initialize_cli_runtime()
        return command(*args, **kwargs)

    return wrapped


@app.callback()
def main(
    json_log: bool = typer.Option(False, "--json-log", help="输出 JSON 格式日志"),
) -> None:
    """Zendesk 工单自动处理 Agent.

    Runtime initialization is deliberately deferred to executable commands so
    nested ``--help`` requests never touch production state.
    """


@app.command()
def version() -> None:
    """显示版本号"""
    console.print(f"zd-agent v{__version__}")


@app.command()
@_runtime_command
def run(
    ticket_id: int = typer.Argument(..., help="工单 ID"),
    hint: Optional[str] = typer.Option(None, "--hint", "-h", help="额外提示信息"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分析不发送回复"),
) -> None:
    """处理指定工单"""
    from zd_agent.services.cli_sessions import run_ticket_session

    console.print(f"[bold]处理工单 #{ticket_id}[/bold]")
    if hint:
        console.print(f"  提示: {hint}")
    if dry_run:
        console.print("  [yellow]Dry Run 模式：不发送回复[/yellow]")

    session = asyncio.run(
        run_ticket_session(ticket_id=ticket_id, hint=hint or "", dry_run=dry_run)
    )

    # 输出结果摘要
    status_style = {
        "completed": "bold green",
        "failed": "bold red",
        "timeout": "bold yellow",
    }.get(session.status.value, "bold")

    console.print(f"\n{'=' * 60}")
    console.print(
        f"[{status_style}]处理完成 — 状态: {session.status.value}[/{status_style}]"
    )
    console.print(f"  轮次: {session.round_count}")
    console.print(f"  Token: {session.token_usage.total_tokens}")
    console.print(f"  回复已发送: {'是' if session.reply_sent else '否'}")

    if session.analysis:
        console.print(f"\n{'=' * 60}")
        console.print("[bold cyan]Agent 分析结果:[/bold cyan]\n")
        console.print(session.analysis.strip())

    if session.reply_draft:
        console.print(f"\n{'=' * 60}")
        console.print("[bold green]回复草稿:[/bold green]\n")
        console.print(session.reply_draft.strip())
    elif session.analysis and not session.reply_sent:
        console.print(
            "\n[yellow]提示：本次 v2 casework 未确认可发送的客户回复。[/yellow]"
        )
        console.print("[yellow]请查看上方 disposition、证据与下一步记录。[/yellow]")


@app.command()
@_runtime_command
def batch(
    ticket_ids: list[int] = typer.Argument(..., help="工单 ID 列表"),
    hint: Optional[str] = typer.Option(None, "--hint", "-h", help="额外提示信息"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分析不发送回复"),
) -> None:
    """批量处理多个工单（自动并发控制）"""
    from zd_agent.services.cli_sessions import run_batch_sessions

    cfg = get_config()

    console.print(
        f"[bold]批量处理 {len(ticket_ids)} 个工单[/bold]  并发上限: {cfg.agent.max_concurrent_sessions}"
    )
    for tid in ticket_ids:
        console.print(f"  - #{tid}")
    if dry_run:
        console.print("  [yellow]Dry Run 模式：不发送回复[/yellow]")

    results = asyncio.run(
        run_batch_sessions(ticket_ids=ticket_ids, hint=hint or "", dry_run=dry_run)
    )

    # 输出汇总表
    table = Table(title="批量处理结果")
    table.add_column("Ticket", style="cyan", justify="right")
    table.add_column("Status")
    table.add_column("Rounds", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Reply", justify="center")

    for r in results:
        status_style = {
            "completed": "green",
            "failed": "red",
            "timeout": "yellow",
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
@_runtime_command
def serve(
    poll_interval: Optional[int] = typer.Option(
        None, "--interval", "-i", help="轮询间隔（秒），覆盖 config.yaml"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分析不发送回复"),
) -> None:
    """启动 SaaS Webhook 服务（接收 Zendesk webhook，自动处理工单）"""
    from zd_agent.services.app_bootstrap import create_configured_session_manager
    from zd_agent.services.saas_app import run_saas_app

    cfg = get_config()
    # Typer supplies ``OptionInfo`` when this command function is called
    # directly from Python. Only the literal boolean True enables the
    # server-wide no-write boundary.
    force_dry_run = dry_run is True
    manager = create_configured_session_manager(
        cfg,
        force_dry_run=force_dry_run,
    )
    console.print("[bold green]Starting zd-agent SaaS service...[/bold green]")
    console.print(
        f"  Webhook host: {cfg.webhook.listen_host}:{cfg.webhook.listen_port}"
    )
    console.print(f"  Zendesk path: {cfg.webhook.zendesk_path}")
    if force_dry_run:
        console.print(
            "  [yellow]Dry Run 模式：所有 webhook 仅分析并保存草稿，外部写入已禁用[/yellow]"
        )

    run_saas_app(manager)


@app.command()
@_runtime_command
def clean(
    ticket_id: Optional[int] = typer.Argument(
        None, help="指定工单 ID 的 sandbox，不指定则清理全部"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="强制清理，不提示确认"),
) -> None:
    """清理保留的 sandbox 目录"""
    from pathlib import Path

    from zd_agent.services.cli_archive import (
        find_sandbox_cleanup_targets,
        remove_sandbox_targets,
    )

    cfg = get_config()
    base = Path(cfg.sandbox.base_dir)
    targets = find_sandbox_cleanup_targets(base, ticket_id=ticket_id)

    if not targets:
        if ticket_id:
            console.print(f"[yellow]sandbox 不存在: {base / str(ticket_id)}[/yellow]")
            return
        console.print("[dim]无 sandbox 目录需要清理[/dim]")
        return

    if not force:
        console.print(f"即将清理 {len(targets)} 个 sandbox 目录:")
        for t in targets:
            console.print(f"  - {t}")
        if not typer.confirm("确认清理？"):
            return

    for t in remove_sandbox_targets(targets):
        console.print(f"  [green]已清理[/green] {t}")

    console.print(f"[bold green]共清理 {len(targets)} 个 sandbox[/bold green]")


@app.command()
@_runtime_command
def status() -> None:
    """查看 sandbox 目录中保留的 session 状态"""
    from pathlib import Path

    from zd_agent.services.cli_archive import collect_sandbox_status

    cfg = get_config()
    base = Path(cfg.sandbox.base_dir)
    rows = collect_sandbox_status(
        base,
        archive_base=Path(cfg.archive.archive_dir),
    )
    if not rows:
        console.print("[dim]无活跃或保留的 sandbox[/dim]")
        return

    table = Table(title=f"Sandbox 状态（{base}）")
    table.add_column("Ticket", style="cyan", justify="right")
    table.add_column("文件数", justify="right")
    table.add_column("附件", justify="right")
    table.add_column("Worktree", justify="center")
    table.add_column("归档", justify="center")

    for row in rows:
        table.add_row(
            f"#{row['ticket_id']}",
            str(row["file_count"]),
            str(row["attachment_count"]),
            "✓" if row["has_worktree"] else "-",
            "✓" if row["has_archive"] else "-",
        )

    console.print(table)


@app.command("reconcile-delivery")
@_runtime_command
def reconcile_delivery(
    ticket_id: int = typer.Argument(..., help="工单 ID"),
    delivery_attempt_id: str = typer.Option(
        ...,
        "--attempt-id",
        help="被隔离的精确 delivery attempt ID",
    ),
    outcome: str = typer.Option(
        ...,
        "--outcome",
        help="人工核验结果：delivered 或 unsent",
    ),
    reason: str = typer.Option(..., "--reason", help="人工核验证据摘要"),
    actor: str = typer.Option("cli", "--actor", help="执行人标识"),
) -> None:
    """人工核对 Zendesk 后解除 ambiguous public delivery 隔离。"""
    from pathlib import Path

    from zd_agent.services.manual_reconciliation import reconcile_manual_delivery

    if outcome not in {"delivered", "unsent"}:
        raise typer.BadParameter(
            "must be delivered or unsent",
            param_hint="--outcome",
        )
    cfg = get_config()
    state_dir = Path(cfg.resources.base_dir) / cfg.resources.state_dir
    result = reconcile_manual_delivery(
        state_dir=state_dir,
        ticket_id=ticket_id,
        delivery_attempt_id=delivery_attempt_id,
        outcome=outcome,
        reason=reason,
        actor=actor,
    )
    console.print(
        f"[bold green]工单 #{ticket_id} 已完成人工 delivery 对账[/bold green] "
        f"→ {result.state}",
    )


@app.command()
@_runtime_command
def log(
    ticket_id: int = typer.Argument(..., help="工单 ID"),
) -> None:
    """查看工单归档的分析记录"""
    from pathlib import Path

    from zd_agent.services.cli_archive import collect_ticket_log

    cfg = get_config()
    result = collect_ticket_log(
        ticket_id=ticket_id,
        archive_base=Path(cfg.archive.archive_dir),
        sandbox_base=Path(cfg.sandbox.base_dir),
    )

    if result.kind == "archive":
        console.print(f"[bold]工单 #{ticket_id} 归档记录[/bold]  ({result.base_dir})\n")
        for entry in result.entries:
            console.print(f"[bold cyan]── {entry['name']} ──[/bold cyan]")
            console.print(entry["content"])
            console.print()
        return

    if result.kind == "sandbox":
        console.print(
            f"[bold]工单 #{ticket_id} Sandbox 内容[/bold]  ({result.base_dir})\n"
        )
        for entry in result.entries:
            console.print(f"  {entry['path']}")
        return

    console.print(
        f"[yellow]未找到工单 #{ticket_id} 的记录（归档和 sandbox 均不存在）[/yellow]"
    )


@app.command()
@_runtime_command
def stats(
    last: str = typer.Option("7d", "--last", "-l", help="统计时间范围（如 7d, 30d）"),
) -> None:
    """查看处理统计（基于归档目录）"""
    from pathlib import Path

    from zd_agent.services.cli_archive import collect_archive_stats

    cfg = get_config()
    archive_base = Path(cfg.archive.archive_dir)

    try:
        stats = collect_archive_stats(archive_base, last=last)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    if not stats.archive_exists:
        console.print("[dim]暂无归档数据[/dim]")
        return

    console.print(
        f"[bold]过去 {last} 处理统计[/bold]  "
        f"(截止: {stats.cutoff.strftime('%Y-%m-%d %H:%M UTC')})\n"
    )

    table = Table(show_header=False, box=None)
    table.add_column("指标", style="cyan", justify="right")
    table.add_column("值", justify="left")

    table.add_row("处理工单总数", str(stats.total))
    table.add_row("成功", f"[green]{stats.completed}[/green]")
    table.add_row("失败 / 超时", f"[red]{stats.failed}[/red]")
    table.add_row("回复已发送", str(stats.reply_sent_count))
    table.add_row("总 Token", f"{stats.total_tokens:,}")
    table.add_row("总轮次", str(stats.total_rounds))
    if stats.completed > 0:
        table.add_row("平均 Token / 工单", f"{stats.total_tokens // stats.completed:,}")
        table.add_row("平均轮次 / 工单", f"{stats.total_rounds / stats.completed:.1f}")

    console.print(table)


# ── 工具管理子命令 ──────────────────────────────────────────


@tool_app.command("list")
@_runtime_command
def tool_list() -> None:
    """列出所有注册的工具"""
    from zd_agent.services.cli_tools import collect_tool_list

    result = collect_tool_list()
    table = Table(title=f"已注册工具（共 {result.total} 个）")
    table.add_column("#", style="dim", justify="right")
    table.add_column("工具名", style="cyan")
    table.add_column("说明")

    for row in result.rows:
        table.add_row(str(row["index"]), str(row["name"]), str(row["description"]))

    console.print(table)


@tool_app.command("schema")
@_runtime_command
def tool_schema(
    tool_name: str = typer.Argument(..., help="工具名"),
) -> None:
    """查看工具的 JSON Schema 定义"""
    import json

    from zd_agent.services.cli_tools import get_tool_schema

    result = get_tool_schema(tool_name)
    if not result.found:
        console.print(f"[red]未找到工具: {tool_name}[/red]")
        raise typer.Exit(1)

    console.print_json(json.dumps(result.schema, ensure_ascii=False))


# ── 仓库管理子命令 ──────────────────────────────────────────


@repos_app.command("fetch")
@_runtime_command
def repos_fetch(
    repo_names: Optional[list[str]] = typer.Argument(
        None, help="指定仓库名，不指定则 fetch 全部"
    ),
    clone: bool = typer.Option(
        True, "--clone/--no-clone", help="仓库不存在时是否自动 bare clone"
    ),
) -> None:
    """手动 fetch（或初始 clone）仓库"""
    from zd_agent.services.cli_repos import run_repo_fetch

    result = run_repo_fetch(repo_names=repo_names, clone=clone)

    if result.not_found:
        console.print(f"[red]配置中未找到仓库: {', '.join(result.not_found)}[/red]")

    if result.total == 0:
        console.print("[yellow]没有需要操作的仓库[/yellow]")
        return

    console.print(f"[bold]操作 {result.total} 个仓库[/bold]\n")

    for row in result.rows:
        if row["ok"]:
            console.print(
                f"  [green]✓[/green] [cyan]{row['name']}[/cyan]  {row['message']}"
            )
        else:
            console.print(
                f"  [red]✗[/red] [cyan]{row['name']}[/cyan]  [red]{row['message']}[/red]"
            )

    console.print(f"\n[bold]完成: {result.success_count}/{result.total} 成功[/bold]")


@repos_app.command("status")
@_runtime_command
def repos_status() -> None:
    """查看仓库状态"""
    from zd_agent.services.cli_repos import collect_repo_status

    table = Table(title="源码仓库状态")
    table.add_column("仓库", style="cyan")
    table.add_column("类型", justify="center")
    table.add_column("Tags", justify="right")
    table.add_column("Branches", justify="right")
    table.add_column("最后 Fetch", justify="right")
    table.add_column("状态", justify="center")

    for row in collect_repo_status():
        if row["cloned"]:
            table.add_row(
                str(row["name"]),
                str(row["mode"]),
                str(row["tag_count"]),
                str(row["branch_count"]),
                str(row["last_fetch"]),
                "[green]✓[/green]",
            )
        else:
            table.add_row(str(row["name"]), "-", "-", "-", "-", "[red]✗ 未克隆[/red]")

    console.print(table)


@repos_app.command("tags")
@_runtime_command
def repos_tags(
    repo_name: str = typer.Argument(..., help="仓库名"),
    limit: int = typer.Option(30, "--limit", "-n", help="显示最近 N 个 tag"),
) -> None:
    """列出仓库的版本 tag（按版本号降序）"""
    from zd_agent.services.cli_repos import collect_repo_tags

    result = collect_repo_tags(repo_name, limit=limit)
    if not result.found:
        console.print(f"[red]仓库未找到或未克隆: {repo_name}[/red]")
        raise typer.Exit(1)

    if not result.tags:
        console.print(f"[yellow]仓库 {repo_name} 没有 tag[/yellow]")
        return

    console.print(
        f"[bold]仓库 {repo_name} 的 tag（共 {result.total} 个，显示最近 {len(result.tags)} 个）[/bold]\n"
    )
    for tag in result.tags:
        console.print(f"  {tag}")
    if result.remaining > 0:
        console.print(f"\n  [dim]... 还有 {result.remaining} 个[/dim]")


if __name__ == "__main__":
    app()
