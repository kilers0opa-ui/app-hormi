import json, os, urllib.request, urllib.parse
K=os.environ["APIFOOTBALL_KEY"]
def g(e,p):
    r=urllib.request.Request("https://v3.football.api-sports.io"+e+"?"+urllib.parse.urlencode(p),headers={"x-apisports-key":K})
    return json.load(urllib.request.urlopen(r)).get("response",[])
out={}
for term,last in [("Montes","montes"),("Chavez","chavez"),("Sandoval","sandoval"),("Camberos","camberos")]:
    res=[]
    for x in g("/players/profiles",{"search":term}):
        p=x["player"]
        if (p.get("nationality") or "")=="Mexico":
            res.append({"id":p["id"],"name":p["name"],"first":p.get("firstname"),"last":p.get("lastname"),"birth":(p.get("birth") or {}).get("date")})
    out[term]=res
for term,res in out.items():
    for p in res:
        if term=="Montes" and "César" not in (p["first"] or "") and "Cesar" not in (p["first"] or ""): continue
        if term=="Chavez" and "Luis" not in (p["first"] or ""): continue
        if term=="Sandoval" and "Santiago" not in (p["first"] or ""): continue
        p["teams"]=[{"team":t["team"]["name"],"id":t["team"]["id"],"seasons":t["seasons"][:3]} for t in g("/players/teams",{"player":p["id"]})][:4]
print(json.dumps(out,ensure_ascii=False,indent=1))
