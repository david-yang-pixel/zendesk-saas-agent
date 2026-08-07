# 高频工单快速分诊 Playbook（SaaS）

Step 2 快速通道匹配本文。命中者跳过 Step 3-18，直接 Step 19 输出。

---

## 1. 取消订阅 / 退订（非退款）

**触发**：客户说 "how to cancel my subscription" / "取消订阅"
**处理**：
- 回复引导到 Console 操作路径：`Settings → Billing → Cancel Subscription`
- 告知取消后的期限（通常到当前计费周期末）
- 不需要走完整 SQL 查询
- 如同时要求退款 → 进入 refund 流程，**不属于本 playbook**

---

## 2. 获取 API Key

**触发**：客户问 "where can I find my API key"
**处理**：
- 路径：`Console → App → API Access → API Keys`
- Application key vs Service key 的区别
- 附 Help Center 文档链接

---

## 3. 密码重置 / 登录问题

**触发**："can't login" / "password reset"
**处理**：
- 普通账号：指向登录页 "Forgot password"
- SSO 账号：联系 Workspace 管理员 / 检查 SSO IdP
- **不要走 SQL 查询** 除非涉及 workspace 归属问题

---

## 4. IP 白名单配置

**触发**："IP whitelist" / "restrict access by IP"
**处理**：
- 告知 SaaS 目前不支持用户侧 IP 白名单（产品层面）
- 建议使用 API Key 的权限 scoping 替代
- 如是企业客户需求 → 引导到企业版（但本 agent 不处理 enterprise tag，所以这种情况很少）

---

## 5. Workspace 邀请未收到

**触发**："invitation email not received"
**处理**：
- 先用表单 Email 支持侧解析 workspace 并确认被邀请邮箱；邮箱唯一映射时直接使用，不再向客户索取 Workspace ID / workspace name，只有验证后的真实歧义才澄清
- 建议检查垃圾邮件 / 过滤规则
- SQL 查：`accounts`（被邀请人是否已存在）+ `tenant_account_joins`（邀请状态）
- 可能走完整流程（不是纯 playbook 案例）

---

## 6. 文档索引卡住 / indexing stuck

**触发**：Document 长时间处于 `indexing` / `queuing` 状态
**处理**：
- SQL 查 `documents.indexing_status` + `document_segments.status`
- 如是大文件（>10MB）→ 是正常现象，告知预期时间
- 如卡住 >1h → 真实问题，走完整流程

---

## 7. 模型不可用（401/403 from LLM provider）

**触发**："model not available" / "OpenAI 401"
**处理**：
- 用户用的是内置模型还是自己的 Key？
- 内置模型 → 查 `tenant_feature_usages` 是否超出配额
- 自己 Key → 让用户检查 Key 状态 + 模型权限
- 不是产品 bug 可直接回复

---

## 8. Dify Cloud 状态 / 是不是挂了

**触发**："is Dify down?" / 平台级故障关键词
**处理**：
- 立即查 SigNoz
- 如有 incident → 回复 "we are aware, investigating"
- 如正常 → 继续走完整流程找客户侧问题

---

## 命中 Playbook 后的 Internal Note

即使走快速通道，也必须产出完整三段式 Internal Note（内部分析 / 建议回复 / 人工指引）——只是分析段可以简短："Matched COMMON_PLAYBOOK #N"。

## 不匹配时

任何不属于这 8 类明显高频的 → 走完整 Step 3-22 流程，不要强行套 playbook。
