"""battery_v2.py — does the "structure" randomness_battery found actually pay?

WHY v1 CANNOT BE TRUSTED
randomness_battery.py failed 5/5 tests. When every test rejects, suspect the tests.

  Tests C and D used the ASYMPTOTIC chi-square despite the docstring promising a
  block-permutation null. That is fatal here because the samples OVERLAP: ent[i+1] IS
  nxt[i], so consecutive rows of the contingency table share data and the independence
  assumption behind chi-square is violated by construction. Test D is worse -- o1 and o2
  overlap directly.

  Tests A and B are statistically real but economically empty. Worst autocorrelation
  r = -0.0103 is R^2 = 0.0001, one hundredth of one percent of variance. The runs test
  found 270,775 vs 269,258 expected, a 0.56% excess. At n = 600k, Ljung-Box rejects on
  anything.

  Test E (27x mean power at period 3.0 ticks) is the one worth a second look; the
  multiple-comparison threshold across 131k bins is ~12-15, so 27x is a genuine outlier.

WHAT THIS DOES INSTEAD
  1. PERMUTATION NULL for the entry/offset table. Shuffles offsets in blocks, preserving
     autocorrelation and the overlap structure, and compares the observed chi-square to
     that null. This is the test v1 promised.
  2. ECONOMIC SIGNIFICANCE — the question that matters. Fit per-entry-digit offset tables
     on IS, fit one POOLED table on IS, then compare their out-of-sample win rates at the
     EXECUTED payout 1.8286. If per-entry tables do not beat pooled OOS, the "structure"
     is unusable regardless of any p-value.
  3. SPECTRAL FOLLOW-UP — folds the step series at the detected period. A real generator
     artifact shows a stable phase profile; noise does not.
  4. EFFECT SIZE on A and B, expressed as win-rate impact rather than a p-value.

Interleaved-block split with purge, because sigma trends over the sample.

Read-only. Places no trades.

Run: python3 battery_v2.py --symbol JD100
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS

EXEC_M = 1.8286
BE = 1 / EXEC_M
WIN5 = [0, 1, 2, 8, 9]


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def chi_table(ent, off):
    t = np.zeros((10, 10))
    np.add.at(t, (ent, off), 1)
    r, c = t.sum(1, keepdims=True), t.sum(0, keepdims=True)
    e = r @ c / t.sum()
    return float(((t - e) ** 2 / np.maximum(e, 1e-9)).sum()), t, e


def interleave(n, block=20000, purge=2000):
    a = np.zeros(n, bool); b = np.zeros(n, bool)
    for s in range(0, n, block):
        e = min(s + block, n)
        (a if (s // block) % 2 == 0 else b)[min(s + purge, e):max(e - purge, s)] = True
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=600000)
    ap.add_argument("--perms", type=int, default=300)
    ap.add_argument("--block", type=int, default=1000)
    ap.add_argument("--out", default="../results/battery_v2.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    _, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    pip = int(pip)
    v = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    ws.close()
    st = np.diff(v)
    dig = (v % 10).astype(int)
    ent, nxt = dig[:-1], dig[1:]
    off = (nxt - ent) % 10
    n = len(off)
    print(f"got {len(v)} ticks, spot {prices[-1]}\n")

    # ---- 1. permutation null for entry/offset dependence ----------------
    print("=" * 74)
    print("1. ENTRY/OFFSET DEPENDENCE vs BLOCK-PERMUTATION NULL")
    print("   (v1 used the asymptotic chi-square; samples overlap, so that is invalid)")
    print("=" * 74)
    obs, tab, exp = chi_table(ent, off)
    rng = np.random.default_rng(0)
    nb = n // a.block
    null = []
    for _ in range(a.perms):
        order = rng.permutation(nb)
        sh = np.concatenate([off[i * a.block:(i + 1) * a.block] for i in order])
        m = min(len(ent), len(sh))
        null.append(chi_table(ent[:m], sh[:m])[0])
    null = np.array(null)
    z = (obs - null.mean()) / (null.std() or 1e-9)
    pct = float((null >= obs).mean())
    print(f"  observed chi2      {obs:.1f}")
    print(f"  permutation null   mean {null.mean():.1f}  sd {null.std():.1f}  "
          f"max {null.max():.1f}")
    print(f"  z = {z:+.2f}   empirical p = {pct:.4f}")
    print(f"  (v1 reported asymptotic p = 0.00000 on 81 df — critical value 113)")
    real_dep = pct < 0.01
    print(f"  -> {'dependence SURVIVES the correct null' if real_dep else 'ARTIFACT: chi2 is inside the null. v1 test C was invalid.'}")

    # ---- 2. ECONOMIC SIGNIFICANCE ---------------------------------------
    print(f"\n{'='*74}")
    print("2. DOES PER-ENTRY-DIGIT CONDITIONING BEAT A POOLED TABLE, OUT OF SAMPLE?")
    print(f"   executed payout {EXEC_M}, breakeven {BE*100:.2f}%")
    print("=" * 74)
    is_m, oos_m = interleave(n)
    win5 = np.isin(off, WIN5)

    # pooled: one offset pmf; per-entry: ten offset pmfs
    pooled = np.bincount(off[is_m], minlength=10).astype(float)
    pooled /= pooled.sum()
    per = np.zeros((10, 10))
    for c in range(10):
        s = is_m & (ent == c)
        if s.sum() > 500:
            b = np.bincount(off[s], minlength=10).astype(float)
            per[c] = b / b.sum()
        else:
            per[c] = pooled

    # strategy: for each entry digit choose the best 5-wide window from the table,
    # then measure the realised OOS win rate of that choice.
    def eval_tables(tbl_fn):
        wins = tot = 0
        for c in range(10):
            t = tbl_fn(c)
            # best contiguous 5-window of offsets, centred choice
            best_o, best_p = None, -1
            for start in range(10):
                w = [(start + k) % 10 for k in range(5)]
                pw = float(sum(t[x] for x in w))
                if pw > best_p:
                    best_p, best_o = pw, w
            s = oos_m & (ent == c)
            if s.sum() == 0:
                continue
            wins += int(np.isin(off[s], best_o).sum())
            tot += int(s.sum())
        return wins, tot

    wp, tp = eval_tables(lambda c: pooled)
    we, te = eval_tables(lambda c: per[c])
    for lab, w_, t_ in (("pooled table", wp, tp), ("per-entry tables", we, te)):
        p = w_ / t_
        lo, _ = wilson(w_, t_)
        print(f"  {lab:20} n={t_:>7}  p={p:.5f}  vs BE {(p-BE)*100:+.3f}pp  "
              f"EV {(p*EXEC_M-1)*100:+.2f}%  99%low {(lo*EXEC_M-1)*100:+.2f}%")
    gain = (we / te - wp / tp) * 100
    print(f"\n  per-entry gain over pooled: {gain:+.4f}pp")
    se = math.sqrt(0.25 / te + 0.25 / tp) * 100
    print(f"  SE of the difference ~{se:.4f}pp -> t = {gain/se:+.2f}")
    econ = gain > 2 * se
    print(f"  -> {'per-entry conditioning ADDS measurable edge' if econ else 'NO usable gain. Whatever v1 test C detected is not tradeable.'}")

    # ---- 3. spectral follow-up -------------------------------------------
    print(f"\n{'='*74}")
    print("3. SPECTRAL FOLLOW-UP (v1 found 27x power at period 3.0 ticks)")
    print("=" * 74)
    x = st.astype(float) - st.mean()
    m = 1 << int(math.log2(min(len(x), 262144)))
    f = np.abs(np.fft.rfft(x[:m] / (x[:m].std() or 1))) ** 2 / m
    pk = int(np.argmax(f[1:]) + 1)
    period = m / pk
    ratio = f[pk] / f[1:].mean()
    # Bonferroni-ish threshold for the max of ~m/2 exponential bins
    thr = math.log(len(f))
    print(f"  peak bin {pk}, period {period:.3f} ticks, power {ratio:.2f}x mean")
    print(f"  expected max of {len(f)} exponential bins ~ ln(n) = {thr:.1f}")
    print(f"  -> {'genuine outlier' if ratio > 2*thr else 'within multiple-comparison expectation'}")
    if period > 1.5:
        k = int(round(period))
        folded = [x[i::k].mean() for i in range(k)]
        amp = (max(folded) - min(folded)) / (x.std() or 1)
        print(f"  phase profile folded at {k} ticks: amplitude {amp:.5f} sd")
        print(f"  -> {'stable phase -> generator artifact' if amp > 0.02 else 'no stable phase -> noise'}")

    # ---- 4. effect sizes for A and B -------------------------------------
    print(f"\n{'='*74}")
    print("4. EFFECT SIZE OF v1's TESTS A AND B")
    print("=" * 74)
    d0 = float((x * x).sum())
    worst = max(((abs(float((x[:-l] * x[l:]).sum()) / d0), l) for l in range(1, 51)))
    print(f"  worst |autocorr| = {worst[0]:.5f} at lag {worst[1]}  ->  R^2 = {worst[0]**2:.8f}")
    print(f"  that is {worst[0]**2*100:.5f}% of variance explained")
    s = np.sign(st); s = s[s != 0]
    runs = 1 + int((s[1:] != s[:-1]).sum())
    exp_runs = 2 * (s > 0).sum() * (s < 0).sum() / len(s) + 1
    print(f"  runs excess = {(runs/exp_runs-1)*100:+.3f}%")
    print("  -> both statistically detectable at n=600k and economically negligible.")

    print(f"\n{'='*74}")
    print("VERDICT")
    print("=" * 74)
    if econ:
        print("  Per-entry conditioning adds real, measurable edge over the pooled table.")
        print("  Wire per-entry offset tables into the sentinel and re-validate.")
    elif real_dep:
        print("  Dependence survives the correct null but does NOT translate into edge.")
        print("  Statistically real, economically unusable — do not chase it.")
    else:
        print("  v1's test C was an artifact of overlapping samples and the wrong null.")
        print("  (entry digit, sigma) remains a complete description of the tradeable")
        print("  structure. Nothing further to extract from the price series.")

    open(a.out, "w").write(
        f"# Battery v2 — {a.symbol}\n\nobserved chi2 {obs:.1f}, permutation null "
        f"mean {null.mean():.1f} sd {null.std():.1f}, empirical p {pct:.4f}.\n\n"
        f"pooled p={wp/tp:.5f}, per-entry p={we/te:.5f}, gain {gain:+.4f}pp "
        f"(SE {se:.4f}pp).\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
