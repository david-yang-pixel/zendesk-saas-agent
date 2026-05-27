# Subagent 派发指南

## 核心原则

**默认不派 subagent —— 主 agent 直接调 tool 更快**。
只有以下情况才派 subagent：
- 需要**并行查 ≥2 个完全不相关角度**（如同时查源码 + Grafana + SigNoz）
- 单次 tool 输出可能很大（如 SigNoz raw search），让 subagent 在独立 context 处理后返回精简结论
- 任务复杂，需要多轮 tool 调用闭环（subagent 在自己的 message_store 闭环，不污染主 loop）

## Subagent 预算（每个工单累计）

| 复杂度 | 触发条件 | 累计 subagent 数 |
|---|---|---|
| **simple** | usage-howto / product_usage / yes-no | **0-1** |
| **medium** | billing / refund / api / permissions / knowledge_base | **1-3** |
| **complex** | platform_issue / workflow / 多组件交互 / 数据驱动诊断 | **2-5** |

## 派 subagent 必须传 `tools` 字段（小子集 1-3 个）

每多一个 tool 多 150-800 tokens schema。例子：
- task='搜 dify-billing 代码' → `tools=['git_grep']`
- task='查 SigNoz 错误' → `tools=['signoz_search_logs', 'signoz_aggregate_logs']`
- task='看 GitHub PR' → `tools=['github_search_prs', 'github_get_pr']`

**❌ 不要传 10 个 tools** — 每个 subagent 应该只做一件事。

## Task 描述必含"完成判定"段

```
完成判定：
- 找到 X (匹配关键词 Y) → 立即返回，第一句"FOUND: ..."
- 查了 3 个角度仍 0 hit → 立即返回 "NOT_FOUND: tried [Y, Y2, Y3]"
- 6 轮预算用完 → 强制结束（subagent 自己有 guardrail）
```

为什么：subagent 收到模糊任务会无脑跑 10+ 轮挖到 token 爆。明示"什么算找到 / 找不到"让 subagent 知道何时 return。

**✅ 好例子**: task = "在 dify-billing/app/services/billing/* 搜 'refund' / 'cancel_subscription' 关键词。完成判定：找到 1 个匹配函数 → return FOUND + 关键 line；3 个目录搜完 0 hit → return NOT_FOUND，列已搜路径。预算 ≤ 6 轮"

**❌ 坏例子**: task = "搜 SaaS API 文件上传相关代码"（开放无止境）

## 按 category 的必查路径（派 subagent 时粘到 task 描述）

| Category | 必查路径 |
|---|---|
| **billing / refund** | `dify-billing/app/services/billing/*` / `dify-billing/app/services/dify/provider_service.py` / `dify/api/services/billing/*` / `dify/api/services/credit_pool_service.py` / `dify/api/core/app/llm/quota.py` |
| **platform_issue** | `dify/api/core/*` / `dify/api/services/*` / `dify/api/controllers/*` + Grafana / SigNoz |
| **workflow** | `dify/api/core/app/workflow/*` / `dify/api/services/workflow_*` / `dify/api/models/workflow.py` |
| **api** | `dify/api/controllers/*` / `dify/api/services/*` / **插件类（Lark/Notion/Slack/Google Drive） → `github_code_search scope="langgenius/dify-official-plugins"`**（本地没 clone）|
| **knowledge_base** | `dify/api/services/dataset_service.py` / `document_service.py` / `dify/api/core/rag/*` |
| **permissions** | `dify/api/services/account_service.py` / `workspace_service.py` / `dify/api/models/account.py` |
| **product_usage** | KB 优先（不要硬挖源码） |

## Subagent 工作策略（源码搜索类）

1. 先用 `git_grep` 在本地 5 个仓库（dify / dify-billing / dify-marketplace-backend / dify-marketplace-frontend / saas-deploy）搜
2. 本地搜不到 → **立刻** `github_code_search query="..." scope="langgenius"` 覆盖第三方插件仓
3. 拿到匹配后用 `github_get_pr` / `github_search_prs` 看看有没有 fix PR

## 反例（绝不能这样）

- "billing 类工单只搜了 dify 主仓 i18n + workspace_service" → 漏掉 dify-billing 是真理之源
- "插件类问题在本地 dify 仓搜了 0 hit 就下结论代码里没有" → 必须 github_code_search dify-official-plugins
- "git_grep 命中 1 个文件就停" → 要把所有命中文件的关键代码读完再 summarize
