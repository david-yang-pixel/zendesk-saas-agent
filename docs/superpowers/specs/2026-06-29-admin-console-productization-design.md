# Admin Console Productization Design

## Goal

Turn the built-in `zd-agent` admin console into a production-ready operations console for one deployed agent instance. The console should let an operator inspect runs, edit supported non-runtime configuration, verify tool integrations, review audit history, and understand deployment health from one authenticated UI.

## Current State

The project already exposes `/admin` from the agent process. The backend has login, password change, config schema, effective config, config preview/apply, run records, audit logs, and deployment status endpoints. The frontend is a single `src/zd_agent/admin_static/index.html` file with inline CSS and JavaScript.

This is enough for a working internal console, but it is not yet an engineering product. The main gaps are structure, state depth, filtering, validation, operational diagnostics, and maintainability.

## Scope

This v1 productization keeps the admin console inside the existing Python/aiohttp agent service. It does not add a separate frontend build pipeline. The implementation will split the static UI into focused static assets and expand the existing JSON APIs.

Included:

- Modular static frontend files under `src/zd_agent/admin_static/`.
- A complete blue-and-white operations UI with consistent loading, empty, error, and confirmation states.
- Run record search, filtering, details, and summary metrics.
- Schema-driven configuration editing grouped by operational module.
- Config preview with required change reason before save.
- Audit logs for login, logout, password changes, config changes, and integration checks.
- Tool integration status and connection test endpoints.
- Deployment status based on current config and runtime environment.
- Stronger backend validation and secret redaction.
- Tests for backend behavior and static UI contracts.

Excluded from this v1:

- Multi-user roles beyond the single admin account.
- A separate React/Vite frontend project.
- Direct deploy, restart, or rollback buttons from the UI.
- Full Google SSO or Feishu SSO login implementation. The config fields may remain visible, but actual OAuth login flows require a separate auth design.
- Editing true runtime-only values that cannot affect a running process safely.

## Information Architecture

The left navigation keeps the current Chinese menu structure:

- `总览`: service health, today run metrics, recent failures, and recent run events.
- `运行记录`: searchable run-event log center.
- `触发配置`: Zendesk polling, webhook-related behavior, mention trigger, burst handling, and trigger decision settings.
- `回复策略`: public reply switch, ask limits, silence windows, and browser relay config.
- `模型与运行`: provider, model names, base URL, model keys, concurrency, token limits, and guardrails.
- `工具与集成`: Zendesk MCP, DB Gateway, SigNoz, GitHub MCP, Docs service, notification service, and sandbox worker.
- `存储配置`: resource directories, attachment cache location, sandbox directory, and cleanup settings.
- `部署状态`: image/deploy metadata, admin login method status, state/log paths, and pending restart state.
- `审计日志`: all admin-visible changes and operational checks.

The console should not show vague filler text such as `系统摘要`. Every card should either show real data or an explicit empty/error state.

## Backend Design

### Static Assets

`handle_admin_index` should keep serving `index.html`. The route layer should also serve admin static assets from `src/zd_agent/admin_static/` under `/admin/static/{name}`. Only known package files should be served. Paths must not allow directory traversal.

Planned files:

- `src/zd_agent/admin_static/index.html`: HTML shell only.
- `src/zd_agent/admin_static/styles.css`: shared visual system and layout.
- `src/zd_agent/admin_static/app.js`: state management, API calls, routing, and rendering.

### Run Records

`GET /admin/api/run-records` should support:

- `limit`: integer, clamped to `1..500`.
- `ticket_id`: optional exact match against `ticket_id` or `ticket`.
- `event`: optional case-insensitive substring match.
- `module`: optional case-insensitive substring match.
- `level`: optional exact or substring match.
- `errors_only`: optional boolean. When true, return events that look like failures.
- `since`: optional ISO datetime lower bound.
- `until`: optional ISO datetime upper bound.

The response should include:

```json
{
  "items": [],
  "summary": {
    "total": 0,
    "filtered": 0,
    "today_events": 0,
    "today_tickets": 0,
    "error_events": 0,
    "latest_timestamp": ""
  },
  "filters": {},
  "jsonl_enabled": true,
  "log_dir": "/opt/zd-agent/logs",
  "exists": true
}
```

The backend should redact sensitive fields before returning run records. The frontend can keep client-side redaction as defense in depth, but backend redaction is the real boundary.

### Config Schema

`AdminConfigStore` remains the source of editable fields. It should gain validation metadata where useful:

- URL fields validate `http://` or `https://` when non-empty.
- Numeric fields define conservative minimums and maximums.
- Secret writes ignore blank values.
- Protected fields stay visible but clearly marked.
- Restart-required fields save to overrides and show pending restart.

`POST /admin/api/config/preview` and `POST /admin/api/config/apply` should reject invalid payloads with short, non-sensitive error messages.

### Integration Checks

Add `POST /admin/api/integrations/check`.

Request:

```json
{
  "integration": "signoz"
}
```

Supported integrations:

- `zendesk_mcp`
- `db_gateway`
- `signoz`
- `source_mcp`
- `github_mcp`
- `docs_tool_service`
- `notification_service`
- `sandbox_worker`
- `public_reply_browser_relay`

The endpoint should read effective config, check whether URL and API key are configured, and perform a lightweight HTTP health or MCP initialize request when possible. If a service has no safe health check, the endpoint should report `configured` without claiming it is reachable.

The response should avoid secrets:

```json
{
  "ok": true,
  "integration": "signoz",
  "configured": true,
  "reachable": true,
  "status": "ok",
  "detail": "HTTP 200",
  "checked_at": "2026-06-29T00:00:00Z"
}
```

Each check writes an audit event with action `integration_check`.

### Deploy Status

`GET /admin/api/deploy/status` should return:

- agent process status.
- configured image tag if available from env.
- commit SHA if available from env.
- log directory.
- state directory.
- admin auth method configuration.
- whether saved config contains restart-required pending changes.

The UI can show CI/CD and deployment metadata, but v1 should not restart services from the browser.

### Audit Logs

`GET /admin/api/audit-logs` should support `limit`, `action`, and `actor`. The response should include summary counts and fully redacted diff values.

Audit entries should cover:

- `admin_login`
- `admin_logout`
- `admin_password_changed`
- `config_apply`
- `integration_check`

## Frontend Design

The frontend stays framework-free but should use clear module-level functions in `app.js`:

- API client and error handling.
- Auth flow.
- Navigation state.
- Run-record filters and drawer.
- Config form rendering.
- Config diff preview modal.
- Integration check actions.
- Audit-log filters and drawer.

The UI should use restrained blue-and-white operations styling. Cards should show operational content, not decorative marketing sections. The layout should remain dense enough for repeated use.

Key interaction rules:

- Every clickable row uses a real `<button>` or link.
- Drawers and modals close by button and Escape.
- Forms expose save, preview, reset, and validation messages.
- Destructive or high-impact actions require confirmation.
- Empty states explain what is missing and where the data comes from.
- Text must fit at desktop and mobile widths.

## Security Design

The console remains authenticated with an httpOnly session cookie. The backend must enforce authentication on every JSON endpoint. Client-side checks are only UI convenience.

Security constraints:

- Do not return raw secrets in config, logs, or audit responses.
- Do not log secret values in audit records.
- Do not expose stack traces in JSON errors.
- Validate query parameters and payload shapes at route boundaries.
- Keep secure cookie behavior behind HTTPS or `X-Forwarded-Proto: https`.
- Serve only known static files from the package.

The current default password flow stays in place: `admin` / `admin123` is allowed only for first login and forces password change before configuration access.

## Testing Strategy

Backend tests should cover:

- Static asset route registration and traversal rejection.
- Run-record filtering, summary, and redaction.
- Config validation errors and restart-required behavior.
- Integration check responses for configured, missing, reachable, and unreachable states.
- Audit-log filtering and redaction.
- Deploy status shape.

Frontend static contract tests should cover:

- The HTML shell references `styles.css` and `app.js`.
- The UI contains Chinese menu labels.
- The JavaScript contains render paths for run filters, drawers, preview modal, and integration checks.
- No vague `系统摘要` filler text remains.

Manual verification should include:

- Local admin UI loads without console errors.
- Login and forced password-change flow works.
- Run records filter and detail drawer work.
- Config preview shows diffs before save.
- Integration check writes an audit event.
- Mobile-width layout remains usable.

## Rollout Plan

This should land in small, rollback-friendly slices:

1. Add backend run-record filtering, redaction, and summary.
2. Add static asset serving and split the admin frontend into files.
3. Rebuild the run-record and overview UI on the new static structure.
4. Improve config editing, validation messages, and diff preview.
5. Add integration check API and UI.
6. Improve deploy status and audit-log filtering.
7. Run the full test suite, deploy through existing CI/CD, and verify `/admin` online.

Each slice should keep the existing `/admin` URL working.
