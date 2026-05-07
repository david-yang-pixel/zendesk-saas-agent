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
2. SQL 直查（sql_query database=billing）：`tenant_stripe_customers` / `tenant_subscription_settings`
3. 额度相关：`tenant_feature_usages` / `tenant_message_credit_logs`
4. **必查 dify-billing 仓**（按上面的路径表 git_grep）
5. 参考本地知识库 `saas版知识库/08-billing-and-subscription.md`

## 注意
- 涉及退款 → 使用 refund 分类（见 refund.md），流程不同
- 不要直接承诺金额 / 时间；涉及财务的都需人工审批
