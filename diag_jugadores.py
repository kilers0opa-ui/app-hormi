import json, hormi_transferencias as t
c=json.load(open("hormi_config.json"))
out={}
for p in c["players"]:
    lt=t.latest_transfer(p["player_id"])
    club=[x for x in p["targets"] if x["kind"]=="club"][0]
    out[p["key"]]={"config":club["team_id"],"ultimo_fichaje":lt and {"in":lt["teams"]["in"],"date":lt["date"],"type":lt.get("type")}}
print(json.dumps(out,ensure_ascii=False,indent=1))
