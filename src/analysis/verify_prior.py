"""
Independent verification of the prior repo's no-edge conclusion.
Re-derives the headline statistics from scratch (not reusing their code).

Usage:
    python verify_prior.py [path]
If no path is given it falls back to this repo's fresh data/1HZ100V.json.gz so it
runs out of the box; point it at the prior repo's ticks_v100_1s.json to reproduce
their exact 86,383-tick numbers.
"""
import gzip, json, math, os, sys
import numpy as np
from scipy import stats

default = os.path.join(os.path.dirname(__file__), "..", "..", "data", "1HZ100V.json.gz")
path = sys.argv[1] if len(sys.argv) > 1 else default
opener = gzip.open if path.endswith(".gz") else open
with opener(path, "rt") as f:
    ticks = json.load(f)
prices = np.array([p for _, p in ticks], dtype=float)
digits = np.array([int(round(p * 100)) % 10 for p in prices])
N = len(digits)
print(f"N = {N} ticks, price range [{prices.min()}, {prices.max()}]")

# T1: digit frequency chi-square vs uniform
counts = np.bincount(digits, minlength=10)
chi2, p = stats.chisquare(counts)
print(f"\n[T1] digit counts = {counts.tolist()}")
print(f"     chi2={chi2:.2f} df=9 p={p:.3f}  (uniform if p>0.05)")

# T2: autocorrelation of parity + Ljung-Box
parity = (digits % 2).astype(float)
par = parity - parity.mean()
def acf(x, lag):
    return np.sum(x[:-lag]*x[lag:]) / np.sum(x*x)
band = 1.96/math.sqrt(N)
acfs = {L: acf(par, L) for L in [1,2,3,5,10,20,50,100]}
print(f"\n[T2] parity ACF (95% band +-{band:.4f}):")
for L,a in acfs.items():
    flag = "" if abs(a) < band else "  <-- exceeds band"
    print(f"     lag {L:3d}: {a:+.4f}{flag}")
# Ljung-Box
def ljung_box(x, m):
    n=len(x); xc=x-x.mean(); denom=np.sum(xc*xc); s=0.0
    for k in range(1,m+1):
        rk=np.sum(xc[:-k]*xc[k:])/denom
        s+=rk*rk/(n-k)
    Q=n*(n+2)*s; return Q, 1-stats.chi2.cdf(Q,m)
for m in [10,20,50]:
    Q,pv=ljung_box(parity,m); print(f"     Ljung-Box m={m}: Q={Q:.1f} p={pv:.3f}")

# T3: Markov-0 test (transition matrix independence)
trans = np.zeros((10,10))
for a,b in zip(digits[:-1], digits[1:]):
    trans[a,b]+=1
chi2_m, p_m, dof, _ = stats.chi2_contingency(trans, correction=False)
print(f"\n[T3] Markov transition independence: chi2={chi2_m:.1f} dof={dof} p={p_m:.3f}")

# T4: runs test on parity
n1=int(parity.sum()); n0=N-n1
runs=1+int(np.sum(parity[1:]!=parity[:-1]))
mu=2*n1*n0/N+1
var=(mu-1)*(mu-2)/(N-1)
z=(runs-mu)/math.sqrt(var)
print(f"\n[T4] runs test: runs={runs} expected={mu:.1f} z={z:+.2f} p={2*(1-stats.norm.cdf(abs(z))):.3f}")

# T8: price increment distribution
dp = np.diff(prices)*100  # in cents
print(f"\n[T8] dprice cents: mean={dp.mean():+.3f} std={dp.std():.2f} min={dp.min():.0f} max={dp.max():.0f}")
# drift t-test
t,pt = stats.ttest_1samp(dp,0.0)
print(f"     drift t-test: t={t:+.2f} p={pt:.3f} (no drift if p>0.05)")

# T12: empirical win rates / EV for key contracts (lag-1 marginal)
payouts = {"Over0/Under9/Differs":1.096, "Over1/Under8":1.232, "Over4/Under5/Even":1.953,
           "Over7/Under2":4.717, "Over8/Matches":8.929}
def winrate(mask): return mask.mean()
d=digits
contracts = {
    "Over0  (d>0)":   d>0,
    "Under9 (d<9)":   d<9,
    "Differs(next!=cur)": d[1:]!=d[:-1],
    "Even   (d%2==0)": d%2==0,
    "Over4  (d>4)":   d>4,
    "Over7  (d>7)":   d>7,
    "Matches(next==cur)": d[1:]==d[:-1],
}
pay = {"Over0  (d>0)":1.096,"Under9 (d<9)":1.096,"Differs(next!=cur)":1.096,
       "Even   (d%2==0)":1.953,"Over4  (d>4)":1.953,"Over7  (d>7)":4.717,"Matches(next==cur)":8.929}
print(f"\n[T12] empirical win rate & EV per $1 (payout from prior repo):")
print(f"     {'contract':22s} {'win_rate':>9s} {'breakeven':>10s} {'EV/$':>8s}")
for name,mask in contracts.items():
    wr=mask.mean(); P=pay[name]; be=1.0/P; ev=wr*P-1.0
    print(f"     {name:22s} {wr:9.4f} {be:10.4f} {ev:+8.4f}")
