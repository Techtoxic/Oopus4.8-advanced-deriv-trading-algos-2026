"""tick_collector.py — accumulate tick history past Deriv's 1-day API limit.

WHY THIS EXISTS
tick_integrity.py and clean_fetch.py established that Deriv serves exactly 86,401 ticks
per symbol — 1.00 day — and nothing older. history_paged silently loops that same day when
asked for more, which invalidated eight tools in this session: chi-square inflated by the
replication factor, Wilson intervals too narrow by sqrt(k), and IS/OOS splits that put
copies of the same ticks on both sides.

The entry-digit result is the concrete casualty. On unique ticks the spread is 2.42pp
against a noise 95th percentile of 2.39pp, and rho is +0.661 against a noise 95th of
+0.552. Both sit exactly at the edge of noise, and both are computed on a single day that
cannot be re-sampled.

    1 day  ->  8,640 per digit  ->  detects a true edge of 1.96pp at t=2
    7 days ->  60,481 per digit ->  detects 0.74pp
   30 days -> 259,203 per digit ->  detects 0.36pp

The candidate edge is ~0.1-0.5pp. Only weeks of collected data can resolve it.

WHAT THIS DOES
Subscribes to live ticks for several symbols and appends each to a daily CSV. Memory stays
flat: every tick is written straight to disk, nothing accumulates in RAM. Auto-reconnects,
backfills the last 24h from history on first run so no time is wasted, and deduplicates by
epoch so restarts and backfill overlap cannot reintroduce the exact bug this fixes.

Storage is ~1.7 MB per symbol per day, so five symbols for a month is about 260 MB.

Deploy the same way as the trading bot (NSSM service on the EC2 box):
    nssm install TickCollector <python.exe>
    nssm set TickCollector AppParameters "tick_collector.py --dir C:\\ticks"
    nssm set TickCollector AppDirectory <tools dir>
    nssm set TickCollector Start SERVICE_AUTO_START
    nssm start TickCollector

Then analyse with --verify to confirm coverage before trusting any result.

Run: python3 tick_collector.py --dir ./ticks
     python3 tick_collector.py --dir ./ticks --verify
"""
import argparse, csv, json, os, ssl, time, datetime as dt
import asyncio

try:
    import websockets
except ImportError:
    websockets = None

WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=67340"
DEFAULT_SYMS = ["JD100", "JD75", "1HZ100V", "R_100", "1HZ10V"]


def day_path(base, sym, epoch):
    d = dt.datetime.fromtimestamp(epoch, dt.UTC).strftime("%Y-%m-%d")
    p = os.path.join(base, sym)
    os.makedirs(p, exist_ok=True)
    return os.path.join(p, f"{d}.csv")


class Writer:
    """Append-only per-symbol/day CSV with in-memory dedupe of the current day only."""

    def __init__(self, base):
        self.base = base
        self.files = {}
        self.seen = {}

    def _load_seen(self, sym, path):
        s = set()
        if os.path.exists(path):
            with open(path) as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if row:
                        s.add(int(row[0]))
        return s

    def write(self, sym, epoch, quote):
        path = day_path(self.base, sym, epoch)
        key = (sym, path)
        if key not in self.files:
            for k in [k for k in self.files if k[0] == sym]:
                self.files[k].close()
                del self.files[k]
                self.seen.pop(k, None)
            self.seen[key] = self._load_seen(sym, path)
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            f = open(path, "a", newline="", buffering=1)
            if new:
                f.write("epoch,quote\n")
            self.files[key] = f
        if epoch in self.seen[key]:
            return False
        self.seen[key].add(epoch)
        self.files[key].write(f"{epoch},{quote}\n")
        return True


async def backfill(ws, sym, writer):
    await ws.send(json.dumps({"ticks_history": sym, "count": 5000,
                              "end": "latest", "style": "ticks"}))
    n = 0
    end = None
    for _ in range(20):
        msg = json.loads(await ws.recv())
        h = msg.get("history")
        if not h:
            break
        ts, ps = h.get("times", []), h.get("prices", [])
        if not ts:
            break
        for t_, p_ in zip(ts, ps):
            if writer.write(sym, int(t_), p_):
                n += 1
        end = min(int(x) for x in ts) - 1
        await ws.send(json.dumps({"ticks_history": sym, "count": 5000,
                                  "end": end, "style": "ticks"}))
        await asyncio.sleep(0.15)
    return n


async def collect(symbols, writer, stats):
    ctx = ssl.create_default_context()
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20,
                                          ping_timeout=60, ssl=ctx) as ws:
                print(f"[{dt.datetime.now(dt.UTC):%H:%M:%S}] connected")
                for s in symbols:
                    got = await backfill(ws, s, writer)
                    print(f"  backfilled {s}: {got} new ticks")
                for s in symbols:
                    await ws.send(json.dumps({"ticks": s, "subscribe": 1}))
                async for raw in ws:
                    m = json.loads(raw)
                    t = m.get("tick")
                    if not t:
                        continue
                    sym = t.get("symbol") or t.get("underlying_symbol")
                    if writer.write(sym, int(t["epoch"]), t["quote"]):
                        stats[sym] = stats.get(sym, 0) + 1
                    tot = sum(stats.values())
                    if tot and tot % 5000 == 0:
                        print(f"[{dt.datetime.now(dt.UTC):%H:%M:%S}] "
                              + "  ".join(f"{k}:{v}" for k, v in sorted(stats.items())))
        except Exception as e:
            print(f"[{dt.datetime.now(dt.UTC):%H:%M:%S}] {type(e).__name__}: {e} "
                  f"— reconnecting in 5s")
            await asyncio.sleep(5)


def verify(base):
    print(f"{'symbol':10}{'days':>6}{'ticks':>10}{'span':>28}{'density':>10}")
    print("-" * 66)
    for sym in sorted(os.listdir(base)):
        d = os.path.join(base, sym)
        if not os.path.isdir(d):
            continue
        eps = []
        files = sorted(f for f in os.listdir(d) if f.endswith(".csv"))
        for fn in files:
            with open(os.path.join(d, fn)) as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if row:
                        eps.append(int(row[0]))
        if not eps:
            continue
        eps = sorted(set(eps))
        span = eps[-1] - eps[0]
        print(f"{sym:10}{len(files):>6}{len(eps):>10}"
              f"{dt.datetime.fromtimestamp(eps[0], dt.UTC):%m-%d %H:%M} -> "
              f"{dt.datetime.fromtimestamp(eps[-1], dt.UTC):%m-%d %H:%M}"
              f"{len(eps)/max(span,1)*100:>9.1f}%")
    print("\ndensity below ~95% means gaps — check for disconnects before analysing")
    print("\nresolving power on the entry-digit question:")
    print(f"  {'days':>5}{'per digit':>12}{'EV SE':>9}{'detects at t=2':>17}")
    import math
    for days in (1, 3, 7, 14, 30):
        n = 86401 * days / 10
        se = 1.8286 * math.sqrt(0.5475 * 0.4525 / n) * 100
        print(f"  {days:>5}{n:>12.0f}{se:>8.2f}pp{2*se:>16.2f}pp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="./ticks")
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMS)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()

    os.makedirs(a.dir, exist_ok=True)
    if a.verify:
        verify(a.dir)
        return
    if websockets is None:
        print("pip install websockets")
        return

    print(f"collecting {a.symbols} -> {os.path.abspath(a.dir)}")
    print("~1.7 MB per symbol per day. Ctrl-C to stop; restart resumes safely.\n")
    writer = Writer(a.dir)
    try:
        asyncio.run(collect(a.symbols, writer, {}))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
