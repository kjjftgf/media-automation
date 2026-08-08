import docker
import json
import requests

# --- Get cookie from CASX container ---
client = docker.DockerClient(base_url='unix://var/run/docker.sock')
container = client.containers.list(filters={"name": "cloud-auto-save-x"})[0]

python_code = """
import sqlite3
conn = sqlite3.connect('/app/backend/data/app.db')
cursor = conn.cursor()
cursor.execute("SELECT cookie FROM drive_accounts WHERE drive_type='quark'")
row = cursor.fetchone()
if row:
    print(row[0])
conn.close()
"""

result = container.exec_run(["python3", "-c", python_code])
cookie_raw = result.output.decode('utf-8').strip()
print(f"Cookie length: {len(cookie_raw)}")

# --- Quark API ---
BASE_URL = "https://drive-pc.quark.cn"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"

session = requests.Session()
session.headers.update({
    "cookie": cookie_raw,
    "content-type": "application/json",
    "user-agent": USER_AGENT,
})

def ls_dir(pdir_fid, page=1, size=50):
    url = f"{BASE_URL}/1/clouddrive/file/sort"
    params = {
        "pr": "ucpro",
        "fr": "pc",
        "uc_param_str": "",
        "pdir_fid": pdir_fid if pdir_fid else "0",
        "_page": page,
        "_size": size,
        "_fetch_total": "1",
        "_fetch_sub_dirs": "0",
        "_sort": "file_type:asc,updated_at:desc",
    }
    resp = session.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        print(f"  HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    return resp.json()

# --- Step 1: List 剧集 to find 二龙湖 ---
pdir_fid = 'e951d021e7f54e96b8b1a6f325b85c73'

print("\n=== Listing 剧集 directory ===")
data = ls_dir(pdir_fid, page=1, size=100)
if data is None:
    print("FAILED")
    exit(1)

code = data.get('code', -1)
total = data.get('data', {}).get('count', 0)
items = data.get('data', {}).get('list', [])
print(f"Code: {code}, Total: {total}, Items this page: {len(items)}")

erlonghu_dir = None
for item in items:
    name = item.get('file_name', item.get('name', ''))
    fid = item.get('fid', '')
    is_dir = item.get('dir', False) or item.get('file_type', '') == 'dir'
    size = item.get('size', 0)
    tag = '[DIR]' if is_dir else '     '
    print(f"  {tag} {name}  |  fid={fid}")
    if '二龙湖' in name:
        erlonghu_dir = item
        print(f"  *** FOUND 二龙湖! ***")

# If not found, paginate
page = 2
while erlonghu_dir is None and len(items) == 100:
    print(f"\n--- Page {page} ---")
    data = ls_dir(pdir_fid, page=page, size=100)
    if data is None:
        break
    items = data.get('data', {}).get('list', [])
    for item in items:
        name = item.get('file_name', item.get('name', ''))
        if '二龙湖' in name:
            erlonghu_dir = item
            print(f"  [DIR] {name}  *** FOUND! ***")
            break
    if not items:
        break
    page += 1

if not erlonghu_dir:
    print("\n*** 二龙湖 NOT FOUND in 剧集 ***")
    exit(1)

# --- Step 2: List all files inside 二龙湖 ---
erlonghu_fid = erlonghu_dir.get('fid')
erlonghu_name = erlonghu_dir.get('file_name', 'unknown')
print(f"\n{'='*60}")
print(f" Listing: {erlonghu_name} (fid={erlonghu_fid})")
print(f"{'='*60}")

seen_fids = set()
all_files = []
page = 1

while True:
    sub_data = ls_dir(erlonghu_fid, page=page, size=100)
    if sub_data is None:
        break
    
    code = sub_data.get('code', -1)
    if code != 0:
        print(f"API error code={code}: {json.dumps(sub_data, ensure_ascii=False)[:300]}")
        break
    
    sub_items = sub_data.get('data', {}).get('list', [])
    total_declared = sub_data.get('data', {}).get('count', 0)
    
    if not sub_items:
        print(f"Page {page}: empty, done")
        break
    
    new_count = 0
    dup_count = 0
    for item in sub_items:
        fid = item.get('fid', '')
        if fid not in seen_fids:
            seen_fids.add(fid)
            all_files.append(item)
            new_count += 1
        else:
            dup_count += 1
    
    print(f"Page {page}: {len(sub_items)} items, {new_count} new, {dup_count} dup (deduped: {len(all_files)} / declared: {total_declared})")
    
    if len(sub_items) < 100:
        break
    page += 1

# --- Sort and print ---
all_files.sort(key=lambda x: (x.get('file_type', '') != 'dir', x.get('file_name', x.get('name', ''))))

print(f"\n{'='*60}")
print(f" Directory: {erlonghu_name}")
print(f" Total files: {len(all_files)}")
print(f"{'='*60}\n")

for f in all_files:
    name = f.get('file_name', f.get('name', 'unknown'))
    size = f.get('size', f.get('file_size', 0))
    is_dir = f.get('dir', False) or f.get('file_type', '') == 'dir'
    fid = f.get('fid', '')
    
    try:
        size_int = int(size)
        if size_int >= 1073741824:
            size_str = f"{size_int/1073741824:.2f} GB"
        elif size_int >= 1048576:
            size_str = f"{size_int/1048576:.2f} MB"
        elif size_int >= 1024:
            size_str = f"{size_int/1024:.2f} KB"
        else:
            size_str = f"{size_int} B"
    except (ValueError, TypeError):
        size_str = str(size)
    
    tag = '[DIR]' if is_dir else '     '
    print(f"  {tag} {name}")
    print(f"       fid={fid}  size={size_str}")

print(f"\n{'='*60}")
print(f" Done. {len(all_files)} items in '{erlonghu_name}'")
