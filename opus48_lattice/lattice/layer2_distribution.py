"""
lattice.layer2_distribution — Frequency distribution engine & signal scoring.

Given a mini-cluster's last-digit count vector, derive every quantity the
friend's Layer 2 specifies and turn them into per-contract "distribution signal
strength" scores in [0,1] that Layer 4 consumes:

  * frequency map, tapped/untapped sets, mode, least-frequent digit
  * Shannon entropy H (bits) and the entropy gate [H_min, H_max]
  * skew direction & magnitude relative to a reference digit
  * per-market reads: Match target, Differ reference, Over/Under direction,
    Even/Odd bias

IMPORTANT (honesty): these scores describe the *observed in-window distribution*.
Under an i.i.d.-uniform generator they have ZERO predictive power for the next
digit (validation/ proves this). The scoring is faithful to the spec so the
backtest tests the friend's system exactly as designed.
"""
import numpy as np

H_MIN, H_MAX = 1.8, 2.8          # entropy gate (bits) from the spec


def distribution(counts):
    counts = np.asarray(counts, dtype=float)
    tot = counts.sum()
    if tot == 0:
        return None
    p = counts / tot
    nz = p[p > 0]
    H = float(-np.sum(nz * np.log2(nz)))
    tapped = tuple(int(d) for d in range(10) if counts[d] > 0)
    untapped = tuple(int(d) for d in range(10) if counts[d] == 0)
    mode = int(np.argmax(counts))
    # least frequent (prefer an untapped digit; else least-nonzero)
    if untapped:
        least = untapped[len(untapped) // 2]   # a representative untapped digit
    else:
        least = int(np.argmin(counts))
    mean_tapped = float(np.sum(np.arange(10) * counts) / tot)
    skew = mean_tapped - 4.5                    # >0 leans high, <0 leans low
    even_ct = float(counts[0] + counts[2] + counts[4] + counts[6] + counts[8])
    odd_ct = float(counts[1] + counts[3] + counts[5] + counts[7] + counts[9])
    return {
        "p": p, "H": H, "tapped": tapped, "untapped": untapped,
        "mode": mode, "least": least, "mean_tapped": mean_tapped,
        "skew": skew, "even_ct": even_ct, "odd_ct": odd_ct, "tot": tot,
    }


def entropy_gate_ok(dist):
    return H_MIN <= dist["H"] <= H_MAX


def signal_strengths(dist):
    """
    Return {(kind, barrier): strength in [0,1]} for the contracts the friend's
    distribution logic activates. Strength blends concentration (1 - H/H_max),
    skew magnitude, and untapped support. Contracts that contradict the primary
    read are simply omitted (Layer 4 treats absent as 0).
    """
    if dist is None:
        return {}
    out = {}
    conc = max(0.0, 1.0 - dist["H"] / np.log2(10))          # 0 (uniform) .. 1 (spike)
    skew = dist["skew"]; skew_mag = min(abs(skew) / 4.5, 1.0)
    D = dist["mode"]                                         # primary predicted digit
    untapped = set(dist["untapped"])

    # --- Match / Differ -------------------------------------------------- #
    # Match the most-frequent digit that is *still untapped* if any, else mode.
    match_target = None
    if untapped:
        # most "contextually supported" untapped = closest to the mode
        match_target = min(untapped, key=lambda d: abs(d - D))
        out[("MATCH", match_target)] = float(0.5 * conc + 0.5 * (1.0 if match_target in untapped else 0.0))
    # Differ the least-favored digit
    out[("DIFFER", dist["least"])] = float(0.4 + 0.6 * conc)

    # --- Over / Under ---------------------------------------------------- #
    ref = dist["least"]
    if skew > 0:        # leans high -> Over ref
        for b in range(max(ref - 1, 0), max(ref, 1)):
            out[("OVER", b)] = float(min(1.0, 0.3 + 0.7 * skew_mag))
        if ref - 1 >= 0:
            out[("OVER", ref - 1)] = float(min(1.0, 0.3 + 0.7 * skew_mag))
    elif skew < 0:      # leans low -> Under ref
        if ref + 1 <= 9:
            out[("UNDER", ref + 1)] = float(min(1.0, 0.3 + 0.7 * skew_mag))

    # --- Even / Odd ------------------------------------------------------ #
    tot = dist["even_ct"] + dist["odd_ct"]
    if tot > 0:
        gap = abs(dist["even_ct"] - dist["odd_ct"]) / tot
        if dist["odd_ct"] > dist["even_ct"]:
            out[("ODD", None)] = float(min(1.0, gap))
        elif dist["even_ct"] > dist["odd_ct"]:
            out[("EVEN", None)] = float(min(1.0, gap))

    return out, match_target
