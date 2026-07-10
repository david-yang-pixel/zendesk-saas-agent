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
