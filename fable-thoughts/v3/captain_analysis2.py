"""captain_analysis2.py — fix the pmf (discrete Gaussian, continuity-corrected) and hunt the
non-sigma structure: same sigma, different realized window mass across periods. Why?

Checks:
  A. Empirical offset pmf per sigma-bin per dataset vs discrete-Gaussian prediction.
     -> window-5 mass (offsets -2..2) empirical vs model, with binomial SE.
  B. Step autocorrelation (lag 1..5) per dataset — is there momentum/reversion the iid model misses?
  C. Mean step (drift) per dataset — phase shift of the concentration center?
  D. Conditional: window mass conditioned on previous step sign/size (exploitable state?)
"""
import gzip, math
import numpy as np

JUMP = 20
def load(p):
    ts, ps = [], []
    with gzip.open(p, "rt") as f:
        f.readline()
        for line in f:
            a, b = line.strip().split(","); ts.append(int(a)); ps.append(float(b))
    return np.array(ts), np.array(ps)

def phi(v): return 0.5 * (1 + math.erf(v / math.sqrt(2)))

def dg_off(sigma, mu=0.0):
    """discrete Gaussian step pmf folded mod 10 (continuity corrected)"""
    off = np.zeros(10)
    for k in range(-JUMP, JUMP + 1):
        p = phi((k + 0.5 - mu) / sigma) - phi((k - 0.5 - mu) / sigma)
        off[k % 10] += p
    return off / off.sum()

W5 = [(-2) % 10, (-1) % 10, 0, 1, 2]

def analyze(name, path, c):
    ts, ps = load(path)
    x = np.round(ps * 100).astype(np.int64)
    st = np.diff(x); nj = np.abs(st) <= JUMP
    spot = x[:-1] / 100.0
    sig = c * spot
    off = st % 10
    print(f"\n===== {name}: {len(st)} steps, spot {ps.min():.0f}-{ps.max():.0f} =====")
    # C: drift
    print(f"  drift: mean step = {st[nj].mean():+.4f} pips (theory ~ -{(c*spot.mean())**2/2/ (spot.mean()*100) *100:.5f})")
    # B: autocorrelation
    s = st[nj].astype(float); s = s - s.mean()
    ac = [float(np.dot(s[:-k], s[k:]) / np.dot(s, s)) for k in (1, 2, 3, 4, 5)]
    print(f"  step autocorr lag1-5: {' '.join(f'{a:+.4f}' for a in ac)}  (SE~{1/math.sqrt(len(s)):.4f})")
    # A: per sigma-bin offset concentration
    print(f"  {'sig bin':>9} {'n':>7} {'emp w5':>8} {'model w5':>8} {'diff pp':>8} {'SE pp':>6}")
    for lo in np.arange(3.9, 4.7, 0.1):
        m = nj & (sig >= lo) & (sig < lo + 0.1)
        n = int(m.sum())
        if n < 3000: continue
        emp = np.zeros(10)
        for o in off[m]: emp[o] += 1
        emp /= n
        w5e = emp[W5].sum()
        model = dg_off((lo + 0.05) )
        w5m = model[W5].sum()
        se = math.sqrt(w5e * (1 - w5e) / n)
        print(f"  {lo:.1f}-{lo+0.1:.1f} {n:>7} {w5e:>8.4f} {w5m:>8.4f} {100*(w5e-w5m):>+8.2f} {100*se:>6.2f}")
    # full offset pmf at the dominant bin
    m = nj & (sig >= 4.1) & (sig < 4.3)
    if m.sum() > 5000:
        emp = np.zeros(10)
        for o in off[m]: emp[o] += 1
        emp /= m.sum()
        model = dg_off(4.2)
        print("  offsets @sig4.1-4.3: emp  " + " ".join(f"{v:.4f}" for v in emp))
        print("                       model" + " ".join(f" {v:.4f}" for v in model))
    # D: conditional on previous step
    m = nj.copy(); m[0] = False
    prev = np.roll(st, 1)
    pm = np.abs(prev) <= JUMP
    base = m & pm
    for lab, cond in [("prev>+4", base & (prev > 4)), ("prev<-4", base & (prev < -4)),
                      ("|prev|<=2", base & (np.abs(prev) <= 2))]:
        mm = cond & (sig >= 4.0) & (sig < 4.4)
        n = int(mm.sum())
        if n < 3000: continue
        w5e = np.isin(off[mm], W5).mean()
        se = math.sqrt(w5e * (1 - w5e) / n)
        print(f"  cond {lab:>9} @sig4.0-4.4: n={n:6d} w5={w5e:.4f} (+-{se:.4f})")
    return None

if __name__ == "__main__":
    c = 0.017807
    analyze("jun11_wide", "../../opus-thoughts/data/JD100.csv.gz", c)
    analyze("jun29", "../data/JD100_jun29_v2.csv.gz", c)
    analyze("jul05_fresh", "data/JD100_jul05.csv.gz", c)
