from __future__ import annotations

import json

from zd_agent.services.admin_auth import AdminAuthStore
from zd_agent.services.admin_credentials import hash_password


def _bootstrap_password(store: AdminAuthStore) -> str:
    return store.bootstrap_password_path.read_text(encoding="utf-8").strip()


def test_default_admin_login_uses_random_one_time_password(tmp_path) -> None:
    store = AdminAuthStore(tmp_path / "admin_auth.json")
    bootstrap_path = tmp_path / "admin_bootstrap_password.txt"
    bootstrap_password = bootstrap_path.read_text(encoding="utf-8").strip()

    result = store.verify_password("admin", bootstrap_password)

    assert result.ok is True
    assert result.force_password_change is True
    assert store.requires_password_change("admin") is True
    assert bootstrap_password != "admin123"
    assert len(bootstrap_password) >= 20
    assert "admin123" not in (tmp_path / "admin_auth.json").read_text()


def test_existing_legacy_default_password_is_rotated_on_upgrade(tmp_path) -> None:
    path = tmp_path / "admin_auth.json"
    path.write_text(
        json.dumps({
            "users": {
                "admin": {
                    "password": hash_password("admin123"),
                    "force_password_change": True,
                },
            },
        }),
        encoding="utf-8",
    )

    upgraded_store = AdminAuthStore(path)
    bootstrap_password = upgraded_store.bootstrap_password_path.read_text().strip()

    assert upgraded_store.verify_password("admin", "admin123").ok is False
    assert upgraded_store.verify_password("admin", bootstrap_password).ok is True
    assert bootstrap_password != "admin123"


def test_explicit_legacy_default_password_is_replaced_with_random_bootstrap(
    tmp_path,
) -> None:
    store = AdminAuthStore(
        tmp_path / "admin_auth.json",
        initial_password="admin123",
    )
    bootstrap_password = _bootstrap_password(store)

    assert bootstrap_password != "admin123"
    assert len(bootstrap_password) >= 20
    assert store.verify_password("admin", "admin123").ok is False
    assert store.verify_password("admin", bootstrap_password).ok is True


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
    store = AdminAuthStore(
        tmp_path / "admin_auth.json",
        initial_password="admin123",
    )
    bootstrap_password = _bootstrap_password(store)

    changed = store.change_password(
        username="admin",
        current_password=bootstrap_password,
        new_password="new-admin-password",
    )

    assert changed is True
    assert store.verify_password("admin", "admin123").ok is False
    result = store.verify_password("admin", "new-admin-password")
    assert result.ok is True
    assert result.force_password_change is False
    assert store.requires_password_change("admin") is False


def test_password_change_revokes_every_existing_session(tmp_path) -> None:
    store = AdminAuthStore(
        tmp_path / "admin_auth.json",
        initial_password="admin123",
    )
    bootstrap_password = _bootstrap_password(store)
    first = store.create_session("admin")
    second = store.create_session("admin")

    changed = store.change_password(
        username="admin",
        current_password=bootstrap_password,
        new_password="new-admin-password",
    )

    assert changed is True
    assert store.validate_session(first) is None
    assert store.validate_session(second) is None


def test_password_change_removes_generated_bootstrap_secret(tmp_path) -> None:
    store = AdminAuthStore(tmp_path / "admin_auth.json")
    bootstrap_path = tmp_path / "admin_bootstrap_password.txt"
    bootstrap_password = bootstrap_path.read_text(encoding="utf-8").strip()

    changed = store.change_password(
        username="admin",
        current_password=bootstrap_password,
        new_password="new-admin-password",
    )

    assert changed is True
    assert bootstrap_path.exists() is False


def test_login_failures_are_rate_limited_by_ip_and_username(tmp_path) -> None:
    now = [1000.0]
    store = AdminAuthStore(
        tmp_path / "admin_auth.json",
        initial_password="admin123",
        login_failure_limit=2,
        login_failure_window_seconds=300,
        now_fn=lambda: now[0],
    )
    bootstrap_password = _bootstrap_password(store)

    first = store.verify_login(
        username="admin",
        password="wrong",
        remote_ip="203.0.113.10",
    )
    second = store.verify_login(
        username="admin",
        password="wrong",
        remote_ip="203.0.113.10",
    )
    limited = store.verify_login(
        username="admin",
        password=bootstrap_password,
        remote_ip="203.0.113.10",
    )
    other_ip = store.verify_login(
        username="admin",
        password=bootstrap_password,
        remote_ip="203.0.113.11",
    )

    assert first.rate_limited is False
    assert second.rate_limited is False
    assert limited.ok is False
    assert limited.rate_limited is True
    assert limited.retry_after_seconds == 300
    assert other_ip.ok is True


def test_login_rate_limit_expires_and_success_clears_failures(tmp_path) -> None:
    now = [1000.0]
    store = AdminAuthStore(
        tmp_path / "admin_auth.json",
        initial_password="admin123",
        login_failure_limit=1,
        login_failure_window_seconds=300,
        now_fn=lambda: now[0],
    )
    bootstrap_password = _bootstrap_password(store)

    store.verify_login(
        username="admin",
        password="wrong",
        remote_ip="203.0.113.10",
    )
    now[0] = 1301.0
    successful = store.verify_login(
        username="admin",
        password=bootstrap_password,
        remote_ip="203.0.113.10",
    )
    next_attempt = store.verify_login(
        username="admin",
        password="wrong",
        remote_ip="203.0.113.10",
    )

    assert successful.ok is True
    assert successful.rate_limited is False
    assert next_attempt.rate_limited is False


def test_login_failures_are_also_rate_limited_per_account(tmp_path) -> None:
    store = AdminAuthStore(
        tmp_path / "admin_auth.json",
        initial_password="admin123",
        login_failure_limit=2,
        login_account_failure_limit=3,
        login_failure_window_seconds=300,
        now_fn=lambda: 1000.0,
    )
    bootstrap_password = _bootstrap_password(store)

    for index in range(3):
        result = store.verify_login(
            username="admin",
            password="wrong",
            remote_ip=f"203.0.113.{index + 1}",
        )
        assert result.rate_limited is False

    limited = store.verify_login(
        username="admin",
        password=bootstrap_password,
        remote_ip="203.0.113.99",
    )

    assert limited.ok is False
    assert limited.rate_limited is True
