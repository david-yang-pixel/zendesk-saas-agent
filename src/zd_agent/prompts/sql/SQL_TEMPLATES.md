# 常用 SQL 查询模板 — SaaS 工单排查

> 按工单场景分类，直接替换占位符即可使用。
>
> ## ⚡ 优先用 Inspector，再考虑手写 SQL
>
> 下面这些场景**优先调 `inspector_exec` 工具**，不要手写 SQL：
>
> | 场景 | inspector 调用 | 为什么不手写 SQL |
> |---|---|---|
> | **配额/用量类**（应用数 / 文档数 / 知识库存储 / 成员数） | `inspector_exec tenant_id=<id> section=quota` | 知识库存储在 TiDB 不在 PG，手写 SQL 拿不到 |
> | **workflow run 失败定位** | `inspector_exec workflow_run_id=<uuid>` | 一次拿 run + 节点链 + 错误，比 JOIN 两表全 |
> | **app 失败排查**（"今天报错很多"） | `inspector_exec app_id=<uuid> section=runs query=failed limit=5` | 自动按时间筛 + 关联节点 |
> | **UUID 类型未知**（客户给一串 UUID 没说是啥） | `inspector_exec id=<uuid>` | 自动跨表识别 |
> | **账号/租户 drill-down** | `inspector_exec email=<x>` 或 `tenant_id=<x> section=billing/runs/apps/members` | 跨 main+billing 一次返回 |
>
> Inspector **不覆盖**的场景才走下面的 sql_query 路径：
> - 任意自定义 SQL（多表 JOIN / 聚合 / 表结构未覆盖）
> - plugin_daemon 库（dify_plugin_daemon）
> - 需要 percentile / time-series 等聚合分析（如 workflow 慢类工单的 latency 分位数）
>
> **真实 schema** 在 system prompt 的 "Dify SaaS Database Schema" 段（紧接本段后），
> 由生产库 `information_schema` introspect 出来。**不要靠记忆瞎写表名 / 列名**——
> 看那段拿真实结构再写 SQL。
>
> **注意事项：**
> - `<workspace_id>` = 用户提供的 Workspace ID
> - `database=prod` (langgenius) / `database=billing` / `database=plugin`
>   (dify_plugin_daemon) 三个独立 DB，**不能跨库 JOIN**
> - `graph` / `features` 字段是 text 类型，使用 JSON 操作符前需要 `::jsonb` 转换

---

## 1. 用户与工作区

### 1.1 查工作区基本信息

```sql
-- [Dify-Prod] 查工作区名称和状态
SELECT id, name, status, created_at
FROM tenants
WHERE id = '<workspace_id>';
```

### 1.2 查工作区成员

```sql
-- [Dify-Prod] 查工作区所有成员及角色
SELECT
    a.id AS account_id,
    a.name,
    a.email,
    taj.role,
    a.last_login_at,
    a.last_active_at,
    a.status
FROM tenant_account_joins taj
JOIN accounts a ON a.id = taj.account_id
WHERE taj.tenant_id = '<workspace_id>'
ORDER BY taj.role, a.last_active_at DESC;
```

> **输出说明**：role 值为 owner / admin / normal / editor，last_active_at 可判断用户活跃度

### 1.3 通过邮箱查用户

```sql
-- [Dify-Prod] 通过邮箱查 account_id 和所属工作区
SELECT
    a.id AS account_id,
    a.name,
    a.email,
    a.status,
    a.last_login_at,
    taj.tenant_id AS workspace_id,
    taj.role
FROM accounts a
LEFT JOIN tenant_account_joins taj ON taj.account_id = a.id
WHERE a.email = '<用户邮箱>';
```

---

## 2. 订阅与计费

### 2.1 查订阅计划（Dify-Prod）

```sql
-- [Dify-Prod] 查当前订阅计划（简版，Prod 库也有一份）
SELECT tenant_id, plan, interval, expiration_date, stripe_subscription_id
FROM tenant_subscription_settings
WHERE tenant_id = '<workspace_id>';
```

### 2.2 查订阅计划（Dify-billing，更详细）

```sql
-- [Dify-billing] 查订阅计划
SELECT tenant_id, plan, interval, expiration_date, stripe_subscription_id, created_at
FROM tenant_subscription_settings
WHERE tenant_id = '<workspace_id>';
```

### 2.3 查 Stripe 客户 ID

```sql
-- [Dify-billing] 查 Stripe Customer ID（退款时提供给操作人员）
SELECT tenant_id, stripe_customer_id, created_at
FROM tenant_stripe_customers
WHERE tenant_id = '<workspace_id>';
```

### 2.4 查 Stripe 订阅详细状态

```sql
-- [Dify-billing] 查 Stripe 订阅状态（扣费、取消、周期等）
SELECT
    tenant_id,
    stripe_customer_id,
    stripe_subscription_id,
    subscription_status,
    cancel_at_period_end,
    current_period_start,
    current_period_end,
    canceled_at,
    last_payment_succeeded_at,
    last_payment_failed_at,
    last_invoice_id
FROM tenant_stripe_status
WHERE tenant_id = '<workspace_id>';
```

> **输出说明**：
> - `subscription_status`：active / canceled / past_due / trialing
> - `cancel_at_period_end`：'true' 表示到期自动取消，不再续费
> - `current_period_end`：当前计费周期结束时间，也是下次扣费时间
> - `canceled_at`：用户点击取消的时间

### 2.5 查订阅功能配额详情

```sql
-- [Dify-billing] 查订阅对应的功能配额
SELECT
    tsd.tenant_id,
    tss.plan,
    tsd.member_size,
    tsd.app_size,
    tsd.vector_space,
    tsd.document_upload_quota,
    tsd.annotation_quota,
    tsd.docs_processing,
    tsd.knowledge_rate_limit
FROM tenant_subscription_details tsd
JOIN tenant_subscription_settings tss ON tss.id = tsd.tenant_subscription_setting_id
WHERE tsd.tenant_id = '<workspace_id>';
```

### 2.6 查功能用量

```sql
-- [Dify-billing] 查各功能当前用量
SELECT tenant_id, feature_key, plan, quota, usage
FROM tenant_feature_usages
WHERE tenant_id = '<workspace_id>'
ORDER BY feature_key;
```

---

## 3. 教育折扣

### 3.1 查教育身份认证

```sql
-- [Dify-billing] 先在 Dify-Prod 查 account_id，再查教育身份
-- Step 1 (Dify-Prod):
SELECT id AS account_id, email FROM accounts WHERE email = '<用户邮箱>';

-- Step 2 (Dify-billing):
SELECT
    id,
    account_id,
    institution,
    school_role,
    discount_claimed,
    stripe_promo_id,
    stripe_promo_tenant_id,
    expired_at,
    created_at
FROM education_identities
WHERE account_id = '<account_id>';
```

> **输出说明**：
> - `discount_claimed = true`：已领取折扣码
> - `stripe_promo_id`：Stripe 折扣码 ID
> - `stripe_promo_tenant_id`：使用折扣码升级的工作区
> - `expired_at`：教育身份过期时间

### 3.2 查折扣使用记录

```sql
-- [Dify-billing] 查教育折扣使用日志
SELECT
    id,
    stripe_promo_id,
    tenant_id,
    account_id,
    role,
    valid_starts_at,
    valid_ends_at,
    created_at
FROM education_promotion_logs
WHERE account_id = '<account_id>';
```

> **输出说明**：
> - `valid_starts_at` / `valid_ends_at`：折扣有效期
> - `role`：student / teacher / staff

---

## 4. 应用与工作流

### 4.1 查应用列表

```sql
-- [Dify-Prod] 查工作区下所有应用
SELECT
    id,
    name,
    mode,
    status,
    enable_site,
    enable_api,
    created_at,
    updated_at
FROM apps
WHERE tenant_id = '<workspace_id>'
ORDER BY updated_at DESC;
```

> **输出说明**：mode 值为 chat / completion / advanced-chat / workflow / agent-chat

### 4.2 查指定应用详情

```sql
-- [Dify-Prod] 查单个应用的配置
SELECT
    id, name, mode, status,
    workflow_id,
    api_rpm, api_rph
FROM apps
WHERE id = '<app_id>' AND tenant_id = '<workspace_id>';
```

### 4.3 查工作流最近运行记录

```sql
-- [Dify-Prod] 查最近 10 次工作流执行
SELECT
    id,
    app_id,
    status,
    error,
    elapsed_time,
    total_tokens,
    total_steps,
    triggered_from,
    created_at,
    finished_at
FROM workflow_runs
WHERE app_id = '<app_id>'
ORDER BY created_at DESC
LIMIT 10;
```

### 4.4 查工作流节点执行错误

```sql
-- [Dify-Prod] 查指定 workflow_run 中失败的节点
SELECT
    node_id,
    node_type,
    title,
    status,
    left(error, 500) AS error_preview,
    elapsed_time,
    created_at
FROM workflow_node_executions
WHERE workflow_run_id = '<workflow_run_id>'
  AND status = 'failed'
ORDER BY index;
```

### 4.5 查应用的模型配置（Chat/Completion 类型）

```sql
-- [Dify-Prod] 查 Chat/Completion 应用使用的模型
SELECT
    id,
    app_id,
    model_id AS model_name,
    provider AS model_provider,
    opening_statement,
    retriever_resource_id
FROM app_model_configs
WHERE app_id = '<app_id>'
ORDER BY created_at DESC
LIMIT 1;
```

### 4.6 查工作流 Agent 节点的模型和工具

```sql
-- [Dify-Prod] 查 workflow 中 Agent 节点的配置
-- graph 字段是 text 类型，需要 ::jsonb 转换
SELECT
    node->>'id' AS node_id,
    node->'data'->>'title' AS title,
    node->'data'->>'type' AS node_type,
    node->'data'->'model'->>'name' AS model_name,
    node->'data'->'model'->>'provider' AS model_provider
FROM workflows w,
     jsonb_array_elements(w.graph::jsonb->'nodes') AS node
WHERE w.app_id = '<app_id>'
  AND w.version = 'draft'
  AND node->'data'->>'type' = 'agent';
```

### 4.7 查工作流自定义工具

```sql
-- [Dify-Prod] 查工作区的 Workflow 工具
SELECT
    id,
    name,
    label,
    app_id,
    tenant_id,
    created_at
FROM tool_workflow_providers
WHERE tenant_id = '<workspace_id>'
ORDER BY created_at DESC;
```

---

## 5. 知识库

### 5.1 查知识库列表

```sql
-- [Dify-Prod] 查工作区下所有知识库
SELECT
    id,
    name,
    description,
    provider,
    indexing_technique,
    permission,
    created_at
FROM datasets
WHERE tenant_id = '<workspace_id>'
ORDER BY created_at DESC;
```

### 5.2 查知识库中的文档状态

```sql
-- [Dify-Prod] 查指定知识库的文档状态
SELECT
    id,
    name,
    indexing_status,
    enabled,
    archived,
    doc_type,
    word_count,
    tokens,
    error,
    created_at,
    updated_at
FROM documents
WHERE dataset_id = '<dataset_id>'
ORDER BY position;
```

> **输出说明**：
> - `indexing_status`：waiting / indexing / completed / error / paused
> - `enabled`：false 表示文档已禁用
> - `archived`：true 表示文档已归档

### 5.3 查文档分片统计

```sql
-- [Dify-Prod] 查文档分片数量和状态
SELECT
    document_id,
    status,
    count(*) AS segment_count
FROM document_segments
WHERE dataset_id = '<dataset_id>'
GROUP BY document_id, status
ORDER BY document_id;
```

### 5.4 查 indexing 出错的文档

```sql
-- [Dify-Prod] 查索引失败的文档
SELECT
    id,
    name,
    indexing_status,
    left(error, 300) AS error_preview,
    updated_at
FROM documents
WHERE dataset_id = '<dataset_id>'
  AND indexing_status = 'error';
```

---

## 6. 模型与插件

### 6.1 查工作区配置的模型提供者

```sql
-- [Dify-Prod] 查工作区的模型提供者凭证
SELECT
    id,
    tenant_id,
    provider_name,
    provider_type,
    encrypted_config,
    is_valid,
    created_at
FROM providers
WHERE tenant_id = '<workspace_id>'
ORDER BY provider_name;
```

### 6.2 查默认模型配置

```sql
-- [Dify-Prod] 查工作区的默认模型
SELECT
    id,
    tenant_id,
    provider_name,
    model_name,
    model_type
FROM tenant_default_models
WHERE tenant_id = '<workspace_id>';
```

### 6.3 查安装的插件

```sql
-- [plugin_daemon] 查工作区安装的插件
SELECT
    id,
    tenant_id,
    plugin_id,
    plugin_unique_identifier,
    runtime_type,
    source,
    created_at
FROM plugin_installations
WHERE tenant_id = '<workspace_id>'
ORDER BY created_at DESC;
```

### 6.4 查 Agent 策略插件安装

```sql
-- [plugin_daemon] 查工作区的 Agent 策略插件
SELECT
    id,
    tenant_id,
    provider,
    plugin_unique_identifier,
    plugin_id,
    created_at
FROM agent_strategy_installations
WHERE tenant_id = '<workspace_id>';
```

---

## 7. 消息与对话

### 7.1 查应用最近的对话

```sql
-- [Dify-Prod] 查应用最近 20 条对话
SELECT
    id,
    app_id,
    status,
    from_source,
    from_end_user_id,
    from_account_id,
    read_at,
    created_at
FROM conversations
WHERE app_id = '<app_id>'
ORDER BY created_at DESC
LIMIT 20;
```

### 7.2 查对话中的消息

```sql
-- [Dify-Prod] 查指定对话的消息
SELECT
    id,
    conversation_id,
    left(query, 200) AS query_preview,
    left(answer, 200) AS answer_preview,
    message_tokens,
    answer_tokens,
    provider_response_latency,
    status,
    error,
    created_at
FROM messages
WHERE conversation_id = '<conversation_id>'
ORDER BY created_at;
```

---

## 8. 快速组合查询

### 8.1 工单万能起手式（拿到 workspace_id 后第一步）

> 注意：
> - `Dify-Prod.tenant_subscription_settings` 在部分环境里可能为空或并不是前台套餐展示的真实来源。
> - SaaS 前台 workspace 显示的 `Sandbox / Professional / Team` 实际是后端通过 `FeatureService -> BillingService.get_info(/subscription/info)` 取回的 billing API 结果。
> - 因此遇到“Billing 显示已升级，但前台仍是 Sandbox”时，套餐状态以 `Dify-billing.tenant_subscription_settings` 为主，`Dify-Prod` 这张表只能作为辅助参考，不能单独据此下结论。

```sql
-- [Dify-Prod] 快速了解工作区全貌
SELECT t.id, t.name, t.status, t.created_at,
       tss.plan, tss.interval, tss.expiration_date
FROM tenants t
LEFT JOIN tenant_subscription_settings tss ON tss.tenant_id = t.id
WHERE t.id = '<workspace_id>';
```

### 8.2 退款排查组合

```sql
-- Step 1 [Dify-Prod]: 查工作区基本信息和订阅
SELECT t.id, t.name, tss.plan, tss.interval, tss.expiration_date
FROM tenants t
LEFT JOIN tenant_subscription_settings tss ON tss.tenant_id = t.id
WHERE t.id = '<workspace_id>';

-- Step 2 [Dify-billing]: 查 Stripe 状态
SELECT stripe_customer_id, subscription_status, cancel_at_period_end,
       current_period_start, current_period_end, last_payment_succeeded_at
FROM tenant_stripe_status
WHERE tenant_id = '<workspace_id>';

-- Step 3 [Dify-Prod]: 查最近活跃度（看有没有在用）
SELECT a.email, a.last_login_at, a.last_active_at
FROM tenant_account_joins taj
JOIN accounts a ON a.id = taj.account_id
WHERE taj.tenant_id = '<workspace_id>'
ORDER BY a.last_active_at DESC;
```

### 8.3 教育折扣排查组合

```sql
-- Step 1 [Dify-Prod]: 通过邮箱查 account_id
SELECT id AS account_id, email, name, created_at FROM accounts WHERE email = '<邮箱>';

-- Step 2 [Dify-billing]: 查教育身份
SELECT * FROM education_identities WHERE account_id = '<account_id>';

-- Step 3 [Dify-billing]: 查折扣使用记录
SELECT * FROM education_promotion_logs WHERE account_id = '<account_id>';

-- Step 4 [Dify-billing]: 查当前订阅
SELECT plan, interval, expiration_date FROM tenant_subscription_settings WHERE tenant_id = '<workspace_id>';
```

### [工单] 教育版订阅已生效但前台仍显示 Sandbox
- **关键词**: 教育版, Sandbox, 套餐未生效, billing, feature service
- **问题描述**: 用户在 Billing 页面已显示 `Dify Professional Plan`，`Dify-billing.tenant_subscription_settings` 也为 `professional`，教育折扣记录与 workspace 绑定正确，但前台 workspace 仍显示 `Sandbox`。排查中发现 `Dify-Prod.tenant_subscription_settings` 在该环境下对多个已订阅 workspace 均查不到记录，不能作为前台套餐状态的判断依据。
- **解决方案**: 先用 `Dify-Prod.accounts` / `tenant_account_joins` 通过邮箱确认 `account_id` 与 `workspace_id`；再以 `Dify-billing.tenant_subscription_settings`、`education_identities`、`education_promotion_logs` 作为订阅是否生效的主依据。若 billing 侧已正确，但前台仍显示 `Sandbox`，按“前台套餐展示读取 `FeatureService -> BillingService.get_info(/subscription/info)`”的方向继续排查，优先怀疑 billing API 返回或 `tenant:{tenant_id}:billing_info` 缓存未刷新，而不是要求客户反复重登。
- **来源**: 人工录入
