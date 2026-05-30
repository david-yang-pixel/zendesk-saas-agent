# 设计：让 zd-agent 感知 cloud.dify.ai 线上部署版本

日期：2026-05-30
状态：待实现
作者：yyg + Claude

## 问题

zd-agent 处理 SaaS 工单时，**不知道 cloud.dify.ai 生产环境当前实际在跑哪个版本的代码**。这导致它无法准确判断：

- 某个 bug 是否已在线上修复
- 某个功能是否已上线
- 客户报的问题对应线上哪一份源码

现状能力盘点（已确认）：

- agent 能用 `git_show` / `git_grep` / `git_log` / `git_diff` 读任意 branch/tag 的代码，也能 `saas_branch_switch` 切本地 repo 分支。
- agent 有 SigNoz 工具（走 `signoz-mcp-server` 子进程），地址 `https://signoz.dify.dev`。
- agent clone 了 5 个仓库，含 `saas-deploy`（GitOps 部署仓）。
- `platform_issue.md` 已有一段「镜像版本确认」引导，但仅限平台故障类工单的 SigNoz 路径，不系统化，其它类型工单用不上。

缺口：**没有任何机制系统性地把「线上在跑什么版本」喂给 agent。**

## 实测结论（地面真相，2026-05-30）

通过本地 `sz` CLI（`~/Documents/工作/saas工单常见问题/signoz-cli/sz`）实测 `dify-api-oltp` 的 resource 属性：

```
dep            git_commit  image_name           image_tag            service_version
dify-api-oltp  (空)        langgenius/dify-api  hotfix-1.14.2-fix.4  hotfix-1.14.2-fix.4
```

关键事实：

1. **SigNoz 能拿到完整镜像 tag**：`container.image.tag = hotfix-1.14.2-fix.4`（含 hotfix 标识，非裸版本号）。
2. **`service.version` = image tag**：生产构建把 image tag 注入成了 service.version（不是源码里写的 `dify-{version}-{COMMIT_SHA}` 那种形态）。
3. **`custom.deployment.git_commit` 为空**：生产镜像构建没注入 COMMIT_SHA（源码 `ext_otel.py` 本会上报，但生产构建时该字段为空）。属于 Dify 侧问题，不在本任务范围。
4. **SigNoz 是实时真相，saas-deploy 本地副本会陈旧**：实测时 SigNoz 显示线上是 `fix.4`，而本地 clone 的 saas-deploy `kustomization.yaml` 写的是 `fix.3`（落后一个版本）。这直接证明 SigNoz 必须当主源——若用 saas-deploy 当主源，agent 会答错版本。
5. **`signoz-mcp-server` 对 SigNoz EE 后端（v0.121）不兼容**：MCP 子进程一启动就 "closed stdout"。`sz` 之所以存在就是为绕过它，直接打 v4 `query_range` API。
6. **两个 SigNoz 入口同源**：`https://signoz.dify.dev`（生产已配）与 `https://dify.us2.signoz.cloud`（本地 Cursor 配）返回完全一致的数据。采用 `signoz.dify.dev`，agent 生产环境零改动。

## `sz` 验证过的查询机制（实现照搬）

- POST `{SIGNOZ_URL}/api/v4/query_range`
- body：`compositeQuery.queryType = "clickhouse_sql"`，`panelType = "table"`，`chQueries.A.query = <SQL>`，附 `start`/`end`（毫秒）/`step`
- header：`SIGNOZ-API-KEY: <key>`、`Content-Type: application/json`、**`User-Agent: sz-cli/1.0 (curl-compatible)`**（关键：`signoz.dify.dev` 前面的 Cloudflare 会用 403 code 1010 拒绝 urllib 默认 UA）
- 响应行在 `data.result[0].list[].data`
- 时间字段是 UInt64 纳秒；SQL 用 `{start_ns}` 占位

## 设计决策（已与用户确认）

| 决策点 | 选择 |
|---|---|
| 主数据源 | **SigNoz**（运行时真相）；saas-deploy 仅作兜底 |
| 消费方式 | **两者都要**：① 每单自动注入简短版本基线 ② 提供深挖工具 |
| 服务范围 | **只 dify-api**（`dify-api-oltp` deployment）|
| SigNoz 客户端代码 | **新建 `integrations/signoz_http.py`**（agent 自包含，不依赖外部 sz 脚本）|
| SigNoz URL | `signoz.dify.dev`（复用现有 config，零改动）|

## 架构

```
   ┌─────────────────────────────────────────────────┐
   │  saas_version.py（新）                            │
   │  async resolve_deployed_version(use_cache=True)   │
   │   主: signoz_http.query() → dify-api-oltp 的       │
   │       container.image.tag / service.version       │
   │   兜底: saas-deploy kustomization newTag           │
   │   → DeployedVersion(image_tag, git_ref,           │
   │        base_version, ref_verified, source)        │
   │   进程内缓存 5 min                                  │
   └───────────────┬───────────────────────────────────┘
         ┌─────────┴─────────┐
         ▼                   ▼
  ① 动态 prompt 段     ② saas_deployed_version 工具
   (每单自动注入)        (agent 主动深挖)
```

### 组件 1：`integrations/signoz_http.py`（新）

最小 v4 query_range 客户端，职责单一：把一段 ClickHouse SQL 打到 SigNoz 拿回行。

- `async def query(sql: str, *, hours: float = 3.0) -> list[dict]`
- 内部：构造 body、带 curl UA、`SIGNOZ-API-KEY`、`{start_ns}` 替换、解析 `data.result[0].list[].data`
- 配置来源：`cfg.signoz.url or env.signoz_url`、`cfg.signoz.api_key or env.signoz_api_key`（与 `signoz_mcp_tools.py` 读法一致）
- URL/key 缺失 → 抛 `SignozHttpError`（上层捕获后降级，不中断工单）
- **实现方式定稿**：照搬 `sz` 验证过的 `urllib` 同步逻辑，外面用 `asyncio.to_thread` 包成 async（零新依赖、不阻塞事件循环、复用已验证代码）。不引入对项目其它 http 栈的假设。

### 组件 2：`tools/saas_version.py`（新，核心模块）

```python
@dataclass(frozen=True)
class DeployedVersion:
    image_tag: str        # "hotfix-1.14.2-fix.4"  ← SigNoz container.image.tag
    git_ref: str          # "hotfix/1.14.2-fix.4"  ← 转换后、已验证可 git_show 的 ref
    base_version: str     # "1.14.2"
    ref_verified: bool    # git_ref 是否真的在 dify 仓存在
    source: str           # "signoz" | "saas-deploy-fallback"

async def resolve_deployed_version(*, use_cache: bool = True) -> DeployedVersion | None:
    # 读不到任何东西 → 返回 None；绝不抛异常打断工单处理
```

解析流程：

1. 命中进程内缓存（5 min TTL）→ 直接返回
2. **主路径 SigNoz**：`signoz_http.query()` 跑下面 SQL，取 `image_tag`
   ```sql
   SELECT DISTINCT
     resources_string['container.image.tag']  AS image_tag,
     resources_string['service.version']      AS service_version
   FROM signoz_logs.distributed_logs_v2
   WHERE timestamp >= {start_ns}
     AND resources_string['k8s.deployment.name'] = 'dify-api-oltp'
     AND resources_string['deployment.environment'] = 'production'
   LIMIT 5
   ```
   - 多行/取值不一致（滚动发布中）→ 取出现的全部 distinct tag，注入段标注「发布中：fix.3→fix.4」
   - `source = "signoz"`
3. **兜底 saas-deploy**：SigNoz 失败（异常/0 行）→ `git show main:environments/production/dify/api-oltp/kustomization.yaml` 抽 `newTag`，`source = "saas-deploy-fallback"`
   - 读 saas-deploy 前先 `fetch_repo`（复用 `repos.py`），尽量新鲜
4. **tag→ref 转换**（两路径共用）：
   - `hotfix-1.14.2-fix.4` → `hotfix/1.14.2-fix.4`（`hotfix-` 前缀 → `hotfix/`）
   - 纯 tag（如 `1.14.2`）→ 原样
   - 转换后 `git rev-parse --verify <ref>` 验证；**失败先 auto-fetch dify 仓再验一次**（复用 `git_source.py` 的 `_auto_fetch_repo`），仍失败 → `ref_verified=False`，`git_ref` 回退为原 image_tag
   - `base_version`：从 tag 正则抽 `\d+\.\d+\.\d+`
5. 写缓存，返回

### 组件 3：动态 prompt 段（`runtime/prompt/defaults.py` + `section.py`）

- 在 `register_dynamic_sections` 注册新段 `deployed_version`（**必须是动态段**——版本会变，进静态段会破坏 prefix cache 命中率）
- `PromptContext` 加字段 `deployed_version: dict | None`
- runner 在构造 dynamic user context 前调 `resolve_deployed_version()`，填进 ctx
- 渲染（`ref_verified=True` 时）：
  ```
  ## 线上部署版本（cloud.dify.ai 生产 · SigNoz 实时遥测）
  - dify-api 运行中镜像：hotfix-1.14.2-fix.4
  - 对应源码 ref：hotfix/1.14.2-fix.4 ✓
    （用 git_show / git_grep 查这个 ref = 线上实际在跑的代码）
  - 需要更多细节用 saas_deployed_version 工具
  ```
- `condition`: `deployed_version` 为 None → 整段不出现（降级）
- `ref_verified=False` 或 `source=saas-deploy-fallback` 时，文案显式标注不确定性

### 组件 4：深挖工具 `saas_deployed_version`（`tools/saas_version.py` 内）

- 注册到 `source_code` 组，无参（YAGNI——只 dify-api）
- description 写明触发场景：判断 bug 是否已线上修复 / 功能是否已上线 / 客户问题对应线上哪版代码
- 返回结构化文本：运行镜像 tag、service.version、可 git_show 的 ref、ref 是否验证、数据来源
- 复用 `resolve_deployed_version()`，与注入段同源、零重复
- 失败返回明确 error 文本（不抛）

## 错误处理 / 降级（贯穿全设计）

版本信息是「锦上添花」，**任何环节失败都不能中断工单处理**：

- SigNoz 不通 → 降级 saas-deploy
- saas-deploy 也读不到 → `resolve_deployed_version` 返回 None
- 注入段 None → 整段消失
- 工具失败 → 返回明确 error 文本（不抛）
- ref 验证失败 → 标 `ref_verified=False`，不让 agent 拿到查不到的 ref 当真

## 性能 / 缓存

- SigNoz 查询有网络延迟（实测百毫秒级）
- **进程内缓存 5 min TTL**：版本几小时才变一次，注入段与工具共享同一缓存，避免每单都打 SigNoz
- 缓存键无参数（只 dify-api 单一目标）

## 测试

- `saas_version.py` 单测：
  - tag→ref 转换各 case（`hotfix-x-fix.n` / 纯 tag / 异常输入）
  - SigNoz 路径：mock `signoz_http.query` 返回 → 解析正确
  - 降级路径：SigNoz 抛异常 → 回退 saas-deploy → `source` 正确
  - None 路径：两源都失败 → 返回 None
  - 缓存：第二次调用不打 SigNoz
- `signoz_http.py` 单测：body 构造正确、curl UA 存在、响应解析、URL/key 缺失抛错
- 注入段单测：有版本 / 无版本（None）/ ref 未验证 三种渲染
- 工具单测：mock 成功 / None / 异常

## 配置 / 部署

- 复用现有 `signoz` config（url + api_key）与 `repos`（saas-deploy 已在列，别名 `deploy`）
- 无新增 env、无新增依赖
- 内置常量（一处改）：curl UA 字符串、kustomization 路径、目标 deployment 名 `dify-api-oltp`、缓存 TTL、SQL 模板

## 非目标（YAGNI）

- 不支持除 dify-api 外的其它服务（web / worker）——多数工单是后端 bug，api tag 即关键答案
- 不集成 git_commit（生产为空，无意义）
- 不做「声明版本 vs 运行版本」对比面板——SigNoz 主 + saas-deploy 兜底已足够，对比逻辑会过度复杂化
- 不改 `signoz-mcp-server`（已知对 EE 不兼容，本任务用 http 直连绕过；MCP 工具是否保留是独立问题）
