# 退款工单处理策略

> **流程铁律**：agent 查证 + public_reply 告知客户判定 + 飞书推财务实际操作。
> 不再"内部备注全程沉默等 SE"——那样客户体验最差。

## ⚡ Subagent A 必查路径（如确实需要派 subagent）

| 仓库 | 路径 | 看什么 |
|---|---|---|
| **dify-billing** | `app/services/billing/*` | 订阅 / 续费 / 退款流程 |
| **dify-billing** | `app/services/dify/provider_service.py` | provider quota |
| **dify-billing** | `app/models/billing/subscription.py` | SubscriptionPlan 枚举 / status |
| **dify-billing** | `app/processors/*subscription*` | 订阅状态机 |
| dify | `api/services/billing/*` | Cloud 侧调用 |

通常不必派 subagent，主 agent 直接 SQL 即可判定。

## 关键 SQL（database=billing）

- `tenant_stripe_customers WHERE tenant_id = '<wid>'` — Stripe customer_id
- `tenant_subscription_settings WHERE tenant_id = '<wid>'` — plan / interval / expiration_date
- `tenant_stripe_status WHERE tenant_id = '<wid>'` — Stripe 订阅状态
- `subscriptions WHERE tenant_id = '<wid>' ORDER BY created_at DESC` — 历史订阅

## 关键步骤

1. **SQL 直查** 订阅状态（sql_query database=billing）
2. **判定退款类型**：全额 / 按比例 / 拒绝（依据：申请时间 vs 订阅起始时间、是否真使用过）
3. **ticket_record_search** 看 SE 历史先例
4. **写 `zendesk_public_reply`**（reply_type="solution"）告诉客户：
   - 是否符合退款条件 + 具体原因
   - 若符合：约略金额 + "财务会在 1-2 工作日内通过 Stripe 处理"
   - 若不符合：清晰原因 + alternative（暂停 / 切换 plan / credit）
5. **同步 feishu_push ticket_escalate** 推财务（content 必含完整 REFUND REQUEST draft）
6. `archive_ticket category=refund`

详见 `refund/REFUND_FLOW.md` 模板。

## 绝对不能做

- ❌ **不自动发起 Stripe 退款**（agent 只能告知客户判定，不能实操）
- ❌ **不承诺具体到账时间**（"1-2 工作日" OK，"3 天内到账" 不行）
- ❌ **不双语对照** —— 客户中文 → 中文，其他 → English
- ❌ **不沉默 escalate** —— 必须先 public_reply 告诉客户判定

## confidence / needs_human 取值

- SQL 查到清晰订阅状态 + 政策匹配明确 → `confidence=high`, `needs_human=False`
  （财务只需在 Stripe 实操，不需要再决策）
- SQL 查不到 workspace 或政策模糊 → `confidence=low`, `needs_human=True`
  （改走 `zendesk_internal_reply`，让 SE 介入）
