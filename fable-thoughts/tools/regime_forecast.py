"""regime_forecast.py — WHEN does each symbol enter the JD100 zone?

The one edge that ever worked in this repo came from a single mechanism:

    pip size is frozen at product launch; spot decays from volatility drag;
    so sigma MEASURED IN PIPS shrinks over months; the digit pmf goes non-uniform
    while the payout grid stays static.

opus's universe_screen.md is a SNAPSHOT (June 2026): JD100 sigma 4.8, R_100 8.9, everything
else far out. A snapshot cannot tell you which symbol arrives next, or when. But the mechanism
is a function of time, so it is forecastable.

This script:
  1. Enumerates ALL active symbols from the API — so newly launched products are caught.
     New launches matter most: parameters are set at launch and Deriv has the least data
     to recalibrate against.
  2. Diffs against the June-2026 baseline (72 symbols) and flags anything NEW.
  3. For each digit-enabled symbol, fetches DAILY candles (up to ~2 years) and fits
     log-linear decay:  log(spot) ~ a + b*t.  b is the drag rate.
  4. Measures current sigma_pips on the same W=1800 jump-filtered estimator sentinel_v2 uses.
  5. Projects forward: sigma_pips scales with spot, so
         days_to_zone = ln(sigma_target / sigma_now) / b        (b < 0)
     and reports a calendar date.
  6. Pulls live executed-payout quotes so a symbol already repriced is flagged dead on arrival.

Output is a WATCHLIST WITH DATES, not a pass/fail. Symbols arriving within ~90 days are worth
monitoring; anything already inside the zone with an un-repriced payout grid is actionable now.

Read-only. Places no trades.

Run: python3 regime_forecast.py
     python3 regime_forecast.py --sigma-target 4.35 --horizon 365
"""
import argparse, json, math, time, datetime as dt
import numpy as np
from deriv_api import DerivWS

W = 1800
JUMP_THR = 20

# opus universe_screen.md, June 2026 — used only to flag NEW listings
JUNE_BASELINE = {
    "BOOM1000","BOOM150N","BOOM300N","BOOM50","BOOM500","BOOM600","BOOM900",
    "CRASH1000","CRASH150N","CRASH300N","CRASH50","CRASH500","CRASH600","CRASH900",
    "JD10","JD100","JD150","JD200","JD25","JD50","JD75",
    "R_10","R_25","R_50","R_75","R_100",
    "1HZ10V","1HZ15V","1HZ25V","1HZ30V","1HZ50V","1HZ75V","1HZ90V","1HZ100V",
    "RDBEAR","RDBULL","stpRNG","stpRNG2","stpRNG3","stpRNG4","stpRNG5",
}


def sigma_pips(prices, pip):
    vals = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    st = np.diff(vals[-(W + 1):])
    nj = st[np.abs(st) <= JUMP_THR]
    if len(nj) < 200:
        return None
    return float(np.sqrt((nj.astype(float) ** 2).mean()))


def daily_decay(ws, sym, days=730):
    """Fit log(close) ~ a + b*t on daily candles. Returns (b_per_day, n, spot_first, spot_last)."""
    r = ws.call({"ticks_history": sym, "style": "candles", "granularity": 86400,
                 "count": days, "end": "latest"})
    c = r.get("candles")
    if not c or len(c) < 30:
        return None
    close = np.array([float(x["close"]) for x in c], dtype=float)
    close = close[close > 0]
    if len(close) < 30:
        return None
    t = np.arange(len(close), dtype=float)
    b, a = np.polyfit(t, np.log(close), 1)
    return float(b), len(close), float(close[0]), float(close[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigma-target", type=float, default=4.35,
                    help="sigma_pips at which the digit edge opened on JD100")
    ap.add_argument("--horizon", type=int, default=730, help="report arrivals within N days")
    ap.add_argument("--ticks", type=int, default=W + 200)
    ap.add_argument("--synthetics-only", action="store_true", default=True)
    ap.add_argument("--all-markets", dest="synthetics_only", action="store_false")
    ap.add_argument("--out", default="../results/regime_forecast.md")
    a = ap.parse_args()

    ws = DerivWS(token="")

    syms = []
    for req in ({"active_symbols": "brief"},
                {"active_symbols": "full"},
                {"active_symbols": "brief", "landing_company": "svg"}):
        r = ws.call(req)
        syms = r.get("active_symbols", [])
        if syms:
            break
        print(f"  active_symbols {req} -> {r.get('error', {}).get('message')}")
    if not syms:
        print("could not enumerate active_symbols with any variant"); return
    if not isinstance(syms[0], dict):
        print("unexpected active_symbols shape:", type(syms[0]), str(syms[0])[:200]); return

    print("\n  --- active_symbols entry keys ---")
    print("   ", sorted(syms[0].keys()))
    print("  --- sample entry ---")
    print("   ", json.dumps(syms[0])[:300])
    print()

    SYM_KEYS = ("symbol", "underlying_symbol", "symbol_code", "value", "name")
    sym_key = next((k for k in SYM_KEYS if k in syms[0]), None)
    if sym_key is None:
        print(f"could not find a symbol field among {SYM_KEYS}"); return
    NAME_KEYS = ("display_name", "symbol_display_name", "name", "longcode")
    name_key = next((k for k in NAME_KEYS if k in syms[0]), None)

    names, meta = {}, {}
    for s in syms:
        if not isinstance(s, dict) or sym_key not in s:
            continue
        if s.get("exchange_is_open", 1) in (0, False):
            continue
        if s.get("is_trading_suspended", 0) in (1, True):
            continue
        mkt = s.get("market", "")
        if a.synthetics_only and mkt and mkt != "synthetic_index":
            continue
        names[s[sym_key]] = s.get(name_key, "") if name_key else ""
        meta[s[sym_key]] = {"market": mkt, "submarket": s.get("submarket", ""),
                            "pip_size": s.get("pip_size"),
                            "trade_count": s.get("trade_count")}
    print(f"{len(names)} active symbols")

    new = sorted(s for s in set(names) - JUNE_BASELINE
                 if meta.get(s, {}).get("market") == "synthetic_index")
    gone = sorted(JUNE_BASELINE - set(names))
    print("  (baseline covers synthetics only; forex/OTC diffs suppressed)")
    if new:
        print(f"\nNEW since June baseline ({len(new)}):")
        for s in new:
            print(f"  {s:14} {names[s]}")
    if gone:
        print(f"\nGONE since June ({len(gone)}): {', '.join(gone)}")

    # which support digit contracts
    print("\nchecking digit availability...")

    def contracts_of(sym, dump=False):
        """API renamed symbol->underlying_symbol; try both request shapes."""
        for req in ({"contracts_for": sym, "currency": "USD"},
                    {"contracts_for": 1, "underlying_symbol": sym, "currency": "USD"},
                    {"contracts_for": sym}):
            r = ws.call(req)
            body = r.get("contracts_for") or r.get("contracts_for_company") or {}
            avail = body.get("available", []) if isinstance(body, dict) else []
            if avail:
                if dump:
                    print(f"  request that worked: {list(req)}")
                    print(f"  available[0] keys: {sorted(avail[0].keys())}")
                    cats = sorted({c.get("contract_category") or
                                   c.get("contract_category_display") or "?"
                                   for c in avail})
                    print(f"  categories seen: {cats}\n")
                return avail
            if not isinstance(body, dict) and dump:
                print(f"  {list(req)} -> {r.get('error', {}).get('message')}")
        return []

    digit_syms, first = [], True
    for s in sorted(names):
        avail = contracts_of(s, dump=first)
        if avail:
            first = False
        cats = {(c.get("contract_category") or "").lower() for c in avail}
        cats |= {(c.get("contract_type") or "").upper() for c in avail}
        if "digits" in cats or any(t.startswith("DIGIT") for t in cats):
            digit_syms.append(s)
        time.sleep(0.06)
    print(f"digit-enabled: {len(digit_syms)}  {digit_syms if digit_syms else ''}")

    rows = []
    print(f"\n{'symbol':11}{'spot':>12}{'pip':>4}{'sigma':>8}{'b/day':>10}"
          f"{'days→zone':>11}{'date':>12}{'MATCH':>8}  status")
    print("-" * 88)

    for s in digit_syms:
        try:
            _, prices, pip = ws.history_paged(s, a.ticks, sleep=0.15)
        except Exception:
            continue
        pip = int(pip)
        sg = sigma_pips(prices, pip)
        if sg is None:
            continue
        spot = float(prices[-1])

        dec = daily_decay(ws, s)
        b = dec[0] if dec else None

        # live payout — detects an already-repriced book
        m_pay = None
        pr = ws.proposal(amount=10, basis="stake", contract_type="DIGITMATCH",
                         currency="USD", duration=1, duration_unit="t",
                         underlying_symbol=s, barrier="0")
        if "proposal" in pr:
            m_pay = float(pr["proposal"]["payout"]) / 10
        time.sleep(0.12)

        days = None
        date_s = "--"
        if sg <= a.sigma_target:
            days = 0
            date_s = "NOW"
        elif b is not None and b < -1e-9:
            days = math.log(a.sigma_target / sg) / b
            if 0 < days < 3650:
                date_s = (dt.date.today() + dt.timedelta(days=int(days))).isoformat()
            else:
                days = None

        repriced = m_pay is not None and m_pay < 8.5
        if repriced:
            status = f"REPRICED {m_pay:.3f} — dead"
        elif days == 0:
            status = "*** IN ZONE NOW ***"
        elif days is not None and days <= 90:
            status = "arriving soon — WATCH"
        elif days is not None and days <= a.horizon:
            status = "on track"
        elif b is None or b >= 0:
            status = "no decay — never"
        else:
            status = "beyond horizon"

        rows.append(dict(sym=s, spot=spot, pip=pip, sigma=sg, b=b, days=days,
                         date=date_s, m=m_pay, status=status,
                         new=(s not in JUNE_BASELINE)))
        bs = f"{b:.2e}" if b is not None else "--"
        ds = f"{days:.0f}" if days is not None else "--"
        ms = f"{m_pay:.3f}" if m_pay else "--"
        tag = "*" if s not in JUNE_BASELINE else " "
        print(f"{s+tag:11}{spot:>12.4f}{pip:>4}{sg:>8.2f}{bs:>10}"
              f"{ds:>11}{date_s:>12}{ms:>8}  {status}")

    md = ["# Regime forecast — when does each symbol enter the JD100 zone?\n\n",
          f"Target sigma_pips <= {a.sigma_target}. `b/day` is the fitted log-linear drag ",
          "from daily candles. `*` marks symbols new since the June 2026 baseline.\n\n",
          "| symbol | new | spot | pip | sigma | b/day | days | date | MATCH | status |\n",
          "|---|:--:|---:|---:|---:|---:|---:|---|---:|---|\n"]
    for r_ in sorted(rows, key=lambda x: (x["days"] is None, x["days"] or 1e9)):
        md.append(f"| {r_['sym']} | {'*' if r_['new'] else ''} | {r_['spot']:.4f} | "
                  f"{r_['pip']} | {r_['sigma']:.2f} | "
                  f"{r_['b']:.2e} | {r_['days'] if r_['days'] is not None else ''} | "
                  f"{r_['date']} | {r_['m'] or ''} | {r_['status']} |\n")

    live = [r_ for r_ in rows if r_["status"].startswith("***")]
    soon = [r_ for r_ in rows if "WATCH" in r_["status"]]

    print()
    if live:
        print("IN ZONE NOW:")
        for r_ in live:
            print(f"  {r_['sym']} sigma {r_['sigma']:.2f}, MATCH {r_['m']}")
        print("  -> rebuild empirical tables on THIS symbol (do not reuse JD100's),")
        print("     then paper-trade. JD100 tables are keyed to its own step distribution.")
        md.append("\n## In zone now\n\n" + "".join(
            f"- `{r_['sym']}` sigma {r_['sigma']:.2f}, MATCH {r_['m']}\n" for r_ in live))
    if soon:
        print("ARRIVING WITHIN 90 DAYS:")
        for r_ in sorted(soon, key=lambda x: x["days"]):
            print(f"  {r_['sym']:12} ~{r_['days']:.0f}d  ({r_['date']})")
        md.append("\n## Arriving within 90 days\n\n" + "".join(
            f"- `{r_['sym']}` ~{r_['days']:.0f} days ({r_['date']})\n"
            for r_ in sorted(soon, key=lambda x: x["days"])))
    if not live and not soon:
        print("Nothing in zone or arriving within 90 days.")
        print("Re-run monthly — the decay does the work, and new listings are the")
        print("highest-value case since their parameters are freshest.")

    md.append("\n## Method\n\nsigma_pips scales with spot, so from a fitted drag rate b:\n\n"
              "    days_to_zone = ln(sigma_target / sigma_now) / b\n\n"
              "Caveats: b is fitted on daily closes and assumes the drag continues; these "
              "indices have no price anchor so spot can drift up as readily as down (this "
              "caused JD100's day-4 drawdown). A symbol already showing MATCH < 8.5 has had "
              "its book rebuilt and is dead regardless of sigma.\n")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
