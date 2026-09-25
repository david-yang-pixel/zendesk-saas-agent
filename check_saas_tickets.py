import json
import urllib.request
import base64

env = {}
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

subdomain = env['ZENDESK_SUBDOMAIN']
email = env['ZENDESK_EMAIL']
token = env['ZENDESK_API_TOKEN']
auth = base64.b64encode(f"{email}/token:{token}".encode()).decode()

def zendesk_get(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return {}

# Query tickets with tags: saas OR auto-reply-sent OR ai_handed_off OR dify-agent
# Let's search tickets with tag:saas
print("Searching tickets with tag:saas...")
data_saas = zendesk_get(f"https://{subdomain}.zendesk.com/api/v2/search.json?query=type:ticket%20tags:saas%20order_by:created_at%20sort:desc&per_page=50")
tickets_saas = data_saas.get('results', [])
print(f"Found {len(tickets_saas)} saas tickets:")
for t in tickets_saas[:20]:
    print(f"#{t['id']} | status: {t['status']} | tags: {t['tags']} | subject: {t['subject'][:50]} | created: {t['created_at']}")
