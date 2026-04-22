# 工作流问题处理策略

客户报告：workflow 执行失败、节点超时、节点变量未传递、调度问题。

## 关键步骤
1. 收集：Workspace ID、Workflow ID、Run ID（如可从截图/日志提取）、具体节点
2. SQL 直查（sql_query database=prod）：workflow_runs + workflow_node_executions
3. 并行派发 subagent：
   - 源码（dify 仓库 → 对应节点类型实现）
   - GitHub Issues/PRs（关键词：节点名 + 错误信息）
   - 历史工单（ticket_record_search）
   - SigNoz worker 日志（若涉及异步执行）
4. 参考本地知识库 `saas版知识库/03-workflow.md`

## 常见根因
- 变量作用域 / 类型不匹配
- HTTP Request 节点超时 / 证书
- LLM 节点配额 / 模型不可用
- 循环/分支逻辑错误
