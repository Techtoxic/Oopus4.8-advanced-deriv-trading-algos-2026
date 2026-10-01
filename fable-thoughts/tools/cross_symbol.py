"""cross_symbol.py — are Deriv's synthetic symbols actually independent?

WHY THIS AXIS
All thirteen hypotheses in this repo so far have been single-symbol, single-contract,
static-pricing: is contract X on symbol Y mispriced relative to the payout grid. Twelve
came back correctly priced and the one that worked got repriced.

Nobody has ever asked whether the SYMBOLS ARE RELATED TO EACH OTHER.

Every synthetic is RNG-generated. Deriv runs ~35 of them concurrently. If any pair shares
an entropy source, or is a transform/subsample of another, or leads another by even one
tick, that is a predictive signal with completely different properties to everything tested
so far:

  - it does not depend on any payout grid, so repricing cannot kill it
  - it survives sigma drift, decimal changes and rebasing
  - it would let you trade the LAGGING symbol using the LEADING symbol's information

Specific structural suspicion: R_100 (2s ticks) and 1HZ100V (1s ticks) are both nominally
"Volatility 100". If the 2s series is a subsample of the 1s series, or both derive from one
stream, that is directly exploitable.

TESTS RUN (all on epoch-aligned data, no model assumptions)
  1. EXACT COINCIDENCE — do identical price values appear at the same epoch across symbols
     more often than chance? Shared-stream smoking gun.
  2. STEP CORRELATION — Pearson and sign-agreement on epoch-matched step series.
  3. LEAD-LAG — cross-correlation at lags -5..+5 ticks. A peak off zero means one symbol
     leads another; that is the tradeable case.
  4. MAGNITUDE COUPLING — correlation of |step|, which catches shared volatility state even
     when signs are independent.
  5. DIGIT COUPLING — mutual information between last digits, which catches shared low-order
     RNG bits that price correlation would miss entirely.

Everything is compared against a permutation null built by shuffling one series in blocks,
so "significant" means significant against the actual autocorrelation structure, not against
an idealised iid assumption.

Read-only. Places no trades.

Run: python3 cross_symbol.py
     python3 cross_symbol.py --symbols 1HZ100V R_100 1HZ10V R_10 JD100 --ticks 60000
"""
import argparse, itertools, math, time
import numpy as np
from deriv_api import DerivWS

DEFAULT = ["1HZ100V", "R_100", "1HZ10V", "R_10", "1HZ75V", "R_75", "JD100"]


def fetch(ws, sym, n):
    times, prices, pip = ws.history_paged(sym, n, sleep=0.2)
    pip = int(pip)
    d = {}
    for t, p in zip(times, prices):
        d[int(t)] = round(float(p) * (10 ** pip))
    return d, pip


def align(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 500:
        return None, None, keys
    return (np.array([a[k] for k in keys], dtype=np.int64),
            np.array([b[k] for k in keys], dtype=np.int64), keys)


def block_perm_null(x, y, stat_fn, n_perm=200, block=500):
    """Null distribution preserving autocorrelation via block permutation of y."""
    rng = np.random.default_rng(0)
    n = len(y)
    nb = max(1, n // block)
    out = []
    for _ in range(n_perm):
        order = rng.permutation(nb)
        yb = np.concatenate([y[i * block:(i + 1) * block] for i in order])[:n]
        m = min(len(x), len(yb))
        out.append(stat_fn(x[:m], yb[:m]))
    return np.array(out)


def zscore(obs, null):
    mu, sd = float(np.mean(null)), float(np.std(null))
    return (obs - mu) / sd if sd > 1e-12 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT)
    ap.add_argument("--ticks", type=int, default=60000)
    ap.add_argument("--max-lag", type=int, default=5)
    ap.add_argument("--out", default="../results/cross_symbol.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    data, pips = {}, {}
    for s in a.symbols:
        try:
            d, p = fetch(ws, s, a.ticks)
            data[s], pips[s] = d, p
            ts = sorted(d)
            iv = np.median(np.diff(ts)) if len(ts) > 1 else 0
            print(f"  {s:10} {len(d):>7} ticks  pip 1e-{p}  median interval {iv:.0f}s")
        except Exception as e:
            print(f"  {s:10} fetch error: {e}")
        time.sleep(0.2)
    ws.close()

    md = ["# Cross-symbol dependence\n\n",
          "Are Deriv's synthetics independent? Tests run on epoch-aligned ticks with a ",
          "block-permutation null that preserves autocorrelation.\n\n"]

    print(f"\n{'='*94}")
    print("PAIRWISE TESTS  (|z| > 3 vs block-permutation null = suspicious)")
    print(f"{'='*94}")
    print(f"{'pair':24}{'n':>7}{'coincid':>9}{'r(step)':>9}{'z':>7}"
          f"{'sign%':>8}{'z':>7}{'r(|step|)':>11}{'z':>7}  best lag")
    print("-" * 94)

    rows = []
    for s1, s2 in itertools.combinations([s for s in a.symbols if s in data], 2):
        v1, v2, keys = align(data[s1], data[s2])
        if v1 is None:
            print(f"{s1+'/'+s2:24}{len(keys):>7}  too few shared epochs")
            continue

        d1, d2 = np.diff(v1).astype(float), np.diff(v2).astype(float)
        n = len(d1)

        # 1. exact coincidence of raw values at same epoch
        coincid = float((v1 == v2).mean())

        # 2. step correlation
        def pear(x, y):
            if x.std() < 1e-12 or y.std() < 1e-12:
                return 0.0
            return float(np.corrcoef(x, y)[0, 1])
        r = pear(d1, d2)
        zr = zscore(r, block_perm_null(d1, d2, pear))

        # 3. sign agreement
        def signagr(x, y):
            m = (x != 0) & (y != 0)
            return float((np.sign(x[m]) == np.sign(y[m])).mean()) if m.sum() else 0.5
        sa = signagr(d1, d2)
        zsa = zscore(sa, block_perm_null(d1, d2, signagr))

        # 4. magnitude coupling
        am1, am2 = np.abs(d1), np.abs(d2)
        ra = pear(am1, am2)
        zra = zscore(ra, block_perm_null(am1, am2, pear))

        # 5. lead-lag
        best = (0.0, 0)
        for lag in range(-a.max_lag, a.max_lag + 1):
            if lag == 0:
                rl = r
            elif lag > 0:
                rl = pear(d1[:-lag], d2[lag:])
            else:
                rl = pear(d1[-lag:], d2[:lag])
            if abs(rl) > abs(best[0]):
                best = (rl, lag)

        rows.append(dict(pair=f"{s1}/{s2}", n=n, coincid=coincid, r=r, zr=zr,
                         sa=sa, zsa=zsa, ra=ra, zra=zra,
                         best_r=best[0], best_lag=best[1]))
        flag = " <<<" if (abs(zr) > 3 or abs(zsa) > 3 or abs(zra) > 3) else ""
        print(f"{s1+'/'+s2:24}{n:>7}{coincid:>9.5f}{r:>9.4f}{zr:>7.1f}"
              f"{sa*100:>7.2f}%{zsa:>7.1f}{ra:>11.4f}{zra:>7.1f}"
              f"  {best[1]:+d} ({best[0]:+.4f}){flag}")

    # 6. digit coupling
    print(f"\n{'='*94}")
    print("DIGIT MUTUAL INFORMATION  (catches shared low-order RNG bits)")
    print(f"{'='*94}")
    print(f"{'pair':24}{'n':>8}{'MI (bits)':>12}{'null mean':>12}{'z':>8}")
    print("-" * 94)
    for s1, s2 in itertools.combinations([s for s in a.symbols if s in data], 2):
        v1, v2, keys = align(data[s1], data[s2])
        if v1 is None:
            continue
        g1, g2 = (v1 % 10).astype(int), (v2 % 10).astype(int)

        def mi(x, y):
            j = np.zeros((10, 10))
            np.add.at(j, (x, y), 1)
            j /= j.sum()
            px, py = j.sum(1, keepdims=True), j.sum(0, keepdims=True)
            with np.errstate(divide="ignore", invalid="ignore"):
                t = j * np.log2(j / (px @ py))
            return float(np.nansum(t))
        obs = mi(g1, g2)
        null = block_perm_null(g1, g2, mi, n_perm=100)
        z = zscore(obs, null)
        print(f"{s1+'/'+s2:24}{len(g1):>8}{obs:>12.6f}{np.mean(null):>12.6f}{z:>8.1f}"
              + ("  <<<" if abs(z) > 3 else ""))

    sus = [r for r in rows if abs(r["zr"]) > 3 or abs(r["zsa"]) > 3 or abs(r["zra"]) > 3]
    print(f"\n{'='*94}")
    if sus:
        print("SUSPICIOUS PAIRS")
        for r in sus:
            print(f"  {r['pair']:24} r={r['r']:+.4f} (z {r['zr']:+.1f})  "
                  f"sign {r['sa']*100:.2f}% (z {r['zsa']:+.1f})  "
                  f"|step| r={r['ra']:+.4f} (z {r['zra']:+.1f})  "
                  f"best lag {r['best_lag']:+d}")
        print("\n  A non-zero BEST LAG is the tradeable case: the leading symbol's step")
        print("  carries information about the lagging symbol's next step.")
        print("  Next: verify on a fresh, non-overlapping tick block before believing it.")
    else:
        print("NO SUSPICIOUS PAIRS. Symbols look mutually independent at tick level.")
        print("Consistent with independent cryptographic RNG streams per symbol.")

    md.append("| pair | n | coincid | r(step) | z | sign% | z | r(abs) | z | best lag |\n")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for r in rows:
        md.append(f"| {r['pair']} | {r['n']} | {r['coincid']:.5f} | {r['r']:+.4f} | "
                  f"{r['zr']:+.1f} | {r['sa']*100:.2f}% | {r['zsa']:+.1f} | "
                  f"{r['ra']:+.4f} | {r['zra']:+.1f} | {r['best_lag']:+d} |\n")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
