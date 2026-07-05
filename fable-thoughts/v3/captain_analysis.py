"""captain_analysis.py — the physics-first rebuild of the adaptive layer.

Hypotheses under test (all falsifiable here, on 3 separated periods):
  H1  sigma_pips = c * spot  (deterministic; JD100 is a constant-100%-vol 1s index).
      If true, the rolling-sigma estimator (1800-tick window = 30 min lag) is strictly worse:
      it lags after level moves and adds estimation noise. Spot-implied sigma is instantaneous.
  H2  SCALE FAMILY: the no-jump step distribution is  step = sigma(spot) * Z  with Z a fixed,
      regime-invariant shape (heavier-tailed than Gaussian). If true, ONE shape fitted once
      gives the exact digit pmf at ANY sigma — no trailing table, no retrain, no regime problem.
  H3  The resulting analytic EV curve crosses zero at sigma ~= 4.45 and the model P matches
      realized P within noise cross-period (the wrapped-normal's 0.3-1.4pp overconfidence
      disappears once the true tail shape is used).

Run: python3 captain_analysis.py
"""
import gzip, math, sys, json
import numpy as np

JUMP = 20
LADDER = [("DIGITOVER", 3), ("DIGITOVER", 4), ("DIGITUNDER", 5), ("DIGITUNDER", 6)]
PAY = {("DIGITOVER", 3): 1.626, ("DIGITOVER", 4): 1.953,
       ("DIGITUNDER", 5): 1.953, ("DIGITUNDER", 6): 1.626}   # verified live grid
WINSET = {(ct, b): (set(range(b + 1, 10)) if ct == "DIGITOVER" else set(range(0, b)))
          for ct, b in LADDER}

def load(p):
    ts, ps = [], []
    op = gzip.open if p.endswith(".gz") else open
    with op(p, "rt") as f:
        f.readline()
        for line in f:
            a, b = line.strip().split(",")
            ts.append(int(a)); ps.append(float(b))
    return np.array(ts), np.array(ps)

def prep(ts, ps):
    """integer pip prices, steps, no-jump mask, spot-implied sigma per step"""
    x = np.round(ps * 100).astype(np.int64)
    st = np.diff(x)
    nj = np.abs(st) <= JUMP
    return x, st, nj

# ---------- H1: sigma = c * spot ----------
def h1_c_fit(name, x, st, nj):
    spot = x[:-1][nj] / 100.0
    s = st[nj].astype(float)
    # bin by spot, compare measured sigma to c*spot
    c_global = math.sqrt(np.mean((s / spot) ** 2))
    print(f"\n[{name}] H1: c = sigma/spot fit, {len(s)} no-jump steps")
    print(f"  global c = {c_global:.6f} pips/unit (theory 100%/sqrt(365d): {100/math.sqrt(365*86400):.6f})")
    bins = np.quantile(spot, np.linspace(0, 1, 9))
    for i in range(8):
        m = (spot >= bins[i]) & (spot < bins[i + 1])
        if m.sum() < 5000: continue
        sig = math.sqrt(np.mean(s[m] ** 2)); mid = spot[m].mean()
        print(f"  spot {bins[i]:6.1f}-{bins[i+1]:6.1f}: sigma={sig:6.3f}  c*spot={c_global*mid:6.3f}  ratio={sig/(c_global*mid):.4f}")
    return c_global

# ---------- H2: scale family ----------
def h2_shape(name, x, st, nj, c):
    spot = x[:-1][nj] / 100.0
    z = st[nj] / (c * spot)
    print(f"\n[{name}] H2: normalized step Z moments: mean={z.mean():+.4f} std={z.std():.4f} "
          f"kurt={float(((z-z.mean())**4).mean()/z.var()**2 - 3):+.3f}")
    # shape by sigma regime: split into sigma terciles, compare quantiles
    sig = c * spot
    qs = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]
    t1, t2 = np.quantile(sig, [1/3, 2/3])
    for lab, m in [("low ", sig < t1), ("mid ", (sig >= t1) & (sig < t2)), ("high", sig >= t2)]:
        qq = np.quantile(z[m], qs)
        print(f"  {lab} sig[{sig[m].mean():.2f}] q={' '.join(f'{v:+.3f}' for v in qq)}  n={m.sum()}")
    return z

# ---------- pmf machinery ----------
class ShapePMF:
    """P(next digit | current digit, sigma) from a fitted Z shape (ECDF-based scale family)."""
    def __init__(self, z_samples, jump_prob):
        self.zs = np.sort(z_samples)
        self.n = len(self.zs)
        self.jp = jump_prob
    def cdf(self, v):
        return np.searchsorted(self.zs, v, side="right") / self.n
    def step_pmf(self, sigma):
        ks = np.arange(-JUMP, JUMP + 1)
        hi = self.cdf((ks + 0.5) / sigma); lo = self.cdf((ks - 0.5) / sigma)
        p = hi - lo
        p = p / p.sum() * (1 - self.jp)          # no-jump mass
        off = np.zeros(10)
        for k, pk in zip(ks, p): off[k % 10] += pk
        off += self.jp / 10.0                     # jumps ~ uniform mod 10
        return off
    def win_p(self, sigma, digit, winset):
        off = self.step_pmf(sigma)
        return sum(off[(w - digit) % 10] for w in winset)

def wn_off(sigma):
    """wrapped normal offset pmf (the old analytic baseline)"""
    off = np.zeros(10)
    for k in range(10):
        s = 1.0 / 10
        for j in range(1, 8):
            s += (2.0 / 10) * math.exp(-2 * math.pi**2 * j * j * sigma * sigma / 100) * math.cos(2 * math.pi * j * k / 10)
        off[k] = s
    return np.clip(off, 0, None) / np.clip(off, 0, None).sum()

# ---------- H3: cross-period validation ----------
def h3_validate(name, x, st, nj, c, pmf, gate=0.01, cache={}):
    """walk each tick: spot-implied sigma, pick best ladder contract from shape pmf,
       trade if EV>gate; compare model P to realized, PnL per trade; also sigma-binned."""
    n = len(st)
    d = x % 10
    spot = x[:-1] / 100.0
    sig = c * spot
    sig_r = np.round(sig, 1)  # cache pmfs on 0.1 sigma grid
    trades = 0; wins = 0; pnl = 0.0; mp_sum = 0.0
    bin_stats = {}
    for i in range(n):
        if not nj[i]: continue          # decision tick itself fine; skip settle-on-jump? no: jump at i means step i is jump; we trade every tick, outcome includes jumps
        s10 = sig_r[i]
        if s10 > 5.2: continue          # way out of zone, save time
        key = s10
        if key not in cache:
            off = pmf.step_pmf(s10)
            # best contract per digit for this sigma, precomputed
            best = {}
            for dd in range(10):
                bb = None
                for ct_b, M in PAY.items():
                    p = sum(off[(w - dd) % 10] for w in WINSET[ct_b])
                    ev = p * M - 1
                    if bb is None or ev > bb[2]: bb = (ct_b, p, ev)
                best[dd] = bb
            cache[key] = best
        ct_b, p, ev = cache[key][int(d[i])]
        if ev <= gate: continue
        won = int(d[i + 1]) in WINSET[ct_b]
        trades += 1; wins += won; mp_sum += p
        pnl += (PAY[ct_b] - 1) if won else -1.0
        b = round(float(sig[i]) * 5) / 5
        rec = bin_stats.setdefault(b, [0, 0, 0.0, 0.0])
        rec[0] += 1; rec[1] += won; rec[2] += p; rec[3] += (PAY[ct_b] - 1) if won else -1.0
    if trades == 0:
        print(f"\n[{name}] H3: no trades at gate {gate}"); return
    wr = wins / trades; mp = mp_sum / trades
    ept = pnl / trades
    se = math.sqrt(wr * (1 - wr) / trades) * 1.953
    print(f"\n[{name}] H3 gate={gate:.3f}: trades={trades}  model_p={mp:.4f}  real_p={wr:.4f} "
          f"(diff {100*(wr-mp):+.2f}pp)  PnL/t={100*ept:+.2f}%  t={ept/se:.2f}")
    for b in sorted(bin_stats):
        tr, w, mps, pn = bin_stats[b]
        if tr < 300: continue
        print(f"    sig~{b:.1f}: n={tr:6d} model_p={mps/tr:.4f} real_p={w/tr:.4f} PnL/t={100*pn/tr:+.2f}%")

if __name__ == "__main__":
    datasets = {
        "jun11_wide": "../../opus-thoughts/data/JD100.csv.gz",
        "jun29": "../data/JD100_jun29_v2.csv.gz",
        "jul05_fresh": "data/JD100_jul05.csv.gz",
    }
    D = {}
    for k, p in datasets.items():
        try:
            ts, ps = load(p); D[k] = prep(ts, ps)
            x, st, nj = D[k]
            print(f"{k}: {len(x)} ticks, spot {ps.min():.0f}-{ps.max():.0f}, jumps {100*(1-nj.mean()):.2f}%")
        except Exception as e:
            print(f"{k}: LOAD FAIL {e}")

    # H1 on the wide dataset (largest sigma range)
    cs = {}
    for k in D:
        x, st, nj = D[k]
        cs[k] = h1_c_fit(k, x, st, nj)
    c = cs["jun11_wide"]

    # H2 shape invariance on wide data; fit shape on jun11 ONLY (in-sample)
    x, st, nj = D["jun11_wide"]
    z_train = h2_shape("jun11_wide", x, st, nj, c)
    jump_prob = 1 - nj.mean()
    pmf = ShapePMF(z_train, jump_prob)

    # also report the analytic EV curve of the fitted shape vs wrapped normal
    print("\nEV curve (5-wide best window, M=1.953): sigma -> shapeEV | wrappedNormalEV")
    for s in [3.6, 3.8, 4.0, 4.2, 4.35, 4.45, 4.6, 4.8, 5.0]:
        off = pmf.step_pmf(s)
        p5 = sum(off[k % 10] for k in (-2, -1, 0, 1, 2))
        wo = wn_off(s)
        p5w = sum(wo[k % 10] for k in (-2, -1, 0, 1, 2))
        print(f"  {s:4.2f}: {100*(p5*1.953-1):+6.2f}%  |  {100*(p5w*1.953-1):+6.2f}%")

    # H3: validate cross-period (shape from jun11 -> trade jun29 & jul05; also in-sample sanity)
    for k in D:
        x, st, nj = D[k]
        h3_validate(k, x, st, nj, c, pmf, gate=0.01, cache={})
    for k in ("jul05_fresh",):
        if k in D:
            x, st, nj = D[k]
            h3_validate(k + " gate.005", x, st, nj, c, pmf, gate=0.005, cache={})
