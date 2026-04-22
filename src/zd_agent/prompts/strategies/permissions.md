# 权限 / 成员 / SSO 问题处理策略

客户问题涉及：成员邀请、角色、SSO 登录、组织归属。

## 关键步骤
1. 收集：Workspace ID、相关用户邮箱、具体操作路径
2. SQL 直查：tenants / accounts / tenant_account_joins 表
3. 参考本地知识库 `saas版知识库/05-permissions.md`

## 常见根因
- 邀请邮件未投递 / 进垃圾箱
- SSO 配置 redirect URI 错
- 角色权限与预期不符（Admin vs Member）
- 账号绑定到错误 tenant
