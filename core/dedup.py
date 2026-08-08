import os
import sqlite3,json,requests,time,re,sys
BASE="https://drive-pc.quark.cn"
db=sqlite3.connect("/app/backend/data/app.db")
config=json.loads(db.execute("SELECT config_json FROM drive_accounts WHERE drive_type=\"quark\" AND enabled=1").fetchone()[0])
db.close()
h={"User-Agent":"Mozilla/5.0","Cookie":config["cookie"]}
sys.path.insert(0,"/app/backend")
from app.extensions.adapters.quark_adapter import QuarkAdapter
adapter=QuarkAdapter(cookie=config["cookie"],account_name="quark")

PARENTS=["e951d021e7f54e96b8b1a6f325b85c73","0505b6a91a534398bb347e38d39c1d56"]

for parent in PARENTS:
    r=requests.get(BASE+"/1/clouddrive/file/sort",params={"pr":"ucpro","fr":"pc","pdir_fid":parent,"page":"1","size":"50"},headers=h,timeout=15)
    for show_dir in r.json()["data"]["list"]:
        if not show_dir.get("dir"):continue
        fn=show_dir["file_name"];sfid=show_dir["fid"]
        m=re.search(r"tmdbid=(\d+)",fn)
        if not m:continue
        
        # Collect all files, group by episode key
        all_vids=[]
        for pg in range(1,8):
            r2=requests.get(BASE+"/1/clouddrive/file/sort",params={"pr":"ucpro","fr":"pc","pdir_fid":sfid,"page":str(pg),"size":"100"},headers=h,timeout=15)
            vids=[x for x in r2.json()["data"]["list"] if not x.get("dir")]
            if not vids:break
            all_vids.extend(vids)
        
        # Group by SxxExx pattern
        groups={}
        for v in all_vids:
            em=re.search(r"(S\d{2}E\d{3})",v["file_name"])
            if em:key=em.group(1)
            else:
                em=re.search(r"[Ee](\d{2,3})",v["file_name"])
                if em:key="E{:03d}".format(int(em.group(1)))
                else:key=v["file_name"][:20]
            groups.setdefault(key,[]).append(v)
        
        # Delete duplicates
        deleted=0
        for key,entries in groups.items():
            if len(entries)>1:
                # Keep one (prefer named version)
                keep=entries[0]
                for e in entries:
                    if "tmdbid" in e["file_name"]:keep=e;break
                for e in entries:
                    if e["fid"]!=keep["fid"]:
                        requests.post(BASE+"/1/clouddrive/file/delete",params={"pr":"ucpro","fr":"pc"},json={"action_type":2,"filelist":[e["fid"]]},headers=h,timeout=10)
                        deleted+=1
        
        dup_count=sum(len(v)-1 for v in groups.values())
        if dup_count:print("DEDUP "+fn[:50]+": "+str(len(groups))+" unique, "+str(deleted)+" deleted")

print("=== DEDUP DONE ===")
