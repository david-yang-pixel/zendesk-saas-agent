# 开发转交格式（Dev Report）

工单确认是产品 bug 且需转交开发时，Internal Note 中用此格式，纯文字、不用表格。

## 格式

```
问题：<一句话标题>
用户信息：Workspace ID <wid>; 相关 ID：Dataset <did> / Workflow <wid> / Run <rid> 等
现象：<具体错误文本 + 复现步骤>
用户诉求：<客户希望如何处理>
根因（如已定位）：<源码位置 / 调用链 / 原因简述>
相关 PR / Issue：<GitHub 链接，如有>
建议 workaround：<临时方案，如无则写"暂无">
优先级：高/中/低
```

## 示例

```
问题：Knowledge Retrieval 节点元数据过滤失效
用户信息：Workspace ID abc-123-def
现象：在 Workflow 中使用 Knowledge Retrieval 节点，metadata filter `category = "faq"` 未生效，返回了所有段落。API 调用示例：<curl>
用户诉求：希望过滤器按预期生效
根因：metadata filter 在 vector_search 路径中未传递到下游，见 dify/api/core/rag/retrieval/metadata_filter.py:L42
相关 PR：https://github.com/langgenius/dify/pull/12345（待合入）
建议 workaround：改用 full_text_search_method + 自定义 filter
优先级：中
```

## 注意

- 不在此段做冗长的根因分析（根因独立一段更清晰）
- 不承诺修复时间
- 附 GitHub PR / Issue 链接（已确认的）
- 如是 SRE 操作类需求（改数据 / 重启服务 / 扩容），应转交 SRE 而非开发
