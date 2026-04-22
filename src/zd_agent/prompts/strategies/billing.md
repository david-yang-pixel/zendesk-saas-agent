# 计费 / 订阅问题处理策略

客户问：发票、扣款、配额、套餐升降级、额度不足。

## 关键步骤
1. 收集：Workspace ID、涉及时间段、具体金额 / 发票号
2. SQL 直查（sql_query database=billing）：tenant_stripe_customers / tenant_subscription_settings
3. 额度相关：tenant_feature_usages / tenant_message_credit_logs
4. 参考本地知识库 `saas版知识库/08-billing-and-subscription.md`

## 注意
- 涉及退款 → 使用 refund 分类（见 refund.md），流程不同
- 不要直接承诺金额 / 时间；涉及财务的都需人工审批
