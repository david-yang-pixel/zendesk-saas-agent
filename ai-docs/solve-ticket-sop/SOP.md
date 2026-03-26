# Dify 客户工单处理 SOP

> 本文档描述处理 Dify 企业版客户工单的标准操作流程（Standard Operating Procedure）。

---

## 一、工作区初始化

**首次使用**，执行以下命令一键克隆所有源码仓库：

```bash
cd solve-ticket-sop
bash init-workspace.sh
```

脚本会在工作区根目录下完整建立如下结构：

```
dify-enterprise-all/
├── dify-community-and-enterpise-base-source/
│   ├── dify                    # 社区版核心
│   └── dify-sandbox            # 代码执行沙箱
├── dify-enterprise-source/
│   ├── dify-enterprise         # Go 企业版后端
│   ├── dify-enterprise-frontend
│   ├── dify-helm               # Helm Chart 部署
│   ├── dify-monitor            # 监控系统
│   ├── aws-cdk-for-dify        # AWS CDK 部署脚本
│   └── dify-ee-terraform       # Terraform 部署脚本
├── dify-plugin-base-source/
│   ├── dify-plugin-daemon      # 插件守护进程
│   ├── dify-plugin-sdks        # 插件 SDK
│   └── dify-official-plugins   # 官方插件源码
└── zendesk-cli/                # Zendesk 工单 CLI 工具
```

脚本同时会自动初始化 `zendesk-cli/.env` 并执行 `uv sync` 安装依赖。
初始化完成后，需要编辑 `zendesk-cli/.env` 填入 Zendesk 凭据，然后运行 `uv run zd me` 验证连接。

---

## 二、工单处理标准流程

### Step 1 — 问题分析（拉取工单）

**首先用 `zendesk-cli` 获取工单信息：**

```bash
cd zendesk-cli

# 查看最近工单列表
uv run zd tickets -s open

# 查看工单详情（含对话记录）
uv run zd ticket <id> -c

# 下载工单附件（截图、日志等）
uv run zd attachments <id>

# 导出为 Markdown 方便 AI 处理
uv run zd export <id> -o ../ai-docs/ticket-<id>.md

# 关键词搜索历史工单
uv run zd search "workflow error"
```

获取工单内容后：

| 操作 | 位置 | 说明 |
|------|------|------|
| 检索已有解决方案 | `ai-docs/` | 先查知识库，避免重复劳动 |
| 检查数据层异常 | `tables/` | 插件声明、安装记录等 Excel 数据 |

**命令示例：**
```bash
# 在 ai-docs 中模糊搜索关键词
grep -r "关键词" ai-docs/
```

---

### Step 2 — 环境调查

使用 `scripts/` 中的工具脚本复现或排查客户环境：

| 脚本 | 用途 |
|------|------|
| `check-plugin-runtime.sh` | 检查插件运行时状态 |

**命令示例：**
```bash
bash scripts/check-plugin-runtime.sh
```

---

### Step 3 — 源码定位

根据问题类型定位到对应代码库：

| 问题类型 | 查找位置 |
|----------|----------|
| 社区版通用逻辑、API、工作流 | `dify-community-and-enterpise-base-source/dify` |
| 代码执行 / Sandbox 问题 | `dify-community-and-enterpise-base-source/dify-sandbox` |
| SSO、License、企业管理后台 | `dify-enterprise-source/dify-enterprise` |
| 企业版前端控制台 | `dify-enterprise-source/dify-enterprise-frontend` |
| Helm / Kubernetes 部署 | `dify-enterprise-source/dify-helm` |
| 插件加载 / 守护进程 | `dify-plugin-base-source/dify-plugin-daemon` |
| 插件开发 SDK | `dify-plugin-base-source/dify-plugin-sdks` |
| 官方插件逻辑 | `dify-plugin-base-source/dify-official-plugins` |

---

### Step 4 — 修复与验证

1. 在对应源码目录下完成代码修改。
2. 若涉及部署问题，使用 Helm Chart 在测试集群验证：
   ```bash
   # 在 dify-enterprise-source/dify-helm 下操作
   helm upgrade --install dify . -f values.yaml -n dify --dry-run
   ```
3. ⚠️ **禁止直接修改生产配置**，所有变更先在测试环境验证。

---

### Step 5 — 邮件回复

参考 `.cursor/skills/dify-support-email-reply/SKILL.md` 生成专业回复邮件。

如需通过 Codex 双引擎交叉验证回复质量：

```bash
cd zendesk-cli
# 自动拉工单 + 下载附件 + 让 Codex 生成回复
./dual-review.sh <id>

# 自定义 prompt
./dual-review.sh <id> "帮我回复这个企业版工单，重点看用户截图"

# 查看 Codex 输出
cat codex-reviews/ticket-<id>-codex.md
```

- **中文客户**：使用中文模板，语气专业且有同理心。
- **英文客户**：使用英文模板。
- 邮件必须包含：问题确认 → 解决方案/进展 → 后续步骤 → 落款。

**邮件检查清单：**
- [ ] 是否已参考 `ai-docs/` 中的最新修复文档？
- [ ] 邮件语气是否专业且富有同理心？
- [ ] 技术步骤是否清晰、无歧义？
- [ ] 是否提供了必要的后续联系方式？

---

### Step 6 — 文档归档

修复完成后，在 `ai-docs/` 中记录本次处理经验：

```bash
# 文件命名规范：<日期>-<问题简述>.md
touch ai-docs/<YYYY-MM-DD>-<issue-summary>.md
```

---

## 三、快速参考卡

```
收到工单
  │
  ├─► 查 ai-docs/ 有无已知方案 ──────► 有 → 直接回复邮件
  │
  ▼ 无
查 tables/ 数据层是否异常
  │
  ▼
跑 scripts/ 排查脚本复现问题
  │
  ▼
定位源码（社区版 / 企业版 / 插件系统）
  │
  ▼
修复代码 → 测试集群验证
  │
  ▼
发邮件回复客户 → 归档到 ai-docs/
```

---

## 四、注意事项

- **禁止直接修改生产配置**：执行删除或迁移脚本前，务必先在测试环境验证。
- **保持目录整洁**：新文档 → `ai-docs/`，新脚本 → `scripts/`。
- **SSH Key**：部分仓库使用 SSH 协议，请确保本机 SSH Key 已配置在 GitHub。

---

## 五、zendesk-cli 配置说明

### 初次配置

```bash
cd zendesk-cli
cp .env.example .env   # 如果 init-workspace.sh 还没自动创建
```

编辑 `.env`，填入以下三个变量：

| 变量 | 说明 | 获取方式 |
|------|------|----------|
| `ZENDESK_SUBDOMAIN` | 子域名 | URL 中 `xxx.zendesk.com` 的 `xxx` |
| `ZENDESK_EMAIL` | 账号邮箱 | 你的 Zendesk 登录邮箱 |
| `ZENDESK_API_TOKEN` | API Token | Admin → Apps → Zendesk API → Token |

验证连接：

```bash
uv run zd me
```

### 常用命令速查

```bash
# 查看工单列表
uv run zd tickets -s open

# 查看单个工单（含对话）
uv run zd ticket <id> -c

# 下载附件（截图、日志）
uv run zd attachments <id>

# 导出 Markdown
uv run zd export <id> -o report.md

# 关键词搜索
uv run zd search "关键词"

# Codex 双引擎交叉验证
./dual-review.sh <id>
```

### 批量归档与每日更新

```bash
# 归档所有已解决工单为本地知识库
python scripts/archive_enterprise_tickets.py

# 手动触发每日更新（周一到周五 10:30 自动执行）
bash scripts/daily_update.sh

# 查看执行日志
tail -f logs/daily-update-$(date +%Y%m%d).log
```
