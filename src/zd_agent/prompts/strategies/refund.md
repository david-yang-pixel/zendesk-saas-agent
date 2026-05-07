# 退款工单处理策略（必须产出两部分草稿）

涉及钱款，流程特殊。必须产出：
1. **客户回复草稿**（可能先做一次简短挽留）
2. **财务申请草稿**（含 Workspace ID、金额、原因）

## ⚡ Subagent A 必查路径

| 仓库 | 路径 | 看什么 |
|---|---|---|
| **dify-billing** | `app/services/billing/*` | 订阅 / 续费 / 退款流程 |
| **dify-billing** | `app/services/dify/provider_service.py` | provider quota |
| **dify-billing** | `app/models/billing/subscription.py` | SubscriptionPlan 枚举 / status |
| **dify-billing** | `app/processors/*subscription*` | 订阅状态机 |
| dify | `api/services/billing/*` | Cloud 侧调用 |

## 关键 SQL（database=billing）
- `tenant_stripe_customers WHERE tenant_id = '<wid>'` — Stripe customer_id
- `tenant_subscription_settings WHERE tenant_id = '<wid>'` — plan / interval / expiration_date
- `subscriptions WHERE tenant_id = '<wid>'` — 完整订阅记录（按 created_at desc）

## 关键步骤
1. 先 SQL 直查订阅状态（sql_query database=billing）
2. 判定退款类型：全额 / 按比例 / 拒绝（依据：申请时间 vs 订阅起始时间、是否真使用过）
3. **必查 dify-billing 仓** —— 看当前订阅状态机和退款流程逻辑
4. Internal Note 分为两段：
   - 段 1：客户回复草稿（**只用客户本人语言写一遍**，绝不 EN+X 双语）
   - 段 2：财务申请草稿（纯文字，含金额/Workspace ID/原因）
5. 推送飞书 ticket_escalate 卡片提醒人工审批

## 绝对不能做
- 不自动发起 Stripe 退款
- 不承诺具体金额 / 处理时间
- 不跳过财务审批环节

参考 skill `references/refunds.md` 与 Section 11。
