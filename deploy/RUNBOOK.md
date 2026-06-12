# Deployment Runbook — zd-agent (Dify SaaS Support)

当前服务器上拆成两个同级项目：

- `/opt/zd-agent/code`: 只运行 Zendesk 工单处理 agent。
- `/opt/db-gateway/code`: 独立维护 Teleport DB proxy、Teleport 证书续期、续期页面、只读 DB Gateway API 和 MCP server。

## One-time Setup (~2 hours)

### 1. 服务器资源预算

本项目支持两种内存档位：

**🟢 推荐（4G）**：Baidu Cloud BCC Console → resize 到 **4G**（2C/4G）
- `config.yaml`: `max_concurrent_sessions: 5`, `max_tokens_per_session: 1050000`
- `zd-agent.service`: `--memory=1500m --cpus=1.0`（去掉 `--memory-swap`）
- 可同时跑 5 个工单分析会话

**🟡 最低（2G，当前服务器档位）** — 默认配置已按此优化：
- `config.yaml`: `max_concurrent_sessions: 2`, `max_tokens_per_session: 1500000`, `compaction_threshold: 0.3`
  - ⚠️ `max_tokens` 从 500K 提到 1.5M（gpt-5.x 支持 1M context；旧 500K 导致
    62.5% 工单触发 Token 截断 → agent 频繁 "I couldn't verify"）。配套把
    `compaction_threshold` 从 0.65 降到 0.3，否则压缩永远不触发。
- `zd-agent.service`: `--memory=800m --memory-swap=1500m --cpus=0.8`
- swap 作为 burst 兜底；2 个并发会话稳定运行
- **务必确认 swap 已启用**（`free -h` 看 Swap 行应 ≥ 1.5G）
  - 如没 swap：`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab`
- 高峰期建议监控：`docker stats zd-agent` 看 MEM % 是否长期 > 90%

### 1.5 关键 config.yaml 运行字段（非内存档位相关）

| 字段 | 当前值 | 说明 |
|---|---|---|
| `llm.model` | `gpt-5.5` | 主 Agent 模型 |
| `llm.subagent_model` | `gpt-5.4-mini` | Subagent 用便宜模型省 ~70% 成本。**必须是 proxy 真实存在的模型名**（`gpt-5.5-mini` 不存在会全 404，踩过坑）|
| `trigger_decider.llm_model` | `gpt-5.5` | 触发判定模型（误判代价高，不省这个钱）|
| `public_reply.enabled` | `true` | **总开关 / 应急回退**：出问题立即 flip 为 `false`，agent 全部退回只发 Internal Note（SE 手动 forward），无需改代码 |
| `public_reply.max_ask_count` | `10` | 累计追问上限，超过转人工 |
| `public_reply.browser_relay_url` | `http://127.0.0.1:8097` | SaaS native messaging 客户可见回复走独立 browser relay，不使用 Conversations API |
| `zendesk.min_ticket_id` | `2624` | **灰度边界**：只处理 ID ≥ 此值的工单。上线稳定后改 `0` 处理全部 SaaS 工单 |
| `zendesk.search_query` | `status:new tags:saas` | 处理范围 |

> 改 `config.yaml` 后直接 `systemctl restart zd-agent` 即生效（配置挂载为只读 volume，无需 rebuild 镜像）。
> 应急回退 public reply：改 `enabled: false` → restart，最快止血。

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
for repo in dify dify-billing dify-marketplace-backend dify-marketplace-frontend saas-deploy; do
    git clone git@github.com:langgenius/$repo.git
done
```

### 5. Configure env files

```bash
# Agent env: Zendesk / LLM / app integrations + DB Gateway MCP client only
cp /opt/zd-agent/code/.env.example /opt/zd-agent/.env
vim /opt/zd-agent/.env
chmod 600 /opt/zd-agent/.env

# DB Gateway env lives in the sibling db-gateway project.
# See /opt/db-gateway/code/.env.example.

# Browser relay env lives outside the agent repo. Copy only the relay API key
# into /opt/zd-agent/.env:
# ZENDESK_BROWSER_RELAY_URL=http://127.0.0.1:8097
# ZENDESK_BROWSER_RELAY_API_KEY=<from /etc/zendesk-browser-relay/env>

# SigNoz env lives outside the agent repo. Copy only the MCP URL/key
# into /opt/zd-agent/.env:
# SIGNOZ_MCP_URL=http://127.0.0.1:8098
# SIGNOZ_MCP_API_KEY=<from /etc/signoz-mcp/env>
```

Required variables for `/opt/zd-agent/.env` (see `.env.example`):
- ZENDESK_SUBDOMAIN / ZENDESK_EMAIL (`<zendesk-agent-email>`) / ZENDESK_API_TOKEN / ZENDESK_WEBHOOK_SECRET
- OPENAI_API_KEY
- FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_VERIFY_TOKEN / FEISHU_ENCRYPT_KEY / FEISHU_BOT_CHAT_ID
- SIGNOZ_MCP_URL / SIGNOZ_MCP_API_KEY
- GITHUB_TOKEN
- DB_GATEWAY_URL / DB_GATEWAY_API_KEY

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

**可选**：
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

### 7. Configure Feishu bot

1. Feishu Open Platform → Create custom app → enable "Bot" capability
2. Set event subscription URL: `https://<agent-public-domain>/webhook/feishu`
3. Subscribe events: `im.message.receive_v1`
4. Add bot to your team chat
5. Copy App ID / Secret / Verify Token / Encrypt Key → .env

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

### 11. Build Docker image

```bash
cd /opt/zd-agent/code
sudo docker build --network host -f deploy/Dockerfile -t zd-agent:latest .
```

> ⚠️ **必须带 `--network host`**：本服务器 Docker 配了 clash 代理（`127.0.0.1:7890`，
> 见 `~/.docker/config.json` + `/etc/systemd/system/docker.service.d/`）。默认 bridge
> 构建网络访问不到宿主机 `127.0.0.1`，`apt-get update` 会 `Connection refused`
> 导致 `Unable to locate package git/curl`。`--network host` 让构建容器共享宿主网络
> 才能走通代理。

### 12. Install & start systemd services

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

| What | How |
|------|-----|
| Restart agent | `sudo systemctl restart zd-agent` |
| Restart DB Gateway | `sudo systemctl restart db-gateway`（DB Gateway 项目负责） |
| View live logs | `sudo docker logs -f zd-agent` (or `journalctl -u zd-agent -f`) |
| Manual Teleport renew | Feishu `/relogin` or `sudo systemctl restart tsh-auth-watchdog` |
| Pause processing | Feishu `/pause` (doesn't restart process, just suspends polling) |
| Resume | Feishu `/resume` |
| Force reprocess ticket | Feishu `/reprocess 12345` |
| Check status | Feishu `/status` |
| Update SaaS branch | Feishu `/branch dify hotfix/1.11.2-fix3` |
| Update agent code | `cd /opt/zd-agent/code && git pull && sudo docker build --network host -f deploy/Dockerfile -t zd-agent:latest . && sudo systemctl restart zd-agent` |
| Restart only (改 config.yaml 后) | `sudo systemctl restart zd-agent`（config 挂载只读 volume，无需 rebuild）|
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
