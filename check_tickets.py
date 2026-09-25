import json
import urllib.request
import base64

# Read env
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
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

# Search recent tickets
data = zendesk_get(f"https://{subdomain}.zendesk.com/api/v2/search.json?query=type:ticket%20order_by:created_at%20sort:desc&per_page=50")

tickets = data.get('results', [])
print(f"Found {len(tickets)} tickets:")
for t in tickets[:30]:
    print(f"#{t['id']} | status: {t['status']} | tags: {t['tags']} | subject: {t['subject'][:40]} | created: {t['created_at']}")
