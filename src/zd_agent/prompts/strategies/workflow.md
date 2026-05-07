# 工作流问题处理策略

客户报告：workflow 执行失败、节点超时、节点变量未传递、调度问题、
"输入变量后卡在 loading" 这类前端假死、**workflow 整体变慢 / LLM 节点变慢**。

---

## ⚡ Subagent A 必查路径（源码侧）

| 仓库 | 路径 | 看什么 |
|---|---|---|
| dify | `api/core/app/workflow/*` | workflow 引擎 / orchestrator / scheduler |
| dify | `api/core/workflow/*` | 节点定义 / variable resolver / iteration |
| dify | `api/services/workflow_service.py` | workflow 业务入口 |
| dify | `api/services/workflow_run_service.py` | workflow 运行/重试 |
| dify | `api/models/workflow.py` | Workflow / WorkflowRun / WorkflowNodeExecution schema |
| dify | `api/models/model.py` | Message / MessageProvider 字段（**provider/model latency 列名以此为准**） |
| dify | `web/app/components/workflow/*` | 前端 workflow UI（"卡 loading"类问题在这里） |

---

## 🎯 数据驱动诊断 SOP（必跑，按顺序）

> **铁律 1：找基线**。看慢 run / 慢节点之前，**必须先找一个正常状态参考样本**做对比，否则结论无法收敛。
>
> **铁律 2：必须 GROUP BY 一个有意义的维度**（provider / model / node_type / hour-of-day），不许只看单个 run / 单条记录就下结论。
>
> **铁律 3：必须给出 count + p50 + p90 + max 至少 4 个统计量**，没有这些统计量的"慢/快"判断都是猜测。

### Step A — workflow_runs 总览（首跑）

拿到 workspace_id 后，先看慢分布：

```sql
-- [Dify-Prod] 该 workspace 最近 7d workflow_runs 分布（按 elapsed_time 分位数）
SELECT
    app_id,
    COUNT(*)                                                            AS runs,
    percentile_cont(0.5)  WITHIN GROUP (ORDER BY elapsed_time)::numeric(10,2) AS p50_s,
    percentile_cont(0.9)  WITHIN GROUP (ORDER BY elapsed_time)::numeric(10,2) AS p90_s,
    MAX(elapsed_time)::numeric(10,2)                                          AS max_s
FROM workflow_runs
WHERE tenant_id = '<workspace_id>'
  AND created_at > now() - interval '7 days'
  AND status = 'succeeded'
GROUP BY app_id
ORDER BY p90_s DESC;
```

### Step B — 找正常 run 做对照基线（必做！）

如果客户提到了具体慢 run 或 app，**主动**找同一 app 的正常 run 做基线，否则你的"慢"没有参考系：

```sql
-- 同 app 正常 run（耗时 <30s）样本，作为对照基线
SELECT id, elapsed_time, status, created_at
FROM workflow_runs
WHERE app_id = '<app_id>'
  AND status = 'succeeded'
  AND elapsed_time < 30
ORDER BY created_at DESC
LIMIT 5;
```

### Step C — 节点级耗时分布（workflow_node_executions）

哪个节点是慢点？按节点 type / title 聚合：

```sql
-- [Dify-Prod] 节点级耗时分布（同 app 最近 7d，慢点定位关键查询）
SELECT
    node_type,
    title,
    status,
    COUNT(*)                                                          AS executions,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_time)::numeric(10,2) AS p50_s,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY elapsed_time)::numeric(10,2) AS p90_s,
    MAX(elapsed_time)::numeric(10,2)                                  AS max_s
FROM workflow_node_executions wne
JOIN workflow_runs wr ON wne.workflow_run_id = wr.id
WHERE wr.app_id = '<app_id>'
  AND wr.created_at > now() - interval '7 days'
GROUP BY node_type, title, status
ORDER BY p90_s DESC NULLS LAST
LIMIT 30;
```

**判读**：
- 如果某个 `node_type='llm'` 的 p90 比同 workflow 其他节点高 10x 以上 → 慢点确认是 LLM 节点 → **必须接着跑 Step D**
- 如果 `node_type='code' / 'http-request' / 'tool'` 的 p90 异常 → 走对应方向（不需要跑 Step D）
- 所有节点 p90 都低但 workflow_runs.elapsed_time 高 → workflow 引擎 orchestration 开销 / parallel iteration（看 PR #33216）

### Step D — provider/model 维度聚合（LLM 节点慢必跑！）

**这一步是 workflow LLM 慢类工单的关键证据。** 没跑这条 SQL 就给客户回复 = 不及格。

```sql
-- [Dify-Prod] 该 workspace 最近 7d 所有 LLM 调用按 provider/model 聚合
-- 列名以 messages 表实际 schema 为准（参考 system prompt 里的 database_schema 段）
-- 常见列：model_provider, model_id (or model_name), provider_response_latency
SELECT
    model_provider,
    model_id,
    COUNT(*)                                                                          AS calls,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p50_s,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p90_s,
    MAX(provider_response_latency)::numeric(10,2)                                     AS max_s,
    AVG(message_tokens + answer_tokens)::int                                          AS avg_tokens
FROM messages
WHERE app_id = '<app_id>'           -- 或 tenant_id = '<workspace_id>' 范围更广
  AND created_at > now() - interval '7 days'
  AND provider_response_latency IS NOT NULL
GROUP BY model_provider, model_id
ORDER BY calls DESC;
```

**判读**：
- 单一 provider/model 的 p90 远高于其他 → **provider 侧问题**，回复要写 provider 名 + p50/p90/max 的具体数字
- 该 provider 的 max 拖了长尾（如 max=900s, p90=18s）→ **provider 侧偶发抖动 / 重试 / 限流**
- avg_tokens 高的（>50k）配合长尾大 → **大上下文请求 + 代理通道 = 不稳定**

### Step E — 跨 provider 横向对比（如果同 tenant 有备选 provider）

如果 Step D 显示 qiniu/某代理 慢，**主动**找同 tenant 是否还跑过其他 provider 做对比：

```sql
-- [Dify-Prod] 同 tenant 同 model 在不同 provider 上的 p50 对比
SELECT
    model_provider,
    model_id,
    COUNT(*) AS calls,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p50_s
FROM messages
WHERE tenant_id = '<workspace_id>'
  AND model_id ILIKE '%<慢的 model 关键词，比如 gemini>%'
  AND created_at > now() - interval '7 days'
  AND provider_response_latency IS NOT NULL
GROUP BY model_provider, model_id
ORDER BY p50_s ASC;
```

如果同 model 走官方 provider p50=2.6s 而走代理 p50=5.7s → 客户回复直接给数字，建议切官方 provider。

### Step F — token-vs-latency 关联（解释长尾）

如果 Step D 的 max 远大于 p90（长尾很长），看是不是大上下文请求拖慢的：

```sql
-- [Dify-Prod] 慢调用的 token 分布（>60s 的）
SELECT
    message_tokens + answer_tokens AS total_tokens,
    provider_response_latency::numeric(10,2)         AS latency_s,
    model_provider,
    model_id,
    created_at
FROM messages
WHERE app_id = '<app_id>'
  AND created_at > now() - interval '7 days'
  AND provider_response_latency > 60
ORDER BY provider_response_latency DESC
LIMIT 20;
```

---

## 🔧 SQL 失败降级铁律

任何 SQL 报错都不要直接放弃，按下面顺序 retry：

1. **`column "X" does not exist`** → 不要硬猜；先跑 schema 拿真实列名：
   ```sql
   SELECT column_name, data_type FROM information_schema.columns
   WHERE table_name = 'messages' ORDER BY ordinal_position;
   ```
   或查 system prompt 里的 `## 数据库 Schema` 段（已注入真实 schema）。
2. **`ORDER BY "index"` 这类关键字列名报错** → 改用 `ORDER BY ordinal_position` 或干脆去掉 ORDER BY 先跑通联。
3. **多 OR 条件 / LIKE % 开头超时** → 拆成多次 IN 查询，或用单条 run_id 替代模糊匹配。
4. **JOIN 字段不确定** → 先 `SELECT count(*) FROM table_a JOIN table_b ON ... LIMIT 1` 验通联，再加列。
5. **任何 SQL 失败 → 必须 retry 至少一次简化版**，**不许直接放弃跑下一个工具**。

---

## 关键步骤总览

1. **First-Pass 信息抓取**：Workspace ID、App ID（或 Workflow ID / Run ID 反查 app）、慢的具体表现、出现时间窗
2. **跑 Step A → B → C** 确定慢点在 workflow 引擎、还是节点级、还是哪个具体节点
3. **如果慢点在 LLM 节点 → 必须跑 Step D（provider 聚合）+ Step E（跨 provider 对比）**
4. **客户说"卡在 loading 不动"** → 多半是前端 SSE / polling 问题：
   - 看截图 URL 推断 app 类型
   - SQL 查 workflow_runs 看后端有没有真创建 run
   - 后端有 run + 前端没动 → web 仓的前端 polling
   - 后端没 run → workflow_id / wid 错 / API call 没到
5. **并行 subagent**：
   - A. 源码（按上表）
   - B. GitHub Issues/PRs（搜慢相关 PR：parallel iteration、SSE、streaming、provider timeout）
   - C. 历史工单（ticket_record_search 用同义词扩展："slow" / "latency" / "timeout" / "卡" / "慢" / "耗时"）
   - D. SigNoz worker 日志（若涉及异步执行；SigNoz 集群当前可能 ingest 异常，0 行不能反证"无日志"）
6. **参考本地知识库** `saas版知识库/03-workflow.md`

---

## 客户回复必含元素（workflow 慢类工单）

按 SOUL.md Quality Bar 强制：

| 元素 | 写得出 | 写不出 → 重跑 |
|---|---|---|
| 慢点的具体定位（哪个节点 / 哪个 provider） | "issue is the LLM node X using provider qiniu" | 没跑 Step C/D，回去补 |
| 4 个统计量（calls + p50 + p90 + max） | "1580 calls, p50=5.7s, p90=17.7s, max=907s" | 没跑 Step D，回去补 |
| 至少一个对照基线 | "Gemini official provider p50=2.6s vs qiniu 5.7s" | 没跑 Step E（如有备选 provider），回去补 |
| 1-3 条具体可执行建议 | "试试切官方 provider / 联系 Qiniu support / 减小 context" | 没具体动作，回去想 |

**禁止**输出："The slowdown is real, but we need more diagnosis to find the root cause." —— 这是没跑数据查询的偷懒回复。

---

## 常见根因（按概率排序）

1. **特定 provider/model 慢/限流**（最常见）— 通过 Step D 定位，回复带 provider 名 + 数字
2. **大上下文（>50k token）在代理通道上不稳定** — Step F 验证
3. **变量作用域 / 类型不匹配**（节点引用了不存在的 variable_node_path）
4. **HTTP Request 节点超时 / 证书**
5. **循环/分支逻辑错误**
6. **parallel iteration timing 失真**（已修：PR #33216）
7. **前端假死**：SSE 断了但 UI 没重连提示
8. **wid 不存在**（截图里大概率有真实 wid，先看图！）
