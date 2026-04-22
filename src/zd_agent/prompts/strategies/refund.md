# 退款工单处理策略（必须产出两部分草稿）

涉及钱款，流程特殊。必须产出：
1. **客户回复草稿**（可能先做一次简短挽留）
2. **财务申请草稿**（含 Workspace ID、金额、原因）

## 关键步骤
1. 先 SQL 直查订阅状态（sql_query database=billing）
2. 判定退款类型：全额 / 按比例 / 拒绝
3. Internal Note 分为两段：
   - 段 1：客户回复草稿（英文+中文）
   - 段 2：财务申请草稿（纯文字，含金额/Workspace ID/原因）
4. 推送飞书 ticket_escalate 卡片提醒人工审批

## 绝对不能做
- 不自动发起 Stripe 退款
- 不承诺具体金额 / 处理时间
- 不跳过财务审批环节

参考 skill `references/refunds.md` 与 Section 11。
