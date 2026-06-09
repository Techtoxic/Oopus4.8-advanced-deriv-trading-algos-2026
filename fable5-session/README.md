# Fable 5 Session — 2026-06-09

One session, two fronts, everything verified before shipping.

## `deriv/` — independent re-audit of "can a bot be +EV on Deriv?"
- **Synthetics: prior conclusion CONFIRMED from scratch** on 150k fresh ticks
  (5 symbols) + live payouts. RNG is statistically fair; house edge is real;
  no conditional strategy clears the payout threshold. Full numbers in
  `FINDINGS_FABLE5.md` and `results/battery_results.txt`.
- **New finding: Deriv's REAL markets are predictable** — 1-minute mean
  reversion on frxUSDJPY (autocorr −0.11, z = −10.6, 54% directional). It is
  the first genuine inefficiency found in this program — and it is ~10× too
  small for Deriv's multiplier costs (≈0.5 bps edge vs ≈6.4 bps round trip).
  On an MT5 raw-spread account the same signal is borderline tradeable.
- **Bots (both live-tested on demo this session):**
  - `bots/edge_sentinel.py` — rolling statistical battery wired to live
    payouts + quarter-Kelly executor. Trades the instant any RNG deviation
    clears the live payout threshold; provably refuses a fair coin.
  - `bots/realmarket_scanner.py` — re-prices the real-market edge vs live
    costs every cycle and arms itself only when edge > 1.5× costs.

## `mt5/` — nine EAs, every one compiled clean (0 errors, 0 warnings)
| EA | What it is |
|---|---|
| `FFZ_v2` | Purple Bills follower + chop filter + profit protection + optional COT bias |
| `XU_SEMA_NoRepaint_EA` | The "settled arrow" made causal — fires once, never un-prints |
| `XU_SEMA_QTheory_EA` | Quarterly Theory: QFib sweeps + time regimes + SEMA locks |
| `GoldSilver_HedgeGuardian_EA` | Zone recovery with pre-computed bounded worst case + gold/silver ratio pair hedge |
| `MSNR_v532_Fixed` | Rejection-funnel diagnostics + the 7 stacked gates that made v5.31 never trade, relaxed |
| `IFVG_EA_v2` | Structure SL/TP, risk sizing, trend filter, giveback guard |
| `MTF_RSI_Divergence_EA_v2` | Pivot-anchored SL, conviction sizing on synced divergences |
| `QuantumGoldSilver_v4` | Fixed real TP/SL-ratio math bug + partial/giveback protection |
| `SEMA_FVG_CISD_Confluence_EA` | HTF FVG (location) + SEMA lock (structure) + CISD close (trigger) |

See `mt5/README.md` for per-EA details and suggested backtests.
