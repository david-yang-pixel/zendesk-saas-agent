# Agent Admin Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a first usable admin console for the Zendesk SaaS agent with login, editable configuration, secret replacement, audit logs, and blue-white UI.

**Architecture:** Add focused aiohttp admin routes under `/admin` and `/admin/api/*`. Backend state is stored in the existing agent state directory; static UI is served from the Python package. Existing `/agent/*` endpoints remain available for the external management platform.

**Tech Stack:** Python 3.12, aiohttp, pydantic, PyYAML, vanilla HTML/CSS/JS, pytest.

---

### Task 1: Admin Auth

**Files:**
- Create: `src/zd_agent/services/admin_auth.py`
- Modify: `src/zd_agent/services/app_context.py`
- Test: `tests/services/test_admin_auth.py`

- [x] Write tests for default `admin/admin123`, forced password change, session validation, logout, and password change.
- [x] Implement PBKDF2 password hashing using Python stdlib.
- [x] Persist auth state in `.state/admin_auth.json`.
- [x] Keep session tokens server-side and issue httpOnly cookies.

### Task 2: Config Registry and Audit Store

**Files:**
- Create: `src/zd_agent/services/admin_config.py`
- Test: `tests/services/test_admin_config.py`

- [x] Write tests for schema metadata, effective config redaction, preview diff, apply persistence, secret fingerprinting, and audit JSONL.
- [x] Implement field registry grouped by admin menus.
- [x] Persist non-secret overrides to `admin_overrides.yaml`.
- [x] Persist secrets to `admin_secrets.yaml` with `0600` permissions.
- [x] Apply saved overrides to the in-memory `AppConfig`.

### Task 3: Admin Routes

**Files:**
- Create: `src/zd_agent/services/admin_routes.py`
- Modify: `src/zd_agent/services/saas_app.py`
- Modify: `src/zd_agent/services/app_bootstrap.py`
- Modify: `src/zd_agent/services/app_context.py`
- Test: `tests/entry/test_admin_routes.py`

- [x] Register `/admin`, `/admin/`, and `/admin/api/*` routes.
- [x] Protect admin APIs with admin session cookies.
- [x] Keep external `/agent/*` bearer-token endpoints unchanged.
- [x] Load persisted admin overrides during SaaS app bootstrap.

### Task 4: Static Admin UI

**Files:**
- Create: `src/zd_agent/admin_static/index.html`
- Test: `tests/entry/test_admin_routes.py`

- [x] Build the blue-white admin shell with the approved menus.
- [x] Add login screen with default account/password copy.
- [x] Implement model/config pages from schema-driven rendering.
- [x] Add deployment access section for password, Google SSO, and Feishu SSO settings.
- [x] Render audit logs only under 审计日志.

### Task 5: Verification

**Files:**
- Modify tests as needed.

- [x] Run focused admin tests.
- [x] Run existing management route tests.
- [x] Run a syntax compile pass.
- [x] Start a local app and smoke-test `/admin` if feasible.
