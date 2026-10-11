import json, os, urllib.request, urllib.parse
K=os.environ["APIFOOTBALL_KEY"]
def g(e,p):
    r=urllib.request.Request("https://v3.football.api-sports.io"+e+"?"+urllib.parse.urlencode(p),headers={"x-apisports-key":K})
    return json.load(urllib.request.urlopen(r)).get("response",[])
out={}
for term,first in [("Fimbres","Iker"),("Campillo","Diego")]:
    res=[]
    for x in g("/players/profiles",{"search":term}):
        p=x["player"]
        if first in (p.get("firstname") or ""):
            p2={"id":p["id"],"name":p["name"],"first":p.get("firstname"),"last":p.get("lastname"),"birth":(p.get("birth") or {}).get("date"),"pos":p.get("position")}
            p2["teams"]=[{"team":t["team"]["name"],"id":t["team"]["id"],"seasons":t["seasons"][:3]} for t in g("/players/teams",{"player":p["id"]})][:4]
            res.append(p2)
    out[term]=res
out["ligas"]={}
for tid in (597,1088,2278,2279):
    f=g("/fixtures",{"team":tid,"next":2})
    out["ligas"][tid]=[(x["league"]["id"],x["league"]["name"],x["league"]["country"],x["league"]["season"],x["teams"]["home"]["name"],x["teams"]["away"]["name"],x["fixture"]["date"]) for x in f]
for pid in (2873,35690,524762,453887):
    p=g("/players/profiles",{"player":pid})
    out[pid]=[(x["player"]["name"],x["player"].get("position"),(x["player"].get("birth") or {}).get("date")) for x in p]
print(json.dumps(out,ensure_ascii=False,indent=1))
