"""pair_trader.py — H7 live verification + harvester: buy CALLE + PUTE 1-tick pairs.

The edge is unconditional (tie probability of the lattice), so no tick-timing race exists.
Each round: buy CALLE and PUTE simultaneously (two single buy-with-parameters calls), wait,
poll both contracts, log entry/exit spots, win/loss per leg, and round PnL.
Outcomes per $2 round (payout M): no tie -> one leg wins: M-2 = -0.047. tie -> both win: 2M-2 = +1.906.
Run: python3 pair_trader.py [rounds=30] [stake=1] [symbol=JD100] [pause=2]
"""
import sys, json, time
from deriv_api import DerivWS

def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    stake = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    sym = sys.argv[3] if len(sys.argv) > 3 else "JD100"
    pause = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
    tr = DerivWS()
    assert tr.account.get("account_type") == "demo", "demo only"
    print(f"acct {tr.account['account_id']} bal={tr.account.get('balance')}")
    total = 0.0
    ties = wins = 0
    logf = open("../results/pair_trader.log", "a")
    for i in range(rounds):
        cids = []
        for ct in ("CALLE", "PUTE"):
            br = tr.call({"buy": 1, "price": round(stake * 1.05, 2), "parameters":
                          dict(amount=stake, basis="stake", contract_type=ct, currency="USD",
                               duration=1, duration_unit="t", underlying_symbol=sym)})
            if "buy" not in br:
                print("buy err:", br.get("error")); time.sleep(2); break
            cids.append((ct, br["buy"]["contract_id"]))
        if len(cids) < 2:
            continue
        time.sleep(2.5)
        rec = {}
        for ct, cid in cids:
            for _ in range(10):
                pc = tr.open_contract(cid)
                c = pc.get("proposal_open_contract", {})
                if c.get("is_sold") or c.get("status") in ("won", "lost"):
                    rec[ct] = (c.get("status"), float(c.get("profit", 0)),
                               c.get("entry_spot"), c.get("exit_spot"),
                               c.get("entry_spot_time"), c.get("exit_spot_time"))
                    break
                time.sleep(0.8)
        if len(rec) < 2:
            print("settle timeout"); continue
        rpnl = rec["CALLE"][1] + rec["PUTE"][1]
        total += rpnl
        tie = rec["CALLE"][0] == "won" and rec["PUTE"][0] == "won"
        ties += int(tie); wins += int(rpnl > 0)
        line = (f"[{i+1}/{rounds}] CALLE={rec['CALLE'][0]} PUTE={rec['PUTE'][0]} "
                f"entry={rec['CALLE'][2]} exit={rec['CALLE'][3]} "
                f"(t {rec['CALLE'][4]}->{rec['CALLE'][5]}) round={rpnl:+.2f} cum={total:+.2f}"
                + ("  <== TIE, BOTH WON" if tie else ""))
        print(line); logf.write(line + "\n"); logf.flush()
    n = i + 1
    print(f"\n=== {n} rounds: ties={ties} ({ties/n:.3f}) total={total:+.2f} "
          f"avg/round={total/n/(2*stake)*100:+.2f}% of stake")
    logf.write(f"SUMMARY {n} rounds ties={ties} total={total:+.2f}\n")
    logf.close()

if __name__ == "__main__":
    main()
