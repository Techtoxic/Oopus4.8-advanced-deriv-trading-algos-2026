"""
Live demo trade harness for digit contracts.
- Buys 1-tick DIGIT contracts on the demo account.
- Records, per trade: request-time spot, contract entry tick+time, exit tick+time,
  the digit that determined payout, win/loss, payout, and the realized P/L.
- Reconstructs the N / N+1 / N+2 tick relationship from the contract's own
  entry_tick / exit_tick timing (ground truth from Deriv, not assumption).
Saves a CSV trade log.
"""
import os, sys, json, time, csv
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.deriv_client import DerivClient


def run_batch(symbol, contract_type, barrier, n_trades, stake, decimals, out_csv,
              p_win_theory):
    client = DerivClient(); client.connect()
    rows = []
    wins = 0
    start_bal = client.balance()["balance"]
    for i in range(n_trades):
        req_spot = None; req_time = None

        params = dict(amount=stake, basis="stake", contract_type=contract_type,
                      currency="USD", duration=1, duration_unit="t", symbol=symbol)
        if barrier is not None:
            params["barrier"] = str(barrier)
        # buy with simple rate-limit backoff
        buy = None
        for attempt in range(6):
            buy = client.buy_contract(params, price=stake)
            if "error" in buy and "rate limit" in buy["error"]["message"].lower():
                time.sleep(2 + attempt); continue
            break
        if "error" in buy:
            rows.append({"i": i, "error": buy["error"]["message"]})
            time.sleep(1); continue
        cid = buy["buy"]["contract_id"]
        try:
            poc = client.proposal_open_contract(cid, timeout=60)
        except RuntimeError as e:
            if "rate limit" in str(e).lower():
                time.sleep(3)
                try:
                    poc = client.proposal_open_contract(cid, timeout=60)
                except Exception:
                    poc = None
            else:
                poc = None
        if not poc:
            rows.append({"i": i, "error": "no_poc"}); continue
        time.sleep(0.2)  # gentle pacing to respect rate limits

        entry = poc.get("entry_tick"); exit_ = poc.get("exit_tick")
        entry_t = poc.get("entry_tick_time"); exit_t = poc.get("exit_tick_time")
        status = poc.get("status")
        profit = poc.get("profit")
        payout = poc.get("payout")
        is_win = status == "won"
        wins += int(is_win)
        # the digit that determined payout = last digit of exit tick
        exit_digit = int(f"{exit_:.{decimals}f}"[-1]) if exit_ is not None else None
        entry_digit = int(f"{entry:.{decimals}f}"[-1]) if entry is not None else None
        rows.append({
            "i": i, "symbol": symbol, "contract": contract_type, "barrier": barrier,
            "stake": stake,
            "req_time": req_time, "req_spot": req_spot,
            "entry_time": entry_t, "entry_spot": entry, "entry_digit": entry_digit,
            "exit_time": exit_t, "exit_spot": exit_, "exit_digit": exit_digit,
            "ticks_entry_to_exit": (None if (entry_t is None or exit_t is None)
                                    else exit_t - entry_t),
            "status": status, "is_win": int(is_win),
            "payout": payout, "profit": profit,
        })
    end_bal = client.balance()["balance"]
    client.close()

    valid = [r for r in rows if "status" in r]
    nw = sum(r["is_win"] for r in valid)
    nv = len(valid)
    realized = nw / nv if nv else 0
    net = sum(r["profit"] for r in valid)
    # write csv
    keys = ["i","symbol","contract","barrier","stake","req_time","req_spot",
            "entry_time","entry_spot","entry_digit","exit_time","exit_spot",
            "exit_digit","ticks_entry_to_exit","status","is_win","payout","profit"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, ext="ignore") if False else csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in valid: w.writerow(r)

    summary = dict(symbol=symbol, contract=contract_type, barrier=barrier,
                   n=nv, wins=nw, realized_winrate=round(realized,4),
                   theory_winrate=p_win_theory,
                   net_profit=round(net,2),
                   start_bal=start_bal, end_bal=end_bal,
                   errors=len(rows)-nv)
    return summary, valid


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="1HZ100V")
    ap.add_argument("--contract", default="DIGITEVEN")
    ap.add_argument("--barrier", default=None)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--stake", type=float, default=0.5)
    ap.add_argument("--decimals", type=int, default=2)
    ap.add_argument("--pwin", type=float, default=0.5)
    ap.add_argument("--out", default="/home/research/digits/data/live_trades.csv")
    a = ap.parse_args()
    bar = a.barrier if a.barrier is None else (int(a.barrier))
    s, rows = run_batch(a.symbol, a.contract, bar, a.n, a.stake, a.decimals, a.out, a.pwin)
    print(json.dumps(s, indent=2))
