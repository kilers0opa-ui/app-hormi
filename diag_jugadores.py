import json, os, urllib.request, urllib.parse
K=os.environ["APIFOOTBALL_KEY"]
def g(e,p):
    r=urllib.request.Request("https://v3.football.api-sports.io"+e+"?"+urllib.parse.urlencode(p),headers={"x-apisports-key":K})
    return json.load(urllib.request.urlopen(r)).get("response",[])
cfg=json.load(open("hormi_config.json"))
out={}
seen=set()
for p in cfg["players"]:
    t=[x for x in p["targets"] if x["kind"]=="club"][0]
    fx=g("/fixtures",{"team":t["team_id"],"last":1})
    if not fx: continue
    f=fx[0]; fid=f["fixture"]["id"]
    if fid in seen: continue
    seen.add(fid)
    res=g("/fixtures/players",{"fixture":fid})
    mine=None
    for team in res:
        for pl in team["players"]:
            if pl["player"]["id"]==p["player_id"]: mine=pl["statistics"][0]
    out[p["key"]]={"liga":f["league"]["name"],"pais":f["league"]["country"],"fecha":f["fixture"]["date"][:10],"equipos_con_datos":len(res),"stats":mine}
fx=g("/fixtures",{"team":16,"last":1})[0]
res=g("/fixtures/players",{"fixture":fx["fixture"]["id"]})
out["seleccion"]={"liga":fx["league"]["name"],"fecha":fx["fixture"]["date"][:10],"equipos_con_datos":len(res),"ejemplo":res[0]["players"][0]["statistics"][0] if res else None}
print(json.dumps(out,ensure_ascii=False,indent=1))
