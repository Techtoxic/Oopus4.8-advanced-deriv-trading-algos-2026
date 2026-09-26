"""multi_trader.py - fire the same signal across N accounts in parallel.

WHY PARALLEL AND NOT SEQUENTIAL
The measured budget is tight. A tick arrives every 1000ms; the decision costs 0.2ms and a
buy round trip runs 120-190ms median with a tail past 3000ms. Entry slippage is already 30%
against a 37% breakeven, so there are only 7pp of headroom.

Firing accounts sequentially would put account 2's order ~190ms behind account 1's, account
3's ~380ms behind, and so on. By account 5 the order lands most of a tick late and that
account trades a different digit from the one the signal was computed on. The later accounts
would systematically underperform.

Firing them in parallel on independent connections means all five round trips overlap. Total
elapsed is the SLOWEST leg, not the sum. Python releases the GIL during socket I/O, so
threads genuinely run concurrently here.

WHAT THIS IS NOT
Five accounts on one signal is 5x POSITION SIZE, not risk spreading. Same symbol, same tick,
same direction - the outcomes are perfectly correlated. At the measured +6.45% EV and a
per-trade sd near the full stake, five accounts at $0.50 is the risk of one account at $2.50.

Size accordingly: whatever half-Kelly says for the combined bankroll, divided by five.

USAGE
    from multi_trader import MultiTrader

    mt = MultiTrader(["token1", "token2", ...])   # connects and verifies at startup
    mt.report()                                   # account ids, types, balances

    res = mt.buy(contract_type="DIGITOVER", barrier="4", symbol="JD100",
                 stake=0.50, duration=1)
    # res = [{"idx":0,"ok":True,"contract_id":...,"payout":...,"rtt_ms":...}, ...]

    mt.close()

SAFETY
  - refuses to start if any token resolves to a REAL account unless --allow-real is passed
    through allow_real=True
  - reports per-account round trip so a degrading connection is visible immediately
  - a failed leg does not block the others
"""
import threading
import time
from deriv_api import DerivWS


class MultiTrader:
    def __init__(self, tokens, allow_real=False, verbose=True):
        if not tokens:
            raise ValueError("no tokens supplied")
        self.tokens = list(tokens)
        self.conns = []
        self.info = []
        for i, tok in enumerate(self.tokens):
            c = DerivWS(token=tok)
            acct = c.account or {}
            atype = acct.get("account_type", "?")
            if atype != "demo" and not allow_real:
                for x in self.conns:
                    try:
                        x.close()
                    except Exception:
                        pass
                raise RuntimeError(
                    f"token {i} is a {atype} account. Pass allow_real=True only when "
                    f"you mean it - five accounts on one signal is 5x size.")
            self.conns.append(c)
            self.info.append(dict(idx=i, id=acct.get("account_id", "?"),
                                  type=atype, bal=float(acct.get("balance", 0))))
            if verbose:
                print(f"  [{i}] {acct.get('account_id','?')} {atype} "
                      f"bal={acct.get('balance', 0)}")
        self.n = len(self.conns)

    def report(self):
        tot = sum(x["bal"] for x in self.info)
        print(f"  {self.n} accounts connected, combined balance {tot:,.2f}")
        return self.info

    def buy(self, contract_type, symbol, stake, duration=1,
            duration_unit="t", barrier=None, max_price_mult=20, timeout=8.0):
        """
        Fire the same contract on every account SIMULTANEOUSLY.
        Returns one result dict per account, in account order.
        Total elapsed is the slowest leg, not the sum.
        """
        params = dict(amount=stake, basis="stake", contract_type=contract_type,
                      currency="USD", underlying_symbol=symbol,
                      duration=duration, duration_unit=duration_unit)
        if barrier is not None:
            params["barrier"] = str(barrier)
        req = {"buy": 1, "price": round(stake * max_price_mult, 2),
               "parameters": params}

        results = [None] * self.n
        threads = []

        def fire(i):
            t0 = time.perf_counter()
            try:
                r = self.conns[i].call(req)
                rtt = (time.perf_counter() - t0) * 1000
                if "buy" in r:
                    b = r["buy"]
                    bp = float(b.get("buy_price") or stake)
                    results[i] = dict(idx=i, ok=True,
                                      contract_id=b.get("contract_id"),
                                      payout=float(b["payout"]) / bp,
                                      buy_price=bp, rtt_ms=rtt, err=None)
                else:
                    results[i] = dict(idx=i, ok=False, contract_id=None,
                                      payout=None, buy_price=None, rtt_ms=rtt,
                                      err=r.get("error", {}).get("message", "")[:70])
            except Exception as e:
                results[i] = dict(idx=i, ok=False, contract_id=None, payout=None,
                                  buy_price=None,
                                  rtt_ms=(time.perf_counter() - t0) * 1000,
                                  err=f"{type(e).__name__}: {e}"[:70])

        # spawn all first, then start - so the sends go out together rather than
        # staggered by thread construction time
        for i in range(self.n):
            threads.append(threading.Thread(target=fire, args=(i,), daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=timeout)

        for i in range(self.n):
            if results[i] is None:
                results[i] = dict(idx=i, ok=False, contract_id=None, payout=None,
                                  buy_price=None, rtt_ms=timeout * 1000,
                                  err="timeout")
        return results

    @staticmethod
    def summarise(results):
        ok = [r for r in results if r["ok"]]
        rtts = [r["rtt_ms"] for r in results]
        line = (f"{len(ok)}/{len(results)} filled  "
                f"rtt min {min(rtts):.0f} max {max(rtts):.0f}ms")
        bad = [r for r in results if not r["ok"]]
        if bad:
            line += "  FAILED: " + ", ".join(f"[{r['idx']}] {r['err']}" for r in bad)
        return line

    def close(self):
        for c in self.conns:
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", nargs="+", required=True)
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--rounds", type=int, default=10)
    a = ap.parse_args()

    print(f"connecting {len(a.tokens)} accounts...")
    mt = MultiTrader(a.tokens)
    mt.report()
    print(f"\nfiring {a.rounds} parallel rounds to measure the spread across accounts\n")

    allr = []
    for k in range(a.rounds):
        t0 = time.perf_counter()
        res = mt.buy("DIGITOVER", a.symbol, a.stake, barrier="4")
        wall = (time.perf_counter() - t0) * 1000
        allr.extend(res)
        print(f"  [{k+1}/{a.rounds}] wall {wall:>6.0f}ms  {MultiTrader.summarise(res)}")
        time.sleep(1.2)
    mt.close()

    print("\nper-account round trip:")
    for i in range(mt.n):
        v = [r["rtt_ms"] for r in allr if r["idx"] == i and r["ok"]]
        if v:
            v.sort()
            print(f"  [{i}] median {v[len(v)//2]:>6.0f}ms   max {v[-1]:>6.0f}ms   "
                  f"n={len(v)}")
    seq = sum(sorted([r["rtt_ms"] for r in allr if r["idx"] == i and r["ok"]] or [0])[len(
        [r for r in allr if r['idx'] == i and r['ok']]) // 2] for i in range(mt.n))
    print(f"\n  parallel cost = the slowest leg; sequential would have cost ~{seq:.0f}ms")
    print("  if any account's median is far above the others, that connection is the")
    print("  bottleneck and its slippage will be worse than the rest.")
