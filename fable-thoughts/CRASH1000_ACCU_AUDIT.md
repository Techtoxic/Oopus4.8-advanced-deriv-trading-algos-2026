# CRASH1000 accumulator candidate audit

This is a bounded demo experiment, not a validated profitable bot. It uses Deriv's
Options API, not MT5. It does not trade JD100 or adapt to other symbols or growth rates.

## Commands

Install `websocket-client`. Configure `DERIV_TOKEN` and `DERIV_APP_ID` securely in your
environment. Never put tokens in source or shell command arguments. From
`fable-thoughts/tools`:

```sh
python3 crash1000_accu_audit.py --check
python3 crash1000_accu_audit.py --minutes 60
python3 crash1000_accu_audit.py --execute --minutes 60
```

`--check` makes one state check and exits with no purchases, even if combined with
`--execute`. Without `--execute`, the program only watches and reports. Use the last
command only when you want the bounded demo audit. It can still wait if the condition
is false; it does not force purchases just because it is running. `--minutes` is limited
to six hours. A uniquely named JSONL file is created in the current directory; optional
`--out` must name a new file, never an existing one.

## Frozen candidate condition

* CRASH1000, ACCU, 4% growth, three-decimal prices.
* Exact relative barrier `0.0000023454` and at least 20 maximum growth ticks.
* Quoted spot times barrier times 1000 must lie in **[14, 14.25)**.
* At that barrier, approximately **5969.131065 <= spot < 6075.722691**.

The exact computed condition wins over rounded screen numbers. A 3%-growth contract
or a different price level with the same fractional phase is not this test. A changed
barrier, price precision, stake or take-profit specification halts the run.

The published historical replication had 13,492 nonoverlapping entries and +7.38%
idealized wealth return, but it was an earlier historical window, not prospective
execution. Internal versus displayed-price knockout rounding remains unresolved.
These results are not a profitability guarantee. Three earlier mechanics probes outside
the candidate state did not validate this strategy.

## Fixed execution limits

Demo accounts only, with no real-account override. Stake is fixed at $1. At most 20
contracts per session and $10 realized loss; another buy requires room for its full
$1 possible loss. There is at most one pending contract. Restarting resets the limits;
do not repeatedly restart to evade the loss cap or run multiple copies simultaneously.

The proposal must accept a **$1.19 profit target**, aiming at $2.19 total value after
20 surviving growth steps. A buy uses the same parameters, with max price $1 and no
automatic retry. After entry, the audit checks the actual entry state, growth rate,
stake, shortcode barrier and active take-profit order. A mismatch prompts a manual
demo close and stops further purchases. If automatic take profit has not closed the
contract by 22 elapsed growth ticks, it requests a close and halts for review.

Every observed contract response is logged, including audit ticks and actual sale values.
The console shows concise settlement summaries; the full contract stays in the JSONL
file. Each eligible completed path is checked against the displayed-price knockout model.
The first modeled breach must coincide with a losing exit; a winning take-profit exit
must have 20 protected ticks and pay $2.19. Missing/inconsistent paths or disagreement
halt further buys. `path_check.matched` confirms that observation, NOT positive expected
value. A knockout on the twentieth tick is a loss, not a successful 20-tick survival.
`tick_count` is not treated as elapsed growth count; entry/current timestamps are used.
An entry slipping outside the band is labeled a mismatch, not counted as successful
candidate validation. A network failure during buy leaves its outcome unknown; no second
buy is attempted. Inspect the demo portfolio before restarting after any pending-state
warning. Ctrl+C does not cancel broker-side contracts. A lost connection may prevent a
manual close even though the broker-side take-profit or maximum tick limit still exists.

Earlier checks verified proposal take-profit acceptance; three separate demo mechanics
probes checked growth resale behavior without a take-profit order. Automatic take-profit
execution inside the candidate state was subsequently observed in the September-12
20-contract user run: all six winners paid $2.19 after 20 growth ticks, and all fourteen
knockouts matched the displayed-price model. This verifies those observed executions,
not the expected profitability of future trades. See [the live review](CRASH1000_LIVE_REVIEW_2026-09-12.md).

## Tests

From the repository root:

```sh
python3 -m unittest discover -s fable-thoughts/tools -p test_crash1000_accu_audit.py -v
```

Tests cover eligibility, changed specifications, read-only modes, real-account refusal,
fixed loss/count limits, no buy retry, pending interruption, entry mismatch and fallback
close. These use fake executions. Live smoke checks only checked quotes and placed no
trades while the candidate was inactive. Send the JSONL file for execution review before
considering any larger experiment.
