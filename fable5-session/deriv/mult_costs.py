#!/usr/bin/env python3
"""Check multiplier costs + honest cost-aware backtest of 1m mean reversion on Deriv frx."""
import asyncio, json, gzip
import numpy as np
import websockets

APP_ID = 1089
URL = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"

async def req(ws, payload):
    await ws.send(json.dumps(payload))
    while True:
        r = json.loads(await ws.recv())
        if r.get("msg_type") == "ping": continue
        return r

async def main():
    async with websockets.connect(URL, max_size=2**24) as ws:
        for sym in ["frxUSDJPY", "frxEURUSD", "frxGBPUSD", "frxXAUUSD"]:
            # multiplier proposal: get commission
            r = await req(ws, {"proposal": 1, "amount": 100, "basis": "stake", "currency": "USD",
                               "contract_type": "MULTUP", "symbol": sym, "multiplier": 100})
            if "error" in r:
                print(sym, "MULTUP ERROR:", r["error"]["message"])
                # try allowed multipliers
                r2 = await req(ws, {"contracts_for": sym, "currency": "USD"})
                if "contracts_for" in r2:
                    for c in r2["contracts_for"]["available"]:
                        if c["contract_type"] == "MULTUP":
                            print("  allowed multipliers:", c.get("multiplier_range"))
            else:
                pr = r["proposal"]
                print(sym, "MULTUP x100: commission=", pr.get("commission"), "ask=", pr["ask_price"],
                      json.dumps({k: pr[k] for k in pr if k in ("date_expiry", "spot")}))
            # live spread from tick
            r = await req(ws, {"ticks": sym, "subscribe": 1})
            if "tick" in r:
                t = r["tick"]
                print(f"  {sym} tick: quote={t['quote']} bid={t.get('bid')} ask={t.get('ask')}",
                      "spread_pips=", None if not t.get('ask') else round((t['ask']-t['bid'])/ (0.01 if 'JPY' in sym else 0.0001),2))
                await req(ws, {"forget_all": "ticks"})

asyncio.run(main())
