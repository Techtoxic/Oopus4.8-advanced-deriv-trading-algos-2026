import numpy as np, math

d = np.load("/tmp/jd100_fresh.npz")
ep, px, pip = d["ep"], d["px"], int(d["pip"])
x = np.round(px * 10**pip).astype(np.int64)
dig = x % 10
steps = np.diff(x)

# only use consecutive-second ticks (avoid gaps)
gap = np.diff(ep)
valid = gap == 1

# rolling sigma at each tick (trailing 1800, jump-filtered), causal
W = 1800; JUMP = 20
nj = (np.abs(steps) <= JUMP) & valid
sq = np.where(nj, steps.astype(float)**2, 0.0)
cn = nj.astype(float)
csq = np.cumsum(sq); ccn = np.cumsum(cn)
sig = np.full(len(steps), np.nan)
for i in range(W, len(steps)):
    s = csq[i] - csq[i-W]; c = ccn[i] - ccn[i-W]
    if c >= 200: sig[i-1] = math.sqrt(s / c)  # sigma known AT tick i (uses steps up to i)
# sigma[i] = trailing sigma known at tick index i+1... let's index carefully:
# steps[i] = x[i+1]-x[i]. Decision at tick index t uses steps up to t-1 -> sig_at[t]
sig_at = np.full(len(x), np.nan)
for t in range(W+1, len(x)):
    s = csq[t-1] - csq[t-1-W]; c = ccn[t-1] - ccn[t-1-W]
    if c >= 200: sig_at[t] = math.sqrt(s / c)

M = 1.800  # fresh executed OVER4/UNDER5
BE = 1.0 / M

def wilson_lb(k, n, z=2.576):
    if n == 0: return 0.0
    p = k / n
    den = 1 + z*z/n
    ctr = p + z*z/(2*n)
    rad = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (ctr - rad) / den

bands = [("A 4.0-4.3", 4.0, 4.3), ("B 3.5-3.9", 3.5, 3.9), ("C <3.5", 0.0, 3.5)]
print(f"fresh executed payout {M}, breakeven {BE*100:.2f}%")
print(f"sigma range in data: {np.nanmin(sig_at):.2f} .. {np.nanmax(sig_at):.2f}")

# decision: at tick t with digit dt, contract = UNDER5 if dt<=4 else OVER4 (window around current digit)
# outcome: next tick digit in winset, only when ep[t+1]-ep[t]==1
for name, lo, hi in bands:
    stats = {}
    for t in range(W+1, len(x)-1):
        if ep[t+1] - ep[t] != 1: continue
        s = sig_at[t]
        if not (lo <= s < hi) or np.isnan(s): continue
        dt_ = dig[t]; nd = dig[t+1]
        if dt_ <= 4: win = nd <= 4   # UNDER5 wins 0..4
        else:        win = nd >= 5   # OVER4 wins 5..9
        k = int(dt_)
        w_, n_ = stats.get(k, (0, 0))
        stats[k] = (w_ + int(win), n_ + 1)
    ntot = sum(n for _, n in stats.values())
    print(f"\n=== band {name}: n={ntot}")
    if ntot == 0: continue
    wtot = sum(w for w, _ in stats.values())
    print(f"  pooled p={wtot/ntot:.4f}  EV={(wtot/ntot)*M-1:+.4%}  wilson99lb={wilson_lb(wtot,ntot):.4f}")
    for k in sorted(stats):
        w_, n_ = stats[k]
        if n_ < 50: continue
        p = w_ / n_
        lb = wilson_lb(w_, n_)
        ct = "UNDER5" if k <= 4 else "OVER4 "
        flag = " CLEARS(LB)" if lb > BE else (" >BE(point)" if p > BE else "")
        print(f"  d={k} {ct} n={n_:6d} p={p:.4f} lb99={lb:.4f} EV={p*M-1:+.3%}{flag}")

# stress grids on band C pooled + best digits
print("\n=== payout stress (band C, pooled and per-digit point estimates) ===")
for g in [1.953, 1.886, 1.818, 1.800, 1.75, 1.70]:
    print(f"  grid {g}: breakeven {100/g:.2f}%")
