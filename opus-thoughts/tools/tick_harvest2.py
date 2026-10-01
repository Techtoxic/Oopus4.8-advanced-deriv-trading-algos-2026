"""Deep tick harvester v2: explicit epoch windows paged FORWARD (the count+end method gets
clamped to the most recent day on Deriv). Dedupes, validates monotonic time, reports gaps.
Usage: python3 tick_harvest2.py SYM:DAYS [SYM:DAYS ...]
"""
import sys, gzip, time
from deriv_api import DerivWS

def harvest(ws, sym, days):
    now = int(time.time())
    t0 = now - days * 86400
    times, prices = [], []
    cursor = t0
    pip = None
    while cursor < now:
        r = ws.call({"ticks_history": sym, "start": cursor, "end": min(cursor + 4999, now),
                     "style": "ticks", "count": 5000, "adjust_start_time": 1})
        if "error" in r:
            print(sym, "error", r["error"].get("message")); break
        h = r.get("history", {})
        t, p = h.get("times", []), h.get("prices", [])
        pip = r.get("pip_size", pip)
        if t:
            times.extend(t); prices.extend(p)
            cursor = t[-1] + 1
        else:
            cursor += 5000
        time.sleep(0.12)
    # dedupe + sort
    seen = {}
    for tt, pp in zip(times, prices):
        seen[tt] = pp
    ts = sorted(seen)
    with gzip.open(f"../data/{sym}.csv.gz", "wt") as f:
        f.write(f"# pip_size={pip}\n")
        for tt in ts:
            f.write(f"{tt},{seen[tt]}\n")
    gaps = sum(1 for a, b in zip(ts, ts[1:]) if b - a > 10)
    print(f"{sym}: {len(ts)} unique ticks over {days}d  span={(ts[-1]-ts[0])/86400:.2f}d  gaps>10s: {gaps}", flush=True)

def main():
    ws = DerivWS(token="")
    for a in sys.argv[1:]:
        sym, dd = a.split(":")
        harvest(ws, sym, int(dd))
    ws.close()

if __name__ == "__main__":
    main()
