"""
Phase 1 — Digit contract analysis.
Pulls real historical ticks for each volatility index, computes the Deriv
last-digit stream at correct pip precision, saves raw CSV, and runs the full
randomness/edge battery. Writes per-market JSON results + a combined summary.
"""
import csv
import json
import os
import sys
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.deriv_client import DerivClient
from stats import tests as T
import numpy as np

TARGETS = ["R_10", "R_25", "R_50", "R_75", "R_100",
           "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"]
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "digits", "data")
os.makedirs(DATA_DIR, exist_ok=True)


def get_decimals(client):
    res = client.request({"active_symbols": "full", "product_type": "basic"})
    dec = {}
    for s in res["active_symbols"]:
        pip = s.get("pip")
        if pip and pip > 0:
            dec[s["symbol"]] = max(0, int(round(-math.log10(pip))))
    return dec


def analyze_symbol(client, sym, decimals):
    ticks = client.ticks_history(sym, count=5000)
    times = [t for t, _ in ticks]
    prices = [p for _, p in ticks]
    digits = T.digits_from_prices(prices, decimals)

    # save raw CSV
    csv_path = os.path.join(DATA_DIR, f"{sym}_ticks.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "price", "last_digit"])
        for t, p, d in zip(times, prices, digits):
            w.writerow([t, f"{p:.{decimals}f}", int(d)])

    # tick-to-tick price diffs for autocorr/fft of returns
    pr = np.array(prices, dtype=float)
    rets = np.diff(pr)
    # binary up/down for runs test
    updown = (rets > 0).astype(int)

    results = {
        "symbol": sym,
        "decimals": decimals,
        "n_ticks": len(prices),
        "digit_distribution": T.chi_square_uniform(digits),
        "entropy": T.shannon_entropy(digits),
        "serial_corr_digits": T.serial_correlation_digits(digits),
        "digit_autocorr": T.autocorrelation(digits, [1, 2, 3, 5, 10, 20]),
        "transition_NtoN1": T.conditional_digit_table(digits),
        "predict_lags": [T.conditional_predict_lag(digits, l) for l in [1, 2, 3, 5, 10, 20]],
        "even_odd_runs": T.runs_test((digits % 2 == 0).astype(int)),
        "even_count": int(np.sum(digits % 2 == 0)),
        "odd_count": int(np.sum(digits % 2 == 1)),
        "price_updown_runs": T.runs_test(updown),
        "returns_autocorr": T.autocorrelation(rets, [1, 2, 3, 5, 10, 20]),
        "returns_fft": T.fft_periodicity(rets),
        "digit_fft": T.fft_periodicity(digits.astype(float)),
    }
    return results, csv_path


def main():
    client = DerivClient()
    client.connect()
    decmap = get_decimals(client)
    all_results = {}
    for sym in TARGETS:
        dec = decmap.get(sym, 3)
        try:
            res, path = analyze_symbol(client, sym, dec)
            all_results[sym] = res
            dd = res["digit_distribution"]
            tr = res["transition_NtoN1"]
            print(f"{sym:<9} dec={dec} n={res['n_ticks']:<5} "
                  f"chi2_uniform_p={dd['p_value']:.4f} "
                  f"entropy={res['entropy']['fraction_of_max']:.5f} "
                  f"transition_indep_p={tr['p_value']:.4f} "
                  f"repeat_rate={tr['repeat_rate_next_eq_current']:.4f}")
        except Exception as e:
            print(f"{sym}: ERROR {e}")
        # reconnect occasionally to keep socket healthy
    client.close()

    out = os.path.join(os.path.dirname(__file__), "..", "..", "digits", "data",
                       "phase1_digit_stats.json")
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2)
    print("\nSaved:", out)


if __name__ == "__main__":
    main()
