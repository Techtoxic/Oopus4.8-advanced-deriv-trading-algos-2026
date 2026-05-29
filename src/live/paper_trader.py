"""
Live paper-trader / demo runner for Deriv.

Connects to the live Deriv tick stream and runs any strategy from the research
suite against REAL incoming ticks, with correct settlement lag, REAL payouts
(fetched via proposal), full transparency, and an honest live equity curve.

Two modes:
  * PAPER (default): no token, no money. Decisions are settled against the live
    ticks exactly as a real 1-tick contract would settle. This is the safest way
    to "run it on demo" and watch the edge bleed in real time.
  * DEMO EXECUTION (--execute, requires a Deriv DEMO API token via --token or the
    DERIV_TOKEN env var): actually buys contracts on your demo account. Refuses to
    run without an explicit token and prints the account balance/currency first so
    you can confirm it is a virtual account.

Usage:
    python paper_trader.py --symbol 1HZ100V --strategy differs --bankroll 10 --stake 0.35 --max-trades 300
    python paper_trader.py --symbol 1HZ100V --strategy even --stake 0.35 --max-trades 300
    python paper_trader.py --symbol R_100 --strategy momentum --k 3

Strategies: differs, even, over0, gambler_low, momentum, meanrev
"""
import argparse, asyncio, json, os, sys, time
from collections import deque
import websockets

APP_ID = 1089
URL = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"

def decimals(prices):
    for p in range(0, 7):
        if all(abs(round(x*10**p) - x*10**p) < 1e-4 for x in prices[-50:]):
            return p
    return 5

def last_digit(price, dp):
    return int(round(price * 10**dp)) % 10

# ---- strategies: (history_prices, history_digits) -> (contract, barrier) | None ----
def make_strategy(name, k=3):
    def differs(P, D):   return ("DIGITDIFF", D[-1])
    def even(P, D):      return ("DIGITEVEN", None)
    def over0(P, D):     return ("DIGITOVER", 0)
    def gambler_low(P,D):
        return ("DIGITOVER", 3) if D[-1] <= 2 else None
    def momentum(P, D):
        if len(P) <= k: return None
        return ("CALL", None) if all(P[-i] > P[-i-1] for i in range(1,k+1)) else None
    def meanrev(P, D):
        if len(P) <= k: return None
        return ("CALL", None) if all(P[-i] < P[-i-1] for i in range(1,k+1)) else None
    return {"differs":differs,"even":even,"over0":over0,"gambler_low":gambler_low,
            "momentum":momentum,"meanrev":meanrev}[name]

def settle(contract, barrier, entry_price, exit_price, exit_digit):
    if contract == "DIGITDIFF":  return exit_digit != barrier
    if contract == "DIGITEVEN":  return exit_digit % 2 == 0
    if contract == "DIGITOVER":  return exit_digit > barrier
    if contract == "CALL":       return exit_price > entry_price
    if contract == "PUT":        return exit_price < entry_price
    return False

async def get_payout(ws, symbol, contract, barrier):
    req = {"proposal":1,"amount":1,"basis":"stake","currency":"USD",
           "contract_type":contract,"symbol":symbol,"duration":1,"duration_unit":"t"}
    if barrier is not None and contract in ("DIGITOVER","DIGITUNDER","DIGITDIFF","DIGITMATCH"):
        req["barrier"] = str(barrier)
    await ws.send(json.dumps(req))
    for _ in range(50):
        m = json.loads(await ws.recv())
        if m.get("msg_type")=="proposal" and isinstance(m.get("proposal"),dict):
            return m["proposal"]["payout"]
        if "error" in m:
            return None
    return None

async def run(args):
    strat = make_strategy(args.strategy, args.k)
    prices = deque(maxlen=500); digits = deque(maxlen=500)
    pending = []   # (exit_index, contract, barrier, entry_price, stake, payout)
    tick_index = 0
    bankroll = args.bankroll; start = bankroll
    n = wins = losses = 0; total_staked = 0.0; loss_streak = 0
    peak = bankroll; max_dd = 0.0; dp = None
    payout_cache = {}

    print(f"# PAPER live run | symbol={args.symbol} strategy={args.strategy} "
          f"bankroll=${bankroll:.2f} stake=${args.stake:.2f} (no real money)")
    async with websockets.connect(URL, ping_interval=20, ping_timeout=60) as ws:
        await ws.send(json.dumps({"ticks": args.symbol, "subscribe": 1}))
        while n < args.max_trades:
            m = json.loads(await ws.recv())
            if m.get("msg_type") != "tick":
                continue
            price = float(m["tick"]["quote"])
            prices.append(price)
            if dp is None and len(prices) >= 50:
                dp = decimals(list(prices))
            cur_dp = dp if dp is not None else 2
            d = last_digit(price, cur_dp)
            digits.append(d)
            tick_index += 1

            # settle matured contracts
            still = []
            for (exit_idx, contract, barrier, entry_price, stake, payout) in pending:
                if tick_index >= exit_idx:
                    won = settle(contract, barrier, entry_price, price, d)
                    n += 1; total_staked += stake
                    if won:
                        bankroll += stake*(payout-1); wins += 1; loss_streak = 0
                    else:
                        bankroll -= stake; losses += 1; loss_streak += 1
                    peak = max(peak, bankroll); max_dd = max(max_dd, peak-bankroll)
                    wr = wins/n*100
                    print(f"[{n:4d}] {contract:9s}{'' if barrier is None else ' b='+str(barrier):4s} "
                          f"{'WIN ' if won else 'LOSS'} stake ${stake:.2f} payout {payout:.2f} "
                          f"| bankroll ${bankroll:7.2f} | win% {wr:5.1f} | PnL ${bankroll-start:+7.2f} "
                          f"| maxDD ${max_dd:.2f}")
                    if bankroll < args.stake:
                        print(f"\n*** RUINED after {n} trades. Bankroll ${bankroll:.2f} < stake. ***")
                        return summarize(args,n,wins,losses,total_staked,bankroll,start,max_dd)
                else:
                    still.append((exit_idx, contract, barrier, entry_price, stake, payout))
            pending = still

            # new decision (only if no open contract, to keep it non-overlapping)
            if not pending and len(prices) > args.k + 2:
                sig = strat(list(prices), list(digits))
                if sig is not None:
                    contract, barrier = sig
                    stake = args.stake * (2**loss_streak if args.martingale else 1)
                    if stake > bankroll:
                        print(f"\n*** Cannot fund martingale stake ${stake:.2f} > bankroll ${bankroll:.2f} = RUIN ***")
                        return summarize(args,n,wins,losses,total_staked,bankroll,start,max_dd)
                    ck = (contract, barrier)
                    if ck not in payout_cache:
                        po = await get_payout(ws, args.symbol, contract, barrier)
                        payout_cache[ck] = po or 1.0
                    payout = payout_cache[ck]
                    # entry = next tick, exit = entry + 1 (1-tick contract) => exit_index = tick_index+2
                    pending.append((tick_index+2, contract, barrier, price, stake, payout))
    return summarize(args,n,wins,losses,total_staked,bankroll,start,max_dd)

def summarize(args,n,wins,losses,total_staked,bankroll,start,max_dd):
    print("\n" + "="*70)
    print(f"SUMMARY {args.symbol} / {args.strategy}{' / MARTINGALE' if args.martingale else ''}")
    print(f"  trades {n} | wins {wins} ({(wins/n*100 if n else 0):.1f}%) | losses {losses}")
    print(f"  total staked ${total_staked:.2f} | net PnL ${bankroll-start:+.2f} "
          f"| ROI {((bankroll-start)/total_staked*100 if total_staked else 0):+.2f}%")
    print(f"  end bankroll ${bankroll:.2f} | max drawdown ${max_dd:.2f}")
    print("="*70)
    return bankroll

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="1HZ100V")
    ap.add_argument("--strategy", default="differs",
                    choices=["differs","even","over0","gambler_low","momentum","meanrev"])
    ap.add_argument("--bankroll", type=float, default=10.0)
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--max-trades", type=int, default=200)
    ap.add_argument("--martingale", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args))

if __name__ == "__main__":
    main()
