# 退款工单处理流程（SaaS）

退款是**敏感操作**，涉及真钱。必须按此流程，不得跳步。

## 核心约束

- ⚠️ **不得自动发起 Stripe 退款**
- ⚠️ **不得承诺具体退款金额 / 时间**
- ⚠️ 必须产出**两部分草稿**：客户回复 + 财务申请
- ⚠️ 推送飞书 `ticket_escalate` 卡片要求人工审批

## 处理步骤

### 1. 判定退款类型

- **全额退款**：服务从未使用 / 重大平台故障 / 7 天内新订阅
- **按比例退款**：部分月份未使用 / 降级申请
- **拒绝退款**：已过退款期 / 正常使用后单方面取消 / 超出政策范围

详细政策参考 skill `references/refunds.md`（如项目内有副本则读本地版本）。

### 2. SQL 查订阅状态

```sql
-- billing 库
SELECT * FROM tenant_subscription_settings WHERE tenant_id = '<wid>';
SELECT * FROM tenant_stripe_status WHERE tenant_id = '<wid>';
SELECT * FROM tenant_stripe_customers WHERE tenant_id = '<wid>';
```

调用 `sql_query database=billing sql="..."`。记录：plan / interval / expiration / stripe_subscription_id。

### 3. 适当时先做一次简短挽留

如果客户没明确拒绝，且场景合理（比如因某功能不满），可以在客户回复中提一个简短的挽留方案（如"调整配置试试"、"迁移到其他套餐" 等），再给出退款选项。

**不过度挽留**——客户明确要求退款时，简短挽留一次就够。

### 4. 内部备注必须包含两段

#### 段 1：客户回复草稿（**只写一遍**，二选一语言）

**二选一规则**：客户用中文 → 中文；其他客户（含日语 / 葡语 / 英语 ...）→ 一律 English。

**English customer**（含日语 / 葡语等非中文客户都走这条）:
```
Hi <name>,

Thank you for reaching out. I've reviewed your subscription...
<一句简短挽留，可选>
We can process <partial/full> refund of <amount> for <period>. <or rejection reason>
Could you confirm whether you'd like to proceed?

Best regards,

Dify Support
```

**中文 customer**:
```
您好 <name>，

感谢联系我们，我已经查看了您的订阅...
<简短挽留，可选>
我们可以为您处理 <部分/全额> 退款，金额 <金额>，对应 <周期>。<或拒绝理由>
请确认是否继续。

此致

Dify Support
```

#### 段 2：财务申请草稿（纯文字，给财务看）

```
[REFUND REQUEST]
Ticket: #<id>
Requester: <email>
Workspace ID: <wid>
Stripe Customer: <cus_id>
Stripe Subscription: <sub_id>
Plan: <plan>
Refund Type: <全额/按比例/拒绝>
Amount: <金额 + 币种>
Period: <对应周期>
Reason: <简述原因，如"7 天内新订阅 + 功能不符合预期">
Policy Applies: <是否符合现行退款政策>
Evidence: <SQL 查出的订阅记录关键字段>
```

### 5. 推送飞书 ticket_escalate

```python
feishu_push(
    card_type="ticket_escalate",
    content="退款申请待审批：<金额> / <类型>",
    zendesk_url="https://<domain>.zendesk.com/agent/tickets/<id>",
)
```

### 6. archive_ticket 用 `category=refund`

确保工单记录文件里 frontmatter 的 `category: refund`，便于后续追溯所有退款工单。

## 拒绝退款场景的回复

拒绝时**必须**给客户清晰的原因，不要打太极。

```
Hi,

Based on our refund policy, this request doesn't qualify for a refund because:
- <具体原因 1>
- <具体原因 2>

<Alternative if any: e.g. pause subscription, switch plan>

Best regards,

Dify Support
```

## 参考案例

历史退款工单可用 `ticket_record_search keywords=["退款", "refund"]` 检索，参考 Support Engineer 以往的处理方式。
