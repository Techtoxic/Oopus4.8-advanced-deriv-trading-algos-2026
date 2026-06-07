"""
N / N+1 / N+2 tick-mechanic mapping experiment.
For each trade we record the spot visible at purchase (N), then read the
contract's own entry_tick_time and exit_tick_time and locate them in the
real tick stream to determine EXACTLY which tick after N decides the payout.
1HZ100V emits 1 tick/sec so tick offset == time offset in seconds.
"""
import os, sys, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.deriv_client import DerivClient

SYM = "1HZ100V"; DEC = 2

def digit(p): return int(f"{p:.{DEC}f}"[-1])

def one_trade(client, dur):
    hist = client.ticks_history(SYM, count=5)
    n_time, n_spot = hist[-1]
    params = dict(amount=0.5, basis="stake", contract_type="DIGITEVEN",
                  currency="USD", duration=dur, duration_unit="t", symbol=SYM)
    buy = client.buy_contract(params, price=0.5)
    if "error" in buy:
        return {"dur": dur, "error": buy["error"]["message"]}
    cid = buy["buy"]["contract_id"]
    poc = client.proposal_open_contract(cid, timeout=60)
    et, xt = poc.get("entry_tick_time"), poc.get("exit_tick_time")
    return {
        "dur": dur,
        "N_time": n_time, "N_spot": n_spot, "N_digit": digit(n_spot),
        "entry_time": et, "entry_spot": poc.get("entry_tick"),
        "exit_time": xt, "exit_spot": poc.get("exit_tick"),
        "exit_digit": digit(poc.get("exit_tick")) if poc.get("exit_tick") else None,
        "entry_offset_from_N": (et - n_time) if et else None,
        "exit_offset_from_N": (xt - n_time) if xt else None,
        "contract_ticks_entry_to_exit": (xt - et) if (et and xt) else None,
        "status": poc.get("status"),
    }

def main():
    client = DerivClient(); client.connect()
    print(f"{'dur':<5}{'N_off_entry':<12}{'N_off_exit':<12}{'entry->exit':<13}{'status':<6}")
    agg = {}
    for dur in [1, 2, 5]:
        offs_entry, offs_exit = [], []
        for _ in range(8):
            r = one_trade(client, dur)
            if "error" in r:
                print("err", r["error"]); continue
            offs_entry.append(r["entry_offset_from_N"])
            offs_exit.append(r["exit_offset_from_N"])
            print(f"{dur:<5}{str(r['entry_offset_from_N']):<12}{str(r['exit_offset_from_N']):<12}"
                  f"{str(r['contract_ticks_entry_to_exit']):<13}{r['status']:<6}")
        agg[dur] = {"entry_off_mode": max(set(offs_entry), key=offs_entry.count) if offs_entry else None,
                    "exit_off_mode": max(set(offs_exit), key=offs_exit.count) if offs_exit else None}
    client.close()
    print("\n=== SUMMARY (offset in ticks from the tick visible at purchase) ===")
    for dur, a in agg.items():
        print(f"duration={dur}t -> entry tick = N+{a['entry_off_mode']}, "
              f"settlement tick = N+{a['exit_off_mode']}")

if __name__ == "__main__":
    main()
