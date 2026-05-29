"""
Multi-instrument tick collector for Deriv synthetic indices.

Pulls long contiguous tick windows via the public `ticks_history` endpoint
(no auth required) by walking the epoch backwards in chunks of up to 5000.

Usage:
    python collect.py                 # pull the default universe
    python collect.py SYMBOL COUNT    # pull a single symbol

Output: one gzipped JSON file per symbol in ../data/<symbol>.json.gz
Each file is a list of [epoch:int, price:float].
"""
import asyncio
import gzip
import json
import os
import sys
import time

import websockets

URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# (symbol, target_ticks). Chosen to cover every structural family of synthetic.
UNIVERSE = [
    ("1HZ100V", 100_000),   # Volatility 100 (1s) - re-pull to independently verify prior work
    ("R_100",    80_000),   # Volatility 100 (2s)
    ("1HZ10V",   80_000),   # Volatility 10 (1s) - low vol
    ("CRASH1000", 100_000), # asymmetric: up-drift, rare down-crash
    ("BOOM1000",  100_000), # asymmetric: down-drift, rare up-boom
    ("CRASH500",  80_000),
    ("BOOM500",   80_000),
    ("stpRNG",    80_000),  # Step Index 100: fixed step size
    ("JD100",     80_000),  # Jump 100: occasional large jumps
]


async def fetch_history(ws, symbol, end_epoch=None, count=5000):
    req = {
        "ticks_history": symbol,
        "style": "ticks",
        "count": count,
        "end": "latest" if end_epoch is None else str(end_epoch),
        "adjust_start_time": 1,
    }
    await ws.send(json.dumps(req))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("msg_type") == "history":
            return msg["history"]
        if "error" in msg:
            raise RuntimeError(msg["error"]["message"])


async def collect_symbol(symbol, target):
    all_ticks = []
    async with websockets.connect(URL, ping_interval=20, ping_timeout=60, max_size=2**24) as ws:
        end = None
        stale = 0
        while len(all_ticks) < target:
            hist = await fetch_history(ws, symbol, end_epoch=end, count=5000)
            chunk = list(zip(hist["times"], hist["prices"]))
            if not chunk:
                break
            new_part = [t for t in chunk if not all_ticks or t[0] < all_ticks[0][0]]
            if not new_part:
                stale += 1
                if stale >= 2:
                    break
                end = all_ticks[0][0] - 1
                continue
            stale = 0
            all_ticks = new_part + all_ticks
            end = all_ticks[0][0] - 1
            await asyncio.sleep(0.35)
    all_ticks.sort(key=lambda x: x[0])
    out = os.path.join(DATA_DIR, f"{symbol}.json.gz")
    os.makedirs(DATA_DIR, exist_ok=True)
    with gzip.open(out, "wt") as f:
        json.dump(all_ticks, f)
    span = all_ticks[-1][0] - all_ticks[0][0] if all_ticks else 0
    print(f"[{symbol}] {len(all_ticks)} ticks, span {span/3600:.1f}h -> {out}", flush=True)
    return len(all_ticks)


async def main():
    if len(sys.argv) >= 2:
        sym = sys.argv[1]
        cnt = int(sys.argv[2]) if len(sys.argv) > 2 else 80_000
        await collect_symbol(sym, cnt)
        return
    for sym, cnt in UNIVERSE:
        for attempt in range(3):
            try:
                await collect_symbol(sym, cnt)
                break
            except Exception as e:
                print(f"[{sym}] attempt {attempt+1} failed: {e}", flush=True)
                await asyncio.sleep(2)


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    print(f"done in {time.time()-t0:.1f}s", flush=True)
