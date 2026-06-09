#!/usr/bin/env python3
"""Fetch fresh tick + candle data from Deriv WS API for independent verification."""
import asyncio, json, gzip, sys, time
import websockets

APP_ID = 1089
URL = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"

TICK_SYMBOLS = ["1HZ100V", "R_100", "1HZ10V", "R_50", "1HZ75V"]
TICKS_PER_SYMBOL = 30000
CANDLE_SYMBOLS = {
    # real markets: 1-minute candles, max history
    "frxEURUSD": 60, "frxGBPUSD": 60, "frxXAUUSD": 60, "frxXAGUSD": 60,
    "cryBTCUSD": 60, "cryETHUSD": 60, "frxUSDJPY": 60,
}
CANDLE_COUNT = 5000  # per request max
CANDLE_BATCHES = 6   # paginate back ~30000 minutes (~20 days)


async def req(ws, payload):
    await ws.send(json.dumps(payload))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("msg_type") == "ping":
            continue
        return resp


async def fetch_ticks(ws, symbol, total):
    all_times, all_prices = [], []
    end = "latest"
    while len(all_prices) < total:
        r = await req(ws, {"ticks_history": symbol, "count": 5000, "end": end, "style": "ticks"})
        if "error" in r:
            print(f"[{symbol}] ERROR {r['error']}", flush=True)
            break
        h = r["history"]
        times, prices = h["times"], h["prices"]
        if not times:
            break
        all_times = times + all_times
        all_prices = prices + all_prices
        end = times[0] - 1
        print(f"[{symbol}] {len(all_prices)} ticks", flush=True)
        await asyncio.sleep(0.3)
    return {"symbol": symbol, "times": all_times, "prices": all_prices,
            "pip_size": r.get("pip_size") or h.get("pip_size")}


async def fetch_candles(ws, symbol, gran, total):
    candles = []
    end = "latest"
    for _ in range(CANDLE_BATCHES):
        r = await req(ws, {"ticks_history": symbol, "count": CANDLE_COUNT, "end": end,
                           "style": "candles", "granularity": gran})
        if "error" in r:
            print(f"[{symbol}] ERROR {r['error']}", flush=True)
            break
        c = r.get("candles", [])
        if not c:
            break
        candles = c + candles
        end = c[0]["epoch"] - 1
        print(f"[{symbol}] {len(candles)} candles", flush=True)
        await asyncio.sleep(0.3)
        if len(candles) >= total:
            break
    return {"symbol": symbol, "granularity": gran, "candles": candles}


async def fetch_payouts(ws):
    """Live proposal quotes -> measured house edge."""
    out = []
    specs = []
    for sym in ["1HZ100V", "R_100"]:
        specs.append({"contract_type": "DIGITDIFF", "barrier": "0", "symbol": sym, "duration": 1, "duration_unit": "t"})
        specs.append({"contract_type": "DIGITMATCH", "barrier": "0", "symbol": sym, "duration": 1, "duration_unit": "t"})
        specs.append({"contract_type": "DIGITEVEN", "symbol": sym, "duration": 1, "duration_unit": "t"})
        specs.append({"contract_type": "DIGITOVER", "barrier": "0", "symbol": sym, "duration": 1, "duration_unit": "t"})
        for d in [1, 2, 5, 10]:
            specs.append({"contract_type": "CALL", "symbol": sym, "duration": d, "duration_unit": "t"})
            specs.append({"contract_type": "PUT", "symbol": sym, "duration": d, "duration_unit": "t"})
    # real markets rise/fall at various durations
    for sym in ["frxEURUSD", "frxXAUUSD", "cryBTCUSD", "frxGBPUSD", "frxUSDJPY"]:
        for d, u in [(1, "m"), (5, "m"), (15, "m"), (1, "h"), (1, "d")]:
            specs.append({"contract_type": "CALL", "symbol": sym, "duration": d, "duration_unit": u})
            specs.append({"contract_type": "PUT", "symbol": sym, "duration": d, "duration_unit": u})
    for s in specs:
        p = {"proposal": 1, "amount": 10, "basis": "stake", "currency": "USD"}
        p.update(s)
        r = await req(ws, p)
        if "error" in r:
            out.append({**s, "error": r["error"]["message"]})
        else:
            pr = r["proposal"]
            payout = pr["payout"] / pr["ask_price"]
            out.append({**s, "payout_ratio": payout, "ask": pr["ask_price"], "payout": pr["payout"]})
        await asyncio.sleep(0.25)
    return out


async def main():
    async with websockets.connect(URL, max_size=2**24) as ws:
        # ticks
        for sym in TICK_SYMBOLS:
            d = await fetch_ticks(ws, sym, TICKS_PER_SYMBOL)
            with gzip.open(f"/home/work/deriv/data/{sym}_ticks.json.gz", "wt") as f:
                json.dump(d, f)
        # candles
        for sym, gran in CANDLE_SYMBOLS.items():
            d = await fetch_candles(ws, sym, gran, CANDLE_COUNT * CANDLE_BATCHES)
            with gzip.open(f"/home/work/deriv/data/{sym}_candles.json.gz", "wt") as f:
                json.dump(d, f)
        # payouts
        po = await fetch_payouts(ws)
        with open("/home/work/deriv/data/payouts_live.json", "w") as f:
            json.dump(po, f, indent=1)
        print("DONE", flush=True)

asyncio.run(main())
