from importlib import resources


def test_run_records_initial_request_is_quick_without_detail() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    initial_load = app_js[
        app_js.index("async function renderRunRecords") : app_js.index(
            "function refreshRunRecordsInBackground"
        )
    ]
    background_refresh = app_js[
        app_js.index("function refreshRunRecordsInBackground") : app_js.index(
            "function runQueryKey"
        )
    ]

    assert "quick: true" in initial_load
    assert "detail: false" in initial_load
    assert "refreshRunRecordsInBackground();" in initial_load
    assert "quick: false" in background_refresh
    assert "detail: false" in background_refresh


def test_run_records_treat_deferred_as_a_distinct_non_success_status() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )

    run_status = app_js[
        app_js.index("function runStatus") : app_js.index("function renderStatusBadge")
    ]
    summary = app_js[
        app_js.index("function buildRunSummary") : app_js.index(
            "function renderRunRecordRow"
        )
    ]

    assert 'text.includes("deferred")' in run_status
    assert 'return "deferred"' in run_status
    assert 'deferred: "已延期"' in app_js
    assert 'record.status === "deferred"' in summary
    assert "totalRuns - failed - timeout - running - deferred" in summary
    assert "summary.failed || summary.timeout || summary.deferred" in app_js


def test_run_record_tool_calls_expand_to_redacted_details() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    css = (
        resources.files("zd_agent.admin_static")
        .joinpath("styles.css")
        .read_text(encoding="utf-8")
    )

    render_tool_call = app_js[
        app_js.index("function renderToolCall") : app_js.index(
            "function renderEventTimeline"
        )
    ]

    assert '<details class="tool-call-detail">' in render_tool_call
    assert '<summary class="tool-call-summary">' in render_tool_call
    assert "tool.arguments" in render_tool_call
    assert "redactLogValue" in render_tool_call
    assert "参数" in render_tool_call
    assert "结果摘要" in render_tool_call
    assert ".tool-call-detail" in css
    assert ".tool-call-summary" in css


def test_desktop_sidebar_can_be_resized_and_restored() -> None:
    html = (
        resources.files("zd_agent.admin_static")
        .joinpath("index.html")
        .read_text(encoding="utf-8")
    )
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    css = (
        resources.files("zd_agent.admin_static")
        .joinpath("shell.css")
        .read_text(encoding="utf-8")
    )

    assert 'id="sidebar-resizer"' in html
    assert 'role="separator"' in html
    assert 'aria-orientation="vertical"' in html
    assert "initializeSidebarResize" in app_js
    assert "setSidebarWidth" in app_js
    assert 'event.key === "ArrowLeft"' in app_js
    assert 'event.key === "ArrowRight"' in app_js
    assert 'addEventListener("dblclick"' in app_js
    assert "--sidebar-width" in css
    assert ".sidebar-resizer" in css


def test_agent_instructions_page_exposes_safe_layers_and_never_hidden_reasoning() -> (
    None
):
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )

    assert 'key: "Agent 指令"' in app_js
    assert "renderAgentInstructions" in app_js
    assert '"/admin/api/instructions"' in app_js
    assert "instruction-effective-preview" in app_js
    assert "模型隐藏推理不可查看" in app_js
    assert "handleInstructionDraftInput" in app_js
    assert 'id="instruction-save-button"' in app_js


def test_ticket_center_uses_business_api_and_links_to_filtered_run_logs() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    ticket_center = app_js[
        app_js.index("async function renderTicketCenter") : app_js.index(
            "function renderTicketCenterContent"
        )
    ]

    assert "api(`/admin/api/tickets?" in ticket_center
    assert "run-records" not in ticket_center
    assert "viewTicketRunLogs" in app_js
    assert "ticket_id" in app_js
