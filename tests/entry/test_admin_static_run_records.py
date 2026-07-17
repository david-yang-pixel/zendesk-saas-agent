from importlib import resources


def test_run_records_initial_request_is_quick_without_detail() -> None:
    app_js = (
        resources.files("zd_agent.admin_static")
        .joinpath("app.js")
        .read_text(encoding="utf-8")
    )
    initial_load = app_js[
        app_js.index("async function renderRunRecords"):
        app_js.index("function refreshRunRecordsInBackground")
    ]
    background_refresh = app_js[
        app_js.index("function refreshRunRecordsInBackground"):
        app_js.index("function runQueryKey")
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
        app_js.index("function runStatus"):
        app_js.index("function renderStatusBadge")
    ]
    summary = app_js[
        app_js.index("function buildRunSummary"):
        app_js.index("function renderRunRecordRow")
    ]

    assert 'text.includes("deferred")' in run_status
    assert 'return "deferred"' in run_status
    assert 'deferred: "已延期"' in app_js
    assert 'record.status === "deferred"' in summary
    assert "totalRuns - failed - timeout - running - deferred" in summary
    assert "summary.failed || summary.timeout || summary.deferred" in app_js
