"""
test_regression_location.py — can Layer 3 predict the next cluster location?

Layer 3's job is to predict the next PRICE so the bot can pre-load the target
mini-cluster before the tick lands. We run the actual AdaptiveRegressor over each
real series and measure how often its predicted next price lands in the same
mini-cluster band as the realized next price. We compare against the only honest
baseline on a zero-drift walk: PERSISTENCE (predict next = last).

If the adaptive regression cannot beat persistence at hitting the next cluster,
it adds nothing but compute. On a random walk it cannot — and this prints the
proof rather than asserting it.

A 'cluster band' is a price bucket of width = median |1-tick move| * tpm, i.e.
the typical span a 10-tick mini-cluster covers; both predictors are scored on the
identical banding so the comparison is fair.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lattice.layer3_regression import AdaptiveRegressor


def run(prices, label, out_lines, tpm=10, warm=60, stride=1, maxn=40000):
    pr = np.asarray(prices, dtype=float)
    n = min(len(pr), warm + maxn)
    band = max(np.median(np.abs(np.diff(pr[:5000]))) * tpm, 1e-9)
    reg = AdaptiveRegressor()
    reg_hit = reg_n = 0
    per_hit = 0
    for i in range(n - 1):
        pred = reg.predict()
        actual_next = pr[i + 1]
        if pred is not None and i >= warm:
            # band index of prediction vs actual
            reg_n += 1
            reg_hit += (round(pred / band) == round(actual_next / band))
            per_hit += (round(pr[i] / band) == round(actual_next / band))
        reg.update(pr[i], pred)
    p = out_lines.append
    rr = reg_hit / reg_n if reg_n else 0
    pp = per_hit / reg_n if reg_n else 0
    p(f"\n=== {label} ===")
    p(f"  band width = {band:.6g} (median |move| x {tpm})")
    p(f"  regression cluster-hit rate = {rr:.4f}")
    p(f"  persistence cluster-hit rate = {pp:.4f}  (predict next=last)")
    p(f"  regression - persistence = {rr-pp:+.4f}  "
      f"{'REGRESSION ADDS VALUE' if rr > pp + 0.01 else 'no improvement over persistence'}")
    return rr > pp + 0.01


def main():
    from lattice import common
    syms = ["1HZ100V", "1HZ10V", "R_100", "JD100"]
    L = ["=" * 70, "LAYER-3 REGRESSION CLUSTER-LOCATION vs PERSISTENCE", "=" * 70]
    better = False
    for s in syms:
        try:
            e, pr = common.load(s)
        except Exception as ex:
            L.append(f"\n{s}: load failed ({ex})"); continue
        better |= run(pr, s, L)
    L.append("\n" + "=" * 70)
    L.append(f"OVERALL: adaptive regression {'beats' if better else 'does NOT beat'} "
             f"persistence at locating the next cluster.")
    text = "\n".join(L); print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "regression_location.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w").write(text + "\n")


if __name__ == "__main__":
    main()
