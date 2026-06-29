# Agent Admin Console Design

## Goal

Build a dedicated blue-white management console for the Zendesk SaaS agent. It should manage nearly all non-run-state configuration, keep run logs in run records, centralize all change audit logs, and protect the console with configurable admin login.

## Navigation

The first version uses these left-side menus:

- 总览
- 运行记录
- 触发配置
- 回复策略
- 模型与运行
- 工具与集成
- 存储配置
- 部署状态
- 审计日志

There is no separate "密钥与凭据" menu. Secrets are configured in the module that owns them. There is no separate "系统观测" menu. Single-run logs live in 运行记录; service health summaries live in 总览, 工具与集成, and 部署状态.

## Editability

Configuration fields are editable by default. Exceptions are handled through field metadata:

- `secret`: users can replace the value, but the old value is never returned.
- `restart_required`: saving is allowed, but the UI marks the change as needing restart.
- `protected`: saving is allowed only with explicit confirmation in the UI.
- `runtime_only`: displayed in run records, not editable in config pages.

Model provider, model IDs, base URL, and API key are all editable. Model IDs are free-text inputs; model lists are suggestions only.

## Persistence

Runtime admin changes are saved under the agent state directory:

- `admin_overrides.yaml` for non-secret config overrides.
- `admin_secrets.yaml` for secret overrides, written with owner-only file permissions.
- `admin_audit.jsonl` for audit records.
- `admin_auth.json` for local admin password configuration.

The running `AppConfig` is updated immediately for fields that can take effect in memory. Restart-required fields are still persisted and marked in the response.

## Authentication

The console defaults to account-password login:

- Username: `admin`
- Initial password: `admin123`
- First login requires password change.

Google SSO and Feishu SSO are represented as configurable login methods in 部署状态 -> 后台访问. The first implementation stores their configuration and enabled flags; full OAuth callback flows can be added later.

At least one login method must remain enabled. Disabling password login should require a verified SSO provider in a later full SSO implementation.

## Audit Logs

All configuration, secret, auth, reload, and deploy changes are written only to the 审计日志 menu. Other pages may show the generated audit ID and link to the audit record, but they do not render audit tables.

Secret audit records include the field path and fingerprint, not the plaintext old or new value.

## UI Direction

Use a light-blue left navigation rail, white main content, compact forms, blue primary buttons, and small status chips. The bottom-left area shows agent health and a logout button. Logout only clears the admin console session and never stops the agent.
