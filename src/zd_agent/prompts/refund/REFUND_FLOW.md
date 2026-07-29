# 退款工单处理流程（SaaS）

退款涉及**真钱**。流程铁律：

> **agent 先做 Owner gate；通过后负责查证并公开说明事实，Support/Billing 负责人工业务审批，财务负责获批后的 Stripe 操作。**
> agent **不能自行判定**退款资格、全额/按比例/拒绝、金额、审批、处理或时效。
> 不要让客户在没有公开进度的情况下等待人工。

## 核心约束

- ⚠️ **不得自动发起 Stripe 退款**（必须人工/财务在 dashboard 操作）
- ⚠️ **不得承诺处理或到账时间**，包括“1-2 个工作日”
- ⚠️ Owner gate 通过后，只能告诉客户已核实的订阅、支付、请求时间和政策事实
- ⚠️ 退款结论必须由 Support/Billing 人工业务审批；不得把 SQL、历史先例或模型判断写成批准/拒绝
- ⚠️ 使用 `zendesk_public_reply(reply_type="handoff")` 后，由发送层自动通知内部团队；不要额外重复推送飞书

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

- requester 是 workspace owner → 再继续查 billing 库，收集人工审核所需事实。
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
这些是技术/支付事实，不是退款资格结论。

### 2. 收集人工审批所需事实

- requester 与 workspace owner 核验结果
- 当前 plan、订阅起止/续费时间、charge 和币种
- 客户提出请求的时间与原始诉求
- 可验证的使用时间线或平台影响事实
- 本次适用的正式退款政策版本和未知项

agent 可以引用正式政策中的客观条款，但不能自行把客户归入“全额”“按比例”
或“拒绝”类别，也不能估算金额。缺少正式政策或字段时明确标 unknown。

### 3. 历史记录只用于选择核查项

```python
ticket_record_search(keywords=["退款", "refund", <相关 plan/场景词>])
```

历史工单不能证明本单资格、金额、审批或当前政策；只用于发现还需核对哪些字段。

### 4. 写 public_reply 告诉客户**已确认事实和审核下一步**

使用 `zendesk_public_reply(reply_type="handoff")`。公开回复只能包含：

- 已核实的 owner、plan、charge 或时间线事实（只写客户安全字段）
- 当前仍需 Support/Billing 审核的决定
- 下一步由谁处理，不承诺结果、金额或 ETA

**客户安全回复模板（English）**：
```
Hi <name>,

I've verified the following details for your request:
- Plan: <plan>
- Subscription or renewal date: <date>
- Charge: <amount and currency, only if confirmed>

Your refund eligibility and amount require review by our Support/Billing team.
I've forwarded the verified details for that review. We will update you here after
the review is complete.

Best regards,

Dify Support
```

**语言规则**：客户可见 public reply 一律 English，不跟随客户语言。
**绝不双语对照**。

### 5. Internal Analysis

写清 Owner gate 结果、已确认技术/支付事实、适用政策来源、未知项，以及
“资格/金额/审批/处理/时效待人工业务审批”。不要把历史案例写成当前决定。
handoff 客户投递成功后会自动写 Internal Analysis 并通知内部团队。

## ⚠️ 需要人工审核的场景（仍先公开说明）

- 客户**情绪激动**（emotional 类）—— 由 trigger_decider 标记并自动发送客户安全的 handoff，再转人工；不要在公开回复中复述内部分类。
- 客户**SQL 查不到 workspace** / 数据严重异常 → `confidence=low + needs_human=True`。低置信度也要先公开说明当前无法安全核实哪些事实、需要 Support/Billing 审核，再由 `zendesk_public_reply` 自动写 Internal Analysis 并升级；不要暴露 SQL 或内部错误。

## 参考案例

历史退款工单可用 `ticket_record_search keywords=["退款", "refund"]` 检索，
仅用于发现核查项，不能替代本次实时证据和人工业务审批。
