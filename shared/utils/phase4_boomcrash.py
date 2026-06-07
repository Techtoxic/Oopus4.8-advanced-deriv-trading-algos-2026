"""
Phase 4 — Boom & Crash spike-structure analysis on real ticks.
Boom indices spike UP at intervals; Crash spike DOWN. We detect spikes,
measure inter-spike interval distribution, and test whether spike timing is
memoryless (geometric / Poisson-like => unpredictable) or periodic
(=> potentially tradeable).
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.deriv_client import DerivClient
import numpy as np
from scipy import stats

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "boom-crash")
SYMS = ["BOOM500","BOOM1000","BOOM300N","CRASH500","CRASH1000","CRASH300N"]

def analyze(sym, ticks):
    p = np.array([x[1] for x in ticks], dtype=float)
    r = np.diff(p)
    # normalize by typical move
    sd = np.std(r)
    # spikes: moves far beyond normal (Boom=up spikes, Crash=down spikes)
    if sym.startswith("BOOM"):
        spike_idx = np.where(r > 5*sd)[0]      # large up moves
        normal = r[r <= 5*sd]
    else:
        spike_idx = np.where(r < -5*sd)[0]
        normal = r[r >= -5*sd]
    intervals = np.diff(spike_idx)
    res = {
        "symbol": sym, "n_ticks": len(p), "n_spikes": int(len(spike_idx)),
        "mean_interval": float(np.mean(intervals)) if len(intervals) else None,
        "median_interval": float(np.median(intervals)) if len(intervals) else None,
        "std_interval": float(np.std(intervals)) if len(intervals) else None,
        "min_interval": int(np.min(intervals)) if len(intervals) else None,
        "max_interval": int(np.max(intervals)) if len(intervals) else None,
    }
    # Memoryless test: if intervals ~ geometric, std≈mean (CV≈1).
    if len(intervals) > 5:
        cv = np.std(intervals)/np.mean(intervals)
        res["interval_CV"] = float(cv)
        # KS test vs geometric with matched mean
        pmean = 1.0/np.mean(intervals)
        # discrete geometric samples for KS (approx via exponential)
        ks = stats.kstest((intervals-1), 'expon', args=(0, np.mean(intervals)-1))
        res["ks_vs_geometric_p"] = float(ks.pvalue)
        res["min_observed_interval"] = int(np.min(intervals))
        # autocorr of intervals: does a long gap predict the next gap?
        iv = intervals - intervals.mean()
        if np.sum(iv*iv) > 0 and len(iv) > 2:
            res["interval_autocorr_lag1"] = float(np.sum(iv[:-1]*iv[1:])/np.sum(iv*iv))
    # direction of NON-spike ticks: Boom drifts down between spikes?
    res["nonspike_mean_move"] = float(np.mean(normal))
    res["nonspike_down_frac"] = float(np.mean(normal < 0))
    return res

def main():
    c = DerivClient(); c.connect()
    os.makedirs(OUT, exist_ok=True)
    out = {}
    for s in SYMS:
        try:
            ticks = c.ticks_history(s, count=5000)
            r = analyze(s, ticks)
            out[s] = r
            print(f"{s:<10} spikes={r['n_spikes']:<4} mean_int={r['mean_interval']}"
                  f" median={r['median_interval']} min={r['min_interval']}"
                  f" CV={r.get('interval_CV')} ks_geom_p={r.get('ks_vs_geometric_p')}"
                  f" iv_ac1={r.get('interval_autocorr_lag1')}")
            print(f"           between spikes: mean_move={r['nonspike_mean_move']:.4f}"
                  f" down_frac={r['nonspike_down_frac']:.3f}")
        except Exception as e:
            print(s, "ERR", e)
    json.dump(out, open(os.path.join(OUT, "spike_analysis.json"), "w"), indent=2)
    c.close()

if __name__ == "__main__":
    main()
