"""
Statistical battery across ALL collected instruments.
Tests the i.i.d.-uniform null on digits and the random-walk null on prices.
For each instrument we report whether ANY structure is detectable that could
beat a house edge. Writes a human-readable report to results/battery.txt.
"""
import math, os, sys
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common

DIGIT_INSTRUMENTS = ["1HZ100V", "R_100", "1HZ10V", "JD100"]
PRICE_INSTRUMENTS = ["1HZ100V", "R_100", "1HZ10V", "JD100", "stpRNG",
                     "CRASH1000", "BOOM1000", "CRASH500", "BOOM500"]

def acf(x, lag):
    xc = x - x.mean()
    return np.sum(xc[:-lag] * xc[lag:]) / np.sum(xc * xc)

def ljung_box(x, m):
    n = len(x); xc = x - x.mean(); denom = np.sum(xc * xc); s = 0.0
    for k in range(1, m + 1):
        rk = np.sum(xc[:-k] * xc[k:]) / denom
        s += rk * rk / (n - k)
    Q = n * (n + 2) * s
    return Q, 1 - stats.chi2.cdf(Q, m)

def digit_block(sym, out):
    e, p = common.load(sym)
    dec = common.infer_decimals(p)
    d = common.last_digit(p, dec)
    N = len(d)
    out.append(f"\n=== {sym}  (digits, n={N}, {dec} dp) ===")
    # chi-square uniformity
    counts = np.bincount(d, minlength=10)
    chi2, pv = stats.chisquare(counts)
    out.append(f"  digit uniformity  chi2={chi2:6.2f} df=9  p={pv:.3f}  {'OK uniform' if pv>0.05 else 'NON-UNIFORM'}")
    # markov
    trans = np.zeros((10, 10))
    for a, b in zip(d[:-1], d[1:]):
        trans[a, b] += 1
    chi2m, pm, dof, _ = stats.chi2_contingency(trans, correction=False)
    out.append(f"  markov indep      chi2={chi2m:6.1f} df={dof} p={pm:.3f}  {'memoryless' if pm>0.05 else 'HAS MEMORY'}")
    # parity autocorr + LB
    par = (d % 2).astype(float)
    band = 1.96 / math.sqrt(N)
    worst = max([abs(acf(par, L)) for L in [1, 2, 3, 5, 10, 20, 50]])
    Q, pq = ljung_box(par, 20)
    out.append(f"  parity ACF max|r|={worst:.4f} (band {band:.4f})  LjungBox(20) p={pq:.3f}")
    # best conditional cell for DIGITDIFF-style edge hunt:
    # find the entry digit with the highest P(next != entry); does its Wilson LB beat break-even?
    best = None
    for entry in range(10):
        idx = np.where(d[:-1] == entry)[0]
        nxt = d[idx + 1]
        n = len(idx); wins = int(np.sum(nxt != entry))
        ph = wins / n
        # Wilson 99% lower bound
        z = 2.576
        lo = (ph + z*z/(2*n) - z*math.sqrt((ph*(1-ph)+z*z/(4*n))/n)) / (1 + z*z/n)
        if best is None or lo > best[1]:
            best = (entry, lo, ph, n)
    out.append(f"  best DIGITDIFF conditional: entry={best[0]} P(diff)={best[2]:.4f} Wilson99lo={best[1]:.4f} (break-even ~0.917)  {'BEATS BE' if best[1] > 0.917 else 'below BE'}")

def price_block(sym, out):
    e, p = common.load(sym)
    ret = np.diff(np.log(p))  # log returns
    dp = np.diff(p)
    N = len(ret)
    out.append(f"\n--- {sym}  (price dynamics, n={N+1}) ---")
    # drift
    t, pt = stats.ttest_1samp(ret, 0.0)
    ann = ret.mean()
    out.append(f"  mean log-ret/tick={ann:+.2e}  drift t-test t={t:+.2f} p={pt:.3f}  {'no drift' if pt>0.05 else 'DRIFT'}")
    # up fraction
    up = np.mean(dp > 0); dn = np.mean(dp < 0); fl = np.mean(dp == 0)
    out.append(f"  P(up)={up:.4f} P(down)={dn:.4f} P(flat)={fl:.4f}")
    # returns autocorrelation (predictability of direction)
    band = 1.96 / math.sqrt(N)
    rs = {L: acf(ret, L) for L in [1, 2, 3, 5, 10]}
    flagged = [f"lag{L}={r:+.4f}{'*' if abs(r) > band else ''}" for L, r in rs.items()]
    Q, pq = ljung_box(ret, 10)
    out.append(f"  return ACF (band {band:.4f}): " + " ".join(flagged))
    out.append(f"  LjungBox(10) on returns p={pq:.3f}  {'no linear predictability' if pq>0.05 else 'PREDICTABLE'}")
    # sign autocorrelation (does up follow up?)
    sgn = np.sign(dp); sgn = sgn[sgn != 0]
    s1 = acf(sgn.astype(float), 1)
    out.append(f"  sign ACF lag1={s1:+.4f}  (momentum>0 / mean-revert<0; |.|>{band:.4f} notable)")

def main():
    out = []
    out.append("=" * 78)
    out.append("DERIV SYNTHETIC INDICES — STATISTICAL BATTERY")
    out.append("Null hypotheses: digits i.i.d.-uniform; price = random walk (no drift, no ACF)")
    out.append("=" * 78)
    out.append("\n########## DIGIT TESTS ##########")
    for s in DIGIT_INSTRUMENTS:
        digit_block(s, out)
    out.append("\n\n########## PRICE-DYNAMICS TESTS ##########")
    for s in PRICE_INSTRUMENTS:
        price_block(s, out)
    text = "\n".join(out)
    print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "..", "results", "battery.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    with open(rp, "w") as f:
        f.write(text + "\n")

if __name__ == "__main__":
    main()
