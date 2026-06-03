"""
lattice.common — shared primitives for the lattice-microstructure framework.

Data loading, last-digit extraction, contract universe, payouts, and the
exhaustive per-digit contract resolution table (the "compound outcome" map the
friend asked for). Everything else in the package builds on these.
"""
import gzip, json, os
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load(symbol, data_dir=None):
    """Load a gzipped tick file -> (epochs:int64[], prices:float64[])."""
    d = data_dir or DATA_DIR
    path = os.path.join(d, f"{symbol}.json.gz")
    with gzip.open(path, "rt") as f:
        ticks = json.load(f)
    epochs = np.array([int(e) for e, _ in ticks], dtype=np.int64)
    prices = np.array([float(p) for _, p in ticks], dtype=float)
    return epochs, prices


def infer_decimals(prices, maxp=6):
    """Infer how many decimal places Deriv quotes for this symbol."""
    for p in range(0, maxp + 1):
        scaled = prices * (10 ** p)
        if np.max(np.abs(scaled - np.round(scaled))) < 1e-4:
            return p
    return maxp


def last_digit(prices, places=None):
    """Last decimal digit at the instrument's pip precision."""
    if places is None:
        places = infer_decimals(prices)
    scaled = np.round(prices * (10 ** places)).astype(np.int64)
    return (scaled % 10).astype(int), places


# --------------------------------------------------------------------------- #
# Contract universe and resolution
# --------------------------------------------------------------------------- #
# Deriv last-digit contracts. Over 9 and Under 0 are impossible and excluded;
# Match/Differ 0-9; Over 0-8; Under 1-9; Even; Odd.
def contract_universe():
    c = []
    for d in range(10):
        c.append(("MATCH", d))
    for d in range(10):
        c.append(("DIFFER", d))
    for b in range(0, 9):
        c.append(("OVER", b))
    for b in range(1, 10):
        c.append(("UNDER", b))
    c.append(("EVEN", None))
    c.append(("ODD", None))
    return c


def true_prob(kind, barrier):
    """Win probability of a contract under i.i.d.-uniform digits."""
    if kind == "MATCH":
        return 0.1
    if kind == "DIFFER":
        return 0.9
    if kind == "OVER":
        return (9 - barrier) / 10.0
    if kind == "UNDER":
        return barrier / 10.0
    if kind in ("EVEN", "ODD"):
        return 0.5
    raise ValueError(kind)


def resolves_win(kind, barrier, digit):
    """Does a contract win given the settling last digit?"""
    if kind == "MATCH":
        return digit == barrier
    if kind == "DIFFER":
        return digit != barrier
    if kind == "OVER":
        return digit > barrier
    if kind == "UNDER":
        return digit < barrier
    if kind == "EVEN":
        return digit % 2 == 0
    if kind == "ODD":
        return digit % 2 == 1
    raise ValueError(kind)


def winning_contracts_for_digit(digit):
    """
    The friend's 'compound outcome' map: every contract that WINS when `digit`
    settles. The structural invariant is exactly 20 winners / 22 losers for
    every digit (proven in validation/structural_invariant.py).
    """
    return [(k, b) for (k, b) in contract_universe() if resolves_win(k, b, digit)]


# --------------------------------------------------------------------------- #
# Payouts -> EV / house edge
# --------------------------------------------------------------------------- #
def load_payouts(path=None):
    p = path or os.path.join(DATA_DIR, "payouts.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def payout_lookup(payouts, symbol, kind, barrier=None):
    """Map our (kind, barrier) -> Deriv contract_type and fetch the live payout.

    Over/Under payouts depend on the barrier, so we MUST match it. Match/Differ
    payouts are barrier-independent (every digit equally likely); Even/Odd have
    no barrier. (Matching only by contract_type silently pairs e.g. Over0's win
    rate with Over8's payout -> a fake positive EV. Don't.)"""
    ct = {"MATCH": "DIGITMATCH", "DIFFER": "DIGITDIFF", "OVER": "DIGITOVER",
          "UNDER": "DIGITUNDER", "EVEN": "DIGITEVEN", "ODD": "DIGITODD"}[kind]
    barrier_sensitive = kind in ("OVER", "UNDER")
    best = None
    for r in payouts or []:
        if r.get("symbol") != symbol or r.get("contract") != ct or "payout" not in r:
            continue
        if barrier_sensitive and barrier is not None:
            try:
                if int(r.get("params", {}).get("barrier")) != int(barrier):
                    continue
            except (TypeError, ValueError):
                continue
        best = r["payout"]
    return best
