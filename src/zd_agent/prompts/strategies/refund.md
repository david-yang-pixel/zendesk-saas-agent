# 退款工单处理策略

> **流程铁律**：agent 先做 Owner gate；通过后只核实订阅、支付、政策版本和时间线等事实。
> 退款资格、全额/按比例/拒绝、金额、审批、处理和时效属于**人工业务审批**，agent **不能自行判定**。
> 不要让客户在没有公开进度的情况下等待人工。

## ⚡ Subagent A 必查路径（如确实需要派 subagent）

| 仓库 | 路径 | 看什么 |
|---|---|---|
| **dify-billing** | `app/services/billing/*` | 订阅 / 续费 / 退款流程 |
| **dify-billing** | `app/services/dify/provider_service.py` | provider quota |
| **dify-billing** | `app/models/billing/subscription.py` | SubscriptionPlan 枚举 / status |
| **dify-billing** | `app/processors/*subscription*` | 订阅状态机 |
| dify | `api/services/billing/*` | Cloud 侧调用 |

通常不必派 subagent；主 agent 直接查询必要事实即可，但 SQL 结果不能替代业务审批。

## 关键 SQL（database=billing）

- `tenant_stripe_customers WHERE tenant_id = '<wid>'` — Stripe customer_id
- `tenant_subscription_settings WHERE tenant_id = '<wid>'` — plan / interval / expiration_date
- `tenant_stripe_status WHERE tenant_id = '<wid>'` — Stripe 订阅状态
- `subscriptions WHERE tenant_id = '<wid>' ORDER BY created_at DESC` — 历史订阅

## Owner gate（必须先做）

退款请求必须先确认 requester 是否为 workspace owner。先用 prod 库查
`tenant_account_joins` + `accounts`，确认 requester email 在该 workspace 的角色：

```sql
SELECT taj.role, a.email, a.name
FROM tenant_account_joins taj
JOIN accounts a ON a.id = taj.account_id
WHERE taj.tenant_id = '<wid>'
  AND lower(a.email) = lower('<requester_email>');
```

- requester 是 workspace owner → 再继续查 billing 库，收集人工审核所需事实。
- requester 不是 owner / 查不到 owner 身份 → 立即停止，回复要求 workspace owner
  账号或邮箱确认；不要继续判断退款资格，不要写符合/不符合退款政策，
  不要推送飞书，不要 `ticket_escalate`。

## 关键步骤

1. **Owner gate**：先确认 requester 是 workspace owner；不是 owner 就停止
2. **SQL 直查** 订阅状态（sql_query database=billing）
3. 记录可验证事实：plan、订阅/续费时间、charge、使用时间线、客户请求时间、适用政策版本；缺字段就标 unknown
4. 历史工单只用于寻找需要核对的字段，不能当作本单资格或金额依据
5. **写 `zendesk_public_reply(reply_type="handoff")`**：
   - 只说明已核实的订阅/支付事实
   - 明确退款资格和金额仍需 Support/Billing 人工业务审批
   - 不承诺批准、拒绝、金额或完成时间
6. handoff 成功后由发送层自动写 Internal Analysis 并通知内部团队；不要额外重复调用 `feishu_push`

详见 `refund/REFUND_FLOW.md`。

## 绝对不能做

- ❌ **不自动发起 Stripe 退款**
- ❌ **不能自行判定**符合/不符合、全额/部分、拒绝或替代 credit
- ❌ **不估算退款金额**
- ❌ **不承诺处理或到账时间**（包括“1-2 个工作日”）
- ❌ **不双语对照** —— 客户可见 public reply 一律 English
- ❌ **不沉默 escalate** —— 必须先公开回复客户已确认事实和人工审核下一步

## confidence / needs_human 取值

- 技术事实可按成功证据标 high/medium/low，但只代表事实置信度，不代表退款获批。
- 所有需要退款资格、金额、审批、Stripe 操作或时效决定的请求均
  `needs_human=True`。低置信度也要先公开说明：当前能确认什么、什么尚未确认、
  需要 Support/Billing 进一步审核；用 `zendesk_public_reply(reply_type="handoff")`。
  不要向客户暴露 SQL、内部错误或猜测。
