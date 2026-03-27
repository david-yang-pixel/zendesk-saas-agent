## 配置/部署类工单处理策略

### 检索优先级调整

- **优先**：Helm Watchdog（`helm_get_values` 对比默认配置与客户配置差异）、官方文档（`official_docs_search`）、Zendesk Help Center（`zendesk_search_articles`）
- **同样重要**：源码中的配置文件 — `values.yaml`、`docker-compose.yaml`、`.env.example`、环境变量定义、配置解析逻辑等，这些是理解配置项含义和默认值的第一手来源
- 当文档不够清晰或客户配置项在文档中找不到时，源码中的 config 文件往往是唯一权威来源

### 分析重点

- 对比客户配置与默认 `values.yaml` 的差异，尤其关注客户主动覆盖的字段
- 如果涉及版本升级，用 `helm_get_values` 分别拉取新旧版本的 values，对比是否有配置项变更（重命名、废弃、默认值改变）
- 关注环境特定因素：K8s 版本兼容性、存储类（StorageClass）、网络策略（NetworkPolicy）、Ingress 控制器差异
- 镜像拉取问题优先用 `helm_validate_images` 验证架构兼容性（amd64/arm64）

### 回复要求

- 给出具体的配置修改片段（YAML 格式代码块），不要笼统说"修改配置"
- 如果涉及 Helm upgrade，给出完整命令示例，包含 `--set` 或 `-f` 参数
- 如果是环境兼容问题，说明最低版本要求或已知不兼容清单
