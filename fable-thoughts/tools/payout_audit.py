"""payout_audit.py — proposal payout vs EXECUTED payout, across the whole digit grid.

WHY
regime_forecast.py reports JD100 DIGITMATCH at 8.929 from the PROPOSAL endpoint — i.e.
unchanged. But the dtrader UI showed $10 stake -> $66.67 payout (6.667x), and the Over/Under
grid read off the app is clearly cut (Over 4: $2 -> $3.64 = 1.820x vs the old 1.953).

Those cannot both be true, and this repo has already caught the proposal endpoint lying:
FINDINGS.md sec 3 records CALLE/PUTE quoting 1.9530 and filling 1.800.

This matters a lot right now: JD100 is at sigma 3.90, INSIDE the edge zone and deeper than
anything in the walk-forward (best band was 4.15-4.30). If the grid is intact the original
edge is live in better conditions than when it worked. If it is cut, it is dead. Nothing in
between.

METHOD
The BUY response carries the contracted `payout` — that is the authoritative executed price,
available immediately without waiting for settlement. So: for each contract, pull the
proposal payout, then actually buy at minimum stake and read the payout back. Any gap is the
endpoint lying.

Contracts audited: DIGITMATCH/DIGITDIFF on barrier 0, DIGITOVER 0-8, DIGITUNDER 1-9,
DIGITEVEN/DIGITODD. That is the full surface the sigma strategy trades from.

DEMO ONLY. Stake defaults to $0.35. Total exposure ~$8 worst case.

Run: python3 payout_audit.py
     python3 payout_audit.py --symbol JD100 --stake 0.35
     python3 payout_audit.py --no-buy          # proposals only, spends nothing
"""
import argparse, time
from deriv_api import DerivWS

# (contract_type, barrier, old_payout) — old grid from sigma_model.GRID
OLD = {("DIGITMATCH", "0"): 8.929, ("DIGITDIFF", "0"): 1.0958,
       ("DIGITEVEN", None): 1.953, ("DIGITODD", None): 1.953}
_OU = {0: 1.096, 1: 1.232, 2: 1.404, 3: 1.634, 4: 1.953,
       5: 2.427, 6: 3.205, 7: 4.717, 8: 8.929}
for _k in range(9):
    OLD[("DIGITOVER", str(_k))] = _OU[_k]
for _k in range(1, 10):
    OLD[("DIGITUNDER", str(_k))] = _OU[9 - _k]


def params(ct, bar, sym, stake):
    p = dict(amount=stake, basis="stake", contract_type=ct, currency="USD",
             duration=1, duration_unit="t", underlying_symbol=sym)
    if bar is not None:
        p["barrier"] = str(bar)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--no-buy", action="store_true")
    ap.add_argument("--out", default="../results/payout_audit.md")
    a = ap.parse_args()

    order = ([("DIGITMATCH", "0"), ("DIGITDIFF", "0")]
             + [("DIGITOVER", str(k)) for k in range(9)]
             + [("DIGITUNDER", str(k)) for k in range(1, 10)]
             + [("DIGITEVEN", None), ("DIGITODD", None)])

    ws = DerivWS(token="")
    print(f"symbol {a.symbol}  stake ${a.stake}\n")
    print(f"{'contract':16}{'old':>8}{'proposal':>10}{'executed':>10}"
          f"{'exec/old':>10}  verdict")
    print("-" * 68)

    tr = None
    if not a.no_buy:
        tr = DerivWS()
        acct = tr.account or {}
        if acct.get("account_type") != "demo":
            print(f"NOT demo ({acct.get('account_type')}) — proposals only.")
            tr = None
        else:
            print(f"[demo {acct.get('account_id')} bal={acct.get('balance')}]\n")

    rows = []
    for ct, bar in order:
        pr = ws.call({"proposal": 1, **params(ct, bar, a.symbol, a.stake)})
        prop = (float(pr["proposal"]["payout"]) / a.stake) if "proposal" in pr else None

        ex = None
        if tr is not None:
            b = tr.call({"buy": 1, "price": round(a.stake * 20, 2),
                         "parameters": params(ct, bar, a.symbol, a.stake)})
            if "buy" in b:
                pay = b["buy"].get("payout")
                bp = float(b["buy"].get("buy_price") or a.stake)
                if pay:
                    ex = float(pay) / bp
            else:
                ex = b.get("error", {}).get("message", "")[:24]
            time.sleep(0.35)

        old = OLD.get((ct, bar))
        label = ct + (str(bar) if bar is not None else "")
        ratio = (ex / old) if (isinstance(ex, float) and old) else None

        if isinstance(ex, float) and prop:
            if abs(ex - prop) / prop > 0.005:
                v = f"PROPOSAL LIES ({prop:.3f} vs {ex:.3f})"
            elif ratio and ratio < 0.99:
                v = f"CUT {(1-ratio)*100:.1f}%"
            else:
                v = "unchanged"
        elif isinstance(ex, str):
            v = f"buy err: {ex}"
        elif prop and old:
            v = ("cut per proposal" if prop < old * 0.99 else "unchanged per proposal")
        else:
            v = "no data"

        rows.append((label, old, prop, ex, ratio, v))
        os_, ps, es = (f"{old:.3f}" if old else "--",
                       f"{prop:.3f}" if prop else "--",
                       f"{ex:.3f}" if isinstance(ex, float) else (ex or "--"))
        rs = f"{ratio:.4f}" if ratio else "--"
        print(f"{label:16}{os_:>8}{ps:>10}{es:>10}{rs:>10}  {v}")
        time.sleep(0.12)

    md = [f"# Payout audit — {a.symbol}\n\n",
          "Proposal payout vs EXECUTED payout (from the buy response, which carries the ",
          "contracted payout). The proposal endpoint is known to misreport: FINDINGS.md ",
          "sec 3 records CALLE/PUTE quoting 1.9530 and filling 1.800.\n\n",
          "| contract | old | proposal | executed | exec/old | verdict |\n",
          "|---|---:|---:|---:|---:|---|\n"]
    for lbl, old, prop, ex, ratio, v in rows:
        md.append(f"| {lbl} | {old or ''} | {prop or ''} | "
                  f"{ex if isinstance(ex, float) else ''} | "
                  f"{f'{ratio:.4f}' if ratio else ''} | {v} |\n")

    # the decision: the sigma strategy's restricted ladder
    print()
    print("=" * 68)
    print("RESTRICTED LADDER — what adaptive_sentinel actually trades")
    print("=" * 68)
    ladder = ["DIGITOVER3", "DIGITOVER4", "DIGITUNDER5", "DIGITUNDER6"]
    live = {r[0]: r for r in rows}
    alive = False
    for name in ladder:
        r = live.get(name)
        if not r or not isinstance(r[3], float):
            print(f"  {name:14} no executed payout")
            continue
        M = r[3]
        be = 1 / M
        print(f"  {name:14} executed {M:.4f}  breakeven WR {be*100:.2f}%")
        for wr in (0.5180, 0.5278):
            ev = (wr * M - 1) * 100
            print(f"       at WR {wr*100:.2f}%: EV {ev:+.2f}%")
            if ev > 0:
                alive = True

    md.append("\n## Restricted ladder\n\n")
    print()
    if alive:
        print("  POSITIVE EV at a previously measured win rate.")
        print("  JD100 is at sigma 3.90 — deeper than the walk-forward band (4.15-4.30).")
        print("  NEXT: rebuild empirical tables at this sigma, then paper-trade.")
        print("  Do NOT reuse the old tables: they were fitted at sigma 4.15-4.35.")
        md.append("Positive EV at a previously measured win rate. Rebuild empirical tables "
                  "at the current sigma before trading — the existing tables were fitted at "
                  "sigma 4.15-4.35 and JD100 is now at 3.90.\n")
    else:
        print("  Negative EV at both previously measured win rates. Grid is cut too far.")
        md.append("Negative EV at both previously measured win rates.\n")

    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
