"""regime_scan.py — find which symbols are in the tradeable sigma zone.

JD100 was re-priced (MATCH 8.929 -> 6.667) after the exploit. The physics is
symbol-agnostic: any index whose spot has decayed far enough that rolling sigma
falls to ~<=4.35 pips has a non-flat digit pmf while the payout grid stays static.

This scans every candidate symbol and reports:
  - current spot, pip size, median tick interval
  - rolling sigma on the SAME estimator sentinel_v2 uses (W=1800, jump filter 20)
  - live executed MATCH/DIFF payouts (detects re-pricing per symbol)
  - empirical digit pmf skew on the fetched window (chi2 vs uniform)
  - verdict: IN ZONE / marginal / flat

Run: python3 regime_scan.py
     python3 regime_scan.py --symbols JD75 JD50 JD25
"""
import argparse, math, time, collections
from deriv_api import DerivWS

W = 1800
JUMP_THR = 20

# candidates: jump indices + standard vol indices + 1s variants
DEFAULT_SYMS = [
    "JD10", "JD25", "JD50", "JD75", "JD100", "JD150", "JD200",
    "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
]


def rolling_sigma(prices, pip):
    """Same estimator as sentinel_v2.SymState.sigma()."""
    vals = [round(float(q) * (10 ** pip)) for q in prices]
    sq = collections.deque(maxlen=W)
    cn = collections.deque(maxlen=W)
    sum_sq, sum_cn = 0.0, 0
    for i in range(1, len(vals)):
        st = vals[i] - vals[i - 1]
        nj = 1 if abs(st) <= JUMP_THR else 0
        s = float(st * st) if nj else 0.0
        if len(sq) == W:
            sum_sq -= sq[0]; sum_cn -= cn[0]
        sq.append(s); cn.append(nj)
        sum_sq += s; sum_cn += nj
    if sum_cn < 200:
        return None
    return math.sqrt(sum_sq / sum_cn)


def digit_chi2(prices, pip):
    """Chi2 of last-digit distribution vs uniform. High = exploitable skew."""
    digits = [int(f"{float(q):.{pip}f}"[-1]) for q in prices]
    n = len(digits)
    if n < 500:
        return None, None
    cnt = collections.Counter(digits)
    exp = n / 10
    chi2 = sum((cnt.get(d, 0) - exp) ** 2 / exp for d in range(10))
    return chi2, cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMS)
    ap.add_argument("--sigma-max", type=float, default=4.35)
    ap.add_argument("--ticks", type=int, default=W + 2)
    ap.add_argument("--no-payouts", action="store_true",
                    help="skip live payout probe (faster, but misses re-pricing)")
    a = ap.parse_args()

    ws = DerivWS(token="")
    rows = []

    for sym in a.symbols:
        try:
            h = ws.ticks_history(sym, count=a.ticks)
            if "error" in h:
                print(f"{sym:10} unavailable: {h['error'].get('message','')[:50]}")
                continue
            pip = int(h["pip_size"])
            prices = h["history"]["prices"]
            times = h["history"]["times"]
            spot = float(prices[-1])

            ivs = sorted(times[i + 1] - times[i] for i in range(len(times) - 1))
            interval = ivs[len(ivs) // 2] if ivs else None

            sigma = rolling_sigma(prices, pip)
            chi2, cnt = digit_chi2(prices, pip)

            # live payouts — the re-pricing detector
            m_pay = d_pay = None
            if not a.no_payouts:
                for ct, bar, slot in (("DIGITMATCH", 0, "m"), ("DIGITDIFF", 0, "d")):
                    r = ws.proposal(amount=10, basis="stake", contract_type=ct,
                                    currency="USD", duration=1, duration_unit="t",
                                    underlying_symbol=sym, barrier=str(bar))
                    if "proposal" in r:
                        v = float(r["proposal"]["payout"]) / 10
                        if slot == "m": m_pay = v
                        else: d_pay = v
                    time.sleep(0.10)

            rows.append(dict(sym=sym, spot=spot, pip=pip, iv=interval,
                             sigma=sigma, chi2=chi2, m=m_pay, d=d_pay))
        except Exception as e:
            print(f"{sym:10} error: {e}")
        time.sleep(0.20)

    # ── report ────────────────────────────────────────────────────────────
    print()
    print(f"{'symbol':10}{'spot':>11}{'pip':>5}{'iv':>4}{'sigma':>8}"
          f"{'chi2':>9}{'MATCH':>8}{'DIFF':>8}  verdict")
    print("-" * 78)

    in_zone = []
    for r in sorted(rows, key=lambda x: (x["sigma"] is None, x["sigma"] or 1e9)):
        s = r["sigma"]
        sig_s = f"{s:.2f}" if s is not None else "--"
        chi_s = f"{r['chi2']:.1f}" if r["chi2"] is not None else "--"
        m_s = f"{r['m']:.3f}" if r["m"] else "--"
        d_s = f"{r['d']:.4f}" if r["d"] else "--"

        repriced = r["m"] is not None and r["m"] < 8.5
        if s is None:
            v = "no data"
        elif repriced:
            v = f"RE-PRICED ({r['m']:.3f}) - dead"
        elif s <= a.sigma_max:
            v = "*** IN ZONE ***"
            in_zone.append(r)
        elif s <= a.sigma_max + 0.6:
            v = "marginal - watch"
        else:
            v = "flat - no edge"

        print(f"{r['sym']:10}{r['spot']:>11.4f}{r['pip']:>5}{r['iv'] or 0:>4}"
              f"{sig_s:>8}{chi_s:>9}{m_s:>8}{d_s:>8}  {v}")

    print()
    print(f"chi2 crit (df=9, p=.05) = 16.92 — above that the digit pmf is measurably non-uniform")
    if in_zone:
        print(f"\nIN ZONE ({len(in_zone)}): " + ", ".join(r["sym"] for r in in_zone))
        print("Next: rebuild empirical tables on that symbol, then paper-trade it:")
        for r in in_zone:
            print(f"  python3 sentinel_v2.py --watch {r['sym']} --minutes 30")
    else:
        print("\nNothing in zone right now. Re-run periodically — spot decays over days.")
    ws.close()


if __name__ == "__main__":
    main()
