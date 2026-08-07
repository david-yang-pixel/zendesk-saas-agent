# 客户回复模板（SaaS 版）

> **公开回复语言**：客户可见 public reply 一律用 **English** 回复，不跟随客户语言。
> 绝不 EN + 中文双语对照。绝不让 agent 输出中文 / 日语 / 葡语等非英文公开回复。
> 长度上限：≤ 200 字 / 字符（不含签名）。
> 禁止：markdown 超链接 `[name](url)` —— 用 "文档名：URL"。

---

## 1. Missing Critical Info（信息不足）

**English customer**:
```
Hi,

Thanks for reaching out. To diagnose this faster, could you share:
- Full error text or screenshot
- The exact steps you took before the error
- The affected app, workflow, dataset, or page if one is involved

Once we have these we can dig in right away.

Best regards,

Dify Support
```

**中文 customer**:
```
您好，

感谢联系。为了更快定位，请提供：
- 完整报错文本或截图
- 出错前的具体操作步骤
- 涉及的应用、工作流、知识库或页面（如适用）

拿到这些信息我们会立即排查。

此致

Dify Support
```

## 2. Issue Identified, Workaround Available

**English**:
```
Hi,

We've identified this — it's <root cause>. A temporary workaround: <steps>.
We'll ship a fix in an upcoming release; I'll update you when it's live.

Best regards,

Dify Support
```

**中文**:
```
您好，

已经定位到了——是 <根因>。临时绕行方案：<步骤>。
修复会在近期版本上线，上线后会第一时间通知你。

此致

Dify Support
```

## 3. Issue Confirmed, No Workaround (Escalated)

**English**:
```
Hi,

We've confirmed this is a platform-side issue and escalated it to our engineering team.
No reliable workaround yet. I'll follow up as soon as we have an ETA.

Best regards,

Dify Support
```

**中文**:
```
您好，

已确认是平台侧问题，已升级给开发团队。
暂无稳定绕行方案，修复进度有更新我会第一时间通知。

此致

Dify Support
```

## 4. Documentation Reference

**English**:
```
Hi,

This is covered in our docs. See:
Dataset Indexing Guide: https://docs.dify.ai/guides/knowledge-base/indexing
Workflow Variables: https://docs.dify.ai/guides/workflow/variables

If you run into issues following those steps, let me know the specifics and I'll help debug.

Best regards,

Dify Support
```

**中文**:
```
您好，

这个场景我们文档里有说明：
数据集索引指南：https://docs.dify.ai/guides/knowledge-base/indexing
工作流变量：https://docs.dify.ai/guides/workflow/variables

按文档操作遇到具体问题，告诉我错误细节我帮你调。

此致

Dify Support
```

## 5. Sorry for the Wait

- EN: `Hi, sorry for the wait — pulling in our relevant team to look at this carefully.`
- CN: `您好，让你久等了——已经在拉相关同事一起看。`

## 6. Empathetic Opening (Frustrated Customer)

- EN: `Hi, I understand this is frustrating — let me help.`
- CN: `您好，理解你的困扰，我来帮你看。`

后续立即进入排查，不堆废话。

## 7. Follow-up（跟进回复示例）

**跟进**回复应该更短，不重复已说过的内容：

**English**:
```
Thanks for the screenshot. The error confirms it's <具体原因>. This is a known
product-side behavior we're tracking. I'll update you once the fix is available.

Best regards,

Dify Support
```

**中文**:
```
您好，

收到截图，确认是 <具体原因>。这是我们已经在跟进的产品侧行为。
修复上线后我会第一时间通知你。

此致

Dify Support
```

---

## Anti-patterns（反模式，**禁止**）

- ❌ **EN + 中文双语对照**（即使客户是中文母语，也只输出中文一遍即可）
- ❌ **给非中文客户写中文**（包括日语客户）
- ❌ **给日语客户写日语 / 葡语客户写葡语 / 韩语客户写韩语 ...** —— agent 的非中英文输出
  质量不可控（语法错、敬语错、文化差异），一律用 English 兜底
- ❌ 每次都 "Hi there,"（机器人感太强）
- ❌ `We sincerely apologize for any inconvenience this may have caused.`
- ❌ `I hope this email finds you well.`
- ❌ `尊敬的用户，感谢您的耐心等待` 类官话
- ❌ 一句话能说清的写三段
- ❌ 使用 `>` blockquote（Zendesk 渲染会显示竖杠影响阅读）
- ❌ Markdown 超链接 `[text](url)`（Zendesk 不统一渲染；用 "文档名：URL"）

## 写作节奏

1. **开头**自然（Hi / Hey / Thanks for the update / Got it / 您好 / 收到 …）
2. **正文**有过渡（先说查了什么 / 发现了什么，再给方案）
