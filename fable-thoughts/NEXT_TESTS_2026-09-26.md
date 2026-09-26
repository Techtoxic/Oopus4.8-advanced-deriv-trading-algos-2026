# Next tests (2026-09-26): boundary physics, not prediction

This is a brief for an agent working in this repo. Read `PROJECT_BRIEF.md` §2–§3 and
`ACCU_PHASE_LATTICE.md` §6 first.

## Why these and not others

Past ticks carry about 0.002 bits of information about the next tick, far below what any
payout needs (`PROJECT_BRIEF.md` §2). By the data processing inequality, any predictor built
from price history is bounded by that. Don't propose one.

Both real edges this project found came from a mismatch between Deriv's pricing and the
generator's microstructure, not from prediction:

1. JD100 digits: a fixed payout grid went stale as volatility drag lowered σ in pips.
2. The ACCU phase lattice: survival depends on where the barrier falls on the integer
   pip lattice. Deriv then closed it on the terms logged-in accounts get.

Both live at a boundary. The discrete lattice meets a continuous pricer, or a payoff cap
meets a process that jumps. Every test below is of that kind.

## Rules for every test

- **Read-only.** No orders. Quotes come from `DerivWS(token=os.environ['DERIV_TOKEN'],
  account_type='demo')`. Log the public quote next to it as a diagnostic only.
- **Pre-register.** Write the kill and pass criteria into a `prereg_<test>.json` and
  commit it before pulling the data that decides it. Never change thresholds after seeing
  results. If a rule turns out wrong, report both the original and the revised verdict.
- **Controls.** Every scan needs a known-answer control, plus a placebo that gives zero
  by construction.
- **Independence.** Use non-overlapping windows for standard errors. Report 99% intervals.
- **Payouts and terms come from authenticated quotes, or from contracts already executed
  in the account's history.** The proposal endpoint has produced six false positives
  (`PROJECT_BRIEF.md` §6).

---

## T1. The multiplier gap put on Boom/Crash (MULTUP CRASH, MULTDOWN BOOM)

**Physics.** Measured on the local data (86k ticks per symbol), the steps between spikes on
Boom/Crash are **one-signed**:

| symbol | normal step >0 | <0 | =0 | spikes | median / 90th pct / max \|spike\| |
|---|---:|---:|---:|---:|---|
| CRASH1000 | 0.9492 | 0.0000 | 0.0508 | 105 | 0.0010 / 0.0021 / 0.0039 |
| CRASH500 | 0.9516 | 0.0001 | 0.0484 | 159 | 0.0010 / 0.0021 / 0.0036 |
| BOOM1000 | 0.0000 | 0.9796 | 0.0204 | 78 | 0.0009 / 0.0020 / 0.0029 |
| BOOM500 | 0.0001 | 0.9738 | 0.0261 | 150 | 0.0009 / 0.0018 / 0.0035 |

So a long multiplier on CRASH (or short on BOOM) never moves against you between spikes.
Only a spike can stop it out. The stop-out caps your loss at the stake, so when a spike of
relative size S exceeds 1/m, Deriv absorbs the excess. That is a **gap put the house
writes**, worth (m·S − 1)⁺ stakes per spike.

**Why this is not already closed.** `mult_stopout.py` gave up on this because it assumed
"a short can be stopped out by ordinary upward noise long before any spike." The table
above shows that premise is false. The closing verdict in `SESSION_2026-08-04.md` ("paired
long+short pays exactly −2·commission") holds only while both legs are open and the price
moves continuously. A gap is exactly where that identity breaks. The single-leg gap value
was never measured correctly.

**Model.** Hold one position from entry to the spike (one cycle). In stake units:

```
EV_cycle = E[(m·S − 1)⁺]  −  c·m  +  m · E[T] · μ_fav
```

- `c` is the commission as a fraction of notional.
- `E[T]` is the expected hold in ticks, about N.
- `μ_fav` is the generator's mean relative step per tick, all ticks, signed in your favour.
  It is zero if the index is a martingale.

Break-even commission from the local spikes, c* = E[(m·S−1)⁺]/m (ignoring μ):

| m | CRASH1000 | CRASH500 | BOOM1000 | BOOM500 |
|---:|---:|---:|---:|---:|
| 400 | 0.0024% | 0.0014% | 0.0015% | 0.0013% |
| 500 | 0.0059% | 0.0048% | 0.0046% | 0.0035% |
| 750 | 0.0204% | 0.0185% | 0.0200% | 0.0135% |
| 1000 | 0.0348% | 0.0328% | 0.0337% | 0.0246% |
| 2000 | 0.0654% | 0.0646% | 0.0629% | 0.0525% |

**The term that decides it is μ.** On 86k ticks, the CRASH1000 drift is −3.05e-7 per tick
(−1.9 SE). At m = 1000 and a hold of about 1000 ticks, that alone costs about 0.3 stakes
per cycle, the same size as the gift. The drift has to be pinned to about 1e-8 per tick,
which needs roughly 10–20M ticks per symbol (use `derivfetch.py`).

**Protocol (read-only):**
1. Get authenticated MULTUP/MULTDOWN proposals on CRASH300N/500/600/900/1000 and the
   BOOM mirrors. Record:
   - the offered multiplier ladder;
   - `commission`;
   - `limit_order.stop_out` (earlier seen as −9 on a $10 stake);
   - whether deal cancellation is forced on.
2. Read the rules for settling at stop-out. Is the loss capped at exactly the stake when
   the price gaps through? Check the documentation and any past multiplier contracts in the
   demo `profit_table`. If the cap does not hold on gaps, T1 is dead; stop here.
3. Fetch at least 10M ticks per symbol. Estimate μ with a block bootstrap. Measure the
   spike-size distribution, and check whether it depends on spot or on time since the last
   spike.
4. Replay on the ticks: enter at random, hold to the spike, close by stop-out. Compare the
   replay with the formula (the known-answer control). The placebo is the wrong-direction
   position, which must come out at about −c·m minus the gift.

**Kill:** at every offered m, the upper 99% bound of `gift − c·m + m·E[T]·μ` is ≤ 0.
**Pass (for fresh data):** the lower 99% bound is > 0 on at least one offered m, both on
the design data and on a later block it never saw.
**Prior: about 10%.** Deriv probably caps m below about 500 on these symbols, which makes
the gift about zero. Only an authenticated proposal can settle it, and that takes minutes.

---

## T2. Universal ACCU phase map and the re-tuning lag

**Physics.** An ACCU survives a tick only if |ΔP| < w, where w = b·spot/pip. With integer
pip steps, survival is a step function of ⌈w⌉. The per-tick value is `G = (1+g)·P(stay)`,
and it jumps by about 2·pmf(⌊w⌋) as w crosses an integer. At σ ≈ 13 pips (1HZ100V) and
g = 5%, that jump is about 1%, larger than the 0.6% house margin.

Because b is a fixed fraction and spot drifts, **every cell sweeps through phase over
time**. Deriv tightened 45 of 85 cells after 12 Sep, so a correction process exists. The
questions are whether it follows spot continuously, and how fast.

**Deliverable 1, the phase map.** For every ACCU symbol (all 19 in `accu_audit.py`, plus
any JD symbol where `contracts_for` lists accumulators) and g = 1–5%:
1. Get σ in pips. Cells with σ above about 40 pips have negligible lattice effect; list them
   and skip them.
2. For the rest, use the **current authenticated b**. Compute the set of spot intervals in
   which model G exceeds 1.001. Use the empirical integer-step pmf from recent ticks, not a
   Gaussian.
3. Print each interval next to the current spot, as a distance.

Known entries:
- BOOM300N: K2–K4, at spot about 170–211 (spot is about 400 now).
- CRASH1000 5%: K7, not reached.

Once the map exists, a new window can be predicted from spot alone.

**Deliverable 2, the lag.** Run `accu_phase_watch.py --hours 0` (authenticated) for 14 days.
Log the authenticated b per cell every hour. Pre-register "window open": spot inside a
mapped interval, with G_auth > 1.001, for at least 30 minutes. For each opening, record:
- how long it stays open;
- whether b was re-tuned, and how long after entry;
- the realised in-band survival on those ticks, which is fresh data.

**Kill:** after 14 days, no window is open for 30 minutes or more, or every opening is
re-tuned within 30 minutes.
**Pass:** a window open for 30 minutes or more whose fresh in-band ticks clear the existing
`accu_phase_decide.py` PASS-B rules unchanged.
**Prior: about 20%.** The tightening proves Deriv monitors these cells. Whether monitoring
keeps pace with spot drift is untested.

---

## T3. Are the authenticated terms client-specific?

**Why it matters.** The demo run on 12 Sep filled at the public barrier (2.3454e-6).
Afterwards, both of this user's accounts (demo and real) are quoted tighter. There are two
readings:
- (a) Deriv tightened the market for all logged-in clients;
- (b) this client was risk-profiled after ACCU activity.

These have different consequences. Under (b), every future finding has to be validated on
the account that will trade, and the account's terms are themselves a monitored variable.

**Protocol (quotes only).** For CRASH1000 4%, CRASH500 4%, BOOM300N 3%, 1HZ100V 5%, and a
control cell that was not tightened (BOOM1000 4%), record the barrier under each of:
1. public;
2. current demo token;
3. current real token;
4. stakes of 1, 10, 100 and 1000;
5. two different `app_id`s;
6. a demo account belonging to another person, if a collaborator is willing to run the
   quote script with their own token.

**Readout:** a table of barrier by condition.
- (a) holds if all authenticated rows are equal.
- (b) holds if another person's account matches the public barrier.
- Stake dependence is a third, separate mechanism.

**Limit:** do **not** open extra accounts to get looser terms. That breaks Deriv's terms
of service, and it does not produce a tradable edge, because accounts get profiled and
withdrawals get held. This is a diagnostic only.

**Prior that (b) holds: about 30%.**

---

## T4. Step Index barrier-offset parity

**Physics.** Step indices move exactly ±1 pip per tick. After k intervals, the displacement
has the same parity as k. So a barrier offset B pips from entry is either:
- **unreachable** (B has the wrong parity): P(S > B) = P(S ≥ B+1), and there is no tie; or
- **reachable**: its tie mass, C(k,(k+B)/2)/2^k, goes to the house under a
  strict-inequality rule.

A pricer that treats the barrier as continuous gets these wrong, by up to about 2× at small
k. Example: at k = 5 and B = +2.9 pips (if sub-pip offsets are allowed), the true
P(higher) is 6/32 = 0.1875, while a Gaussian model gives about 0.097.

**What was covered.** `step_parity_scan.py` and `step_units_scan.py` showed Deriv prices
the exact binomial **at barrier = entry** (rise/fall ties). Barrier offsets were never
scanned.

**Protocol (proposals only):**
1. `contracts_for` on stpRNG to stpRNG5. Is `higherlower` (CALL/PUT with a barrier) offered?
   What offsets and durations are allowed? If it isn't offered, T4 is closed in 5 minutes.
2. If it is offered, quote every allowed offset, from 1 to 20 pips and including any
   sub-pip ones, on both sides, at tick durations 5–10 and second durations 5–30. Map
   seconds to intervals using the D−1 rule.
3. Compute the exact binomial probability using the strict rule. The known-answer control
   is the barrier-0 cells, which must reproduce `step_units_scan.py`.

**Kill:** `payout × P_exact ≤ 0.98` in every cell.
**Pass:** any cell with a ratio above 1.00. Then run a parity check: the ratio must
alternate with the parity of B relative to k, as the physics predicts. After that, run
execution-verified quotes (outside this brief's read-only scope, with the user's sign-off).
**Prior: about 5%.** They already compute binomials. But T4 is the cheapest test here, and
it is exactly the "different code path" boundary where the seconds off-by-one lived.

---

## Also keep running (already built)

`watchdog.py --loop 21600`. It flags new symbols, pip-size changes, σ below 5 on any
digit-offering symbol, and executed payouts above proposals. A new symbol launch is the
cleanest instance of the JD100 failure mode.

## What to report back

For each test, report:
- the pre-registration file hash;
- the exact authenticated quotes used, with timestamps;
- the verdict against the pre-registered rule, with 99% intervals;
- the control and placebo results;
- any rule you believe was wrong, and why. Report it; don't apply the fix silently.

Paste the raw table outputs, not summaries.

---

## Results so far (reported 2026-09-26, read-only, authenticated demo quotes)

- **T3: market-wide.** A second account matches this one on every cell. Barriers don't depend on stake at
  $1, $10 or $100 (CRASH1000 4% 2.28724e-6, BOOM300N 4% 1.9525167e-5; $1000 isn't offered). A second
  `app_id` was not tested. Validating on this account is enough.
- **T4: closed.** Step indices reject every barrier offset ("Invalid barrier"). Plain Rise already prices
  the exact parity binomial: payout × P is 0.977 at odd tick counts and 0.954–0.966 at even ones.
- **T1: CRASH500 killed, the rest parked.**
  - Maximum multipliers: 500 on CRASH1000 and BOOM1000, 400 on CRASH500 and BOOM500, 100 on the
    300N symbols.
  - CRASH500 ×400: EV per cycle −5.2%, 99% [−9.6%, −1.5%].
  - CRASH1000 ×500: EV per cycle −0.5%, 99% [−7.1%, +6.0%]. The gap put covers 41% of commission, so
    break-even needs an upward drift of about 5e-8 per tick. Measured: +3.4e-8, 99% [−8.8e-8, +1.5e-7].
  - The drift question rides along in T2 (pre-registered in `prereg_t2.json`); 14 days won't settle it.
- **T2: built.** Status below.

## T2: how to run it

Rules: `tools/accu_phase_data/prereg_t2.json`, committed before any map or watcher data.

```
cd fable-thoughts/tools
DERIV_TOKEN=<demo token> python3 accu_phase_map.py build            # about 20-40 min
DERIV_TOKEN=<demo token> python3 accu_phase_watch.py --map ../results/accu_phase_map_<UTC>/phase_map.json \
        --outdir ../results/t2_watch --hours 0                     # 14 days; restart-safe
python3 accu_phase_map.py evaluate --watch-dir ../results/t2_watch --map <same phase_map.json>
```

`build` gates each symbol in this order:
1. The ACCU contract is offered.
2. A step law exists for it.
3. The small-step σ is at most 40 pips.
4. OR-1 passes on the house's `ticks_stayed_in` lists.
5. Per cell, model misfit |z| ≤ 3.

For each armed cell, it prints the spot intervals where model G ≥ 1.001 at the authenticated b, and
the distance from the current spot.

The watcher:
- logs the prereg hash first, and refuses a map built on a different hash;
- quotes every 600 s, or every 60 s when a cell is within one level of a window;
- writes `WINDOW` open and close events;
- runs `evaluate` daily.

`evaluate` replays the quote log through the window rules, so window state is a pure function of
`quotes.jsonl`. It then applies the unchanged PASS-B rule to pooled fresh ticks inside qualifying
windows. Before day 14 the verdict reads INTERIM.

### T2 build, 2026-09-26 11:57 UTC (`accu_phase_map_20260926T115657Z`)

- **Hash.** The Windows checkout hashes `prereg_t2.json` as `9927fd03…`, the repo as `c6cedbfe…`. Only
  the line endings differ: the repo file with LF converted to CRLF hashes to exactly `9927fd03…`. The
  rules are identical. The watcher compares hashes on the same machine, so this is consistent.
- **65 armed cells**:
  - all standard Boom/Crash (500/600/900/1000);
  - CRASH300N and BOOM300N;
  - 1HZ10V, 1HZ100V and R_100.
- **Excluded**:
  - N=50/150N: σ above 40 pips and OR-1 fails, which agrees with the earlier refutation. Their
    authenticated barrier at 2–5% is 15–25× tighter than the public one.
  - Every other volatility index: σ from 72 to 81,000 pips.
  - JD10–JD100: ACCU isn't offered.
- **No armed cell is near a window.** The closest are:
  - BOOM300N 4%: 2.8 levels away, −36% spot (window at 256; spot 402);
  - CRASH1000/CRASH500 5%: about 4.7 levels away, −34% to −38%;
  - R_100 4%: −44%.
  - 1HZ100V 4–5%, the cells Deriv tightened, have no window anywhere in the 3× spot range.
- **Reachability in 14 days (information, not a rule change).** Standard Boom/Crash move about 1% a day
  (1 SD), so a −35% move is out of reach. The volatility indices need moves of 3 SD or more. BOOM300N
  has drifted from about 1004 in June to 402, and at that pace the 4% window is roughly seven weeks
  away. So the 14-day T2 verdict will almost certainly be KILL (no qualifying window), unless Deriv
  loosens a barrier. The watcher remaps immediately if that happens.
- **Misfit z on BOOM300N is about +2.0 at all five rates** (the same ticks, so not five independent
  results): the data survive slightly more than the model predicts. It is within the pre-registered |z| ≤ 3.
