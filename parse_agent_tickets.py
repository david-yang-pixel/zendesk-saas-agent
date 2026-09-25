import json

with open('/Users/yangyonggang/.claude/projects/-Users-yangyonggang-appdata-zendesk-agent/a140b5fc-fff2-467f-b969-9680b46de30e/tool-results/call_7c63055129ac9240.txt') as f:
    data = json.load(f)

results = data.get('data', {}).get('results', [])
print(f"Total tickets where support-agent commented: {len(results)}")
for t in results:
    print(f"ID: {t['id']} | Created: {t['created_at']} | Updated: {t['updated_at']} | Subject: {t.get('subject')}")
