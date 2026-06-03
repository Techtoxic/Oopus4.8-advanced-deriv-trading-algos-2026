"""
test_untapped_fill.py — falsify (or confirm) the framework's core premise.

The friend's central observation: "~90% of untapped digits get filled when price
revisits a mini-cluster." That observation is TRUE but it is a tautology of the
geometric distribution, and it carries ZERO per-tick predictive edge. This script
separates the two cleanly:

  (A) EVENTUAL FILL is a tautology. For i.i.d.-uniform digits, the chance a
      specific digit appears within the next K ticks is 1 - 0.9^K. At K=22 that
      is ~0.90. "90% fill on revisit" just means revisits last ~22 ticks. We
      show the measured fill curve sits on top of 1 - 0.9^K.

  (B) EXPLOITABILITY is false. The only thing that matters for a trade is the
      NEXT tick. We condition on "digit d has not appeared in the last w ticks"
      (d is 'untapped') and measure P(next digit = d). Under the gambler's-
      fallacy belief baked into the framework this should exceed 0.10. Under a
      memoryless RNG it is exactly 0.10. We report the pooled estimate with a
      Wilson 99% CI and check whether it clears the Differs/Over0/Under9
      break-even implied by the cheapest house edge.

Accepts any integer digit array so controls.py can run the identical test on a
known-fair RNG and on a planted-edge stream.
"""
import math, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def wilson(k, n, z=2.576):
    if n == 0:
        return (0.0, 0.0)
    ph = k / n
    c = z * z / (2 * n)
    half = z * math.sqrt((ph * (1 - ph) + z * z / (4 * n)) / n)
    return ((ph + c - half) / (1 + 2 * c), (ph + c + half) / (1 + 2 * c))


def eventual_fill(digits, Ks=(10, 22, 30, 50)):
    """Measured P(digit absent now appears within next K ticks) vs 1-0.9^K."""
    d = np.asarray(digits)
    n = len(d)
    res = {}
    for K in Ks:
        hits = 0; trials = 0
        # sample positions where we know K future ticks exist
        for t in range(0, n - K, 7):           # stride to decorrelate samples
            window_future = d[t + 1:t + 1 + K]
            present_now = d[t]
            for cand in range(10):
                if cand == present_now:
                    continue
                trials += 1
                if cand in window_future:
                    hits += 1
        res[K] = (hits / trials if trials else None, 1 - 0.9 ** K, trials)
    return res


def exploitability(digits, lag=1, windows=(5, 10, 20)):
    """
    Condition on 'd absent in last w ticks' -> P(next-lag tick == d).
    Pools over all (t, d) pairs. Null (memoryless) = 0.10 exactly.
    """
    d = np.asarray(digits)
    n = len(d)
    out = {}
    for w in windows:
        hits = 0; trials = 0
        for t in range(w, n - lag):
            recent = set(d[t - w:t])
            target = d[t + lag]
            for cand in range(10):
                if cand in recent:
                    continue                    # tapped -> skip
                trials += 1
                if cand == target:
                    hits += 1
        ph = hits / trials if trials else None
        lo, hi = wilson(hits, trials)
        out[w] = {"p": ph, "lo": lo, "hi": hi, "trials": trials, "null": 0.10}
    return out


def run(digits, label, out_lines):
    p = out_lines.append
    p(f"\n=== {label}  (n={len(digits)}) ===")
    p("  (A) EVENTUAL FILL  P(absent digit appears within K ticks) vs 1-0.9^K:")
    for K, (obs, theory, tr) in eventual_fill(digits).items():
        p(f"      K={K:3d}  measured={obs:.4f}  theory(1-0.9^K)={theory:.4f}  "
          f"diff={obs-theory:+.4f}")
    p("  (B) EXPLOITABILITY  P(next==d | d untapped in last w)  [null=0.1000]:")
    res = exploitability(digits)
    verdict_edge = False
    for w, r in res.items():
        flag = "EDGE!" if r["lo"] > 0.1124 else "no edge"  # 0.1124 ~ Match BE-ish gambler target
        # the directly relevant break-even is for a 'bet this untapped digit will
        # appear next' = a Matches-style 10% bet; cheapest exploit needs lo>BE.
        beats = r["lo"] > 0.112
        verdict_edge |= beats
        p(f"      w={w:2d}  P={r['p']:.4f}  Wilson99=[{r['lo']:.4f},{r['hi']:.4f}]  "
          f"trials={r['trials']:,}  {'BEATS 0.112 BE' if beats else 'at/below null'}")
    p(f"  -> per-tick predictive edge from 'untapped': "
      f"{'DETECTED' if verdict_edge else 'NONE (P=0.10, memoryless)'}")
    return verdict_edge


def main():
    from lattice import common
    syms = ["1HZ100V", "1HZ10V", "1HZ75V", "1HZ25V", "R_100", "R_10", "JD100"]
    L = ["=" * 70, "UNTAPPED-FILL: tautology vs exploitability", "=" * 70]
    any_edge = False
    for s in syms:
        try:
            e, pr = common.load(s)
        except Exception as ex:
            L.append(f"\n{s}: load failed ({ex})"); continue
        dig, places = common.last_digit(pr)
        any_edge |= run(dig, f"{s} [{places}dp]", L)
    L.append("\n" + "=" * 70)
    L.append(f"OVERALL: untapped status {'CARRIES' if any_edge else 'CARRIES NO'} "
             f"per-tick edge on real Deriv data.")
    text = "\n".join(L)
    print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "untapped_fill.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w").write(text + "\n")


if __name__ == "__main__":
    main()
