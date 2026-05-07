# 权限 / 成员 / SSO 问题处理策略

客户问题涉及：成员邀请、角色、SSO 登录、组织归属。

## ⚡ Subagent A 必查路径

| 仓库 | 路径 |
|---|---|
| dify | `api/services/account_service.py` — 账号管理 |
| dify | `api/services/workspace_service.py` — workspace 成员 |
| dify | `api/services/auth/*` — SSO / OAuth flow |
| dify | `api/models/account.py` — Account / Tenant / TenantAccountJoin / Role |
| dify | `api/controllers/console/auth/*` — 登录/邀请 endpoint |

## 关键 SQL（database=prod）
- `tenants WHERE id = '<wid>'`
- `accounts WHERE email = '<email>'`
- `tenant_account_joins WHERE tenant_id = '<wid>' AND account_id = '<aid>'` — 看 role
- `account_integrates WHERE account_id = '<aid>'` — SSO 提供商绑定

## 关键步骤
1. 收集：Workspace ID、相关用户邮箱、具体操作路径
2. SQL 直查 tenants / accounts / tenant_account_joins
3. 并行 subagent：A. 源码（按上表）+ B. ticket_record_search
4. 参考本地知识库 `saas版知识库/05-permissions.md`

## 常见根因
- 邀请邮件未投递 / 进垃圾箱
- SSO 配置 redirect URI 错
- 角色权限与预期不符（Admin vs Member vs Owner）
- 账号绑定到错误 tenant（account 在另一个 wid 但客户在错的 wid 操作）
- SSO 后多账号合并冲突
