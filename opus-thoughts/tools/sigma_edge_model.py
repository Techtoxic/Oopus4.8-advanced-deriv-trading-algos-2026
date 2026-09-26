"""JD100 sigma->edge theory: P(next-digit window | current digit) as a function of sigma_pips,
calibrated against the empirical step distribution (rescaled bootstrap) and discretized normal.
Answers: at what spot/sigma does the conditional over/under edge go positive, and by how much.
Run: python3 sigma_edge_model.py
Out: ../results/sigma_edge_model.md
"""
import gzip, math
import numpy as np

PAYOUT_OU = 1.953          # OVER4/UNDER5 measured multiplier (re-quoted live by the trader)
THRESH = 1 / PAYOUT_OU

def load_steps(sym="JD100"):
    ps = []
    with gzip.open(f"../data/{sym}.csv.gz", "rt") as f:
        f.readline()
        for line in f:
            ps.append(float(line.strip().split(",")[1]))
    ps = np.array(ps)
    v = np.round(ps * 100).astype(np.int64)
    st = np.diff(v)
    return ps, st

def p_window_normal(sigma, half=2):
    """P(s mod 10 in {-half..half}) for s ~ round(N(0,sigma)), exact by summation."""
    ks = np.arange(-2000, 2001)
    from math import erf
    def cdf(x): return 0.5 * (1 + erf(x / (sigma * math.sqrt(2))))
    pmf = np.array([cdf(k + 0.5) - cdf(k - 0.5) for k in ks])
    sel = (np.abs(((ks + 5) % 10) - 5) <= half)
    return float(pmf[sel].sum())

def p_window_bootstrap(steps, sigma_target, half=2, jump_thr=20, reps=8):
    """rescale empirical no-jump steps to sigma_target with continuous jitter (kills lattice
    artifacts), keep jumps as-is."""
    rng = np.random.default_rng(7)
    nj = steps[np.abs(steps) <= jump_thr].astype(float)
    j = steps[np.abs(steps) > jump_thr]
    s0 = nj.std()
    acc = 0.0
    for _ in range(reps):
        cont = (nj + rng.uniform(-0.5, 0.5, len(nj))) * (sigma_target / s0)
        scaled = np.round(cont).astype(np.int64)
        allm = np.concatenate([scaled, j])
        m = ((allm + 5) % 10) - 5
        acc += (np.abs(m) <= half).mean()
    return float(acc / reps)

def main():
    ps, st = load_steps()
    nj = st[np.abs(st) <= 20]
    sigma_now = nj.std()
    spot_now = ps[-1]
    md = ["# JD100 sigma -> conditional edge model\n\n",
          f"sample: {len(ps)} ticks, spot {ps.min():.2f}..{ps.max():.2f} (last {spot_now:.2f}), ",
          f"sigma_nojump = {sigma_now:.2f} pips, jump frac = {(np.abs(st)>20).mean():.4f}\n\n",
          "Contract: UNDER5 bought when current digit = 2 (window {0..4} = d±2), or OVER4 when d = 7.\n",
          f"Break-even P = 1/{PAYOUT_OU} = {THRESH:.4f}. EV = P×{PAYOUT_OU}−1.\n\n",
          "| sigma_pips | spot(JD100) | P_theory(normal) | P_bootstrap | EV/trade |\n|---:|---:|---:|---:|---:|\n"]
    # spot = sigma / (1.782e-4 * 100) for vol 100%/yr 1s ticks, pip 0.01
    for sigma in [2.0, 2.5, 3.0, 3.5, 4.0, 4.3, 4.6, 5.0, 5.5, 6.0, 7.0, 8.0]:
        spot = sigma / (1.782e-4 * 100)
        pn = p_window_normal(sigma)
        pb = p_window_bootstrap(st, sigma)
        ev = pb * PAYOUT_OU - 1
        md.append(f"| {sigma:.1f} | {spot:.0f} | {pn:.4f} | {pb:.4f} | {ev*100:+.2f}% |\n")
    md.append("\nEmpirical calibration (chunked 200k sample): sigma 4.28 -> P(U5|d2)=0.532, "
              "sigma 4.54 -> 0.518; bootstrap column should bracket these.\n")
    md.append(f"\n**Regime gate**: trade only when sigma_nojump(rolling) <= ~4.3 pips "
              f"(JD100 spot <= ~{4.3/(1.782e-4*100):.0f}). Today: sigma={sigma_now:.2f}, spot={spot_now:.2f}.\n")
    md.append("\nlag-2 (missed tick) kills it: measured P(U5|d2)@lag2 = 0.504 -> EV −1.5%. "
              "Execution must land inside the 1s gap (single buy-with-parameters call, no proposal round-trip).\n")
    open("../results/sigma_edge_model.md", "w").write("".join(md))
    print("".join(md))

if __name__ == "__main__":
    main()
