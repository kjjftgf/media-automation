import os
import sys
sys.path.insert(0, "/workspace")
from catchup import search_pansou, search_xiaokupan

print("=== PanSou 搜索: 九门 ===")
try:
    for r in search_pansou("九门", max_ep=17):
        print(f"score={r['score']} url={r['url'][:60]} note={r['note'][:80]} ep={r['episodes'][:10]} s={r['season']}")
except Exception as e:
    print("ERR:", e)

print("\n=== xiaokupan 搜索: 九门 ===")
try:
    for r in search_xiaokupan("九门", max_ep=17):
        print(f"score={r['score']} url={r['url'][:60]} note={r['note'][:80]} ep={r['episodes'][:10]} s={r['season']}")
except Exception as e:
    print("ERR:", e)
