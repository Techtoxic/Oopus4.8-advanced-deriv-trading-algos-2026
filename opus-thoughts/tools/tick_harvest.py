"""Deep tick harvester: pages ticks_history backwards, saves data/{sym}.csv.gz (epoch,price).
Usage: python3 tick_harvest.py JD100:200000 R_100:150000 ...
"""
import sys, gzip, time
from deriv_api import DerivWS

def main():
    jobs = []
    for a in sys.argv[1:]:
        sym, n = a.split(":")
        jobs.append((sym, int(n)))
    ws = DerivWS(token="")
    for sym, total in jobs:
        t0 = time.time()
        try:
            times, prices, pip = ws.history_paged(sym, total, sleep=0.30,
                progress=lambda k, s=sym: print(f"{s}: {k}", flush=True) if k % 25000 == 0 else None)
        except Exception as e:
            print(f"{sym} FAILED: {e}", flush=True); continue
        with gzip.open(f"../data/{sym}.csv.gz", "wt") as f:
            f.write(f"# pip_size={pip}\n")
            for t, p in zip(times, prices):
                f.write(f"{t},{p}\n")
        print(f"{sym}: saved {len(times)} ticks ({time.time()-t0:.0f}s) pip={pip}", flush=True)
    ws.close()

if __name__ == "__main__":
    main()
