# API / 端点问题处理策略

客户通过 API 或 SDK 调用出错（401/403/429/500 等），或第三方插件认证失败
（Lark / Notion / Slack / Google Drive / 飞书多维表格 等）。

## ⚡ Subagent A 必查路径（**派 subagent A 时把这些路径明写进任务描述**）

### 主 API endpoint 类
| 仓库 | 路径 |
|---|---|
| dify | `api/controllers/*` — endpoint 定义 |
| dify | `api/services/*` — 业务逻辑 |
| dify | `api/core/app/*` — app runtime |

### **插件类（Lark Bitable / Notion / Slack / Google Drive / 飞书）必走 GitHub 远程搜**
官方插件源码不一定在 Source MCP runtime 仓库里。插件类先用 `github_code_search` 覆盖
`langgenius/dify-official-plugins`，不要只看 dify 主仓就下结论：

```
github_code_search query="<feature 名 + 错误关键词>" scope="langgenius/dify-official-plugins"
```

| 客户问题关键词 | scope |
|---|---|
| Lark / Larksuite / 飞书 / Bitable | `langgenius/dify-official-plugins` |
| Notion | `langgenius/dify-official-plugins` |
| Slack / Discord / Teams | `langgenius/dify-official-plugins` |
| Google Drive / Sheets / Docs | `langgenius/dify-official-plugins` |
| invalid_credential / unauthorized / scope | 同上，配合 query="<plugin> auth" / "InvalidCredential" |

⚠️ **反例（之前 #2313）**：客户报 Lark Bitable invalid credential，agent 只 git_grep dify 主仓搜 0 hit 就下结论"无相关代码" → 完全错过 dify-official-plugins 仓里的 lark_trigger / lark_bitable 实现。

## 关键步骤
1. 收集：Workspace ID、具体端点 / 插件名、请求示例（curl/代码）、完整报错
2. 检查 API Key 是否有效 / 所属 tenant 匹配（sql_query 可查 `apps` 表）
3. **判断主 API 类还是插件类**：
   - 主 API → git_grep dify/api/controllers + dify/api/services
   - 插件类 → 直接 github_code_search scope=langgenius/dify-official-plugins
4. 并行派发 subagent：
   - A. 源码（按上面分类选）
   - B. GitHub Issues/PRs（搜 endpoint 名 + 错误码）
   - C. SigNoz 请求日志（5xx 才需要，用 `signoz_errors --deployment dify-api-oltp`）

## 常见根因
- 鉴权头格式错误 / API key 过期
- 配额 / 速率限制
- Payload schema 不符（版本差异）
- **插件特定**：scope 没勾选 / 应用没 publish / 用错了 token type / Feishu vs Lark 域名错
