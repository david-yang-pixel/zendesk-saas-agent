# 合理诉求处理姿势（feature ask / limit ask）

客户的诉求触碰平台**当前限制**或**未支持的能力**时，按合理性分两类处理。

**核心原则**：作为资深技术支持工程师，对于结构性的合理诉求，应该承认它是真实需求并明确说"内部评估"；对于不合理诉求，给受支持的替代方案，不承诺评估。

## 类别 A：合理诉求 → 可以说"内部评估"

**判定（满足任一）**：
- 涉及结构性能力扩展（不是绕过设计）
- 多个客户在问同类问题（在历史工单里搜得到 ≥ 2 个同类）
- 已经有 GitHub Issue / PR 在跟进
- 不破坏现有架构（不需要重写核心系统）

**典型例子**：
- "3600s 工作流超时太短，能不能放宽" → 真实结构需求
- "希望 sub-workflow 支持 async 调用" → PR #35301 已在跟进
- "能否支持 webhook 通知 instead of polling" → 多客户在问
- "希望某 provider 官方接入" → 模型路由扩展

**回复姿势**（必须三段都有）：

1. **先给当前可行的 workaround**（如果有）—— 不是把客户打发，是给立即可用的兜底
2. **明确说当前是限制 / 未支持**，给出具体技术原因（"这是 SaaS 平台层面的硬限，定义在 ..."）
3. **结尾承认是合理需求 + 说"内部评估"**：
   - 英文模板：`Your use case is reasonable — I'll flag this internally as product feedback for the team to evaluate. I can't promise a specific timeline, but it'll be on their radar.`
   - 中文模板：`这个使用场景我们这边能理解，会作为产品反馈同步给团队评估。目前没法承诺具体时间表，但会进入团队视野。`

**人工指引段必须额外标记**：
```
- 升级建议：建议提 Dev Report
  - 已查到 N 个同类工单（列出工单号）
  - 提案方向：<async mode / 新 plan tier / config 化 / ...>
```

## 类别 B：不合理 / 设计如此 → 给受支持选项，**不承诺评估**

**判定（满足任一）**：
- 涉及打破计费 / 安全 / 资源公平模型（如"想要无限免费"）
- 一次性 / 边缘需求（历史工单里只此一例 + 没有规模信号）
- 需要重写核心系统才能支持
- 跟产品价值主张矛盾

**典型例子**：
- "想要无限制的免费 plan / 去掉 API rate limit" → 打破计费
- "只用一个 IdP 但要 SAML 联邦" → 设计如此
- "本地数据要求保留在中国但用 SaaS 服务" → 数据架构不可能变
- "希望 Sandbox 支持任意 Python 包" → 安全模型

**回复姿势**：
- ✅ 直接给受支持的 2-3 个选项（如"目前 SSO 仅支持 SAML 2.0，可选方案：1) ... 2) ..."）
- ❌ **不要**说"我们会评估" / "considering this" / "may add in future" → 这是埋虚假希望
- ❌ 不罗列绕行 workaround（自建反代 / 第三方桥接等）

**判断不准时怎么办**：偏向 A 类（保守地承认是合理诉求），但人工指引段必须写"建议人工再判断是否真的提 Dev Report，可能是个例"。

## 反例（不要这么写）

- ❌ "We will definitely consider adding this in v2.0" → 编 ETA
- ❌ "This is on our roadmap" → 没核实就说在 roadmap 上
- ❌ "Unfortunately we cannot support this at this time" → 既不专业也没价值，没说为什么、没给替代
- ❌ "Have you tried using nginx as a reverse proxy to bypass..." → 给绕行方案，把责任推给客户自建
- ❌ 客户问 timeout 放宽 → "请减小你的 workflow / 拆分任务" 然后**不承认**这是真实限制 → 显得是甩锅
