from __future__ import annotations

from zd_agent.services.admin_auth import AdminAuthStore


def test_default_admin_login_requires_password_change(tmp_path) -> None:
    store = AdminAuthStore(tmp_path / "admin_auth.json")

    result = store.verify_password("admin", "admin123")

    assert result.ok is True
    assert result.force_password_change is True
    assert store.requires_password_change("admin") is True
    assert "admin123" not in (tmp_path / "admin_auth.json").read_text()


def test_admin_login_rejects_wrong_password(tmp_path) -> None:
    store = AdminAuthStore(tmp_path / "admin_auth.json")

    result = store.verify_password("admin", "wrong")

    assert result.ok is False
    assert result.force_password_change is False


def test_sessions_can_be_created_validated_and_revoked(tmp_path) -> None:
    store = AdminAuthStore(tmp_path / "admin_auth.json")

    token = store.create_session("admin")

    assert store.validate_session(token) == "admin"
    store.revoke_session(token)
    assert store.validate_session(token) is None


def test_password_change_clears_force_change_flag(tmp_path) -> None:
    store = AdminAuthStore(tmp_path / "admin_auth.json")

    changed = store.change_password(
        username="admin",
        current_password="admin123",
        new_password="new-admin-password",
    )

    assert changed is True
    assert store.verify_password("admin", "admin123").ok is False
    result = store.verify_password("admin", "new-admin-password")
    assert result.ok is True
    assert result.force_password_change is False
    assert store.requires_password_change("admin") is False
