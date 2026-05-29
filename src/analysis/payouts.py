"""
Query real, live Deriv contract payouts via the `proposal` endpoint (no auth
needed for price quotes). For a $1 stake, Deriv returns the total payout on a
win. Combined with the true/empirical win probability this gives the exact,
data-grounded expected value and house edge for every contract.

Output: ../data/payouts.json
"""
import asyncio, json, os, sys
import websockets

URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "payouts.json")

# True win probability for digit contracts under uniform i.i.d. (verified).
TRUE_P = {
    "DIGITDIFF": 0.9, "DIGITMATCH": 0.1,
    "DIGITEVEN": 0.5, "DIGITODD": 0.5,
    # over/under depend on barrier; filled in below
}

async def _proposal_once(ws, **kw):
    req = {"proposal": 1, "amount": 1, "basis": "stake", "currency": "USD"}
    req.update(kw)
    await ws.send(json.dumps(req))
    while True:
        m = json.loads(await ws.recv())
        if "error" in m:
            return {"error": m["error"]["message"]}
        if m.get("msg_type") == "proposal" and isinstance(m.get("proposal"), dict):
            p = m["proposal"]
            return {"payout": p.get("payout"), "spot": p.get("spot")}

async def proposal(ws, **kw):
    for attempt in range(6):
        r = await _proposal_once(ws, **kw)
        if "error" in r and "rate limit" in r["error"].lower():
            await asyncio.sleep(2.5 * (attempt + 1))
            continue
        return r
    return r

def digit_jobs(symbol):
    jobs = []
    jobs.append((symbol, "DIGITDIFF", {"barrier": "5"}, 0.9))
    jobs.append((symbol, "DIGITMATCH", {"barrier": "5"}, 0.1))
    jobs.append((symbol, "DIGITEVEN", {}, 0.5))
    jobs.append((symbol, "DIGITODD", {}, 0.5))
    for b in range(0, 9):  # Over b wins if digit > b => prob (9-b)/10
        jobs.append((symbol, "DIGITOVER", {"barrier": str(b)}, (9 - b) / 10))
    for b in range(1, 10):  # Under b wins if digit < b => prob b/10
        jobs.append((symbol, "DIGITUNDER", {"barrier": str(b)}, b / 10))
    return jobs

def updown_jobs(symbol):
    jobs = []
    for dur in (1, 5):
        jobs.append((symbol, "CALL", {"duration": dur, "duration_unit": "t"}, None))
        jobs.append((symbol, "PUT", {"duration": dur, "duration_unit": "t"}, None))
    return jobs

async def main():
    instruments_digits = ["1HZ100V", "R_100", "1HZ10V", "JD100"]
    instruments_updown = ["1HZ100V", "R_100", "1HZ10V", "JD100", "stpRNG"]
    results = []
    async with websockets.connect(URL, max_size=2**24) as ws:
        jobs = []
        for s in instruments_digits:
            jobs += [(s, ct, dict(extra, duration=1, duration_unit="t"), p) for (s, ct, extra, p) in digit_jobs(s)]
        for s in instruments_updown:
            jobs += updown_jobs(s)
        for (sym, ct, extra, truep) in jobs:
            r = await proposal(ws, contract_type=ct, symbol=sym, **extra)
            await asyncio.sleep(0.8)
            row = {"symbol": sym, "contract": ct, "params": extra, "true_p": truep}
            if "error" in r:
                row["error"] = r["error"]
            else:
                payout = r["payout"]
                row["payout"] = payout
                row["breakeven_p"] = round(1.0 / payout, 4) if payout else None
                if truep is not None:
                    row["ev_per_$1"] = round(truep * payout - 1.0, 4)
                    row["house_edge"] = round(-(truep * payout - 1.0), 4)
            results.append(row)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
    # Pretty print
    print(f"{'symbol':9s} {'contract':11s} {'barrier':7s} {'payout':>7s} {'true_p':>7s} {'BE_p':>7s} {'EV/$':>8s} {'edge%':>7s}")
    for r in results:
        if "error" in r:
            print(f"{r['symbol']:9s} {r['contract']:11s} {'':7s} ERROR: {r['error']}")
            continue
        b = r["params"].get("barrier", "")
        tp = f"{r['true_p']:.3f}" if r.get("true_p") is not None else "  -  "
        ev = f"{r['ev_per_$1']:+.4f}" if r.get("ev_per_$1") is not None else "   -   "
        he = f"{r['house_edge']*100:.2f}" if r.get("house_edge") is not None else "  -  "
        print(f"{r['symbol']:9s} {r['contract']:11s} {str(b):7s} {r['payout']:7.3f} {tp:>7s} {r['breakeven_p']:.4f} {ev:>8s} {he:>7s}")

if __name__ == "__main__":
    asyncio.run(main())
