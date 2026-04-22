# API / 端点问题处理策略

客户通过 API 或 SDK 调用出错（401/403/429/500 等）。

## 关键步骤
1. 收集：Workspace ID、具体端点、请求示例（curl/代码）、完整报错
2. 检查 API Key 是否有效 / 所属 tenant 匹配（sql_query 可查）
3. 并行派发 subagent：
   - 源码（API endpoint 实现）+ SDK 代码
   - GitHub（搜索 endpoint 名 + 错误码）
   - Cloudflare / SigNoz 请求日志

## 常见根因
- 鉴权头格式错误 / key 过期
- 配额 / 速率限制
- Payload schema 不符（版本差异）
- CORS / 跨域
