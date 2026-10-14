import json, os, urllib.request, urllib.parse
K=os.environ["APIFOOTBALL_KEY"]
def g(e,p):
    r=urllib.request.Request("https://v3.football.api-sports.io"+e+"?"+urllib.parse.urlencode(p),headers={"x-apisports-key":K})
    return json.load(urllib.request.urlopen(r)).get("response",[])
out={}
for key,team,league,pid in [("montes",597,235,2873),("luis_chavez",1088,235,35690),("armando",553,197,291713),("raul",39,40,2887),("quinones",2933,307,35532)]:
    fx=g("/fixtures",{"team":team,"league":league,"season":2026,"last":3})
    for f in fx:
        res=g("/fixtures/players",{"fixture":f["fixture"]["id"]})
        mine=[pl["statistics"][0] for t in res for pl in t["players"] if pl["player"]["id"]==pid]
        s=mine[0] if mine else None
        if s and (s["games"]["minutes"] or 0)>0:
            out[key]={"fecha":f["fixture"]["date"][:10],"rating":s["games"]["rating"],"min":s["games"]["minutes"],"shots":s["shots"],"passes":s["passes"],"duels":s["duels"],"drib":s["dribbles"]}; break
    else:
        out[key]="sin partido con minutos en las últimas 3"
print(json.dumps(out,ensure_ascii=False,indent=1))
