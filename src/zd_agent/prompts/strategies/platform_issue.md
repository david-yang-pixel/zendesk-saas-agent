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

---

## 🔍 SigNoz 查询规则（避免误报"无异常"）

> 服务器跑 SigNoz MCP 时，最容易踩的坑是**搜错 service.name → 0 结果 → 误判客户报的故障"我们这边没看到异常"**。下面是死规则，必须按顺序遵守：

### 1. service.name 在 prod / staging 名字不一样

| 服务 | Production | Staging |
|---|---|---|
| Dify API | `dify-api-oltp` | `dify-api` |
| Plugin Daemon | `dify-plugindaemon` | `dify-plugin-daemon` |
| Billing | `dify-billing` | `dify-billing` |

> 客户工单 99% 是 prod 的问题。**优先用 prod 名字**；找不到再退到 staging 名字。

### 2. API 与 Celery Worker 共享 `service.name = langgenius/dify`

同一份代码跑不同入口，光按 service.name 区分不出 API / 各个 worker。**必须用 `k8s.deployment.name` 进一步过滤**：

| Deployment 名 | 跑什么 |
|---|---|
| `dify-api-oltp` | HTTP API |
| `dify-app-worker` | 应用消费（workflow / chat / completion 实际跑这里）|
| `dify-dataset-worker` | 知识库索引 |
| `dify-trigger-worker` | 触发器 |
| `dify-mail-worker` | 邮件发送 |

**典型例子**：客户报"工作流提交后 pending 不动" → 查 `k8s.deployment.name = dify-app-worker`，**不是** `dify-api-oltp`（API 早就把请求 enqueue 给 worker 了）

### 3. 每次查询必加环境过滤

```
deployment.environment = 'production'   ← 99% 的工单
deployment.environment = 'staging'      ← 内部联调时
```

SigNoz 是单租户多环境共用，不加过滤会把 prod / staging 日志混搅在一起，结论失真。

### 4. 时间范围必须显式设

不要依赖工具默认时间窗（一般是 1h 或 24h）：

- 客户报"5 分钟前报错" → `now-15m to now`
- 客户报"今天上午" → 明确 ISO 时间窗
- 客户给具体时间戳 → 前后扩 ±5 min 容错

### 5. 聚合先于搜索

**先 count(*) group by k8s.deployment.name + severity** 看错误分布，**再**钻具体日志。

直接 list 几百条肉眼看：容易漏模式 + 浪费 token。signoz_search_logs limit 50 一把抓不到全貌，应该先用 signoz_aggregate_logs 看分布。

### 6. 找不到结果时的 fallback

第一次查 0 行 → **不要立即下"无异常"结论**：
1. 先用 signoz_get_field_values group by `service.name` 列出当前环境所有活跃服务名
2. 对照客户描述匹配（service.name 可能拼写有差异）
3. 实在找不到才说"未在 SigNoz 看到对应服务的日志"

### 7. 镜像版本确认

追问"客户是不是在某个特定 hotfix 上"时：

```
查 container.image.name = 'langgenius/dify-api'
取 container.image.tag 字段 → 拿到具体 hotfix 镜像 tag
```

可以用来对照客户报错时间点 prod 实际部署的版本。

### 8. SigNoz 集群本身的健康检查

如果连续多个查询返回 `rowsScanned: 0` 且**全部** signoz 工具都查不到任何 service / log / metric → 怀疑 ingest 端故障，参考之前 #2375 处理路径（不是 agent 自己能修，需要 SaaS observability 同事介入）。

---

## 关键步骤

1. 确认客户 Workspace ID、出错时间段、具体端点/路径
2. **并行派发 4 个 subagent**：A 源码 + B GitHub + C Grafana + D SigNoz
   - 派 D 时**必须**把上面 SigNoz 规则的关键点写进 subagent 任务描述（特别是 service.name 的 prod/staging 差异 + k8s.deployment.name 区分 worker）
3. SQL 查 workspace_runs / 相关业务表，看故障是否单 tenant
4. 看 SigNoz trace 里 5xx 的实际异常 stack
5. 如是广泛故障 → 标记 incident，建议 escalate SRE

## 回复原则

- 告知客户已确认，给时间预期（不承诺精确 ETA）
- 有 PR 修复则引用；workaround 明确给步骤
- 如需 SRE 操作 → 内部备注里标注 + 飞书 alert
- 引用具体 SigNoz 数字时必带 `service.name` + `k8s.deployment.name` + 时间窗，不能只写"看 SigNoz 有这个错误"含糊带过
