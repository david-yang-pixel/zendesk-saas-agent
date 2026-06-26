# 平台/稳定性问题处理策略

客户报告 SaaS 平台层故障（5xx / 超时 / 503 / WAF 拦截 / 区域性访问问题）。

## ⚡ Subagent A 必查路径

| 仓库 | 路径 |
|---|---|
| dify | `api/core/*` — 核心运行时（最常出 5xx 的地方）|
| dify | `api/services/*` — 业务调用 |
| dify | `api/controllers/*` — endpoint 层 |
| saas-deploy | `*` — 集群 / nginx / WAF / 资源限制配置 |

⚡ **日志 subagent 必派**（仅 platform_issue 类）：
- SigNoz：错误日志 severity=ERROR + trace

---

## 🚨 批量故障关联（platform incident 信号）

30 分钟内如果出现 >=2 个同类工单（相同错误文本、同一功能模块、相同
endpoint / 状态码、或同一时间段集中爆发），不要按单个客户逐个排查，直接按
platform incident 信号处理：

1. 先用 `zendesk_search` 查近 30-60 分钟同类工单，并用 `ticket_record_search`
   补历史相似案例，确认是否已有同类模式。
2. 命中 >=2 个同类工单后，立即查全局指标：请求错误率、5xx/超时、相关
   service/deployment 的 SigNoz logs/traces/metrics、worker/queue 和 pod 重启/OOM。
   不要只查单个 tenant。
3. 全局指标异常或多工单信号持续扩大时，升级 SRE，`feishu_push` 用 alert /
   ticket_escalate 携带 ticket 列表、时间窗、SigNoz/指标摘要和当前客户 draft。

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

### 3. 环境过滤不要先入为主

做广谱聚合、schema 探测、或 0 行复查时，不要默认加 `--env production` 或直接写死
`deployment.environment = 'production'` 到 `signoz_query`。部分 logs / traces / metrics
没有稳定写入 `deployment.environment`，硬过滤会把真实数据过滤掉。`signoz_errors` /
`signoz_services` 这类便利工具若 0 行，必须用不带 env 的 `signoz_query` 交叉验证。

正确顺序：
1. 先不加 env 过滤，用 `signoz_services` 或 `signoz_query` 聚合当前窗口的 service /
   deployment / environment 分布。
2. 只有确认字段存在且该窗口有环境标签时才加环境过滤。
3. 如果必须区分 prod/staging，先在结果里展示各 environment 的 count，再选择下钻。

### 4. 时间范围必须显式设

不要依赖工具默认时间窗（一般是 1h 或 24h）：

- 客户报"5 分钟前报错" → `now-15m to now`
- 客户报"今天上午" → 明确 ISO 时间窗
- 客户给具体时间戳 → 前后扩 ±5 min 容错

### 5. 聚合先于搜索

**先 count(*) group by k8s.deployment.name + severity** 看错误分布，**再**钻具体日志。

直接 list 几百条肉眼看：容易漏模式 + 浪费 token。`signoz_errors` limit 50 一把抓不到全貌，应该先用 `signoz_query` 跑 `count(*) ... GROUP BY resources_string['k8s.deployment.name'], severity_text` 看分布。

### 6. 0 行不是无异常

第一次查 0 行 → **不要立即下"无异常"结论**。0 行不是无异常，通常只是
service / deployment / env / 字段 / 时间窗其中一个条件错了。

排查顺序：
1. 先用 `signoz_services` 列出当前窗口所有活跃 service / k8s deployment。
2. 再用 `signoz_query` 聚合 service、deployment、severity、环境标签和时间分布。
3. 必要时用 `signoz_query` 跑 `DESCRIBE signoz_logs.distributed_logs_v2`；如果查
   traces 或 metrics，也先 DESCRIBE 对应 signoz_traces / signoz_metrics 表，确认真实列名。
4. 对照客户描述调整 service / deployment / 时间窗后再下钻。
5. 所有 MCP 查询都失败，才写"SigNoz MCP 不可用，未拿到监控数据"；不要使用本地
   `sz` CLI 或 SigNoz Web UI 兜底。

### 7. 镜像版本确认

追问"客户是不是在某个特定 hotfix 上"时：

```
查 container.image.name = 'langgenius/dify-api'
取 container.image.tag 字段 → 拿到具体 hotfix 镜像 tag
```

可以用来对照客户报错时间点 prod 实际部署的版本。

### 8. SigNoz 不只是 logs

SigNoz MCP 的能力面包括 logs、traces、metrics、services、schema 和 ClickHouse SQL。
不要只跑 `signoz_errors` 后就结束：
- API 5xx / traceback → logs + traceback
- 慢请求 / endpoint 退化 → traces 或 ClickHouse 聚合
- worker / queue / pod 行为 → deployment 维度 logs + metrics 聚合
- 字段不确定 → schema / DESCRIBE

如果连续多个查询返回 `rowsScanned: 0` 且**全部** signoz 工具都查不到任何 service /
log / trace / metric → 怀疑 ingest 端故障，参考之前 #2375 处理路径（不是 agent 自己能修，需要 SaaS observability 同事介入）。

---

## 关键步骤

1. 确认客户 Workspace ID、出错时间段、具体端点/路径
2. **并行派发 3 个 subagent**：A 源码 + B GitHub + C SigNoz
   - 派 SigNoz 时**必须**把上面 SigNoz 规则的关键点写进 subagent 任务描述（特别是 service.name 的 prod/staging 差异 + k8s.deployment.name 区分 worker）
3. SQL 查 workspace_runs / 相关业务表，看故障是否单 tenant
4. 看 SigNoz trace 里 5xx 的实际异常 stack
5. 如是广泛故障 → 标记 incident，建议 escalate SRE

## 回复原则

- 告知客户已确认，给时间预期（不承诺精确 ETA）
- 有 PR 修复则引用；workaround 明确给步骤
- 如需 SRE 操作 → 内部备注里标注 + 飞书 alert
- 引用具体 SigNoz 数字时必带 `service.name` + `k8s.deployment.name` + 时间窗，不能只写"看 SigNoz 有这个错误"含糊带过
