# 平台/稳定性问题处理策略

客户报告 SaaS 平台层故障（5xx / 超时 / 503 / WAF 拦截 / 区域性访问问题）。

## ⚡ Subagent A 必查路径

| 仓库 | 路径 |
|---|---|
| dify | `api/core/*` — 核心运行时（最常出 5xx 的地方）|
| dify | `api/services/*` — 业务调用 |
| dify | `api/controllers/*` — endpoint 层 |
| saas-deploy | `*` — 集群 / nginx / WAF / 资源限制配置 |

⚡ **Subagent C/D 必派**（仅 platform_issue 类）：
- C. Grafana：请求成功率 / Pod 状态 / 资源饱和度
- D. SigNoz：错误日志 severity=ERROR + trace

## 关键步骤
1. 确认客户 Workspace ID、出错时间段、具体端点/路径
2. **并行派发 4 个 subagent**：A 源码 + B GitHub + C Grafana + D SigNoz
3. SQL 查 workspace_runs / 相关业务表，看故障是否单 tenant
4. 看 SigNoz trace 里 5xx 的实际异常 stack
5. 如是广泛故障 → 标记 incident，建议 escalate SRE

## 回复原则
- 告知客户已确认，给时间预期（不承诺精确 ETA）
- 有 PR 修复则引用；workaround 明确给步骤
- 如需 SRE 操作 → 内部备注里标注 + 飞书 alert
