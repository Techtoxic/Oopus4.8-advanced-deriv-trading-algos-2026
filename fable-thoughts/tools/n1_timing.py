"""n1_timing.py — THE critical unverified assumption from opus-thoughts:
does a 1-tick digit contract bought right after tick t settle on tick t+1 (lag-1, edge alive)
or tick t+2 (lag-2, edge dead)? Opus could never run this (AccountDisabled). We run it now.

Method: stream JD100 ticks on the public WS. The instant tick t (epoch T) arrives, fire a
single buy-with-parameters call on the authenticated WS. Then poll the contract and record
entry_tick_time and exit_tick_time relative to T. Repeat N times.
Run: python3 n1_timing.py [n_trades=10] [symbol=JD100]
"""
import sys, json, time
import websocket
from deriv_api import DerivWS, WS_PUBLIC

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    sym = sys.argv[2] if len(sys.argv) > 2 else "JD100"
    trader = DerivWS()
    assert trader.account.get("account_type") == "demo", "demo only"
    pub = websocket.create_connection(WS_PUBLIC, timeout=30)
    pub.send(json.dumps({"ticks": sym, "subscribe": 1}))
    rows = []
    done = 0
    while done < n:
        msg = json.loads(pub.recv())
        if msg.get("msg_type") != "tick":
            continue
        tk = msg["tick"]
        T = tk["epoch"]; t_local = time.time()
        recv_delay = t_local - T
        params = dict(amount=1, basis="stake", contract_type="DIGITDIFF", currency="USD",
                      duration=1, duration_unit="t", underlying_symbol=sym,
                      barrier=str((int(round(tk["quote"] * 100)) % 10 + 5) % 10))
        t0 = time.time()
        br = trader.call({"buy": 1, "price": 1.05, "parameters": params})
        rtt = time.time() - t0
        if "error" in br:
            print("BUY ERROR:", br["error"]); break
        cid = br["buy"]["contract_id"]
        buy_time = br["buy"].get("start_time") or br["buy"].get("purchase_time")
        # wait for settlement then fetch
        entry = exitt = status = None
        for _ in range(15):
            time.sleep(0.7)
            pc = trader.open_contract(cid)
            c = pc.get("proposal_open_contract", {})
            if c.get("is_sold") or c.get("status") in ("won", "lost"):
                entry = c.get("entry_tick_time"); exitt = c.get("exit_tick_time")
                status = c.get("status")
                break
        rows.append((T, recv_delay, rtt, buy_time, entry, exitt, status))
        lag_e = (entry - T) if entry else None
        lag_x = (exitt - T) if exitt else None
        print(f"tick T={T} recv+{recv_delay*1000:.0f}ms rtt={rtt*1000:.0f}ms buy_t={buy_time} "
              f"entry=T+{lag_e}s exit=T+{lag_x}s {status}")
        done += 1
        # drain any queued ticks so next decision tick is fresh
        pub.settimeout(0.05)
        try:
            while True: pub.recv()
        except Exception: pass
        pub.settimeout(30)
    if rows:
        lags = [r[5] - r[0] for r in rows if r[5]]
        print(f"\nexit-tick lag vs decision tick: {sorted(lags)}")
        print("VERDICT: settlement is decision-tick + ", sorted(lags)[len(lags)//2], "seconds (median)")

if __name__ == "__main__":
    main()
