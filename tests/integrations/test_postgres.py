"""Test postgres integration: read-only validation."""
import pytest
from zd_agent.integrations.postgres import (
    validate_read_only_sql,
    PsqlSafetyError,
)


def test_validate_select_ok():
    validate_read_only_sql("SELECT * FROM tenants WHERE id = '123'")


def test_validate_select_with_cte_ok():
    validate_read_only_sql("""
        WITH t AS (SELECT id FROM tenants LIMIT 10)
        SELECT * FROM t
    """)


def test_validate_rejects_insert():
    with pytest.raises(PsqlSafetyError, match="INSERT"):
        validate_read_only_sql("INSERT INTO tenants (name) VALUES ('x')")


def test_validate_rejects_update():
    with pytest.raises(PsqlSafetyError, match="UPDATE"):
        validate_read_only_sql("UPDATE tenants SET name = 'x' WHERE id = 1")


def test_validate_rejects_delete():
    with pytest.raises(PsqlSafetyError, match="DELETE"):
        validate_read_only_sql("DELETE FROM tenants WHERE id = 1")


def test_validate_rejects_drop():
    with pytest.raises(PsqlSafetyError, match="DROP"):
        validate_read_only_sql("DROP TABLE tenants")


def test_validate_rejects_truncate():
    with pytest.raises(PsqlSafetyError, match="TRUNCATE"):
        validate_read_only_sql("TRUNCATE TABLE tenants")


def test_validate_rejects_alter():
    with pytest.raises(PsqlSafetyError, match="ALTER"):
        validate_read_only_sql("ALTER TABLE tenants ADD COLUMN x INT")


def test_validate_rejects_multi_statement_injection():
    with pytest.raises(PsqlSafetyError, match="multiple statements"):
        validate_read_only_sql("SELECT * FROM tenants; DROP TABLE tenants;")


def test_validate_rejects_copy_from_program():
    with pytest.raises(PsqlSafetyError):
        validate_read_only_sql("COPY tenants FROM PROGRAM 'rm -rf /'")


def test_validate_case_insensitive():
    with pytest.raises(PsqlSafetyError):
        validate_read_only_sql("delete FROM tenants")
