from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS_PROMPT = ROOT / "src/zd_agent/prompts/TOOLS.md"
PLATFORM_PROMPT = ROOT / "src/zd_agent/prompts/strategies/platform_issue.md"


def test_signoz_prompt_declares_remote_mcp_as_observability_path() -> None:
    text = TOOLS_PROMPT.read_text(encoding="utf-8")

    assert "远程 SigNoz MCP 是统一观测查询入口" in text
    assert "不只是查日志" in text
    assert "logs / traces / metrics / services / schema / ClickHouse SQL" in text
    assert "不要使用本地 `sz` CLI" in text
    assert "不要使用 SigNoz Web UI" in text
    assert "MCP 不可用" in text
    assert "停止该监控查询路径" in text


def test_signoz_prompt_mentions_schema_and_describe_escape_hatch() -> None:
    text = TOOLS_PROMPT.read_text(encoding="utf-8")

    assert "signoz_errors" in text
    assert "signoz_traceback" in text
    assert "signoz_services" in text
    assert "signoz_query" in text
    assert "schema" in text
    assert "DESCRIBE signoz_logs.distributed_logs_v2" in text
    assert "DESCRIBE signoz_traces" in text
    assert "DESCRIBE signoz_metrics" in text
    assert "字段不确定时先查 schema" in text
    assert "不要靠记忆猜字段" in text


def test_platform_issue_prompt_avoids_zero_rows_false_negative() -> None:
    text = PLATFORM_PROMPT.read_text(encoding="utf-8")

    assert "0 行不是无异常" in text
    assert "先用 `signoz_services`" in text
    assert "再用 `signoz_query` 聚合" in text
    assert "必要时用 `signoz_query` 跑 `DESCRIBE" in text
    assert "不要默认加 `--env production`" in text
    assert "只有确认字段存在且该窗口有环境标签时才加环境过滤" in text
