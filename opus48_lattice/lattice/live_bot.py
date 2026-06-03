"""
lattice.live_bot — run the full 6-layer pipeline on Deriv's LIVE tick feed,
paper-traded (no money). Settles each decision exactly as a 1-tick digital
option would (entry = next tick, exit = tick after), and prints the running,
honest P/L so you can watch the house edge work in real time.

    python -m lattice.live_bot --symbol 1HZ100V --max-ticks 5000 [--conv 0.5]
                               [--bankroll 1000] [--no-drift] [--honest]

Default belief is the framework's own ("it trusts its 99% signal"); pass
--honest to size on the uniform truth (Kelly -> 0 -> it will simply observe).
Requires no auth: ticks stream from the public endpoint. Nothing is ever sent
to a real account.
"""
import argparse, asyncio, json, os, sys
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lattice import common
from lattice.engine import LatticeEngine

URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"


async def run(symbol, max_ticks, conv, bankroll, drift, belief, entropy_gate):
    # infer decimals from a short history pull
    async with websockets.connect(URL, max_size=2**24) as ws:
        await ws.send(json.dumps({"ticks_history": symbol, "style": "ticks",
                                  "count": 200, "end": "latest"}))
        hist = None
        while hist is None:
            m = json.loads(await ws.recv())
            if m.get("msg_type") == "history":
                hist = m["history"]
            elif "error" in m:
                raise RuntimeError(m["error"]["message"])
        import numpy as np
        places = common.infer_decimals(np.array(hist["prices"], dtype=float))
        eng = LatticeEngine(symbol, places, common.load_payouts(), belief=belief,
                            bankroll=bankroll, conv_threshold=conv,
                            require_entropy_gate=entropy_gate, disable_drift=not drift)
        print(f"[live] {symbol} {places}dp belief={belief} conv>={conv} "
              f"drift={'on' if drift else 'off'} bankroll=${bankroll}")
        await ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))
        pending = []       # list of (settle_after_n, order); settle after 2 more ticks
        seen = 0
        while seen < max_ticks:
            m = json.loads(await ws.recv())
            if m.get("msg_type") != "tick":
                continue
            t = m["tick"]; price = float(t["quote"]); epoch = int(t["epoch"])
            seen += 1
            # settle matured paper trades (lag-2 from decision)
            still = []
            d = int(round(price * (10 ** places))) % 10
            for (ticks_left, od) in pending:
                if ticks_left <= 1:
                    won, pnl = eng.settle(od, d)
                else:
                    still.append((ticks_left - 1, od))
            pending = still
            dec = eng.on_tick(price, epoch)
            if dec:
                for od in dec["orders"]:
                    pending.append((2, od))     # exit two ticks later
                s = eng.L6.summary()
                print(f"  t={seen:5d} d={d} -> {len(dec['orders'])} order(s); "
                      f"bank=${eng.bankroll:.2f} trades={s['trades']} "
                      f"win={s['win_rate'] if s['win_rate'] is not None else '-'}")
            if seen % 500 == 0:
                s = eng.L6.summary()
                print(f"[{seen}] bank=${eng.bankroll:.2f} trades={s['trades']} "
                      f"win={s['win_rate']} roi={s['roi']}")
        s = eng.L6.summary()
        print("\n==== LIVE PAPER SESSION SUMMARY ====")
        print(json.dumps({k: s[k] for k in ("trades", "wins", "win_rate", "pnl", "roi")}, indent=2))
        print(f"final bankroll ${eng.bankroll:.2f} (started ${bankroll})")
        print(f"gated: {eng.gated}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="1HZ100V")
    ap.add_argument("--max-ticks", type=int, default=3000)
    ap.add_argument("--conv", type=float, default=0.5)
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--drift", action="store_true", help="enable Layer-6 drift halt")
    ap.add_argument("--honest", action="store_true", help="size on uniform truth (Kelly->0)")
    ap.add_argument("--entropy-gate", action="store_true")
    a = ap.parse_args()
    belief = "honest" if a.honest else "framework"
    asyncio.run(run(a.symbol, a.max_ticks, a.conv, a.bankroll, a.drift, belief,
                    a.entropy_gate))


if __name__ == "__main__":
    main()
