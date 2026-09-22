# Internal note mention webhook rollout

Endpoint: `https://zendesk.smlershou.top/webhook/zendesk-ticket-events`

Independent webhook: `Zendesk SaaS Agent - Internal Note Mentions`.
Created inactive: `01M34YMV1EC6461DBJHE0N8KCA`.
POST / JSON; subscription: `zen:event-type:ticket.comment_added`.
Official payload: https://developer.zendesk.com/api-reference/webhooks/event-types/ticket-events/

## Configuration

- Store this webhook's own signing secret in GitHub Actions Secret
  `ZENDESK_TICKET_EVENTS_WEBHOOK_SECRET`, never the Messaging secret.
- Alternatively, configure it in the deployment source env on the server.
  Precedence: nonempty deployment environment, source env, saved runtime env.
- `prepare_runtime.py` writes the private `runtime.env` (0600); Compose injects
  the secret into ingress only. It is not baked into an image.
- Keep `MENTION_POLLER_ENABLED=true` (default). Set the GitHub Actions variable
  of that name to `false` only after real acceptance. It is injected into pi-agent.
- Without the new secret, existing endpoints and health remain available;
  the new endpoint rejects all signatures with 401.

## Deployment and acceptance

1. Deploy the new images through the existing workflow. Verify ingress and
   pi-agent readiness. The workflow does not automatically change Nginx.
2. Apply `deploy/nginx_switch.py` to the production Nginx config with a fresh
   explicit backup path, run `nginx -t`, then reload Nginx. If validation fails,
   restore that backup before reloading. The renderer upgrades the managed
   Greenfield route block while preserving unrelated locations. Do not run the
   legacy cutover script merely to add this route.
3. Confirm existing Messaging traffic still works. Enable only the new webhook.
4. Use an explicitly designated test ticket, with delivery disabled/isolated
   for acceptance, to add an employee internal note containing `@agent`.
   No real customer replies or ticket metadata mutations during testing.
5. Check the top-level event ID in `events` and `case_jobs`: one event and one
   admission, with `expected_event_id=mention:<comment_id>`. Replaying the same
   signed event must return duplicate. Public/customer comments, ordinary notes
   and other event types must return ignore without admission.
6. Check signature failures and logs without dumping request bodies, event
   payloads or signing secrets. Raw Zendesk payloads are not retained; the
   operator instruction is retained in the event's DB payload for the worker,
   but is not emitted in ingress/worker log messages.
7. After acceptance, set `MENTION_POLLER_ENABLED=false` and redeploy. Confirm
   `Mention Poller Disabled` at startup and no periodic scan calls. Observe
   real-time delivery before removing Poller code in a subsequent change.

No cross-channel Poller/Webhook deduplication is implemented, by request.
Webhook retries alone deduplicate using the top-level event ID. Do not treat
an overlap-period Poller run as proof of webhook acceptance.

HTTP outcomes: 202 enqueue/duplicate/ignore; 400 bad_payload; 401 bad_signature;
503 admission_unavailable (retry after restoring PostgreSQL/configuration).
No Zendesk/OpenConnector lookup or model call occurs inside this endpoint.
