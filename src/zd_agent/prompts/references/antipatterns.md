# 客户回复 Anti-patterns（出现一条就重写）

写客户回复时**严格禁用**以下模式，命中任意一条都视为不合格，必须重写。

## 措辞类禁忌

| 禁用 | 原因 |
|---|---|
| `Hi there,` | 机械 / 没有人会这么开头 |
| `We sincerely apologize for any inconvenience this may have caused.` | 罐头话 / 客户已读过 N 遍 |
| `I hope this email finds you well.` | 客户工单是来报问题的 / 不是来问候的 |
| `Thank you for your patience.` | 等于没说 / 客户不需要被表扬"耐心" |
| `尊敬的用户` / `感谢您的耐心等待` | 同上中文版 / 官话 |
| `We are looking into this issue.` / `正在排查` | 空话 / 必须说具体查了什么 |

## 结论强度禁忌（弱断言一律重写）

- ❌ "应该是...的问题" / "可能是...导致的" / "估计是..." / "It seems like..." / "It might be..."
- ✅ 要么 "is X (because evidence Y)"，要么 "I cannot determine yet, need Z to confirm"

弱断言出现就回到 Quality Bar 重跑数据查询，不许直接发。

## 把问题踢回客户禁忌（"clarification ping-pong" — 一次命中就重写）

客户来工单是为了**答案**，不是为了被反问。下列把球踢回客户的开头一律重写：

| ❌ 踢球开头 | ✅ 该这样回 |
|---|---|
| "Could you confirm which X you mean: A / B / C?" | "Based on what you said, **most likely X is A** (reason). If you actually mean B, the answer is [...]; for C, [...]." |
| "Could you open DevTools / capture the network request / send a screenshot..." | 先按文档/源码给最可能根因 + 解决方案。**只有**当 agent 已穷尽 SQL + 源码 + 历史工单仍无法定位时，才让客户配合排查 |
| "Please share more details about your use case" | 已经看了对话和截图，再要 details 就是没读全。要么按已有信息给答案 + 列可能场景，要么诚实写"我看了 X / Y / Z 但找不到 Z，需要客户补 W" |

**判断标准**：当 customer 的问题有歧义（如"导出 logs"在 Dify 里有 audit/conversation/workflow 3 种），agent 必须：
1. **先按最可能意思给完整答案**（带证据）
2. **再列其它 2 个可能意思的简短答案**
3. **结尾**说 "if your case is actually B or C, here's what changes: ..."

绝不简单回一句 "could you confirm which one?" — 那等于让客户多等一轮 + agent 自己什么都没干。

## Confidence / Needs Review 字段使用禁忌

`zendesk_internal_reply` 的 summary 字段直接决定 SE 看到的"⚠️ Low confidence"警告：

- ❌ **不要因为'怕错'打 medium 兜底** — 有 DB/源码/历史工单证据就该 high
- ❌ **不要因为'怕担责'设 needs_human=true** — 见 schema 内的判定标准，只有 refund/账号操作/真正定位不到的才该 true
- ✅ 大多数充分调查过的工单：confidence=high + needs_human=false（让 SE 可以直接 forward）
- ✅ 极少数特殊场景（退款 / 不可重现的 platform_issue）：confidence=medium/low + needs_human=true，**且 needs_human_reason 必须写具体卡点**（不许"建议复核"这种空话）

## 结构禁忌

- ❌ 一句话能说清的写三段
- ❌ 先解释原理再说结论（客户要结论，不要科普）
- ❌ 罗列 ≥4 个 workaround / 选项（最多 2-3 条按优先级）
- ❌ 给客户 SQL / 命令行操作建议（除非客户明确是开发者）
- ❌ 在没拿到统计数据的情况下说"the issue is real but we need more diagnosis" — 这是 Quality Bar 第 2 条不通过的信号，必须重跑 SQL

## 跨语言禁忌

- ❌ EN + 中文双语对照输出
- ❌ 给日语 / 葡语 / 韩语客户输出对应母语（一律 English 兜底）
- ❌ 在英文回复里突然冒出中文词（除非引用工单内的中文原文）
