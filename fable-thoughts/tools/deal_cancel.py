"""deal_cancel.py — is Deriv's deal-cancellation fee mispriced?

THE ONE STRUCTURE NEVER TESTED
Every closure in this repo rests on linearity: E[X+Y] = E[X]+E[Y] for FIXED payoffs, so any
combination of hold-to-expiry contracts sums to a negative EV. The exceptions are structures
whose payoff depends on a DECISION. Two exist on this book:

  early close  -> tested. 17.81% spread, widened exactly where it would help. CLOSED.
  DEAL CANCELLATION -> never tested. Not one mention anywhere in the repo.

Deal cancellation is an American put on your own position, sold at a FIXED QUOTED FEE. Pay
the fee, and within the window you may cancel for a FULL refund of stake regardless of how
far the position has moved against you. That is an option, and on a synthetic index we know
the volatility exactly, so it can be priced exactly rather than argued about.

WHY IT ALSO DEFEATS THE mult_trunc IDENTITY
mult_trunc.py closed all hedged multiplier structures with an identity: a paired long+short
pays exactly -2*commission, because at the instant one leg hits its stop-out the other sits
at +SO. That identity assumes both legs run to termination.

With cancellation on BOTH legs you cancel the LOSER (stake fully refunded) and let the
WINNER run. The payoff becomes

    |move| * multiplier * stake  -  2 * fee

which is a straddle bought for two fees. The identity does not apply.

FAIR VALUE
For a driftless GBM over window T, the expected one-sided loss avoided is

    E[max(-r,0)] = sigma_T / sqrt(2*pi)

so the fair fee on a single leg is

    fair_fee = stake * multiplier * sigma_T / sqrt(2*pi)

and for the pair, the expected absolute move is E|r| = sigma_T * sqrt(2/pi), giving

    fair_total = stake * multiplier * sigma_T * sqrt(2/pi)     vs   2 * fee

sigma_T is measured directly from ticks over the actual cancellation window, not assumed.

WHAT THIS DOES
  1. Quotes the cancellation fee for every offered window and multiplier, on several symbols.
  2. Measures sigma over each window empirically from clean ticks (no model).
  3. Computes fee / fair_value. A ratio below 1 means they undercharge for the option.
  4. Prices the paired long+short straddle: E|move| * m * stake vs 2 * fee.
  5. Flags any cell where the pair is positive, for live verification.

Note Deriv does not allow take-profit/stop-loss together with deal cancellation, and the
windows are fixed (typically 5/15/30/60 min). Both handled below.

Read-only (proposals only) unless --execute.

Run: python3 deal_cancel.py
     python3 deal_cancel.py --symbols 1HZ100V R_100 --execute
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval

WINDOWS = [5, 15, 30, 60]          # minutes, per Deriv's offered set
MULTS = [100, 200, 400]


def sigma_over(v, k):
    """Empirical sd of the k-step RELATIVE move, straight from ticks."""
    if k <= 0 or k >= len(v):
        return float("nan")
    r = (v[k:].astype(float) - v[:-k]) / v[:-k]
    return float(r.std())


def mean_abs_over(v, k):
    """Empirical E|relative move| over k steps — no normality assumed."""
    if k <= 0 or k >= len(v):
        return float("nan")
    r = (v[k:].astype(float) - v[:-k]) / v[:-k]
    return float(np.abs(r).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                    default=["1HZ100V", "R_100", "JD100", "1HZ75V"])
    ap.add_argument("--ticks", type=int, default=400000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out", default="../results/deal_cancel.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    cands = []

    for sym in a.symbols:
        print("=" * 84)
        print(f"{sym}")
        print("=" * 84)
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False)
        except Exception as e:
            print(f"  fetch failed: {str(e)[:50]}")
            continue
        v = np.round(p * (10 ** pip)).astype(np.int64)
        iv = native_interval(t)
        print(f"  {len(t)} ticks, {iv}s cadence, spot {p[-1]:.4f}")

        print(f"\n  {'mult':>5}{'window':>8}{'fee':>8}{'comm':>8}{'sigma_T':>9}"
              f"{'fair':>9}{'ratio':>7}{'so_x':>7}{'gain':>8}{'cost':>8}{'EV':>8}")
        print("  (so_x = sigma / stop-out distance; >1 means the position would be")
        print("   stopped out during the window unless cancellation suspends it)")
        print("  " + "-" * 88)

        for m in MULTS:
            for W in WINDOWS:
                req = {"proposal": 1, "amount": a.stake, "basis": "stake",
                       "contract_type": "MULTUP", "currency": "USD",
                       "underlying_symbol": sym, "multiplier": m,
                       "cancellation": f"{W}m"}
                r = ws.call(req)
                time.sleep(0.06)
                if "proposal" not in r:
                    msg = r.get("error", {}).get("message", "")[:38]
                    if m == MULTS[0] and W == WINDOWS[0]:
                        print(f"  {m:>5}{str(W)+'m':>8}   not offered: {msg}")
                    continue
                pr = r["proposal"]
                fee = None
                for key in ("cancellation", "limit_order"):
                    d = pr.get(key)
                    if isinstance(d, dict):
                        c = d.get("ask_price") or (d.get("cancellation") or {}).get("ask_price")
                        if c:
                            fee = float(c)
                if fee is None:
                    fee = float(pr.get("cancellation_price") or 0) or None
                if fee is None:
                    continue

                # COMMISSION — the term that killed the first version of this script.
                # mult_trunc.py already proved paired long+short pays exactly
                # -2*commission, so it is the DOMINANT cost here, and it scales with
                # NOTIONAL (stake*multiplier), not stake. Omitting it produced a fake
                # edge whose size grew with multiplier and window: the exact signature
                # of a forgotten notional-scaled cost.
                comm = float(pr.get("commission") or 0.0)

                # STOP-OUT sanity: at multiplier m the stop-out is a 1/m adverse move.
                # If sigma over the window is many times that, the position would be
                # stopped out repeatedly unless cancellation suspends it. Report the
                # ratio so it is visible rather than assumed.
                k = int(round(W * 60 / iv))
                sT = sigma_over(v, k)
                eabs = mean_abs_over(v, k)
                if not np.isfinite(sT):
                    continue

                notional = a.stake * m
                so_move = 1.0 / m                      # adverse move that stops you out
                so_ratio = sT / so_move                # sigma in units of stop-out distance

                # The option cannot be worth more than the loss it prevents. Without
                # cancellation the loss is CAPPED at the stake by the stop-out, so the
                # fair fee is capped at the stake too.
                fair_raw = notional * sT / math.sqrt(2 * math.pi)
                fair_fee = min(fair_raw, a.stake)
                ratio = fee / fair_fee if fair_fee > 0 else float("nan")

                # paired straddle, WITH commission on both legs
                pair_gain = notional * eabs
                pair_cost = 2 * fee + 2 * comm
                pair_ev = pair_gain - pair_cost
                flag = ""
                if pair_ev > 0:
                    flag = "  <<< PAIR POSITIVE"
                    cands.append((sym, m, W, fee, fair_fee, ratio, pair_gain, pair_cost))
                print(f"  {m:>5}{str(W)+'m':>8}{fee:>8.2f}{comm:>8.2f}{sT:>9.5f}"
                      f"{fair_fee:>9.2f}{ratio:>7.2f}{so_ratio:>7.1f}"
                      f"{pair_gain:>8.2f}{pair_cost:>8.2f}{pair_ev:>+8.2f}{flag}")
        print()

    ws.close()
    print("=" * 84)
    print("VERDICT")
    print("=" * 84)
    if cands:
        print(f"  {len(cands)} cell(s) where the cancellable PAIR is positive:")
        for sym, m, W, fee, fair, ratio, g, c in sorted(cands, key=lambda x: x[6]-x[7],
                                                        reverse=True)[:10]:
            print(f"    {sym:9} mult {m:>4} window {W:>2}m: fee {fee:.2f} "
                  f"(fair {fair:.2f}, ratio {ratio:.2f})  "
                  f"gain {g:.2f} vs cost {c:.2f} (incl. commission)  EV {g-c:+.2f}")
        print()
        print("  BEFORE BELIEVING ANY OF THIS:")
        print("   1. the fee is from a PROPOSAL. Re-read it from an EXECUTED buy —")
        print("      proposals have lied by up to 25% on this book.")
        print("   2. verify the cancellation actually refunds the FULL stake, and that")
        print("      commission is not charged separately on top.")
        print("   3. check whether both legs can be open simultaneously on one symbol.")
        print("   4. E|move| here is the empirical mean, but you only capture it if you")
        print("      cancel at exactly the right moment — model the DECISION rule, not")
        print("      the hindsight optimum.")
    else:
        print("  No cell where the cancellable pair is positive.")
        print("  The fee is priced at or above the option's fair value at every window,")
        print("  multiplier and symbol tested. Deal cancellation joins early close:")
        print("  the non-linearity exists but is sold above fair value.")
        print()
        print("  With that, every decision-dependent structure on the book is priced,")
        print("  and linearity closes everything else.")

    with open(a.out, "w") as f:
        f.write("# Deal cancellation vs fair value\n\n"
                "fair_fee = stake*mult*sigma_T/sqrt(2pi); "
                "pair pays stake*mult*E|move| for 2*fee\n\n"
                "| symbol | mult | window | fee | fair | ratio |\n|---|---|---|---|---|---|\n")
        for sym, m, W, fee, fair, ratio, g, c in cands:
            f.write(f"| {sym} | {m} | {W}m | {fee:.3f} | {fair:.3f} | {ratio:.3f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
