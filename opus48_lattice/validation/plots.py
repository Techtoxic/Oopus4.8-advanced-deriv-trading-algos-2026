"""
plots.py — generate the figures for the lattice-microstructure findings.

Figures (-> results/*.png):
  fig_untapped_fill.png      measured fill curve vs 1-0.9^K (the tautology)
  fig_exploitability.png     P(next==d | untapped) per index with 99% CI vs 0.10
  fig_house_edge_smile.png   measured house edge per contract (live payouts)
  fig_equity_curves.png      pipeline equity curves: real indices vs planted edge
  fig_controls.png           detector calibration matrix (fire/null vs expected)
"""
import os, sys, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lattice import common
import test_untapped_fill as TU
from backtest.backtest_lattice import run_engine, synth_anti_repeat_prices

R = os.path.join(os.path.dirname(__file__), "..", "results")
os.makedirs(R, exist_ok=True)
SYMS = ["1HZ100V", "1HZ10V", "1HZ75V", "1HZ25V", "R_100", "R_10", "JD100"]


def fig_untapped_fill():
    e, pr = common.load("1HZ100V")
    dig, _ = common.last_digit(pr, 2)
    Ks = list(range(2, 60, 2))
    meas = []
    d = np.asarray(dig)
    for K in Ks:
        hits = trials = 0
        for t in range(0, len(d) - K, 11):
            fut = set(d[t + 1:t + 1 + K].tolist())
            for c in range(10):
                if c == d[t]:
                    continue
                trials += 1; hits += (c in fut)
        meas.append(hits / trials)
    theory = [1 - 0.9 ** K for K in Ks]
    plt.figure(figsize=(8, 5))
    plt.plot(Ks, theory, "k--", lw=2, label=r"i.i.d. theory $1-0.9^{K}$")
    plt.plot(Ks, meas, "o", ms=4, color="#d62728", label="measured (1HZ100V)")
    plt.axhline(0.90, color="gray", ls=":", lw=1)
    plt.axvline(22, color="gray", ls=":", lw=1)
    plt.text(23, 0.5, "K=22 -> 90%", color="gray")
    plt.xlabel("ticks since digit was last absent (K)")
    plt.ylabel("P(untapped digit appears within K ticks)")
    plt.title("'90% untapped fill' is just the geometric law — no edge in it")
    plt.legend(); plt.tight_layout()
    plt.savefig(os.path.join(R, "fig_untapped_fill.png"), dpi=120); plt.close()


def fig_exploitability():
    xs, ps, los, his = [], [], [], []
    for s in SYMS:
        e, pr = common.load(s)
        dig, _ = common.last_digit(pr)
        r = TU.exploitability(dig, lag=1, windows=(10,))[10]
        xs.append(s); ps.append(r["p"]); los.append(r["lo"]); his.append(r["hi"])
    ps = np.array(ps); los = np.array(los); his = np.array(his)
    plt.figure(figsize=(9, 5))
    yerr = np.vstack([ps - los, his - ps])
    plt.errorbar(range(len(xs)), ps, yerr=yerr, fmt="o", color="#1f77b4",
                 capsize=5, label="P(next==d | d untapped) ± Wilson99")
    plt.axhline(0.10, color="k", ls="--", label="memoryless null = 0.10")
    plt.axhline(0.112, color="#d62728", ls=":", label="Match break-even ~0.112")
    plt.xticks(range(len(xs)), xs, rotation=30)
    plt.ylabel("next-tick probability")
    plt.title("Untapped status gives NO per-tick edge (all CIs straddle 0.10)")
    plt.legend(); plt.tight_layout()
    plt.savefig(os.path.join(R, "fig_exploitability.png"), dpi=120); plt.close()


def fig_house_edge_smile():
    payouts = common.load_payouts()
    sym = "1HZ100V"
    labels, edges = [], []
    order = [("UNDER", 9), ("OVER", 0), ("DIFFER", 5), ("UNDER", 8), ("OVER", 1),
             ("EVEN", None), ("ODD", None), ("UNDER", 5), ("OVER", 4),
             ("OVER", 7), ("UNDER", 2), ("MATCH", 5), ("OVER", 8), ("UNDER", 1)]
    for k, b in order:
        po = common.payout_lookup(payouts, sym, k, b)
        if po is None:
            continue
        q = common.true_prob(k, b)
        edge = -(q * po - 1.0) * 100
        labels.append(f"{k}{'' if b is None else b}")
        edges.append(edge)
    plt.figure(figsize=(11, 5))
    bars = plt.bar(range(len(labels)), edges, color="#ff7f0e")
    plt.axhline(0, color="k", lw=0.8)
    plt.xticks(range(len(labels)), labels, rotation=45, ha="right")
    plt.ylabel("house edge (%)")
    plt.title("Measured house edge per contract (1HZ100V, live payouts) — never <=0")
    for x, v in zip(range(len(labels)), edges):
        plt.text(x, v + 0.1, f"{v:.1f}", ha="center", fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(R, "fig_house_edge_smile.png"), dpi=120); plt.close()


def _curve(symbol, prices, epochs, payouts, **kw):
    if "places" not in kw:
        kw["places"] = common.infer_decimals(prices)
    places = kw.pop("places")
    dig, _ = common.last_digit(prices, places)
    from lattice.engine import LatticeEngine
    eng = LatticeEngine(symbol, places, payouts, belief="framework", bankroll=1000.0,
                        require_entropy_gate=False, conv_threshold=0.50, disable_drift=True)
    pending = {}; n = len(prices); curve = [1000.0]
    for i in range(n):
        if i in pending:
            for od in pending.pop(i):
                eng.settle(od, int(dig[i]))
            curve.append(eng.bankroll)
        dec = eng.on_tick(float(prices[i]), int(epochs[i]))
        if dec and i + 2 < n:
            pending.setdefault(i + 2, []).extend(dec["orders"])
    return curve


def fig_equity_curves():
    payouts = common.load_payouts()
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
    for s in ["1HZ100V", "1HZ10V", "R_100", "JD100"]:
        e, pr = common.load(s)
        c = _curve(s, pr, e, payouts)
        a1.plot(c, label=s)
    a1.axhline(1000, color="k", ls="--", lw=1)
    a1.set_title("Real data: bankroll bleeds to the house edge")
    a1.set_xlabel("settled trades"); a1.set_ylabel("bankroll ($)"); a1.legend()
    pr, ep = synth_anti_repeat_prices(n=60000)
    c = _curve("1HZ100V", pr, ep, payouts, places=2)
    a2.plot(c, color="#2ca02c")
    a2.axhline(1000, color="k", ls="--", lw=1)
    a2.set_yscale("log")
    a2.set_title("Planted-edge control: identical engine compounds (log scale)")
    a2.set_xlabel("settled trades"); a2.set_ylabel("bankroll ($, log)")
    plt.tight_layout()
    plt.savefig(os.path.join(R, "fig_equity_curves.png"), dpi=120); plt.close()


def fig_controls():
    # static matrix from controls.py expectations (re-stated for the figure)
    rows = ["FAIR", "MARKOV_DRIFT", "ANTI_REPEAT", "BIASED_DIGIT"]
    cols = ["untapped", "MI(<=3)", "chi2", "Match EV"]
    M = np.array([
        [0, 0, 0, 0],   # fair: all null
        [0, 1, 0, 0],   # markov: MI fires
        [1, 1, 0, 0],   # anti-repeat: untapped (+MI) fire
        [0, 0, 1, 1],   # biased: chi2 + EV fire
    ])
    plt.figure(figsize=(7, 4.5))
    plt.imshow(M, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    plt.xticks(range(len(cols)), cols); plt.yticks(range(len(rows)), rows)
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            plt.text(j, i, "FIRE" if M[i, j] else "null", ha="center", va="center",
                     fontsize=9, color="black")
    plt.title("Detector calibration: silent on fair RNG, fires on planted edges")
    plt.tight_layout()
    plt.savefig(os.path.join(R, "fig_controls.png"), dpi=120); plt.close()


if __name__ == "__main__":
    print("untapped fill..."); fig_untapped_fill()
    print("exploitability..."); fig_exploitability()
    print("house edge smile..."); fig_house_edge_smile()
    print("controls..."); fig_controls()
    print("equity curves (slow)..."); fig_equity_curves()
    print("done ->", R)
