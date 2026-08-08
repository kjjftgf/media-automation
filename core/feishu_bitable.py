#!/usr/bin/env python3
"""飞书多维表格同步 - 三表版（📺剧集 / 🎞️动漫 / 🎬电影）"""
import os
import urllib.request, json, time

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")

# 三张表
TABLES = {
    "tv":    "tblCQbsK6FBkGIY9",    # 📺 剧集
    "anime": "tblvXnrGj0Al03HL",   # 🎞️ 动漫
    "movie": "tblhSzGZwQ4SOBUe",   # 🎬 电影
}

# 字段映射：表内显示名 → 字段ID
# 三表字段名不同但结构相同
FIELDS_TV =    {"name":"📺 名字","ep":"集数","status":"状态","quality":"质量","genre":"流派","year":"发行年份","tmdb":"TMDB ID","size":"大小(GB)","fc":"文件数"}
FIELDS_ANIME = {"name":"🎞️ 名字","ep":"集数","status":"状态","quality":"质量","genre":"流派","year":"发行年份","tmdb":"TMDB ID","size":"大小(GB)","fc":"文件数"}
FIELDS_MOVIE = {"name":"🎬 名字","ep":"集数","status":"状态","quality":"质量","genre":"流派","year":"发行年份","tmdb":"TMDB ID","size":"大小(GB)","fc":"文件数"}

# 字段ID
FIELDS_TV_ID =    {"name":"fld8hKbxBo","ep":"fldMtKpKs0","status":"fldFvmhF6M","quality":"fldIKfSR7d","genre":"fldviHRDkv","year":"fldDEOpmEE","tmdb":"fldfNZjl9d","size":"fldzwKHjvI","fc":"fldO4F1iB5"}
FIELDS_ANIME_ID = {"name":"fldLvjkoTM","ep":"fldzdbkRFv","status":"fld8379Qzf","quality":"fld7mjMqoK","genre":"fld0rM24AX","year":"fld97tDTbz","tmdb":"fldBDmlO1D","size":"fldE1dqTIP","fc":"fldiSCsP9t"}
FIELDS_MOVIE_ID = {"name":"fld3AhUcYK","ep":"fldKOCyCnm","status":"fldX1yKtgH","quality":"fldad7OBAa","genre":"fldOppqRLf","year":"fld0FmqWml","tmdb":"fldYGK7l30","size":"fldB07sZYH","fc":"fldr7GNsGt"}

FIELD_MAP = {
    "tv":    (FIELDS_TV,    FIELDS_TV_ID),
    "anime": (FIELDS_ANIME, FIELDS_ANIME_ID),
    "movie": (FIELDS_MOVIE, FIELDS_MOVIE_ID),
}

# 选项映射（三表共用相同 option ID）
STATUS_OPTIONS = {"✅已入库":"optSXYaSMr","🔄追更中":"optVZqcuvs","⭐想看":"optpYOarwN"}
GENRE_OPTIONS = {"动画":"opt144275708","动作冒险":"opt1201370868","剧情":"opt144268455","悬疑":"opt144387022","喜剧":"opt144290228","犯罪":"opt144532484","科幻":"opt144580659","爱情":"opt144520733","动作":"opt144266013"}

def get_token():
    data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        data=data, headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())['tenant_access_token']

def sync_record(token, fields, table="tv", record_id=None):
    """创建或更新一条记录。table: tv/anime/movie"""
    names, _ = FIELD_MAP[table]
    table_id = TABLES[table]
    feishu_fields = {}
    for key, value in fields.items():
        if value is None or value == "":
            continue
        display_key = names.get(key, key)
        if key == "status" and value in STATUS_OPTIONS:
            feishu_fields[display_key] = STATUS_OPTIONS[value]
        elif key == "genre" and isinstance(value, list):
            feishu_fields[display_key] = [GENRE_OPTIONS.get(v, v) for v in value if v in GENRE_OPTIONS]
        elif isinstance(value, list):
            feishu_fields[display_key] = value
        else:
            feishu_fields[display_key] = value

    APP = BITABLE_APP_TOKEN
    TABLE = table_id

    if record_id:
        body = json.dumps({'fields': feishu_fields}).encode()
        req = urllib.request.Request(
            f'https://open.feishu.cn/open-apis/bitable/v1/apps/{APP}/tables/{TABLE}/records/{record_id}',
            data=body, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        req.method = 'PUT'
    else:
        body = json.dumps({'fields': feishu_fields}).encode()
        req = urllib.request.Request(
            f'https://open.feishu.cn/open-apis/bitable/v1/apps/{APP}/tables/{TABLE}/records',
            data=body, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})

    resp = urllib.request.urlopen(req, timeout=15)
    result = json.loads(resp.read())
    if result.get('code') == 0:
        rid = result.get('data', {}).get('record', {}).get('record_id', '')
        name = fields.get('name', '?')
        print(f"  ✅ 飞书: {name} ({rid[:8]})")
        return rid
    else:
        print(f"  ❌ 飞书失败: {result.get('msg', '?')}")
        return None

def find_by_tmdb(token, tmdb_id, table="tv"):
    """按 TMDB ID 在指定表中查找"""
    names, _ = FIELD_MAP[table]
    tmdb_key = names["tmdb"]  # "TMDB ID"
    table_id = TABLES[table]
    APP = BITABLE_APP_TOKEN
    req = urllib.request.Request(
        f'https://open.feishu.cn/open-apis/bitable/v1/apps/{APP}/tables/{table_id}/records?page_size=50',
        headers={'Authorization': f'Bearer {token}'})
    d = json.loads(urllib.request.urlopen(req, timeout=15).read())
    for item in d.get('data', {}).get('items', []):
        f = item.get('fields', {})
        if str(f.get(tmdb_key, '')) == str(tmdb_id):
            return item['record_id']
    return None

def delete_all(token, table="tv"):
    table_id = TABLES[table]
    APP = BITABLE_APP_TOKEN
    req = urllib.request.Request(
        f'https://open.feishu.cn/open-apis/bitable/v1/apps/{APP}/tables/{table_id}/records?page_size=50',
        headers={'Authorization': f'Bearer {token}'})
    items = json.loads(urllib.request.urlopen(req, timeout=15).read())['data']['items']
    for item in items:
        req2 = urllib.request.Request(
            f'https://open.feishu.cn/open-apis/bitable/v1/apps/{APP}/tables/{table_id}/records/{item["record_id"]}',
            headers={'Authorization': f'Bearer {token}'})
        req2.method = 'DELETE'
        urllib.request.urlopen(req2, timeout=10)
    print(f'删除 {len(items)} 条')

if __name__ == '__main__':
    import sys
    token = get_token()

    if len(sys.argv) < 2:
        print("用法: feishu_bitable.py sync <table> <name> <tmdb> <ep> <status> <size> <fc> <quality> <year> <genres...>")
        print("  table: tv / anime / movie")
        sys.exit(1)

    action = sys.argv[1]
    if action == 'sync':
        if len(sys.argv) < 10:
            print("参数不足")
            sys.exit(1)
        table = sys.argv[2]  # tv/anime/movie
        _, ids = FIELD_MAP[table]
        fields = {}
        fields['name'] = sys.argv[3]
        fields['tmdb'] = sys.argv[4]
        fields['ep'] = sys.argv[5]
        fields['status'] = sys.argv[6]
        fields['size'] = float(sys.argv[7]) if sys.argv[7] else None
        fields['fc'] = int(sys.argv[8]) if sys.argv[8] else None
        fields['quality'] = sys.argv[9]
        fields['year'] = int(sys.argv[10]) if sys.argv[10] else None
        fields['genre'] = sys.argv[11:]
        existing = find_by_tmdb(token, sys.argv[4], table)
        sync_record(token, fields, table, existing)
    elif action == 'delete-all':
        table = sys.argv[2] if len(sys.argv) > 2 else 'tv'
        delete_all(token, table)
