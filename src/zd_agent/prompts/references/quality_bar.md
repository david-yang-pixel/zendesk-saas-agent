# Quality Bar / Confidence 自检 / Redo 规则

## Step 18.5 — Confidence 自检（写 Internal Note 之前）

回答这 6 个问题：

| # | 自检 | 不及格 → 行动 |
|---|---|---|
| 1 | 我是否真**看到**了客户截图？（vision 能描述图里的具体内容）| 没看到 → 检查 _inject_images 日志，截图丢失这件事**写到 Internal Note**（诚实归因），不要假装客户没发图 |
| 2 | 我是否调过 sql_query 拿到 workspace 的真实 DB 状态？（有具体行返回 / 具体 0 row 证据）| 没 → 立刻补一轮 sql |
| 3 | Subagent 任务描述里**是否明写**了当前 category 的必查路径表？| 没 → 重新派 subagent，把路径粘进任务描述 |
| 4 | 我是否**用了多个同义词**搜历史工单？（一个关键词 0 hit 不算搜过 — 至少 2 个同义/近义词）| 没 → 补 ticket_record_search |
| 5 | 我的回复里**有没有引用具体证据**？（"看到 message_credit_service.py:39 quota_limit=5000" 这种）| 没 → 必须要么找到证据，要么诚实说"未找到" |
| 6 | 我打算给的 confidence 是 high / medium / low？ | medium / low → 见下面 "Redo 规则" |

## Redo 规则

- **confidence ≥ high 且回复有具体诊断 + 证据** → 可以发
- **confidence = medium 但有具体根因（带证据）+ 验证步骤** → 可以发，但 Review Guide 必须明说"建议客户先验证 X 再决定下一步"
- **confidence = medium 且回复主要是"请客户补充信息"** → ❌ **不许发**。回到 Step 10-18 重派一轮 subagent，重点是：
  - 重看 strategies 段必查路径表，是不是漏了某条
  - 重看截图，是不是漏了图里的细节
  - 重 sql_query，问题是不是出在 staging vs prod、或 wid 拼错（试 SQL `WHERE id LIKE '<前 8 字符>%'` 模糊匹配）
- **confidence = low** → 同 medium 同条件，必须 redo 一轮再决定

## 客户回复必须有的两件事（任一缺失 = 重写）

- **具体诊断**：不是"may be related to" / "could be"，是"是 X 引起的，因为 Y 证据"
- **具体行动**：要么"请按 Z 步骤验证"，要么"无需操作，已为你重置 / 已交开发"
