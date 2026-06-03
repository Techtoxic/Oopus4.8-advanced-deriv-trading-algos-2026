"""
Fresh, independent multi-instrument tick collector for the lattice-microstructure
validation session.

Pulls long contiguous tick windows from Deriv's public `ticks_history` endpoint
(no auth) walking the epoch backwards in chunks of 5000. Writes one gzipped JSON
per symbol: a list of [epoch:int, price:float].

This deliberately pulls a *new* time window (and larger samples on the key
indices) so the validation is statistically independent of the data cached in
the prior repo's data/ folder.

Usage:
    python fetch_data.py            # default universe
    python fetch_data.py SYM N      # single symbol, N ticks
"""
import asyncio, gzip, json, os, sys, time
import websockets

URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Digit-capable indices across the volatility family (1s and 2s) plus Jump.
# Larger samples on V100/V10/V75 because those are the friend's "fast" assets.
UNIVERSE = [
    ("1HZ100V", 200_000),  # Volatility 100 (1s) - the friend's primary fast asset
    ("1HZ10V",  150_000),  # Volatility 10  (1s) - low vol, tightest digit modulus
    ("1HZ75V",  150_000),  # Volatility 75  (1s)
    ("1HZ25V",  120_000),  # Volatility 25  (1s)
    ("R_100",   120_000),  # Volatility 100 (2s)
    ("R_10",    120_000),  # Volatility 10  (2s)
    ("JD100",   120_000),  # Jump 100
]


async def fetch_history(ws, symbol, end_epoch=None, count=5000):
    req = {
        "ticks_history": symbol, "style": "ticks", "count": count,
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
    seen = {}
    end = None
    async with websockets.connect(URL, ping_interval=20, ping_timeout=60, max_size=2**24) as ws:
        while len(seen) < target:
            for attempt in range(6):
                try:
                    h = await fetch_history(ws, symbol, end_epoch=end, count=5000)
                    break
                except Exception as e:
                    if "rate" in str(e).lower():
                        await asyncio.sleep(2.5 * (attempt + 1)); continue
                    raise
            times, prices = h["times"], h["prices"]
            if not times:
                break
            for t, p in zip(times, prices):
                seen[int(t)] = float(p)
            oldest = min(int(t) for t in times)
            if end is not None and oldest >= end:
                break  # no progress
            end = oldest - 1
            await asyncio.sleep(0.4)
            print(f"  {symbol}: {len(seen):>7d}/{target}", flush=True)
    ticks = sorted(seen.items())
    ticks = [[e, p] for e, p in ticks]
    out = os.path.join(DATA_DIR, f"{symbol}.json.gz")
    os.makedirs(DATA_DIR, exist_ok=True)
    with gzip.open(out, "wt") as f:
        json.dump(ticks, f)
    span_h = (ticks[-1][0] - ticks[0][0]) / 3600.0
    print(f"SAVED {symbol}: {len(ticks)} ticks, span {span_h:.1f}h -> {out}", flush=True)


async def main():
    if len(sys.argv) == 3:
        await collect_symbol(sys.argv[1], int(sys.argv[2]))
        return
    for sym, tgt in UNIVERSE:
        t0 = time.time()
        try:
            await collect_symbol(sym, tgt)
        except Exception as e:
            print(f"FAILED {sym}: {e}", flush=True)
        print(f"  ({time.time()-t0:.0f}s)\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
