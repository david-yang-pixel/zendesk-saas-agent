-- All current writers and workers are stopped. No mutations are needed to
-- decide whether the previous release can consume this queue safely.
BEGIN READ ONLY;
SET LOCAL casework.deploy_legacy_queue TO :'legacy_queue';
DO $guard$
DECLARE
    schema_revision text;
    bad_queue boolean;
    unresolved boolean;
    legacy_queue boolean := current_setting('casework.deploy_legacy_queue')::boolean;
BEGIN
    SELECT version_num INTO STRICT schema_revision FROM public.alembic_version;
    IF schema_revision NOT IN (
        '0001_casework_persistence', '0002_positive_generation', '0003_config_revisions', '0004_job_session_id',
        '0005_run_telemetry', '0006_expected_event_id', '0007_queued_successor', '0008_run_log_exports',
        '0009_run_log_tool_exports', '0010_internal_draft_deliveries', '0011_human_handoffs',
        '0012_message_coverage', '0013_endpoint_cooldown', '0014_public_delivery', '0015_handoff_resume'
    ) THEN
        RAISE EXCEPTION 'Rollback blocked: unrecognized schema revision %', schema_revision;
    END IF;
    IF legacy_queue AND EXISTS (
        SELECT 1 FROM public.case_jobs WHERE status = 'queued' GROUP BY ticket_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Rollback blocked: multiple queued jobs for one ticket';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid = 'public.case_jobs'::regclass
          AND attname = 'is_operator_mention' AND NOT attisdropped
    ) THEN
        SELECT EXISTS (SELECT 1 FROM public.case_jobs WHERE status = 'queued' AND is_operator_mention)
        INTO bad_queue;
        IF legacy_queue AND bad_queue THEN
            RAISE EXCEPTION 'Rollback blocked: queued operator mentions need the newer worker';
        END IF;
    ELSIF schema_revision IN ('0012_message_coverage', '0013_endpoint_cooldown', '0014_public_delivery', '0015_handoff_resume') THEN
        RAISE EXCEPTION 'Rollback blocked: the expected queue schema is missing';
    END IF;

    IF to_regclass('public.public_deliveries') IS NOT NULL THEN
        -- A reservation with no dispatch permit is safe after all workers stop.
        SELECT EXISTS (
            SELECT 1 FROM public.public_deliveries
            WHERE status IS NULL OR status NOT IN ('reserved', 'sent', 'abandoned', 'staff_handled')
               OR (status IN ('reserved', 'abandoned') AND started_at IS NOT NULL)
               OR (status = 'sent' AND (started_at IS NULL OR confirmed_at IS NULL OR receipt IS NULL))
               OR (status = 'staff_handled' AND (
                   started_at IS NULL OR receipt->>'source' IS DISTINCT FROM 'staff_attestation'
                   OR COALESCE(receipt->>'message_id', '') = ''
               ))
        ) INTO unresolved;
        IF unresolved THEN
            RAISE EXCEPTION 'Rollback blocked: unresolved public deliveries require reconciliation by the newer release';
        END IF;
    ELSIF schema_revision IN ('0014_public_delivery', '0015_handoff_resume') THEN
        RAISE EXCEPTION 'Rollback blocked: could not verify the public delivery journal at revision %', schema_revision;
    END IF;
END;
$guard$;
COMMIT;
