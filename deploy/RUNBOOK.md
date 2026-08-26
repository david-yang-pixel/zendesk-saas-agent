# Deployment Runbook — zd-agent (Dify SaaS Support)

当前服务器上拆成多个独立项目：

- `/opt/zd-agent/code`: 只运行 Zendesk 工单处理 agent。
- `/opt/db-gateway/code`: 独立维护 Teleport DB proxy、Teleport 证书续期、续期页面、只读 DB Gateway API 和 MCP server。
- `/opt/mcp/code`: Source、GitHub、official docs、notification 四个当前远程 MCP 服务源码；独立管理、独立部署，agent CI/CD 不重启它们。历史 sandbox worker 即使仍运行也不属于 v2 支持链路。

## One-time Setup (~2 hours)

### 1. 服务器资源预算

本项目支持两种内存档位。调查轮数、工具调用次数和整单累计模型 token
不设提前终止上限，不能用这些指标推算容器内存；单工单只受运行时限约束。

**🟢 推荐（4G）**：Baidu Cloud BCC Console → resize 到 **4G**（2C/4G）
- `config.yaml`: 可将 `agent.max_concurrent_sessions` 从 `2` 逐步提高到 `5`，扩大前先做并发压测
- `zd-agent.service`: `--memory=1500m --cpus=1.0`（去掉 `--memory-swap`）
- 可同时跑 5 个工单分析会话

**🟡 最低（2G，当前服务器档位）** — 默认配置已按此优化：
- `config.yaml`: `agent.max_concurrent_sessions: 2`
- `agent.session_timeout_seconds: 1440` 从工单取得执行槽后开始计时，排队时间不计入
- `zd-agent.service`: `--memory=800m --memory-swap=1500m --cpus=0.8`
- swap 作为 burst 兜底；2 个并发会话稳定运行
- **务必确认 swap 已启用**（`free -h` 看 Swap 行应 ≥ 1.5G）
  - 如没 swap：`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab`
- 高峰期建议监控：`docker stats zd-agent` 看 MEM % 是否长期 > 90%

### 1.5 关键 config.yaml 运行字段（非内存档位相关）

| 字段 | 当前值 | 说明 |
|---|---|---|
| `llm.model` | `gpt-5.5` | 主 Agent 模型 |
| `llm.enable_vision_input` | `true` | 下载并校验 Zendesk 图片后作为不可信视觉观察输入；OpenAI/Anthropic 均有 provider 适配 |
| `casework.mode` | `casework` | v2 是唯一决策核心；`canary` 仅允许显式 ticket allowlist，`legacy/shadow` 会被配置校验拒绝 |
| `agent.session_timeout_seconds` | `1440` | 单工单取得执行槽后的绝对运行时限 |
| `public_reply.enabled` | `true` | **仅客户写入 kill switch**。`false` 时继续调查和归档草稿，不调用 Browser Relay，不回落旧 Internal Note/handoff；已确认的 typed ownership 仍独立执行 |
| `public_reply.browser_relay_url` | `https://zendesk.smlershou.top/zendesk-reply-mcp` | SaaS native messaging 客户可见回复走独立 browser relay MCP，不使用 Conversations API |
| `public_reply.freshness_callback_url` | `https://zendesk.smlershou.top/webhook/relay-pre-send-freshness` | remote relay 在点击 Send 前调用的 HTTPS freshness gate；复用现有 `/webhook/` 反代，应用层仍强制 Bearer 校验 |
| `zendesk.min_ticket_id` | `2624` | **灰度边界**：只处理 ID ≥ 此值的工单。上线稳定后改 `0` 处理全部 SaaS 工单 |
| `zendesk.search_query` | `status:new tags:saas` | 处理范围 |

> 生产唯一的宿主机配置源是 `/opt/zd-agent/code/config.yaml`，由 systemd
> 只读挂载到 `/app/config.yaml`。不要再挂载或同步旧的
> `/opt/zd-agent/releases/main/config.yaml`。
> 生产 checkout 必须保持 tracked files clean；不要直接编辑其中的 `config.yaml`。
> 配置变更应走管理后台/`.env`，或提交到 `main` 后由 CI/CD 发布。
> 应急停止客户写入：在管理后台将 `public_reply.enabled` 设为 `false`。
> 如需停止全部后台处理，直接 `systemctl stop zd-agent`；不要把 public reply
> kill switch 误当成全局暂停。
> CI/CD 会在重启前使用目标镜像加载 `config.yaml`、`.env` 和管理后台覆盖，
> 并在重启后再次检查运行容器的最终有效配置。远程 Browser Relay 缺少 HTTPS
> 非 loopback callback 时部署必须失败。CI/CD 还会从服务器本机请求 freshness
> callback 的公网 HTTPS 地址；未带 Bearer 必须返回 401，否则部署判失败。

### 2. Clone repo on server

```bash
mkdir -p /opt/zd-agent
cd /opt/zd-agent
git clone <repo-url> code
mkdir resources
```

### 3. Rsync initial resources from your local machine

```bash
# From your local Mac
rsync -av --progress \
    "<local-ticket-records-dir>/" \
    root@<agent-server>:/opt/zd-agent/resources/zendesk-cli/工单记录/

rsync -av --progress \
    "<local-knowledge-base-dir>/" \
    root@<agent-server>:/opt/zd-agent/resources/saas版知识库/

# Initialize the directory skeleton (creates .state, etc.)
ssh root@<agent-server> "/opt/zd-agent/code/scripts/init-resources.sh /opt/zd-agent/resources"
```

### 4. Clone the 5 SaaS source repos

```bash
ssh root@<agent-server>
cd /opt/zd-agent/resources/saas源代码
for repo in dify dify-billing dify-marketplace-backend dify-marketplace-frontend dify-plugin-daemon saas-deploy; do
    git clone git@github.com:langgenius/$repo.git
done
```

### 5. Configure env files

```bash
# Agent env: Zendesk / LLM plus authenticated client URLs for external MCP services
cp /opt/zd-agent/code/.env.example /opt/zd-agent/.env
vim /opt/zd-agent/.env
chmod 600 /opt/zd-agent/.env

# DB Gateway env lives in the sibling db-gateway project.
# See /opt/db-gateway/code/.env.example.

# Browser relay env lives outside the agent repo. Copy only the relay API key
# into /opt/zd-agent/.env:
# ZENDESK_BROWSER_RELAY_URL=https://zendesk.smlershou.top/zendesk-reply-mcp
# ZENDESK_BROWSER_RELAY_API_KEY=<from /etc/zendesk-browser-relay/env>
# ZENDESK_FRESHNESS_CALLBACK_URL=https://zendesk.smlershou.top/webhook/relay-pre-send-freshness

# SigNoz env lives outside the agent repo. Copy only the MCP URL/key
# into /opt/zd-agent/.env:
# SIGNOZ_MCP_URL=http://127.0.0.1:8098
# SIGNOZ_MCP_API_KEY=<from /etc/signoz-mcp/env>

# Zendesk MCP env lives outside the agent repo. Copy only the MCP URL/key
# into /opt/zd-agent/.env:
# ZENDESK_MCP_URL=http://127.0.0.1:8099
# ZENDESK_MCP_API_KEY=<from /etc/zendesk-mcp/env>

# Tool Services env lives outside the agent process and is managed from
# /opt/mcp/code, not from this repo's CI/CD. Copy only the remote MCP URLs/API
# keys into /opt/zd-agent/.env:
# SOURCE_MCP_URL=https://zendesk.smlershou.top/source-mcp/mcp
# SOURCE_MCP_API_KEY=<from /etc/source-mcp/env>
# GITHUB_MCP_URL=https://zendesk.smlershou.top/github-mcp/mcp
# DOCS_TOOL_SERVICE_URL=https://zendesk.smlershou.top/docs-tool-service/mcp
# NOTIFICATION_SERVICE_URL=https://zendesk.smlershou.top/notification-service/mcp
```

Required variables for `/opt/zd-agent/.env` (see `.env.example`):
- ZENDESK_SUBDOMAIN / ZENDESK_EMAIL (`<zendesk-agent-email>`) / ZENDESK_API_TOKEN
- ZENDESK_WEBHOOK_SECRET / ZENDESK_MESSAGING_WEBHOOK_SECRET（两条入口必须使用不同 secret）
- ZENDESK_MCP_URL / ZENDESK_MCP_API_KEY
- OPENAI_API_KEY
- SIGNOZ_MCP_URL / SIGNOZ_MCP_API_KEY
- SOURCE_MCP_URL / SOURCE_MCP_API_KEY
- GITHUB_MCP_URL / GITHUB_MCP_API_KEY
- DOCS_TOOL_SERVICE_URL / DOCS_TOOL_SERVICE_API_KEY
- NOTIFICATION_SERVICE_URL / NOTIFICATION_SERVICE_API_KEY
- DB_GATEWAY_URL / DB_GATEWAY_API_KEY

`GITHUB_TOKEN` is optional in the agent env for operator-run local repository
clone/fetch commands only. The v2 model never receives it. GitHub PR/code search uses the remote `github-mcp` service;
the PAT, rate limit handling, and service env are owned by the independent MCP
deployment.

Required variables for `/opt/db-gateway/.env` are documented in `/opt/db-gateway/code/.env.example`:
- DB_GATEWAY_API_KEY（must match `/opt/zd-agent/.env`）
- TELEPORT_USER
- TELEPORT_PUBLIC_BASE_URL
- INTERNAL_SHARED_SECRET

Required variables for `/opt/signoz-mcp/.env` are documented in `/opt/signoz-mcp/code/.env.example`:
- SIGNOZ_MCP_API_KEY（must match `/opt/zd-agent/.env`）
- SIGNOZ_URL
- SIGNOZ_API_KEY
- FEISHU_WEBHOOK_URL / FEISHU_WEBHOOK_SECRET（watchdog alerting）

Required variables for `/opt/zendesk-mcp/.env` are documented in `/opt/zendesk-mcp/code/.env.example`:
- ZENDESK_MCP_API_KEY（must match `/opt/zd-agent/.env`）
- ZENDESK_SUBDOMAIN
- ZENDESK_EMAIL
- ZENDESK_API_TOKEN

Required variables for the independent Tool Services deployment:
- `/etc/source-mcp/env`: `SOURCE_MCP_API_KEY`
- `/etc/github-mcp/env`: `GITHUB_MCP_API_KEY`, `GITHUB_MCP_GITHUB_TOKEN`
- `/etc/docs-tool-service/env`: `DOCS_TOOL_SERVICE_API_KEY`
- `/etc/notification-service/env`: `NOTIFICATION_SERVICE_API_KEY`, `FEISHU_WEBHOOK_URL`, optional `FEISHU_WEBHOOK_SECRET`

The v2 agent does not call sandbox-worker. An independently deployed historical
sandbox-worker may remain running, but it is not part of the current support path
and needs no `/opt/zd-agent/.env` entry.

Do not manage those files from `zd-agent` CI/CD. Update/restart Tool Services
from `/opt/mcp/code`, then update `/opt/zd-agent/.env` only if a remote URL or
API key changes.

**可选**：
- `/opt/zendesk-mcp/.env`: `ZENDESK_MCP_DOWNLOAD_MAX_BYTES` — caps `download_attachment` responses returned to the agent as base64; default is 10 MiB.
- `/opt/db-gateway/.env`: `PG_PASSWORD` — 默认走 Teleport 证书认证不需要；仅在直连 DB 或双重认证时填
- `/opt/db-gateway/.env`: `TELEPORT_PASSWORD` / `TELEPORT_TOTP_SECRET` — 填了才自动续期；不填则退回飞书手动续期卡片

### 6. Configure Zendesk webhook + Trigger

本项目使用 `.env` 中的 Zendesk agent 账号调用 API。可以复用运维账号，也可以单独准备 bot/agent 账号；所有 Internal Note 落款都来自 `ZENDESK_EMAIL`。

1. 用**管理员账号**登录 Zendesk，生成 API token：Admin Center → Apps → APIs → Zendesk API tokens → Add
   - Token 与谁生成无关，最终身份取决于 `.env` 里 `ZENDESK_EMAIL`
2. 确认 `<zendesk-agent-email>` 账号的 **Role = Agent**（End user 不能写 Internal Note）
3. Create webhook:
   - Admin Center → Apps and integrations → Webhooks → Create webhook
   - Endpoint: `https://<agent-public-domain>/webhook/zendesk`
   - Method: POST, Authentication: Signed Secret (copy secret → ZENDESK_WEBHOOK_SECRET)
4. Create trigger:
   - Conditions: Ticket Status = New AND Tags contain "saas" AND Ticket is (Created OR Updated)
   - Actions: Notify webhook
   - Body (JSON):
     ```json
     {
       "event": "ticket_updated",
       "ticket_id": "{{ticket.id}}",
       "latest_comment_id": "{{ticket.latest_comment.id}}",
       "latest_comment_author_id": "{{ticket.latest_comment.author.id}}",
       "latest_comment_is_public": "{{ticket.latest_comment.is_public}}",
       "tags": "{{ticket.tags}}",
       "updated_at": "{{ticket.updated_at}}"
     }
     ```
5. 为内部备注 `@mention` 创建独立的低延迟 Trigger（代码部署完成后再启用）：
   - Conditions（ALL）：Tags contain `saas`；Comment is Private；Ticket is Updated
   - 不要限制 `Status = New`，也不要在 Zendesk 中按 mention 文本过滤
   - Action：Notify the same signed Tickets webhook
   - Body (JSON)：
     ```json
     {
       "event": "agent_mention_candidate",
       "ticket_id": "{{ticket.id}}",
       "latest_comment_id": "{{ticket.latest_comment.id}}",
       "updated_at": "{{ticket.updated_at}}"
     }
     ```
   - 该事件只是候选信号；Agent 会重新读取精确 comment，并复用 private-note、alias
     和 self-loop 检查后才派发。候选与 5 秒轮询兜底共享 `mention:<comment_id>`
     幂等键。
   - Rollback：先停用这条专用 Trigger；轮询兜底继续运行。
6. 为 Zendesk Messaging 的 `messaging_ticket.message_added` 事件创建独立 webhook：
   - Endpoint: `https://<agent-public-domain>/webhook/zendesk-messaging`
   - Secret: `ZENDESK_MESSAGING_WEBHOOK_SECRET`，不得复用 Tickets webhook secret
   - 这条入口负责客户实时追问；waiting poller 只作为 300 秒低频丢事件兜底

### 7. Verify the notification service

v2 不接收飞书 bot webhook，也不在 Agent 内保存飞书 webhook secret。工单完成和
typed ownership 通知统一调用独立 `notification-service` MCP；它负责飞书凭据、
签名、幂等键和传输重试。

```bash
source /opt/zd-agent/.env
test -n "$NOTIFICATION_SERVICE_URL"
test -n "$NOTIFICATION_SERVICE_API_KEY"
```

### 8. DNS

Add A record at your DNS provider:
- `<agent-public-domain>` → `<agent-server-ip>`
- Wait for propagation (typically < 5 min)

### 9. Nginx + Let's Encrypt

```bash
sudo cp /opt/zd-agent/code/deploy/nginx/zd-agent.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/zd-agent.conf /etc/nginx/sites-enabled/

# Get cert
sudo certbot certonly --webroot -w /var/www/html -d <agent-public-domain>

# Reload
sudo nginx -t && sudo systemctl reload nginx
```

This `zd-agent` nginx template exposes only agent endpoints (`/webhook/`,
`/healthz`, `/admin`, `/admin/`) and intentionally does not define routes for
remote MCP services.
Routes such as `/source-mcp/`, `/github-mcp/`, `/docs-tool-service/`, `/notification-service/`,
`/zendesk-mcp/`, `/signoz-mcp/`, and
`/zendesk-reply-mcp` are owned by their independent service configs.

### 10. Install and start DB Gateway sibling project

Follow `/opt/db-gateway/code/README.md` for:

- installing `tsh`
- first-time Teleport login
- building `db-gateway:latest`
- installing `db-gateway.service`, `tsh-proxy-db*.service`, `tsh-auth-watchdog.service`, and `proxy-health.timer`
- adding nginx locations `/db-gateway/` and `/teleport-auth`

Quick verification:

```bash
sudo systemctl status db-gateway tsh-proxy-db tsh-proxy-db-billing tsh-proxy-db-plugin tsh-auth-watchdog proxy-health.timer
source /opt/db-gateway/.env
curl -fsS -H "X-API-Key: $DB_GATEWAY_API_KEY" https://<agent-public-domain>/db-gateway/status
```

### 11. GitHub Actions CI/CD

Push to `main` runs `.github/workflows/docker-image.yml`:

1. build `deploy/Dockerfile`
2. push `ghcr.io/david-yang-pixel/zendesk-saas-agent:main`
3. push `ghcr.io/david-yang-pixel/zendesk-saas-agent:sha-<commit-sha>`
4. SSH to the server, fast-forward `/opt/zd-agent/code` to the exact workflow commit
5. deploy the immutable build digest with its matching `sha-<commit-sha>` revision
6. send a success email only after `deploy-ghcr.sh` finishes and `healthz` passes,
   when SMTP notification secrets are configured

Required GitHub repository secrets or `production` environment secrets for
deployment:

| Secret | Purpose |
|---|---|
| `ZD_AGENT_DEPLOY_HOST` | Server host/IP, for example `180.76.243.249` |
| `ZD_AGENT_DEPLOY_USER` | SSH user that can run Docker and `systemctl restart zd-agent`; current server uses root |
| `ZD_AGENT_DEPLOY_PORT` | SSH port; current server uses `22022` (the workflow defaults to `22` when empty) |
| `ZD_AGENT_DEPLOY_SSH_KEY` | Private key for the deploy user |
| `ZD_AGENT_DEPLOY_KNOWN_HOSTS` | Pinned SSH host key line for the server |

Generate `ZD_AGENT_DEPLOY_KNOWN_HOSTS` from a trusted network and verify the
fingerprint before saving it as a secret:

```bash
ssh-keyscan -p 22022 180.76.243.249
```

Optional GitHub repository secrets or `production` environment secrets for
success email notification. If any of these are missing, the workflow logs a
notice and skips the email step without failing the deployment:

| Secret | Purpose |
|---|---|
| `DEPLOY_NOTIFY_SMTP_HOST` | SMTP host, for example `smtp.gmail.com` |
| `DEPLOY_NOTIFY_SMTP_PORT` | SMTP port; `465` uses SMTP SSL, other ports use STARTTLS |
| `DEPLOY_NOTIFY_SMTP_USERNAME` | SMTP login username |
| `DEPLOY_NOTIFY_SMTP_PASSWORD` | SMTP password or app password |
| `DEPLOY_NOTIFY_EMAIL_FROM` | Sender address |
| `DEPLOY_NOTIFY_EMAIL_TO` | Recipient address |

### 12. Pull Docker image from GHCR

```bash
# First-time only for private packages. Use a PAT with read:packages only.
read -rsp "GHCR PAT: " GHCR_PAT; echo
COMMIT=<40-hex-commit-sha>
IMAGE=ghcr.io/david-yang-pixel/zendesk-saas-agent
DIGEST="$(docker buildx imagetools inspect "$IMAGE:sha-$COMMIT" --format '{{.Manifest.Digest}}')"
(
set -euo pipefail
cd /opt/zd-agent/code
exec 9>/run/lock/zd-agent-deploy.lock
flock -w 120 9
export ZD_AGENT_DEPLOY_LOCK_HELD=1
git diff --quiet -- && git diff --cached --quiet --
git fetch origin main
git switch --detach "$COMMIT"
ZD_AGENT_IMAGE_DIGEST="$DIGEST" GHCR_USERNAME=<github-user> GHCR_PAT="$GHCR_PAT" \
  ./deploy/deploy-ghcr.sh "sha-$COMMIT"
)
unset GHCR_PAT
```

CI/CD deploys the immutable `sha-<commit-sha>` tag built by that workflow run
by its immutable digest, and passes the workflow `GITHUB_TOKEN` to
`deploy-ghcr.sh` for a temporary GHCR login. Moving tags such as `main` are
intentionally rejected by the production deploy script.

To deploy or roll back to an exact commit image:

```bash
ZD_AGENT_IMAGE_DIGEST=sha256:<manifest-digest> \
  /opt/zd-agent/code/deploy/deploy-ghcr.sh sha-<40-hex-commit-sha>
```

The checkout must already be clean and at that exact commit. If a manual
operation also changes the checkout, acquire `/run/lock/zd-agent-deploy.lock`
before `git fetch/switch` and export `ZD_AGENT_DEPLOY_LOCK_HELD=1`, as shown
above. Keep the checkout at the deployed commit; the next successful CI/CD run
switches it back to `main` under the same lock.

`deploy-ghcr.sh` pulls the immutable image digest, verifies its OCI revision
label and the clean server checkout match the requested commit, retags it as
local `zd-agent:latest`, restarts `zd-agent`, and waits for
`http://127.0.0.1:8080/healthz`. After both the health check and the protected
freshness-route check succeed, it removes older images from that Agent image
repository while preserving the running image, `zd-agent:latest`, and the three
most recently created Agent image versions. Set `ZD_AGENT_IMAGE_KEEP_RECENT` to
a positive integer to change that retention count. Failed deployments never run
the image cleanup, and images from other repositories are not touched. If an
old image is still pinned and cannot be removed, the script emits a warning but
does not turn an otherwise healthy deployment into a failed deployment.

Fallback source build if GHCR is unavailable:

```bash
cd /opt/zd-agent/code
sudo docker build --network host -f deploy/Dockerfile -t zd-agent:latest .
sudo systemctl restart zd-agent
curl -fsS http://127.0.0.1:8080/healthz
```

> ⚠️ Fallback builds must keep `--network host`: this server's Docker uses the
> host proxy, and the default bridge network may fail during `apt-get update`.

### 13. Install & start systemd services

```bash
sudo useradd --system --home /opt/zd-agent --shell /bin/false zd-agent
sudo chown -R zd-agent:zd-agent /opt/zd-agent

sudo cp /opt/zd-agent/code/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now zd-agent.service

# Verify
sudo systemctl status zd-agent db-gateway
curl -fsS https://<agent-public-domain>/healthz
sudo journalctl -u zd-agent -f
```

---

## Daily Operations

### Control plane and Agent Worker

The `zd-agent` systemd unit now keeps the HTTP control plane online. The
control plane owns a separate, HTTP-free Agent Worker process. Use the admin
console **部署状态** page to start, stop, or restart only that Worker.

- **关闭 Agent** exits the Worker process completely; the control plane,
  `/admin`, `/healthz`, and signed Zendesk webhook ingress remain online.
- While the Worker is stopped, valid Zendesk events are acknowledged and
  audited as discarded. They are not queued.
- **开启 Agent** creates a new online watermark. Queue rows and ticket updates
  from before that watermark are not backfilled.
- Stop and restart first fence new sessions, stop all pollers/consumers, and
  wait for the Worker to acknowledge that fence. Events received during this
  drain remain in the durable queue. Stop discards them only after every active
  session and customer-visible side effect is safe and the Worker has exited;
  restart preserves them for the replacement Worker.
- A failed or interrupted drain never commits `desired_state=stopped`, so its
  webhooks remain queued rather than being acknowledged as offline drops.
- `systemctl stop zd-agent` stops both the control plane and the Worker and is
  reserved for host maintenance; it is not the normal Agent off switch.

The persisted desired state survives container and host restarts. A deployment
must not change `desired_state=stopped` back to running.

### Cross-host cutover (single active Agent)

Do not start the new host while the old Agent can restart. The host identity
marker is part of `.state` and the production systemd unit refuses to start if
the marker does not equal the host machine ID.

```bash
# 1. On the NEW host, record its identity.
NEW_INSTANCE_ID="$(tr -d '[:space:]' </etc/machine-id)"

# 2. On the OLD host, stop, mask, verify no container, and transfer ownership.
sudo /opt/zd-agent/code/deploy/demote-instance.sh "$NEW_INSTANCE_ID"

# 3. Only after step 2 succeeds, perform the final stopped-state sync.
# Run on the NEW host; use a temporary directory and then atomically replace .state.
sudo rsync -a --delete root@<old-host>:/opt/zd-agent/resources/.state/ \
  /opt/zd-agent/resources/.state.incoming/
test "$(cat /opt/zd-agent/resources/.state.incoming/active_instance_id)" = \
  "$NEW_INSTANCE_ID"
sudo mv /opt/zd-agent/resources/.state \
  /opt/zd-agent/resources/.state.pre-cutover
sudo mv /opt/zd-agent/resources/.state.incoming \
  /opt/zd-agent/resources/.state

# 4. Verify the fence, deploy, then check health before changing DNS.
sudo ZD_AGENT_INSTANCE_ID="$NEW_INSTANCE_ID" \
  /opt/zd-agent/code/deploy/verify-instance-fence.sh
sudo systemctl start zd-agent
curl -fsS http://127.0.0.1:8080/healthz
```

The old service remains permanently masked and its local marker now names the
new host. Reversing the cutover requires the same stopped-state procedure in
the opposite direction; DNS changes alone never transfer background-worker
ownership.

| What | How |
|------|-----|
| Restart only the Agent Worker | 管理后台 → **系统与审计** → **部署状态** → **重启 Agent** |
| Restart DB Gateway | `sudo systemctl restart db-gateway`（DB Gateway 项目负责） |
| Restart Tool Services | 通过 `/opt/mcp/code` 的独立 MCP 运维流程处理；agent 部署不会重启它们 |
| View live logs | `sudo docker logs -f zd-agent` (or `journalctl -u zd-agent -f`) |
| Manual Teleport renew | Follow `/opt/db-gateway/code/README.md`; Agent does not own DB credentials or Teleport login |
| Stop Agent processing, keep control plane online | 管理后台 → **部署状态** → **关闭 Agent** |
| Resume Agent processing without offline backlog | 管理后台 → **部署状态** → **开启 Agent** |
| Stop the entire control plane for host maintenance | `sudo systemctl stop zd-agent` |
| Resolve an ambiguous historical reply | After verifying the exact Zendesk comment, run `zd-agent reconcile-delivery <ticket> --attempt-id <id> --outcome delivered\|unsent --reason '<evidence>' --actor '<operator>'`; never guess an outcome |
| Check status | `curl -fsS http://127.0.0.1:8080/healthz` |
| Refresh local source fallback | `uv run zd-agent repos fetch <repo>`; production root-cause evidence should prefer Source MCP `ref=runtime` |
| Update agent code | Push the desired commit to `main`; CI/CD deploys its immutable digest |
| List downgrade-safe rollback bundles | `sudo find /opt/zd-agent/resources/.release-rollbacks -mindepth 1 -maxdepth 1 -type d -printf '%f\n'` |
| Roll back after a successful deploy/canary | `cd /opt/zd-agent/code && sudo ./deploy/rollback-release.sh <40-hex-previous-revision>`; this restores the retained image, matching config, systemd unit, and state snapshot under the deployment lock |
| Restart only (管理后台要求重启或 `.env` 变更后) | `sudo systemctl restart zd-agent` |
| Refresh DB schema cheat sheet | `bash /opt/db-gateway/code/scripts/dump_db_schema.sh` (写到 saas版知识库/database_schema.md，agent 重启后即生效) |

## Daily Backups

The backup cron should copy `.state/` + `工单记录/` daily. Add to root's crontab:

```
0 2 * * * tar czf /backup/zd-agent-$(date +\%Y\%m\%d).tar.gz /opt/zd-agent/resources/.state /opt/zd-agent/resources/zendesk-cli/工单记录 && find /backup -name "zd-agent-*.tar.gz" -mtime +7 -delete
```

## Health Check

```bash
curl -I https://<agent-domain>/healthz
# Expected: HTTP/2 200

curl -I https://<agent-domain>/admin
# Expected: HTTP/2 200

curl -X POST https://<agent-domain>/webhook/zendesk
# Expected: 401 (no signature) — proves the endpoint is reachable and the verify path is active

sudo docker ps | grep zd-agent
# Expected: container running
```

## Troubleshooting

### Agent keeps restarting

```bash
sudo journalctl -u zd-agent -n 100 --no-pager
sudo docker logs zd-agent 2>&1 | tail -100
```

Common causes:
- Missing agent env var → check `/opt/zd-agent/.env`
- Missing DB/Teleport env var → check `/opt/db-gateway/.env`
- Port conflict → `sudo ss -tlnp | grep 8080`
- Volume mount error → `ls -la /opt/zd-agent/resources`

### Teleport cert expired & Feishu not pushing

```bash
sudo -u zd-agent TELEPORT_HOME=/opt/zd-agent/.tsh tsh status
sudo journalctl -u tsh-auth-watchdog -n 50
```

Manually trigger renew:
```bash
source /opt/db-gateway/.env
sudo -u zd-agent TELEPORT_HOME=/opt/zd-agent/.tsh tsh login --headless --proxy=<teleport-proxy> --user=$TELEPORT_USER
```

### Webhook 401 for legit requests

Make sure ZENDESK_WEBHOOK_SECRET in .env matches the secret shown in Zendesk Admin Center → Webhooks → your webhook → "Signed Secret".

### SQL queries failing

First check DB Gateway via the same domain MCP uses:
```bash
source /opt/db-gateway/.env
curl -fsS -H "X-API-Key: $DB_GATEWAY_API_KEY" https://<agent-public-domain>/db-gateway/status
```

Then check Teleport tunnel:
```bash
sudo ss -tlnp | grep 56061
# Should show tsh listening on 127.0.0.1:56061
```

If nothing on 56061: `sudo systemctl restart tsh-proxy-db`.
If DB Gateway is down: `sudo systemctl restart db-gateway`.

### Out of memory

Check container mem:
```bash
sudo docker stats --no-stream zd-agent
```

If near the 1500m limit: raise the `--memory` in `zd-agent.service` (after upgrading instance memory).

---

## Logs

The web log viewer has been removed. Do not delete the log directories; the agent
still writes operational evidence there.

| What | Path / command |
|------|----------------|
| Structured JSONL events | `/opt/zd-agent/logs/events-<UTC-date>.jsonl` |
| Trigger queue log | `/opt/zd-agent/resources/logs/trigger_log.jsonl` |
| Per-ticket archive | `/opt/zd-agent/resources/zendesk-cli/工单记录/<ticket-id>.md` |
| Agent live logs | `journalctl -u zd-agent -f` or `docker logs -f zd-agent` |
| DB Gateway live logs | `journalctl -u db-gateway -f` or `docker logs -f db-gateway` |

These paths are runtime data. Keep them out of git cleanup and deployment sync.
