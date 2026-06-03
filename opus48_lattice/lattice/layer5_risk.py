"""
lattice.layer5_risk — Adaptive stake sizing & risk control.

Fractional-Kelly anchored, modulated by convergence, cluster confidence, and
drawdown, capped by per-contract risk class and a hard 2%-of-bankroll ceiling.

The single most important line in this file:

    if ev_per_dollar <= 0:  ->  stake = 0

Kelly on a non-positive edge prescribes a zero bet. Every honest sizing scheme
collapses to "don't trade" against a negative-EV menu. The adaptive machinery
only ever scales a *positive* edge down for safety; it cannot manufacture one.
"""
import numpy as np

KELLY_FRACTION = 0.25
HARD_CAP = 0.02                  # 2% of bankroll, absolute

RISK_CLASS_CAP = {              # max fraction of bankroll by contract risk
    "DIFFER": 0.02, "OVER0": 0.02, "UNDER9": 0.02,
    "EVEN": 0.015, "ODD": 0.015, "OVER_MID": 0.015, "UNDER_MID": 0.015,
    "OVER_HI": 0.0075, "UNDER_LO": 0.0075,
    "MATCH": 0.005, "OVER8": 0.0025, "UNDER1": 0.0025,
}


def risk_class(kind, barrier):
    if kind == "DIFFER":
        return "DIFFER"
    if kind == "MATCH":
        return "MATCH"
    if kind in ("EVEN", "ODD"):
        return kind
    if kind == "OVER":
        if barrier == 0:
            return "OVER0"
        if barrier == 8:
            return "OVER8"
        return "OVER_HI" if barrier >= 5 else "OVER_MID"
    if kind == "UNDER":
        if barrier == 9:
            return "UNDER9"
        if barrier == 1:
            return "UNDER1"
        return "UNDER_LO" if barrier <= 4 else "UNDER_MID"
    return "MATCH"


def kelly_fraction(p_win, payout):
    """Binary-bet Kelly: f* = (p*(b) - (1-p)) / b, b = payout-1 (net odds)."""
    b = payout - 1.0
    if b <= 0:
        return 0.0
    f = (p_win * b - (1.0 - p_win)) / b
    return max(0.0, f)


def stake(bankroll, p_win, payout, convergence, cluster_conf01, drawdown_frac,
          kind, barrier, min_stake=0.35, conv_floor=0.99):
    """Return (stake_usd, reason)."""
    ev = p_win * payout - 1.0
    if ev <= 0:
        return 0.0, f"EV<=0 ({ev:+.4f}) -> Kelly=0"
    f = KELLY_FRACTION * kelly_fraction(p_win, payout)
    # modifiers in [0,1]; convergence scales from the active gate up to 1.0
    conv_mod = np.interp(convergence, [conv_floor, 1.0], [0.5, 1.0]) if convergence >= conv_floor else 0.0
    clus_mod = np.interp(cluster_conf01 * 100, [65, 90], [0.5, 1.0])
    if drawdown_frac >= 0.30:
        return 0.0, "drawdown>=30% -> HALT"
    dd_mod = (0.25 if drawdown_frac >= 0.20 else
              0.5 if drawdown_frac >= 0.20 else
              0.7 if drawdown_frac >= 0.10 else 1.0)
    f *= conv_mod * clus_mod * dd_mod
    raw = bankroll * f
    cap = bankroll * min(HARD_CAP, RISK_CLASS_CAP.get(risk_class(kind, barrier), 0.005))
    s = min(raw, cap)
    if s < min_stake:
        return 0.0, f"sized {s:.4f} < min {min_stake}"
    return float(s), f"f={f:.4f} ev={ev:+.4f}"
