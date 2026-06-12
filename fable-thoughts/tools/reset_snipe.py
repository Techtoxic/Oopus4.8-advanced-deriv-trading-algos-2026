"""reset_snipe.py — Daily Reset Index midnight test (RUN AT ~23:58 GMT).

VERIFIED FACT (2026-06-12, raw tick feed): RDBULL/RDBEAR tick continuously through midnight
and the 00:00:00 tick is EXACTLY 1000 (e.g. 23:59:58 = 1205.8112 -> 00:00:00 = 1000.0000).
The reset tick's price and last digit (0) are known in advance.

IF Deriv accepts 1-tick contracts whose exit tick is the reset tick (buy at ~23:59:56-58),
these are deterministic wins:
  - price > 1000 at 23:59:5x  ->  PUT  (exit 1000 < entry)            ~1.92x  guaranteed
  - price < 1000 at 23:59:5x  ->  CALL                                ~1.92x  guaranteed
  - always                    ->  DIGITMATCH barrier 0 (digit of 1000.0000 = 0)  ~8.93x
Most likely Deriv rejects purchases near settlement (23:59:59) or excludes the reset tick.
This script finds out and logs everything. Expected outcomes:
  A) buys rejected (error logged)            -> hypothesis dead, documented
  B) buys accepted, settle on reset tick     -> JACKPOT, check contracts won
  C) buys accepted, settle on 00:00:02 tick  -> partial (digit 0 edge dies, PUT edge survives
     only if 00:00:02 still below entry, which it is when |entry-1000| >> 2s of vol)

Run (demo): python3 reset_snipe.py --stake 1
It waits until 23:59:30 GMT, then arms; fires at 23:59:54, 56, 58 on both RDBULL and RDBEAR.
"""
import argparse, json, time
import websocket
from deriv_api import DerivWS, WS_PUBLIC

def log(f, s):
    line = f"{time.strftime('%H:%M:%S', time.gmtime())} {s}"
    print(line); f.write(line + "\n"); f.flush()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--symbols", nargs="+", default=["RDBULL", "RDBEAR"])
    a = ap.parse_args()
    tr = DerivWS()
    assert tr.account.get("account_type") == "demo", "demo only"
    f = open("../results/reset_snipe.log", "a")
    log(f, f"armed on {tr.account['account_id']} bal={tr.account.get('balance')}")

    pub = websocket.create_connection(WS_PUBLIC, timeout=30)
    for s in a.symbols:
        pub.send(json.dumps({"ticks": s, "subscribe": 1}))
    last = {}
    fired = set()
    bought = []
    while True:
        msg = json.loads(pub.recv())
        if msg.get("msg_type") != "tick": continue
        tk = msg["tick"]; sym = tk["symbol"]; ep = tk["epoch"]; q = float(tk["quote"])
        last[sym] = q
        sod = ep % 86400
        if sod < 86374 or sod > 86399:
            if sod % 600 == 0: log(f, f"waiting... {sym}={q}")
            continue
        # 23:59:34 .. 23:59:59 window: fire once per (sym, second bucket)
        for fire_at in (86394, 86396, 86398):   # 23:59:54 / 56 / 58
            keyb = (sym, fire_at)
            if sod >= fire_at and keyb not in fired:
                fired.add(keyb)
                legs = [("DIGITMATCH", "0"), ("PUT" if q > 1000 else "CALL", None)]
                for ct, bar in legs:
                    params = dict(amount=a.stake, basis="stake", contract_type=ct, currency="USD",
                                  duration=1, duration_unit="t", underlying_symbol=sym)
                    if bar is not None: params["barrier"] = bar
                    br = tr.call({"buy": 1, "price": round(a.stake * 10, 2), "parameters": params})
                    if "buy" in br:
                        cid = br["buy"]["contract_id"]
                        bought.append((sym, ct, cid))
                        log(f, f"BUY OK {sym} {ct} sod={sod} q={q} cid={cid}")
                    else:
                        log(f, f"BUY REJECTED {sym} {ct} sod={sod} q={q} err={br.get('error',{}).get('message')}")
        if sod >= 86398 and all((s2, fa) in fired for s2 in a.symbols for fa in (86394, 86396, 86398)):
            break
    log(f, "midnight passed; settling...")
    time.sleep(8)
    total = 0.0
    for sym, ct, cid in bought:
        for _ in range(12):
            pc = tr.open_contract(cid)
            c = pc.get("proposal_open_contract", {})
            if c.get("is_sold") or c.get("status") in ("won", "lost"):
                pnl = float(c.get("profit", 0)); total += pnl
                log(f, f"SETTLED {sym} {ct} {c.get('status')} entry={c.get('entry_spot')}@{c.get('entry_spot_time')} "
                       f"exit={c.get('exit_spot')}@{c.get('exit_spot_time')} pnl={pnl:+.2f}")
                break
            time.sleep(1)
    log(f, f"TOTAL {total:+.2f}")
    f.close()

if __name__ == "__main__":
    main()
