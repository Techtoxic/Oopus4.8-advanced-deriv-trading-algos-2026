"""Quantify the midnight reset snipe: P(00:00:02 tick same side of 1000 as 23:59:5x entry)
on historical RDBULL/RDBEAR ticks."""
import sys, time, numpy as np; sys.path.insert(0,'.')
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval
ws = DerivWS(token='')
for SYM in ["RDBULL","RDBEAR"]:
    t, p, pip = fetch_ticks(ws, SYM, 400000)
    v = np.round(p*10**pip).astype(np.int64)
    iv = native_interval(t)
    sod = t % 86400
    # find reset ticks: where sod wraps (00:00:00) -> value should be 1000*10^pip
    wraps = np.where(np.diff(sod) < 0)[0] + 1
    print(f"\n{SYM}: {len(wraps)} midnight resets in {(t[-1]-t[0])/86400:.1f} days, interval {iv}s")
    resets = [i for i in wraps if t[i] % 86400 == 0]
    print(f"  reset tick at exactly 00:00:00: {len(resets)}/{len(wraps)}")
    for i in wraps:
        if p[i] != 1000.0:
            print(f"  *** NON-1000 reset at {t[i]}: {p[i]}")
    wins_c = wins_p = 0; margin = []
    for i in wraps:
        j = i + 1  # 00:00:02 tick
        if j >= len(p) or t[j] != t[i] + 2: continue
        for k in range(2, 8):  # ticks at 23:59:54/56/58 (iv=2 -> 3 ticks before)
            e = i - k
            if e < 0 or t[e] != t[i] - k*iv: continue
            if v[e] > v[j]: wins_p += 1
            if v[e] < v[j]: wins_c += 1
            margin.append(abs(v[j]-v[e])/10**pip)
    n = wins_c + wins_p
    print(f"  PUT/CALL settles at 00:00:02 vs entry at 23:59:5x: same-side {n} / {len(wraps)*3 if n else '?'} "
          f"({n/(3*len(wraps))*100 if len(wraps) else 0:.1f}%)")
    if margin:
        print(f"  median |settle-entry|: {np.median(margin):.3f}, min {min(margin):.3f}")
    # digit of reset tick: always 0?
    digs = [int(np.round(p[i]*10**pip))%10 for i in wraps]
    print(f"  reset-tick digit distribution: {np.bincount(digs, minlength=10)}")
    time.sleep(2)
