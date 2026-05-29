"""
Risk-of-ruin Monte Carlo for the "guaranteed $1/day" dream.

The ONLY way to "win almost every day" on a negative-EV game is a martingale:
after a loss, raise the stake enough to recover everything plus the daily
target on the next win. This wins on the vast majority of days -- which is
exactly why it fools people -- but each session risks the whole account, and
the expected value stays negative. Over many days, ruin is not a tail risk;
it is the destination.

We simulate this honestly using the REAL contract probabilities and payouts
verified from Deriv tick data, and report:
  * P(complete a +$target day)         (looks amazing)
  * P(account ruined within a week/month)
  * Expected profit per day and per week (the truth)
  * The full outcome distribution (median vs mean -- the skew that deceives)
"""
import numpy as np

RNG = np.random.default_rng(20260529)

def simulate_target_martingale(p_win, payout, start=10.0, target=1.0,
                               min_stake=0.35, days=30, n_paths=200_000):
    """
    Each 'day' = one martingale cycle aiming to net +target, then stop for the day.
    Within a day: stake s = (invested_so_far + target)/(payout-1) so that a single
    win recovers all losses plus the target. If the required stake exceeds the
    bankroll, the day is a blow-up: the account is wiped (cannot fund recovery).
    Returns arrays of final bankroll and the day index of ruin (or -1).
    """
    net = payout - 1.0
    bankroll = np.full(n_paths, start, dtype=float)
    alive = np.ones(n_paths, dtype=bool)
    ruin_day = np.full(n_paths, -1, dtype=int)
    days_won = np.zeros(n_paths, dtype=int)
    for day in range(days):
        invested = np.zeros(n_paths, dtype=float)
        day_done = ~alive  # dead paths skip
        # simulate the within-day martingale, capped at a generous # of steps
        for step in range(60):
            need = (invested + target) / net
            need = np.maximum(need, min_stake)
            # can we afford the next stake?
            cant = alive & ~day_done & (need > bankroll + 1e-9)
            # those that can't fund -> ruin today
            if np.any(cant):
                bankroll[cant] = 0.0
                alive[cant] = False
                ruin_day[cant] = np.where(ruin_day[cant] == -1, day, ruin_day[cant])
                day_done[cant] = True
            active = alive & ~day_done
            if not np.any(active):
                break
            s = np.where(active, need, 0.0)
            wins = (RNG.random(n_paths) < p_win) & active
            losses = active & ~wins
            # apply: lose stake (and keep going) or win (recover+target, stop day)
            bankroll[losses] -= s[losses]
            invested[losses] += s[losses]
            # winners: net change over the whole day is +target
            bankroll[wins] += s[wins] * net - invested[wins]  # = target (by construction)
            days_won[wins] += 1
            day_done[wins] = True
            if np.all(day_done | ~alive):
                break
    return bankroll, ruin_day, days_won

def report(label, p_win, payout, **kw):
    days = kw.get("days", 30)
    start = kw.get("start", 10.0)
    target = kw.get("target", 1.0)
    bankroll, ruin_day, days_won = simulate_target_martingale(p_win, payout, **kw)
    n = len(bankroll)
    ruined = ruin_day >= 0
    p_ruin_week = np.mean((ruin_day >= 0) & (ruin_day < 7))
    p_ruin_month = np.mean(ruined)
    # per-day success probability (fraction of attempted days that were won, among first survival)
    mean_final = bankroll.mean()
    median_final = np.median(bankroll)
    ev_total = mean_final - start
    print(f"\n### {label}")
    print(f"    contract win prob {p_win:.3f}, payout {payout:.3f}, start ${start:.2f}, target ${target:.2f}/day, horizon {days} days")
    print(f"    P(win any single day)         ~ {1 - p_lose_day(p_win, payout, start, target):.4f}")
    print(f"    P(RUIN within 7 days)         = {p_ruin_week*100:6.2f}%")
    print(f"    P(RUIN within {days:>2} days)        = {p_ruin_month*100:6.2f}%")
    print(f"    median final bankroll         = ${median_final:7.2f}   (the 'it works!' illusion)")
    print(f"    MEAN  final bankroll          = ${mean_final:7.2f}   (the truth: EV)")
    print(f"    expected P/L over {days:>2} days     = ${ev_total:+7.2f}")
    return dict(label=label, p_ruin_week=p_ruin_week, p_ruin_month=p_ruin_month,
                median_final=median_final, mean_final=mean_final, ev_total=ev_total)

def p_lose_day(p_win, payout, start, target, min_stake=0.35):
    """Analytic-ish: probability a day blows up = probability of a losing streak long
    enough that the next required stake exceeds the bankroll."""
    net = payout - 1.0
    invested = 0.0
    bankroll = start
    streak = 0
    while True:
        need = max((invested + target) / net, min_stake)
        if need > bankroll + 1e-9:
            break
        bankroll -= need
        invested += need
        streak += 1
        if streak > 100:
            break
    return p_win ** 0 * (1 - p_win) ** streak  # P(streak consecutive losses)

if __name__ == "__main__":
    import io, os
    buf = io.StringIO()
    import sys as _sys
    class _Tee:
        def __init__(self, *streams): self.streams = streams
        def write(self, d):
            for s in self.streams: s.write(d)
        def flush(self):
            for s in self.streams: s.flush()
    _orig = _sys.stdout
    _sys.stdout = _Tee(_orig, buf)
    print("=" * 90)
    print("RISK OF RUIN — the 'guaranteed $1/day' martingale, simulated honestly")
    print("=" * 90)
    rows = []
    # Even/Odd or Rise/Fall: ~50% win, payout ~1.95
    rows.append(report("Even/Odd martingale, $1/day, $10 start (1 week)", 0.50, 1.95, days=7, target=1.0))
    rows.append(report("Even/Odd martingale, $1/day, $10 start (1 month)", 0.50, 1.95, days=30, target=1.0))
    # Rise/Fall strict (loses on ties): empirical p_win ~0.487
    rows.append(report("Rise/Fall martingale, $1/day, $10 start (1 month)", 0.487, 1.92, days=30, target=1.0))
    # The 'safe' 90% contract: payout 1.09 -> recovery stake explodes (need 11x the loss)
    rows.append(report("Differs(90%) martingale, $0.50/day, $10 start (1 month)", 0.90, 1.09, days=30, target=0.50))
    # Bigger bankroll, same dream
    rows.append(report("Even/Odd martingale, $1/day, $50 start (1 month)", 0.50, 1.95, days=30, target=1.0, start=50.0))
    print("\n" + "=" * 90)
    print("TAKEAWAY: high P(win a day) + negative mean = the martingale illusion.")
    print("You bank dollars for a while, then one losing streak returns all of them plus your stake.")
    print("=" * 90)
    _sys.stdout = _orig
    rp = os.path.join(os.path.dirname(__file__), "..", "..", "results", "risk_of_ruin.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    with open(rp, "w") as f:
        f.write(buf.getvalue())
