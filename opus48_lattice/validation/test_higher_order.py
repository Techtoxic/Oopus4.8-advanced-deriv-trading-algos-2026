"""
test_higher_order.py — does the digit stream carry memory at depth k?

The cluster framework is, at heart, a windowed higher-order conditional model:
"the recent distribution predicts the next digit." First-order Markov (done by
the prior repos) is not enough to rule that out. Here we measure the mutual
information I(next ; context_k) for context orders k=1,2,3 — the exact quantity
that upper-bounds ANY predictor's gain from a length-k history.

Finite-sample plug-in MI is biased upward (more so for larger k), so we build a
SURROGATE NULL by shuffling the digit sequence many times and recomputing MI.
Real memory = observed MI sitting clearly above the shuffled band. We report the
z-score of observed vs the shuffle null and the implied max win-rate lift.

Accepts any digit array so controls.py reuses it.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _mi_next_given_context(d, k):
    """Plug-in mutual information (bits) between next digit and prior k digits."""
    n = len(d)
    if n <= k + 1:
        return 0.0
    ctx = np.zeros(n - k, dtype=np.int64)
    for j in range(k):
        ctx = ctx * 10 + d[j:n - k + j]
    nxt = d[k:n]
    # joint counts
    # MI = H(next) + H(ctx) - H(ctx,next)
    def entropy(vals):
        _, c = np.unique(vals, return_counts=True)
        p = c / c.sum()
        return -np.sum(p * np.log2(p))
    joint = ctx.astype(np.int64) * 10 + nxt
    return float(entropy(nxt) + entropy(ctx) - entropy(joint))


def run(digits, label, out_lines, n_shuffle=40):
    d = np.asarray(digits, dtype=np.int64)
    p = out_lines.append
    p(f"\n=== {label}  (n={len(d)}) ===")
    rng = np.random.default_rng(12345)
    any_mem = False
    for k in (1, 2, 3):
        obs = _mi_next_given_context(d, k)
        # surrogate null: shuffle destroys order but keeps the marginal
        null = np.empty(n_shuffle)
        for i in range(n_shuffle):
            ds = d.copy(); rng.shuffle(ds)
            null[i] = _mi_next_given_context(ds, k)
        mu, sd = null.mean(), null.std() + 1e-12
        z = (obs - mu) / sd
        # MI(bits) -> rough max win-rate lift on a 0.5 binary: sqrt(2*ln2*MI)/... 
        # we just report MI; a real edge needs obs >> null AND > house edge.
        mem = z > 4.0          # ~4 sigma above the finite-sample null
        any_mem |= mem
        p(f"  order-{k}: MI_obs={obs:.5f} bits  null={mu:.5f}+/-{sd:.5f}  "
          f"z={z:+.2f}  {'MEMORY' if mem else 'no memory'}")
    p(f"  -> {label}: {'HAS exploitable-depth memory' if any_mem else 'memoryless to order 3'}")
    return any_mem


def main():
    from lattice import common
    syms = ["1HZ100V", "1HZ10V", "1HZ75V", "1HZ25V", "R_100", "R_10", "JD100"]
    L = ["=" * 70, "HIGHER-ORDER MEMORY  I(next ; last-k) vs shuffle null", "=" * 70]
    any_mem = False
    for s in syms:
        try:
            e, pr = common.load(s)
        except Exception as ex:
            L.append(f"\n{s}: load failed ({ex})"); continue
        dig, places = common.last_digit(pr)
        any_mem |= run(dig, f"{s} [{places}dp]", L)
    L.append("\n" + "=" * 70)
    L.append(f"OVERALL: {'memory found' if any_mem else 'NO memory to order 3 on any index'}")
    text = "\n".join(L)
    print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "higher_order.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w").write(text + "\n")


if __name__ == "__main__":
    main()
