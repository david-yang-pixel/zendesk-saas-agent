# 知识库 / 数据集 / 检索问题处理策略

客户报告：文档上传失败、检索结果为空、元数据过滤异常、向量生成问题等。

## ⚡ Subagent A 必查路径

| 仓库 | 路径 |
|---|---|
| dify | `api/services/dataset_service.py` |
| dify | `api/services/document_service.py` |
| dify | `api/services/segment_service.py` |
| dify | `api/core/rag/*` — retrieval / reranker / vector 入口 |
| dify | `api/core/rag/datasource/*` — 各类数据源 indexing |
| dify | `api/models/dataset.py` — Dataset / Document / DocumentSegment schema |

## 关键 SQL（database=prod，参考顶部 Database Schema 段拿真实列名）
- `datasets WHERE tenant_id = '<wid>'`
- `documents WHERE dataset_id = '<dataset_id>' AND indexing_status != 'completed'` — 失败的 doc
- `document_segments WHERE document_id = '<doc_id>' LIMIT 5` — 看分段质量

## 关键步骤
1. 收集 Dataset ID、Document ID、具体报错或期望 vs 实际；工作空间先由表单 Email（无则 requester email）在支持侧解析。邮箱唯一映射时直接使用，禁止再索取 Workspace ID / workspace name；只有成功查询证明多工作区无法消歧、无 membership 或 canonical ID 冲突时才向客户澄清最小身份信息。
2. SQL 直查 documents / document_segments 状态
3. 如涉及 embedding：查 embeddings 模型配置 + model_provider 额度
4. 并行 subagent：A. 源码（按上表）+ B. GitHub Issues + C. ticket_record_search
5. 参考本地知识库 `saas版知识库/02-knowledge-base.md`

## 常见根因
- Document `indexing_status != 'completed'` → 重试 indexing
- 分段失败 → 格式/大小限制
- Top_k 或 score_threshold 过严
- Metadata filter 语法错误
- Embedding model 配额满 / API key 失效
