#!/usr/bin/env python3
"""
飞书表格选项审计 — 找出三表中的孤儿选项 (未被任何记录引用的 option)
用法: python3 feishu_audit.py

飞书 REST API 不支持删除 option 端点。本脚本列出需要手动在 UI 删除的垃圾选项。
"""
import os
import json, urllib.request

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")

TABLES = {
    "📺 剧集": ("tblCQbsK6FBkGIY9",    "状态", "流派"),
    "🎞️ 动漫": ("tblvXnrGj0Al03HL",   "状态", "流派"),
    "🎬 电影": ("tblhSzGZwQ4SOBUe",    "状态", "流派"),
}


def _api(method, path, body=None):
    """飞书 API 请求"""
    data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=data, headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req, timeout=15).read())["tenant_access_token"]

    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if body:
        req.data = json.dumps(body).encode()
        req.method = method
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def get_field_options(table_id, field_name):
    """获取 field 的所有 options"""
    # 先获取 field ID
    fields = _api("GET", f"/bitable/v1/apps/{APP_TOKEN}/tables/{table_id}/fields")
    field_id = None
    for f in fields.get("data", {}).get("items", []):
        if f.get("field_name") == field_name:
            field_id = f["field_id"]
            break
    if not field_id:
        return []

    # 获取 options (在 field 的 property 里)
    for f in fields.get("data", {}).get("items", []):
        if f.get("field_id") == field_id:
            prop = f.get("property", {})
            return prop.get("options", [])

    return []


def get_all_records(table_id, page_size=100):
    """获取表的所有记录"""
    records = []
    page_token = ""
    while True:
        url = f"/bitable/v1/apps/{APP_TOKEN}/tables/{table_id}/records?page_size={page_size}"
        if page_token:
            url += f"&page_token={page_token}"
        d = _api("GET", url)
        records.extend(d.get("data", {}).get("items", []))
        if not d.get("data", {}).get("has_more"):
            break
        page_token = d["data"].get("page_token", "")
    return records


def audit():
    """审计所有三表，找孤儿 option"""
    for table_name, (table_id, status_field, genre_field) in TABLES.items():
        print(f"\n{'='*60}")
        print(f"  {table_name} ({table_id})")
        print(f"{'='*60}")

        # 获取所有 option
        status_opts = get_field_options(table_id, status_field)
        genre_opts = get_field_options(table_id, genre_field)

        # 获取所有记录，收集被引用的 option ID
        records = get_all_records(table_id)
        used_status = set()
        used_genre = set()
        for r in records:
            fields = r.get("fields", {})
            s = fields.get(status_field, "")
            if s:
                used_status.add(s)
            g = fields.get(genre_field, [])
            if isinstance(g, list):
                used_genre.update(g)

        # 检查状态选项
        print(f"\n  [{status_field}] {len(status_opts)} options, {len(used_status)} used:")
        orphans = []
        for opt in status_opts:
            opt_id = opt.get("id", "")
            name = opt.get("name", "?")
            in_use = opt_id in used_status
            mark = "✅" if in_use else "⚠️ ORPHAN"
            print(f"    {mark} {name:12} ({opt_id})")
            if not in_use:
                orphans.append(name)

        # 检查流派选项
        print(f"\n  [{genre_field}] {len(genre_opts)} options, {len(used_genre)} used:")
        for opt in genre_opts:
            opt_id = opt.get("id", "")
            name = opt.get("name", "?")
            in_use = opt_id in used_genre
            mark = "✅" if in_use else "⚠️ ORPHAN"
            print(f"    {mark} {name:12} ({opt_id})")
            if not in_use:
                orphans.append(name)

        if orphans:
            print(f"\n  🔧 需手动删除的选项: {', '.join(orphans)}")
            print(f"  操作: 飞书表格 → 右击列头 → 编辑字段 → 删除对应选项")
        else:
            print(f"\n  ✅ 所有选项均在使用中")


if __name__ == "__main__":
    audit()
