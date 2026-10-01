"""mult_trunc.py — H10 v2. Measure the stop-out truncation value properly.

WHAT v1 GOT WRONG
  1. Stop-out level. The proposal schema shows limit_order.stop_out.order_amount = -9 on a
     $10 stake, not -10. v1 stopped at -K, so it let positions survive losses that would
     really have been closed. Every favourable number was inflated.
  2. Sample size. max_hold=2000 over 30k ticks gave 15-31 non-overlapping trades per cell.
     The +92.96% headline was 22 trades in one window.
  3. No out-of-sample split. This repo has already been burned once by a fitted result
     (opus's +3.22% deep-gate became -1.67% OOS).
  4. Directional contamination. Cells where the CONTROL direction won (BOOM500, CRASH300N,
     CRASH500) show the numbers were tracking realised drift, not structure.

THE FIX: THE PAIRED ESTIMATOR
Run both directions on identical ticks from identical entry points. Directional drift is
exactly equal and opposite, so it cancels:

    truncation_value = EV_favourable + EV_control + 2 * commission

Validated on v1's output: every cell with stop% = 0 gives exactly 0.000 (to 3 dp), because
with no stop-outs there is no truncation. Cells with stop-outs gave +0.145 at 11.8% stop
rate, +6.705 at 40.9%. Right signature, but on 15-31 trades.

This version:
  - reads the REAL stop-out from limit_order.stop_out.order_amount
  - enters every --stride ticks (overlapping), giving thousands of paired trades
  - reports truncation_value with a bootstrap CI, not just a point estimate
  - splits the sample in half: fit on the first, report OOS on the second
  - flags any cell where the two halves disagree in sign

Read-only. Places no trades.

Run: python3 mult_trunc.py --symbols BOOM300N CRASH300N BOOM1000 CRASH1000
     python3 mult_trunc.py --ticks 60000 --stride 25
"""
import argparse, json, time
import numpy as np
from deriv_api import DerivWS

DEFAULT = ["BOOM300N", "BOOM500", "BOOM900", "BOOM1000",
           "CRASH300N", "CRASH500", "CRASH900", "CRASH1000"]


def mult_proposal(ws, sym, ctype, mult, stake):
    r = ws.call({"proposal": 1, "amount": stake, "basis": "stake",
                 "contract_type": ctype, "currency": "USD",
                 "underlying_symbol": sym, "multiplier": mult})
    if "proposal" not in r:
        return None
    p = r["proposal"]
    so = (p.get("limit_order") or {}).get("stop_out") or {}
    amt = so.get("order_amount")
    return {"commission": float(p.get("commission") or 0.0),
            "stop_out": abs(float(amt)) if amt is not None else stake,
            "spot": float(p.get("spot") or 0.0)}


def run_pairs(prices, spike_mask, fav_dir, M, K, comm, stop_at, max_hold, stride):
    """
    Both directions from the SAME entry indices. Returns arrays of per-pair
    favourable pnl, control pnl, stop flags, and spike-attribution.
    """
    n = len(prices)
    fav, ctl, fstop, cstop, fspike = [], [], [], [], []
    for i in range(0, n - max_hold - 1, stride):
        entry = prices[i]
        seg = prices[i + 1: i + 1 + max_hold]
        move = (seg - entry) / entry

        out = {}
        for tag, d in (("f", fav_dir), ("c", -fav_dir)):
            pnl_path = K * M * d * move - comm
            hit = np.where(pnl_path <= -stop_at)[0]
            if len(hit):
                j = int(hit[0])
                out[tag] = (-stop_at, True, bool(spike_mask[i + j]) if i + j < len(spike_mask) else False)
            else:
                out[tag] = (float(pnl_path[-1]), False, False)

        fav.append(out["f"][0]); fstop.append(out["f"][1]); fspike.append(out["f"][2])
        ctl.append(out["c"][0]); cstop.append(out["c"][1])
    return (np.array(fav), np.array(ctl), np.array(fstop),
            np.array(cstop), np.array(fspike))


def boot_ci(x, n_boot=2000, alpha=0.01):
    if len(x) < 20:
        return float("nan"), float("nan")
    rng = np.random.default_rng(0)
    ms = np.array([rng.choice(x, len(x), replace=True).mean() for _ in range(n_boot)])
    return float(np.quantile(ms, alpha / 2)), float(np.quantile(ms, 1 - alpha / 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT)
    ap.add_argument("--ticks", type=int, default=60000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--mults", type=int, nargs="+", default=[100, 200, 300, 400])
    ap.add_argument("--max-hold", type=int, default=600)
    ap.add_argument("--stride", type=int, default=25)
    ap.add_argument("--out", default="../results/mult_trunc.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    md = ["# H10 v2 — stop-out truncation value (paired estimator)\n\n",
          "`trunc = EV_fav + EV_ctl + 2*commission`. Drift cancels between mirrored ",
          "positions, so this isolates the structural effect.\n\n",
          "Split-half: IS = first half of ticks, OOS = second half. Sign disagreement ",
          "between halves means the cell is noise.\n\n",
          "| symbol | M | stop_out | pairs | stop% | spike% | trunc IS | trunc OOS | "
          "OOS 99% CI | agree |\n",
          "|---|---:|---:|---:|---:|---:|---:|---:|---|:--:|\n"]

    print(f"{'symbol':11}{'M':>5}{'SO':>6}{'pairs':>7}{'stop%':>7}{'spk%':>6}"
          f"{'truncIS':>10}{'truncOOS':>10}{'OOS 99% CI':>22}{'ok':>4}")
    print("-" * 90)

    survivors = []
    for sym in a.symbols:
        is_boom = sym.startswith("BOOM")
        fav_ct = "MULTDOWN" if is_boom else "MULTUP"
        fav_dir = -1 if is_boom else +1
        try:
            _, prices, _ = ws.history_paged(sym, a.ticks, sleep=0.2)
        except Exception as e:
            print(f"{sym:11} fetch error: {e}"); continue
        prices = np.asarray(prices, dtype=float)
        if len(prices) < 20000:
            print(f"{sym:11} only {len(prices)} ticks"); continue

        rel = np.abs(np.diff(prices) / prices[:-1])
        med = float(np.median(rel)); mad = float(np.median(np.abs(rel - med))) or 1e-12
        spike_mask = rel > (med + 5 * 1.4826 * mad)

        half = len(prices) // 2
        for M in a.mults:
            pr = mult_proposal(ws, sym, fav_ct, M, a.stake)
            time.sleep(0.1)
            if pr is None:
                continue
            comm, so = pr["commission"], pr["stop_out"]

            res = {}
            for tag, sl in (("IS", slice(0, half)), ("OOS", slice(half, None))):
                p = prices[sl]
                sm = spike_mask[sl.start or 0: (sl.stop or len(spike_mask))]
                if len(p) < a.max_hold + 100:
                    res[tag] = None; continue
                f, c, fs, cs, fk = run_pairs(p, sm, fav_dir, M, a.stake,
                                             comm, so, a.max_hold, a.stride)
                if len(f) < 20:
                    res[tag] = None; continue
                trunc_series = f + c + 2 * comm
                res[tag] = dict(n=len(f), trunc=float(trunc_series.mean()),
                                series=trunc_series,
                                stop=float(fs.mean()),
                                spike=float(fk.sum() / max(fs.sum(), 1)))
            if not res.get("IS") or not res.get("OOS"):
                continue

            lo, hi = boot_ci(res["OOS"]["series"])
            agree = (res["IS"]["trunc"] > 0) == (res["OOS"]["trunc"] > 0)
            ok = agree and lo > 0
            if ok:
                survivors.append((res["OOS"]["trunc"], sym, M, lo, hi))

            print(f"{sym:11}{M:>5}{so:>6.1f}{res['OOS']['n']:>7}"
                  f"{res['OOS']['stop']*100:>7.1f}{res['OOS']['spike']*100:>6.0f}"
                  f"{res['IS']['trunc']:>10.4f}{res['OOS']['trunc']:>10.4f}"
                  f"  [{lo:>+8.4f},{hi:>+8.4f}]{'YES' if ok else 'no':>4}")
            md.append(f"| {sym} | {M} | {so:.1f} | {res['OOS']['n']} | "
                      f"{res['OOS']['stop']*100:.1f}% | {res['OOS']['spike']*100:.0f}% | "
                      f"{res['IS']['trunc']:+.4f} | {res['OOS']['trunc']:+.4f} | "
                      f"[{lo:+.4f}, {hi:+.4f}] | {'YES' if ok else 'no'} |\n")
        time.sleep(0.2)

    print()
    if survivors:
        survivors.sort(reverse=True)
        md.append("\n## Survivors (IS/OOS sign agreement AND OOS 99% CI above zero)\n\n")
        print("SURVIVORS — sign-consistent across halves with OOS 99% CI above zero:")
        for t, sym, M, lo, hi in survivors:
            print(f"  {sym:11} M={M:<4} trunc {t:+.4f}/pair  CI [{lo:+.4f}, {hi:+.4f}]")
            md.append(f"- `{sym}` M={M}: {t:+.4f}/pair, 99% CI [{lo:+.4f}, {hi:+.4f}]\n")
        print("\n  NOTE: truncation value is not a tradeable P&L on its own. It says the")
        print("  stop-out is worth something structurally. Turning that into a strategy")
        print("  needs a directional view or a real straddle, and both cost more spread.")
        md.append("\nTruncation value is a structural quantity, not a tradeable P&L. "
                  "Converting it into a strategy requires a directional view or a genuine "
                  "straddle, and both carry additional spread.\n")
    else:
        print("NO SURVIVORS. Truncation value does not hold up out of sample.")
        print("v1's positive numbers were small-sample artifacts (15-31 trades) plus a")
        print("stop-out level set at -K instead of the real -9.")
        md.append("\n**No survivors.** Truncation value does not survive the split-half "
                  "test. v1's positives were small-sample artifacts plus a wrong stop-out.\n")

    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
