"""
Conditional-signal analysis on real tick streams (Phase 1 Steps 3 & 4, 1C).
Tests every 'pattern' strategy the mission proposes by conditioning on recent
history and checking whether the next-digit distribution actually shifts.
High sample sizes; binomial tests vs the correct random baseline.

Note: these run on REAL ticks pulled live from the Deriv API. If a conditioning
signal does not exist in the real sequence, no live execution can manufacture
one -- so this is the most powerful possible test of these hypotheses.
"""
import os, glob, csv
import numpy as np
from scipy import stats

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "digits", "data")

def load(sym):
    rows = list(csv.DictReader(open(os.path.join(DATA, f"{sym}_ticks.csv"))))
    return np.array([int(r["last_digit"]) for r in rows], dtype=int)

def streak_test(d, k, over_is_ge=5):
    """After k consecutive 'over' (digit>=5) outcomes, is next 'under' (<5)?
    Baseline P(under)=0.5. Tests the gambler's-fallacy hypothesis."""
    over = (d >= over_is_ge).astype(int)  # 1=over,0=under
    nxt_under = []
    run = 0
    for i in range(len(over)-1):
        if over[i] == 1: run += 1
        else: run = 0
        if run >= k:
            nxt_under.append(1 if over[i+1] == 0 else 0)
    n = len(nxt_under); s = sum(nxt_under)
    if n < 20: return (n, None, None)
    p = s/n
    bt = stats.binomtest(s, n, 0.5)
    return (n, round(p,4), round(bt.pvalue,4))

def freq_window(d, w=20, thresh=0.6, over_is_ge=5):
    """If 'over' appeared > thresh in last w ticks, bet 'under' next. Accuracy?"""
    over = (d >= over_is_ge).astype(int)
    correct=0; total=0
    for i in range(w, len(over)-1):
        frac = over[i-w:i].mean()
        if frac > thresh:        # over-heavy -> predict under
            total += 1; correct += (over[i+1]==0)
        elif frac < 1-thresh:    # under-heavy -> predict over
            total += 1; correct += (over[i+1]==1)
    if total < 20: return (total, None, None)
    p = correct/total
    bt = stats.binomtest(correct, total, 0.5)
    return (total, round(p,4), round(bt.pvalue,4))

def overdue_match(d, look=50):
    """Lowest-frequency digit in last `look` ticks -> bet MATCH next. Acc vs 0.1."""
    correct=0; total=0
    for i in range(look, len(d)-1):
        window = d[i-look:i]
        counts = np.bincount(window, minlength=10)
        cold = int(np.argmin(counts))
        total += 1; correct += (d[i+1]==cold)
    p = correct/total
    bt = stats.binomtest(correct, total, 0.1)
    return (total, round(p,4), round(bt.pvalue,4))

def momentum_updown(d, k):
    """After k up-moves in the digit-as-proxy? Use price proxy: digit>prev.
    Simpler: treat tick direction via even-run alt; here we test digit repeat."""
    # momentum on 'over' side: after k overs, P(next over) vs 0.5
    over=(d>=5).astype(int); nxt=[]; run=0
    for i in range(len(over)-1):
        if over[i]==1: run+=1
        else: run=0
        if run>=k: nxt.append(over[i+1])
    n=len(nxt); s=sum(nxt)
    if n<20: return (n,None,None)
    return (n, round(s/n,4), round(stats.binomtest(s,n,0.5).pvalue,4))

SYMS = ["R_10","R_25","R_50","R_75","R_100","1HZ10V","1HZ25V","1HZ50V","1HZ75V","1HZ100V"]

print("=== STREAK / GAMBLER'S FALLACY: P(under | k consecutive overs), baseline 0.50 ===")
print(f"{'sym':<9}", *[f'k={k}'.center(20) for k in [3,5,7,10]])
for s in SYMS:
    d=load(s); cells=[]
    for k in [3,5,7,10]:
        n,p,pv=streak_test(d,k)
        cells.append(f"n={n} p={p} pv={pv}".ljust(20) if p else f"n={n} (low)".ljust(20))
    print(f"{s:<9}", *cells)

print("\n=== FREQUENCY-WINDOW (>60% in last 20 -> fade): accuracy vs 0.50 ===")
for s in SYMS:
    d=load(s); n,p,pv=freq_window(d)
    print(f"{s:<9} n={n:<6} acc={p} binom_p={pv}")

print("\n=== OVERDUE-DIGIT MATCH (coldest of last 50 -> MATCH): accuracy vs 0.10 ===")
for s in SYMS:
    d=load(s); n,p,pv=overdue_match(d)
    print(f"{s:<9} n={n:<6} acc={p} binom_p={pv}  (need >0.10 to beat, >0.141 to profit)")

print("\n=== MOMENTUM: P(over | k consecutive overs), baseline 0.50 ===")
print(f"{'sym':<9}", *[f'k={k}'.center(18) for k in [3,5,7]])
for s in SYMS:
    d=load(s); cells=[]
    for k in [3,5,7]:
        n,p,pv=momentum_updown(d,k)
        cells.append(f"n={n} p={p} pv={pv}".ljust(18) if p else f"n={n}(low)".ljust(18))
    print(f"{s:<9}", *cells)
