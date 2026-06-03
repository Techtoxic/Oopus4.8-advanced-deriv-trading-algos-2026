"""Pull live Deriv payouts (proposal endpoint, no auth) for the validation
symbol set, so house edge is measured not assumed. -> ../data/payouts.json"""
import asyncio, json, os
import websockets

URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "payouts.json")
SYMBOLS = ["1HZ100V", "1HZ10V", "1HZ75V", "1HZ25V", "R_100", "R_10", "JD100"]


async def _once(ws, **kw):
    req = {"proposal": 1, "amount": 1, "basis": "stake", "currency": "USD"}
    req.update(kw)
    await ws.send(json.dumps(req))
    while True:
        m = json.loads(await ws.recv())
        if "error" in m:
            return {"error": m["error"]["message"]}
        if m.get("msg_type") == "proposal" and isinstance(m.get("proposal"), dict):
            return {"payout": m["proposal"].get("payout")}


async def prop(ws, **kw):
    r = None
    for a in range(6):
        r = await _once(ws, **kw)
        if "error" in r and "rate" in r["error"].lower():
            await asyncio.sleep(2.5 * (a + 1)); continue
        return r
    return r


def jobs(sym):
    j = [(sym, "DIGITDIFF", {"barrier": "5"}, 0.9),
         (sym, "DIGITMATCH", {"barrier": "5"}, 0.1),
         (sym, "DIGITEVEN", {}, 0.5), (sym, "DIGITODD", {}, 0.5)]
    for b in range(0, 9):
        j.append((sym, "DIGITOVER", {"barrier": str(b)}, (9 - b) / 10))
    for b in range(1, 10):
        j.append((sym, "DIGITUNDER", {"barrier": str(b)}, b / 10))
    return j


async def main():
    results = []
    async with websockets.connect(URL, max_size=2**24) as ws:
        for s in SYMBOLS:
            for (sym, ct, extra, tp) in jobs(s):
                r = await prop(ws, contract_type=ct, symbol=sym,
                               duration=1, duration_unit="t", **extra)
                await asyncio.sleep(0.7)
                row = {"symbol": sym, "contract": ct, "params": extra, "true_p": tp}
                if "error" in r:
                    row["error"] = r["error"]
                else:
                    po = r["payout"]; row["payout"] = po
                    row["breakeven_p"] = round(1.0 / po, 4) if po else None
                    row["ev_per_$1"] = round(tp * po - 1.0, 4)
                    row["house_edge"] = round(-(tp * po - 1.0), 4)
                results.append(row)
                print(row.get("symbol"), row.get("contract"), row.get("params"),
                      row.get("payout"), row.get("house_edge"), flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
    print("SAVED", OUT)


if __name__ == "__main__":
    asyncio.run(main())
