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

## 🎯 数据驱动诊断 Skill（必跑，按顺序）

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

哪个节点是慢点？按节点 type / title 聚合。**JOIN 时所有列必须用表别名（`wne.X` / `wr.X`）**，否则 `status` / `created_at` 这些两表都有的列会触发 `column reference is ambiguous` 错误：

```sql
-- [Dify-Prod] 节点级耗时分布（同 app 最近 7d，慢点定位关键查询）
-- 注意：JOIN 后 status / created_at / id / updated_at 都两表都有，必须 wne. 限定
SELECT
    wne.node_type,
    wne.title,
    wne.status                                                                AS node_status,
    COUNT(*)                                                                  AS executions,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY wne.elapsed_time)::numeric(10,2) AS p50_s,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY wne.elapsed_time)::numeric(10,2) AS p90_s,
    MAX(wne.elapsed_time)::numeric(10,2)                                      AS max_s
FROM workflow_node_executions wne
JOIN workflow_runs wr ON wne.workflow_run_id = wr.id
WHERE wr.app_id = '<app_id>'
  AND wr.created_at > now() - interval '7 days'
GROUP BY wne.node_type, wne.title, wne.status
ORDER BY p90_s DESC NULLS LAST
LIMIT 30;
```

**如果 JOIN 太重 / 超时**，退化成不 JOIN 的版本（先拿 run_ids 再二次查）：

```sql
-- 退化版：先拿到 7d 的 run_id 列表，再用 IN 查节点级
WITH runs AS (
  SELECT id FROM workflow_runs
  WHERE app_id = '<app_id>' AND created_at > now() - interval '7 days'
)
SELECT
    node_type, title, status,
    COUNT(*) AS executions,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_time)::numeric(10,2) AS p50_s,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY elapsed_time)::numeric(10,2) AS p90_s,
    MAX(elapsed_time)::numeric(10,2)                                         AS max_s
FROM workflow_node_executions
WHERE workflow_run_id IN (SELECT id FROM runs)
GROUP BY node_type, title, status
ORDER BY p90_s DESC NULLS LAST
LIMIT 30;
```

**判读**：
- 如果某个 `node_type='llm'` 的 p90 比同 workflow 其他节点高 10x 以上 → 慢点确认是 LLM 节点 → **必须接着跑 Step D**
- 如果 `node_type='code' / 'http-request' / 'tool'` 的 p90 异常 → 走对应方向（不需要跑 Step D）
- 所有节点 p90 都低但 workflow_runs.elapsed_time 高 → workflow 引擎 orchestration 开销 / parallel iteration（看 PR #33216）

### Step D — provider/model 维度聚合（LLM 节点慢必跑！）

**这一步是 workflow LLM 慢类工单的关键证据。** 没跑这两条 SQL 就给客户回复 = 不及格。

> **铁律：必须跑两次，先窄后宽。** 单纯 app_id 过滤可能样本量不足（如 30 calls），统计意义弱。
> 必须再用 tenant_id 拉宽到整个 workspace 拿大样本（典型 1000+ calls），两组数据**都**写进 Internal Note。

#### Step D-1：app 范围（窄，定位"这个 workflow 现在用啥 provider"）

```sql
-- [Dify-Prod] 该 app 最近 7d 的 provider/model 聚合
-- 列名以 messages 表实际 schema 为准；不确定时先查 information_schema.columns
-- 常见列：model_provider, model_id, provider_response_latency
SELECT
    model_provider,
    model_id,
    COUNT(*)                                                                              AS calls,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p50_s,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p90_s,
    MAX(provider_response_latency)::numeric(10,2)                                         AS max_s,
    AVG(message_tokens + answer_tokens)::int                                              AS avg_tokens
FROM messages
WHERE app_id = '<app_id>'
  AND created_at > now() - interval '7 days'
  AND provider_response_latency IS NOT NULL
GROUP BY model_provider, model_id
ORDER BY calls DESC;
```

#### Step D-2：tenant 范围（宽，拿大样本统计 + 全局 provider 分布）

```sql
-- [Dify-Prod] 整个 workspace 最近 7d 的 provider/model 聚合（大样本）
SELECT
    model_provider,
    model_id,
    COUNT(*)                                                                              AS calls,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p50_s,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p90_s,
    MAX(provider_response_latency)::numeric(10,2)                                         AS max_s,
    AVG(message_tokens + answer_tokens)::int                                              AS avg_tokens
FROM messages
WHERE tenant_id = '<workspace_id>'
  AND created_at > now() - interval '7 days'
  AND provider_response_latency IS NOT NULL
GROUP BY model_provider, model_id
ORDER BY calls DESC
LIMIT 30;
```

**判读两组数据**：
- D-1 calls 少（< 50）→ 窄样本，p50/p90 不稳；以 D-2 大样本为主结论
- D-2 中某个 provider/model 的 p90 远高于其他 → 只确认“该 provider/model 边界在本样本中更慢”；**不能据此直接写成 provider 侧根因**
- 该 provider 的 max 拖了长尾（如 max=900s, p90=18s）→ 只确认长尾相关性；重试、限流、上游抖动仍是待验证假设
- avg_tokens 高的（>50k）配合长尾大 → 只确认 token 与延迟相关；大上下文是否为触发条件**仍需继续排查**
- 在根因结论前，继续核对对应 trace/log、provider 错误码、重试/限流记录、同对象相邻基线，以及请求是否真的到达/离开该边界

### Step E — 跨 provider 横向对比（如果同 tenant 有备选 provider）

如果 D-2 显示 qiniu/某代理 慢，看同 tenant 是否还跑过其他 provider 提供同 family model：

```sql
-- [Dify-Prod] 同 tenant 同 model family 在不同 provider 上的 p50 对比
SELECT
    model_provider,
    model_id,
    COUNT(*) AS calls,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p50_s,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_response_latency)::numeric(10,2) AS p90_s
FROM messages
WHERE tenant_id = '<workspace_id>'
  AND model_id ILIKE '%<慢的 model 关键词，比如 gemini>%'
  AND created_at > now() - interval '7 days'
  AND provider_response_latency IS NOT NULL
GROUP BY model_provider, model_id
ORDER BY p50_s ASC;
```

如果同 model 走官方 provider p50=2.6s 而走代理 p50=5.7s → 可以向客户陈述该时间窗内的对照数字，并在产品支持时建议把官方 provider 作为临时规避选项；仍不能把对照相关性写成根因。

### Step F — token-vs-latency 关联（解释长尾）

如果 D-2 的 max 远大于 p90（长尾很长），看是不是大上下文请求拖慢的。**用 tenant_id 范围**（不是 app_id）拿到足够样本：

```sql
-- [Dify-Prod] 慢调用的 token 分布（>60s 的，整个 workspace 范围）
SELECT
    message_tokens + answer_tokens                  AS total_tokens,
    provider_response_latency::numeric(10,2)        AS latency_s,
    model_provider,
    model_id,
    created_at
FROM messages
WHERE tenant_id = '<workspace_id>'
  AND created_at > now() - interval '7 days'
  AND provider_response_latency > 60
ORDER BY provider_response_latency DESC
LIMIT 20;
```

---

## 🔧 SQL 失败降级铁律（具体到 error 文本如何应对）

**任何 SQL 报错都不许直接放弃**。下面是常见 error 文本到具体 retry 动作的映射表，**必须按表对应处理至少一次**才能进入下一个工具：

### 1. `column reference "X" is ambiguous`

**原因**：JOIN 多表后该列名两边都有（最常见 `status`、`created_at`、`id`、`updated_at`、`tenant_id`）。

**Retry 动作**：把 SELECT / WHERE / GROUP BY / ORDER BY 里**所有**该列加表别名前缀：

```sql
-- 错误：SELECT status, created_at FROM workflow_node_executions wne JOIN workflow_runs wr ...
-- 正确：SELECT wne.status, wr.created_at FROM workflow_node_executions wne JOIN workflow_runs wr ...
```

更安全：直接显式 `SELECT wne.col1, wne.col2, wr.col3` 不用 `*`。

### 2. `column "X" does not exist`

**原因**：列名硬猜错了（如 `messages.tenant_id` 实际是 `app_id` 反查 tenant）。

**Retry 动作**：
1. 先跑只读 introspection：
   ```sql
   SELECT column_name, data_type FROM information_schema.columns
   WHERE table_name = '<table>' ORDER BY ordinal_position;
   ```
2. 拿到真实列名后重写 SQL

### 3. `syntax error` / `relation "X" does not exist`

**原因**：表名错（如 `workflow_node_execution` 缺 s）或 reserved keyword 没引号。

**Retry 动作**：先查 `information_schema.tables/columns` 拿正确表名和列名；reserved keyword（`index` / `order` / `user` 等）一律用双引号或 alias 改名。

### 4. SQL 超时（默认 30s）

**原因**：JOIN 太重 / `LIKE '%xxx%'` 全表扫 / 时间窗太宽 / 缺索引。

**Retry 动作**（按顺序试）：
1. **拆 JOIN**：用 CTE / WITH 先拿 PK 列表，再 IN 子查询（见 Step C 的退化版）
2. **缩时间窗**：7d → 24h → 1h，先拿到结果再扩
3. **去 LIKE %xxx%**：换 `=` 精确匹配；或前缀匹配 `LIKE 'xxx%'`（用得上索引）
4. **加 LIMIT**：先 `LIMIT 100` 验证数据存在再去 LIMIT
5. **拆 OR**：`WHERE id IN ('a','b','c')` 比 `WHERE id LIKE 'a%' OR id LIKE 'b%' OR id LIKE 'c%'` 快得多

### 5. `ORDER BY "index"` 等关键字列报错

**原因**：列名是 SQL 保留字。

**Retry 动作**：
- 双引号包：`ORDER BY "index"` （PG 对 `index` 不严，可能 OK）
- 改用别名：`SELECT idx AS i ... ORDER BY i`
- **去掉 ORDER BY 先跑通联**，验证查询本身没别的问题

### 6. 工具返回 `SQL execution failed:` 但无 error 文本

**原因**：底层查询进程异常退出（连接断开 / OOM / 超时被 kill）。

**Retry 动作**：
1. 先跑 `SELECT 1;` 验证连接活着
2. 活着 → 把上一条 SQL 拆小（缩时间窗 / 加 LIMIT 1 / 单字段 SELECT）
3. 死了 → Internal Note 写"sql_query 工具异常，连续 N 次无 error 文本"，**不要瞎猜原因**（不许写"Teleport auth interruption"这种未证实揣测）

---

### 🚫 禁止的 SQL 失败处理方式

- ❌ "SQL 失败了，跳过这一步" → 必须按上表 retry 至少一次
- ❌ "改用 SigNoz 查日志补数据" → SigNoz 不是 SQL 替代品；workflow 慢类问题主要看 DB 表里的 elapsed_time 分布，SigNoz 是补充而非主线
- ❌ "我假设 elapsed_time 是 X" → 数据缺失就老实写"未能获得节点级数据，限制了诊断深度"
- ❌ 第一次 retry 还失败就放弃 → 至少试两次不同的简化方向

---

## 关键步骤总览

1. **First-Pass 信息抓取**：App ID（或 Workflow ID / Run ID 反查 app）、慢的具体表现、出现时间窗；工作空间先由表单 Email（无则 requester email）在支持侧解析。邮箱唯一映射时直接使用，禁止再索取 Workspace ID / workspace name；只有成功查询证明多工作区无法消歧、无 membership 或 canonical ID 冲突时才向客户澄清最小身份信息。
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
   - D. SigNoz worker 日志（若涉及异步执行）。**workflow 异步任务实际跑在 `k8s.deployment.name = dify-app-worker`，不是 `dify-api-oltp`**：用 `signoz_errors --deployment dify-app-worker`；查询规则详见 `strategies/platform_issue.md` 的 SigNoz 章节（deployment 名区分、默认 production、聚合先于搜索）
6. **参考本地知识库** `saas版知识库/03-workflow.md`

---

## 客户回复必含元素（workflow 慢类工单）

按 SOUL.md Quality Bar 强制：

| 元素 | 写得出 | 写不出 → 重跑 |
|---|---|---|
| 慢点的具体定位（哪个节点 / 哪个 provider） | "issue is the LLM node X using provider qiniu" | 没跑 Step C/D，回去补 |
| **大样本统计量**（用 D-2 tenant 范围拿 ≥ 几百 calls）| "1580 calls, p50=5.7s, p90=17.7s, max=907s" | 只跑了 D-1（窄），回去补 D-2 |
| 至少一个对照基线 | "Gemini official provider p50=2.6s vs qiniu 5.7s" | 没跑 Step E（如有备选 provider），回去补 |
| 1-3 条具体可执行建议 | "试试切官方 provider / 联系 Qiniu support / 减小 context" | 没具体动作，回去想 |

**样本量不足的判断**（D-1 范围下 calls < 50）：必须**额外**跑 D-2（tenant 范围）拿大样本，且 Internal Note 要明示"app 范围 30 calls 样本量不足，引用 workspace 范围 1580 calls 数据"。

**禁止**输出：
- ❌ 只写泛泛的 “The slowdown is real, but we need more diagnosis.” 而不说明已确认边界、准确未知跳点和下一项核查
- ✅ 根因尚未确认时可以明确写仍需继续排查，但必须同时说明：已确认的慢点/时间窗、不能据此直接写成 provider 侧根因、下一步将核对的 trace/log/error/retry/baseline
- ❌ 只写 app 范围的 30 calls 数据当主结论 —— 样本量不够，结论不可靠
- ❌ 跳过 Step D-2 tenant 范围聚合 —— 这是大样本统计的关键来源

---

## 待验证假设（不按概率预设根因）

下面只用于选择下一项核查，不能从列表顺序、单项相关性或历史频率直接选择根因：

1. **特定 provider/model 边界延迟或限流** — Step D 只定位边界，需结合 trace/log/error/retry 证据验证
2. **大上下文（>50k token）与长尾相关** — Step F 验证相关性后，还需排除 model/provider/queue 等替代解释
3. **变量作用域 / 类型不匹配**（节点引用了不存在的 variable_node_path）
4. **HTTP Request 节点超时 / 证书**
5. **循环/分支逻辑错误**
6. **parallel iteration timing 失真**（已修：PR #33216）
7. **前端假死**：SSE 断了但 UI 没重连提示
8. **wid 不存在**（截图里大概率有真实 wid，先看图！）
