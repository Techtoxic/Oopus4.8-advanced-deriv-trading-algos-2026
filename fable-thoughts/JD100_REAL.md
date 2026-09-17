# Explicit real-account support

This mode places real-money orders only when **both `--real` and `--trade`** are supplied.
Without `--real`, trading explicitly selects a demo account and does not fall back to real.
Without `--trade`, no orders are placed. The model and its profitability are not guaranteed.

## Replace damaged local files

Use a fresh download of the `fable-thoughts` branch, or replace files from the supplied clean
bundle. Do not merge its contents into hand-edited function bodies. In particular, deleting
every `except Exception as exc:` line breaks unrelated `try` blocks and cannot repair syntax.
Preserve your old JSONL session logs separately.

Keep these four files from the same revision in `fable-thoughts/tools`:

- `adaptive_sentinel_v4.py`
- `jd100_continuous.py`
- `deriv_api.py`
- `crash1000_accu_audit.py`

The bundle also includes the unchanged imported helpers `jd100_demo.py`, `sigma_model.py`,
and `derivfetch.py`. Python dependencies are `numpy` and `websocket-client`.

From the tools directory, syntax checking does not connect or trade:

```powershell
python3 -m py_compile adaptive_sentinel_v4.py jd100_continuous.py deriv_api.py crash1000_accu_audit.py jd100_demo.py sigma_model.py derivfetch.py
```

## Configuration and small test command

Configure `DERIV_TOKEN` and `DERIV_APP_ID` securely in your local environment. The token must
have access to the intended USD real account and permission to trade. No token is embedded
in these files. The account must have enough available funds for the requested stake.
Successful proposal requests do not prove an order can be funded or accepted.

The following is a bounded example that **will place real-money orders if you run it**:

```powershell
python3 adaptive_sentinel_v4.py --continuous --real --trade --stake 0.35 --minutes 10 --max-trades 20 --max-loss 2 --max-pending 1
```

`--max-loss` is the net session-loss budget, not a trailing drawdown limit. Every unresolved
stake remains reserved. A manual restart starts a new budget; separate bots do not share
one account-wide budget. At $0.35, cent rounding can change the payout multiplier, so the
bot requests an actual-stake quote and checks the purchased contract's terms.

## Diagnostics

- `real_trade: true` identifies a real trading session. The historical field `demo_trade`
  is now false for real mode rather than simply indicating that a trading client exists.
- A requested account type must exist. Buyer and settlement account IDs must match;
  read recovery refuses an account switch. Existing accumulator runners remain demo-only.
- A reported balance below the stake produces a balance-specific halt before a buy.
- Initialization errors include their stage. Imported-file syntax errors include the
  filename and line number without printing source lines or credential-bearing tracebacks.
- An unsuccessful buy response logs a sanitized broker `error_code`, where available.
  No buy is retried. Unknown outcomes retain their reservation: check the selected account's
  portfolio before restarting. Do not infer that zero confirmed trades means no purchase
  could have happened when `unknown_buy_outcome` is true.
- Continuous-mode feed and heartbeat transport failures use bounded reconnects. A buy
  response timeout waits up to another 20 seconds for the matching acknowledgment on the
  original connection, without resending the order. A recovered session keeps its existing
  P&L and risk limits. A lost connection or still-unknown buy cannot be safely retried and
  remains a stop condition. Update both `deriv_api.py` and `jd100_continuous.py` for this behavior.

Latency and non-next-tick timing remain diagnostic, not shutdown conditions. Payout/stake
checks, stale-tick rejection, and unresolved-contract handling still apply. This change
cannot prevent broker rejections, outages, insufficient funds, or losses.

Validation of this change uses mocked API responses and Python compilation only. No live
or demo bot session is required to run those tests, and no real trade was placed during development.
