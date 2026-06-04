from pathlib import Path


def test_frontend_formats_timestamps_in_beijing_time():
    html = Path("static/index.html").read_text(encoding="utf-8")

    assert 'const DISPLAY_TIME_ZONE="Asia/Shanghai"' in html
    assert "formatToParts" in html
    assert "北京时间" in html


def test_frontend_exposes_admin_console_without_secret_storage():
    html = Path("static/index.html").read_text(encoding="utf-8")

    assert "Agent 控制" in html
    assert "模型配置" in html
    assert "健康检查" in html
    assert "审计日志" in html
    assert "api/admin/config" in html
    assert "api/admin/service/restart" in html
    assert "localStorage.setItem('OPENAI_API_KEY'" not in html


def test_frontend_raw_json_toggle_selector_matches_click_target():
    html = Path("static/index.html").read_text(encoding="utf-8")

    assert "this.classList.toggle('open')" in html
    assert ".ev .bd.open pre{display:block}" in html
    assert ".ev.open pre{display:block}" not in html
