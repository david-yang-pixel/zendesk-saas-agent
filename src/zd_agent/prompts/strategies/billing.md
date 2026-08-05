# 计费 / 订阅问题处理策略

客户问：发票、扣款、配额、套餐升降级、额度不足、AI Credits。

## ⚡ Subagent A 必查路径（**派 subagent A 时把这些路径明写进任务描述**）

| 仓库 | 路径 | 看什么 |
|---|---|---|
| **dify-billing** | `app/services/billing/message_credit_service.py` | **AI Credits 配额计算的源头**（plan → quota_limit 映射） |
| **dify-billing** | `app/services/dify/provider_service.py` | credit pool init / get_or_create / hosted model 配额 |
| **dify-billing** | `app/models/billing/*` | subscription / message_credit_log / TenantMessageCreditLog schema |
| **dify-billing** | `app/processors/message_credit_*` | credit 扣减 / 续费触发 / 年订阅续费 |
| dify | `api/services/billing/*` | Cloud 侧 billing 调用入口 |
| dify | `api/services/credit_pool_service.py` | credit pool 业务逻辑 |
| dify | `api/core/app/llm/quota.py` | quota 扣费实际位置 |

⚠️ **绝不能漏掉 dify-billing**——SaaS 计费的真理之源在那里，不在 dify 主仓的 i18n 或 workspace_service。
**反例（之前被错答的 #2364）**：客户问 "每个模型 AI Credits 占比" → agent 只搜 dify 主仓 i18n + workspace_service 就给"未公开比率表"。
**正确做法**：搜 `dify-billing/app/services/billing/message_credit_service.py` → 看到 `quota_limit = 5000 (Professional)` 的硬编码 → 知道 **不区分模型**，按 message count 一刀切扣 1 credit/次。

## 关键步骤
1. 收集：Workspace ID、涉及时间段、具体金额 / 发票号
2. **套餐判断必须走身份绑定，不能用 `section=all` 代替**：
   - 身份定位优先使用用户表单 Email：`inspector_exec email=<表单 Email> section=tenants`，这里只取 account / tenant_id / name / status / role；其中 TENANT/TENANTS 的 Plan 是 Prod 辅助字段，禁止作为当前套餐结论。只有表单没有 Email 时才退回 requester email。
   - 表单 Email 只对应一个 workspace → 使用该 tenant_id；对应多个 workspace → 仅当表单 Workspace ID 位于返回列表中时才能选择，否则禁止猜测套餐，向客户澄清目标 workspace。
   - 套餐 / 订阅 / Stripe 状态：拿到 tenant_id 后必须调用 `inspector_exec tenant_id=<workspace_id> section=billing`，只采用工具顶部的 `AUTHORITATIVE_BILLING_*` 与 `Source=billing_db` 结果。
   - 配额 / 用量（apps / 文档 / 知识库存储 / 成员）：`inspector_exec tenant_id=<workspace_id> section=quota` —— 含 TiDB 知识库存储（手写 SQL 拿不到）
   - `email + section=all` 以及 `tenant_id + section=all` 都不能授权公开回复中的当前套餐断言。
3. **inspector billing 缺失、超时或返回 unknown 时立即走 `sql_query database=billing`**：
   - 当前套餐查 `tenant_subscription_settings`；订阅有效性同时查 `tenant_stripe_status`。
   - AI Credits 时间窗聚合（`tenant_message_credit_logs` 按 day 累计）
   - 自定义跨字段 JOIN / 任意聚合
4. **必查 dify-billing 仓**（按上面的路径表 git_grep）
5. 参考本地知识库 `saas版知识库/08-billing-and-subscription.md`

## 注意
- 涉及退款 → 使用 refund 分类（见 refund.md），流程不同
- 不要直接承诺金额 / 时间；涉及财务的都需人工审批
