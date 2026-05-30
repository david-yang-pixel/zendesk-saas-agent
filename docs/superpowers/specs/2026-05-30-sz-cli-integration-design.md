# 设计：zd-agent 接入 sz CLI —— 线上版本感知 + 全面接管 SigNoz 查询

日期：2026-05-30（v3）
状态：待实现
作者：yyg + Claude

## 范围（v3）

本任务三件事，围绕同一个 `sz` CLI 展开：

1. **线上版本感知**：让 agent 知道 cloud.dify.ai 生产实际在跑哪个版本（自动注入 + 深挖工具）
2. **sz 接管 SigNoz 日志查询**：用 sz 的结构化子命令替换现有 SigNoz MCP 工具
3. **弃用 SigNoz MCP**：停止注册 MCP 工具（保留代码备用）

三件事共享同一个 vendored `sz`，所以合并为一个设计。

## 背景问题

### 问题 A：agent 不知道线上版本

zd-agent 处理工单时无法判断 cloud.dify.ai 生产当前在跑哪个版本，导致它判断不了：

- 某个 bug 是否已在线上修复
- 某个功能是否已上线
- 客户报的问题对应线上哪一份源码

### 问题 B：SigNoz MCP 在生产是死的（实测铁证）

- 当前生产容器启动日志里**完全没有 signoz_mcp 任何记录**（无 `signoz_mcp_tools_loaded`、无 binary_missing），说明 MCP 工具从未成功加载。
- 宿主机 glibc 2.31 跑不起 `signoz-mcp-server`（需 GLIBC_2.32/2.34）。容器内 glibc 2.41 虽能跑 binary，但 MCP server 对 SigNoz EE v0.121 后端不兼容（手动驱动一启动就 "closed stdout"）。
- **后果**：prompt 里教 agent 用的 `signoz_search_logs` / `signoz_aggregate_logs` 等工具实际不存在，agent 真去调会失败。这是一个已存在的生产故障。

## 实测结论（地面真相，2026-05-30）

### 版本字段（dify-api-oltp resource 属性）

```
dep            git_commit  image_name           image_tag            service_version
dify-api-oltp  (空)        langgenius/dify-api  hotfix-1.14.2-fix.4  hotfix-1.14.2-fix.4
```

1. **SigNoz 能拿到完整镜像 tag**：`container.image.tag = hotfix-1.14.2-fix.4`（含 hotfix 标识，非裸版本号）。
2. **`service.version` = image tag**：生产构建把 image tag 注入成了 service.version（非源码里 `dify-{version}-{COMMIT_SHA}` 形态）。
3. **`custom.deployment.git_commit` 为空**：生产镜像构建没注入 COMMIT_SHA（源码 `ext_otel.py` 本会上报）。Dify 侧问题，不在本任务范围。
4. **SigNoz 是实时真相，saas-deploy 本地副本会陈旧**：实测时 SigNoz 显示 `fix.4`，本地 clone 的 saas-deploy `kustomization.yaml` 写 `fix.3`（落后一版）。证明 SigNoz 必须当主源——若用 saas-deploy 当主源，agent 会答错版本。

### sz 功能验证

- `sz query "<SQL>"`：拿到 dify-api image tag ✅
- `sz errors --env production`：实时捞到 dify-api-oltp 的 ERROR 日志 ✅
- `sz schema`：读表结构 ✅
- `sz services --env production`：列活跃 deployment ✅

### SigNoz 实例 URL 对比

| 实例 | 来源 | 查 dify-api image tag |
|---|---|---|
| `https://signoz.dify.dev` | 服务器 config + sz 默认 | ✅ `hotfix-1.14.2-fix.4` |
| `https://dify.us2.signoz.cloud` | 本地 Cursor MCP 配置 | ❌ 0 行（key 不匹配该实例）|

定稿用 `signoz.dify.dev`（= sz 默认 = 现有 config，零改动）。

## sz CLI 关键事实（实现依据）

- 零依赖纯 Python3 stdlib，直接打 SigNoz v4 `query_range` API + raw ClickHouse SQL
- **必须带 `User-Agent: sz-cli/1.0 (curl-compatible)`**（signoz.dify.dev 前的 Cloudflare 会 403 拒绝 urllib 默认 UA）
- 子命令：`errors` / `traceback` / `query` / `schema` / `services`
- 全局 flag `--json` / `-q` 在**子命令前**传；实测 `--json` 在 query 子命令下报 `unrecognized arguments`（argparse 父子 parser 未贯通），需修
- 内置默认 `SIGNOZ_URL=https://signoz.dify.dev` + fallback read key（脚本第 40 行明文，只读）
- 时间字段 UInt64 纳秒；SQL 用 `{start_ns}`/`{end_ns}` 占位符自动替换
- 日志表 `signoz_logs.distributed_logs_v2`；tenant 走 `attributes_string['tenant_id']`；traceback 走 `attributes_string['exception.stacktrace']`

## 设计决策（已与用户确认）

| 决策点 | 选择 |
|---|---|
| 主数据源 | **SigNoz**（运行时真相）；saas-deploy 仅版本查询的兜底 |
| 版本消费方式 | **两者都要**：① 每单自动注入版本基线 ② `saas_deployed_version` 深挖工具 |
| 版本服务范围 | **只 dify-api**（`dify-api-oltp` deployment）|
| sz 查询实现 | **vendored sz + subprocess shell-out**（纳入仓库、打进镜像）|
| sz 工具形态 | **结构化子命令各包一个工具**：`signoz_errors` / `signoz_traceback` / `signoz_services` / `signoz_query`(raw 兜底) |
| MCP 去留 | **停止注册，保留代码**（signoz_mcp_tools.py 不删，registry 不再 spawn）|
| prompt 改写 | **本次一起改写**（所有 signoz_* 旧工具名 → sz 工具名）|
| SigNoz URL | `signoz.dify.dev` |

## 架构

```
   ┌──────────────────────────────────────────────────────┐
   │  vendored sz  →  scripts/sz（打进镜像 /app/scripts/sz）  │
   └───────────────┬──────────────────────┬────────────────┘
                   │                      │
       ┌───────────▼──────────┐  ┌────────▼─────────────────────┐
       │ saas_version.py       │  │ signoz_tools.py（新）         │
       │ resolve_deployed_     │  │ signoz_errors / _traceback /  │
       │   version()           │  │ _services / _query            │
       │ 主:sz query image tag │  │ 各 = subprocess 调 sz 子命令   │
       │ 兜底:saas-deploy newTag│  └────────┬─────────────────────┘
       └────┬─────────────┬────┘           │
   ┌────────▼──┐    ┌─────▼────────┐   ┌────▼──────────────┐
   │①动态prompt │    │②saas_deployed│   │ agent 日志查询      │
   │ 段(注入)   │    │ _version 工具 │   │（替换 MCP signoz_*）│
   └───────────┘    └──────────────┘   └───────────────────┘
```

## 组件清单

### 组件 0：vendored `sz`（共享基础）

- 复制本地 `~/Documents/工作/saas工单常见问题/signoz-cli/sz` → 仓库 **`scripts/sz`**（可执行）
- 连同其 `README.md` → `scripts/signoz-cli-README.md`（保留用法文档）
- **Dockerfile 无需改**：第 47 行已有 `COPY scripts ./scripts`，sz 自动进镜像 `/app/scripts/sz`
- 容器已有 python3.12，零额外依赖
- **sz 需修一处**：让 `--json` 在所有子命令（尤其 query/errors/traceback/services）生效。vendored 副本改，不影响本地原版
- **明文 read key**：脚本第 40 行硬编码只读 key，vendored 时保留（已只读 + 私有仓库），PR 注明

### 组件 1：subprocess 调用基础设施（共享）

新建 `src/zd_agent/integrations/sz_cli.py`：

- `async def run_sz(*args: str, timeout: float = 30) -> dict | list | None`
- 实现：`asyncio.create_subprocess_exec(sys.executable, SZ_PATH, "--json", *args)`，解析 stdout JSON
- `SZ_PATH` 常量（容器 `/app/scripts/sz`；env `ZD_SZ_PATH` 可覆盖；本地开发回退仓库相对路径）
- env 注入：`SIGNOZ_URL` / `SIGNOZ_API_KEY` 从 `cfg.signoz` 透传（缺省则 sz 用内置默认）
- 失败（非零退出 / 超时 / 非 JSON）→ 返回 None，记 warning，不抛
- 被 saas_version.py 和 signoz_tools.py 共用

### 组件 2：`tools/saas_version.py`（版本核心）

```python
@dataclass(frozen=True)
class DeployedVersion:
    image_tag: str        # "hotfix-1.14.2-fix.4"  ← SigNoz container.image.tag
    git_ref: str          # "hotfix/1.14.2-fix.4"  ← 转换+验证后可 git_show 的 ref
    base_version: str     # "1.14.2"
    ref_verified: bool    # git_ref 是否真的在 dify 仓存在
    source: str           # "signoz" | "saas-deploy-fallback"

async def resolve_deployed_version(*, use_cache: bool = True) -> DeployedVersion | None:
    # 读不到任何东西 → None；绝不抛异常打断工单
```

流程：
1. 命中进程内缓存（5 min TTL）→ 返回
2. **主路径 sz**：`run_sz("query", SQL, "--hours", "3")`，SQL：
   ```sql
   SELECT DISTINCT
     resources_string['container.image.tag'] AS image_tag,
     resources_string['service.version']     AS service_version
   FROM signoz_logs.distributed_logs_v2
   WHERE timestamp >= {start_ns}
     AND resources_string['k8s.deployment.name'] = 'dify-api-oltp'
     AND resources_string['deployment.environment'] = 'production'
   LIMIT 5
   ```
   - 多个 distinct tag（滚动发布中）→ 全列出，注入段标注「发布中：fix.3→fix.4」
   - `source = "signoz"`
3. **兜底 saas-deploy**：sz 失败/0 行 → `fetch_repo` 后 `git show main:environments/production/dify/api-oltp/kustomization.yaml` 抽 `newTag`，`source = "saas-deploy-fallback"`
4. **tag→ref 转换**（共用）：
   - `hotfix-1.14.2-fix.4` → `hotfix/1.14.2-fix.4`（`hotfix-` → `hotfix/`）；纯 tag（`1.14.2`）原样
   - `git rev-parse --verify` 验证；**失败先 auto-fetch dify 仓再验**（复用 `git_source._auto_fetch_repo`）；仍失败 → `ref_verified=False`，`git_ref` 回退为 image_tag
   - `base_version`：正则抽 `\d+\.\d+\.\d+`
5. 写缓存，返回

### 组件 3：动态 prompt 段（版本注入）

- `runtime/prompt/defaults.py` 的 `register_dynamic_sections` 加 `deployed_version` 段（**必须动态**——版本会变，进静态段破坏 prefix cache）
- `PromptContext` 加 `deployed_version: dict | None`
- runner 构造 dynamic user context 前调 `resolve_deployed_version()` 填入
- 渲染（`ref_verified=True`）：
  ```
  ## 线上部署版本（cloud.dify.ai 生产 · SigNoz 实时遥测）
  - dify-api 运行中镜像：hotfix-1.14.2-fix.4
  - 对应源码 ref：hotfix/1.14.2-fix.4 ✓（git_show/git_grep 查这个 = 线上实际代码）
  - 更多细节用 saas_deployed_version 工具
  ```
- `condition`: None → 整段消失；`ref_verified=False` 或 fallback 来源时文案标注不确定性

### 组件 4：深挖工具 `saas_deployed_version`

- 在 `tools/saas_version.py` 内，注册到 `source_code` 组，无参
- description 写明触发场景（判断 bug 是否已修/功能是否上线/对应哪版代码）
- 返回结构化：运行镜像 tag / service.version / 可 git_show 的 ref / ref 是否验证 / 数据来源
- 复用 `resolve_deployed_version()`

### 组件 5：SigNoz 日志查询工具（替换 MCP）`tools/signoz_tools.py`（新）

四个工具，各包 sz 一个子命令，均注册到 `platform_issue` 组：

| 工具 | sz 子命令 | 参数 | 用途 |
|---|---|---|---|
| `signoz_errors` | `errors` | tenant?, grep?, deployment?, env=production, hours=1, limit=30 | 搜 ERROR/FATAL 日志 |
| `signoz_traceback` | `traceback` | id? / tenant? / grep?, hours=24, limit=5 | 拉完整 Python stacktrace |
| `signoz_services` | `services` | env=production, hours=1 | 列活跃 service/deployment（fallback 用） |
| `signoz_query` | `query` | sql(必填), hours | raw ClickHouse SQL 兜底 |

- 每个 execute 内调 `run_sz(subcmd, ...args)`，把 sz JSON 转成精简文本/表格返回
- `max_output_tokens` 限制（沿用 MCP 工具的 8000，避免 message_store 暴涨）
- env 默认 `production`（99% 工单是 prod）
- 失败返回明确 error 文本（不抛）

### 组件 6：弃用 MCP

- `registry.py` 的 `augment_registry_async`：不再 spawn SigNoz MCP（删 `discover_signoz_mcp_tools` 调用，或加开关默认关）
- `signoz_mcp_tools.py`：**保留文件**（备用），不删
- 镜像里 binary：保留不删（不占功能面）
- 新的 `signoz_*` 工具走 `create_tool_registry()` 同步注册（不再依赖 async augment）

### 组件 7：prompt 改写（全面）

把所有引用旧 MCP 工具名的地方改成 sz 工具名 + sz 用法：

| 文件 | 改动 |
|---|---|
| `prompts/TOOLS.md` | SigNoz 段（31-34 行）：`signoz_list_services`/`signoz_search_logs`/... → `signoz_errors`/`signoz_traceback`/`signoz_services`/`signoz_query` |
| `prompts/AGENTS.md` | 30 行 `signoz_search_logs`/`signoz_aggregate_logs` → 新工具名 |
| `prompts/references/subagent_guide.md` | 23 行示例 tools 列表 → 新工具名 |
| `prompts/strategies/platform_issue.md` | 第 20-91 行整段「SigNoz 查询规则」重写：把 MCP 的 service.name/searchText 那套，改成 sz 子命令用法（sz errors/traceback/query + ClickHouse 字段约定 resources_string/attributes_string）|
| `prompts/strategies/workflow.md` | 286/306 行 SigNoz 引用对齐新工具 + dify-app-worker deployment 过滤改成 sz `--deployment` 参数 |
| `prompts/strategies/api.md` | 41 行 SigNoz 引用对齐 |
| `tools/subagent_parallel.py` | 56-57/97 行示例 `tools=['signoz_search_logs', ...]` → 新工具名（代码内字符串）|

- 注意 `platform_issue.md` 已有的「7. 镜像版本确认」段（78-87 行）改成「用 saas_deployed_version 工具 / sz query container.image.tag」

## 错误处理 / 降级

版本与日志都是「锦上添花」，任何失败不中断工单：
- sz 不通 → 版本降级 saas-deploy；日志工具返回明确 error
- 两源皆失败 → 版本 resolve 返回 None → 注入段消失
- ref 验证失败 → `ref_verified=False`，不让 agent 拿到查不到的 ref 当真
- 工具层一律捕获异常返回 error 文本，不抛

## 性能 / 缓存

- 版本查询进程内缓存 5 min（注入段与深挖工具共享；版本几小时才变一次）
- 日志查询**不缓存**（每次都是实时诉求）
- sz subprocess 超时 30s

## 测试

- `sz_cli.py`：run_sz 成功解析 JSON / 非零退出→None / 超时→None / env 透传
- sz `--json` 修复：query/errors/traceback/services 带 `--json` 不再报错（冒烟）
- `saas_version.py`：tag→ref 转换各 case；sz 主路径（mock run_sz）；降级 saas-deploy；None 路径；缓存命中
- 注入段：有版本 / None / ref 未验证 三种渲染
- `saas_deployed_version` 工具：成功 / None / 异常
- `signoz_tools.py`：四工具各 mock run_sz 成功 + 失败；env 默认 production
- registry：不再注册 MCP signoz 工具；注册了新 signoz_* 工具
- prompt 改写后无残留旧工具名（grep 校验 `signoz_search_logs`/`signoz_aggregate_logs`/`signoz_list_services`/`signoz_get_field` 为 0）

## 配置 / 部署

- vendored sz → `scripts/sz`（Dockerfile 已 COPY scripts，无需改）
- 复用现有 `signoz` config（url + api_key）与 `repos`（saas-deploy 已在列）
- 无新增 Python 依赖
- 内置常量：sz 路径（env `ZD_SZ_PATH` 可覆盖）、kustomization 路径、目标 deployment `dify-api-oltp`、缓存 TTL、版本 SQL 模板
- 部署：vendored sz → build+push 镜像 → 服务器 pull+重启（复用一键部署）

## 非目标（YAGNI）

- 版本只支持 dify-api（不含 web / worker）
- 不集成 git_commit（生产为空）
- 不做「声明版本 vs 运行版本」对比面板
- **删除** signoz_mcp_tools.py（只停注册，保留备用）
- 不为 sz 的 schema 子命令单独包工具（agent 需要时用 signoz_query 跑 `DESCRIBE`）
