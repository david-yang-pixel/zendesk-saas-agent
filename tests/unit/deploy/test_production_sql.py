"""Run the deployment SQL against an isolated Docker PostgreSQL when opted in."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
POSTGRES = "postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"

pytestmark = pytest.mark.skipif(
    os.environ.get("CASEWORK_TEST_DEPLOY_SQL") != "1", reason="set CASEWORK_TEST_DEPLOY_SQL=1 to start isolated PostgreSQL"
)


def run(*argv: str, env: dict[str, str] | None = None, sql: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, env=env, input=sql, capture_output=True, text=True, timeout=30, check=False)


class Postgres:
    def __init__(self, name: str, port: str) -> None:
        self.name = name
        self.url = f"postgresql+asyncpg://casework:casework@{port}/casework"

    def migrate(self, revision: str) -> None:
        result = run(sys.executable, "-m", "alembic", "upgrade", revision, env={**os.environ, "DATABASE_URL": self.url})
        assert result.returncode == 0, result.stderr

    def query(self, sql: str, *options: str, error: str = "") -> str:
        result = run(
            "docker", "exec", "-i", self.name, "psql", "-XqAt", "-v", "ON_ERROR_STOP=1",
            "-U", "casework", "-d", "casework", *options, sql=sql,
        )
        if error:
            assert result.returncode != 0
            assert error in result.stderr
        else:
            assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def fence(self, enable: bool) -> None:
        self.query((DEPLOY / "production-claim-fence.sql").read_text(), "-v", f"enable_fence={str(enable).lower()}")

    def rollback_check(self, error: str = "", *, legacy: bool = True) -> None:
        self.query(
            (DEPLOY / "production-rollback-check.sql").read_text(), "-v", f"legacy_queue={str(legacy).lower()}", error=error,
        )

    def queue_layout(self, legacy: bool) -> None:
        self.query((DEPLOY / "production-queue-layout.sql").read_text(), "-v", f"legacy_queue={str(legacy).lower()}")


@pytest.fixture(scope="module")
def postgres() -> Iterator[Postgres]:
    name = f"casework-sql-test-{uuid4().hex}"
    result = run(
        "docker", "run", "--rm", "-d", "--name", name, "-p", "127.0.0.1::5432",
        "-e", "POSTGRES_USER=casework", "-e", "POSTGRES_PASSWORD=casework", "-e", "POSTGRES_DB=casework", POSTGRES,
    )
    assert result.returncode == 0, result.stderr
    try:
        for _ in range(30):
            result = run("docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "casework", "-d", "casework")
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("isolated PostgreSQL never became ready")
        result = run("docker", "port", name, "5432/tcp")
        assert result.returncode == 0, result.stderr
        yield Postgres(name, result.stdout.strip())
    finally:
        run("docker", "rm", "-fv", name)


def test_release_fence_and_rollback_checks_on_old_and_upgraded_schema(postgres: Postgres) -> None:
    postgres.migrate("0011_human_handoffs")
    postgres.query("""
        INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, payload)
        VALUES ('case', 1, now(), 'test', 'test', 'test', '{}'),
               ('mention', 1, now(), 'test', 'test', 'test', '{}'),
               ('handoff', 1, now(), 'test', 'test', 'test', '{}');
        INSERT INTO case_jobs (job_id, ticket_id, generation, event_id, expected_event_id, session_id,
            config_revision, root_model_config_id, child_model_config_ids, model_digest, skill_digest, tool_digest)
        VALUES ('case', 1, 1, 'case', 'case', 'case', 'test', 'test', '[]', 'test', 'test', 'test');
        INSERT INTO human_handoffs (handoff_id, ticket_id, acknowledge) VALUES ('handoff', 1, false);
    """)
    postgres.rollback_check()
    postgres.query("UPDATE case_jobs SET status='running', lease_expires_at=now() + interval '1 minute'")
    postgres.query("UPDATE human_handoffs SET status='processing', lease_expires_at=now() - interval '1 minute'")
    postgres.fence(True)
    postgres.query("UPDATE case_jobs SET lease_expires_at=now() - interval '1 minute'")
    postgres.query(
        "UPDATE case_jobs SET status='running', attempts=attempts + 1", error="worker claims are fenced",
    )
    postgres.query(
        "UPDATE case_jobs SET status='failed', dead_letter_reason='lease_expired_max_attempts_exceeded'",
        error="worker claims are fenced",
    )
    postgres.query("UPDATE human_handoffs SET status='processing', attempts=attempts + 1", error="claims are fenced")
    postgres.query(
        "UPDATE human_handoffs SET status='failed', last_error='lease_expired_max_attempts_exceeded'",
        error="claims are fenced",
    )
    postgres.query("UPDATE human_handoffs SET status='completed'; UPDATE case_jobs SET status='completed';")
    postgres.fence(False)
    postgres.query("UPDATE case_jobs SET status='running'; UPDATE case_jobs SET status='queued';")

    postgres.migrate("0015_handoff_resume")
    postgres.query("""
        INSERT INTO case_jobs (job_id, ticket_id, generation, event_id, expected_event_id, session_id,
            config_revision, root_model_config_id, child_model_config_ids, model_digest, skill_digest, tool_digest,
            is_operator_mention)
        VALUES ('mention', 1, 1, 'mention', 'mention', 'mention', 'test', 'test', '[]', 'test', 'test', 'test', true);
    """)
    postgres.rollback_check("multiple queued jobs for one ticket")
    postgres.rollback_check(legacy=False)
    postgres.query("UPDATE case_jobs SET status='completed' WHERE job_id='case'")
    postgres.rollback_check("queued operator mentions")
    postgres.query("UPDATE case_jobs SET status='completed' WHERE job_id='mention'")
    postgres.rollback_check()
    postgres.query("""
        INSERT INTO public_deliveries (job_id, delivery_id, ticket_id, status, messages, reply_body)
        VALUES ('case', gen_random_uuid(), 1, 'reserved', '[]', 'draft');
    """)
    postgres.rollback_check()
    for status in ("pending", "uncertain"):
        postgres.query(f"UPDATE public_deliveries SET status='{status}', started_at=now(), next_check_at=now();")
        postgres.rollback_check("unresolved public deliveries")
    postgres.query("UPDATE public_deliveries SET status='staff_handled', next_check_at=NULL;")
    postgres.rollback_check("unresolved public deliveries")
    postgres.query("""
        UPDATE public_deliveries SET receipt='{"source":"staff_attestation","message_id":"message-1"}';
    """)
    postgres.rollback_check()
    postgres.query("UPDATE public_deliveries SET status='sent', receipt='{}', confirmed_at=now(), next_check_at=NULL;")
    postgres.rollback_check()
    # PostgreSQL must find the original all-queued index predicate at plan time.
    legacy_upsert = """
        EXPLAIN INSERT INTO public.case_jobs SELECT * FROM public.case_jobs WHERE false
        ON CONFLICT (ticket_id) WHERE status = 'queued' DO UPDATE SET available_at=EXCLUDED.available_at;
    """
    postgres.query(legacy_upsert, error="no unique or exclusion constraint")
    postgres.queue_layout(legacy=True)
    assert "casework_deploy_legacy_queued_ticket" in postgres.query(legacy_upsert)
    postgres.query("""
        UPDATE case_jobs SET status='queued', expected_event_id='mention:test', is_operator_mention=false
        WHERE job_id='mention';
    """)
    postgres.queue_layout(legacy=False)
    assert postgres.query("SELECT is_operator_mention FROM case_jobs WHERE job_id='mention'") == "t"
    postgres.query(legacy_upsert, error="no unique or exclusion constraint")
    postgres.query("UPDATE case_jobs SET status='completed' WHERE job_id='mention'")
    postgres.query("ALTER TABLE public_deliveries RENAME TO unrecognized_journal")
    postgres.rollback_check("could not verify the public delivery journal")
    postgres.query("UPDATE alembic_version SET version_num='0015_unrecognized'")
    postgres.rollback_check("unrecognized schema revision")
