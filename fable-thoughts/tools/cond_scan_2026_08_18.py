import sys, math, json, numpy as np, os; sys.path.insert(0,'.')
GRID = json.load(open('/tmp/grid_audit.json'))
OUT = {}

def wilson_lo(k, n, z=2.576):
    if n == 0: return 0.0
    p = k/n; den = 1+z*z/n; ctr = p+z*z/(2*n)
    rad = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (ctr-rad)/den

def scan(sym):
    d = np.load(f'/tmp/ticks/{sym}.npz')
    ep, px, pip = d['ep'], d['px'], int(d['pip'])
    x = np.round(px*10**pip).astype(np.int64)
    iv = int(np.median(np.diff(ep)))
    contig = np.diff(ep) == iv
    s = np.diff(x)              # steps, aligned: s[i] from x[i] to x[i+1]
    valid = np.zeros(len(s), bool)
    # full validity: steps t-4..t and outcome tick t+1 all contiguous
    for j in range(-4, 2):
        # contig[i] valid for i in corresponding range
        pass
    # simpler: compute a global good index on tick axis
    good_tick = np.zeros(len(x), bool)
    good_tick[:-1] = contig
    # rolling sigma
    W = 1800
    abs_s = np.abs(s.astype(float))
    sq = s.astype(float)**2
    csq = np.concatenate([[0], np.cumsum(sq)])
    sig = np.full(len(s), np.nan)
    if len(s) > W:
        sig[W:] = np.sqrt((csq[W+1:len(s)+1] - csq[1:len(s)+1-W])/W)
    med = np.nanmedian(sig)
    lo_t, hi_t = np.nanpercentile(sig, [33.33, 66.67])
    # rolling range position
    RW = 1800
    rmax = np.full(len(x), np.nan); rmin = np.full(len(x), np.nan)
    # cheap rolling via pandas-free method on every 10th? use full stride with deque-free:
    import pandas as pd
    ser = pd.Series(x)
    rmax = ser.rolling(RW, min_periods=600).max().to_numpy()
    rmin = ser.rolling(RW, min_periods=600).min().to_numpy()

    g = GRID[sym]
    M_O4 = g.get('DIGITOVER4'); M_U5 = g.get('DIGITUNDER5'); M_EO = g.get('DIGITEVEN')
    M_M0 = g.get('DIGITMATCH0'); M_O3 = g.get('DIGITOVER3'); M_U6 = g.get('DIGITUNDER6')
    M_DIFF = None

    tests = []  # (name, n, k, p, ev, evlo, payout_key)
    def add(name, cond_mask, win_mask, payout):
        n = int(cond_mask.sum())
        if n < 200 or payout is None: return
        k = int((cond_mask & win_mask).sum())
        p = k/n
        ev = p*payout - 1
        evlo = wilson_lo(k, n)*payout - 1
        tests.append(dict(name=name, n=n, p=round(p,5), ev=round(ev,5), evlo=round(evlo,5)))

    # decision at tick t: features from x[..t], outcome at tick t+1
    N = len(x)
    t_idx = np.arange(5, N-1)
    ok = good_tick[t_idx-1] & good_tick[t_idx] & good_tick[t_idx+1]
    ti = t_idx[ok]
    s1 = s[ti-1]; s2 = s[ti-2]; s3 = s[ti-3]
    dcur = x[ti] % 10
    s_next = s[ti]          # step to settlement tick
    dnext = x[ti+1] % 10
    sgn_next = np.sign(s_next)
    up = sgn_next > 0; dn = sgn_next < 0; tie = sgn_next == 0
    o4 = dnext >= 5; u5 = dnext <= 4; even = (dnext % 2 == 0)
    match = dnext == dcur; o3 = dnext >= 4; u6 = dnext <= 5
    sigt = sig[ti-1]  # sigma known before tick t (uses steps < t)
    pos = (x[ti]-rmin[ti]) / np.maximum(rmax[ti]-rmin[ti], 1)

    def m(arr, op, val):
        if op=='<': return arr < val
        if op=='>': return arr > val
        if op=='>=': return arr >= val
        if op=='<=': return arr <= val
        if op=='==': return arr == val

    # controls (unconditional)
    add('CTRL P(up)', np.ones(len(ti),bool), up, M_O4)
    add('CTRL P(tie)', np.ones(len(ti),bool), tie, M_O4)
    add('CTRL OVER4', np.ones(len(ti),bool), o4, M_O4)
    add('CTRL EVEN', np.ones(len(ti),bool), even, M_EO)

    # lagged-sign momentum/reversal
    for L, arr in ((1,s1),(2,s2)):
        add(f's{L}>0 -> up', arr>0, up, M_O4)
        add(f's{L}>0 -> down', arr>0, dn, M_O4)
        add(f's{L}<0 -> down', arr<0, dn, M_O4)
        add(f's{L}<0 -> up', arr<0, up, M_O4)
    add('R2 same-sign -> trend', (s1>0)&(s2>0), up, M_O4)
    add('R2 same-sign- -> trend', (s1<0)&(s2<0), dn, M_O4)
    add('R3 -> trend', (s1>0)&(s2>0)&(s3>0), up, M_O4)

    # digit-conditioned
    for dd in range(10):
        md = dcur == dd
        add(f'd={dd} OVER4', md, o4, M_O4)
        add(f'd={dd} UNDER5', md, u5, M_U5)
        add(f'd={dd} MATCH', md, match, M_M0)
        add(f'd={dd} EVEN', md, even, M_EO)
        add(f'd={dd} OVER3', md, o3, M_O3)
        add(f'd={dd} UNDER6', md, u6, M_U6)

    # sigma regime x direction
    for lbl, ms in (('sigLo', sigt<lo_t), ('sigMid', (sigt>=lo_t)&(sigt<=hi_t)), ('sigHi', sigt>hi_t)):
        add(f'{lbl} d<=4 UNDER5', ms & (dcur<=4), u5, M_U5)
        add(f'{lbl} d>=5 OVER4', ms & (dcur>=5), o4, M_O4)
        add(f'{lbl} up', ms, up, M_O4)
        add(f'{lbl} tie', ms, tie, M_O4)
        add(f'{lbl} EVEN', ms, even, M_EO)

    # range position mean reversion
    for lbl, mp in (('posLo', pos<0.2), ('posHi', pos>0.8)):
        add(f'{lbl} meanrev', mp, (dn if lbl=='posHi' else up), M_O4)

    return tests, len(ti), dict(med_sigma=float(med), lo=float(lo_t), hi=float(hi_t))

ALL = {}
for f in sorted(os.listdir('/tmp/ticks')):
    sym = f[:-4]
    if sym in ('BOOM500','BOOM1000','CRASH500','CRASH1000'): continue
    if sym not in GRID: continue
    try:
        tests, nti, meta = scan(sym)
        ALL[sym] = tests
        pos = [t for t in tests if t['evlo'] > 0]
        print(f"{sym:9s} n={nti:7d} tests={len(tests)} candidates(Wilson99LB>0): {len(pos)}")
        for t in sorted(pos, key=lambda t:-t['evlo'])[:6]:
            print(f"   {t['name']:22s} n={t['n']:7d} p={t['p']:.4f} EV={t['ev']:+.3%} lo={t['evlo']:+.3%}")
    except Exception as e:
        print(f"{sym}: ERR {e}")
json.dump(ALL, open('/tmp/cond_scan.json','w'))
