#!/usr/bin/env python3
"""Independent verification battery — synthetics + real markets on Deriv."""
import gzip, json, math
import numpy as np
from scipy import stats

D = "/home/work/deriv/data"
R = "/home/work/deriv/results"
out = []
def p(*a):
    s = " ".join(str(x) for x in a)
    out.append(s); print(s, flush=True)

# ---------- payouts ----------
payouts = json.load(open(f"{D}/payouts_live.json"))
p("=" * 70); p("LIVE PAYOUTS / HOUSE EDGE (measured this session)"); p("=" * 70)
for q in payouts:
    if "error" in q:
        p(f"{q['symbol']:>10} {q['contract_type']:>9} d={q.get('duration')}{q.get('duration_unit','t')} ERROR: {q['error']}")
    else:
        ratio = q["payout_ratio"]
        p(f"{q['symbol']:>10} {q['contract_type']:>9} b={q.get('barrier','-'):>2} d={q['duration']}{q['duration_unit']} payout_x={ratio:.4f} req_winrate={1/ratio:.4f}")

# ---------- synthetics battery ----------
def digits(prices, pip):
    return np.array([int(round(x * 10**pip)) % 10 for x in prices])

p(""); p("=" * 70); p("SYNTHETIC TICK BATTERY (30k fresh ticks each)"); p("=" * 70)
for sym in ["1HZ100V", "R_100", "1HZ10V", "R_50", "1HZ75V"]:
    d = json.load(gzip.open(f"{D}/{sym}_ticks.json.gz", "rt"))
    pr = np.array(d["prices"]); pip = int(d["pip_size"])
    dg = digits(pr, pip)
    n = len(dg)
    # uniformity
    cnt = np.bincount(dg, minlength=10)
    chi2, puni = stats.chisquare(cnt)
    # lag-1 transition
    T = np.zeros((10, 10))
    for a, b in zip(dg[:-1], dg[1:]): T[a, b] += 1
    exp = T.sum(1, keepdims=True) @ (T.sum(0, keepdims=True) / T.sum())
    chi2t = ((T - exp) ** 2 / np.where(exp == 0, 1, exp)).sum()
    pt = 1 - stats.chi2.cdf(chi2t, 81)
    # lag-2
    T2 = np.zeros((10, 10))
    for a, b in zip(dg[:-2], dg[2:]): T2[a, b] += 1
    exp2 = T2.sum(1, keepdims=True) @ (T2.sum(0, keepdims=True) / T2.sum())
    chi2t2 = ((T2 - exp2) ** 2 / np.where(exp2 == 0, 1, exp2)).sum()
    pt2 = 1 - stats.chi2.cdf(chi2t2, 81)
    # returns drift + ACF
    ret = np.diff(np.log(pr))
    tdrift, pdrift = stats.ttest_1samp(ret, 0)
    # Ljung-Box lag10
    acfs = [np.corrcoef(ret[:-k], ret[k:])[0, 1] for k in range(1, 11)]
    lb = n * (n + 2) * sum(a * a / (n - k - 1) for k, a in enumerate(acfs))
    plb = 1 - stats.chi2.cdf(lb, 10)
    # parity runs test
    par = dg % 2
    runs = 1 + (par[1:] != par[:-1]).sum()
    n1, n0 = par.sum(), n - par.sum()
    mu = 2 * n1 * n0 / n + 1
    var = 2 * n1 * n0 * (2 * n1 * n0 - n) / (n * n * (n - 1))
    zruns = (runs - mu) / math.sqrt(var)
    p(f"{sym:>8}: uniform_p={puni:.3f} lag1_p={pt:.3f} lag2_p={pt2:.3f} drift_p={pdrift:.3f} ljungbox_p={plb:.3f} runs_z={zruns:+.2f}")
    # conditional: best digit-cell exploit check (DIGITDIFF needs win>1/1.0x ratio)
    best = 0; bi = None
    for a in range(10):
        idx = np.where(dg[:-1] == a)[0]
        nxt = dg[idx + 1]
        for b in range(10):
            wr = (nxt != b).mean()  # differ win rate conditioned on prev digit
            lo = wr - 2.576 * math.sqrt(wr * (1 - wr) / len(nxt))
            if lo > best: best, bi = lo, (a, b, wr, len(nxt))
    p(f"          best conditional DIGITDIFF Wilson99-lo={best:.4f} (need >{1/1.0958:.4f} at 1.0958x) cell={bi}")

# ---------- real markets ----------
p(""); p("=" * 70); p("REAL MARKETS — PREDICTABILITY vs MEASURED BINARY EDGE"); p("=" * 70)
def load_candles(sym):
    d = json.load(gzip.open(f"{D}/{sym}_candles.json.gz", "rt"))
    c = d["candles"]
    o = np.array([x["open"] for x in c]); h = np.array([x["high"] for x in c])
    l = np.array([x["low"] for x in c]); cl = np.array([x["close"] for x in c])
    t = np.array([x["epoch"] for x in c])
    return t, o, h, l, cl

req = {}
for q in payouts:
    if "payout_ratio" in q and q["symbol"].startswith(("frx", "cry")) and q["contract_type"] == "CALL":
        req[(q["symbol"], f"{q['duration']}{q['duration_unit']}")] = 1 / q["payout_ratio"]

for sym in ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxXAUUSD", "frxXAGUSD", "cryBTCUSD", "cryETHUSD"]:
    t, o, h, l, cl = load_candles(sym)
    ret1 = np.diff(np.log(cl))
    n = len(ret1)
    # horizon predictability: sign autocorrelation at 1,5,15,60 min
    p(f"--- {sym}  ({n} 1m bars)  req win rates: " +
      " ".join(f"{k[1]}:{v:.3f}" for k, v in req.items() if k[0] == sym))
    for hz in [1, 5, 15, 60]:
        # aggregate returns over hz
        m = (n // hz) * hz
        rh = np.log(cl[hz::hz] / cl[:-hz:hz][:len(cl[hz::hz])]) if hz > 1 else ret1
        rh = rh[~np.isnan(rh)]
        if len(rh) < 100: continue
        nz = rh[rh != 0]
        # momentum: P(next same sign | prev sign)
        s = np.sign(nz)
        same = (s[1:] == s[:-1]).mean()
        ac1 = np.corrcoef(rh[:-1], rh[1:])[0, 1]
        nn = len(s) - 1
        se = math.sqrt(0.25 / nn)
        z = (same - 0.5) / se
        p(f"   h={hz:>3}m: P(same sign)={same:.4f} (z={z:+.2f}, n={nn})  ret_ac1={ac1:+.4f}")
    # hour-of-day drift (for session bias)
    hrs = ((t[1:] // 3600) % 24).astype(int)
    best_h = sorted(range(24), key=lambda H: abs(np.nan_to_num(ret1[hrs == H].mean())) * -1)[:3]
    for H in best_h:
        r = ret1[hrs == H]
        if len(r) > 200:
            tt, pp = stats.ttest_1samp(r, 0)
            p(f"   hour {H:02d} UTC: mean1m={r.mean():+.2e} t={tt:+.2f} p={pp:.3f} n={len(r)}")

with open(f"{R}/battery_results.txt", "w") as f:
    f.write("\n".join(out))
print("SAVED")
