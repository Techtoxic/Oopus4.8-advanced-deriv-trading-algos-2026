"""universe_scan.py — screen EVERY digit-capable symbol for the lattice-concentration zone.

The digit edge exists when the per-tick no-jump step stdev in pips (sigma_pips) is small enough
that the wrapped-normal concentration beats the ~2.4% house margin. Analytically:
    EV_bestwindow(sigma) ~= 0.647 * M * exp(-sigma^2 * (2*pi^2/100)) + (M/2 - 1),  M~=1.953
zero-crossing at sigma ~= 4.45. Any symbol below that is tradeable NOW; the scan also reports
spot-implied sigma slope c = sigma/spot (deterministic for a const-vol index) so we know each
symbol's tradeable spot range.

Run: python3 universe_scan.py
"""
import math, sys, time
sys.path.insert(0, "../tools")
from deriv_api import DerivWS

WSIG = 2000
JUMP = 20

def sigma_of(prices, pip):
    scale = 10 ** pip
    xs = [round(float(p) * scale) for p in prices]
    steps = [xs[i+1] - xs[i] for i in range(len(xs)-1)]
    nj = [s for s in steps if abs(s) <= JUMP]
    if len(nj) < 300: return None, len(nj), len(steps)
    return math.sqrt(sum(s*s for s in nj)/len(nj)), len(nj), len(steps)

def main():
    ws = DerivWS(token="")
    syms = ws.active_symbols()
    # candidates: anything synthetic with ticks (digit contracts live on synthetics)
    cands = [s for s in syms if s.get("market") in ("synthetic_index",) ]
    print(f"{len(cands)} synthetic symbols")
    rows = []
    for s in sorted(cands, key=lambda x: x["symbol"]):
        sym = s["symbol"]
        try:
            r = ws.ticks_history(sym, count=WSIG)
            if "history" not in r: continue
            h = r["history"]; pr = h["prices"]; ts = h["times"]
            if len(pr) < 400: continue
            pip = int(r["pip_size"])
            sig, nnj, nst = sigma_of(pr, pip)
            if sig is None: continue
            iv = (ts[-1] - ts[0]) / (len(ts) - 1)
            spot = float(pr[-1])
            c = sig / (spot * 10**pip) if spot else 0
            # analytic best-window EV at this sigma (5-wide centered, M=1.953)
            q = math.exp(-2 * math.pi**2 * sig**2 / 100)
            ev = 0.647 * 1.953 * q + (1.953/2 - 1)
            rows.append((sig, sym, s["display_name"], spot, pip, iv, ev, c, nnj/nst))
        except Exception as e:
            print(f"  {sym}: {e}")
        time.sleep(0.1)
    rows.sort()
    print(f"\n{'sigma':>6} {'symbol':<12} {'spot':>12} {'pip':>3} {'tick_s':>6} {'EV_win5':>8} {'c=sig/spot_pips':>16} {'nojump%':>8}  name")
    for sig, sym, name, spot, pip, iv, ev, c, njf in rows:
        zone = " <<< IN ZONE" if sig <= 4.45 else ""
        print(f"{sig:6.2f} {sym:<12} {spot:>12.4f} {pip:>3} {iv:>6.2f} {ev*100:>+7.2f}% {c:>16.6f} {njf*100:>7.1f}%  {name}{zone}")
    ws.close()

if __name__ == "__main__":
    main()
