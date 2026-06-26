# 退款工单处理流程（SaaS）

退款涉及**真钱**。流程铁律：

> **agent 先做 Owner gate；通过后负责查证 + 告知客户判定（public_reply），人工/财务负责实际操作 Stripe。**
> 不要又沉默又把客户晾在那儿等几小时——客户能等的是"具体操作"，等不了的是"没回音"。

## 核心约束

- ⚠️ **不得自动发起 Stripe 退款**（必须人工/财务在 dashboard 操作）
- ⚠️ **不得承诺具体到账时间**（"我们会尽快处理"OK，"3 天内到账"不行）
- ⚠️ Owner gate 通过后，**可以告诉客户是否符合退款条件** —— 这是 agent 的核心价值
- ⚠️ Owner gate 通过且已完成退款资格判定后，才推送飞书 `ticket_escalate`
  让财务跟进实际操作

## 处理步骤（按顺序）

### 0. Owner gate（必须先做）

退款请求必须先确认 requester 是否为 workspace owner。先用 prod 库查
`tenant_account_joins` + `accounts`，确认 requester email 在该 workspace 的角色：

```sql
SELECT taj.role, a.email, a.name
FROM tenant_account_joins taj
JOIN accounts a ON a.id = taj.account_id
WHERE taj.tenant_id = '<wid>'
  AND lower(a.email) = lower('<requester_email>');
```

- requester 是 workspace owner → 再继续查 billing 库并判断退款资格。
- requester 不是 owner / 查不到 owner 身份 → 停止退款资格判断，只回复要求
  workspace owner 账号或邮箱确认；不要继续判断退款资格，不要写符合/不符合退款政策，
  不要推送飞书，不要 `ticket_escalate`。

### 1. SQL 查订阅状态（database=billing）

```sql
SELECT * FROM tenant_subscription_settings WHERE tenant_id = '<wid>';
SELECT * FROM tenant_stripe_status         WHERE tenant_id = '<wid>';
SELECT * FROM tenant_stripe_customers      WHERE tenant_id = '<wid>';
SELECT * FROM subscriptions WHERE tenant_id = '<wid>' ORDER BY created_at DESC;
```

记下：plan / interval / 续费时间 / stripe_subscription_id / 上次 charge 金额。

### 2. 判定退款类型

| 类型 | 判定依据 |
|---|---|
| **全额退款** | 7 天内新订阅 / 服务从未使用 / 重大平台故障期间订阅 |
| **按比例退款** | 部分月份未使用 / 主动降级 |
| **拒绝退款** | 已过退款期 / 正常使用后单方面取消 / 超出政策范围 |

详细政策见 skill `references/refunds.md`（如项目内有副本则读本地版本）。

### 3. ticket_record_search 看历史先例

```python
ticket_record_search(keywords=["退款", "refund", <相关 plan/场景词>])
```

看 SE 以前类似单是怎么判的，避免和先例不一致。

### 4. 写 public_reply 告诉客户**判定结果**

⚠️ **关键变化**：不再"内部备注 + 全程沉默等 SE"，而是直接 public_reply
告诉客户判定结论，让客户有明确预期。

**符合退款条件的回复模板（English）**：
```
Hi <name>,

I've reviewed your subscription and our refund policy:
- Plan: <plan>, started on <date>
- Reason qualifying: <e.g., within 7-day window / unused remaining months>

Good news — your request **qualifies** for a <full / partial> refund of approximately <amount>.

Our finance team has been notified and will process the actual refund via Stripe within
the next 1-2 business days. You'll receive a separate email once it's issued.

Best regards,

Dify Support
```

**符合退款条件的回复模板（中文）**：
```
您好 <name>，

我已根据退款政策核查了您的订阅：
- 套餐：<plan>，订阅起始：<日期>
- 符合条件的原因：<如"7 天内新订阅" / "剩余月份未使用">

您的退款申请**符合**<全额/部分>退款条件，预计金额约 <金额>。

财务团队已收到通知，将在 1-2 个工作日内通过 Stripe 实际处理退款。
退款发起后您将收到单独的邮件确认。

此致

Dify Support
```

**不符合退款条件的回复模板（English）**：
```
Hi <name>,

I've reviewed your subscription against our refund policy:
- Plan: <plan>, started on <date>
- Request falls outside refund window: <具体原因>

Unfortunately, this request **does not qualify** for a refund under our current policy.

Alternative options we can offer:
- <option 1, e.g., pause subscription / switch plan / credit toward next renewal>

If you'd like to proceed with any of these, just reply here.

Best regards,

Dify Support
```

**语言规则**：客户中文 → 中文；其他语言（含日 / 葡 / 英）→ 一律 English。
**绝不双语对照**。

### 5. 同步推送飞书 ticket_escalate（**必须 inline 完整 draft**）

```python
feishu_push(
    card_type="ticket_escalate",
    content="""**退款申请待财务处理**（agent 已告知客户判定）

[REFUND REQUEST]
Ticket: #<id>
Requester: <email>
Workspace ID: <wid>
Stripe Customer: <cus_xxx>
Stripe Subscription: <sub_xxx>
Plan: <plan>
Refund Type: <full / partial / rejected>
Amount: <USD>
Period: <对应周期>
Reason: <简述>
Policy Applies: <是否符合现行退款政策>
Evidence: <SQL 关键字段>

**Agent 已向客户回复**：<符合 / 不符合> 退款条件
**财务下一步**：<dashboard 操作 / 无需操作（已拒绝）>
""",
    zendesk_url="https://<domain>.zendesk.com/agent/tickets/<id>",
)
```

财务在飞书看到完整 draft → 知道 agent 已经回复客户 → 只需要在 Stripe
dashboard 实际点退款（或不操作）。**财务不需要再写客户回复**，agent 已经写好了。

### 6. archive_ticket 用 `category=refund`

便于后续追溯所有退款工单 + agent 学习历史判定。

## 拒绝退款的额外要求

拒绝时 **必须**：
- 给客户清晰的具体原因（不要打太极）
- 给一个 alternative（暂停 / 切换 plan / credit），除非客户已明确不接受

## ⚠️ 什么情况依然 escalate 不公开回复

- 客户**情绪激动**（emotional 类）—— 由 trigger_decider 标记，直接转人工
  不走 agent，agent 看到 escalated 状态就不会进入了
- 客户**SQL 查不到 workspace** / 数据严重异常 → Internal Note 标
  `confidence=low + needs_human=True`，让 SE 介入而不是公开回复

## 参考案例

历史退款工单可用 `ticket_record_search keywords=["退款", "refund"]` 检索，参考 SE 以往的处理方式。
