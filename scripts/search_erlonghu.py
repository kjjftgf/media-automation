import os
import urllib.request, json, re, ssl, sys, time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

query = "二龙湖"
url = f"https://xiaokupan.com/s/{urllib.parse.quote(query)}"
req = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0"
})

try:
    html = urllib.request.urlopen(req, timeout=15, context=ctx).read().decode('utf-8', errors='ignore')
except Exception as e:
    print(f"SEARCH_ERROR: {e}")
    sys.exit(1)

pattern = r'\{url:"(https://pan\.quark\.cn/s/[a-f0-9]+)",password:"[^"]*",note:"([^"]*)",datetime:"([^"]*)",source:"([^"]*)"'
matches = re.findall(pattern, html)

print(f"TOTAL_MATCHES: {len(matches)}")

results = []
for url, note, dt, src in matches:
    note_lower = note.lower()
    has_erlonghu = "二龙湖" in note
    has_cunnuan = "村暖" in note or "春暖" in note
    is_s3 = any(x in note_lower for x in ["s03", "season 3", "第三季", "season3", "s3"])
    
    score = 0
    if has_erlonghu: score += 10
    if has_cunnuan: score += 20
    if is_s3: score += 30
    
    ep_nums = [int(x) for x in re.findall(r'(\d+)', note) if 1 <= int(x) <= 50]
    max_ep = max(ep_nums) if ep_nums else 0
    
    if score >= 10:
        results.append({
            "url": url, "note": note, "datetime": dt, "source": src,
            "score": score, "max_ep_note": max_ep
        })

results.sort(key=lambda r: (r["score"], r["datetime"]), reverse=True)

for r in results[:10]:
    print(f'LINK: {r["url"]}|NOTE: {r["note"]}|DT: {r["datetime"]}|SRC: {r["source"]}|SCORE: {r["score"]}|MAXEP: {r["max_ep_note"]}')

print("DONE")
