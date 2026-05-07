# 工作流问题处理策略

客户报告：workflow 执行失败、节点超时、节点变量未传递、调度问题、
"输入变量后卡在 loading" 这类前端假死。

## ⚡ Subagent A 必查路径

| 仓库 | 路径 | 看什么 |
|---|---|---|
| dify | `api/core/app/workflow/*` | workflow 引擎 / orchestrator / scheduler |
| dify | `api/core/workflow/*` | 节点定义 / variable resolver / iteration |
| dify | `api/services/workflow_service.py` | workflow 业务入口 |
| dify | `api/services/workflow_run_service.py` | workflow 运行/重试 |
| dify | `api/models/workflow.py` | Workflow / WorkflowRun / WorkflowNodeExecution schema |
| dify | `web/app/components/workflow/*` | 前端 workflow UI（"卡 loading"类问题在这里） |

## 关键 SQL（参考 prompt 顶部 Database Schema 段拿真实列名）
- `workflow_runs WHERE id = '<run_id>'` — 拿 status / error_message / total_steps
- `workflow_node_executions WHERE workflow_run_id = '<run_id>' ORDER BY created_at` — 节点级 trace
- `apps WHERE workflow_id = '<wid>'` — 反查 app

## 关键步骤
1. 收集：Workspace ID、Workflow ID、Run ID（截图/日志/对话里抠）、具体节点
2. SQL 直查 workflow_runs + workflow_node_executions（ID 不全也要按 tenant_id + 时间窗查最近 10 条）
3. **客户说"卡在 loading 不动"** → 多半是前端 SSE / polling 问题：
   - 看截图 URL 推断 app 类型
   - SQL 查 workflow_runs 看后端有没有真创建 run
   - 后端有 run + 前端没动 → web 仓的前端 polling
   - 后端没 run → workflow_id / wid 错 / API call 没到
4. 并行 subagent：
   - A. 源码（按上表）
   - B. GitHub Issues/PRs
   - C. 历史工单（ticket_record_search "workflow loading" / "stuck" / "卡住"）
   - D. SigNoz worker 日志（若涉及异步执行）
5. 参考本地知识库 `saas版知识库/03-workflow.md`

## 常见根因
- 变量作用域 / 类型不匹配（节点引用了不存在的 variable_node_path）
- HTTP Request 节点超时 / 证书
- LLM 节点配额 / 模型不可用
- 循环/分支逻辑错误
- **前端假死**：SSE 断了但 UI 没重连提示
- **wid 不存在**（截图里大概率有真实 wid，先看图！）
