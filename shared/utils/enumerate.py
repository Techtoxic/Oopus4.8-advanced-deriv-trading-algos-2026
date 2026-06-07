import json, os, websocket

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

# Active synthetic symbols
res = call({"active_symbols": "brief", "product_type": "basic"}, "active_symbols")
syms = res["active_symbols"]
synth = [s for s in syms if s["market"] == "synthetic_index"]
print("=== SYNTHETIC INDEX MARKETS:", len(synth), "===")
# group by submarket
from collections import defaultdict
g = defaultdict(list)
for s in synth:
    g[s["submarket"]].append(s)
for sub, items in sorted(g.items()):
    print(f"\n-- submarket: {sub} ({len(items)}) --")
    for s in sorted(items, key=lambda x: x["symbol"]):
        print(f"  {s['symbol']:<16} {s['display_name']:<32} open={s['exchange_is_open']}")

ws.close()
