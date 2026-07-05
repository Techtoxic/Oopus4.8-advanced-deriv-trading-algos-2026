"""captain_backtest.py — walk-forward of the FINAL v3 rule on all periods, day by day.

THE RULE (zero fitted parameters, zero retraining, instantaneous regime adaptation):
  sigma  = c * spot          (c = 100%/sqrt(365*86400) = 0.017807, verified on 1.38M ticks)
  pmf    = discrete Gaussian (continuity-corrected) folded mod 10
  pick   = max-EV contract among the wide ladder OVER3/OVER4/UNDER5/UNDER6, conditioned on digit
  trade  iff (p - haircut) * M - 1 > gate   and sigma <= ceiling (safety)
Also prints the per-digit trigger table: max spot at which each digit's best contract clears gate.
"""
import gzip, math
import numpy as np

JUMP = 20
C = 0.017807
LADDER = [("DIGITOVER", 3), ("DIGITOVER", 4), ("DIGITUNDER", 5), ("DIGITUNDER", 6)]
PAY = {("DIGITOVER", 3): 1.626, ("DIGITOVER", 4): 1.953,
       ("DIGITUNDER", 5): 1.953, ("DIGITUNDER", 6): 1.626}
WINSET = {(ct, b): (frozenset(range(b + 1, 10)) if ct == "DIGITOVER" else frozenset(range(0, b)))
          for ct, b in LADDER}

def phi(v): return 0.5 * (1 + math.erf(v / math.sqrt(2)))

def dg_off(sigma):
    off = np.zeros(10)
    for k in range(-JUMP, JUMP + 1):
        off[k % 10] += phi((k + 0.5) / sigma) - phi((k - 0.5) / sigma)
    return off / off.sum()

def best_table(sig_grid):
    """(sigma_idx, digit) -> (ct,b,p)"""
    T = {}
    for si, s in enumerate(sig_grid):
        off = dg_off(s)
        for d in range(10):
            bb = None
            for cb, M in PAY.items():
                p = sum(off[(w - d) % 10] for w in WINSET[cb])
                ev = p * M - 1
                if bb is None or ev > bb[3]: bb = (cb[0], cb[1], p, ev)
            T[(si, d)] = bb
    return T

def load(p):
    ts, ps = [], []
    with gzip.open(p, "rt") as f:
        f.readline()
        for line in f:
            a, b = line.strip().split(","); ts.append(int(a)); ps.append(float(b))
    return np.array(ts), np.array(ps)

def run(name, path, gate=0.01, haircut=0.001, ceiling=4.6):
    ts, ps = load(path)
    x = np.round(ps * 100).astype(np.int64)
    st = np.diff(x); nj = np.abs(st) <= JUMP
    d = x % 10
    sig = C * x[:-1] / 100.0
    grid = np.arange(3.0, 5.01, 0.01)
    T = best_table(grid)
    si = np.clip(np.round((sig - 3.0) / 0.01).astype(int), 0, len(grid) - 1)
    trades = wins = 0; pnl = 0.0; mp = 0.0; day_pnl = {}
    for i in range(len(st) - 1):
        s = sig[i]
        if s > ceiling or s < 3.0: continue
        ct, b, p, ev = T[(si[i], int(d[i]))]
        if (p - haircut) * PAY[(ct, b)] - 1 <= gate: continue
        won = int(d[i + 1]) in WINSET[(ct, b)]
        trades += 1; wins += won; mp += p
        gain = (PAY[(ct, b)] - 1) if won else -1.0
        pnl += gain
        dd = ts[i] // 86400
        r = day_pnl.setdefault(dd, [0, 0.0]); r[0] += 1; r[1] += gain
    if not trades:
        print(f"\n[{name}] gate={gate}: 0 trades"); return
    wr = wins / trades; ept = pnl / trades
    se = math.sqrt(wr * (1 - wr) / trades) * 1.953
    print(f"\n[{name}] gate={gate} haircut={haircut}: trades={trades} model_p={mp/trades:.4f} "
          f"real_p={wr:.4f} PnL/t={100*ept:+.2f}% t={ept/se:.2f}")
    for dd in sorted(day_pnl):
        n, pn = day_pnl[dd]
        print(f"    day {dd}: n={n:6d} PnL={pn:+8.1f} ({100*pn/max(n,1):+.2f}%/t)")

if __name__ == "__main__":
    # trigger table: for each digit, spot below which EV clears the gate
    grid = np.arange(3.0, 5.01, 0.005)
    print("per-digit trigger spots (gate 1% / 0.5%, haircut 0.001):")
    for dgt in range(10):
        row = []
        for gate in (0.01, 0.005):
            smax = None
            for s in grid[::-1]:
                off = dg_off(s)
                bb = max(((cb, sum(off[(w - dgt) % 10] for w in WINSET[cb])) for cb in PAY),
                         key=lambda t: t[1] * PAY[t[0]] - 1)
                if (bb[1] - 0.001) * PAY[bb[0]] - 1 > gate:
                    smax = s; break
            row.append((smax, bb[0] if smax else None))
        s1, c1 = row[0]; s2, c2 = row[1]
        f = lambda s: f"spot<{s/C:5.1f} (sig {s:.2f})" if s else "never       "
        print(f"  d={dgt}: gate1% {f(s1)}   gate0.5% {f(s2)}   best={c1 or c2}")

    for nm, p in [("jun11_wide", "../../opus-thoughts/data/JD100.csv.gz"),
                  ("jun29", "../data/JD100_jun29_v2.csv.gz"),
                  ("jul05_fresh", "data/JD100_jul05.csv.gz")]:
        run(nm, p, gate=0.01)
        run(nm, p, gate=0.005)
