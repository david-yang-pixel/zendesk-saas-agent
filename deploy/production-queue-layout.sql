-- The old worker's upsert names the legacy partial-index predicate. Keep the
-- current schema and add a compatible index only for an old release.
BEGIN;
\if :legacy_queue
CREATE UNIQUE INDEX IF NOT EXISTS casework_deploy_legacy_queued_ticket
ON public.case_jobs (ticket_id) WHERE status = 'queued';
\else
SELECT to_regclass('public.casework_deploy_legacy_queued_ticket') IS NOT NULL AS has_legacy_queue \gset
\if :has_legacy_queue
-- A rolled-back ingress can queue old-style mentions after 0012 ran. Restore
-- their classification before allowing the new ingress or worker to consume them.
UPDATE public.case_jobs AS job
SET is_operator_mention = TRUE
FROM public.events AS event
WHERE job.event_id = event.event_id AND job.status = 'queued' AND NOT job.is_operator_mention
  AND (job.event_id LIKE 'mention:%' OR job.expected_event_id LIKE 'mention:%'
       OR event.payload->>'trigger' = 'operator_mention');
DROP INDEX public.casework_deploy_legacy_queued_ticket;
\endif
\endif
COMMIT;
