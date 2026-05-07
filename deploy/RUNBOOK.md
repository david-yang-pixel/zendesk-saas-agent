# Deployment Runbook — zd-agent (Dify SaaS Support)

## One-time Setup (~2 hours)

### 1. 服务器资源预算

本项目支持两种内存档位：

**🟢 推荐（4G）**：Baidu Cloud BCC Console → resize 到 **4G**（2C/4G）
- `config.yaml`: `max_concurrent_sessions: 5`, `max_tokens_per_session: 1050000`
- `zd-agent.service`: `--memory=1500m --cpus=1.0`（去掉 `--memory-swap`）
- 可同时跑 5 个工单分析会话

**🟡 最低（2G，当前服务器档位）** — 默认配置已按此优化：
- `config.yaml`: `max_concurrent_sessions: 2`, `max_tokens_per_session: 500000`, `compaction_threshold: 0.65`
- `zd-agent.service`: `--memory=800m --memory-swap=1500m --cpus=0.8`
- swap 作为 burst 兜底；2 个并发会话稳定运行
- **务必确认 swap 已启用**（`free -h` 看 Swap 行应 ≥ 1.5G）
  - 如没 swap：`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab`
- 高峰期建议监控：`docker stats zd-agent` 看 MEM % 是否长期 > 90%

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
sudo docker build -f deploy/Dockerfile -t zd-agent:latest .
```

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
sudo systemctl enable --now tsh-cert.path  # 续期成功后自动 restart 上面 3 个 proxy
sudo systemctl enable --now proxy-health.timer  # 每 2min 探测 3 端口、socket 死自动 restart
sudo systemctl enable --now zd-agent.service

# Verify
sudo systemctl status zd-agent tsh-proxy-db tsh-auth-watchdog tsh-cert.path proxy-health.timer
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
| Update agent code | `cd /opt/zd-agent/code && git pull && sudo docker build -f deploy/Dockerfile -t zd-agent:latest . && sudo systemctl restart zd-agent` |
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
