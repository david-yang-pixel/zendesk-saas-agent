-- Run under the host deployment lock. DDL takes a table lock that waits for
-- any claims already in transactions to commit before the fence is visible.
BEGIN;
\if :enable_fence
CREATE OR REPLACE FUNCTION public.casework_deploy_block_claim() RETURNS trigger
LANGUAGE plpgsql AS $fence$
BEGIN
    -- An exception rolls back the claim. The previous worker dispatches the
    -- selected job even if its claim UPDATE reports zero affected rows.
    RAISE EXCEPTION 'Greenfield worker claims are fenced for a release change'
        USING ERRCODE = '55P03';
END;
$fence$;

CREATE OR REPLACE TRIGGER casework_deploy_block_claim
BEFORE UPDATE OF status ON public.case_jobs FOR EACH ROW
WHEN (NEW.status = 'running' OR (
    OLD.status = 'running' AND NEW.status = 'failed'
    AND NEW.dead_letter_reason = 'lease_expired_max_attempts_exceeded'
)) EXECUTE FUNCTION public.casework_deploy_block_claim();

SELECT to_regclass('public.human_handoffs') IS NOT NULL AS has_handoffs \gset
\if :has_handoffs
CREATE OR REPLACE TRIGGER casework_deploy_block_claim
BEFORE UPDATE OF status ON public.human_handoffs FOR EACH ROW
WHEN (NEW.status = 'processing' OR (
    OLD.status = 'processing' AND NEW.status = 'failed'
    AND OLD.lease_expires_at <= statement_timestamp()
    AND NEW.last_error = COALESCE(OLD.last_error, 'lease_expired_max_attempts_exceeded')
)) EXECUTE FUNCTION public.casework_deploy_block_claim();
\endif
\else
DROP TRIGGER IF EXISTS casework_deploy_block_claim ON public.case_jobs;
SELECT to_regclass('public.human_handoffs') IS NOT NULL AS has_handoffs \gset
\if :has_handoffs
DROP TRIGGER IF EXISTS casework_deploy_block_claim ON public.human_handoffs;
\endif
DROP FUNCTION IF EXISTS public.casework_deploy_block_claim();
\endif
COMMIT;
