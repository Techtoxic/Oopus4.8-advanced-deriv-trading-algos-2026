"""
Robust + fast live demo trade harness for digit contracts.
Key design choices (after a hang/crash with is_sold polling):
- Settle DETERMINISTICALLY from the exit tick digit (the payout-determining tick),
  which is fully known the instant the exit tick prints -> no waiting on is_sold,
  no hang. Win/loss for digit contracts is a deterministic function of exit_digit.
- Stream proposal_open_contract via subscribe; stop at the first message carrying
  exit_tick; forget the subscription.
- Catch socket timeouts and reconnect transparently.
- Write each trade to CSV immediately (crash-safe), and cross-check the running
  net against the account balance delta at the end.
"""
import os, sys, json, time, csv
import websocket
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.deriv_client import DerivClient


def is_win(ct, barrier, d):
    if ct == "DIGITEVEN":  return d % 2 == 0
    if ct == "DIGITODD":   return d % 2 == 1
    if ct == "DIGITOVER":  return d > barrier
    if ct == "DIGITUNDER": return d < barrier
    if ct == "DIGITMATCH": return d == barrier
    if ct == "DIGITDIFF":  return d != barrier
    raise ValueError(ct)


def wait_exit(client, cid, timeout=30):
    """Subscribe to poc; return first snapshot that has exit_tick (settlement)."""
    rid = next(client._ids)
    client.ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid,
                               "subscribe": 1, "req_id": rid}))
    deadline = time.time() + timeout
    sub_id = None
    while time.time() < deadline:
        raw = client.ws.recv()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("req_id") != rid:
            continue
        poc = msg.get("proposal_open_contract") or {}
        sub_id = msg.get("subscription", {}).get("id", sub_id)
        if poc.get("exit_tick") is not None:
            if sub_id:
                try: client.ws.send(json.dumps({"forget": sub_id}))
                except Exception: pass
            return poc
    return None


def run(symbol, ct, barrier, n, stake, decimals, payout_mult, out_csv):
    client = DerivClient(); start_bal = client.connect()["balance"]
    keys = ["i","symbol","contract","barrier","entry_time","entry_spot","entry_digit",
            "exit_time","exit_spot","exit_digit","is_win","profit"]
    f = open(out_csv, "w", newline=""); w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
    wins = nv = 0; net = 0.0
    i = 0
    while i < n:
        params = dict(amount=stake, basis="stake", contract_type=ct, currency="USD",
                      duration=1, duration_unit="t", symbol=symbol)
        if barrier is not None: params["barrier"] = str(barrier)
        try:
            buy = client.buy_contract(params, price=stake)
        except (websocket.WebSocketTimeoutException, websocket.WebSocketConnectionClosedException, OSError):
            client.reconnect(); continue
        if "error" in buy:
            m = buy["error"]["message"].lower()
            if "rate limit" in m: time.sleep(2); continue
            time.sleep(0.5); continue
        cid = buy["buy"]["contract_id"]
        try:
            poc = wait_exit(client, cid, timeout=30)
        except (websocket.WebSocketTimeoutException, websocket.WebSocketConnectionClosedException, OSError):
            client.reconnect(); continue
        if not poc or poc.get("exit_tick") is None:
            continue
        ed = int(f"{poc['exit_tick']:.{decimals}f}"[-1])
        win = is_win(ct, barrier if barrier is not None else 0, ed)
        profit = round((payout_mult - 1) * stake, 4) if win else -stake
        net += profit; wins += int(win); nv += 1
        endig = int(f"{poc['entry_tick']:.{decimals}f}"[-1]) if poc.get("entry_tick") else None
        w.writerow({"i": i, "symbol": symbol, "contract": ct, "barrier": barrier,
                    "entry_time": poc.get("entry_tick_time"), "entry_spot": poc.get("entry_tick"),
                    "entry_digit": endig, "exit_time": poc.get("exit_tick_time"),
                    "exit_spot": poc.get("exit_tick"), "exit_digit": ed,
                    "is_win": int(win), "profit": profit})
        f.flush()
        i += 1
        time.sleep(0.15)
    f.close()
    end_bal = client.balance()["balance"]; client.close()
    return dict(symbol=symbol, contract=ct, barrier=barrier, n=nv, wins=wins,
                realized_winrate=round(wins/nv, 4) if nv else None,
                net_profit=round(net, 2), balance_delta=round(end_bal-start_bal, 2),
                start_bal=start_bal, end_bal=end_bal)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="1HZ100V"); ap.add_argument("--contract", default="DIGITEVEN")
    ap.add_argument("--barrier", default=None); ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--stake", type=float, default=0.5); ap.add_argument("--decimals", type=int, default=2)
    ap.add_argument("--mult", type=float, default=1.923); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    bar = None if a.barrier is None else int(a.barrier)
    print(json.dumps(run(a.symbol, a.contract, bar, a.n, a.stake, a.decimals, a.mult, a.out), indent=2))
