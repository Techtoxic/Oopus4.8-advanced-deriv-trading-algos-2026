# PRIVATE-FABLE — the institutional layer (compile-verified 0/0)

What actually separates institutional operations from retail EAs is not a
secret indicator — it's the RISK DESK and the ALLOCATOR. These two are that.

| File | Role |
|---|---|
| `FABLE_TailGuard_Overlay.mq5` | Account-level risk desk supervising EVERY position from EVERY EA/magic/symbol: drawdown ladder (halve → flatten+lock), portfolio heat cap, correlation-cluster limits, no-stop police (auto-SL on naked positions), margin guard, weekend flat. Attach to one chart, set `InpDryRun=true` first to watch what it WOULD do. |
| `FABLE_RegimeAllocator.mq5` | Desk-style allocator routing risk between the two validated engines (TREND core / TSMOM) with a shock-regime cutoff and per-engine trailing-expectancy weights (floored, never zeroed). Engines that decay lose allocation instead of your account. Run on XAUUSD first. |

TailGuard would have capped the HedgeGuardian floating-loss spiral by
construction — that is the class of failure it exists to prevent.
