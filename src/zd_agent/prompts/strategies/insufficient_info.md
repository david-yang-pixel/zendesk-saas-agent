# 信息不足工单处理策略

客户描述过于简略，缺关键信息。

## 关键步骤
1. 先判断客户是否已经表达真实问题或诉求；如果只是打招呼或内容过短，不要假设是 bug。
2. 通过 `zendesk_public_reply` 发送 `reply_type="ask"`，让大模型按最新上下文生成针对性 ask；ask 的目的，是让客户说出这次需要什么帮助，以便 agent 后续自行分类和处理。
3. 如果客户只是"你好 / 您好 / hello / 在吗"，先礼貌询问客户需要什么帮助；可以用 `For example...` 列常见方向（billing/refund、feature usage/how-to、account/subscription、workflows、knowledge base、API、error/bug），但不要要求客户先选择分类。
4. 如果 public ask 发送失败或当前不允许公开回复，只写 internal note 说明等待客户补充，不要推送飞书人工介入。
5. 不要瞎猜根因。

## 严禁
- 不要因为"信息不足"、"客户只打招呼"、"没有具体问题"推送飞书 `ticket_escalate`。
- `ticket_escalate` 只用于客户明确要求人工、ask 次数达到上限、退款/财务/SRE/敏感操作等真实人工动作；初次信息不足不属于人工介入。

## 不要做
- 不要基于臆测写客户回复
- 不要调用 SQL / 源码工具（没有 ID 查不出）
- 不要调用 SigNoz（没有时间点）
