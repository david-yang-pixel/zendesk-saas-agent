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
| SigNoz 查询实现 | **vendored `sz` CLI + subprocess shell-out**（把 sz 纳入仓库、打进容器镜像，agent 调它）|
| SigNoz URL | `signoz.dify.dev`（sz 默认值 = 现有 config，零改动）|

### SigNoz 实例 URL 实测对比（2026-05-30）

| 实例 | 来源 | 查 dify-api-oltp image tag |
|---|---|---|
| `https://signoz.dify.dev` | 服务器 config + sz 默认 | ✅ `hotfix-1.14.2-fix.4` |
| `https://dify.us2.signoz.cloud` | 本地 Cursor MCP 配置 | ❌ 0 行（key 不匹配该实例数据）|

定稿用 `signoz.dify.dev`。

## 架构

```
   ┌─────────────────────────────────────────────────┐
   │  saas_version.py（新）                            │
   │  async resolve_deployed_version(use_cache=True)   │
   │   主: subprocess 调 vendored sz → dify-api-oltp 的 │
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

### 组件 0：vendored `sz` CLI（纳入仓库 + 打进镜像）

- 把本地 `~/Documents/工作/saas工单常见问题/signoz-cli/sz` 复制进仓库：**`scripts/sz`**（可执行，零依赖纯 Python3 stdlib）
- 连同 `signoz-cli/README.md` 一起 vendored 到 `scripts/`（保留用法文档）
- `Dockerfile` runtime 阶段加一行 `COPY scripts/sz /app/scripts/sz`（容器已有 python3.12，无需额外依赖）
- sz 内置默认 `SIGNOZ_URL=https://signoz.dify.dev` + fallback read key，容器内零配置可跑；若容器有 `SIGNOZ_API_KEY` env 则自动覆盖
- **改动注意**：sz 第 40 行硬编码了一个明文 read key。vendored 进公开/私有仓库时保留（已是只读 key，且仓库私有），但在 spec/PR 注明这是已知的只读凭据

### 组件 1：subprocess 调用层（`saas_version.py` 内部函数，不单独建文件）

职责单一：用 subprocess 跑 vendored sz，拿回 dify-api 版本行。

- `async def _query_signoz_via_sz() -> dict | None`
- 实现：`asyncio.create_subprocess_exec(sys.executable, "/app/scripts/sz", "query", "<SQL>", "--hours", "3", "-q")`，解析 sz 的对齐文本输出（或给 sz 加 `--json` 走 JSON——见下"sz 改动"）
- sz 路径来自常量（容器内 `/app/scripts/sz`；本地开发用仓库相对路径），可被 env `ZD_SZ_PATH` 覆盖
- 超时 30s；非零退出 / 解析失败 → 返回 None（上层降级，不中断工单）

#### sz 需要的小改动（vendored 后）

实测发现 sz 的 `--json` 在 `query` 子命令下报 `unrecognized arguments`（README 写了但全局 flag 没贯通到子命令的输出路径）。两个选项：
- **(A) 解析对齐文本**：sz query 默认输出是对齐表格，行可解析（首行 meta 到 stderr，`-q` 可静音）。零改 sz。
- **(B) 修 sz 让 `--json` 在 query 生效**：更稳健，agent 拿结构化 JSON。改动小（emit 已支持 json，只是 arg 传递问题）。

定稿取 **(B)**：vendored 时顺手修好 `--json`，让 subprocess 层拿 JSON，避免文本解析脆弱。这是 vendored 副本，改它不影响你本地原版。

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
2. **主路径 SigNoz**：`_query_signoz_via_sz()` 跑下面 SQL（透传给 sz query），取 `image_tag`
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
  - SigNoz 路径：mock `_query_signoz_via_sz`（patch subprocess 或注入假 sz）返回 → 解析正确
  - 降级路径：SigNoz 返回 None → 回退 saas-deploy → `source` 正确
  - None 路径：两源都失败 → 返回 None
  - 缓存：第二次调用不再起 subprocess
- sz `--json` 修复后的子命令冒烟测试（query 子命令带 `--json` 不再报错）
- 注入段单测：有版本 / 无版本（None）/ ref 未验证 三种渲染
- 工具单测：mock 成功 / None / 异常

## 配置 / 部署

- vendored `sz` 到 `scripts/sz`；`Dockerfile` runtime 阶段 `COPY scripts/sz /app/scripts/sz`
- 复用现有 `signoz` config（url + api_key）与 `repos`（saas-deploy 已在列，别名 `deploy`）
- 无新增 Python 依赖（sz 纯 stdlib；容器已有 python3.12 + git）
- 内置常量（一处改）：sz 路径（`/app/scripts/sz`，env `ZD_SZ_PATH` 可覆盖）、kustomization 路径、目标 deployment 名 `dify-api-oltp`、缓存 TTL、SQL 模板
- **部署步骤**（落地时）：vendored sz → 改 Dockerfile → 重新 build+push 镜像 → 服务器 pull+重启（复用现有一键部署）。sz 不打进镜像则容器内无此文件、工具直接降级

## 非目标（YAGNI）

- 不支持除 dify-api 外的其它服务（web / worker）——多数工单是后端 bug，api tag 即关键答案
- 不集成 git_commit（生产为空，无意义）
- 不做「声明版本 vs 运行版本」对比面板——SigNoz 主 + saas-deploy 兜底已足够，对比逻辑会过度复杂化
- 不改 `signoz-mcp-server`（已知对 EE 不兼容，本任务用 vendored sz 绕过；MCP 工具是否保留是独立问题）
- 不把 sz 做成 agent 的通用日志查询工具（本任务只用它查版本；sz 的 errors/traceback 等能力是否暴露给 agent 是独立决策）
