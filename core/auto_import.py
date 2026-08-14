#!/usr/bin/env python3
"""
影视资源全自动入库管线 v2
用法: python3 auto_import.py "https://pan.quark.cn/s/XXXXXX" [--type anime|tv|movie] [--season N] [--clean/--no-clean]
流程: 分享链接 → 解析文件 → TMDB匹配 → 画质检查/自动升级 → 转存夸克(原生POST+task验证) → VidHub命名(剧名 [tmdbid=XXX].SXXEXX.ext, 2位集数) → 飞书分表同步
"""
import os
import json, re, sys, socket, http.client, time


# ═══════════════════════════════════════════════════════════════
#  Docker Exec 工具 (通过 Unix socket 在 CASX 容器内执行)
# ═══════════════════════════════════════════════════════════════
SOCK = "/var/run/docker.sock"

def unix_req(method, path, body=None):
    conn = http.client.HTTPConnection("localhost")
    conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.sock.connect(SOCK)
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return raw, resp.status

def exec_casx(script, timeout=120):
    """在 cloud-auto-save-x 容器内执行 Python 代码"""
    raw, status = unix_req("POST", "/containers/cloud-auto-save-x/exec", {
        "Cmd": ["python3", "-c", script],
        "AttachStdout": True, "AttachStderr": True
    })
    if status != 201:
        return f"ERR {status}: {raw.decode()}"
    exec_id = json.loads(raw.decode())["Id"]
    raw, _ = unix_req("POST", f"/exec/{exec_id}/start", {
        "Detach": False, "Tty": False
    })
    out = ""
    i = 0
    while i + 8 <= len(raw):
        size = int.from_bytes(raw[i+4:i+8], 'big')
        i += 8
        out += raw[i:i+size].decode('utf-8', errors='replace')
        i += size
    return out


# ═══════════════════════════════════════════════════════════════
#  夸克目录 FID (完整32位, 不要截断!)
# ═══════════════════════════════════════════════════════════════
DIR_FIDS = {
    "动漫": "97aab41296184ea58cef2934889716c2",
    "剧集": "27bda1ac26de4240bbdb56e884da92e5",
    "电影": "c6106c11094c4c02b433e5239d37dbd5",  # ← 32位完整!
}
TABLE_MAP = {"动漫": "anime", "剧集": "tv", "电影": "movie"}


# ═══════════════════════════════════════════════════════════════
#  TMDB 搜索 (通过 CASX 容器访问 TMDB API)
# ═══════════════════════════════════════════════════════════════
def tmdb_search(title, count=5, retries=2):
    """用 TMDB search/multi 搜索，仅返回 tv/movie。自动重试 2 次"""
    safe = repr(title)
    script = f"""import sqlite3, json, requests
db=sqlite3.connect('/app/backend/data/app.db')
cfg=json.loads(db.execute("SELECT config_json FROM tmdb_settings LIMIT 1").fetchone()[0])
api_key=cfg.get("api_key","")
r=requests.get("https://api.themoviedb.org/3/search/multi",
    params={{'api_key':api_key,'query':{safe},'language':'zh-CN'}},timeout=15)
results=r.json().get('results',[])
filtered=[r for r in results if r.get('media_type') in ('tv','movie')][:{count}]
print(json.dumps([{{'id':r['id'],'name':r.get('name','') or r.get('title',''),
    'year':(r.get('first_air_date','') or r.get('release_date',''))[:4],
    'type':r.get('media_type',''),'overview':(r.get('overview','') or '')[:80]}}
    for r in filtered],ensure_ascii=False))
"""
    for attempt in range(retries):
        out = exec_casx(script, 20)
        try:
            result = json.loads(out.strip())
            if result: return result
        except:
            pass
        if attempt < retries - 1:
            time.sleep(2)
    return []


def select_best_tmdb(results, context):
    """智能挑选最佳 TMDB 匹配 (评分制, 非第一条)"""
    if not results: return None
    if len(results) == 1:
        return results[0]

    ctx_name = context.get("best_title", "").lower()
    ctx_years = context.get("years", [])
    ctx_is_tv = context.get("media_type", "") == "tv"

    best, best_score = None, -100
    for r in results:
        score = 0
        name = (r.get("name", "")).lower()
        year = int(r.get("year", "0") or 0)
        rtype = r.get("type", "")

        if ctx_name == name: score += 15
        elif ctx_name in name: score += 8
        elif name in ctx_name: score += 6

        if ctx_is_tv and rtype == "tv": score += 4
        elif ctx_is_tv and rtype == "movie": score -= 2
        elif not ctx_is_tv and rtype == "movie": score += 4

        if year and ctx_years:
            for cy in ctx_years:
                if abs(year - cy) <= 1: score += 3; break
            else: score -= 1

        overview = (r.get("overview", "") or "").lower()
        bad_words = ["documentary", "音乐会", "演唱会", "纪录片", "behind the scenes"]
        if any(w in overview for w in bad_words): score -= 20

        if score > best_score:
            best_score = score
            best = r

    return best or results[0]


# ═══════════════════════════════════════════════════════════════
#  SearXNG 交叉验证 (防九龙拉棺→遮天 类误匹配)
# ═══════════════════════════════════════════════════════════════
def searxng_search(query, max_results=10):
    """通过 SearXNG (127.0.0.1:8080) 搜索, 返回结果列表"""
    import urllib.request, urllib.parse
    q = urllib.parse.quote(query)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1/search?q={q}&format=json&language=zh-CN",
            headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return data.get("results", [])[:max_results]
    except Exception as e:
        sys.stderr.write(f"searxng_search error: {e}\n")
        return []


def searxng_cross_verify(title, tmdb_name):
    """
    用 SearXNG 交叉验证: 该标题是独立作品还是某作品的子章节/arc?
    策略: 搜索 "{title} 动漫/豆瓣" → 提取所有《书名》→ 频次最高的即为父作品
    返回: (verified_name, verified_id, verified_result_dict)
        所有为 None 表示无更优匹配
    """
    search_terms = [f"{title} 动漫", f"{title} 豆瓣"]
    all_text = []
    for term in search_terms:
        for r in searxng_search(term, 3):
            all_text.append(r.get("title", ""))
            snippet = r.get("content", "") or r.get("snippet", "")
            all_text.append(snippet)

    if not all_text:
        return None, None, None

    full_text = " ".join(all_text)

    # 提取所有《XXX》中的书名, 统计频次
    from collections import Counter
    book_names = Counter()
    for m in re.finditer(r'《([^》]{1,15})》', full_text):
        name = m.group(1).strip()
        # 清理: 去掉数字前缀、特殊字符
        name = re.sub(r'^\d+[·.\-]?', '', name)
        name = re.sub(r'[：:].*$', '', name)  # 去掉冒号后缀
        if len(name) >= 2 and name != title and name not in tmdb_name and title not in name:
            book_names[name] += 1

    if not book_names:
        return None, None, None

    # 取出现次数最多的 (至少出现 2 次才可信)
    best, count = book_names.most_common(1)[0]
    if count < 2:
        return None, None, None

    results = tmdb_search(best, 3)
    if results:
        return results[0]["name"], str(results[0]["id"]), results[0]

    return None, None, None


def is_suspicious_tmdb_match(name):
    """
    检查 TMDB 结果是否可疑:
    - 名称含 "之XXX" 后缀 (如 "九龙拉棺之轩辕出世" — 这通常是子章节)
    - 名称过长 (可能是拼接标题)
    """
    suspicious_patterns = [
        (r'之.{2,8}$',     '之XXX后缀 (子章节/arc)'),
        (r'[：:][^：:]{4,20}$', '冒号后缀'),
    ]
    # 短标题不触发 (如 "剑之歌" "星之梦")
    if len(name) < 6:
        return False
    for pat, _desc in suspicious_patterns:
        if re.search(pat, name):
            return True
    return False


# ═══════════════════════════════════════════════════════════════
#  分享内容解析
# ═══════════════════════════════════════════════════════════════
def list_share(share_id):
    """列出夸克分享链接的全部文件/目录树, 返回 D|... / F|... 行"""
    m = re.search(r'/s/([a-f0-9]+)', share_id)
    sid_raw = m.group(1) if m else share_id

    anchor_dir = ""
    m2 = re.search(r'#/list/share(/.*)', share_id)
    if m2 and m2.group(1):
        import urllib.parse
        anchor_dir = urllib.parse.unquote(m2.group(1)).rstrip("/")

    script = f"""import sqlite3, json, requests, time, re, sys
BASE="https://drive-pc.quark.cn"
db=sqlite3.connect("/app/backend/data/app.db")
config=json.loads(db.execute("SELECT config_json FROM drive_accounts WHERE drive_type='quark' AND enabled=1").fetchone()[0])
db.close()
h={{'User-Agent':'Mozilla/5.0','Cookie':config['cookie']}}
sys.path.insert(0,"/app/backend")
from app.extensions.adapters.quark_adapter import QuarkAdapter
adapter=QuarkAdapter(cookie=config['cookie'],account_name='quark')
SHARE="{sid_raw}"
ANCHOR="{anchor_dir}"
stok=adapter.get_stoken(SHARE)['data']['stoken']
def ls(pdir='0'):
    p={{'pr':'ucpro','fr':'pc','pwd_id':SHARE,'stoken':stok,'pdir_fid':pdir,'_page':'1','_size':'200','_sort':'file_type:asc'}}
    r=requests.get(BASE+'/1/clouddrive/share/sharepage/detail',params=p,headers=h,timeout=15)
    return r.json().get('data',{{}}).get('list',[])
def show(path,fid='0',d=0):
    if d>8:return
    for x in ls(fid):
        n=x.get('file_name','').replace('丨','').replace('｜','').replace('|','');di=x.get('dir',False);sz=x.get('size',0);ci=x.get('fid','');sft=x.get('share_fid_token','')
        if di:print('D|{{}}/{{}}|{{}}||{{}}|{{}}'.format(path,n,ci,x.get('share_fid_token',''),x.get('file_name','')));show(path+'/'+n,ci,d+1)
        else:print('F|{{}}/{{}}|{{}}|{{}}|{{}}|{{}}'.format(path,n,sz,ci,sft,n))
if ANCHOR:
    parts=ANCHOR.strip('/').split('/')
    cur='0'
    for part in parts:
        found=False
        for x in ls(cur):
            if x.get('dir') and x.get('file_name','')==part:cur=x['fid'];found=True;break
            elif x.get('dir') and part in x.get('file_name',''):cur=x['fid'];found=True;break
        if not found:print('ANCHOR_NOT_FOUND:'+part);sys.exit(1)
    show('/'+'/'.join(parts),cur,0)
else:
    show('/', '0', 0)
"""
    return exec_casx(script, 30)


def analyze(raw):
    """解析分享列表输出，提取标题、季、集、画质"""
    if not raw.strip(): return None
    lines = raw.strip().split("\n")
    dirs, files = [], []
    for line in lines:
        if line.startswith("D|"): dirs.append(line)
        elif line.startswith("F|"): files.append(line)
    if not files and not dirs: return None

    root_dir = ""
    for d in dirs:
        parts = d.split("|")
        path = parts[1] if len(parts) > 1 else ""
        # 找顶层目录: 格式 "//dirname" (path="/" + n) 或 "/dirname"
        stripped = path.strip("/")
        if stripped and "/" not in stripped:
            root_dir = stripped
            break
    best_title = root_dir
    if not best_title:
        # Use first filename (parts[5] is the original name!)
        for f_line in files:
            parts = f_line.split("|")
            best_title = parts[5] if len(parts) > 5 else parts[1].split("/")[-1]
            best_title = best_title.rsplit(".", 1)[0] if "." in best_title else best_title
            break
    if not best_title:
        best_title = files[0].split("|")[5] if len(files[0].split("|")) > 5 else "Unknown"

    # Clean title: remove year suffixes, quality tags, bracketed junk, season markers
    best_title = re.sub(r'[\[\(（]\d{4}[\]\)）]', '', best_title)
    best_title = re.sub(r'(?i)\.?(S\d{2}|Season\s*\d+|E\d{2,3})\.?', ' ', best_title)
    best_title = re.sub(r'(?i)\b(4k|2160p|1080p|720p|hdr|dv|h\.?265|hevc|x265|x264|bluray|web-dl|webrip|dubbed|subbed|completed|完结|remux)\b', '', best_title)
    # Also strip these without word boundary (for Chinese context)
    best_title = re.sub(r'(?i)(4k|2160p|1080p|720p)', '', best_title)
    # Strip Chinese quality descriptors (very common in share titles)
    best_title = re.sub(r'(?:蓝光|杜比视界|杜比全景声|杜比音效|杜比|全景声|高码率|高码|国英双音轨|国英|内封[简繁英中文字幕]+|内嵌[简繁英中文字幕]+|双语特效字幕|双语字幕|双语|特效字幕|中文字幕|原盘|REMUX)', '', best_title, flags=re.IGNORECASE)
    best_title = re.sub(r'[（(][^）)]*[）)]', '', best_title)
    best_title = re.sub(r'\[[^\]]*\]', '', best_title)
    best_title = re.sub(r'\s+', ' ', best_title).strip().rstrip("._- ")
    # Strip single-letter alphabetical prefix (e.g. "J 间谍过家家" → "间谍过家家", "Z杖与剑" → "杖与剑")
    best_title = re.sub(r'^[A-Za-z]\s*', '', best_title)
    # Strip Chinese season marker (e.g. "间谍过家家 第三季" → "间谍过家家")
    best_title = re.sub(r'\s*第\s*(?:[一二三四五六七八九十]+|\d+)\s*季\s*', '', best_title)

    episodes = set(); seasons = set(); years = set(); qualities = set()
    all_names = []; total_size = 0

    quality_map = {"4k":4,"2160p":4,"hdr":3,"dv":5,"bluray":3,"1080p":2,"remux":4,"高码率":3,"臻彩":4,"2160":4,"uhd":4}

    for f_line in files:
        parts = f_line.split("|")
        size = int(parts[2]) if len(parts) > 2 else 0
        total_size += size
        name = parts[5] if len(parts) > 5 else parts[1].split("/")[-1]
        all_names.append(name)
        name_lower = name.lower()

        ep_matches = re.findall(r'[Ss](\d{1,2})\s*[Ee](\d{1,3})', name)
        if ep_matches:
            for s, e in ep_matches: seasons.add(int(s)); episodes.add(int(e))
        else:
            ep_matches = re.findall(r'[Ee](\d{1,3})', name)
            if ep_matches: seasons.add(1); [episodes.add(int(e)) for e in ep_matches]
            else:
                ep_matches = re.findall(r'(?:^|\D)(\d{2,3})(?:\.\w+)?$', name)
                if ep_matches: seasons.add(1); [episodes.add(int(e)) for e in ep_matches]

        year_match = re.findall(r'(?:^|\D)(19|20)(\d{2})(?:\D|$)', name)
        for y1, y2 in year_match: years.add(int(y1 + y2))

        for tag in quality_map:
            # ⚠️ 'dv' 必须词边界匹配 (2026-08-11 修复): 子串匹配会误伤 'dvd' (480p老片源被误判为杜比视界DV)
            #    'xxx.DVD.xxx' → 'dv' in 'dvd' 为 True → 错误标记 DV(权重5最高) → has_4k 误判
            #    正确: 杜比视界标签写法为 .DV. 或 .dv. (词边界), DVD 是标清载体与 DV 无关
            if tag == 'dv':
                if re.search(r'(?<![a-z])dv(?![a-z])', name_lower) and 'dvd' not in name_lower:
                    qualities.add('DV')
            elif tag in name_lower:
                qualities.add(tag.upper() if len(tag) <= 3 else tag)

    has_4k = any(q in ("4K","2160P","HDR","DV","REMUX","UHD") for q in qualities)

    return {
        "best_title": best_title, "root_dir": root_dir,
        "episodes": sorted(episodes), "seasons": sorted(seasons),
        "years": sorted(years), "qualities": sorted(qualities),
        "has_4k": has_4k, "total_size": total_size,
        "files": files, "dirs": dirs, "all_names": all_names,
        "media_type": "tv" if (episodes or seasons) else "movie",
    }


# ═══════════════════════════════════════════════════════════════
#  画质标准 (2026-08-12 按当前公网网络重新制定)
#  ══════════════════════════════════════════════════════════════
#  公网链路实况 (2026-08-11 实测): frp 满速 5.2MB/s=41.6Mbps, 88frp
#  限速三档: 正常 5.2MB/s → 半速 2.5MB/s(20Mbps) → 极端 20KB/s(累计
#  流量触发≈看半小时4K)。按半速 20Mbps 留 25% 余量 → 安全码率 ≤15Mbps。
#
#  首选: 1080p WEB-DL (4-8Mbps) / 4K WEB-DL 低码率 (≤15Mbps) — 公网流畅
#  禁止: 4K REMUX / 原盘 / 高码率蓝光 (>25Mbps) — 公网必卡, 仅适合本地
# ═══════════════════════════════════════════════════════════════
PUBLIC_SAFE_MBPS = 15      # 公网安全码率上限 (88frp 半速限速 20Mbps 留余量)
PUBLIC_HARD_MBPS = 25      # 硬上限: 超过即视为公网不可播 (REMUX/原盘级别)
EP_DURATION_S = 2700       # 剧集单集估算时长 45min
MOVIE_DURATION_S = 7200    # 电影估算时长 2h

def estimate_bitrate_mbps(size_bytes, ep_count, is_movie=False):
    """估算平均码率 (Mbps): 大小×8 / 总时长秒。用于判断公网可播性"""
    if ep_count <= 0:
        ep_count = 1
    duration_s = (MOVIE_DURATION_S if is_movie else EP_DURATION_S) * ep_count
    if duration_s <= 0:
        return 0
    return size_bytes * 8 / duration_s / 1_000_000

def quality_fits_public(qualities, size_bytes, ep_count, is_movie=False):
    """判断当前源是否符合公网可播标准。
    返回: (fits, reason) — fits=True 达标直接入库; False 需搜索替代"""
    q_low = " ".join(qualities).lower()
    # 高码率标签: REMUX/原盘/蓝光 → 公网必卡
    has_high = ('remux' in q_low or '原盘' in q_low or 'bluray' in q_low
                or '蓝光' in q_low or '高码率' in q_low or '高码' in q_low)
    mbps = estimate_bitrate_mbps(size_bytes, ep_count, is_movie)
    if has_high:
        return False, f"含REMUX/原盘/蓝光标签(公网必卡, ~{mbps:.0f}Mbps)"
    if mbps > PUBLIC_HARD_MBPS:
        return False, f"码率 ~{mbps:.0f}Mbps 超过公网硬上限 {PUBLIC_HARD_MBPS}Mbps"
    if mbps > PUBLIC_SAFE_MBPS:
        return False, f"码率 ~{mbps:.0f}Mbps 超过安全线 {PUBLIC_SAFE_MBPS}Mbps(半速限速会卡)"
    # 低于1080p(720p/480p)也需要升级
    has_1080 = ('1080p' in q_low or '1080' in q_low or 'bluray' in q_low)
    has_4k = ('4k' in q_low or '2160p' in q_low or 'uhd' in q_low or '2160' in q_low)
    if not (has_1080 or has_4k):
        return False, f"画质 {q_low or '?'} 低于1080p"
    return True, f"{q_low or '?'} ~{mbps:.0f}Mbps 公网可播"


# ═══════════════════════════════════════════════════════════════
#  画质升级搜索
# ═══════════════════════════════════════════════════════════════
def search_better_quality(title, current_qualities):
    """通过 CloudSaver + NetSearch 搜索更高画质版本"""
    safe_title = repr(title)
    script = f"""import requests, json, re, urllib.parse
results=[]
title={safe_title}
try:
    token_r=requests.post("http://127.0.0.1/api/user/login",
        json={{'username':'admin','password': os.environ.get('CLOUDSAVER_ADMIN_CODE', '')}},timeout=10)
    tok=token_r.json().get('data',{{}}).get('token','')
    if tok:
        r=requests.get(f\"http://127.0.0.1/api/search?q={{urllib.parse.quote(title)}}&page=1&pageSize=5\",
            headers={{'Authorization':f'Bearer {{tok}}'}},timeout=15)
        raw=r.json().get('data',[])
        items=[]
        if isinstance(raw,list):
            for chunk in raw:
                if isinstance(chunk,dict):items.extend(chunk.get('list',[]))
        elif isinstance(raw,dict):items=raw.get('list',[])
        for item in items:
            content=item.get('content','') or ''
            cloud_links=item.get('cloudLinks',[])
            for cl in cloud_links:
                link=cl.get('link','')
                if 'pan.quark.cn' in link:results.append({{'title':item.get('title',title),'url':link,'source':'cloudsaver'}})
except Exception as e1:sys.stderr.write("CS_ERR:"+str(e1)[:60]+"\\n")
try:
    req=urllib.request.Request(f"http://127.0.0.1/api/search?kw={{urllib.parse.quote(title)}}&res=all",
        headers={{"User-Agent":"Mozilla/5.0"}})
    pd=json.loads(urllib.request.urlopen(req,timeout=15).read())
    if pd.get("code")==0:
        quarks=pd.get("data",{{}}).get("merged_by_type",{{}}).get("quark",[])
        for item in quarks:
            link=item.get("url","")
            if link and "pan.quark.cn" in link:
                results.append({{'title':item.get('note',''),'url':link,'source':'pansou'}})
except Exception as e2:sys.stderr.write("PS_ERR:"+str(e2)[:60]+"\\n")
print(json.dumps(results,ensure_ascii=False))
"""
    out = exec_casx(script, 30)
    try: return json.loads(out.strip())
    except: return []


def pick_best_upgrade(candidates, original_url, analysis, season=None):
    """从候选链接中选最佳升级版。season 参数用于筛选匹配季度的结果

    2026-08-12 新标准 (公网可播优先): 评分不再无脑追 4K REMUX/原盘,
    而是综合「分辨率 + 公网可播性」:
      - 4K WEB-DL (低码率, 公网可播) > 1080p WEB-DL > 4K REMUX > 原盘
      - REMUX/原盘/蓝光 = 高码率 → 公网必卡 → 大幅降权
      - WEB-DL/流媒体 = 码率适中 → 公网友好 → 加分
    """
    quark_candidates = [c for c in candidates if 'quark' in c['url']]
    if not quark_candidates: return None, None
    scored = []
    for c in quark_candidates:
        score = 0
        name_low = c['title'].lower()
        # 分辨率 (0-12)
        if '4k' in name_low or '2160p' in name_low or 'uhd' in name_low or '2160' in name_low:
            score += 12
        elif '1080p' in name_low:
            score += 8
        # HDR/DV (色彩加分)
        if 'hdr' in name_low: score += 3
        # ⚠️ 'dv' 词边界匹配 (2026-08-11 修复): 'dvd' 标题会被误判为杜比视界+9
        #    Apple 生态: DV P5/P8 完美点亮(权重最高), HDR10+ 不支持只按 HDR10 回放(已被上方 hdr 子串覆盖+8)
        if re.search(r'(?<![a-z])dv(?![a-z])', name_low) and 'dvd' not in name_low: score += 4
        # ═══ 公网可播性修正 (2026-08-12) ═══
        # REMUX/原盘/蓝光 = 高码率 50-90Mbps → 公网必卡 → 大降权
        if 'remux' in name_low: score -= 12
        if '原盘' in c['title'] or 'bluray' in name_low or '蓝光' in c['title']: score -= 10
        if '高码率' in c['title'] or '高码' in c['title']: score -= 6
        # WEB-DL/流媒体/在线 = 码率适中 → 公网友好 → 加分
        if 'web-dl' in name_low or 'webdl' in name_low or 'webrip' in name_low \
           or '流媒体' in c['title'] or '在线' in c['title']: score += 6
        # Season boost: match specific season in title
        if season and season > 1:
            s_tag = f's{season:02d}'
            if s_tag in name_low or f' 第{season}季' in name_low:
                score += 3
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] > 0:
        best = scored[0][1]
        m = re.search(r'/s/([a-f0-9]+)', best['url'])
        return (m.group(1) if m else None), best['title']
    return None, None


# ═══════════════════════════════════════════════════════════════
#  核心入库 — 基于 fix_v4.py 战绩验证逻辑
# ═══════════════════════════════════════════════════════════════
def do_import(share_id, tmdb_id, show_name, target_subdir="动漫", season=1, clean=True, password="", anchor=""):
    """
    转存夸克分享 → task验证 → 规范化命名 → 返回统计
    ★ 完全重写: 原生POST保存 + task验证 + 季过滤rename

    参数:
      share_id:   夸克分享ID (如 "cd66f04e79ab")
      tmdb_id:    TMDB ID
      show_name:  规范化剧名
      target_subdir: 目标子目录 ("动漫"/"剧集"/"电影")
      season:     当前季数 (用于文件命名和rename过滤)
      clean:      True=删除旧目录重建 / False=保留追加
      anchor:     分享内子目录锚点路径 (如 "xxx/HDR"), 为空则取第一个目录

    返回: "IMPORTED|ep_count|size_gb|detected_quality|file_count"
    """
    sid = share_id
    m = re.search(r'/s/([a-f0-9]+)', share_id)
    if m: sid = m.group(1)

    parent_fid = DIR_FIDS.get(target_subdir, DIR_FIDS["动漫"])

    # 安全转义: 所有外部输入用 json.dumps 序列化, 杜绝代码注入
    import json as _json
    sid_safe = _json.dumps(sid)
    tmdb_safe = _json.dumps(str(tmdb_id))
    show_safe = _json.dumps(show_name)
    pwd_safe = _json.dumps(password or "")
    anchor_safe = _json.dumps(anchor or "")

    script = f"""import sqlite3, json, requests, time, re, sys
BASE="https://drive-pc.quark.cn"
db=sqlite3.connect("/app/backend/data/app.db")
config=json.loads(db.execute("SELECT config_json FROM drive_accounts WHERE drive_type='quark' AND enabled=1").fetchone()[0])
db.close()
h={{'User-Agent':'Mozilla/5.0','Cookie':config['cookie']}}
sys.path.insert(0,"/app/backend")
from app.extensions.adapters.quark_adapter import QuarkAdapter
adapter=QuarkAdapter(cookie=config['cookie'],account_name='quark')

SHARE={sid_safe}; TMDB={tmdb_safe}; SHOW={show_safe}; SE={season}; CLEAN={str(clean)}; PWD={pwd_safe}; ANCHOR={anchor_safe}

# ── Step 0: Cookie 健康检查 ──
try:
    acct=adapter.get_account_info()
    if not acct:print("COOKIE_EXPIRED: 夸克Cookie已过期，请重新在CASX容器更新cookie");sys.exit(1)
except Exception as e:
    print("COOKIE_CHECK_FAIL: "+str(e)[:60]);sys.exit(1)

PARENT=\"{parent_fid}\"
DIR_NAME=SHOW+" [tmdbid="+TMDB+"]"

# ── Step 1: 删除/创建目录 ──
if CLEAN:
    r=requests.get(BASE+"/1/clouddrive/file/sort",
        params={{'pr':'ucpro','fr':'pc','pdir_fid':PARENT,'page':'1','size':'50'}},headers=h,timeout=10)
    for x in r.json()['data']['list']:
        if ("tmdbid="+TMDB+"]") in x.get('file_name',''):
            requests.post(BASE+"/1/clouddrive/file/delete",
                params={{'pr':'ucpro','fr':'pc'}},
                json={{'action_type':2,'filelist':[x['fid']]}},headers=h,timeout=10)
            time.sleep(1)
            break

r=requests.post(BASE+"/1/clouddrive/file",
    params={{'pr':'ucpro','fr':'pc','method_name':'file.create'}},
    json={{'pdir_fid':PARENT,'file_name':DIR_NAME,'dir_init_lock':False,'file_type':0}},
    headers=h,timeout=15)
d=r.json()
if d.get('status')==200:
    TF=d['data']['fid']
elif d.get('code')==23008:
    r2=requests.get(BASE+"/1/clouddrive/file/sort",
        params={{'pr':'ucpro','fr':'pc','pdir_fid':PARENT,'page':'1','size':'50'}},headers=h,timeout=10)
    TF=None
    for x in r2.json()['data']['list']:
        if ("tmdbid="+TMDB+"]") in x.get('file_name',''):
            TF=x['fid'];break
    if not TF:print("FAIL_MKDIR");sys.exit(1)
else:
    print("MKDIR_FAIL code="+str(d.get('code')));sys.exit(1)

# ── Step 2: 获取 stoken → 列表分享 → 立即保存 (不能有延迟!) ──
stok=adapter.get_stoken(SHARE)['data']['stoken']

def ls(pdir='0'):
    p={{'pr':'ucpro','fr':'pc','pwd_id':SHARE,'stoken':stok,'pdir_fid':pdir,'_page':'1','_size':'200'}}
    r=requests.get(BASE+'/1/clouddrive/share/sharepage/detail',params=p,headers=h,timeout=20)
    return r.json().get('data',{{}}).get('list',[])

root=ls('0')
rf='0'
if ANCHOR:
    # 下钻到 anchor 路径 (如 "xxx/HDR")
    parts=[p for p in ANCHOR.strip('/').split('/') if p]
    cur='0'
    for part in parts:
        found=False
        for x in ls(cur):
            if x.get('dir') and x.get('file_name','')==part:
                cur=x['fid'];found=True;break
        if not found:print("ANCHOR_NOT_FOUND:"+part);sys.exit(1)
    rf=cur
else:
    for x in root:
        if x.get('dir'):rf=x['fid'];break

# Collect files recursively from any nesting depth
def collect_files(fid,depth=0):
    if depth>8:return [],[]
    items=ls(fid)
    dirs=[x for x in items if x.get('dir')]
    vids=[x for x in items if not x.get('dir')]
    # Filter to target season if possible
    if vids:return vids,[]
    # Recurse into subdirs
    all_vids=[]
    for d in dirs:
        dv,dd=collect_files(d['fid'],depth+1)
        all_vids.extend(dv)
    return all_vids,[]

vids, _ = collect_files(rf)
fids=[x['fid'] for x in vids]
tokens=[x.get('share_fid_token','') for x in vids]

# 原生 POST 保存 (不用 adapter.save_file — 它 pdir_fid 硬编码 "0")
save_body={{'fid_list':fids,'fid_token_list':tokens,'to_pdir_fid':TF,'pwd_id':SHARE,'stoken':stok,'pdir_fid':rf,'scene':'link'}}
if PWD: save_body['pwd']=PWD
r=requests.post(BASE+"/1/clouddrive/share/sharepage/save",
    params={{'pr':'ucpro','fr':'pc','app':'clouddrive'}},
    json=save_body,headers=h,timeout=60)
d=r.json()
if d.get('code')!=0:
    # 41013/41020 = token expired → retry once with fresh stoken
    if d.get('code') in (41013, 41020):
        time.sleep(2)
        stok2=adapter.get_stoken(SHARE)['data']['stoken']
        save_body['stoken']=stok2
        refresh_body={{'fid_list':fids,'fid_token_list':tokens,'to_pdir_fid':TF,'pwd_id':SHARE,'stoken':stok2,'pdir_fid':rf,'scene':'link'}}
        if PWD: refresh_body['pwd']=PWD
        r=requests.post(BASE+\"/1/clouddrive/share/sharepage/save\",
            params={{'pr':'ucpro','fr':'pc','app':'clouddrive'}},
            json=refresh_body,headers=h,timeout=60)
        d=r.json()
        if d.get('code')!=0:
            print(\"SAVE_FAIL code=\"+str(d.get('code'))+\" \"+str(d.get('message',''))[:60])
            sys.exit(1)
    else:
        print(\"SAVE_FAIL code=\"+str(d.get('code'))+\" \"+str(d.get('message',''))[:60])
        sys.exit(1)

# ── Step 3: Task 验证实际保存数量 (不信任 file/sort!) ──
task_id=(d.get('data',{{}}) or {{}}).get('task_id','')
saved=len(fids)
if task_id:
    for _ in range(20):
        time.sleep(3)
        r2=requests.get(BASE+"/1/clouddrive/task",
            params={{'pr':'ucpro','fr':'pc','task_id':task_id,'retry_index':'0'}},headers=h,timeout=15)
        td=r2.json()
        if (td.get('data',{{}}) or {{}}).get('status')==2:
            saved=len((td.get('data',{{}}) or {{}}).get('save_as',{{}}).get('save_as_top_fids',[]))
            break
time.sleep(10)

# ── Step 4: 重命名 + 去重 — 只处理当前季文件! ──
time.sleep(3)
# ★ 用 adapter.ls_dir 替代 file/sort (file/sort 分页不可靠, size参数无效, 只返回部分)
items=(adapter.ls_dir(TF).get('data',{{}}) or {{}}).get('list',[]) or []
if not items:
    # 兜底: file/sort 多页去重
    items=[];seen=set()
    for page in range(1,15):
        r=requests.get(BASE+"/1/clouddrive/file/sort",
            params={{'pr':'ucpro','fr':'pc','pdir_fid':TF,'page':str(page),'size':'200'}},headers=h,timeout=10)
        batch=r.json()['data']['list']
        if not batch:break
        new=[x for x in batch if x['fid'] not in seen]
        if not new:break
        for x in new:seen.add(x['fid'])
        items.extend(new)

# 第一遍: 收集已有文件名 (用于冲突检测)
existing_names=set(x['file_name'] for x in items)

# 第二遍: 重命名/去重
renamed=0;deleted_dup=0
for x in items:
    if x.get('dir') or (\"tmdbid="+TMDB) in x.get('file_name',''):continue
    old=x['file_name'];fid=x['fid'];ep=None;s=SE
    em=re.search(r\"[Ss](\\d{{1,2}})\\s*[Ee](\\d{{1,3}})\",old)
    if em:s=int(em.group(1));ep=int(em.group(2))
    else:
        em=re.search(r\"[Ee](\\d{{1,3}})\",old)
        if em:ep=int(em.group(1))
        else:
            em=re.search(r\"(\\d{{2,3}})\\.\\w+$\",old)
            if em:ep=int(em.group(1))
    if ep is None:
        ext=old.rsplit('.',1)[-1] if '.' in old else 'mp4'
        new=SHOW+\" [tmdbid="+TMDB+\"].\"+ext
        if new!=old:
            rr=requests.post(BASE+\"/1/clouddrive/file/rename\",params={{'pr':'ucpro','fr':'pc'}},json={{'fid':fid,'file_name':new}},headers=h,timeout=15)
            if rr.json().get('code')==0:renamed+=1
        continue
    # ★ 只重命名当前季! 防止跨季覆盖
    if s != SE:continue
    ext=old.rsplit('.',1)[-1] if '.' in old else 'mp4'
    new=SHOW+\" [tmdbid="+TMDB+\"].S{{0:0>2d}}E{{1:0>2d}}.{{2}}".format(s,ep,ext)
    if new==old:continue

    # 检查目标名是否已存在 (冲突)
    if new in existing_names:
        # 这是重复文件 → 删除
        rd=requests.post(BASE+\"/1/clouddrive/file/delete\",
            params={{'pr':'ucpro','fr':'pc'}},
            json={{'action_type':2,'filelist':[fid]}},headers=h,timeout=15)
        if rd.json().get('code')==0:
            deleted_dup+=1
        continue

    # 执行重命名
    rr=requests.post(BASE+\"/1/clouddrive/file/rename\",
        params={{'pr':'ucpro','fr':'pc'}},
        json={{'fid':fid,'file_name':new}},headers=h,timeout=15)
    rc=rr.json().get('code',-1)
    if rc==0:
        existing_names.add(new)  # 更新已知名称池
        renamed+=1
    elif rc==23008:
        # 目标名已存在 (可能并发或已有) → 删除源文件
        rd=requests.post(BASE+\"/1/clouddrive/file/delete\",
            params={{'pr':'ucpro','fr':'pc'}},
            json={{'action_type':2,'filelist':[fid]}},headers=h,timeout=15)
        if rd.json().get('code')==0:
            deleted_dup+=1

# 第三遍: 再次删除任何残留的 (1) 后缀文件 (兜底)
time.sleep(2)
r=requests.get(BASE+\"/1/clouddrive/file/sort\",
    params={{'pr':'ucpro','fr':'pc','pdir_fid':TF,'page':'1','size':'200'}},headers=h,timeout=10)
for x in r.json()['data']['list']:
    nm = x.get('file_name','')
    if not x.get('dir') and chr(40) in nm and 'tmdbid=' not in nm:
        rd=requests.post(BASE+\"/1/clouddrive/file/delete\",
            params={{'pr':'ucpro','fr':'pc'}},
            json={{'action_type':2,'filelist':[x['fid']]}},headers=h,timeout=15)
        if rd.json().get('code')==0:
            deleted_dup+=1

if renamed or deleted_dup:
    print(\"RENAME: \"+str(renamed)+\" files, \"+str(deleted_dup)+\" dups deleted\")

# ── Step 5: 最终统计 + 画质检测 ──
time.sleep(2)
d5=adapter.ls_dir(TF)
v2=[x for x in ((d5.get('data',{{}}) or {{}}).get('list',[]) or []) if not x.get('dir') and x.get('category')!=0]
sz=round(sum(x['size'] for x in v2)/1073741824,1)

quality_map={{3840:'4K',1920:'1080p',1280:'720p',7680:'8K'}}
detected_q=''
for x in v2:
    vw=x.get('video_width',0)
    dq=quality_map.get(vw,'')
    if dq and (not detected_q or quality_map.get(dq,0)>quality_map.get(detected_q,0)):
        detected_q=dq
    # Also check max_resolution for Dolby Vision etc
    mr=x.get('video_max_resolution','')
    if mr=='super': detected_q='HQ' if not detected_q else detected_q

print('IMPORTED|'+str(len(v2))+'|'+str(sz)+'|'+detected_q+'|'+str(len(v2))+'|'+str(saved)+'/'+str(len(fids)))
"""
    return exec_casx(script, 180)


# ═══════════════════════════════════════════════════════════════
#  飞书同步
# ═══════════════════════════════════════════════════════════════
def feishu_sync(name, tmdb, ep_str, status, size_gb, fc, quality, year, genres, table="tv"):
    """通过 feishu_bitable.py 同步一条记录到指定表"""
    import subprocess
    args = [
        "python3", "/workspace/feishu_bitable.py", "sync",
        table,                          # ← 关键: tv/anime/movie
        name, str(tmdb), ep_str, status,
        str(size_gb), str(fc), str(quality), str(year or ""),
    ] + (genres if genres else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=15)
    print(r.stdout.strip())
    return r.returncode == 0


# ═══════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="影视资源全自动入库管线 v2")
    p.add_argument("url", help="夸克分享链接")
    p.add_argument("--type", "-t", choices=["anime","tv","movie"], default=None,
                   help="目标分类 (anime/tv/movie), 默认自动推断")
    p.add_argument("--season", "-s", type=int, default=1, help="季数 (默认1)")
    p.add_argument("--clean", action="store_true", default=True, help="删除旧目录重建 (默认)")
    p.add_argument("--no-clean", dest="clean", action="store_false", help="保留已有文件追加")
    p.add_argument("--password", type=str, default="", help="分享密码 (如有)")
    args = p.parse_args()

    url = args.url
    m = re.search(r'/s/([a-f0-9]+)', url)
    sid = m.group(1) if m else url

    # 解析 anchor (分享内子目录锚点 #/list/share/xxx)
    anchor = ""
    m2 = re.search(r'#/list/share(/.*)', url)
    if m2 and m2.group(1):
        import urllib.parse
        anchor = urllib.parse.unquote(m2.group(1)).lstrip("/")

    print(f"📎 分享: {sid}" + (f" → 锚点: {anchor}" if anchor else ""))

    # ① 列出分享内容
    print("  📂 解析分享...")
    raw = list_share(url)
    if not raw.strip():
        print("  ❌ 分享为空"); sys.exit(1)

    a = analyze(raw)
    if not a:
        print("  ❌ 无法解析"); sys.exit(1)

    # anchor 模式: 用 anchor 第一段作为标题 (避免取到 HDR/DV/SDR 等目录名)
    if anchor:
        anchor_title = anchor.split("/")[0]
        t = re.sub(r'[（(][^）)]*[）)]', '', anchor_title)
        t = re.sub(r'\[[^\]]*\]', '', t)
        t = re.sub(r'(?i)\b(4k|2160p|1080p|720p|hdr|dv|h\.?265|hevc|x265|x264|bluray|web-dl|webrip|dubbed|subbed|completed|完结|remux)\b', '', t)
        t = re.sub(r'(?i)(4k|2160p|1080p|720p)', '', t)
        t = re.sub(r'(?:蓝光|杜比视界|杜比全景声|杜比音效|杜比|全景声|高码率|高码|内封[简繁英中文字幕]+|内嵌[简繁英中文字幕]+|双语|中文字幕|原盘|REMUX)', '', t, flags=re.IGNORECASE)
        t = re.sub(r'^[A-Za-z]\s*', '', t)
        t = re.sub(r'\s+', ' ', t).strip().rstrip("._- ")
        if t:
            a["best_title"] = t

    size_gb = a["total_size"] / 1073741824
    q_str = ", ".join(a["qualities"]) if a["qualities"] else "?"
    eps = a["episodes"]
    seasons = a["seasons"]
    ep_str = ""
    if seasons and eps:
        ep_str = f"S{seasons[0]:02d}E{eps[0]:02d}"
        if len(eps) > 1: ep_str += f"~E{eps[-1]:02d}"

    samples = [n[:60] for n in a["all_names"][:3]]
    print(f"  📺 {a['best_title']}  ·  {ep_str}  ·  {size_gb:.1f}GB  ·  {q_str}")
    print(f"  📄 {' · '.join(samples)}")

    # ② TMDB 搜索
    print(f"  🔍 TMDB搜索: {a['best_title']}...")
    results = tmdb_search(a["best_title"])

    if not results:
        alt = a["best_title"].lower().replace("completed","").replace("web-dl","").strip().rstrip("._- ")
        results = tmdb_search(alt)

    if not results:
        print("  ❌ TMDB无匹配，跳过"); sys.exit(1)

    selected = select_best_tmdb(results, a)
    if not selected: selected = results[0]

    for i, r in enumerate(results):
        mark = "←" if r == selected else " "
        print(f"  [{mark}] {r.get('name','?')} ({r.get('year','?')}) tmdb={r['id']}")

    tmdb_id = str(selected["id"])
    tmdb_name = selected.get("name", "")

    # ②.5 SearXNG 交叉验证 (防九龙拉棺→遮天 类误匹配)
    # 触发条件: TMDB名含之XX后缀 或 搜索结果仅1条且TMDB名扩展了原标题
    suspicious = is_suspicious_tmdb_match(tmdb_name)
    single_extended = (len(results) == 1 and 
                       a["best_title"].lower() in tmdb_name.lower() and
                       a["best_title"].lower() != tmdb_name.lower())
    if suspicious or single_extended:
        print(f"  🔎 SearXNG交叉验证: {a['best_title']}...")
        verified_name, verified_id, verified_result = searxng_cross_verify(a["best_title"], tmdb_name)
        if verified_id and verified_id != tmdb_id:
            print(f"  ⚠️ 疑似子章节/arc! TMDB={tmdb_name}({tmdb_id}) → 实际作品: {verified_name}({verified_id})")
            print(f"  🔄 切换至 {verified_name} (tmdb={verified_id})")
            tmdb_id = verified_id
            tmdb_name = verified_name
            # 直接用交叉验证返回的结果，不再二次搜索
            if verified_result:
                selected = verified_result
        elif not verified_id:
            print(f"  ✅ 未发现更优匹配，保持 {tmdb_name}")

    # ③ 确定目标表/目录
    if args.type:
        target_subdir = {"anime":"动漫","tv":"剧集","movie":"电影"}[args.type]
    else:
        # 自动推断: movie → 电影, 其余 → 剧集
        if selected.get("type") == "movie":
            target_subdir = "电影"
        else:
            target_subdir = "剧集"  # 默认剧集，手动 --type anime 覆盖
    feishu_table = TABLE_MAP[target_subdir]

    # ④ 画质检查/升级 (2026-08-12 新标准: 按公网可播码率, 不再无脑追4K REMUX)
    final_sid = sid
    upgraded = False
    final_quality = q_str

    is_movie = (a["media_type"] == "movie")
    ep_count = max(len(eps), 1)
    fits, fit_reason = quality_fits_public(a["qualities"], a["total_size"], ep_count, is_movie)

    if not fits:
        print(f"  ⚠️ 当前源不达标: {fit_reason}")
        print(f"  🔍 搜索公网可播替代版...")
        candidates = search_better_quality(a["best_title"], a["qualities"])
        if candidates:
            quark_candidates = [c for c in candidates if 'quark' in c['url']]
            print(f"  🎯 找到 {len(quark_candidates)} 个候选版")
            for i, c in enumerate(quark_candidates[:5]):
                name = c['title'][:80]
                tags = []
                nl = name.lower()
                for t in ['4K','4k','HDR','DV','蓝光','REMUX','高码率','臻彩','WEB-DL','1080p']:
                    if t.lower() in nl: tags.append(t)
                tag_str = ' '.join(tags) if tags else '?'
                print(f"     [{i+1}] {tag_str:16} {name}")

            better_sid, better_name = pick_best_upgrade(candidates, url, a, args.season)
            if better_sid:
                final_sid = better_sid
                upgraded = True
                # 候选版画质标签 (新标准: 不一定是4K, 用名字里的实际标签)
                nl = better_name.lower()
                if '4k' in nl or '2160p' in nl: cand_q = "4K"
                elif '1080p' in nl: cand_q = "1080p"
                else: cand_q = "WEB-DL"
                if 'hdr' in nl: cand_q += " HDR"
                if re.search(r'(?<![a-z])dv(?![a-z])', nl) and 'dvd' not in nl: cand_q += " DV"
                if 'web-dl' in nl or 'webdl' in nl: cand_q += " WEB-DL"
                final_quality = cand_q
                print(f"  💡 已选: {better_name[:60]} → {cand_q}")
            else:
                print(f"  ℹ️ 无可用替代, 保留原源入库 (超公网标准, 公网可能卡顿)")
        else:
            print(f"  ℹ️ 未找到替代版, 保留原源入库 (超公网标准, 公网可能卡顿)")
    else:
        print(f"  ✅ 画质达标: {fit_reason}")

    # ⑤ 入库
    print(f"  📥 入库中 (→ {target_subdir}, S{args.season:02d}, clean={args.clean})...")
    pwd = args.password or ""
    result = do_import(final_sid, tmdb_id, tmdb_name, target_subdir, args.season, args.clean, pwd, anchor)

    # ★ 从 exec 输出中提取 IMPORTED 行 (可能被 RENAME/其他日志行干扰)
    imported_line = ""
    for line in result.strip().split("\n"):
        if line.startswith("IMPORTED|"):
            imported_line = line
            break
    parts = imported_line.split("|") if imported_line else []
    if len(parts) >= 5 and parts[0] == "IMPORTED":
        fc = int(parts[1])
        sz = float(parts[2])
        detected_q = parts[3]

        # 用检测画质覆盖
        if detected_q and detected_q not in final_quality:
            if final_quality == "?":
                final_quality = detected_q
            else:
                final_quality = f"{final_quality} ({detected_q})"

        # ⑥ 飞书同步
        status = "🔄追更中" if (len(eps) < 20) else "✅已入库"
        year = selected.get("year", "")
        genres = []

        upgrade_flag = " ⬆️" if upgraded else ""
        feishu_sync(tmdb_name, tmdb_id, ep_str, status, sz, fc, final_quality, year, genres, table=feishu_table)
        print(f"  ✅ 完成: {tmdb_name} · {ep_str} · {sz}GB · {final_quality}{upgrade_flag}  ·  {status}")
    else:
        print(f"  ❌ 入库失败: {result[:200]}")
