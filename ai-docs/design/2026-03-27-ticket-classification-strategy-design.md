# 工单分类 + 条件策略注入设计

## 背景

当前 zd-agent 采用单 Agent + ToolLoop 架构，system prompt 由 SOUL.md + AGENTS.md + TOOLS.md 静态拼接。所有类型的工单使用完全相同的处理流程和指令权重。

### 问题

- 不同类型的工单（配置问题 vs 代码缺陷 vs 使用咨询）需要不同的分析侧重点和工具优先级
- 单一 Prompt 无法为每种类型提供专精指导，导致处理质量因工单类型而波动
- 随着支持场景增多，AGENTS.md 有膨胀趋势

### 设计目标

- 用最小改动解决工单类型差异问题
- 不引入 subagent 编排复杂度
- 不改动核心执行引擎（tool_loop、runner、guardrails）

## 方案：classify_ticket 工具 + 策略文件

### 核心思路

新增一个 `classify_ticket` 工具，LLM 在信息收集（Step 1-2）完成后主动调用。工具内部读取对应的策略 `.md` 文件，将策略文本作为 tool result 返回到对话上下文中，引导后续 Steps 3-7 的执行侧重。

### 工单类型划分（4 种）

| 类型 ID | 适用场景 | 举例 |
|---------|---------|------|
| `config-deploy` | 配置/部署/环境问题 | Helm values 不生效、Pod CrashLoop、镜像拉取失败、SSL/域名 |
| `code-bug` | 代码缺陷/回归/异常行为 | 升级后报错、API 返回非预期、工作流执行中断 |
| `usage-howto` | 使用方法/功能咨询 | 如何配置 SSO、如何开启某功能、如何迁移数据 |
| `general` | 无法明确归类 / 复合问题 | 兜底，不注入额外策略 |

### 数据流

```
Step 1-2: LLM 收集信息、确认版本
    ↓
Step 2.5: LLM 调用 classify_ticket(ticket_type="code-bug", reasoning="...")
    ↓
classify_ticket 读取 strategies/code-bug.md，返回策略文本
    ↓
策略文本作为 tool result 自然进入 messages
    ↓
Step 3-7: LLM 按策略侧重执行后续步骤
```

### classify_ticket 工具设计

**参数：**

- `ticket_type: str` — 枚举值：`config-deploy` | `code-bug` | `usage-howto` | `general`
- `reasoning: str` — 分类依据（简要说明选择理由）

**执行逻辑：**

1. 校验 `ticket_type` 是否合法，非法则 fallback 到 `general`
2. 用 `importlib.resources` 从 `prompts/strategies/` 读取对应 `.md` 文件
3. 记录 `ticket_type` 和 `reasoning` 到 session
4. 返回策略文本

**工具描述（给 LLM 的）：**

> 信息收集和版本确认完成后，对工单进行分类并获取针对性的处理策略。必须在 Step 2 完成后、Step 3 开始前调用。

### 策略文件结构

```
src/zd_agent/prompts/
├── SOUL.md
├── AGENTS.md
├── TOOLS.md
└── strategies/
    ├── __init__.py
    ├── config-deploy.md   # 配置/部署类策略
    ├── code-bug.md        # 代码缺陷类策略
    ├── usage-howto.md     # 使用咨询类策略
    └── general.md         # 兜底（默认流程）
```

### 策略文件内容定位

策略文件**补充**而非替代 AGENTS.md。它告诉 LLM 在执行 Steps 3-6 时的侧重点和优先级。

每个策略文件约 30-40 行，包含三部分：
- **检索优先级调整**：针对该类型，哪些知识源优先、哪些可以跳过
- **分析重点**：该类型问题最应关注什么
- **回复要求**：该类型的回复格式和内容侧重

### 涉及改动的文件

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/zd_agent/tools/classify.py` | 新增 | ClassifyTicket 工具实现 |
| `src/zd_agent/tools/registry.py` | 修改 | 注册新工具（~2 行） |
| `src/zd_agent/session/agent_session.py` | 修改 | 加 `ticket_type`、`classification_reasoning` 字段 |
| `src/zd_agent/prompts/AGENTS.md` | 修改 | 插入 Step 2.5 说明（~3 行） |
| `src/zd_agent/prompts/TOOLS.md` | 修改 | 加 classify_ticket 工具说明（~5 行） |
| `src/zd_agent/prompts/strategies/*.md` | 新增 | 4 个策略文件 |

### 不需要改动的文件

- `runner.py` — 零改动
- `tool_loop.py` — 零改动
- `prompt_loader.py` — 零改动
- `guardrails.py` — 零改动
- `config.py` — 零改动

## 未来扩展

- 新增工单类型：只需添加策略 `.md` 文件 + 在 classify_ticket 的 enum 里加一个值
- 如果未来需要更强的隔离（subagent），策略文件已按类型组织，拆分成本低
- 归档时可将 `ticket_type` 写入 `meta.json`，用于统计各类型工单的处理质量
