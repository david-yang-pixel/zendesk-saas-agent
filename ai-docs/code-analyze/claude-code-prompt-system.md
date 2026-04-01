---
name: claude-code-prompt-system
description: Claude Code 完整 Prompt 体系拆解（基于 v2.1.88 源码）
created_at: 2026-04-01
---

# Claude Code 完整 Prompt 体系拆解

Claude Code 的 prompt 不是一个大字符串，而是一个**分层组装系统**。它由 system prompt、user context、system context 三部分组成，在发送 API 请求前动态拼装。本文档把每一层、每一段都列出来。

---

## 一、整体结构

```
API 请求
├── system（system prompt + systemContext 追加）
│   ├── 静态块（可缓存，跨会话复用）
│   │   ├── Intro（身份定义）
│   │   ├── System（输出规范、权限、hooks）
│   │   ├── Doing Tasks（工程任务指引）
│   │   ├── Actions（谨慎执行）
│   │   ├── Using Your Tools（工具使用总则）
│   │   ├── Tone and Style（语气风格）
│   │   └── Output Efficiency（输出效率）
│   ├── ── DYNAMIC_BOUNDARY ──（缓存分界线）
│   ├── 动态块（会话级，每次可能不同）
│   │   ├── Session Guidance（会话特定指引）
│   │   ├── Memory（记忆 prompt）
│   │   ├── Environment（环境信息）
│   │   ├── Language（语言偏好）
│   │   ├── Output Style（输出风格）
│   │   ├── MCP Instructions（MCP 服务说明）
│   │   ├── Scratchpad（临时目录）
│   │   ├── FRC（工具结果清理说明）
│   │   ├── Summarize Tool Results（工具结果摘要提醒）
│   │   └── Token Budget / Brief 等条件段
│   └── systemContext 追加
│       ├── gitStatus（git 仓库快照）
│       └── cacheBreaker（调试用）
│
└── messages
    ├── [meta user message]  ← userContext 注入
    │   ├── claudeMd（CLAUDE.md 等 workspace 记忆）
    │   └── currentDate（当前日期）
    ├── user message 1
    ├── assistant message 1
    └── ...
```

---

## 二、System Prompt 各段详解

### 静态块（缓存边界之前）

这些段落内容相对固定，可被 Anthropic 的 prompt cache 缓存。

#### 1. Intro（`getSimpleIntroSection`）

**用途**：定义 Claude Code 的身份和基本行为约束。

**核心内容**：
- "You are an interactive agent that helps users with software engineering tasks"
- 如果配置了 Output Style，会提及"按照指定风格回复"
- 嵌入 `CYBER_RISK_INSTRUCTION`（网络安全风险指令）
- "IMPORTANT: You must NEVER generate fake/fabricated URLs, API endpoints, or web addresses"

**动态变量**：`outputStyleConfig`（是否有自定义输出风格）

#### 2. System（`getSimpleSystemSection`）

**用途**：输出规范、权限模式、外部数据处理、hooks 说明。

**核心内容**：
- "All text you output outside of tool use is displayed to the user"
- 权限相关：标签含义、分类提示
- 外部数据（MCP、用户消息）可能不可信
- `getHooksSection()`：用户可配置 hooks（shell 命令），hooks 反馈视同用户输入
- 上下文压缩说明

**动态变量**：基本为静态

#### 3. Doing Tasks（`getSimpleDoingTasksSection`）

**用途**：工程任务执行的总体原则。

**核心内容**：
- "The user will primarily request you to perform software engineering tasks"
- 先读后改、优先编辑已有文件而非新建
- 安全：不暴露 secrets
- 代码风格：跟随项目现有约定
- 包管理：用包管理器安装而非杜撰版本号
- Linter 错误：修改后检查
- `/help` 和反馈指引

**动态变量**：`USER_TYPE`（ant 内部用户有额外条目）、`MACRO.ISSUES_EXPLAINER`（构建期宏）

**条件存在**：如果 `outputStyleConfig.keepCodingInstructions === false`，这整段不出现。

#### 4. Actions（`getActionsSection`）

**用途**：要求模型谨慎执行有副作用的操作。

**核心内容**：
- 考虑操作的可逆性和影响范围
- 高风险操作（如 `git push --force`、批量删除等）需要确认
- 给出具体场景示例

**动态变量**：无，纯静态

#### 5. Using Your Tools（`getUsingYourToolsSection`）

**用途**：工具使用的总体指引。

**核心内容**：
- "Use your specialized tools instead of Bash whenever possible"
- Bash 安全指引（引号、避免 heredoc 等）
- Task 工具使用规范
- "Issue multiple tool calls at once whenever possible"（鼓励并行工具调用）
- REPL 模式下会简化

**动态变量**：`enabledTools`（实际启用的工具列表）、`isReplModeEnabled`、`hasEmbeddedSearchTools`

#### 6. Tone and Style（`getSimpleToneAndStyleSection`）

**用途**：输出的语气和格式规范。

**核心内容**：
- 不主动使用 emoji（除非用户要求）
- 代码引用格式规范
- 数学公式格式（`\(` 和 `\[`）
- URL 使用 markdown 链接
- 工具调用前不要用冒号（"Let me read the file." 而非 "Let me read the file:"）

**动态变量**：`USER_TYPE`（ant 有额外简洁回复要求）

#### 7. Output Efficiency（`getOutputEfficiencySection`）

**用途**：控制输出长度和效率。

**两个版本**：
- **ant 内部版**（`# Communicating with the user`）：长版沟通指引
- **外部版**（`# Output efficiency`）："Go straight to the point, avoid unnecessary preamble"

**动态变量**：`USER_TYPE`

---

### 缓存分界线

```
__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__
```

仅在 `shouldUseGlobalCacheScope()` 为真时插入。告诉 Anthropic API：这个标记之前的内容可以全局缓存，之后的内容会话级变化。

---

### 动态块（缓存边界之后）

这些段落每个会话可能不同，通过 `systemPromptSection` / `DANGEROUS_uncachedSystemPromptSection` 注册。

#### 8. Session Guidance（`getSessionSpecificGuidanceSection`）

**用途**：会话特定的行为指引，是最"杂"的一段。

**核心内容**：
- 工具调用被拒绝时如何应对
- `!` 快捷方式（跑命令而不需要 Bash 工具）
- **Agent 工具使用指引**（`getAgentToolSection`）：
  - 何时使用 Explore 子代理
  - fork 子代理 vs 经典子代理
  - 验证子代理
- **技能系统**（`/skill` 命令和 `getDiscoverSkillsGuidance`）
- 非交互会话的特殊行为

**动态变量**：`enabledTools`、`skillToolCommands`、各工具名常量、`isNonInteractiveSession`

#### 9. Memory（`loadMemoryPrompt`）

**用途**：加载记忆目录中的持久化内容。

**来源**：`memdir` 等记忆文件系统

**动态变量**：完全动态，异步加载

#### 10. Environment（`computeSimpleEnvInfo`）

**用途**：告诉模型它运行在什么环境中。

**核心内容**（`# Environment` 格式）：
- Primary working directory: `/path/to/project`
- Git worktree 信息（如果有）
- Git repository: yes/no
- 附加目录列表
- Platform: macOS / Linux / Windows
- Shell: zsh / bash 等
- OS version
- 当前模型描述与名称
- Knowledge cutoff: August 2025 等
- Claude 产品家族说明
- Fast 模式状态

**动态变量**：`getCwd()`、`isGit`、`platform`、`shell`、`model`、`knowledgeCutoff` 等

#### 11. Language（`getLanguageSection`）

**用途**：按用户语言偏好回复。

**核心内容**：
```
# Language
Always respond in {languagePreference}. Use {languagePreference} for all explanations...
```

**条件存在**：无语言偏好设置时为 `null`，不出现

#### 12. Output Style（`getOutputStyleSection`）

**用途**：用户自定义的输出风格。

**核心内容**：
```
# Output Style: {name}
{用户定义的 prompt 文本}
```

**条件存在**：无自定义风格时不出现

#### 13. MCP Instructions（`getMcpInstructionsSection`）

**用途**：MCP 服务的使用说明。

**核心内容**：
```
# MCP Server Instructions
The following MCP servers have provided instructions...

## {server_name}
{server_instructions}
```

**特殊标记**：`DANGEROUS_uncachedSystemPromptSection`——MCP 连接状态随时变化，不可缓存

**动态变量**：MCP 连接列表、每个 server 的 name 和 instructions

#### 14. Scratchpad（`getScratchpadInstructions`）

**用途**：会话临时目录的使用说明。

**核心内容**：
```
# Scratchpad Directory
IMPORTANT: Always use this scratchpad directory for temporary files: {scratchpadDir}
```

**条件存在**：未启用 scratchpad 时不出现

#### 15. FRC（`getFunctionResultClearingSection`）

**用途**：告诉模型旧的工具结果会被自动清理。

**核心内容**：
```
# Function Result Clearing
Old tool results will be automatically cleared to manage context...
Keep the most recent {keepRecent} results...
```

**动态变量**：`model`、微压缩配置

#### 16. Summarize Tool Results（`SUMMARIZE_TOOL_RESULTS_SECTION`）

**用途**：提醒模型把重要信息写入回复，因为工具结果可能被清理。

**核心内容**：
```
When working with tool results, write down any important information you might need later 
in your response, as the original tool result may be cleared later.
```

**动态变量**：无，纯静态

#### 17-19. 条件段

| 段 | 条件 | 内容 |
|----|------|------|
| `numeric_length_anchors` | `USER_TYPE === 'ant'` | 数字长度锚点，减少输出 token |
| `token_budget` | `feature('TOKEN_BUDGET')` | 用户指定 token 目标时的行为 |
| `brief` | `feature('KAIROS')` 相关 | Brief 工具的自主工作说明 |

---

## 三、User Context（注入到消息列表开头）

通过 `prependUserContext` 在消息数组最前面插入一条 **meta user message**：

```xml
<system-reminder>
The following is contextual information you may find useful. 
Only respond to this if it's highly relevant to the task.

# claudeMd
{CLAUDE.md 等 workspace 记忆文件的全部内容}

# currentDate
Today's date is 2026-04-01.
</system-reminder>
```

### `claudeMd` 的来源（CLAUDE.md 发现规则）

按以下顺序从低优先级到高优先级加载，后面的会出现在拼接结果的更后面（模型更容易注意到）：

| 优先级 | 来源 | 路径示例 |
|--------|------|----------|
| 1（最低） | Managed（企业管理） | `/etc/claude-code/CLAUDE.md`、`.claude/rules/*.md` |
| 2 | User（个人全局） | `~/.claude/CLAUDE.md`、`~/.claude/rules/*.md` |
| 3 | Project（项目级，从根目录到 cwd 逐层） | `./CLAUDE.md`、`./.claude/CLAUDE.md`、`./.claude/rules/*.md` |
| 4 | Local（本地未提交） | `./CLAUDE.local.md` |
| 5（最高） | 额外目录（`--add-dir` 指定） | 同上规则扫描 |

**特殊规则**：
- 支持 `@include` 递归引用其他文件（最大深度 5）
- 嵌套 worktree 会跳过主仓中 worktree 根之外的项目文件
- 可通过 `claudeMdExcludes` 配置排除特定路径
- `CLAUDE_CODE_DISABLE_CLAUDE_MDS` 环境变量可完全禁用

### `currentDate`

```
Today's date is {YYYY-MM-DD}.
```

来自 `getLocalISODate()`，可通过 `CLAUDE_CODE_OVERRIDE_DATE` 环境变量覆盖。

---

## 四、System Context（追加到 system prompt 末尾）

通过 `appendSystemContext` 在 system prompt 数组末尾追加一段文本：

```
gitStatus: {git 仓库状态快照}
```

### `gitStatus` 内容

- 说明这是会话开始时的快照，可能已过时
- 当前分支名
- 主分支名（如果有）
- Git 用户名（可选）
- `git status --short` 输出（最长约 2000 字符）
- 最近 5 条 `git log --oneline`

**条件存在**：远程模式（`CLAUDE_CODE_REMOTE`）或禁用 git 说明时不注入。

---

## 五、子代理 Prompt 差异

子代理**不走** `getSystemPrompt()`，而是走 `getAgentSystemPrompt()` + `enhanceSystemPromptWithEnvDetails()`。

### 子代理 prompt 结构

```
子代理 system prompt
├── 角色文案（各内置 agent 自己的 getSystemPrompt）
├── Notes（enhanceSystemPromptWithEnvDetails 追加）
│   ├── "Agent threads always have their cwd reset between bash calls, use absolute paths"
│   ├── "Share relevant file paths (absolute) in final response"
│   ├── "Avoid using emojis"
│   └── "Do not use a colon before tool calls"
├── DiscoverSkills Guidance（条件追加）
└── Environment Info（computeEnvInfo，<env> 格式）
```

### 三种内置 Agent 的角色文案对比

| Agent | 身份定位 | 只读约束 | 特殊输出要求 | Thinking |
|-------|----------|----------|-------------|----------|
| **Explore** | "file search specialist" | **强约束只读**：整段 CRITICAL READ-ONLY，列举所有禁止操作 | 高效搜索、并行工具调用、直接报告结果 | 关闭 |
| **Plan** | "software architect and planning specialist" | **强约束只读**：同 Explore | 要求输出"Critical Files for Implementation"列表 | 关闭 |
| **general-purpose** | "an agent for Claude Code" | **允许写**：仅限制不要随意建 md/README | 完成任务后简洁报告 | 关闭 |

### 子代理 vs 主线程的关键差异

| 维度 | 主线程 | 子代理 |
|------|--------|--------|
| system prompt 长度 | 很长（7 段静态 + 10+ 段动态） | 短（1 段角色 + Notes + env） |
| CLAUDE.md | 默认注入 | Explore/Plan **省略**（节省 5-15 Gtok/周） |
| gitStatus | 默认注入 | Explore/Plan **省略**（节省 1-3 Gtok/周） |
| 会话级段落 | Session Guidance、Memory、MCP、Scratchpad 等全有 | **全部没有** |
| Agent 工具说明 | 有（何时用子代理等） | **没有**（Explore/Plan 还会主动禁用 Agent 工具） |
| 环境信息格式 | `computeSimpleEnvInfo`（`# Environment` bullet 格式） | `computeEnvInfo`（`<env>` XML 格式，更短） |
| Extended Thinking | 启用 | **默认关闭**（节省 token） |

### `DEFAULT_AGENT_PROMPT`（兜底 prompt）

当 `agentDefinition.getSystemPrompt()` 抛错时使用：

```
You are an agent for Claude Code, Anthropic's official CLI for Claude. 
Given the user's message, you should use the tools available to complete the task. 
Complete the task fully — don't gold-plate, but don't leave it half-done. 
When you complete the task, respond with a concise report covering what was done 
and any key findings — the caller will relay this to the user, so it only needs 
the essentials.
```

---

## 六、三条互斥路径

`getSystemPrompt` 实际有三条返回路径，不会同时生效：

| 路径 | 触发条件 | 特点 |
|------|----------|------|
| **Simple** | `CLAUDE_CODE_SIMPLE` 环境变量 | 极简：仅 "You are Claude Code" + CWD + Date |
| **Proactive** | `feature('PROACTIVE'/'KAIROS')` 且 `isProactiveActive()` | 自主代理模式：不走静态块，走独立的 memory + env + proactive 行为说明 |
| **主路径** | 上述均不满足 | 完整的静态 + 动态分层结构（本文档主要描述的） |

---

## 七、对 zd-agent 的启示

你当前的 prompt 是 `SOUL.md + AGENTS.md + TOOLS.md` 拼成一个字符串。对照 Claude Code，可以考虑的改进方向：

1. **分段注册**：把 prompt 拆成独立段落，每段有名字、有缓存属性、有条件存在判断
2. **动态环境块**：注入当前工单信息、版本信息、已加载的工具列表等运行时数据
3. **子代理精简**：如果未来加子代理，不要复制完整 prompt，而是只保留角色定义 + 环境信息
4. **workspace 覆盖**：支持不同客户线/工单类型加载不同的指令文件（类似 CLAUDE.md 的发现机制）
5. **缓存分层**：把"跨工单不变"的部分和"每个工单不同"的部分分开，有助于未来接入 prompt cache
