# fable-thoughts — 2026-06-12 session

**Read `FINDINGS.md` first.** Tools in `tools/`, evidence in `results/`.

The one-paragraph story: the opus-thoughts JD100 step-physics edge survived a hostile audit —
with corrections. Lag-1 settlement is now PROVEN with real demo contracts (99.9% of 2k+ live
trades settled on decision-tick+1). The regime (σ≲4.3, spot≲240) opened for the first time on
06-11 and is live right now. Walk-forward says the edge exists ONLY inside that zone
(+1.5%/trade, t=1.6 in-zone; noise-bleed outside it) — so the production bot
(`tools/sentinel_v2.py`) carries a hard `--sigma-max` physics gate, empirical offset tables
rebuilt nightly, RTT/stale-tick guards, settlement tracking and risk caps. Two live demo hours
are logged (−$33 on 2.2k trades — statistically uninformative, execution flawless). The open
lottery ticket is `tools/reset_snipe.py`: RDBULL/RDBEAR tick straight through midnight to
EXACTLY 1000.0000 and the snipe tests whether 1-tick contracts can settle on that known tick —
run it at 23:58 GMT. Falsified this session: reset-index drift trading (priced to a uniform
−2.3% margin), CALLE/PUTE tie pairs (priced at execution; the proposal endpoint lies), barrier
dutch books (3% overround everywhere), jump-timing exploits (clustered but unpredictable).

## Quick start
```bash
pip install websocket-client numpy
export DERIV_TOKEN=...   # demo token
cd tools
python3 sentinel_v2.py --minutes 10                                   # watch
python3 sentinel_v2.py --trade --stake 1 --ev-gate 0.01 --minutes 480 --max-loss 100
python3 reset_snipe.py --stake 1                                      # at 23:58 GMT
```

## Sizing discipline (do not skip)
- Until cumulative LIVE t-stat > 2 over ≥20k in-zone trades: $1 stakes only.
- After that: quarter-Kelly ≈ 0.5% of bankroll per trade at +1.5% edge (full Kelly ≈ 2%;
  half of Kelly-optimal is where one σ of bad luck still compounds upward).
- Stop instantly if: executed digit payouts deviate from the 1.953 grid, exit-lag histogram
  grows a 2s tail > 5%, or JD100 gets re-based (spot jumps to a round number).
