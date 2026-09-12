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
python3 crash1000_accu_audit.py --execute --minutes 300 --stake 1 --max-loss 200
```

`--check` makes one state check and exits with no purchases, even if combined with
`--execute`. Without `--execute`, the program only watches and reports. Use execution
commands only when you want the bounded demo audit. It can still wait if the condition
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

## Configurable session limits

Demo accounts only, with no real-account override. `--stake` is fixed for the session
(default $1, minimum $1, at most two decimal places); broker limits also apply. The
20-contract cap is removed. Purchases are limited by `--minutes`, `--max-loss` (default
$10), the candidate condition and all execution checks. At most one contract is pending.

`--max-loss` is the maximum **net realized loss from this session's starting point**,
not drawdown from the highest session profit and not losses from other bots/accounts.
Another buy requires room for the entire stake to be lost. For example, at stake $2.03
and max loss $5, two full losses stop the session at -$4.06 rather than risk crossing
-$5. With stake $1 and max loss $200, it never opens a trade that could take session
P&L below -$200 under the full-stake-loss assumption. Restarting resets these limits;
do not repeatedly restart to evade them or run multiple copies simultaneously.

The take-profit target scales with stake for the unchanged 20-growth-tick policy:
`floor_to_cent(stake * (1.04^20 - 1))`. Flooring avoids asking for a fraction of a cent
above the raw twentieth-tick profit and accidentally requiring another tick. The expected
sale value is the cent-rounded `stake * 1.04^20`, verified after settlement. For $1,
the profit target remains **$1.19**, with expected total value **$2.19**; for $2 it is
$2.38, with total $4.38. Non-integer stakes can have a target below the final displayed
profit: $2.03 targets $2.41 profit and expects a $4.45 sale. Larger-stake executions
remain experimental and halt if actual payment differs from this model.

The proposal must accept the configured stake and derived target. A buy uses those same
parameters, with maximum price equal to the stake and no
automatic retry. After entry, the audit checks the actual entry state, growth rate,
stake, shortcode barrier and active take-profit order. A mismatch prompts a manual
demo close and stops further purchases. If automatic take profit has not closed the
contract by 22 elapsed growth ticks, it requests a close once and waits for the final
contract response. A known exit timestamp takes precedence over `current_spot_time`,
which can keep advancing after the actual exit. A rejected fallback sell such as
`ContractAlreadySold` does not itself imply a strategy mismatch: a subsequently confirmed
20-tick take-profit exit may continue after full reconciliation. Actual specification
mismatches remain sticky; genuinely late exits, losses after tick 20, invalid payments,
or closure still unconfirmed after the bounded polling period halt further purchases.

Every observed contract response is logged, including audit ticks and actual sale values.
The console shows concise settlement summaries; the full contract stays in the JSONL
file. Each eligible completed path is checked against the displayed-price knockout model.
The first modeled breach must coincide with a losing exit; a winning take-profit exit
must have 20 protected ticks and pay the expected stake-scaled amount. Missing/inconsistent paths or disagreement
halt further buys. `path_check.matched` confirms that observation, NOT positive expected
value. A knockout on the twentieth tick is a loss, not a successful 20-tick survival.
`tick_count` is not treated as elapsed growth count; entry/current timestamps are used.
An entry slipping outside the band is labeled a mismatch, not counted as successful
candidate validation. A network failure during buy leaves its outcome unknown; no second
buy is attempted. Inspect the demo portfolio before restarting after any pending-state
warning. Ctrl+C does not cancel broker-side contracts. A lost connection may prevent a
manual close even though the broker-side take-profit or maximum tick limit still exists.
The duration limit stops new buys; an already-open contract is reconciled before exit,
so the process can finish shortly after the requested duration.

## Connection recovery

Read-only history, proposal and open-contract requests now have bounded recovery: up to
three additional attempts, with 1/2/4-second backoff. A transport failure reconnects and
verifies the original demo account ID before repeating the read. Rate-limit responses
back off without reconnecting. Recovery is subject to the existing session or settlement
deadline; individual connection/API timeouts still apply.

The running session's P&L, trade count, deadline, loss budget and pending contract ID stay
in memory across reconnects. No new purchase is allowed until a known pending contract
is confirmed closed and its actual profit and path are reconciled. `read_retry` and
`read_recovered` events make this visible. Account changes, exhausted recovery, invalid
contract data and model mismatches still halt the audit.

Buy requests are never automatically repeated. A disconnect during a buy whose contract
ID was not received still halts with an unknown outcome. A disconnected manual close is
also not resubmitted; its known contract is read and reconciled instead. This distinction
prevents duplicate purchases and treats the broker's final state as authoritative.

Recovery applies to a running process; it does not restore counters from an old log after
manual restart. Check any pending contract from a stopped run before restarting, and retain
the old log when evaluating the complete experiment. A real forced-socket-close test on
a previously settled demo contract verified same-account recovery with zero new orders.

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
configurable stake/loss budgets, sessions exceeding 20 contracts, duration enforcement,
stake-scaled take profit and payment, no buy retry, pending interruption, entry mismatch
and fallback close. These use fake executions. Live smoke checks only checked quotes;
no orders were placed for the session-limit update. Keep and, if large, zip the JSONL
file for review. A longer run does not guarantee a positive result.
