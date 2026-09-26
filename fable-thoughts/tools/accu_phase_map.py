#!/usr/bin/env python3
"""accu_phase_map.py -- T2 (NEXT_TESTS_2026-09-26.md): universal ACCU phase map and re-tuning lag.

THE IDEA
An accumulator survives a tick while |move| < w, with w = b*spot/pip, and the move is a whole number of
pips. So survival is a step function of the level K = floor(w), and G = (1+g)*P(stay) jumps each time w
crosses an integer. b is a fixed fraction of spot, so as spot drifts every cell sweeps through levels.
Deriv tightened the authenticated barrier on 45 of 85 cells after 12 Sep, so it corrects these cells.
The question T2 answers: does the correction keep pace with spot, or do windows with G > 1 open for
long enough to be real?

Every rule is in accu_phase_data/prereg_t2.json, committed before any map or watcher data. This tool
records that file's sha256 in the map, and accu_phase_watch.py refuses a map built on another hash.

SUBCOMMANDS
  build     read-only, network. For every candidate symbol: authenticated (demo) ACCU quotes at g = 1-5%
            (public ones alongside, for the oracle), recent public ticks, then the gates -- ACCU offered,
            step law known, sigma_pips <= 40, OR-1 on the house's ticks_stayed_in lists, model misfit
            |z| <= 3 -- and, per armed cell, the spot intervals where model G >= 1.001 at the current
            authenticated b, with the distance from spot to the nearest one -> phase_map.json
  evaluate  offline. Replays the watcher's quotes.jsonl through the window rules, pools the fresh
            archived ticks inside qualifying windows (>= 30 min open) and applies the pre-registered
            verdict; also the carried CRASH1000 drift question from T1 -> t2_eval.json, t2_summary.txt

RUN (from fable-thoughts/tools; needs websocket-client>=1.6, numpy, scipy)
  DERIV_TOKEN=<demo token> python3 accu_phase_map.py build
  DERIV_TOKEN=<demo token> python3 accu_phase_watch.py --map ../results/accu_phase_map_<UTC>/phase_map.json \\
          --outdir ../results/t2_watch --hours 0
  python3 accu_phase_map.py evaluate --watch-dir ../results/t2_watch --map <same phase_map.json>
No order is sent anywhere; the token opens a demo account for proposals only.
"""
import argparse, bisect, datetime as dt, hashlib, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
from scipy import stats
import accu_phase_lib as lib

PREREG_T2 = os.path.join(lib.DATA_DIR, 'prereg_t2.json')
UTC = dt.timezone.utc
VOL_ALL = ('1HZ10V', '1HZ15V', '1HZ25V', '1HZ30V', '1HZ50V', '1HZ75V', '1HZ90V', '1HZ100V',
           'R_10', 'R_25', 'R_50', 'R_75', 'R_100')
JD_ALL = ('JD10', 'JD25', 'JD50', 'JD75', 'JD100')
CANDIDATES = lib.BC + VOL_ALL + JD_ALL
BUILD_TICKS = 20000
E32 = (np.arange(32) + 0.5) / 32 - 0.5          # rounding offset of the previous true price, U(-.5, .5)


def prereg_t2():
    with open(PREREG_T2, 'rb') as fh:
        raw = fh.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def utc_tag():
    return dt.datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')


def step_kind(sym):
    if sym.startswith(('CRASH', 'BOOM')):
        return 'bc'
    if sym.startswith(('R_', '1HZ')):
        return 'vol'
    return None


# ------------------------------------------------------------------ models: G as a function of (K, phase)
def vol_sigma_rel(sym):
    """Nominal per-tick relative sigma of a volatility index (sigma_ann / sqrt(365*86400/dt))."""
    return lib.vol_sigma_pips(sym, 1.0, 0)


def vol_pstay(K, ph, sigma_rel, b):
    """P(stay) on a vol index: D = round(e + X), e ~ U(-.5, .5), X ~ N(0, s) with s = sigma_rel * spot/pip
    = sigma_rel * w / b; for phase > 0 the rule |D| < w means |D| <= K."""
    K = np.asarray(K, float)
    w = K + np.asarray(ph, float)
    s = sigma_rel * w / b
    hi = (K + 0.5 - E32[:, None]) / s
    lo = (-K - 0.5 - E32[:, None]) / s
    return (stats.norm.cdf(hi) - stats.norm.cdf(lo)).mean(0)


def pstay_model(model, b, K, ph, how='min', law=None):
    """Model P(stay | K, phase) for a cell. Boom/Crash: frozen law with kappa = b/m, min (or mean) over the
    V1 and V2 kernels; vol: vol_pstay."""
    K = np.atleast_1d(np.asarray(K, float))
    ph = np.atleast_1d(np.asarray(ph, float))
    if model['kind'] == 'vol':
        return vol_pstay(K, ph, model['sigma_rel'], b)
    law = law or LAW()
    kappa = b / model['m']
    v1 = lib.pstay(K, ph, kappa, model['lam'], law, 'V1')
    v2 = lib.pstay(K, ph, kappa, model['lam'], law, 'V2')
    return np.minimum(v1, v2) if how == 'min' else 0.5 * (v1 + v2)


_LAW = None


def LAW():
    global _LAW
    if _LAW is None:
        _LAW = lib.load_frozen()
    return _LAW


def cell_G(model, g, b, K, ph):
    return (1 + g) * pstay_model(model, b, K, ph)


# ------------------------------------------------------------------ windows
def windows(model, g, b, pipdec, spot_lo, spot_hi, thr, grid=64):
    """Maximal runs of w-grid points (phase step 1/grid) with model G >= thr, as w and spot intervals."""
    scale = b * 10.0 ** pipdec                   # w = scale * spot
    k0, k1 = max(1, math.floor(scale * spot_lo)), max(1, math.floor(scale * spot_hi))
    Ks = np.repeat(np.arange(k0, k1 + 1), grid)
    js = np.tile(np.arange(grid), k1 - k0 + 1)
    G = cell_G(model, g, b, Ks, (js + 0.5) / grid)
    ok = G >= thr
    out = []
    i, n = 0, len(ok)
    while i < n:
        if not ok[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and ok[j + 1]:
            j += 1
        w_lo, w_hi = Ks[i] + js[i] / grid, Ks[j] + (js[j] + 1) / grid
        out.append({'K': int(Ks[i]), 'w_lo': float(w_lo), 'w_hi': float(w_hi),
                    'spot_lo': float(w_lo / scale), 'spot_hi': float(w_hi / scale),
                    'G_max': float(G[i:j + 1].max()), 'G_mean': float(G[i:j + 1].mean())})
        i = j + 1
    return out


def w_of(b, spot, pipdec):
    return b * float(spot) * 10.0 ** pipdec


def in_window(wins, w):
    for win in wins:
        if win['w_lo'] <= w < win['w_hi']:
            return win
    return None


def w_distance(wins, w):
    """Distance in levels (w units; one level = pip/b in spot) from w to the nearest window, 0 inside."""
    if not wins:
        return None
    return min(0.0 if x['w_lo'] <= w < x['w_hi'] else min(abs(w - x['w_lo']), abs(w - x['w_hi'])) for x in wins)


def nearest(wins, spot, b, pipdec):
    if not wins:
        return None
    w = w_of(b, spot, pipdec)
    best = min(wins, key=lambda x: 0.0 if x['w_lo'] <= w < x['w_hi'] else min(abs(w - x['w_lo']), abs(w - x['w_hi'])))
    target = best['spot_lo'] if spot < best['spot_lo'] else best['spot_hi'] if spot >= best['spot_hi'] else spot
    return {'window': best, 'distance_levels': w_distance([best], w), 'distance_pct': 100 * (target / spot - 1)}


# ------------------------------------------------------------------ gates
def oracle_gate(records, min_rates, min_runs=None):
    """OR-1 per symbol: every primary record with >= 50 covered runs must match all completed house runs
    under raw_incl; at least min_rates such records."""
    min_runs = min_runs or lib.THRESH['OR1_min_runs']
    q = [r for r in records if r.get('rules') and r.get('mode') == 'primary' and (r.get('covered') or 0) >= min_runs]
    fails = [r['g'] for r in q if not r['rules']['raw_incl'].get('completed_full', r['rules']['raw_incl']['full'])]
    ok = len(q) >= min_rates and not fails
    return {'ok': bool(ok), 'qualifying': len(q), 'failed_rates': fails,
            'reason': None if ok else (f'OR-1 failed at g={fails}' if fails else f'only {len(q)} qualifying rates')}


def small_sigma_pips(S):
    d = S['D'][S['small']]
    return float(np.std(d)) if len(d) > 1 else None


def misfit(model, g, b, S, zmax):
    """Empirical survival at the authenticated b on recent contiguous ticks vs the model (mean kernel)."""
    ok = S['ok']
    Pp, Pn = S['P'][:-1][ok], S['P'][1:][ok]
    if len(Pp) < 1000:
        return {'ok': False, 'n': int(len(Pp)), 'reason': 'fewer than 1000 contiguous ticks'}
    kn = lib.knockout(Pp, Pn, b)
    emp = float(kn['stay'].mean())
    inv, Kc, phc = lib.sub_bins(kn['K'], kn['ph'])
    mod = float(pstay_model(model, b, Kc, phc, how='mean')[inv].mean())
    se = math.sqrt(max(mod * (1 - mod), 1e-12) / len(Pp))
    z = (emp - mod) / se
    return {'ok': bool(abs(z) <= zmax), 'n': int(len(Pp)), 'empirical': emp, 'model': mod, 'z': float(z),
            'reason': None if abs(z) <= zmax else f'misfit z = {z:+.2f}'}


# ------------------------------------------------------------------ build (network, read-only)
def build(args):
    import accu_phase_decide as apd
    from derivfetch import fetch_ticks
    pr, sha = prereg_t2()
    gates, mp_ = pr['gates'], pr['map']
    outdir = args.outdir or os.path.normpath(os.path.join(HERE, '..', 'results', f'accu_phase_map_{utc_tag()}'))
    os.makedirs(outdir, exist_ok=True)
    apd.need_token()
    syms = args.symbols or list(CANDIDATES)
    print(f'== T2 phase map: {len(syms)} candidate symbols; prereg_t2.json sha256 {sha}')
    out = {'utc': utc_tag(), 'prereg_sha256': sha, 'terms': 'authenticated', 'symbols': {}}
    for sym in syms:
        ws = wa = None
        rec = {'sym': sym}
        out['symbols'][sym] = rec
        try:
            ws, wa = apd.new_ws(), apd.auth_ws()
            qa, qp = {}, {}
            for g in lib.RATES:
                q = apd.quote(wa, sym, g)
                time.sleep(apd.PAUSE_PROPOSAL)
                if q and q.get('b') is not None:
                    qa[g] = q
                    p = apd.quote(ws, sym, g)
                    time.sleep(apd.PAUSE_PROPOSAL)
                    if p and p.get('b') is not None:
                        qp[g] = p
            if not qa:
                rec.update(offered=False, armed=False, reason='no authenticated ACCU quote (not offered)')
                print(f'  {sym:10s} not offered')
                continue
            rec['offered'] = True
            pip = apd.get_pip(ws, sym)
            ep, px, pip2 = fetch_ticks(ws, sym, args.ticks, verbose=False, strict=False)
            pip = pip if pip is not None else pip2
            rec['pip'] = pip
            S = lib.prep_series(sym, ep, px, pip)
            spot = float(qa[lib.RATES[0]]['spot']) if qa.get(lib.RATES[0]) else float(next(iter(qa.values()))['spot'])
            rec.update(spot=spot, n_ticks=int(len(ep)))
            # oracle (public b with the public ticks_stayed_in list)
            P = lib.to_pips(px, pip)
            orecs = []
            for g, p in qp.items():
                if p.get('ticks_stayed_in') is None:
                    continue
                r = lib.oracle_align(ep, P, p['b'], p['ticks_stayed_in'], p['last_tick_epoch'])
                r['g'] = g
                orecs.append(r)
            og = oracle_gate(orecs, gates['oracle_min_rates'])
            rec['oracle'] = og | {'per_rate': {str(r['g']): {'mode': r.get('mode'), 'covered': r.get('covered'),
                                                          'completed_full': (r.get('rules') or {}).get('raw_incl', {}).get('completed_full')}
                                               for r in orecs}}
            # step law
            kind = step_kind(sym)
            if kind == 'bc':
                s1 = lib.step_law(S, LAW(), 200, 1)
                model = {'kind': 'bc', 'm': float(s1['model']['m']), 'lam': float(s1['model']['lam']),
                         'm_source': s1['model']['m_source'], 'lam_source': s1['model']['lam_source']}
                sig = small_sigma_pips(S)
            elif kind == 'vol':
                model = {'kind': 'vol', 'sigma_rel': vol_sigma_rel(sym)}
                sig = lib.vol_sigma_pips(sym, spot, pip)
                emp = small_sigma_pips(S)
                model['sigma_pips_measured'] = emp
            else:
                model, sig = None, small_sigma_pips(S)
            rec.update(model=model, sigma_pips=sig)
            base = None
            if model is None:
                base = 'no step law'
            elif sig is None or sig > gates['sigma_pips_max']:
                base = f"sigma_pips {sig if sig is None else round(sig, 2)} > {gates['sigma_pips_max']} (lattice negligible)"
            elif not og['ok']:
                base = og['reason']
            cells = {}
            for g, q in qa.items():
                b = q['b']
                c = {'b': b, 'b_repr': q.get('b_repr'), 'b_public': qp.get(g, {}).get('b'), 'spot': float(q['spot']),
                     'maximum_ticks': q.get('maximum_ticks')}
                reason = base
                if model is not None and sig is not None and sig <= gates['sigma_pips_max']:
                    c['misfit'] = misfit(model, g, b, S, gates['misfit_z_max'])
                    if reason is None and not c['misfit']['ok']:
                        reason = c['misfit']['reason']
                    w = w_of(b, c['spot'], pip)
                    K = math.floor(w)
                    c['now'] = {'w': w, 'K': K, 'phase': w - K, 'G': float(cell_G(model, g, b, [K], [w - K])[0])}
                    F = mp_['spot_range_factor']
                    c['windows'] = windows(model, g, b, pip, c['spot'] / F, c['spot'] * F, mp_['G_threshold'], mp_['phase_grid'])
                    c['nearest'] = nearest(c['windows'], c['spot'], b, pip)
                c['armed'] = reason is None
                c['reason'] = reason
                cells[str(g)] = c
            rec['cells'] = cells
            rec['armed'] = any(c['armed'] for c in cells.values())
            print_symbol(rec)
        except Exception as e:
            rec.update(armed=False, reason=f'{type(e).__name__}: {str(e)[:160]}')
            print(f'  {sym:10s} error: {rec["reason"]}')
        finally:
            for c in (ws, wa):
                if c is not None:
                    c.close()
            time.sleep(apd.PAUSE_SYMBOL)
    armed = [(s, g) for s, r in out['symbols'].items() for g, c in (r.get('cells') or {}).items() if c['armed']]
    out['armed_cells'] = [f'{s} {g}' for s, g in armed]
    path = os.path.join(outdir, 'phase_map.json')
    with open(path, 'x') as fh:
        json.dump(lib.jsonable(out), fh, indent=1)
    print(f'\n  {len(armed)} armed cells; wrote {path}')
    print('  next: DERIV_TOKEN=... python3 accu_phase_watch.py --map ' + path + ' --outdir ../results/t2_watch --hours 0')
    return out


def print_symbol(rec):
    o = rec.get('oracle') or {}
    head = (f"  {rec['sym']:10s} pip {rec.get('pip')}  spot {rec.get('spot')}  sigma_pips "
            f"{lib._f(rec.get('sigma_pips'), 2)}  OR-1 {'ok' if o.get('ok') else 'FAIL'} ({o.get('qualifying')} rates)")
    print(head)
    for g, c in (rec.get('cells') or {}).items():
        now, nr, mf = c.get('now') or {}, c.get('nearest'), c.get('misfit') or {}
        win = (f"nearest window K{nr['window']['K']} spot [{nr['window']['spot_lo']:.6g}, {nr['window']['spot_hi']:.6g}) "
               f"G_max {nr['window']['G_max']:.5f}, {nr['distance_levels']:.2f} levels / {nr['distance_pct']:+.1f}%"
               if nr else 'no window in range')
        print(f"      g={g}  b {c['b']:.7g} (pub {'n/a' if c.get('b_public') is None else format(c['b_public'], '.7g')})  K{now.get('K')}/{lib._f(now.get('phase'), 3)}"
              f"  G_now {lib._f(now.get('G'), 5)}  misfit z {lib._f(mf.get('z'), 2)}  {win}  "
              f"{'ARMED' if c['armed'] else 'excluded: ' + str(c['reason'])}")


# ------------------------------------------------------------------ window tracking (used by the watcher)
class WindowTracker:
    """Pre-registered window rules over authenticated quotes of the armed cells of a phase map."""

    def __init__(self, pmap, prereg=None):
        pr = prereg or prereg_t2()[0]
        self.thr = pr['map']['G_threshold']
        self.grid = pr['map']['phase_grid']
        self.factor = pr['map']['spot_range_factor']
        self.min_dwell = pr['window_rules']['min_dwell_s']
        self.cells = {}
        for sym, r in pmap['symbols'].items():
            for gs, c in (r.get('cells') or {}).items():
                if c.get('armed'):
                    self.cells[(sym, float(gs))] = {'model': r['model'], 'pip': int(r['pip']), 'spot0': c['spot']}
        self.cache = {}
        self.open = {}                            # key -> {'t_open', 'b_open', 'window', 'epoch_open'}

    def symbols(self):
        return list(dict.fromkeys(s for s, _ in self.cells))

    def rates(self, sym):
        return [g for s, g in self.cells if s == sym]

    def wins(self, key, b, spot):
        """Windows of cell `key` at barrier b over a spot range that covers `spot` (recomputed when b
        changes: the remap rule)."""
        c = self.cells[key]
        hit = self.cache.get((key, b))
        if hit is None or not (hit['lo'] <= spot <= hit['hi']):
            lo = min(c['spot0'], spot) / self.factor
            hi = max(c['spot0'], spot) * self.factor
            hit = {'lo': lo, 'hi': hi, 'w': windows(c['model'], key[1], b, c['pip'], lo, hi, self.thr, self.grid)}
            self.cache[(key, b)] = hit
        return hit['w']

    def inside(self, key, b, spot):
        return in_window(self.wins(key, b, spot), w_of(b, spot, self.cells[key]['pip']))

    def distance(self, key, b, spot):
        return w_distance(self.wins(key, b, spot), w_of(b, spot, self.cells[key]['pip']))

    def on_quote(self, sym, g, b, spot, t, epoch=None):
        """Feed one authenticated quote; returns WINDOW events (open / close) as dicts."""
        key = (sym, float(g))
        if key not in self.cells or b is None or spot is None:
            return []
        spot = float(spot)
        win = self.inside(key, b, spot)
        st = self.open.get(key)
        ev = []
        if st is None and win is not None:
            self.open[key] = {'t_open': t, 'epoch_open': epoch, 'b_open': b, 'window': win, 'b_seen': [b]}
            ev.append({'state': 'open', 'sym': sym, 'g': float(g), 'b': b, 'spot': spot, 'epoch': epoch,
                       'K': win['K'], 'spot_lo': win['spot_lo'], 'spot_hi': win['spot_hi'], 'G_max': win['G_max']})
        elif st is not None and win is None:
            retune = b != st['b_open'] and self.inside(key, st['b_open'], spot) is not None
            dwell = t - st['t_open']
            ev.append({'state': 'close', 'sym': sym, 'g': float(g), 'b': b, 'b_open': st['b_open'], 'spot': spot,
                       'epoch': epoch, 'epoch_open': st['epoch_open'], 't_open': st['t_open'], 'dwell_s': dwell,
                       'reason': 'retune' if retune else 'spot_exit', 'qualifying': bool(dwell >= self.min_dwell)})
            del self.open[key]
        elif st is not None and b not in st['b_seen']:
            st['b_seen'].append(b)
        return ev

    def near(self, sym, quotes):
        """True when any armed cell of sym is within one level of a window: quotes = {g: (b, spot)}."""
        for g, (b, spot) in quotes.items():
            key = (sym, float(g))
            if key in self.cells and b is not None and spot is not None:
                d = self.distance(key, b, float(spot))
                if d is not None and d <= 1.0:
                    return True
        return False


# ------------------------------------------------------------------ evaluate (offline)
def read_quotes(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            try:
                q = json.loads(line)
            except ValueError:
                continue
            if q.get('terms') == 'authenticated' and q.get('b') is not None and q.get('spot') is not None:
                rows.append(q)
    rows.sort(key=lambda q: q['t'])
    return rows


def replay(tracker, rows):
    """Window intervals from the quote log: closed ones, plus the ones still open at the last quote
    (censored; their dwell so far counts)."""
    out = []
    t_last = rows[-1]['t'] if rows else None
    for q in rows:
        for e in tracker.on_quote(q['sym'], q['g'], q['b'], q['spot'], q['t'], q.get('epoch')):
            if e['state'] == 'close':
                out.append(e | {'censored': False})
    for (sym, g), st in tracker.open.items():
        dwell = t_last - st['t_open']
        out.append({'state': 'open', 'sym': sym, 'g': g, 'b_open': st['b_open'], 't_open': st['t_open'],
                    'epoch_open': st['epoch_open'], 'dwell_s': dwell, 'reason': 'censored', 'censored': True,
                    'qualifying': bool(dwell >= tracker.min_dwell)})
    return out, t_last


def in_window_ticks(tracker, series, rows, intervals):
    """Fresh ticks inside qualifying windows: per tick the authenticated b in force (latest quote at or
    before it), in-window at that b, stay under raw_incl; one cell per (sym, tick): highest model G."""
    by_cell = {}
    for q in rows:
        by_cell.setdefault((q['sym'], float(q['g'])), []).append((int(q.get('epoch') or q['t']), q['b']))
    for v in by_cell.values():
        v.sort()
    prepped, best = {}, {}
    for iv in intervals:
        sym, g = iv['sym'], float(iv['g'])
        key = (sym, g)
        if not iv['qualifying'] or sym not in series or key not in by_cell:
            continue
        if sym not in prepped:
            ep, px, pip = series[sym]
            prepped[sym] = lib.prep_series(sym, ep, px, pip)
        S = prepped[sym]
        pip = S['pipdec']
        t0 = int(iv.get('epoch_open') or iv['t_open'])
        m = (S['ep'][:-1] >= t0) & S['ok']
        if not iv['censored'] and iv.get('epoch'):
            m &= S['ep'][:-1] < int(iv['epoch'])
        idx = np.flatnonzero(m)
        qe = np.array([e for e, _ in by_cell[key]], np.int64)
        qb = np.array([b for _, b in by_cell[key]], float)
        j = np.searchsorted(qe, S['ep'][idx], 'right') - 1
        idx, j = idx[j >= 0], j[j >= 0]
        for b in np.unique(qb[j]):
            sel = idx[qb[j] == b]
            Pp, Pn = S['P'][sel], S['P'][sel + 1]
            kn = lib.knockout(Pp, Pn, b)
            wins = tracker.wins(key, float(b), float(S['P'][sel].mean()) * 10.0 ** -pip)
            inw = np.zeros(len(sel), bool)
            for win in wins:
                inw |= (kn['w'] >= win['w_lo']) & (kn['w'] < win['w_hi'])
            if not inw.any():
                continue
            Gm = cell_G(tracker.cells[key]['model'], g, float(b), kn['K'][inw], kn['ph'][inw])
            for e, gm, st in zip(S['ep'][sel][inw], Gm, kn['stay'][inw]):
                k2 = (sym, int(e))
                if k2 not in best or gm > best[k2][0]:
                    best[k2] = (float(gm), (1 + g) * bool(st), sym, g)
    return best


def drift(series, sym, reps, seed=1):
    if sym not in series:
        return None
    ep, px, pip = series[sym]
    S = lib.prep_series(sym, ep, px, pip)
    P = S['P'].astype(float)
    r = (np.diff(P) / P[:-1])[S['ok']]
    return lib.boot_mean(r, S['blk'][S['ok']], reps, seed)


def evaluate(args):
    import accu_phase_decide as apd
    pr, sha = prereg_t2()
    with open(args.map) as fh:
        pmap = json.load(fh)
    if pmap.get('prereg_sha256') != sha:
        raise SystemExit(f"phase map built on prereg sha {pmap.get('prereg_sha256')}, file is {sha}: refusing")
    tracker = WindowTracker(pmap, pr)
    rows = read_quotes(os.path.join(args.watch_dir, 'quotes.jsonl'))
    lines = []
    say = lambda s: (print(s), lines.append(s))
    if not rows:
        raise SystemExit('no authenticated quotes in quotes.jsonl')
    days = (rows[-1]['t'] - rows[0]['t']) / 86400
    intervals, _ = replay(tracker, rows)
    series = apd.load_tick_dir(args.watch_dir, {})
    best = in_window_ticks(tracker, series, rows, intervals)
    vals = np.array([v[1] for v in best.values()], float)
    blk = np.array([k[1] // 3600 for k in best], np.int64)
    D = lib.boot_mean(vals, blk, args.reps, 1)
    per = {}
    for (sym, e), (Gm, v, s, g) in best.items():
        per.setdefault(f'{s} {g}', []).append(v)
    dec = pr['decision']
    qual = [iv for iv in intervals if iv['qualifying']]
    opened = [iv for iv in intervals]
    fast_retune = [iv for iv in opened if iv['reason'] == 'retune' and iv['dwell_s'] < pr['window_rules']['min_dwell_s']]
    final = days >= dec['horizon_days']
    if np.isfinite(D['lo']) and D['lo'] >= 1 and D['n_blocks'] >= lib.THRESH['B_min_blocks']:
        verdict = 'PASS'
    elif np.isfinite(D['hi']) and D['hi'] < 1 and D['n'] >= 100000:
        verdict = 'KILL (pooled D upper 99% < 1)'
    elif final and not qual:
        verdict = 'KILL (no qualifying window in 14 days)'
    elif final and opened and len(fast_retune) == len(opened):
        verdict = 'KILL (every window retuned within 30 min)'
    else:
        verdict = 'INCONCLUSIVE' if final else 'INTERIM'
    dr = drift(series, pr['carried_T1_drift']['symbol'], args.reps)
    be = pr['carried_T1_drift']['breakeven']
    dv = None
    if dr and dr.get('valid'):
        dv = 'T1 KILL' if dr['hi'] < be else 'T1 ALIVE' if dr['lo'] > be else 'INCONCLUSIVE'
    say(f'T2 evaluate  prereg sha256 {sha}')
    say(f'  quotes {len(rows)} over {days:.2f} days; armed cells {len(tracker.cells)}')
    say(f'  windows opened {len(opened)}, qualifying (>= 30 min) {len(qual)}, retuned < 30 min {len(fast_retune)}')
    for iv in intervals:
        say(f"    {iv['sym']:10s} g={iv['g']}  open {lib._f(iv['dwell_s'] / 60, 1)} min  {iv['reason']}"
            f"{'  QUALIFYING' if iv['qualifying'] else ''}")
    say(f"  pooled in-window D {lib._f(D['point'], 5)} [{lib._f(D['lo'], 5)}, {lib._f(D['hi'], 5)}]  "
        f"n {D.get('n')}  blocks {D['n_blocks']}")
    for k, v in sorted(per.items()):
        say(f'    {k:16s} n {len(v)}  mean (1+g)*stay {np.mean(v):.5f}  (information only)')
    if dr:
        say(f"  carried T1 drift {pr['carried_T1_drift']['symbol']}: {lib._f(dr['point'], 3)} per tick "
            f"[{lib._f(dr['lo'], 3)}, {lib._f(dr['hi'], 3)}] n {dr.get('n')}  vs breakeven {be:g} -> {dv}")
    say(f'VERDICT: {verdict}')
    res = {'prereg_sha256': sha, 'days': days, 'n_quotes': len(rows), 'intervals': intervals, 'D': D,
           'per_cell': {k: {'n': len(v), 'mean': float(np.mean(v))} for k, v in per.items()},
           'drift': dr, 'drift_verdict': dv, 'verdict': verdict}
    if args.outdir:                              # the watcher's daily run: <outdir>/summary.txt has the VERDICT
        os.makedirs(args.outdir, exist_ok=True)
        jp, sp = os.path.join(args.outdir, 't2_eval.json'), os.path.join(args.outdir, 'summary.txt')
    else:
        tag = utc_tag()
        jp = os.path.join(args.watch_dir, f't2_eval_{tag}.json')
        sp = os.path.join(args.watch_dir, f't2_summary_{tag}.txt')
    with open(jp, 'x') as fh:
        json.dump(lib.jsonable(res), fh, indent=1)
    with open(sp, 'x') as fh:
        fh.write('\n'.join(lines) + '\n')
    print(f'  wrote {jp}, {sp}')
    return res


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--symbols', nargs='+')
    b.add_argument('--ticks', type=int, default=BUILD_TICKS)
    b.add_argument('--outdir')
    e = sub.add_parser('evaluate')
    e.add_argument('--watch-dir', required=True)
    e.add_argument('--map', required=True)
    e.add_argument('--reps', type=int, default=5000)
    e.add_argument('--outdir', help='write t2_eval.json and summary.txt here (default: tagged files in --watch-dir)')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return build(args) if args.cmd == 'build' else evaluate(args)


if __name__ == '__main__':
    main()
