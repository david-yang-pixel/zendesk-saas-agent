# 平台/稳定性问题处理策略

客户报告 SaaS 平台层故障（5xx / 超时 / 503 / WAF 拦截 / 区域性访问问题）。

## 关键步骤
1. 确认客户 Workspace ID、出错时间段、具体端点/路径
2. **并行派发 subagent**（run_subagents_parallel）查询：
   - Grafana：请求成功率、Pod 状态（grafana_query_prometheus）
   - SigNoz：错误日志 severity=ERROR（signoz_search_logs）+ trace
   - 历史工单：搜同类故障（ticket_record_search）
3. 如是广泛故障 → 标记 incident，建议 escalate SRE

## 回复原则
- 告知客户已确认，给时间预期（不承诺精确 ETA）
- 有 PR 修复则引用；workaround 明确给步骤
- 如需 SRE 操作 → 内部备注里标注 + 飞书 alert
