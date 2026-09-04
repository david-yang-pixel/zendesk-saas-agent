from contextlib import asynccontextmanager

from starlette.testclient import TestClient

from casework_admin.api_v2 import register_modern_routes
from casework_admin.app import create_app


@asynccontextmanager
async def dummy_scope():
    yield None


def test_modern_skills_route():
    app = create_app()
    register_modern_routes(app, dummy_scope)
    client = TestClient(app)

    res = client.get("/api/skills")
    assert res.status_code == 200
    skills = res.json()
    assert isinstance(skills, list)
    assert any(s["name"] == "core-casework" for s in skills)


def test_modern_model_settings_route():
    app = create_app()
    register_modern_routes(app, dummy_scope)
    client = TestClient(app)

    res = client.get("/api/settings/model")
    assert res.status_code == 200
    data = res.json()
    assert "provider" in data
    assert "model" in data
    assert "base_url" in data
