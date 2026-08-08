import urllib.request, json, re, ssl, sys, time, sqlite3

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

sys.path.insert(0, "/app/backend")
from app.extensions.adapters.quark_adapter import QuarkAdapter

# Get cookie
db = sqlite3.connect("/app/backend/data/app.db")
cookie = db.execute("SELECT cookie FROM drive_accounts WHERE drive_type='quark' LIMIT 1").fetchone()
db.close()
cookie = cookie[0]
adapter = QuarkAdapter(cookie)

SID = "bfdb3dbcbf3b"
BASE = "https://drive-pc.quark.cn"
SHOW_NAME = "二龙湖·村暖花开"
TMDB_ID = "270845"
SE = 3
TARGET_DIR_FID = "bd8e2910b1a84a1ebd0e7c94e06a4d27"

# Step 1: Get stoken
print("Getting stoken...")
stok_result = adapter.get_stoken(SID)
stok = stok_result["data"]["stoken"]
print(f"STOKEN_OK: {stok[:20]}...")

# Step 2: List share root - try multiple approaches
# Approach 1: GET with pr/fr params
print("\nApproach 1: GET with pr/fr params")
try:
    ts = int(time.time() * 1000)
    params = f"pwd_id={SID}&stoken={stok}&pdir_fid=0&_page=1&_size=200&_fetch_total=1&pr=ucpro&fr=pc&__dt={ts}&__t={ts}"
    url = f"{BASE}/1/clouddrive/share/sharepage/detail?{params}"
    req = urllib.request.Request(url, headers={"Cookie": cookie})
    resp = urllib.request.urlopen(req, timeout=15, context=ctx)
    data = json.loads(resp.read())
    print(f"  code={data.get('code')}, items={len(data.get('data',{}).get('list',[]))}")
except Exception as e:
    print(f"  ERROR: {e}")

# Approach 2: Try with drive-m.quark.cn
print("\nApproach 2: drive-m.quark.cn")
BASE2 = "https://drive-m.quark.cn"
try:
    ts = int(time.time() * 1000)
    params = f"pwd_id={SID}&stoken={stok}&pdir_fid=0&_page=1&_size=200&pr=ucpro&fr=pc&__dt={ts}&__t={ts}"
    url = f"{BASE2}/1/clouddrive/share/sharepage/detail?{params}"
    req = urllib.request.Request(url, headers={"Cookie": cookie})
    resp = urllib.request.urlopen(req, timeout=15, context=ctx)
    data = json.loads(resp.read())
    print(f"  code={data.get('code')}, items={len(data.get('data',{}).get('list',[]))}")
    if data.get("code") == 0:
        items = data["data"]["list"]
        for item in items:
            print(f"  {'[DIR]' if item.get('dir') else '[FILE]'} {item.get('file_name','')[:60]}")
except Exception as e:
    print(f"  ERROR: {e}")

# Approach 3: Try POST with payload
print("\nApproach 3: POST with payload")
try:
    body = json.dumps({"pwd_id": SID, "stoken": stok, "pdir_fid": "0"}).encode()
    url = f"{BASE}/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc"
    req = urllib.request.Request(url, data=body, headers={
        "Cookie": cookie, "Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=15, context=ctx)
    data = json.loads(resp.read())
    print(f"  code={data.get('code')}, items={len(data.get('data',{}).get('list',[]))}")
except Exception as e:
    print(f"  ERROR: {e}")

# Approach 4: GET without pdir_fid
print("\nApproach 4: GET without pdir_fid")
try:
    ts = int(time.time() * 1000)
    url = f"{BASE}/1/clouddrive/share/sharepage/detail?pwd_id={SID}&stoken={stok}&_page=1&_size=200&pr=ucpro&fr=pc&__dt={ts}&__t={ts}"
    req = urllib.request.Request(url, headers={"Cookie": cookie})
    resp = urllib.request.urlopen(req, timeout=15, context=ctx)
    data = json.loads(resp.read())
    print(f"  code={data.get('code')}, items={len(data.get('data',{}).get('list',[]))}")
    if data.get("code") == 0:
        items = data["data"]["list"]
        for item in items:
            print(f"  {'[DIR]' if item.get('dir') else '[FILE]'} {item.get('file_name','')[:60]}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nDONE")
