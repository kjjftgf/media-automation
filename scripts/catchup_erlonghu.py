import os
import urllib.request, json, re, ssl, sys, time, sqlite3

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

sys.path.insert(0, "/app/backend")
from app.extensions.adapters.quark_adapter import QuarkAdapter

# Get cookie from CASX DB
db = sqlite3.connect("/app/backend/data/app.db")
cookie = db.execute("SELECT cookie FROM drive_accounts WHERE drive_type='quark' LIMIT 1").fetchone()
db.close()

if not cookie:
    print("ERROR: No Quark cookie found")
    sys.exit(1)

cookie = cookie[0]
adapter = QuarkAdapter(cookie)

# Check cookie health
try:
    acct = adapter.get_account_info()
    print(f"COOKIE_OK: {acct.get('data', {}).get('nickname', 'unknown')[:20]}")
except Exception as e:
    print(f"COOKIE_CHECK_FAIL: {str(e)[:80]}")
    sys.exit(1)

# The best share link
SID = "bfdb3dbcbf3b"
BASE = "https://drive-h.quark.cn"
SHOW_NAME = "二龙湖·村暖花开"
TMDB_ID = "270845"
SE = 3
TARGET_DIR_FID = "bd8e2910b1a84a1ebd0e7c94e06a4d27"  # Existing show dir
PARENT_FID = "e951d021e7f54e96b8b1a6f325b85c73"  # 剧集 parent

headers = {"Content-Type": "application/json", "Cookie": cookie}

# Step 1: Get stoken
print("Getting stoken...")
stok_result = adapter.get_stoken(SID)
stok = stok_result["data"]["stoken"]
print(f"STOKEN_OK: {stok[:20]}...")

# Step 2: List root of share
print("Listing share root...")
import time as tmod
ts = int(tmod.time() * 1000)
r = urllib.request.Request(
    f"{BASE}/1/clouddrive/share/sharepage/detail?pwd_id={SID}&stoken={stok}&pdir_fid=0&_page=1&_size=200&_fetch_total=1&__dt={ts}&__t={ts}",
    headers=headers
)
resp = urllib.request.urlopen(r, timeout=15, context=ctx)
data = json.loads(resp.read())
items = data.get("data", {}).get("list", [])
print(f"Root items: {len(items)}")

# Find the share root directory
rf = None
for item in items:
    if item.get("dir"):
        rf = item["fid"]
        print(f"Share dir: {item['file_name']} (fid={rf})")
        break

if not rf:
    print("ERROR: No share root dir found")
    sys.exit(1)

# Step 3: List files inside share root
print(f"Listing files in share dir {rf}...")
ts = int(tmod.time() * 1000)
r = urllib.request.Request(
    f"{BASE}/1/clouddrive/share/sharepage/detail?pwd_id={SID}&stoken={stok}&pdir_fid={rf}&_page=1&_size=200&_fetch_total=1&__dt={ts}&__t={ts}",
    headers=headers
)
resp = urllib.request.urlopen(r, timeout=15, context=ctx)
data = json.loads(resp.read())
items = data.get("data", {}).get("list", [])

# Separate dirs and files
dirs = [x for x in items if x.get("dir")]
files = [x for x in items if not x.get("dir")]
print(f"Subdirs: {len(dirs)}, Files: {len(files)}")

# Extract episodes from file names
all_files = []
actual_dir_fid = rf  # The fid to use as pdir_fid for save

if files:
    for f in files:
        fn = f["file_name"]
        fid = f["fid"]
        token = f.get("share_fid_token", "")
        m = re.search(r'[Ss](\d{2})\s*[Ee](\d{2,3})', fn)
        m2 = re.search(r'[Ee](\d{2,3})', fn)
        m3 = re.match(r'^(\d{1,2})[\s\.\-]', fn)
        ep = None
        if m:
            ep = int(m.group(2))
        elif m2:
            ep = int(m2.group(1))
        elif m3:
            ep = int(m3.group(1))
        
        all_files.append({
            "fid": fid, "name": fn, "token": token, "ep": ep,
            "dir_fid": actual_dir_fid
        })
        if ep:
            print(f"  FILE: ep={ep:02d} | {fn[:60]}")
        else:
            print(f"  FILE: ep=None | {fn[:60]}")

# If no files, check subdirs for S03
if not files and dirs:
    print("No files in root, checking subdirs...")
    for d in dirs:
        dn = d["file_name"]
        if re.search(r'(?i)\bS0?3\b', dn) or "第三季" in dn or "村暖花开3" in dn:
            print(f"Found S03 subdir: {dn} (fid={d['fid']})")
            actual_dir_fid = d["fid"]
            ts = int(tmod.time() * 1000)
            r = urllib.request.Request(
                f"{BASE}/1/clouddrive/share/sharepage/detail?pwd_id={SID}&stoken={stok}&pdir_fid={d['fid']}&_page=1&_size=200&_fetch_total=1&__dt={ts}&__t={ts}",
                headers=headers
            )
            resp = urllib.request.urlopen(r, timeout=15, context=ctx)
            data = json.loads(resp.read())
            sub_items = data.get("data", {}).get("list", [])
            for f in [x for x in sub_items if not x.get("dir")]:
                fn = f["file_name"]
                fid = f["fid"]
                token = f.get("share_fid_token", "")
                m = re.search(r'[Ss](\d{2})\s*[Ee](\d{2,3})', fn)
                m2 = re.search(r'[Ee](\d{2,3})', fn)
                m3 = re.match(r'^(\d{1,2})[\s\.\-]', fn)
                ep = None
                if m:
                    ep = int(m.group(2))
                elif m2:
                    ep = int(m2.group(1))
                elif m3:
                    ep = int(m3.group(1))
                
                all_files.append({
                    "fid": fid, "name": fn, "token": token, "ep": ep,
                    "dir_fid": actual_dir_fid
                })
                if ep:
                    print(f"  FILE: ep={ep:02d} | {fn[:60]}")
                else:
                    print(f"  FILE: ep=None | {fn[:60]}")
            break

# Step 4: Filter to only E20+ (new episodes)
new_files = [f for f in all_files if f["ep"] is not None and f["ep"] >= 20]
print(f"\nNew episodes to import (E20+): {len(new_files)}")
for f in new_files:
    print(f"  -> ep={f['ep']:02d} | {f['name'][:60]}")

if not new_files:
    print("NO_NEW_EPISODES: All episodes already imported or no valid files found")
    sys.exit(0)

# Step 5: Save only new files
fids = [f["fid"] for f in new_files]
tokens = [f["token"] for f in new_files]

print(f"\nSaving {len(fids)} new files to target dir {TARGET_DIR_FID}...")
save_body = json.dumps({
    "fid_list": fids,
    "fid_token_list": tokens,
    "to_pdir_fid": TARGET_DIR_FID,
    "pwd_id": SID,
    "stoken": stok,
    "pdir_fid": actual_dir_fid,
    "scene": "link"
}).encode()

r = urllib.request.Request(
    f"{BASE}/1/clouddrive/share/sharepage/save?pr=ucpro&fr=pc&app=clouddrive",
    data=save_body, headers=headers
)
resp = urllib.request.urlopen(r, timeout=30, context=ctx)
save_result = json.loads(resp.read())
print(f"SAVE_RESULT: code={save_result.get('code')}")

# Check for task_sync
task_id = save_result.get("data", {}).get("task_id")
task_sync = save_result.get("data", {}).get("task_sync")
print(f"task_id={task_id}, task_sync={task_sync}")

if task_sync:
    print("SAVE_SYNC: Files synced immediately")
elif task_id:
    print(f"Waiting for task {task_id}...")
    for i in range(20):
        tmod.sleep(3)
        ts = int(tmod.time() * 1000)
        r = urllib.request.Request(
            f"{BASE}/1/clouddrive/task?task_id={task_id}&retry_index=0&__dt={ts}&__t={ts}",
            headers=headers
        )
        resp = urllib.request.urlopen(r, timeout=10, context=ctx)
        td = json.loads(resp.read())
        status = td.get("data", {}).get("status")
        if status == 2 or status is None:
            saved_fids = td.get("data", {}).get("save_as", {}).get("save_as_top_fids", [])
            print(f"TASK_DONE: status={status}, saved_count={len(saved_fids)}")
            break
        print(f"  Task status: {status}")

# Step 6: Verify saved files with file/sort + rename
print("\nVerifying saved files...")
ts = int(tmod.time() * 1000)
r = urllib.request.Request(
    f"{BASE}/1/clouddrive/file/sort?pr=ucpro&fr=pc&pdir_fid={TARGET_DIR_FID}&_page=1&_size=200&_fetch_total=1&fetch_all_file=1&__dt={ts}&__t={ts}",
    headers=headers
)
resp = urllib.request.urlopen(r, timeout=15, context=ctx)
verify_data = json.loads(resp.read())
verify_items = verify_data.get("data", {}).get("list", [])

# Check for unplayable files
for item in verify_items:
    fn = item.get("file_name", "")
    if item.get("dir"):
        continue
    cat = item.get("obj_category", "")
    vw = item.get("video_width", 0)
    if cat == "image" and fn.endswith((".mp4", ".mkv", ".ts")):
        print(f"WARNING: {fn[:50]} classified as IMAGE but is video!")
    if vw == 0 and not item.get("dir"):
        print(f"WARNING: {fn[:50]} has no video metadata!")

# Step 7: Rename new files to VidHub format
print("\nRenaming new files...")
ext_map = {
    ".mp4": ".mp4", ".mkv": ".mkv", ".ts": ".ts",
    ".MP4": ".mp4", ".MKV": ".mkv", ".TS": ".ts"
}

for item in verify_items:
    fn = item.get("file_name", "")
    fid = item.get("fid", "")
    if item.get("dir"):
        continue
    
    # Skip already renamed files
    if "tmdbid=" in fn:
        continue
    
    # Extract episode
    m = re.search(r'[Ss](\d{2})\s*[Ee](\d{2,3})', fn)
    m2 = re.search(r'[Ee](\d{2,3})', fn)
    m3 = re.match(r'^(\d{1,2})[\s\.\-]', fn)
    ep = None
    if m:
        ep = int(m.group(2))
    elif m2:
        ep = int(m2.group(1))
    elif m3:
        ep = int(m3.group(1))
    
    if ep is None:
        print(f"SKIP (no ep): {fn[:50]}")
        continue
    
    # Find extension
    ext = None
    for e in ext_map:
        if fn.lower().endswith(e.lower()):
            ext = ext_map[e]
            break
    if not ext:
        ext = ".mkv"
    
    new_name = f"{SHOW_NAME} [tmdbid={TMDB_ID}].S{SE:02d}E{ep:02d}{ext}"
    print(f"RENAME: {fn[:50]} -> {new_name}")
    result = adapter.rename(fid, new_name)
    rc = result.get("code")
    if rc == 0:
        print(f"  OK")
    elif rc == 23008:
        print(f"  CONFLICT: deleting source")
        adapter.delete([fid])
    else:
        print(f"  FAIL: code={rc}")

# Step 8: Final count
print("\nFinal directory listing:")
ts = int(tmod.time() * 1000)
r = urllib.request.Request(
    f"{BASE}/1/clouddrive/file/sort?pr=ucpro&fr=pc&pdir_fid={TARGET_DIR_FID}&_page=1&_size=200&_fetch_total=1&fetch_all_file=1&__dt={ts}&__t={ts}",
    headers=headers
)
resp = urllib.request.urlopen(r, timeout=15, context=ctx)
final_data = json.loads(resp.read())
final_items = [x for x in final_data.get("data", {}).get("list", []) if not x.get("dir")]
named = [x for x in final_items if "tmdbid=" in x.get("file_name", "")]
total_size = sum(int(x.get("size", 0)) for x in final_items)

# Get max episode
max_ep = 0
for x in named:
    m = re.search(r'S03E(\d{2,3})', x.get("file_name", ""))
    if m:
        max_ep = max(max_ep, int(m.group(1)))

print(f"Total files: {len(final_items)}")
print(f"Named files: {len(named)}")
print(f"Total size: {total_size / (1024**3):.1f} GB")
print(f"Max episode: S03E{max_ep:02d}")

# Output summary for Feishu update
print(f"SUMMARY: files={len(final_items)}|size_gb={total_size / (1024**3):.1f}|max_ep={max_ep}|ep_range=S01E001~S03E{max_ep:03d}")

print("DONE")
