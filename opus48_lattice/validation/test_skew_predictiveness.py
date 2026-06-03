"""
test_skew_predictiveness.py — does Layer 2's within-window skew predict anything?

Layer 2 claims: the recent distribution's skew (mean vs midpoint) tells you the
Over/Under direction, and its mode tells you the Match target. We test both as
the framework would actually trade them, settling on the real lag-2 digit:

  * SKEW-FOLLOW: window mean > 4.5 -> bet Over 4 ; mean < 4.5 -> bet Under 5.
    Null P(win) = 0.5. Need realized > break-even (~0.512) to profit.
  * MODE-MATCH: bet Match(window mode). Null P(win)=0.1. Need > ~0.112.
  * CONTRARIAN-MATCH (the 'fill the untapped' bet): Match(least-frequent digit).

Reports realized win rate with Wilson 99% lower bound vs break-even per index.
Accepts a digit array for controls reuse.
"""
import math, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def wilson_lo(k, n, z=2.576):
    if n == 0:
        return 0.0
    ph = k / n; c = z * z / (2 * n)
    half = z * math.sqrt((ph * (1 - ph) + z * z / (4 * n)) / n)
    return (ph + c - half) / (1 + 2 * c)


def run(digits, label, out_lines, w=10, lag=2):
    d = np.asarray(digits, dtype=int)
    n = len(d)
    p = out_lines.append
    skew_w = skew_n = 0
    mode_w = mode_n = 0
    least_w = least_n = 0
    for t in range(w, n - lag):
        win = d[t - w:t]
        nxt = d[t + lag]
        m = win.mean()
        if m > 4.5:
            skew_n += 1; skew_w += (nxt > 4)
        elif m < 4.5:
            skew_n += 1; skew_w += (nxt < 5)
        counts = np.bincount(win, minlength=10)
        mode = int(np.argmax(counts))
        mode_n += 1; mode_w += (nxt == mode)
        least = int(np.argmin(counts))     # often an untapped/low-freq digit
        least_n += 1; least_w += (nxt == least)
    p(f"\n=== {label}  (n={n}, w={w}, lag={lag}) ===")
    def line(name, k, nn, be):
        rate = k / nn if nn else 0
        lo = wilson_lo(k, nn)
        p(f"  {name:16s} win={rate:.4f}  Wilson99lo={lo:.4f}  BE~{be:.3f}  "
          f"{'BEATS BE' if lo > be else 'below BE'}  (n={nn:,})")
        return lo > be
    a = line("skew-follow O4/U5", skew_w, skew_n, 0.512)
    b = line("mode-match", mode_w, mode_n, 0.112)
    c = line("least-freq match", least_w, least_n, 0.112)
    edge = a or b or c
    p(f"  -> Layer-2 signal edge: {'DETECTED' if edge else 'NONE'}")
    return edge


def main():
    from lattice import common
    syms = ["1HZ100V", "1HZ10V", "1HZ75V", "1HZ25V", "R_100", "R_10", "JD100"]
    L = ["=" * 70, "LAYER-2 SKEW / MODE PREDICTIVENESS (settled lag-2)", "=" * 70]
    any_edge = False
    for s in syms:
        try:
            e, pr = common.load(s)
        except Exception as ex:
            L.append(f"\n{s}: load failed ({ex})"); continue
        dig, places = common.last_digit(pr)
        any_edge |= run(dig, f"{s} [{places}dp]", L)
    L.append("\n" + "=" * 70)
    L.append(f"OVERALL: Layer-2 distribution signals {'BEAT' if any_edge else 'DO NOT beat'} break-even.")
    text = "\n".join(L); print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "skew_predictiveness.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w").write(text + "\n")


if __name__ == "__main__":
    main()
