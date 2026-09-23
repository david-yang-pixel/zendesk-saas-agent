import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

local_settings = {}
local_env_path = Path(__file__).with_name(".env")
if local_env_path.is_file():
    for line in local_env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in {"ZENDESK_EMAIL", "ZENDESK_API_TOKEN"}:
            local_settings[name] = value.strip().strip('"').strip("'")

zendesk_email = (os.environ.get("ZENDESK_EMAIL") or local_settings.get("ZENDESK_EMAIL", "")).strip()
zendesk_api_token = (os.environ.get("ZENDESK_API_TOKEN") or local_settings.get("ZENDESK_API_TOKEN", "")).strip()
if not zendesk_email or not zendesk_api_token:
    raise SystemExit("Set ZENDESK_EMAIL and ZENDESK_API_TOKEN in the environment or local .env")
auth = base64.b64encode((zendesk_email + "/token:" + zendesk_api_token).encode("utf-8")).decode("ascii")

class SameOriginHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urllib.parse.urlsplit(req.full_url)
        target_url = urllib.parse.urljoin(req.full_url, newurl)
        target = urllib.parse.urlsplit(target_url)
        if target.scheme != "https" or target.netloc.lower() != source.netloc.lower():
            raise urllib.error.HTTPError(req.full_url, code, "Blocked unsafe Zendesk redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, target_url)


opener = urllib.request.build_opener(SameOriginHttpsRedirectHandler)

def zendesk_req(url):
    target = urllib.parse.urlsplit(url)
    if target.scheme != "https" or target.netloc.lower() != "langgenius.zendesk.com":
        raise ValueError("Zendesk requests must use the configured HTTPS host")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json"
    })
    try:
        with opener.open(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error {url}: {e}")
        return None

# Find recent tickets
search_url = "https://langgenius.zendesk.com/api/v2/search.json?query=" + urllib.parse.quote("type:ticket order_by:created_at sort:desc")
data = zendesk_req(search_url)
if not data or "results" not in data:
    print("No results")
    exit()

print(f"Checking {len(data['results'])} tickets...")
leaked_tickets = []

for t in data["results"]:
    tid = t["id"]
    comments_data = zendesk_req(f"https://langgenius.zendesk.com/api/v2/tickets/{tid}/comments.json")
    if not comments_data:
        continue
    comments = comments_data.get("comments", [])
    for c in comments:
        # Check if public comment contains internal indicators
        is_public = c.get("public", False)
        body = c.get("body", "")
        author_id = c.get("author_id")

        # Leaked markers or internal patterns
        internal_indicators = [
            "INTERNAL_ANALYSIS",
            "CUSTOMER_REPLY",
            "调查备忘录",
            "Dify AI Agent 调查备忘录",
            "【内部分析",
            "内部排查",
            "[Dify AI Agent 调查备忘录]",
            "Trace ID",
            "SigNoz",
            "Postgres Admission",
            "DB Gateway",
            "feishu_doc_search",
            "official_docs_search"
        ]

        found = [ind for ind in internal_indicators if ind.lower() in body.lower()]
        if is_public and found:
            print(f"🚨 LEAK FOUND in Ticket #{tid} (public comment by author {author_id}): matched {found}")
            print(f"Snippet: {body[:300]}...\n")
            leaked_tickets.append((tid, found, body))
        elif not is_public and "Dify AI Agent" in body:
            print(f"ℹ️ Ticket #{tid} has private AI memo (author {author_id})")

print(f"Done. Total leaked tickets found: {len(leaked_tickets)}")
