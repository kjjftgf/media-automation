#!/usr/bin/env python3
"""
追更脚本 — 自动检测追更剧集的新集并入库
用法: python3 catchup.py            # 检查所有追更中剧集
      python3 catchup.py --dry-run  # 仅搜索，不入库
      python3 catchup.py --show 怪兽8号  # 只检查指定剧集

流程: 飞书查追更中 → xiaokupan.com搜新集 → 比较已有 → auto_import增量导入
适合 cron 定期执行: 每6-12小时
"""
import json, re, sys, time, subprocess, urllib.request, http.client
from collections import defaultdict
from datetime import datetime

# ── 配置 ────────────────────────────────────────────────────────
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")

TABLES = {
    "tv":    ("tblCQbsK6FBkGIY9",    "📺 名字"),
    "anime": ("tblvXnrGj0Al03HL",   "🎞️ 名字"),
    "movie": ("tblhSzGZwQ4SOBUe",   "🎬 名字"),
}

# 🔄追更中 option IDs — 每表不同！
STATUS_OPT_IDS = {
    "tv":    "optVZqcuvs",
    "anime": "optjAvUVGO",
    "movie": "optHSJi1Hj",
}


# ── 飞书 API ───────────────────────────────────────────────────
def _feishu_get(path):
    """飞书 GET 请求"""
    # Get token
    data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=data, headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req, timeout=15).read())["tenant_access_token"]

    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis{path}",
        headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def get_watching_shows():
    """从飞书三表拉取所有追更中的剧集"""
    shows = []
    for table_key, (table_id, name_field) in TABLES.items():
        chase_opt = STATUS_OPT_IDS[table_key]  # 每表独立ID
        # 拉取所有记录 (最多100条)
        d = _feishu_get(
            f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records?page_size=100")
        for item in d.get("data", {}).get("items", []):
            fields = item.get("fields", {})
            status = fields.get("状态", "")
            if status != chase_opt:
                continue

            name = fields.get(name_field, "")
            tmdb = fields.get("TMDB ID", "")
            ep_str = fields.get("集数", "")
            quality = fields.get("质量", "")

            # 解析当前最大集数
            max_ep = 0
            season = 1
            # 格式: "S02E00~E11" / "S03E001~S03E012" / "S03E01~E04"
            ep_str_val = str(ep_str)
            # 先提取所有 SxxExx 匹配
            ep_matches = re.findall(r'[Ss](\d+)\s*[Ee](\d+)', ep_str_val)
            if ep_matches:
                season = int(ep_matches[0][0])
                for s, e in ep_matches:
                    max_ep = max(max_ep, int(e))
            # 处理 ~Exx 简写格式
            range_matches = re.findall(r'~[Ee](\d+)', ep_str_val)
            for e in range_matches:
                max_ep = max(max_ep, int(e))

            if name and tmdb:
                shows.append({
                    "name": name,
                    "tmdb": str(tmdb),
                    "type": table_key,
                    "max_ep": max_ep,
                    "season": season,
                    "quality": quality or "",
                })

        # Paginate if needed
        while d.get("data", {}).get("has_more"):
            pt = d["data"].get("page_token", "")
            d = _feishu_get(
                f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records"
                f"?page_size=100&page_token={pt}")
            for item in d.get("data", {}).get("items", []):
                fields = item.get("fields", {})
                if fields.get("状态", "") == chase_opt:
                    name = fields.get(name_field, "")
                    tmdb = fields.get("TMDB ID", "")
                    if name and tmdb:
                        shows.append({
                            "name": name, "tmdb": str(tmdb),
                            "type": table_key, "max_ep": 0, "season": 1,
                            "quality": fields.get("质量", ""),
                        })

    return shows


# ── 搜索新集 ───────────────────────────────────────────────────
def _num_to_cn(n):
    """Convert integer to Chinese number (1-10)"""
    return ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'][n-1] if 1 <= n <= 10 else str(n)


def _fetch_xiaokupan(query):
    """底层HTTP请求: 搜索 xiaokupan.com，返回原始HTML"""
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        q = urllib.parse.quote(query)
        url = f"https://xiaokupan.com/s/{q}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0"})
        return urllib.request.urlopen(req, context=ctx, timeout=15).read().decode('utf-8', errors='ignore')
    except Exception:
        return ""


def _parse_raw_results(html):
    """从HTML中提取搜索结果: {url, note, datetime, source}"""
    if not html:
        return []
    # JavaScript object format:
    # {url:"https://pan.quark.cn/s/XXX",password:"...",note:"TITLE",datetime:"DATE",source:"SRC"}
    pattern = r'\{url:"(https://pan\.quark\.cn/s/[a-f0-9]+)",password:"[^"]*",note:"([^"]*)",datetime:"([^"]*)",source:"([^"]*)"'
    matches = re.findall(pattern, html)
    results = []
    for link, note, dt, src in matches:
        results.append({
            "url": link,
            "note": note[:200],
            "datetime": dt[:19],
            "source": src,
            "score": 0,
            "episodes": [],
            "season": None,
            "quality": None,
        })
    return results


def _build_query(title, season=None):
    """构建搜索query: 精确名 + 可选季数后缀"""
    query = title
    if season and season > 1:
        query += f" S{season:02d}"
    return query


def _extract_short_name(title):
    """提取短名称: 去掉季数后缀、常见前缀，取核心名"""
    # 去掉"第二季"、"S02"等季数后缀
    cleaned = re.sub(r'[第][一二三四五六七八九十\d]+[季部]', '', title).strip()
    cleaned = re.sub(r'[Ss]\d+', '', cleaned).strip()
    # 去掉常见前缀词
    cleaned = re.sub(r'^(剧集版|电影版|动画版)', '', cleaned).strip()
    return cleaned if len(cleaned) >= 2 else title


def _extract_keywords(title):
    """提取核心关键词: 中文段 + 英文token"""
    cleaned = re.sub(r'[第][一二三四五六七八九十\d]+[季部]', '', title)
    cleaned = re.sub(r'[Ss]\d+', '', cleaned).strip()
    cleaned = re.sub(r'^(剧集版|电影版|动画版)', '', cleaned).strip()
    keywords = []
    # 中文段 (>=2字)
    for m in re.finditer(r'[\u4e00-\u9fff]{2,}', cleaned):
        seg = m.group()
        keywords.append(seg)
        if len(seg) >= 4:
            # 长名取后半段: "葬送的芙莉莲" → "芙莉莲"
            keywords.append(seg[-(len(seg)//2 + len(seg)%2):])
    # 英文token (>=3字符)
    for m in re.finditer(r'[a-zA-Z]{3,}', cleaned):
        keywords.append(m.group().lower())
    # 去重
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result[:5]


def _score_note_relevance(note, title, season=None):
    """名称相关性评分 (0-100)"""
    score = 0
    note_lower = note.lower()
    title_lower = title.lower()

    # 1) 子串匹配 (0-40)
    if title_lower.replace(" ", "") in note_lower.replace(" ", ""):
        score += 40
    else:
        title_words = [w for w in title_lower.split() if len(w) > 1]
        if title_words:
            matched = sum(1 for w in title_words if w in note_lower)
            score += min(30, matched * 10)

    # 2) Token重叠 (0-30) - character bigram Jaccard
    title_bigrams = set(title[i:i+2] for i in range(len(title)-1))
    note_bigrams = set(note[i:i+2] for i in range(len(note)-1))
    if title_bigrams:
        jaccard = len(title_bigrams & note_bigrams) / max(1, len(title_bigrams | note_bigrams))
        score += int(jaccard * 30)

    # 3) 季数匹配 (0-15)
    if season and season > 1:
        s_tag = f's{season:02d}'
        s_cn = f'第{_num_to_cn(season)}季'
        if s_tag in note_lower or s_cn in note:
            score += 15
        # 检查是否提到其他季 (负面)
        for s in range(1, 10):
            if s != season and (f's{s:02d}' in note_lower or f'第{_num_to_cn(s)}季' in note):
                if abs(s - season) > 1:
                    score -= 10
                    break
    else:
        # season=1: 检查是否明确提到第2季+ (负面)
        if re.search(r'[Ss]0[2-9]|第[二三四五六七八九十\d]季', note):
            score -= 15

    # 4) 明确排除信号
    exclude_patterns = [r'OST', r'原声', r'OP\b', r'ED\b', r'NCOP', r'NCED']
    for pat in exclude_patterns:
        if re.search(pat, note, re.IGNORECASE):
            score -= 10

    return max(0, score)


def _score_search_result(result, title, season=None, max_ep=0):
    """对单个搜索结果综合评分 (0-100)"""
    score = 0
    note = result.get('note', '')

    # 1) 相关性 (0-40)
    rel_score = _score_note_relevance(note, title, season)
    score += rel_score

    # 2) 集数信号 (0-25)
    text_for_eps = note + " " + result.get('content', result.get('datetime', ''))
    eps = parse_episodes_from_text(text_for_eps)
    result['episodes'] = eps
    if eps:
        new_eps = [e for e in eps if e > max_ep] if max_ep else eps
        if new_eps:
            score += min(25, len(new_eps) * 5)
            score += min(10, max(new_eps) * 0.5)

    # 3) 时效性 (0-20)
    dt = result.get('datetime', '')
    if dt:
        try:
            parsed = datetime.strptime(dt[:19], '%Y-%m-%d %H:%M:%S')
            days_ago = (datetime.now() - parsed).days
            if days_ago <= 1:
                score += 20
            elif days_ago <= 3:
                score += 15
            elif days_ago <= 7:
                score += 10
            elif days_ago <= 14:
                score += 5
        except Exception:
            pass

    # 4) 画质加分 (0-10)
    note_lower = note.lower()
    if '4k' in note_lower or '2160p' in note_lower:
        score += 10
    elif '1080p' in note_lower:
        score += 5

    # 5) 来源可信度 (0-5)
    trusted_sources = ['ANi', 'LoliHouse', 'ReinForce', 'VCB-Studio']
    for s in trusted_sources:
        if s.lower() in note_lower:
            score += 5
            break

    # 解析元数据
    meta = parse_note_metadata(note)
    result['season'] = meta['season']
    result['quality'] = meta['quality']

    return min(100, score)


def _sort_results(results, title, season=None, max_ep=0):
    """评分并排序结果"""
    scored = []
    for r in results:
        r['score'] = _score_search_result(r, title, season, max_ep)
        scored.append(r)

    scored.sort(key=lambda x: (-x['score'],
                                -max(x.get('episodes', []) or [0])))
    return scored


def _deduplicate(results):
    """URL去重: 相同URL只保留最高分"""
    seen = {}
    for r in results:
        url = r['url']
        if url not in seen or r['score'] > seen[url]['score']:
            seen[url] = r
    return list(seen.values())


def parse_note_metadata(note):
    """从note中提取元数据: 画质, 季数, 集数信号"""
    metadata = {
        'episodes': [],
        'quality': None,
        'season': None,
        'has_episode_signal': False,
    }
    # 画质
    qual_map = {'4k': '4K', '2160p': '4K', '1080p': '1080P',
                '720p': '720P', 'hdr': 'HDR', 'dv': 'DV'}
    for key, val in qual_map.items():
        if key in note.lower():
            metadata['quality'] = val
            break
    # 季数
    s_match = re.search(r'[Ss](\d+)', note)
    if s_match:
        metadata['season'] = int(s_match.group(1))
    # 集数信号
    metadata['has_episode_signal'] = bool(
        re.search(r'更至|更新|E\d+|第.*[集话期]|\d{2,3}\s+\d{2,3}', note)
    )
    metadata['episodes'] = parse_episodes_from_text(note)
    return metadata


def search_xiaokupan(title, season=None, max_ep=0):
    """
    增强搜索: 三轮退避 + 评分排序 + 集数感知
    Returns: sorted list of dict with keys:
        url, note, datetime, source, score, episodes[], season, quality
    """
    all_results = []

    # ── Round 1: 精确名搜索 ──
    query = _build_query(title, season)
    raw = _fetch_xiaokupan(query)
    results1 = _parse_raw_results(raw)
    scored1 = _sort_results(results1, title, season, max_ep)
    all_results.extend(scored1)

    good = [r for r in scored1 if r['score'] >= 60]
    if len(good) >= 1:
        return _deduplicate(good)

    # ── Round 2: 短名搜索 ──
    short = _extract_short_name(title)
    if short and short != title:
        query2 = _build_query(short, season)
        raw2 = _fetch_xiaokupan(query2)
        results2 = _parse_raw_results(raw2)
        results2 = [r for r in results2 if r['url'] not in {x['url'] for x in all_results}]
        scored2 = _sort_results(results2, title, season, max_ep)
        all_results.extend(scored2)

        good = [r for r in all_results if r['score'] >= 40]
        if len(good) >= 1:
            return _deduplicate(good)

    # ── Round 3: 关键词搜索 ──
    keywords = _extract_keywords(title)
    if keywords:
        for kw in keywords[:2]:
            raw3 = _fetch_xiaokupan(kw)
            results3 = _parse_raw_results(raw3)
            results3 = [r for r in results3 if r['url'] not in {x['url'] for x in all_results}]
            scored3 = _sort_results(results3, title, season, max_ep)
            all_results.extend(scored3)

    good = [r for r in all_results if r['score'] >= 25]
    return _deduplicate(good) if good else []


def search_cloudsaver(title, season=None):
    """搜索 CloudSaver 找新分享链接 (API v2: data[0].list)"""
    try:
        conn = http.client.HTTPConnection(os.environ.get("CLOUDSAVER_HOST", "127.0.0.1"), int(os.environ.get("CLOUDSAVER_PORT", "8008")), timeout=15)
        body = json.dumps({"username": "admin", "password": os.environ.get("CLOUDSAVER_ADMIN_CODE", "")}).encode()
        conn.request("POST", "/api/user/login", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        token = json.loads(resp.read()).get("data", {}).get("token", "")
        if not token:
            conn.close()
            return []

        query = title
        if season and season > 1:
            query += f" S{season:02d}"
        q = urllib.parse.quote(query)
        conn.request("GET", f"/api/search?q={q}&page=1&pageSize=5",
                     headers={"Authorization": f"Bearer {token}"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()

        results = []
        # New API: data is [{"list": [...]}]
        raw_data = data.get("data", [])
        items = []
        if isinstance(raw_data, list):
            for chunk in raw_data:
                if isinstance(chunk, dict):
                    items.extend(chunk.get("list", []))
        elif isinstance(raw_data, dict):
            items = raw_data.get("list", [])

        for item in items:
            content = item.get("content", "") or ""
            cloud_links = item.get("cloudLinks", [])
            for cl in cloud_links:
                link = cl.get("link", "")
                if "pan.quark.cn" in link:
                    results.append({
                        "title": item.get("title", title),
                        "url": link,
                        "content": content[:200],
                    })
        return results
    except Exception:
        return []


def parse_episodes_from_text(text):
    """修复版集数提取 (2026-08-06): 只认明确集数信号, 拒绝裸数字
    修复假阳性: 旧版模式6/7会提取note中所有裸数字(年份/大小), 如"更16【陈伟霆 曾舜晞】(老九门2)"→[16,35,37]"""
    eps = set()

    # 1) 标准 SxxExx / SxxEyy-Ezz
    for m in re.finditer(r'[Ss](\d+)\s*[Ee](\d+)', text):
        eps.add(int(m.group(2)))

    # 2) Exx (注意排除 Example, Extra 等单词)
    for m in re.finditer(r'(?<![a-zA-Z])[Ee](\d+)(?![a-zA-Z])', text):
        eps.add(int(m.group(1)))

    # 3) 第XX集 / 第XX话 / 第XX期
    for m in re.finditer(r'第\s*(\d+)\s*(?:集|话|期)', text):
        eps.add(int(m.group(1)))

    # 4) 更至XX集 / 更新到XX集 / 至XX集
    for m in re.finditer(r'(?:更至|更新到|更新至|更到|至|更|更新)\s*(\d{1,3})\s*集', text):
        eps.add(int(m.group(1)))

    # 5) XX集 / XX话 (裸数字+集/话)
    for m in re.finditer(r'(\d+)\s*(?:集|话)', text):
        n = int(m.group(1))
        if 1 <= n <= 999:
            eps.add(n)

    # 6) 更XX (无"集"后缀, 如 "九门.更16" / "更新至16") — 精确跟随关键词, 拒绝其他裸数字
    for m in re.finditer(r'(?:更至|更新至|更新到|更到|更|更新)\s*(\d{1,3})(?![.\d])', text):
        n = int(m.group(1))
        if 1 <= n <= 500:
            eps.add(n)

    # 7) 集数区间 "E01-E12" (第二个数字后不能跟数字, 防 E791-260701 日期误匹配)
    for m in re.finditer(r'[Ee](\d{1,3})\s*[-~]\s*[Ee]?(\d{1,3})(?![.\d])', text):
        eps.add(int(m.group(1))); eps.add(int(m.group(2)))

    return sorted(eps)


# ── PanSou 搜索 ─────────────────────────────────────────────────
PANSOU_BASE = os.environ.get("PANSOU_BASE", "http://127.0.0.1:8888")

def search_pansou(title, season=None, max_ep=0):
    """搜索本地 PanSou 容器 (ghcr.io/fish2018/pansou, 61 plugins, 101 channels)

    返回格式兼容 xiaokupan: [{url, note, datetime, source, score, episodes[], season}]
    """
    try:
        import ssl, time
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        q = urllib.parse.quote(title)
        url = f"{PANSOU_BASE}/api/search?kw={q}&res=all"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0"})
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        d = json.loads(resp.read())

        if d.get("code") != 0:
            return []

        data = d.get("data", {})
        merged = data.get("merged_by_type", {})
        quark_items = merged.get("quark", [])

        results = []
        for item in quark_items:
            note = item.get("note", "") or ""
            url_ = item.get("url", "") or ""
            dt = item.get("datetime", "") or ""
            src = item.get("source", "") or "pansou"

            # 名称相关性过滤
            score = _score_note_relevance(note, title, season)
            if score < 20:
                continue  # 太不相关跳过

            # 提取元数据
            eps = parse_episodes_from_text(note)
            s_num = None
            s_m = re.search(r'[Ss](\d+)', note)
            if s_m:
                s_num = int(s_m.group(1))

            results.append({
                "url": url_,
                "note": note[:500],
                "datetime": dt,
                "source": src,
                "score": score,
                "episodes": eps,
                "season": s_num,
            })

        # 评分排序 (复用 xiaokupan 的评分体系)
        results = _sort_results(results, title, season, max_ep)
        return results
    except Exception as e:
        sys.stderr.write(f"  ⚠️ PanSou搜索失败: {e}\n")
        return []


# ── 主流程 ──────────────────────────────────────────────────────
def _tmdb_season_total(tmdb_id, season):
    """TMDB 该季总集数 — 用于过滤假阳性 (老九门48集混入九门搜索等).
    返回 None 表示获取失败(不限制). 容差 +5: TMDB 国漫/国产剧集数可能滞后."""
    try:
        import sqlite3, ssl
        db = sqlite3.connect("/app/backend/data/app.db")
        cfg = json.loads(db.execute(
            "SELECT config_json FROM tmdb_settings WHERE id=1").fetchone()[0])
        key = cfg["api_key"]
        db.close()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={key}&language=zh-CN"
        req = urllib.request.Request(url)
        d = json.loads(urllib.request.urlopen(req, context=ctx, timeout=15).read())
        for s in d.get("seasons", []):
            if s.get("season_number") == season:
                return s.get("episode_count")
        return None
    except Exception:
        return None


def check_show(show, dry_run=False):
    """检查单个剧集是否有新集"""
    name = show["name"]
    max_ep = show["max_ep"]
    season = show["season"]
    # TMDB 季总集数上限 (容差+5), 过滤"老九门48集"类假阳性
    season_total = _tmdb_season_total(show["tmdb"], season)

    def _is_new(e):
        if e <= max_ep:
            return False
        if season_total is not None and e > season_total + 5:
            return False  # 超过TMDB总集数+容差 → 明显异常(串剧/旧剧), 拒绝
        return True

    # 搜索: xiaokupan (优先, 已评分排序) → PanSou → CloudSaver (备用)
    cloud_results = search_xiaokupan(name, season, max_ep)
    if cloud_results:
        # ⚠️ 遍历所有结果取最大新集 (2026-08-07 沧元图血案: 只取 results[0] 会漏 —
        # 裸标题结果 score 最高但 eps=[], 含新集链接排名稍低被跳过)
        best = None
        best_new_eps = 0
        for r in cloud_results:
            eps = r.get('episodes', [])
            if not eps:
                eps = parse_episodes_from_text(r['note'] + " " + r.get('datetime', ''))
            new_eps = [e for e in eps if _is_new(e)]
            if new_eps and len(new_eps) > best_new_eps:
                best_new_eps = len(new_eps)
                best = {"url": r["url"], "new_eps": new_eps, "title": r["note"][:200]}
        if best:
            new_eps = best["new_eps"]
            print(f"  🆕 {name}: {len(new_eps)} 新集 {new_eps[:5]}... @ {best['url'][:50]}")

            if dry_run:
                return {"show": show, "best": best, "imported": False}

            # 调用 auto_import 入库
            table_type = show["type"]
            cmd = [
                "python3", "/workspace/auto_import.py",
                best["url"],
                "--type", table_type,
                "--season", str(season),
                "--no-clean",
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                output = result.stdout + result.stderr
                print(f"  📥 {name}: {result.returncode}")
                for line in output.strip().split("\n"):
                    if "完成" in line or "失败" in line:
                        print(f"     {line.strip()[:100]}")
                return {"show": show, "best": best, "imported": result.returncode == 0, "output": output}
            except subprocess.TimeoutExpired:
                print(f"  ⏰ {name}: 超时")
                return {"show": show, "best": best, "imported": False, "error": "timeout"}
            except Exception as e:
                print(f"  ❌ {name}: {e}")
                return {"show": show, "best": best, "imported": False, "error": str(e)}

        # xiaokupan 有结果但无新集 → 明确无更新, 不进入 CloudSaver 处理循环
        # (xiaokupan dict 无 'title' 键, 掉进下方 for 循环会 KeyError)
        return None

    # xiaokupan无结果 → PanSou → CloudSaver
    if not cloud_results:
        cloud_results = search_pansou(name, season, max_ep)
    if not cloud_results:
        cloud_results = search_cloudsaver(name, season)
    if not cloud_results:
        return None

    # CloudSaver结果处理 (保持原有逻辑)
    best = None
    best_new_eps = 0
    for r in cloud_results:
        # ⚠️ PanSou/xiaokupan dict 无 'title' 键 (只有 note), CloudSaver 有 title+content
        text = r.get("title", "") + " " + r.get("content", "") + " " + r.get("note", "")
        eps = parse_episodes_from_text(text)
        new_eps = [e for e in eps if _is_new(e)]
        if len(new_eps) > best_new_eps:
            best_new_eps = len(new_eps)
            best = {"url": r["url"], "new_eps": new_eps,
                    "title": (r.get("title") or r.get("note", ""))[:200]}

    if best is None or best_new_eps == 0:
        return None

    print(f"  🆕 {name}: {best_new_eps} 新集 {best['new_eps'][:5]}... @ {best['url'][:50]}")

    if dry_run:
        return {"show": show, "best": best, "imported": False}

    table_type = show["type"]
    cmd = [
        "python3", "/workspace/auto_import.py",
        best["url"],
        "--type", table_type,
        "--season", str(season),
        "--no-clean",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        output = result.stdout + result.stderr
        print(f"  📥 {name}: {result.returncode}")
        for line in output.strip().split("\n"):
            if "完成" in line or "失败" in line:
                print(f"     {line.strip()[:100]}")
        return {"show": show, "best": best, "imported": result.returncode == 0, "output": output}
    except subprocess.TimeoutExpired:
        print(f"  ⏰ {name}: 超时")
        return {"show": show, "best": best, "imported": False, "error": "timeout"}
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        return {"show": show, "best": best, "imported": False, "error": str(e)}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="追更检测脚本")
    p.add_argument("--dry-run", action="store_true", help="仅搜索不导入")
    p.add_argument("--show", type=str, help="只检查指定剧集(名称关键词)")
    args = p.parse_args()

    print("📋 获取追更列表...")
    shows = get_watching_shows()
    print(f"  找到 {len(shows)} 部追更中剧集")

    if args.show:
        shows = [s for s in shows if args.show in s["name"]]
        if not shows:
            print(f"  ❌ 未找到 '{args.show}'")
            sys.exit(1)
        print(f"  过滤: {shows[0]['name']} (tmdb={shows[0]['tmdb']}, ep≤{shows[0]['max_ep']})")

    results = []
    for i, show in enumerate(shows):
        print(f"\n[{i+1}/{len(shows)}] {show['name']} "
              f"(tmdb={show['tmdb']}, season={show['season']}, ep≤{show['max_ep']})")
        try:
            r = check_show(show, dry_run=args.dry_run)
            if r:
                results.append(r)
        except Exception as e:
            print(f"  ❌ 异常: {e}")
        time.sleep(2)  # 限流

    if args.dry_run:
        total_new = sum(len(r["best"]["new_eps"]) for r in results)
        print(f"\n📊 DRY RUN: {len(results)} 部有新集, 共 {total_new} 新集")
    else:
        success = sum(1 for r in results if r.get("imported"))
        print(f"\n✅ 完成: {success}/{len(results)} 部导入成功")
