import os
import urllib.request, json, re, ssl, sys, time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Feishu auth
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE = os.environ.get("BITABLE_APP_TOKEN", "")
TABLE_TV = os.environ.get("FEISHU_TABLE_TV", "")
RECORD_ID = os.environ.get("FEISHU_RECORD_ID", "")

# Get token
data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=data, headers={"Content-Type": "application/json"}
)
token = json.loads(urllib.request.urlopen(req, timeout=15, context=ctx).read())["tenant_access_token"]
print(f"TOKEN_OK: {token[:10]}...")

# Current values from import
FILES = 21
SIZE_GB = 30.8
MAX_EP = 20
EP_RANGE = "S01E001~S03E020"

# Status: still 追更中 (not 24 yet)
STATUS = "optVZqcuvs"  # 🔄追更中 for 剧集表

# Update record
fields = {
    "📺 名字": "二龙湖·村暖花开",
    "集数": EP_RANGE,
    "文件数": FILES,
    "大小(GB)": round(SIZE_GB, 1),
    "状态": STATUS,
}

body = json.dumps({"fields": fields}).encode()
url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE}/tables/{TABLE_TV}/records/{RECORD_ID}"
req = urllib.request.Request(url, data=body, headers={
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
})
req.method = "PUT"
resp = urllib.request.urlopen(req, timeout=15, context=ctx)
result = json.loads(resp.read())
print(f"UPDATE: code={result.get('code')}, msg={result.get('msg')}")

# Verify
req2 = urllib.request.Request(
    f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE}/tables/{TABLE_TV}/records/{RECORD_ID}",
    headers={"Authorization": f"Bearer {token}"}
)
resp2 = urllib.request.urlopen(req2, timeout=15, context=ctx)
record = json.loads(resp2.read())
flds = record.get("data", {}).get("record", {}).get("fields", {})
print(f"\nRecord verification:")
print(f"  名字: {flds.get('📺 名字', '?')}")
print(f"  集数: {flds.get('集数', '?')}")
print(f"  文件数: {flds.get('文件数', '?')}")
print(f"  大小: {flds.get('大小(GB)', '?')} GB")
print(f"  状态: {flds.get('状态', '?')}")

# Also search xiaokupan for episodes beyond 20
print("\n--- Searching for E21+ ---")
for query in ["二龙湖", "二龙湖 村暖花开 21"]:
    q = urllib.parse.quote(query)
    url = f"https://xiaokupan.com/s/{q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0"
    })
    try:
        html = urllib.request.urlopen(req, timeout=15, context=ctx).read().decode('utf-8', errors='ignore')
        pattern = r'\{url:"(https://pan\.quark\.cn/s/[a-f0-9]+)",password:"[^"]*",note:"([^"]*)",datetime:"([^"]*)",source:"([^"]*)"'
        matches = re.findall(pattern, html)
        # Filter for 二龙湖 + high episode count
        for url_m, note, dt, src in matches:
            if "二龙湖" in note and "村暖" in note:
                ep_nums = [int(x) for x in re.findall(r'(\d+)', note) if 1 <= int(x) <= 50]
                max_ep_note = max(ep_nums) if ep_nums else 0
                if max_ep_note >= 21:
                    print(f"  E{max_ep_note}+: {note[:80]} | {dt[:10]} | {src}")
        if not any("二龙湖" in n and "村暖" in n for _, n, _, _ in matches):
            # Print all 二龙湖 results
            for url_m, note, dt, src in matches:
                if "二龙湖" in note:
                    ep_nums = [int(x) for x in re.findall(r'(\d+)', note) if 1 <= int(x) <= 50]
                    max_ep_note = max(ep_nums) if ep_nums else 0
                    print(f"  E{max_ep_note}: {note[:80]} | {dt[:10]}")
    except Exception as e:
        print(f"  Search error: {e}")

print("\nDONE")
