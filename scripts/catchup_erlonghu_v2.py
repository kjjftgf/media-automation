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

def ls_share(pdir_fid):
    ts = int(time.time() * 1000)
    params = f"pwd_id={SID}&stoken={stok}&pdir_fid={pdir_fid}&_page=1&_size=200&_fetch_total=1&pr=ucpro&fr=pc&__dt={ts}&__t={ts}"
    url = f"{BASE}/1/clouddrive/share/sharepage/detail?{params}"
    req = urllib.request.Request(url, headers={"Cookie": cookie})
    resp = urllib.request.urlopen(req, timeout=15, context=ctx)
    data = json.loads(resp.read())
    return data.get("data", {}).get("list", [])

# Step 1: Get stoken
print("Getting stoken...")
stok_result = adapter.get_stoken(SID)
stok = stok_result["data"]["stoken"]
print(f"STOKEN_OK: {stok[:20]}...")

# Step 2: List share root
print("\nListing share root...")
root_items = ls_share("0")
for item in root_items:
    print(f"  {'[DIR]' if item.get('dir') else '[FILE]'} fid={item['fid']} {item.get('file_name','')[:60]}")

# Find the share dir (should be "ERLONG")
rf = None
for item in root_items:
    if item.get("dir"):
        rf = item["fid"]
        break

if not rf:
    print("ERROR: No share dir found")
    sys.exit(1)

# Step 3: List inside share dir
print(f"\nListing inside share dir (fid={rf})...")
items = ls_share(rf)
dirs = []
files = []
for item in items:
    is_dir = item.get("dir")
    fn = item.get("file_name", "")
    fid = item.get("fid", "")
    token = item.get("share_fid_token", "")
    if is_dir:
        dirs.append(item)
        print(f"  [DIR] {fn} (fid={fid})")
    else:
        files.append(item)
        # Extract episode
        m = re.search(r'[Ss](\d{2})\s*[Ee](\d{2,3})', fn)
        ep = m.group(2) if m else "?"
        print(f"  [FILE] ep={ep} | {fn[:60]}")

# If files empty, look inside subdir
if not files and dirs:
    print("\nNo files in root dir, searching subdirs...")
    for d in dirs:
        dn = d.get("file_name", "")
        print(f"\n  Checking subdir: {dn} (fid={d['fid']})")
        sub_items = ls_share(d["fid"])
        sub_dirs = [x for x in sub_items if x.get("dir")]
        sub_files = [x for x in sub_items if not x.get("dir")]
        for f in sub_files:
            fn = f.get("file_name", "")
            m = re.search(r'[Ss](\d{2})\s*[Ee](\d{2,3})', fn)
            ep = int(m.group(1) + m.group(2)) if m else 0
            if m:
                print(f"    [FILE] S{m.group(1)}E{m.group(2)} | {fn[:50]}")
            else:
                print(f"    [FILE] no_ep | {fn[:50]}")
            files.append(f)
        if sub_dirs:
            print(f"    (has {len(sub_dirs)} subdirs)")
            for sd in sub_dirs:
                sd_items = ls_share(sd["fid"])
                for f in [x for x in sd_items if not x.get("dir")]:
                    fn = f.get("file_name", "")
                    files.append(f)
                    m = re.search(r'[Ss](\d{2})\s*[Ee](\d{2,3})', fn)
                    ep = f"S{m.group(1)}E{m.group(2)}" if m else "?"
                    print(f"      [FILE] {ep} | {fn[:50]}")

# Step 4: Find new episodes (E20+)
print(f"\nTotal files: {len(files)}")
new_eps = []
for f in files:
    fn = f.get("file_name", "")
    m = re.search(r'[Ss](\d{2})\s*[Ee](\d{2,3})', fn)
    if m:
        sn = int(m.group(1))
        ep = int(m.group(2))
        if sn == SE and ep >= 20:
            new_eps.append({**f, "ep": ep, "season": sn})

new_eps.sort(key=lambda x: x["ep"])
print(f"New episodes (S03E20+): {len(new_eps)}")
for f in new_eps:
    print(f"  S03E{f['ep']:02d} | {f['file_name'][:50]}")

if not new_eps:
    print("\nNO_NEW: All episodes already imported")
    sys.exit(0)

# Step 5: Save new files
print(f"\nSaving {len(new_eps)} new files...")
fids = [f["fid"] for f in new_eps]
tokens = [f.get("share_fid_token", "") for f in new_eps]

save_body = json.dumps({
    "fid_list": fids,
    "fid_token_list": tokens,
    "to_pdir_fid": TARGET_DIR_FID,
    "pwd_id": SID,
    "stoken": stok,
    "pdir_fid": rf,
    "scene": "link"
}).encode()

r = urllib.request.Request(
    f"{BASE}/1/clouddrive/share/sharepage/save?pr=ucpro&fr=pc&app=clouddrive",
    data=save_body,
    headers={"Cookie": cookie, "Content-Type": "application/json"}
)
resp = urllib.request.urlopen(r, timeout=30, context=ctx)
save_result = json.loads(resp.read())
print(f"SAVE: code={save_result.get('code')}")
task_id = save_result.get("data", {}).get("task_id")
task_sync = save_result.get("data", {}).get("task_sync")
print(f"task_id={task_id}, task_sync={task_sync}")

# Wait for task if needed
if task_id and not task_sync:
    print(f"Waiting for task {task_id}...")
    for i in range(20):
        time.sleep(3)
        ts = int(time.time() * 1000)
        r = urllib.request.Request(
            f"{BASE}/1/clouddrive/task?task_id={task_id}&retry_index=0&__dt={ts}&__t={ts}",
            headers={"Cookie": cookie}
        )
        resp = urllib.request.urlopen(r, timeout=10, context=ctx)
        td = json.loads(resp.read())
        status = td.get("data", {}).get("status")
        if status == 2 or status is None:
            saved_fids = td.get("data", {}).get("save_as", {}).get("save_as_top_fids", [])
            print(f"  TASK_DONE: status={status}, saved={len(saved_fids)}")
            break
        print(f"  status={status}...")

# Step 6: Verify + rename
print("\nVerifying and renaming...")
time.sleep(3)
ts = int(time.time() * 1000)
r = urllib.request.Request(
    f"{BASE}/1/clouddrive/file/sort?pr=ucpro&fr=pc&pdir_fid={TARGET_DIR_FID}&_page=1&_size=200&_fetch_total=1&fetch_all_file=1&__dt={ts}&__t={ts}",
    headers={"Cookie": cookie}
)
resp = urllib.request.urlopen(r, timeout=15, context=ctx)
verify_data = json.loads(resp.read())
verify_items = verify_data.get("data", {}).get("list", [])

renamed = 0
deleted_dup = 0
for item in verify_items:
    fn = item.get("file_name", "")
    fid = item.get("fid", "")
    if item.get("dir"):
        continue
    if "tmdbid=" in fn:
        continue
    
    # Check obj_category
    cat = item.get("obj_category", "")
    vw = item.get("video_width", 0)
    if cat == "image" and fn.endswith((".mp4", ".mkv", ".ts")):
        print(f"  WARNING: {fn[:50]} classified as IMAGE!")
    if vw == 0:
        print(f"  WARNING: {fn[:50]} no video metadata (may be unplayable)")
    
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
        print(f"  SKIP (no ep): {fn[:50]}")
        continue
    
    # Find extension
    ext = ".mkv"
    for e in [".mp4", ".mkv", ".ts"]:
        if fn.lower().endswith(e):
            ext = e
            break
    
    new_name = f"{SHOW_NAME} [tmdbid={TMDB_ID}].S{SE:02d}E{ep:02d}{ext}"
    print(f"  RENAME: {fn[:50]} -> {new_name[:60]}")
    result = adapter.rename(fid, new_name)
    rc = result.get("code", -1)
    if rc == 0:
        renamed += 1
        print(f"    OK")
    elif rc == 23008:
        print(f"    CONFLICT: deleting")
        adapter.delete([fid])
        deleted_dup += 1
    else:
        print(f"    FAIL: code={rc}")

# Step 7: Final count
print(f"\nFinal check...")
time.sleep(2)
ts = int(time.time() * 1000)
r = urllib.request.Request(
    f"{BASE}/1/clouddrive/file/sort?pr=ucpro&fr=pc&pdir_fid={TARGET_DIR_FID}&_page=1&_size=200&_fetch_total=1&fetch_all_file=1&__dt={ts}&__t={ts}",
    headers={"Cookie": cookie}
)
resp = urllib.request.urlopen(r, timeout=15, context=ctx)
final_data = json.loads(resp.read())
final_items = [x for x in final_data.get("data", {}).get("list", []) if not x.get("dir")]
named = [x for x in final_items if "tmdbid=" in x.get("file_name", "")]
total_size = sum(int(x.get("size", 0)) for x in final_items)
max_ep = 0
for x in named:
    m = re.search(r'S03E(\d{2,3})', x.get("file_name", ""))
    if m:
        max_ep = max(max_ep, int(m.group(1)))

print(f"Total files: {len(final_items)}")
print(f"Named files: {len(named)}")
print(f"Total size: {total_size / (1024**3):.1f} GB")
print(f"Max episode: S03E{max_ep:02d}")
print(f"Renamed: {renamed}, Dups deleted: {deleted_dup}")

# Check for E20 specifically
has_e20 = any("S03E20" in x.get("file_name","") for x in final_items if "tmdbid=" in x.get("file_name",""))
print(f"E20 present: {has_e20}")

# Output structured summary
ep_range = f"S01E001~S03E{max_ep:03d}"
print(f"\nSUMMARY: files={len(final_items)}|size_gb={total_size/(1024**3):.1f}|max_ep={max_ep}|ep_range={ep_range}|renamed={renamed}|deleted={deleted_dup}")

print("\nDONE")
