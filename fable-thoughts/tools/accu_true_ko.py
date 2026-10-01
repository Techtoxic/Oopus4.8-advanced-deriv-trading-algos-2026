"""Correct ACCU pricing: KO iff |step|/prev_spot > tick_size_barrier (tsb, the shortcode
field). Bands per (symbol,g) from live probes; tsb scales ~inversely with spot so history
priced at quoted rel band is biased — use tsb with prev-spot normalization everywhere.
Also R_100 / 1HZ100V / 1HZ10V with their larger tsb."""
import numpy as np
# tsb per (sym,g) — measured: g=0.03 row live; tsb scales with band, band table /0.03 ref
TSB = {
 "CRASH500":  {"0.01":4.9222e-06*0.00055/0.00049,"0.02":4.9222e-06*0.00052/0.00049,
               "0.03":4.9222e-06,"0.04":4.9222e-06*0.00047/0.00049,"0.05":4.9222e-06*0.00045/0.00049},
 "CRASH1000": {"0.03":2.447e-06},
 "BOOM500":   {"0.03":4.9295e-06},
 "BOOM1000":  {"0.01":2.4506e-06*0.00027/0.00025,"0.02":2.4506e-06*0.00026/0.00025,
               "0.03":2.4506e-06,"0.04":2.4506e-06*0.00023/0.00025,"0.05":2.4506e-06*0.00023/0.00025},
 "R_100":     {"0.03":5.369e-04},   # from longcode 0.05369%
 "1HZ100V":   {"0.03":3.7967e-04},
 "1HZ10V":    {"0.03":3.7967e-05},
}
MAXT = {"0.01":250,"0.02":135,"0.03":90,"0.04":65,"0.05":55}
for SYM, gs in TSB.items():
    d = np.load(f"/tmp/ticks/{SYM}.npz")
    ep, px = d["ep"], d["px"]
    iv = int(np.median(np.diff(ep))); m = np.diff(ep)==iv
    rel = np.abs(np.diff(px))/px[:-1]
    rel = rel[m]
    for g, tsb in gs.items():
        ko = rel > tsb
        runs = np.diff(np.where(np.concatenate(([True],ko,[True])))[0])-1
        tot = len(rel)
        outs=[]
        for N in (10, 50, MAXT[g]):
            S = np.maximum(runs-N+1,0).sum()/tot
            outs.append(f"N{N}:{S*(1+float(g))**N-1:+.2%}")
        print(f"{SYM:9s} g={g} tsb={tsb:.3e} h={ko.mean():.5f} " + " ".join(outs))
