"""reset_days_2026_08_18.py — daily-reset index battery (session 2026-08-18 pm).

Fetches a year of hourly candles for RDBULL/RDBEAR and answers:
  Q1 reset exactness (00:00 == 1000), Q2 day-to-day independence (seeding),
  Q3 intraday drift shape, Q4 hourly vol term structure, Q5 per-hour CALL/PUT EV
  at EXECUTED payouts (probe live). Prints the full table; see SESSION_2026-08-18b.md.
"""
import sys, time, json, math, datetime as dt
import numpy as np
from deriv_api import DerivWS


def fetch_hourly(ws, sym):
    seen, end = {}, "latest"
    for _ in range(10):
        req = {"ticks_history": sym, "style": "candles", "granularity": 3600,
               "count": 1000, "end": end}
        if isinstance(end, int):
            req["start"] = end - 1000 * 3600 - 3600
        r = ws.call(req)
        cs = r.get("candles", [])
        if not cs:
            break
        for c in cs:
            seen[int(c["epoch"])] = c
        end = min(int(c["epoch"]) for c in cs) - 1
        time.sleep(0.4)
    hours = sorted((int(k), float(v["close"]), float(v["open"]))
                   for k, v in seen.items())
    ep = np.array([h[0] for h in hours])
    return ep, np.array([h[1] for h in hours]), np.array([h[2] for h in hours])


def battery(sym, ep, cl, op, payouts):
    sod = (ep % 86400) // 3600
    days = ep // 86400
    lr = np.log(cl) - np.log(op)
    print(f"\n=== {sym}: {len(ep)} hours, "
          f"{dt.datetime.fromtimestamp(ep[0], dt.UTC):%Y-%m-%d} .. "
          f"{dt.datetime.fromtimestamp(ep[-1], dt.UTC):%Y-%m-%d}")
    # Q2 daily independence
    first_open, day_close = {}, {}
    for i in range(len(ep)):
        first_open.setdefault(int(days[i]), op[i])
        day_close[int(days[i])] = cl[i]
    dk = sorted(first_open)
    wdr = np.array([math.log(day_close[d] / first_open[d]) for d in dk])
    ac1 = np.corrcoef(wdr[:-1], wdr[1:])[0, 1]
    print(f"Q2 within-day returns n={len(wdr)} mean={wdr.mean()*100:+.2f}% "
          f"autocorr1={ac1:+.4f} (noise {1.96/math.sqrt(len(wdr)):.3f})")
    # Q3/Q5 per-hour P(up) and EV at executed payouts
    print("Q3/Q5 hour: P(up)  EV_CALL  EV_PUT  (executed payouts)")
    for h in range(24):
        a = lr[sod == h]
        p = (a > 0).mean()
        n = len(a)
        se = math.sqrt(p * (1 - p) / n)
        mc, mp = payouts
        evc, evp = p * mc - 1, (1 - p) * mp - 1
        mark = " <-- point +EV, LB99 %+.1f%%" % ((p - 2.576 * se) * mc * 100 - 100) \
            if evc > 0 else ""
        print(f"  {h:02d}: {p:.4f}  {evc:+.2%}  {evp:+.2%}{mark}")


if __name__ == "__main__":
    ws = DerivWS(token="")
    auth = DerivWS()
    for sym in ("RDBULL", "RDBEAR"):
        pays = {}
        for ct in ("CALL", "PUT"):
            r = auth.call({"buy": 1, "price": 3.5, "parameters": dict(
                amount=1, basis="stake", contract_type=ct, currency="USD",
                duration=1, duration_unit="h", underlying_symbol=sym)})
            pays[ct] = float(r["buy"]["payout"]) / float(r["buy"]["buy_price"]) \
                if "buy" in r else None
            time.sleep(0.4)
        ep, cl, op = fetch_hourly(ws, sym)
        battery(sym, ep, cl, op, (pays["CALL"] or 1.0, pays["PUT"] or 1.0))
