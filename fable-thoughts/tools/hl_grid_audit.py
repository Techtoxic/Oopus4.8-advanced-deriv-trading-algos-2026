"""hl_grid_audit.py: is any Higher/Lower barrier on the volatility indices paid above fair?

WHY THIS IS THE RIGHT TEST
On a volatility index the generator is GBM with a known sigma (measured/nominal 0.99998 on
1HZ30V). The chance that a tick contract finishes above entry + B is therefore a closed-form
number. No prediction is needed. For every (symbol, duration, barrier) cell:

    EV = payout_multiplier x P_fair - 1

If no cell is positive, Higher/Lower barrier mispricing is dead. That also closes the
two-sided "strangle" (ledger 15.1): by linearity a pair of legs is positive only if a leg is.

This cell family was never audited. surface_scan.py covered touch, runs, asians and ticks,
not HIGHER/LOWER with an offset.

THE MODEL, AND THE CORRECTIONS TO THE PROPOSED PROTOCOL
  - sigma is the per-tick log sd s, measured from recent ticks. It is not "c * S0" plugged
    into dS = sigma S dW, which counts spot twice.
  - P(Higher) = P(S_exit > S_entry + B) = Phi((-ln(1 + B/S) - m) / (s sqrt n))
    P(Lower)  = P(S_exit < S_entry - B) = Phi(( ln(1 - B/S) - m) / (s sqrt n))
    m is the log drift: 0 if price is a log-martingale, -s^2 n / 2 if price is a martingale.
    Both are evaluated. Lower uses d2, not d1: 1 - Phi(d1) is the share-measure probability.
  - n is the number of tick increments. For tick contracts, n = N or N-1. For seconds
    contracts, it depends on the tick interval (1s for 1HZ*, 2s for R_*); the step index
    turned 15s into 14 intervals. The longcode pins it; the other count is reported.
  - Wins are strict ("strictly higher"), so the barrier is shifted half a pip.
  - The replay scales the barrier to each window's entry spot. A fixed absolute offset on a
    drifting path biased a simulated replay by up to 40 standard errors.
  - sigma is corrected for quote rounding and taken at +-2 standard errors. The increment
    count is pinned from the longcode; the EV with one fewer increment is reported alongside.
  P_fair_max is the most favourable value over all of these variants. A cell is FLAGGED only
  if payout x P_fair_max > 1 + margin. This guards against a false KILL. A FLAGGED cell is
  not an edge: it must then pass the empirical replay and the power calculation below.
  - JD100 has jumps, so GBM does not apply. It is excluded by default (jump-diffusion would
    otherwise manufacture positive cells).

CONTROLS
  1. Known answer: Rise/Fall (barrier-free CALL/PUT, 5 ticks) must come out at P_fair ~ 0.5
     and EV equal to the known house margin.
  2. Model fit: for every quoted cell, the model P is compared with the empirical win rate on
     non-overlapping historical windows (stride = n, so no overlap inflation, ledger bug 20).
     The z-scores must look standard normal. If they don't, the model, the tick count or the
     barrier rule is wrong, and the EVs mean nothing.

PRE-REGISTERED VERDICT (written before any quote was taken, 2026-10-01)
  KILL     no cell with payout x P_fair_max > 1.005 on any symbol, and both controls pass.
  FLAGGED  at least one such cell. Then, for that cell only:
             (a) empirical replay on fresh ticks: Wilson-99 lower bound on win rate > 1/payout
             (b) trades needed for alpha 0.01 two-sided, power 0.90:
                 N = ((2.576 + 1.2816) * payout * sqrt(p(1-p)) / EV)^2
                 (+2.5% EV at payout ~1.95 needs ~22,600 trades, not 6,600)
           A live demo run then only checks settlement rules (entry tick, ties). It does not
           prove the edge.
  INVALID  a control fails. Fix the harness, do not read the EVs.
  The grid can change, so a KILL holds for the grid on the date of the run only.

QUOTES
With DERIV_TOKEN set, quotes come from the logged-in demo account, which is what you can
buy (ledger A11). Without a token the public endpoint is used and every number is labelled
PUBLIC, which does not count toward the verdict. No orders are ever placed.

Run:
  DERIV_TOKEN=... python3 hl_grid_audit.py
  python3 hl_grid_audit.py --symbols 1HZ30V --cache 1HZ30V=../data/1HZ30V.npz
  python3 hl_grid_audit.py --selftest
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

SYMBOLS = ['1HZ10V', '1HZ25V', '1HZ30V', '1HZ50V', '1HZ75V', '1HZ90V', '1HZ100V',
           'R_10', 'R_25', 'R_50', 'R_75', 'R_100']
TICK_DURS = [5, 6, 7, 8, 9, 10]
SEC_DURS = [15, 30, 60, 300]
KS = [0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
FLAG_MARGIN = 0.005
STAKE = 10.0
Z_A, Z_B = 2.576, 1.2816
LONGCODES = {}


def ncdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2))


def p_fair(side, B, S, s, n, pip=0.0, drift='log'):
    """P(win) for a Higher (side=+1) or Lower (side=-1) contract with absolute offset B > 0,
    entry spot S, per-tick log sd s and n increments. pip shifts the barrier by +-pip/2 for
    the tie rule (negative pip = favourable shift)."""
    if n <= 0 or s <= 0:
        return float('nan')
    m = 0.0 if drift == 'log' else -0.5 * s * s * n
    sd = s * math.sqrt(n)
    if side > 0:
        k = math.log1p((B + pip / 2) / S)
        return ncdf((-k + m) / sd)
    lo = 1 - (B + pip / 2) / S
    if lo <= 0:
        return 1.0
    return ncdf((math.log(lo) - m) / sd)


def p_fair_max(side, B, S, s, s_se, n, pip):
    """Most favourable P over drift convention and sigma +-2SE. Wins are strict ("strictly
    higher than", per the longcode), so the barrier is always shifted half a pip; letting the
    tie rule float moved tail-cell P by ~10% and flagged 73 cells priced at -3% in the test.
    The increment count n is NOT varied here: it is pinned by the contract longcode, and the
    N vs N-1 ambiguity alone moves a tail cell's EV by several percent (it flagged almost every
    tail cell in the offline test)."""
    best = 0.0
    for sig in (s - 2 * s_se, s, s + 2 * s_se):
        for d in ('log', 'price'):
            best = max(best, p_fair(side, B, S, sig, n, pip, d))
    return best


def increments(dur, unit, dt):
    """(central, alternative) tick-increment counts. A tick contract's exit is the Nth tick
    after the entry spot (check the printed longcode); a seconds contract settles on the last
    tick at or before entry + T, so floor(T/dt) increments, or one fewer."""
    if unit == 't':
        return dur, max(dur - 1, 1)
    k = int(math.floor(dur / dt))
    return k, max(k - 1, 1)


def sigma_tick(p, pip=0.0):
    """Per-tick log sd and its standard error, with a robust cap on the rare rebase jump.
    Quotes are rounded to the pip, which adds 2 x pip^2/12 to every increment's variance;
    that is removed so sigma is not overstated."""
    p = np.asarray(p, dtype=float)
    r = np.diff(np.log(p))
    mad = np.median(np.abs(r - np.median(r))) * 1.4826
    r = r[np.abs(r) <= 8 * mad] if mad > 0 else r
    v = float(np.mean(r * r)) - (pip * pip / 6) / float(np.median(p)) ** 2
    s = math.sqrt(max(v, 0.0))
    return s, s / math.sqrt(2 * len(r))


def empirical(prices, pip_size, side, B, n, S_ref=None):
    """Win rate on non-overlapping windows: entry = tick i, exit = tick i+n.
    The barrier is scaled to each window's entry spot (B * e / S_ref). A fixed absolute
    offset on history means a different distance in sigma as spot drifts (ledger bug 19),
    and on a simulated path it biased the replay by up to 40 standard errors."""
    v = np.round(np.asarray(prices, dtype=float) * 10 ** pip_size).astype(np.int64)
    e, x = v[:-n:n], v[n::n]
    m = min(len(e), len(x))
    e, x = e[:m], x[:m]
    b = np.round(B * 10 ** pip_size * (e / (S_ref * 10 ** pip_size) if S_ref else 1.0)).astype(np.int64)
    win = (x > e + b) if side > 0 else (x < e - b)
    return int(win.sum()), int(m)


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def n_needed(payout, p, ev):
    if ev <= 0:
        return float('inf')
    return ((Z_A + Z_B) * payout * math.sqrt(p * (1 - p)) / ev) ** 2


def fmt_barrier(B, pip_size, side):
    return f"{'+' if side > 0 else '-'}{B:.{pip_size}f}"


def get_ticks(ws, sym, total, cache):
    if cache and os.path.exists(cache):
        z = np.load(cache)
        t, p = z['t'].astype(np.int64), z['p'].astype(float)
        o = np.argsort(t, kind='stable')
        return t[o][-total:], p[o][-total:], int(z['pip'])
    t, p, pip = ws.history_paged(sym, total, sleep=0.2)
    return np.asarray(t, dtype=np.int64), np.asarray(p, dtype=float), int(pip)


def quote(ws, sym, ct, dur, unit, barrier=None):
    req = {'proposal': 1, 'amount': STAKE, 'basis': 'stake', 'contract_type': ct,
           'currency': 'USD', 'underlying_symbol': sym, 'duration': dur, 'duration_unit': unit}
    if barrier is not None:
        req['barrier'] = barrier
    r = ws.call(req)
    if 'proposal' in r:
        LONGCODES.setdefault((sym, unit), r['proposal'].get('longcode'))
        return float(r['proposal']['payout']) / STAKE, None
    e = r.get('error', {})
    return None, f"{e.get('code')}: {str(e.get('message'))[:70]}"


def audit_symbol(ws, sym, a, out):
    cache = dict(c.split('=', 1) for c in a.cache).get(sym)
    t, p, pip_size = get_ticks(ws, sym, a.ticks, cache)
    dt = float(np.median(np.diff(t)))
    S = float(p[-1])
    pip = 10.0 ** -pip_size
    s, s_se = sigma_tick(p, pip)
    print(f"\n=== {sym}  spot {S}  pip {pip}  dt {dt:.0f}s  ticks {len(p)}  "
          f"s/tick {s:.3e} (se {s_se/s*100:.2f}%)")

    ctl = []
    for side, ct in ((1, 'CALL'), (-1, 'PUT')):
        pay, err = quote(ws, sym, ct, 5, 't')
        k, m = empirical(p, pip_size, side, 0.0, 5, S)
        if pay:
            ctl.append(dict(ct=ct, pay=pay, emp=k / m, ev=pay * 0.5 - 1))
            print(f"  control {ct:4} 5t  payout {pay:.4f}  empirical P {k/m:.4f}  "
                  f"EV at P=0.5 {(pay*0.5-1)*100:+.2f}%")
        else:
            print(f"  control {ct} failed: {err}")
        time.sleep(a.sleep)

    cells = []
    durs = [(d, 't') for d in TICK_DURS] + [(d, 's') for d in SEC_DURS]
    for dur, unit in durs:
        nmid, nalt = increments(dur, unit, dt)
        for kk in KS:
            B = round(kk * S * s * math.sqrt(nmid), pip_size)
            if B < pip:
                continue
            for side, ct in ((1, 'CALL'), (-1, 'PUT')):
                bs = fmt_barrier(B, pip_size, side)
                pay, err = quote(ws, sym, ct, dur, unit, bs)
                time.sleep(a.sleep)
                if pay is None:
                    cells.append(dict(sym=sym, dur=dur, unit=unit, k=kk, side=side, B=B,
                                      barrier=bs, err=err))
                    continue
                pm = p_fair_max(side, B, S, s, s_se, nmid, pip)
                pc = p_fair(side, B, S, s, nmid, pip)        # strict win: barrier + half pip
                pa = p_fair(side, B, S, s, nalt, pip)
                c = dict(sym=sym, dur=dur, unit=unit, k=kk, side=side, B=B, barrier=bs,
                         pay=pay, p_central=pc, p_max=pm, ev_central=pay * pc - 1,
                         ev_max=pay * pm - 1, ev_alt_n=pay * pa - 1, n_inc=(nmid, nalt))
                c['flag'] = c['ev_max'] > FLAG_MARGIN
                if unit == 't':
                    kw, mw = empirical(p, pip_size, side, B, nmid, S)
                    if mw:
                        se = math.sqrt(pc * (1 - pc) / mw)
                        c.update(emp=kw / mw, n_win=mw, z=(kw / mw - pc) / se if se else 0.0)
                cells.append(c)
    return dict(sym=sym, spot=S, s=s, s_se=s_se, dt=dt, pip=pip_size, controls=ctl), cells


def report(meta, cells, public, path):
    good = [c for c in cells if 'pay' in c]
    bad = [c for c in cells if 'err' in c]
    # one control cell per (symbol, duration): the barrier nearest 1 sd, Higher and Lower
    # pooled. Cells sharing a path and nested barriers are strongly correlated, so a mean of
    # z over all cells is not a valid test; these few are compared one by one.
    ctl_cells = {}
    for c in good:
        if 'z' in c and abs(c['k'] - 1.0) < 1e-9:
            ctl_cells.setdefault((c['sym'], c['dur']), []).append(c)
    zs = []
    for cs in ctl_cells.values():
        k = sum(c['emp'] * c['n_win'] for c in cs)
        m = sum(c['n_win'] for c in cs)
        pm = sum(c['p_central'] * c['n_win'] for c in cs) / m
        zs.append((k / m - pm) / math.sqrt(pm * (1 - pm) / m))
    zs = np.array(zs)
    flags = sorted([c for c in good if c['flag']], key=lambda c: -c['ev_max'])
    L = []
    L.append(f"# Higher/Lower barrier grid audit ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())})\n")
    L.append(f"Quotes: **{'PUBLIC: does not count toward the verdict' if public else 'logged-in demo'}**. "
             f"{len(good)} cells quoted, {len(bad)} rejected by the server.\n")
    L.append("## Controls\n")
    ctl_ok = True
    for m in meta:
        for c in m['controls']:
            okp = abs(c['emp'] - 0.5) < 0.03 and -0.10 < c['ev'] < 0
            ctl_ok &= okp
            L.append(f"- {m['sym']} {c['ct']} 5t: payout {c['pay']:.4f}, empirical P {c['emp']:.4f}, "
                     f"EV at 0.5 {c['ev']*100:+.2f}% (known margin 2-4%) {'ok' if okp else 'CHECK'}")
    if len(zs):
        zok = bool(np.abs(zs).max() < 3.5)
        ctl_ok &= zok
        L.append(f"- Model vs empirical, 1-sd barrier, {len(zs)} (symbol, duration) cells: "
                 f"z {', '.join(f'{z:+.2f}' for z in zs)}; all |z| < 3.5 {'ok' if zok else 'CHECK'}")
    L.append("\n## Settlement rule (from the contract longcode; confirms the increment count)\n")
    for (sy, u), lc in sorted(LONGCODES.items()):
        L.append(f"- {sy} [{u}]: {lc}")
    L.append("\n## Per symbol: best cells\n")
    L.append("| symbol | cells | best EV (central) | best EV (most favourable) | cell |")
    L.append("|---|---:|---:|---:|---|")
    for m in meta:
        cs = [c for c in good if c['sym'] == m['sym']]
        if not cs:
            continue
        b = max(cs, key=lambda c: c['ev_max'])
        L.append(f"| {m['sym']} | {len(cs)} | {max(c['ev_central'] for c in cs)*100:+.2f}% | "
                 f"{b['ev_max']*100:+.2f}% | {b['dur']}{b['unit']} {'Higher' if b['side']>0 else 'Lower'} "
                 f"{b['barrier']} ({b['k']} sd), payout {b['pay']:.4f} |")
    L.append("\n## Verdict\n")
    if not ctl_ok:
        v = "INVALID: a control failed. Do not read the EVs."
    elif public:
        v = "NO VERDICT: public quotes. Rerun with DERIV_TOKEN set."
    elif flags:
        v = f"FLAGGED: {len(flags)} cell(s) above +{FLAG_MARGIN*100:.1f}% at the most favourable model."
    else:
        v = (f"KILL: no Higher/Lower cell above +{FLAG_MARGIN*100:.1f}% on any symbol, even at the most "
             "favourable model. Idea 15.1 is closed for this grid.")
    L.append(f"**{v}**\n")
    for c in flags[:20]:
        pc = c['p_max']
        L.append(f"- {c['sym']} {c['dur']}{c['unit']} {'Higher' if c['side']>0 else 'Lower'} {c['barrier']}: "
                 f"payout {c['pay']:.4f}, P {c['p_central']:.4f} to {pc:.4f}, EV {c['ev_central']*100:+.2f}% "
                 f"to {c['ev_max']*100:+.2f}% (EV with n-1 increments {c['ev_alt_n']*100:+.2f}%), trades needed ~{n_needed(c['pay'], pc, c['ev_max']):,.0f}"
                 + (f", empirical {c['emp']:.4f} on {c['n_win']} windows" if 'emp' in c else ''))
    if bad:
        L.append("\n## Rejected requests (first 10)\n")
        for c in bad[:10]:
            L.append(f"- {c['sym']} {c['dur']}{c['unit']} {c['barrier']}: {c['err']}")
    txt = "\n".join(L) + "\n"
    with open(path, 'w') as f:
        f.write(txt)
    print("\n" + txt)


def selftest():
    S, s = 1000.0, 1e-4
    assert abs(p_fair(1, 1e-9, S, s, 5) - 0.5) < 1e-6
    assert abs(p_fair(-1, 1e-9, S, s, 5) - 0.5) < 1e-6
    # one sd up: Higher ~ 0.1587
    B = S * math.expm1(s * math.sqrt(9))
    assert abs(p_fair(1, B, S, s, 9) - 0.158655) < 1e-4
    assert p_fair_max(1, B, S, s, 0.01 * s, 9, 0.01) >= p_fair(1, B, S, s, 9, 0.01)
    rng = np.random.default_rng(1)
    p = S * np.exp(np.cumsum(rng.normal(0, s, 400_000)))
    st, se = sigma_tick(p)
    B = round(1.0 * p[-1] * s * math.sqrt(5), 2)
    k, m = empirical(p, 2, 1, B, 5, float(p[-1]))
    pm = p_fair(1, B, float(p[-1]), s, 5, 0.01)
    assert abs(k / m - pm) < 5 * math.sqrt(pm * (1 - pm) / m), ('replay bias', k / m, pm)
    assert abs(st / s - 1) < 4 * se / s, (st, se)
    assert increments(15, 's', 1.0) == (15, 14)
    assert increments(5, 't', 1.0) == (5, 4)
    assert 20_000 < n_needed(1.95, 0.5256, 0.025) < 25_000
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', nargs='+', default=SYMBOLS)
    ap.add_argument('--ticks', type=int, default=60_000, help='history per symbol for sigma and replay')
    ap.add_argument('--cache', nargs='*', default=[], help='SYMBOL=path.npz (t, p, pip) to skip fetching')
    ap.add_argument('--sleep', type=float, default=0.08)
    ap.add_argument('--out', default='../results/hl_grid_audit.md')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if any(sy.startswith('JD') for sy in a.symbols):
        sys.exit('JD symbols have jumps; the GBM fair value does not apply. Remove them.')
    from deriv_api import DerivWS
    tok = os.environ.get('DERIV_TOKEN', '')
    public = not tok
    if public:
        print('WARNING: no DERIV_TOKEN, so quotes are PUBLIC and cannot give a verdict (ledger A11).')
    ws = DerivWS(token=tok, account_type='demo') if tok else DerivWS(token='')
    meta, cells = [], []
    try:
        for sym in a.symbols:
            try:
                m, c = audit_symbol(ws, sym, a, cells)
            except Exception as e:  # one bad symbol must not lose the rest
                print(f"{sym}: {e}")
                continue
            meta.append(m)
            cells += c
    finally:
        ws.close()
    with open(a.out.replace('.md', '.json'), 'w') as f:
        json.dump(dict(meta=meta, cells=cells, public=public), f, indent=1, default=str)
    report(meta, cells, public, a.out)


if __name__ == '__main__':
    main()
