"""
controls.py — prove the validation harness is calibrated (no false +/-).

A "no edge found" result is only trustworthy if the SAME tests (a) find nothing
on a known-fair RNG and (b) DO find edges that we deliberately plant. This runs
four synthetic streams through the exact detectors used on real Deriv data:

  1. FAIR              numpy uniform digits (truly memoryless)        -> expect NULL
  2. MARKOV_DRIFT      P(next=(d+1)%10)=0.22 (order-1 memory)         -> expect MI fires
  3. ANTI_REPEAT       digits avoid the last 10 (gambler's fallacy
                       made REAL: untapped digits ARE more likely)    -> expect untapped fires
  4. BIASED_DIGIT      digit 7 at 16% (non-uniform marginal)          -> expect chi2 + EV fire

If detectors fire correctly here and stay silent on real data, the null verdict
on Deriv is earned, not assumed.
"""
import os, sys, math
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lattice import common
import test_untapped_fill as TU
import test_higher_order as TH

N = 200_000
RNG = np.random.default_rng(2026)


def gen_fair(n=N):
    return RNG.integers(0, 10, size=n)


def gen_markov_drift(n=N, p=0.22):
    d = np.empty(n, dtype=int)
    d[0] = RNG.integers(0, 10)
    rest = (1.0 - p) / 9.0
    for i in range(1, n):
        probs = np.full(10, rest)
        probs[(d[i - 1] + 1) % 10] = p
        d[i] = RNG.choice(10, p=probs)
    return d


def gen_anti_repeat(n=N, w=10, avoid_strength=0.6):
    """With prob avoid_strength, draw a digit NOT seen in the last w ticks
    (untapped) -> untapped digits genuinely more likely than 0.10."""
    d = np.empty(n, dtype=int)
    d[:w] = RNG.integers(0, 10, size=w)
    for i in range(w, n):
        recent = set(d[i - w:i].tolist())
        untapped = [x for x in range(10) if x not in recent]
        if untapped and RNG.random() < avoid_strength:
            d[i] = RNG.choice(untapped)
        else:
            d[i] = RNG.integers(0, 10)
    return d


def gen_biased_digit(n=N, digit=7, p=0.16):
    rest = (1.0 - p) / 9.0
    probs = np.full(10, rest); probs[digit] = p
    return RNG.choice(10, size=n, p=probs)


def detect_untapped(d):
    """Does untapped status raise next-tick prob beyond 0.10 (Wilson99 lo)?"""
    r = TU.exploitability(d, lag=1, windows=(10,))[10]
    return r["lo"] > 0.105, r["p"], r["lo"]


def detect_mi(d):
    L = []
    fired = TH.run(d, "ctrl", L, n_shuffle=20)
    # parse order-1 line
    return fired


def detect_chi2(d):
    counts = np.bincount(d, minlength=10)
    chi2, pv = stats.chisquare(counts)
    return pv < 0.001, chi2, pv


def detect_match_ev(d, digit=7, payout=8.93):
    wr = float(np.mean(d == digit))
    ev = wr * payout - 1.0
    return ev > 0, wr, ev


def main():
    L = ["=" * 74, "DETECTOR CALIBRATION CONTROLS (null + planted edges)", "=" * 74]
    p = L.append
    streams = {
        "FAIR (uniform)":      (gen_fair(),        "all NULL"),
        "MARKOV_DRIFT":        (gen_markov_drift(),"MI fires"),
        "ANTI_REPEAT":         (gen_anti_repeat(), "untapped fires"),
        "BIASED_DIGIT(7@16%)": (gen_biased_digit(),"chi2 + EV fire"),
    }
    p(f"\n{'stream':22s} {'untapped':>22s} {'MI(o<=3)':>10s} {'chi2':>14s} {'Match7 EV':>16s}")
    rows = {}
    for name, (d, expect) in streams.items():
        u_fire, u_p, u_lo = detect_untapped(d)
        mi_fire = detect_mi(d)
        c_fire, c_chi, c_pv = detect_chi2(d)
        ev_fire, ev_wr, ev = detect_match_ev(d)
        rows[name] = (u_fire, mi_fire, c_fire, ev_fire, expect)
        p(f"{name:22s} "
          f"{'FIRE' if u_fire else 'null':>6s} P={u_p:.3f},lo={u_lo:.3f}  "
          f"{'FIRE' if mi_fire else 'null':>10s} "
          f"{'FIRE' if c_fire else 'null':>5s} p={c_pv:.1e}  "
          f"{'FIRE' if ev_fire else 'null':>5s} wr={ev_wr:.3f},EV={ev:+.2f}")
    # verdicts
    p("\nExpected vs observed:")
    ok = True
    checks = [
        ("FAIR (uniform)",      lambda r: not (r[0] or r[1] or r[2] or r[3]), "silent on all"),
        ("MARKOV_DRIFT",        lambda r: r[1],                               "MI detects memory"),
        ("ANTI_REPEAT",         lambda r: r[0],                               "untapped detects fill bias"),
        ("BIASED_DIGIT(7@16%)", lambda r: r[2] and r[3],                      "chi2 + EV detect bias"),
    ]
    for name, fn, desc in checks:
        good = fn(rows[name]); ok &= good
        p(f"  {name:22s} {desc:32s} {'PASS' if good else 'FAIL'}")
    p("")
    p(f"HARNESS CALIBRATION: {'PASS - detectors fire on real edges, silent on fair RNG' if ok else 'FAIL'}")
    text = "\n".join(L); print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "controls.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w").write(text + "\n")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
