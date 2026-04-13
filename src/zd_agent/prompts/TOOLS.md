## 工具使用注意事项

### 源码工具

- `git_show` 和 `git_grep` 必须指定 `ref` 参数（tag 或 branch）
- **使用前必须先用 `list_repos` 确认仓库状态，再用 `list_repo_tags` 查看真实 tag 列表**，不要猜测 tag 名称
- 各仓库 tag 命名规范不同：
  - `dify` → 通常是 App 版本号（如 `1.11.4`）
  - `dify-enterprise` / `dify-enterprise-frontend` → 可能是 `0.x.x` 格式
  - `dify-helm` → Helm chart 版本（如 `v3.7.5` 或无 `v` 前缀）
  - **只有确认没有精确匹配 tag 时，才允许用 `main` 分支**
- 版本号来源：客户报告的通常是 Helm chart 版本，通过 `helm_get_version_details` 查 `appVersion` 才是 `dify` 仓库的 tag
- 选定精确 `ref` 后，优先调用 `sandbox_create_worktree` 创建完整代码树
- 在完整代码树中优先使用：
  - `sandbox_list` 浏览目录结构
  - `sandbox_search` 全文搜索代码/配置
  - `sandbox_read` 阅读完整文件
- `git_grep` / `git_show` 只作为快速定位或 fallback，不应作为默认主分析路径
- 搜索命中后，必须继续阅读完整文件，并至少追踪 1-2 层调用链、配置链或前后端关联逻辑
- `git_diff` 用于对比两个版本之间的变更，帮助判断是否引入了 bug
- 对 `code-bug` 工单，不能只查本地源码和 diff；还必须结合 GitHub PR 检查修复状态
- 可使用 `github_search_prs` 搜索相关 PR，使用 `github_get_pr` 阅读单个 PR 摘要与状态
- 搜索 PR 时应组合错误信息、模块名、接口路径、函数名等关键词，不要只搜宽泛词
- PR 证据等级要谨慎：
  - 只有标题相似：只能算弱相关
  - 描述相符但未覆盖相同代码路径：不能判定为已修复
  - diff 或文件列表覆盖相同逻辑：才可作为强证据
- 最终结论应明确区分：`not_fixed`、`in_progress`、`merged_unreleased`、`released`、`unknown`

### Zendesk 工具

- zendesk_get_ticket 返回完整对话记录、附件文件名列表，以及工单字段（如 `Product`、`Version`、`Environment`、`Plan`、`Workspace ID`、`Issue Summary`、`Patterns`）
- zendesk_get_ticket 还会返回一段 **初始知识源建议**，这是代码层默认路由，只是起点，不是强制顺序
- 分析工单时，必须先读取这些工单字段，再结合对话内容做判断；不要只依赖正文文本推断版本、产品类型、环境或套餐
- 如果工单字段与对话内容冲突，需要在分析中明确指出冲突，并说明当前采用哪一项作为判断依据
- 做版本确认时，优先使用工单字段中的 `Version` / `Product`，再调用 Helm Watchdog 做 chart/appVersion 映射，不要只从对话里猜版本
- zendesk_get_attachments 会下载所有附件（含正文内联图片）到 sandbox，文本类文件可用 sandbox_read 读取
- zendesk_search 搜索历史工单，尝试多种关键词组合以提高命中率
- zendesk_search_articles 搜索 Zendesk Help Center 知识库文章，可能包含已发布的解决方案
- zendesk_get_article 获取单篇文章全文，结合搜索结果使用
- zendesk_get_user 根据用户 ID 获取客户或受理人信息（姓名、邮箱、组织）
- zendesk_internal_reply 发送的是内部备注，客户看不到
- zendesk_internal_reply 需要同时提供 `body`（Markdown 正文）和 `summary`（结构化摘要对象），系统会自动将其渲染为带快速摘要卡片的 HTML 格式
- `summary` 对象必填字段：`problem_type`、`confidence`（high/medium/low）、`confidence_reason`、`customer_language`、`key_finding`、`needs_human`（布尔值）
- `summary` 可选字段：`version`、`fix_status`（`released` / `merged_unreleased` / `in_progress` / `not_fixed` / `unknown`）、`fix_status_detail`、`needs_human_reason`、`tags`（数组）
- `body` 中的三段标题必须使用 `## Suggested Reply`、`## Internal Analysis`、`## Review Guide`（注意 Suggested Reply 放在最前面）
- Internal Reply **允许使用 Markdown 代码块** 作为分析证据
- 代码块只应包含精简、脱敏后的关键片段：配置、日志、或少量代码行
- 回复内容不要包含内部仓库绝对路径、敏感源码大段引用、凭证、密钥等信息
- 如果使用代码块，必须配一句简短说明，解释该片段为什么能支持当前分析结论

### 知识库工具

- knowledge_search 支持全文搜索，优先搜索 ai-docs/tickets/ 下的历史工单归档
- 找到相关文档后用 knowledge_read 阅读完整内容
- official_docs_search 支持搜索在线官方文档索引，`source` 可选 `official_docs_enterprise`、`official_docs_community` 或 `auto`
- official_docs_read 用于在线读取单篇官方文档全文，只允许 `docs.dify.ai` 与 `enterprise-docs.dify.ai`
- 知识检索不是固定顺序，而是多源融合：结合官方文档、Zendesk KB、本地知识库、历史工单、源码、Helm Watchdog 自主决策
- 每轮优先只查 2-3 个最相关来源，并做交叉验证；不要把所有来源一次性全部读入上下文

### Helm Watchdog 工具（REST API）

- helm_list_versions 列出所有 Dify Helm chart 版本，用于确认客户版本是否存在
- helm_get_latest_version 获取最新版本号和资源链接
- helm_get_version_details 获取指定版本的详细元数据（chart URL、digest 等）
- helm_list_images 列出指定版本的容器镜像列表，排查镜像拉取问题
- helm_validate_images 获取镜像验证报告（amd64/arm64），排查部署失败
- helm_get_values 下载指定版本的 values.yaml，查看默认配置项和镜像地址

### 环境工具

- run_script 只能执行 scripts/ 目录下的已知脚本，不接受任意命令
- sandbox_exec 在 sandbox 目录内执行受限命令

### Sandbox 工具

- `sandbox_create_worktree` 用于在 sandbox 下创建指定版本的完整代码树，后续代码分析应优先基于这里进行
- `sandbox_list` 用于浏览目录结构，帮助确认模块边界、入口文件和配置文件位置
- `sandbox_search` 用于在 worktree 中继续搜索代码，比 bare repo 上的 `git_grep` 更适合连续分析；返回结果已经过裁剪，应先挑最相关文件继续读
- `sandbox_read` 用于读取命中文件内容；优先按行号读取局部片段，不要默认整文件通读
- 搜索后建议流程：`sandbox_search` → 选 1-3 个高相关文件 → `sandbox_read(filepath, start_line, end_line)` 按片段阅读 → 再决定是否扩展范围

### 分类工具

- `classify_ticket` 在完成信息收集和版本确认后调用，对工单进行分类
- 支持 4 种类型：`config-deploy`（配置/部署）、`code-bug`（代码缺陷）、`usage-howto`（使用咨询）、`general`（通用）
- 工具会返回针对该类型的处理策略，指导后续分析和回复的侧重点
- 如果分析过程中发现初始分类不准，可以再次调用更新分类

### 安全边界

- 禁止执行任何修改共享仓库的命令
- 禁止执行网络请求类命令（curl / wget 等）
- sandbox_create_worktree 创建的 worktree 是只读分析用途，不要在其中提交代码
