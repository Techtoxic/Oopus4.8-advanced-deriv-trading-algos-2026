# CRASH500 accumulator candidate audit

This is a separate entry point for a historical candidate, not a proven profitable bot.
It uses the Options API and demo accounts only, not MT5. It shares the tested execution,
reconnection, risk-budget and path-reconciliation engine with CRASH1000; it does not copy
and diverge that logic or change the CRASH1000 defaults.

## Run from `fable-thoughts/tools`

Install `websocket-client` and configure `DERIV_TOKEN` and `DERIV_APP_ID` securely in the
environment, as for the CRASH1000 bot. Do not place tokens in command arguments or source.

```sh
python3 crash500_accu_audit.py --check
python3 crash500_accu_audit.py --minutes 60
python3 crash500_accu_audit.py --execute --minutes 300 --stake 1 --max-loss 200
```

`--check` checks once and never buys, even with `--execute`. Without `--execute`, the bot
only watches. Execution can wait outside the allowed spot range; do not loosen the gate
to force activity. The default stake is $1 and default net-session-loss budget is $10.
The last example explicitly raises the demo budget to $200. Duration is limited to six
hours; there is no contract-count cap. It still halts on unrecoverable or ambiguous
execution, changed terms, account changes, missing audit data or model disagreement.

## Exact frozen CRASH500 conditions

| Parameter | Required value |
|---|---|
| Symbol | CRASH500 |
| Contract | ACCU |
| Growth | 4% |
| Price precision | Three decimals |
| Relative barrier | 0.0000047141 |
| Barrier width in pip units | 14 inclusive to 14.25 exclusive |
| Spot interval at that barrier | 2969.813962 <= spot < 3022.846355 |
| Intended exit | 20 surviving growth ticks |

The bot checks both the quote and the actual entered contract. A CRASH1000 barrier or
contract is rejected by the CRASH500 configuration. Only integer level 14 is enabled;
another integer level sharing the same fractional phase is not covered by this bot.

At $1, the take-profit trigger is $1.19 profit and expected sale value $2.19. Other stakes
use the shared cent-rounded 20-tick calculation. Stakes are fixed per session, not
compounded or increased after losses. The broker validates minimum and maximum amounts.
Actual CRASH500 in-state take-profit and knockout execution remains to be verified by
the audit; successful quotes or tests with fake contracts are not live proof.

## Evidence and limits

An earlier, disjoint 600,000-tick historical replication had +4.15% idealized pooled
return. The level-14 subset used here had 7,526 entries across four days and +4.02%
idealized return, with an approximate day-bootstrap 99% interval of +3.09% to +4.96%.
These are not prospective executed returns. Internal/displayed-price rounding sensitivity
spans losses as well as gains; an extra-exit-tick sensitivity also weakened confidence.
The CRASH1000 live results do not automatically validate CRASH500.

The [shared audit documentation](CRASH1000_ACCU_AUDIT.md) explains recovery and safety
behavior. Use the CRASH500 symbol and price range above instead of its CRASH1000 values.
`--max-loss` is net realized loss from the start of this process, not a trailing peak
drawdown limit. Running two bots creates two separate risk budgets; $200 on each is not
a combined $200 limit. Restarting resets counters. Keep every log and check pending
contracts before restarting after a halt.

Logs use the prefix `crash500_accu_` and identify the symbol in state/settlement records.
Send a completed small-stake demo log for review before considering larger experiments.
