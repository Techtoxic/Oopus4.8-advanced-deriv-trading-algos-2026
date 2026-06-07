import json, os, websocket
from collections import defaultdict
TOKEN = os.environ["DERIV_TOKEN"]
URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"
ws = websocket.create_connection(URL, timeout=30)
def call(payload, want):
    ws.send(json.dumps(payload))
    while True:
        msg = json.loads(ws.recv())
        if "error" in msg: return msg
        if want in msg: return msg
call({"authorize": TOKEN}, "authorize")

for sym in ["R_10", "1HZ10V", "BOOM500", "R_100"]:
    res = call({"contracts_for": sym, "currency": "USD"}, "contracts_for")
    if "error" in res:
        print(sym, "ERROR", res["error"]["message"]); continue
    av = res["contracts_for"]["available"]
    print(f"\n===== {sym} : {len(av)} contract variants =====")
    bycat = defaultdict(set)
    specs = {}
    for c in av:
        ct = c["contract_type"]
        bycat[c["contract_category"]].add(ct)
        if ct not in specs:
            specs[ct] = {
                "min_dur": c.get("min_contract_duration"),
                "max_dur": c.get("max_contract_duration"),
                "barriers": c.get("barriers"),
                "barrier_cat": c.get("barrier_category"),
            }
    for cat, cts in sorted(bycat.items()):
        print(f"  [{cat}]: {sorted(cts)}")
    # print digit specs detail
    for ct in sorted(specs):
        if "DIGIT" in ct:
            print(f"    {ct}: dur {specs[ct]['min_dur']}..{specs[ct]['max_dur']} barriers={specs[ct]['barriers']}")
ws.close()
