# Deployment Runbook — zd-agent (Dify SaaS Support)

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
    "/Users/yangyonggang/Documents/工作/saas工单常见问题/zendesk-cli/工单记录/" \
    root@180.76.243.249:/opt/zd-agent/resources/zendesk-cli/工单记录/

rsync -av --progress \
    "/Users/yangyonggang/Documents/工作/saas工单常见问题/saas版知识库/" \
    root@180.76.243.249:/opt/zd-agent/resources/saas版知识库/

# Initialize the directory skeleton (creates .state, etc.)
ssh root@180.76.243.249 "/opt/zd-agent/code/scripts/init-resources.sh /opt/zd-agent/resources"
```

### 4. Clone the 5 SaaS source repos

```bash
ssh root@180.76.243.249
cd /opt/zd-agent/resources/saas源代码
for repo in dify dify-billing dify-marketplace-backend dify-marketplace-frontend saas-deploy; do
    git clone git@github.com:langgenius/$repo.git
done
```

### 5. Configure .env

```bash
cp /opt/zd-agent/code/.env.example /opt/zd-agent/.env
vim /opt/zd-agent/.env   # fill in all credentials
chmod 600 /opt/zd-agent/.env
```

Required variables (see `.env.example` for full list):
- ZENDESK_SUBDOMAIN / ZENDESK_EMAIL (yonggang@dify.ai，即运维人本人) / ZENDESK_API_TOKEN / ZENDESK_WEBHOOK_SECRET
- OPENAI_API_KEY
- FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_VERIFY_TOKEN / FEISHU_ENCRYPT_KEY / FEISHU_BOT_CHAT_ID
- GRAFANA_API_KEY / SIGNOZ_API_KEY
- GITHUB_TOKEN
- TELEPORT_USER

**可选**：
- `PG_PASSWORD` — 默认走 Teleport 证书认证不需要；仅在直连 DB 或双重认证时填

### 6. Configure Zendesk webhook + Trigger

本项目复用运维人本人的 Zendesk 账号（`yonggang@dify.ai`），不需要单独注册 bot 账号。
注意：agent 以你本人身份调 API，所有 Internal Note 落款都是你本人。

1. 用**管理员账号**登录 Zendesk，生成 API token：Admin Center → Apps → APIs → Zendesk API tokens → Add
   - Token 与谁生成无关，最终身份取决于 `.env` 里 `ZENDESK_EMAIL`
2. 确认 `yonggang@dify.ai` 账号的 **Role = Agent**（End user 不能写 Internal Note）
3. Create webhook:
   - Admin Center → Apps and integrations → Webhooks → Create webhook
   - Endpoint: `https://zd-agent.smlershou.top/webhook/zendesk`
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
2. Set event subscription URL: `https://zd-agent.smlershou.top/webhook/feishu`
3. Subscribe events: `im.message.receive_v1`
4. Add bot to your team chat
5. Copy App ID / Secret / Verify Token / Encrypt Key → .env

### 8. DNS

Add A record at your DNS provider:
- `zd-agent.smlershou.top` → `180.76.243.249`
- Wait for propagation (typically < 5 min)

### 9. Nginx + Let's Encrypt

```bash
sudo cp /opt/zd-agent/code/deploy/nginx/zd-agent.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/zd-agent.conf /etc/nginx/sites-enabled/

# Get cert
sudo certbot certonly --webroot -w /var/www/html -d zd-agent.smlershou.top

# Reload
sudo nginx -t && sudo systemctl reload nginx
```

### 10. Install Teleport CLI + Machine ID (or use headless login)

```bash
curl https://teleport.dify.dev/scripts/install.sh | bash
```

First-time manual login (sets up TELEPORT_HOME):
```bash
sudo -u zd-agent TELEPORT_HOME=/opt/zd-agent/.tsh tsh login --headless --proxy=teleport.dify.dev --user=$TELEPORT_USER
# Click the printed URL on your phone to approve
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
sudo cp /opt/zd-agent/code/deploy/systemd/*.path /etc/systemd/system/
sudo cp /opt/zd-agent/code/deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now tsh-proxy-db.service \
                            tsh-proxy-db-billing.service \
                            tsh-proxy-db-plugin.service
sudo systemctl enable --now tsh-auth-watchdog.service
sudo systemctl enable --now proxy-health.timer  # 每 2min 探测 3 端口、socket 死自动 restart
sudo systemctl enable --now zd-agent.service

# Verify
sudo systemctl status zd-agent tsh-proxy-db tsh-auth-watchdog proxy-health.timer
sudo journalctl -u zd-agent -f
```

---

## Daily Operations

| What | How |
|------|-----|
| Restart agent | `sudo systemctl restart zd-agent` |
| View live logs | `sudo docker logs -f zd-agent` (or `journalctl -u zd-agent -f`) |
| Manual Teleport renew | Feishu `/relogin` or `sudo systemctl restart tsh-auth-watchdog` |
| Pause processing | Feishu `/pause` (doesn't restart process, just suspends polling) |
| Resume | Feishu `/resume` |
| Force reprocess ticket | Feishu `/reprocess 12345` |
| Check status | Feishu `/status` |
| Update SaaS branch | Feishu `/branch dify hotfix/1.11.2-fix3` |
| Update agent code | `cd /opt/zd-agent/code && git pull && sudo docker build --network host -f deploy/Dockerfile -t zd-agent:latest . && sudo systemctl restart zd-agent` |
| Restart only (改 config.yaml 后) | `sudo systemctl restart zd-agent`（config 挂载只读 volume，无需 rebuild）|
| Refresh DB schema cheat sheet | `bash /opt/zd-agent/code/scripts/dump_db_schema.sh` (写到 saas版知识库/database_schema.md，agent 重启后即生效) |

## Daily Backups

The backup cron should copy `.state/` + `工单记录/` daily. Add to root's crontab:

```
0 2 * * * tar czf /backup/zd-agent-$(date +\%Y\%m\%d).tar.gz /opt/zd-agent/resources/.state /opt/zd-agent/resources/zendesk-cli/工单记录 && find /backup -name "zd-agent-*.tar.gz" -mtime +7 -delete
```

## Health Check

```bash
curl -I https://zd-agent.smlershou.top/healthz
# Expected: HTTP/2 200

curl -X POST https://zd-agent.smlershou.top/webhook/zendesk
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
- Missing env var → check `/opt/zd-agent/.env` has all required keys
- Port conflict → `sudo ss -tlnp | grep 8080`
- Volume mount error → `ls -la /opt/zd-agent/resources`

### Teleport cert expired & Feishu not pushing

```bash
sudo -u zd-agent TELEPORT_HOME=/opt/zd-agent/.tsh tsh status
sudo journalctl -u tsh-auth-watchdog -n 50
```

Manually trigger renew:
```bash
sudo -u zd-agent TELEPORT_HOME=/opt/zd-agent/.tsh tsh login --headless --proxy=teleport.dify.dev --user=$TELEPORT_USER
```

### Webhook 401 for legit requests

Make sure ZENDESK_WEBHOOK_SECRET in .env matches the secret shown in Zendesk Admin Center → Webhooks → your webhook → "Signed Secret".

### SQL queries failing

Check Teleport tunnel:
```bash
sudo ss -tlnp | grep 56061
# Should show tsh listening on 127.0.0.1:56061
```

If nothing on 56061: `sudo systemctl restart tsh-proxy-db`.

### Out of memory

Check container mem:
```bash
sudo docker stats --no-stream zd-agent
```

If near the 1500m limit: raise the `--memory` in `zd-agent.service` (after upgrading instance memory).

---

## 工单日志 Web 界面（log-viewer）

按工单查看 agent 处理日志，`https://zendesk.smlershou.top/logs/`，Google 登录保护（仅 `@dify.ai`）。

### 架构（三个独立组件）

```
浏览器 ─Google登录─> nginx /logs/ ─auth_request─> oauth2-proxy(:4180)
                                       │ 已登录(@dify.ai)
                                       ▼
                       log-viewer FastAPI(:8081) ─读─> /opt/zd-agent/logs/events-*.jsonl
                                                              ▲
                       zd-agent 容器 ─structlog jsonl sink──┘
```

- **jsonl 落盘**：agent `log.py` 在 stdout(ConsoleRenderer)外额外写 JSON 到
  `/opt/zd-agent/logs/events-<UTC日期>.jsonl`（`config.yaml` 的 `logging.jsonl_enabled` 控制；
  容器挂载 `-v /opt/zd-agent/logs:/opt/zd-agent/logs`，已在 `zd-agent.service`）。
- **log-viewer**（`zd-log-viewer.service`，宿主 venv uvicorn :8081）：只读 jsonl，纯只读无认证逻辑。
- **oauth2-proxy**（`oauth2-proxy.service` :4180）：Google OAuth + `--email-domain=dify.ai`。
  密钥在 `/opt/zd-agent/oauth2-proxy.env`（chmod 600，**不进 git**）。

### ⚠️⚠️ nginx 最大的坑（务必先读）

本机有**两套** nginx 配置树，实际生效的是**宝塔(BT Panel)**，不是 `/etc/nginx`：

- 运行中的 nginx：`/usr/sbin/nginx -c /www/server/nginx/conf/nginx.conf`，
  include `/www/server/panel/vhost/nginx/*.conf` → 真正生效的 vhost 是
  **`/www/server/panel/vhost/nginx/zendesk.smlershou.top.conf`**（证书也在 `/www/server/panel/vhost/cert/`）。
- `/etc/nginx/`（sites-enabled/conf.d）是**另一套、不被运行 nginx 使用**的影子配置。
  `nginx -T`（不带 -c）默认读 `/etc/nginx/nginx.conf` → 显示影子配置，**看着对其实没生效**
  （曾因此 /logs/ 裸奔 200，改 /etc/nginx 完全无效，排查了一整轮）。
- **判断哪套生效**：`ss -tlnp | grep :443` 找 pid → `tr '\0' ' ' < /proc/<pid>/cmdline` 看 `-c`。
- **改配置只改宝塔 vhost**；测试 `nginx -t -c /www/server/nginx/conf/nginx.conf`；
  reload `systemctl reload nginx`（systemd 管的就是这个进程）。
- **改完务必验证**：`curl -s -o /dev/null -w "%{http_code}" https://zendesk.smlershou.top/logs/`
  **检查 body 内容**：未登录访问应得 oauth2 登录页（含 "sign in"），**绝不能含 `ticket_id`**。
  注意：状态码 **200 或 302 都正常**（`error_page 401 = /oauth2/sign_in` 用 `=` 时返 200+登录页）。
  判定泄露看**内容有没有 ticket 数据**，不是看状态码。命令：
  `curl -s https://zendesk.smlershou.top/logs/api/tickets | grep -c ticket_id` → 必须 `0`。
- ⚠️ 宝塔 UI 里操作过该站点可能覆盖手加的 location，UI 操作后要复查 vhost。

vhost 里需要的三个 location（合并进宝塔 vhost）：`location /oauth2/`、`location = /oauth2/auth`、
`location /logs/`（含 `auth_request /oauth2/auth;` + `error_page 401 = /oauth2/sign_in;`）。
完整内容见 git `deploy/nginx/zd-agent.conf`（仅作参考，证书路径需改成宝塔的）。

### 一次性安装

```bash
# 1. log-viewer venv（⚠️ pip 装依赖要去掉 clash 代理，否则百度镜像失败）
cp -r /opt/zd-agent/code/log-viewer /opt/zd-agent/log-viewer
cd /opt/zd-agent/log-viewer && python3 -m venv .venv
env -u https_proxy -u http_proxy -u HTTPS_PROXY -u HTTP_PROXY .venv/bin/pip install fastapi uvicorn

# 2. oauth2-proxy 二进制（用 clash 代理拉 github）
cd /tmp && curl -fsSL -x http://127.0.0.1:7890 -o o2p.tar.gz \
  https://github.com/oauth2-proxy/oauth2-proxy/releases/download/v7.6.0/oauth2-proxy-v7.6.0.linux-amd64.tar.gz
tar xzf o2p.tar.gz && cp oauth2-proxy-*/oauth2-proxy /usr/local/bin/ && chmod +x /usr/local/bin/oauth2-proxy

# 3. 密钥 env（Client ID/Secret 来自 Google Cloud Console）
umask 077
cat > /opt/zd-agent/oauth2-proxy.env <<EOF
OAUTH2_PROXY_CLIENT_ID=<client id>.apps.googleusercontent.com
OAUTH2_PROXY_CLIENT_SECRET=<client secret>
OAUTH2_PROXY_COOKIE_SECRET=$(python3 -c 'import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')
EOF
chmod 600 /opt/zd-agent/oauth2-proxy.env

# 4. systemd
cp /opt/zd-agent/code/deploy/systemd/zd-log-viewer.service /etc/systemd/system/
cp /opt/zd-agent/code/deploy/systemd/oauth2-proxy.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now zd-log-viewer oauth2-proxy

# 5. nginx：把上面三个 location 合并进宝塔 vhost（不是 /etc/nginx！），然后：
nginx -t -c /www/server/nginx/conf/nginx.conf && systemctl reload nginx
# 验证：看 body 内容，不要只看状态码！未登录访问应返回 oauth2 登录页（含 sign in），
# 绝不能含 ticket_id（那才是泄露）。状态码 200 或 302 都正常（error_page = 用 200+登录页）：
curl -s https://zendesk.smlershou.top/logs/api/tickets | grep -c ticket_id   # 必须 0（无泄露）
```

### Google OAuth client 创建

1. Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID → **Web application**
2. Authorized redirect URIs 填精确值：`https://zendesk.smlershou.top/oauth2/callback`
3. Client ID + Secret 填入 `/opt/zd-agent/oauth2-proxy.env`
4. ⚠️ client secret 若曾在截图/聊天暴露，用完应在 Google Console 轮换。

### 常用操作

| What | How |
|------|-----|
| 更新 log-viewer 代码 | `cd /opt/zd-agent/code && git pull && cp -r log-viewer/* /opt/zd-agent/log-viewer/ && systemctl restart zd-log-viewer` |
| 改 oauth2-proxy 密钥 | 编辑 `/opt/zd-agent/oauth2-proxy.env` → `systemctl restart oauth2-proxy` |
| 改 nginx 后验证 | `curl -s -o /dev/null -w "%{http_code}" https://zendesk.smlershou.top/logs/` 应为 `302` |
| 看 oauth2-proxy 日志 | `journalctl -u oauth2-proxy -f` |
