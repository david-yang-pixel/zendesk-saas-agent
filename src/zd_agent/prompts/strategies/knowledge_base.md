# 知识库 / 数据集 / 检索问题处理策略

客户报告：文档上传失败、检索结果为空、元数据过滤异常、向量生成问题等。

## 关键步骤
1. 收集：Workspace ID、Dataset ID、Document ID、具体报错或期望 vs 实际
2. SQL 直查（sql_query database=prod）：documents 表、document_segments 表状态
3. 如涉及 embedding：查 embeddings 模型配置 + model_provider 额度
4. 并行派发 subagent：源码（knowledge retrieval 节点）+ GitHub Issues
5. 参考本地知识库 `saas版知识库/02-knowledge-base.md`

## 常见根因
- Document 状态非 completed → 重试 indexing
- 分段失败 → 格式/大小限制
- Top_k 或 score_threshold 过严
- Metadata filter 语法错误
