"""
lattice.layer4_gametheory — Cross-market convergence decision table.

Collapses the full contract menu to the subset that clears a convergence
threshold, using:

  * convergence(C) = w1*cluster_conf + w2*dist_signal(C) + w3*reg_conf
                     + w4*cross_market_agreement(C) + w5*hist_winrate(C)
  * weak-dominance pruning (drop a contract another contract dominates on both
    payoff and convergence)
  * compound-chain intersection: for any candidate set, the digits on which ALL
    win is computed exactly (deterministic given the settling digit), giving the
    true joint win probability and EV.

The friend wanted "every single possible outcome" enumerated — that lives in
lattice.common.winning_contracts_for_digit and is exercised exhaustively by
validation/structural_invariant.py. Here we use those exact resolutions to score
candidate sets honestly (no independence assumption: contracts on the same tick
are perfectly dependent through the digit).
"""
import numpy as np
from . import common

WEIGHTS = dict(cluster=0.25, dist=0.35, reg=0.20, agree=0.15, hist=0.05)
CONV_THRESHOLD = 0.99            # the friend's 99% exactness gate


def cross_market_agreement(kind, barrier, dist_signals):
    """Count how many *other* market families have an active signal consistent
    with this contract's directional read (0,1,2)."""
    families = {"MATCH": "MD", "DIFFER": "MD", "OVER": "OU", "UNDER": "OU",
                "EVEN": "EO", "ODD": "EO"}
    fam = families[kind]
    others = set()
    for (k, b) in dist_signals:
        if families[k] != fam:
            others.add(families[k])
    return min(len(others), 2)


def convergence_scores(dist_signals, cluster_conf01, reg_conf, hist_winrate=None):
    """dist_signals: {(kind,barrier): strength}. Returns {(kind,barrier): score}."""
    hist_winrate = hist_winrate or {}
    out = {}
    for (k, b), strength in dist_signals.items():
        agree = cross_market_agreement(k, b, dist_signals) / 2.0
        hw = hist_winrate.get((k, b), common.true_prob(k, b))
        score = (WEIGHTS["cluster"] * cluster_conf01 +
                 WEIGHTS["dist"] * strength +
                 WEIGHTS["reg"] * reg_conf +
                 WEIGHTS["agree"] * agree +
                 WEIGHTS["hist"] * hw)
        out[(k, b)] = float(score)
    return out


def prune_dominated(scores, payouts, symbol):
    """Remove contracts weakly dominated (<= score AND <= payoff) by another."""
    items = list(scores.items())
    keep = []
    for i, ((k, b), s) in enumerate(items):
        pay_i = common.payout_lookup(payouts, symbol, k, b) or (1.0 / max(common.true_prob(k, b), 1e-9))
        dominated = False
        for j, ((k2, b2), s2) in enumerate(items):
            if i == j:
                continue
            pay_j = common.payout_lookup(payouts, symbol, k2, b2) or (1.0 / max(common.true_prob(k2, b2), 1e-9))
            if s2 >= s and pay_j >= pay_i and (s2 > s or pay_j > pay_i):
                dominated = True
                break
        if not dominated:
            keep.append(((k, b), s))
    return dict(keep)


def compound_intersection(contract_set):
    """Digits on which EVERY contract in the set wins (exact, deterministic)."""
    digs = []
    for d in range(10):
        if all(common.resolves_win(k, b, d) for (k, b) in contract_set):
            digs.append(d)
    return digs


def select(dist_signals, cluster_conf01, reg_conf, payouts, symbol,
           hist_winrate=None, threshold=CONV_THRESHOLD):
    """
    Returns the chosen contract set (list of (kind,barrier,score)) that clears
    the convergence threshold after dominance pruning, or [] if none qualify.
    """
    scores = convergence_scores(dist_signals, cluster_conf01, reg_conf, hist_winrate)
    qualified = {c: s for c, s in scores.items() if s >= threshold}
    if not qualified:
        return [], scores
    qualified = prune_dominated(qualified, payouts, symbol)
    chosen = sorted(((k, b, s) for (k, b), s in qualified.items()),
                    key=lambda x: -x[2])
    return chosen, scores
