# JD100 centered-digit demo runner

Use `tools/jd100_demo.py`, not the older adaptive sentinel, for a bounded measurement of
the currently tested rule. This is experimental software, not a profitability guarantee.

Install `websocket-client`. Configure `DERIV_TOKEN` and `DERIV_APP_ID` in your environment
using a demo account. Do not paste credentials into source files. Existing credentials
embedded in older repository files should be revoked and replaced.

From the repository root:

```sh
python3 fable-thoughts/tools/jd100_demo.py --minutes 10
python3 fable-thoughts/tools/jd100_demo.py --check-latency
python3 fable-thoughts/tools/jd100_demo.py --trade --stake 1 --minutes 60 --max-trades 100 --max-loss 10 --log jd100-demo.jsonl
```

The runner refuses real accounts with no override. It buys UNDER5 only on current digit 2,
and OVER4 only on digit 7, within the sampled price interval 171.28–185. It requires two
decimal places and a tick age no greater than 0.35 seconds. Local clock synchronization is
required. It may skip many opportunities; inactivity is preferable to a stale buy.

Run `--check-latency` first: it authenticates to demo and makes five ping requests, with
no purchases even if combined with `--trade`. Trading requires the worst ping RTT to be
at most 0.4 seconds, rechecked every 30 seconds before fetching a decision tick. Tick age
plus that RTT plus a 0.25-second reserve must fit within one second. These conservative
limits screen poor connections; ping cannot guarantee buy-processing latency. A slow buy
still settles and is logged, then halts further trading if its RTT exceeds 0.4 seconds.
Never disable the settlement guard or repeatedly restart after a lag-2 result.

It holds at most one contract, never retries a buy, logs each contracted payout, and waits
for settlement before another decision. It halts if payout is below 1.79, settlement is
not exactly decision epoch +1, or the remaining loss budget cannot cover another stake.
The first contract after a payout change can still lose: price deterioration is detected
after purchase, not prevented. A transport failure after purchase is ambiguous: inspect
the demo portfolio before restarting. Session limits reset on restart.

At $1 the observed payout is 1.79 rather than the $10 payout of 1.794. The breakeven
win rate is therefore 55.87%. Do not infer execution quality from historical win rates.
The JSONL log records timing, payout and realized profit separately. Watch mode makes no
purchases. Demo smoke testing confirmed a next-tick fill after the final code change;
that proves the path ran, not its long-run reliability or profitability.
