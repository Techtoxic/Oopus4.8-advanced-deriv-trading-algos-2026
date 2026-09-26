#!/usr/bin/env python3
"""accu_phase_decide.py -- read-only GO / KILL / INCONCLUSIVE decision for the phase-gated Crash ACCU lead.

THE CLAIM UNDER TEST (ACCU_PHASE_LATTICE.md)
On CRASH1000 and CRASH500 the knockout compares an integer-pip move with a continuous barrier
w = b*P_prev, so per-tick survival is a sawtooth in the phase of w. At any growth rate whose level
K = floor(w) is low enough, G = (1+g)*P(stay) is above 1 while the phase is in [0.05, 0.125).
Pick whichever g is in band at the current spot. The criteria below were fixed before any fresh data.

SUBCOMMANDS (public data only: no token, no buy or sell anywhere)
  ladder   barrier ladder, 14 Boom/Crash + R_100/1HZ100V/1HZ10V x g = 1-5%, stake check; L1-L4
  collect  tick history -> ticks/<SYM>.npz (600k CRASH1000/500, 300k the rest, 150k on N=50)
  oracle   ticks_stayed_in snapshots (8 symbols x 5 rates), the last 20k ticks, rule alignment; OR-1..3
  analyze  S1-S7, D, M, C1-C5 and the verdict -> analysis.json, summary.txt (last line VERDICT: ...)
  all      ladder, oracle snapshot 1, collect, oracle snapshot 2, 20k-tick fetch, alignment, analyze
           (about 60-75 minutes)

RUN (from fable-thoughts/tools; needs websocket-client>=1.6, numpy, scipy)
  python3 accu_phase_decide.py all
  python3 accu_phase_decide.py ladder --repeat 6 --interval 600          # barrier-vs-spot series
  python3 accu_phase_decide.py analyze --from-dir ../results/accu_phase_<UTC>
  python3 accu_phase_decide.py analyze --from-dir <watcher outdir>       # ticks/<SYM>/<date>.csv archive
  python3 accu_phase_decide.py analyze --local-json ../../data/CRASH500.json.gz ../../data/CRASH1000.json.gz \\
          --barrier CRASH500=0.04:4.7141e-6 CRASH1000=0.04:2.3454e-6     # offline, repo json.gz format
Outputs go to --outdir (default ../results/accu_phase_<UTC>/); every file is opened with mode 'x'.
Send back: ladder.json, oracle_*.json, analysis.json, summary.txt, ticks/*.npz (zipped) and the stdout.
"""
import argparse, datetime as dt, glob, gzip, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import accu_phase_lib as lib
from derivfetch import fetch_ticks, native_interval

DerivWS = None               # imported on first use, so analyze needs no websocket-client
PAUSE_PROPOSAL = 0.15
PAUSE_SYMBOL = 1.0
UTC = dt.timezone.utc
LADDER_SYMBOLS = lib.BC + lib.VOL
ORACLE_SYMBOLS = ('CRASH1000', 'CRASH500', 'BOOM300N', 'CRASH300N', 'BOOM500', 'BOOM1000', 'R_100', '1HZ100V')
TICK_TARGETS = {'CRASH1000': 600_000, 'CRASH500': 600_000,
                'BOOM300N': 300_000, 'CRASH300N': 300_000, 'BOOM500': 300_000, 'BOOM1000': 300_000,
                'BOOM600': 300_000, 'BOOM900': 300_000, 'CRASH600': 300_000, 'CRASH900': 300_000,
                'BOOM150N': 300_000, 'CRASH150N': 300_000, 'BOOM50': 150_000, 'CRASH50': 150_000}
STAKES = (1, 1.13, 100)
CD_FIELDS = ('tick_size_barrier', 'barrier_spot_distance', 'high_barrier', 'low_barrier', 'maximum_ticks',
             'maximum_payout', 'ticks_stayed_in', 'last_tick_epoch')


def utc_tag():
    return dt.datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')


def utc_str(ep):
    return dt.datetime.fromtimestamp(int(ep), UTC).strftime('%Y-%m-%d %H:%M:%S')


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def write_json(path, obj):
    with open(path, 'x') as fh:
        json.dump(lib.jsonable(obj), fh, indent=1)
    print(f'  wrote {path}')


def save_npz(path, ep, px, pip):
    with open(path, 'xb') as fh:
        np.savez_compressed(fh, ep=np.asarray(ep, np.int64), px=np.asarray(px, float), pip=np.int64(pip))


# ------------------------------------------------------------------ network (read-only)
def new_ws():
    global DerivWS
    if DerivWS is None:
        from deriv_api import DerivWS as _W
        DerivWS = _W
    return DerivWS(token="", timeout=15)


def request(ws, payload, what):
    r = ws.call(payload)
    if 'error' in r:
        e = r['error']
        print(f"  {what}: error {e.get('code')}: {e.get('message')}")
    return r


def get_pip(ws, sym):
    r = request(ws, {"ticks_history": sym, "count": 1, "end": "latest", "style": "ticks"}, f'{sym} pip_size')
    return int(r['pip_size']) if r.get('pip_size') is not None else None


def quote(ws, sym, g, amount=10):
    r = request(ws, {"proposal": 1, "amount": amount, "basis": "stake", "contract_type": "ACCU",
                     "currency": "USD", "underlying_symbol": sym, "growth_rate": g}, f'{sym} g={g} amount={amount}')
    p = r.get('proposal')
    if not p:
        return None
    cd = p.get('contract_details') or {}
    q = {k: cd.get(k) for k in CD_FIELDS}
    q.update(spot=p.get('spot'), spot_time=p.get('spot_time'), validation_params=p.get('validation_params'),
             amount=amount, proposal=p)
    if q['tick_size_barrier'] is not None:
        q['b'] = float(q['tick_size_barrier'])
        q['b_repr'] = repr(q['b'])
    return q


# ------------------------------------------------------------------ ladder
def run_ladder(args, outdir):
    syms = args.symbols or list(LADDER_SYMBOLS)
    print(f'\n== ladder: {len(syms)} symbols x {len(lib.RATES)} rates, {args.repeat} round(s)')
    rounds, pips = [], {}
    for rep in range(args.repeat):
        if rep:
            print(f'  sleeping {args.interval}s before round {rep + 1}')
            time.sleep(args.interval)
        rnd = {'utc': utc_tag(), 'quotes': {}}
        for sym in syms:
            ws = None
            try:
                ws = new_ws()
                if sym not in pips:
                    pips[sym] = get_pip(ws, sym)
                qs = {}
                for g in lib.RATES:
                    q = quote(ws, sym, g)
                    time.sleep(PAUSE_PROPOSAL)
                    if q and 'b' in q:
                        qs[str(g)] = q
                rnd['quotes'][sym] = qs
                print(f"  {sym:10s} pip {pips[sym]}  " + '  '.join(f"{g}:{q['b']:.6g}" for g, q in qs.items()))
            except Exception as e:
                print(f'  {sym}: {type(e).__name__}: {str(e)[:160]}')
            finally:
                if ws is not None:
                    ws.close()
            time.sleep(PAUSE_SYMBOL)
        rounds.append(rnd)
    stake = {}
    for sym in ('CRASH1000', 'CRASH500'):
        if sym not in syms:
            continue
        ws = None
        try:
            ws = new_ws()
            stake[sym] = {}
            for amt in STAKES:
                q = quote(ws, sym, 0.04, amt)
                time.sleep(PAUSE_PROPOSAL)
                stake[sym][str(amt)] = q.get('b') if q else None
            last = rounds[-1]['quotes'].get(sym, {}).get('0.04')
            stake[sym]['10'] = last.get('b') if last else None
        except Exception as e:
            print(f'  stake check {sym}: {type(e).__name__}: {str(e)[:160]}')
        finally:
            if ws is not None:
                ws.close()
    lad = {'utc': utc_tag(), 'rounds': rounds, 'pip': pips, 'stake_check': stake}
    lad['derived'] = ladder_derived(lad)
    lad['checks'] = ladder_checks(lad)
    write_json(os.path.join(outdir, 'ladder.json'), lad)
    return lad


def ladder_derived(lad):
    """Per (sym, g, round): w, K, phase at the quoted spot, b_g/b_4% against law A and the Gaussian
    ladder, and which rounding of b*spot reproduces barrier_spot_distance."""
    prereg = lib.load_prereg()
    out = {}
    for i, rnd in enumerate(lad['rounds']):
        for sym, qs in rnd['quotes'].items():
            pip = lad['pip'].get(sym)
            b4 = (qs.get('0.04') or {}).get('b')
            N = lib.sym_N(sym)
            for gs, q in qs.items():
                b, spot = q.get('b'), q.get('spot')
                if b is None or spot is None or pip is None:
                    continue
                w = b * float(spot) * 10 ** pip
                K = math.floor(w)
                row = {'round': i, 'b': b, 'spot': spot, 'spot_time': q.get('spot_time'), 'w': w, 'K': K,
                       'phase': w - K, 'maximum_ticks': q.get('maximum_ticks')}
                if b4:
                    row['ratio_to_4pct'] = b / b4
                    row['lawA_ratio'] = lib.law_a_ratio(N, float(gs), prereg) if N else None
                    row['gauss_ratio'] = lib.gauss_ratio(float(gs))
                if q.get('barrier_spot_distance') is not None:
                    row['rounding'] = lib.rounding_directions(b, spot, pip, q['barrier_spot_distance'])
                out.setdefault(sym, {}).setdefault(gs, []).append(row)
    return out


def ladder_checks(lad):
    T = lib.THRESH
    prereg = lib.load_prereg()
    der = lad['derived']
    last = len(lad['rounds']) - 1
    print(f"\n  L1  b_g/b_4% vs law A (standard Boom/Crash symbols; tolerance {100 * T['L1_tol']:.1f}%)")
    l1 = []
    for sym, per in der.items():
        if lib.family(sym) == 'vol' or lib.is_nseries(sym):
            continue
        for gs, rows in per.items():
            r = rows[-1]
            if gs == '0.04' or r.get('lawA_ratio') is None or r.get('ratio_to_4pct') is None:
                continue
            dev = r['ratio_to_4pct'] / r['lawA_ratio'] - 1
            l1.append({'sym': sym, 'g': gs, 'ratio': r['ratio_to_4pct'], 'lawA': r['lawA_ratio'],
                       'gauss': r['gauss_ratio'], 'dev': dev, 'ok': abs(dev) < T['L1_tol']})
            print(f"      {sym:10s} g={gs}  ratio {r['ratio_to_4pct']:.4f}  lawA {r['lawA_ratio']:.4f}  "
                  f"gauss {r['gauss_ratio']:.4f}  dev {dev * 100:+.3f}%  {'ok' if abs(dev) < T['L1_tol'] else 'FAIL'}")
    pred = prereg['predicted_barriers_anchored_on_recorded_4pct'].get('CRASH1000', {})
    q1000 = der.get('CRASH1000', {})
    print('      CRASH1000 predicted (law A / Gaussian) vs quoted: ' + '  '.join(
        f"{g}: {v['lawA']:.4e}/{v['gaussian']:.4e} vs {q1000[g][-1]['b']:.4e}" if g in q1000 else f"{g}: {v['lawA']:.4e} vs n/a"
        for g, v in pred.items()))
    print('  state at the last round (g: K / phase):')
    for sym, per in der.items():
        print(f'      {sym:10s} ' + '  '.join(f"{gs}: {rows[-1]['K']}/{rows[-1]['phase']:.3f}" for gs, rows in per.items()))
    print('  L2  recorded barriers unchanged')
    l2 = []
    for sym, per in prereg['recorded'].items():
        for gs, v in per.items():
            if not isinstance(v, (int, float)):
                continue
            q = (der.get(sym, {}).get(gs) or [None])[-1]
            if q is None:
                l2.append({'sym': sym, 'g': gs, 'recorded': v, 'quoted': None, 'ok': None})
                print(f'      {sym:10s} g={gs}  recorded {v:.5g}  quoted n/a')
                continue
            rel = q['b'] / v - 1
            ok = abs(rel) <= lib.recorded_tol(v) + 1e-12
            l2.append({'sym': sym, 'g': gs, 'recorded': v, 'quoted': q['b'], 'rel': rel, 'ok': ok})
            print(f"      {sym:10s} g={gs}  recorded {v:.5g}  quoted {q['b']!r}  {'ok' if ok else 'CHANGED'}")
    print('  L3  barrier independent of stake (4%)')
    l3 = {}
    for sym, d in lad['stake_check'].items():
        vals = [v for v in d.values() if v is not None]
        l3[sym] = {'values': d, 'ok': bool(vals) and len(set(vals)) == 1}
        print(f"      {sym:10s} {d}  {'ok' if l3[sym]['ok'] else 'STAKE-DEPENDENT'}")
    tally = {'ceil': 0, 'near': 0, 'floor': 0, 'none': 0, 'n': 0}
    for per in der.values():
        for rows in per.values():
            for r in rows:
                if 'rounding' not in r:
                    continue
                tally['n'] += 1
                for k in r['rounding'] or ['none']:
                    tally[k] += 1
    tally['consistent_with_all'] = [k for k in ('ceil', 'near', 'floor') if tally['n'] and tally[k] == tally['n']]
    print(f"  L4  barrier_spot_distance rounding (quotes matched): {tally}")
    series = {}
    if len(lad['rounds']) > 1:
        for sym, per in der.items():
            for gs, rows in per.items():
                bs = sorted(set(r['b'] for r in rows))
                series[f'{sym} {gs}'] = {'distinct_b': bs, 'spot_range': [min(r['spot'] for r in rows), max(r['spot'] for r in rows)]}
        moved = [k for k, v in series.items() if len(v['distinct_b']) > 1]
        print(f"  barrier-vs-spot series over {len(lad['rounds'])} rounds: b changed on {moved or 'no cell'}")
    return {'L1': {'ok': bool(l1) and all(r['ok'] for r in l1), 'rows': l1, 'round': last},
            'L2': {'ok': all(r['ok'] for r in l2 if r['ok'] is not None) if l2 else None, 'rows': l2},
            'L3': {'ok': all(v['ok'] for v in l3.values()) if l3 else None, 'rows': l3},
            'L4': tally, 'barrier_vs_spot': series}


# ------------------------------------------------------------------ collect
def tick_report(sym, ep, target):
    ep = np.asarray(ep, np.int64)
    if len(ep) < 2:
        return {'n': int(len(ep)), 'short': True}
    iv = native_interval(ep)
    d = np.diff(ep)
    rep = {'n': int(len(ep)), 'target': int(target), 'first_utc': utc_str(ep[0]), 'last_utc': utc_str(ep[-1]),
           'interval_s': int(iv), 'contiguous_frac': float(np.mean(d == iv)), 'gaps': int(np.sum(d != iv)),
           'short': bool(len(ep) < lib.THRESH['short_frac'] * target)}
    print(f"  {sym:10s} n={rep['n']:>7} {rep['first_utc']} -> {rep['last_utc']} UTC  contiguous "
          f"{rep['contiguous_frac']:.4f}  gaps {rep['gaps']}{'  SHORT' if rep['short'] else ''}")
    return rep


def run_collect(args, outdir):
    syms = args.symbols or list(TICK_TARGETS)
    print(f'\n== collect: {len(syms)} symbols' + (f' (from {args.from_dir})' if args.from_dir else ''))
    report = {}
    if args.from_dir:
        series = load_tick_dir(args.from_dir, {})
        for sym in syms:
            if sym in series:
                report[sym] = tick_report(sym, series[sym][0], args.ticks or TICK_TARGETS.get(sym, 300_000))
        return report
    tdir = os.path.join(outdir, 'ticks')
    os.makedirs(tdir, exist_ok=True)
    for sym in syms:
        target = args.ticks or TICK_TARGETS.get(sym, 300_000)
        ws = None
        t0 = time.time()
        try:
            ws = new_ws()
            if get_pip(ws, sym) is None:          # pre-flight: echoes the server's error for this symbol
                continue
            ep, px, pip = fetch_ticks(ws, sym, target, verbose=False, strict=False)
        except Exception as e:
            print(f'  {sym}: fetch failed: {type(e).__name__}: {str(e)[:160]}')
            continue
        finally:
            if ws is not None:
                ws.close()
        save_npz(os.path.join(tdir, f'{sym}.npz'), ep, px, pip)
        report[sym] = tick_report(sym, ep, target)
        report[sym].update(pip=int(pip), fetch_s=round(time.time() - t0, 1))
        time.sleep(PAUSE_SYMBOL)
    write_json(os.path.join(outdir, 'collect.json'), report)
    return report


# ------------------------------------------------------------------ oracle
def oracle_snapshot(args, outdir, label):
    syms = args.oracle_symbols or list(ORACLE_SYMBOLS)
    print(f'\n== oracle snapshot {label}: {len(syms)} symbols x {len(lib.RATES)} rates')
    snap = {'label': label, 'utc': utc_tag(), 'snaps': {}}
    for sym in syms:
        ws = None
        try:
            ws = new_ws()
            for g in lib.RATES:
                q = quote(ws, sym, g)
                time.sleep(PAUSE_PROPOSAL)
                if q and q.get('ticks_stayed_in') is not None and q.get('b') is not None:
                    snap['snaps'].setdefault(sym, {})[str(g)] = {
                        'b': q['b'], 'b_repr': q['b_repr'], 'spot': q['spot'], 'spot_time': q['spot_time'],
                        'last_tick_epoch': q['last_tick_epoch'], 'ticks_stayed_in': q['ticks_stayed_in'],
                        'proposal': q['proposal']}
            got = snap['snaps'].get(sym, {})
            print(f"  {sym:10s} {len(got)} rates, lists of {[len(v['ticks_stayed_in']) for v in got.values()]}")
        except Exception as e:
            print(f'  {sym}: {type(e).__name__}: {str(e)[:160]}')
        finally:
            if ws is not None:
                ws.close()
        time.sleep(PAUSE_SYMBOL)
    write_json(os.path.join(outdir, f'oracle_{label}.json'), snap)
    return snap


def align_snapshots(snaps, ticks):
    """Align every snapshot record against the tick feed of its symbol."""
    records = []
    for snap in snaps:
        for sym, per in snap['snaps'].items():
            if sym not in ticks:
                print(f'  {sym}: no ticks for alignment')
                continue
            ep, px, pip = ticks[sym]
            P = lib.to_pips(px, pip)
            for gs, s in per.items():
                r = lib.oracle_align(ep, P, s['b'], s['ticks_stayed_in'], s['last_tick_epoch'])
                r.update(sym=sym, g=float(gs), snapshot=snap.get('label'))
                records.append(r)
                ru = r.get('rules', {})
                if not ru:
                    print(f"  {sym:10s} g={gs} {snap.get('label')}: {r.get('error')}")
                    continue
                es = r.get('eps_sweep') or {}
                print(f"  {sym:10s} g={gs} {snap.get('label')} {r['mode']:7s} lag {r.get('lag', 0)}  raw_incl {ru['raw_incl']['matched']}/"
                      f"{ru['raw_incl']['covered']}  ceil_strict {ru['ceil_strict']['matched']}  near_incl "
                      f"{ru['near_incl']['matched']}  K-1 {ru['K-1']['matched']}  K+1 {ru['K+1']['matched']}  "
                      f"completed {ru['raw_incl'].get('completed_matched', '-')}/{ru['raw_incl']['covered'] - 1}  "
                      f"in-progress delta {ru['raw_incl'].get('in_progress_delta', '-')}  "
                      f"longest {ru['raw_incl']['longest']}  shuffled max {r['shuffle_null']['max_longest']}  eps [{lib._f(es.get('eps_min'), 4)}, "
                      f"{lib._f(es.get('eps_max'), 4)}]  touches {sum(d['exact_touch'] for d in r['disagreements'])}")
    return records


def print_outcomes(oc):
    print(f"  OR-1 (knockout rule confirmed on CRASH1000 and CRASH500): {oc['OR1']['status']}  "
          + '  '.join(f"{s}: {v['status']} ({v['qualifying_snapshots']} snapshots)" for s, v in oc['OR1'].items()
                      if isinstance(v, dict)))
    print(f"  OR-2 E0 events (phase in (0,.05), |move| = K): {oc['OR2']['n_events']}, breaches "
          f"{oc['OR2']['n_breach']} -> {oc['OR2']['decision']}")
    print(f"  OR-3 phase in [.9,1) with |move| = K+1: {oc['OR3']['n_events']} events -> {oc['OR3']['status']}")
    for h in oc['HALT']:
        print(f'  HALT: {h}  -- the lattice program would be dead')


def run_oracle(args, outdir, snaps=None):
    if args.from_dir and snaps is None:
        snaps = [read_json(f) for f in sorted(glob.glob(os.path.join(glob.escape(args.from_dir), 'oracle_snap*.json')))]
        ticks = load_tick_dir(os.path.join(args.from_dir, 'oracle_ticks'), {})
    else:
        if snaps is None:
            snaps = [oracle_snapshot(args, outdir, 'snap1')]
        syms = sorted({s for sn in snaps for s in sn['snaps']})
        print(f'\n== oracle ticks: last {args.oracle_ticks} ticks of {len(syms)} symbols')
        odir = os.path.join(outdir, 'oracle_ticks')
        os.makedirs(odir, exist_ok=True)
        ticks = {}
        for sym in syms:
            ws = None
            try:
                ws = new_ws()
                if get_pip(ws, sym) is None:      # pre-flight: echoes the server's error for this symbol
                    continue
                ep, px, pip = fetch_ticks(ws, sym, args.oracle_ticks, verbose=False, strict=False)
            except Exception as e:
                print(f'  {sym}: fetch failed: {type(e).__name__}: {str(e)[:160]}')
                continue
            finally:
                if ws is not None:
                    ws.close()
            save_npz(os.path.join(odir, f'{sym}.npz'), ep, px, pip)
            ticks[sym] = (ep, px, pip)
            time.sleep(PAUSE_SYMBOL)
    print('\n== oracle alignment (house list vs replayed runs)')
    records = align_snapshots(snaps, ticks)
    oc = lib.oracle_outcomes(records)
    print_outcomes(oc)
    out = {'utc': utc_tag(), 'records': records, 'outcomes': oc}
    write_json(os.path.join(outdir, 'oracle_align.json'), out)
    return out


# ------------------------------------------------------------------ analyze inputs
def read_csv_archive(files):
    ep, qs = [], []
    for f in files:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line or line[0].isalpha():
                    continue
                a, b = line.split(',')[:2]
                ep.append(int(float(a)))
                qs.append(b.strip())
    dec = max((len(q.split('.')[1]) if '.' in q else 0) for q in qs) if qs else 0
    return np.array(ep, np.int64), np.array([float(q) for q in qs]), dec


def load_tick_dir(d, pips):
    """ticks/<SYM>.npz (collect) and/or ticks/<SYM>/<YYYY-MM-DD>.csv (watcher archive), merged."""
    tdir = os.path.join(d, 'ticks') if os.path.isdir(os.path.join(d, 'ticks')) else d
    series = {}
    for f in sorted(glob.glob(os.path.join(glob.escape(tdir), '*.npz'))):   # escape: '[' in a folder name
        z = np.load(f)
        series[os.path.basename(f)[:-4]] = (z['ep'], z['px'], int(z['pip']))
    for sd in sorted(glob.glob(os.path.join(glob.escape(tdir), '*'))):
        files = sorted(glob.glob(os.path.join(glob.escape(sd), '*.csv'))) if os.path.isdir(sd) else []
        if not files:
            continue
        sym = os.path.basename(sd)
        ep, px, dec = read_csv_archive(files)
        pip = pips.get(sym, dec)
        if sym in series:
            e0, p0, pip0 = series[sym]
            ep, px, pip = np.concatenate([e0, ep]), np.concatenate([p0, px]), pip0
        series[sym] = (ep, px, pip)
    return series


def load_local_json(paths, outdir):
    """Repo json.gz ([[epoch, price], ...]) -> ticks/<SYM>.npz in outdir (the converter), then load."""
    tdir = os.path.join(outdir, 'ticks')
    os.makedirs(tdir, exist_ok=True)
    series = {}
    for p in paths:
        sym = os.path.basename(p).split('.json')[0]
        with (gzip.open(p, 'rt') if p.endswith('.gz') else open(p)) as fh:
            a = np.asarray(json.load(fh), float)
        ep, px = a[:, 0].astype(np.int64), a[:, 1]
        pip = lib.infer_pipdec(px)
        save_npz(os.path.join(tdir, f'{sym}.npz'), ep, px, pip)
        series[sym] = (ep, px, pip)
        print(f'  {sym}: {len(ep)} ticks, pip 1e-{pip} -> {tdir}/{sym}.npz')
    return series


def parse_barriers(items):
    out = {}
    for it in items or []:
        sym, rest = it.split('=')
        g, b = rest.split(':')
        out.setdefault(sym, {})[float(g)] = float(b)
    return out


def ladder_inputs(lad):
    """Barriers, spots and pips from the last ladder round, plus the (time, b) history of every round."""
    bars, spots, hist = {}, {}, []
    pips = {k: v for k, v in lad.get('pip', {}).items() if v is not None}
    for rnd in lad.get('rounds', []):
        for sym, qs in rnd['quotes'].items():
            for gs, q in qs.items():
                if q.get('b') is None:
                    continue
                bars.setdefault(sym, {})[float(gs)] = q['b']
                if q.get('spot') is not None and sym in pips:
                    spots.setdefault(sym, {})[float(gs)] = (float(q['spot']), pips[sym])
                hist.append({'sym': sym, 'g': float(gs), 'b': q['b'], 't': q.get('spot_time')})
    return bars, spots, pips, hist


def quotes_log_inputs(path):
    """Watcher quotes.jsonl: latest b and spot per (sym, g) plus the full (time, b) history. Accepts the
    raw proposal shape (contract_details.tick_size_barrier) or flat keys."""
    bars, spots, hist = {}, {}, []
    with open(path) as fh:
        for line in fh:
            try:
                q = json.loads(line)
            except ValueError:
                continue
            p = q.get('proposal') if isinstance(q.get('proposal'), dict) else q
            cd = p.get('contract_details') or {}
            er = q.get('echo_req') or {}            # a verbatim ws.call() response names sym and g only here
            sym = q.get('sym') or q.get('symbol') or q.get('underlying_symbol') or er.get('underlying_symbol')
            g = q.get('g', q.get('growth_rate', er.get('growth_rate')))
            b = q.get('b', q.get('tick_size_barrier', cd.get('tick_size_barrier')))
            if sym is None or g is None or b is None:
                continue
            t = q.get('epoch', q.get('t', q.get('spot_time', p.get('spot_time'))))
            bars.setdefault(sym, {})[float(g)] = float(b)
            spot = q.get('spot', p.get('spot'))
            pip = q.get('pip', q.get('pip_size'))
            if spot is not None and pip is not None:
                spots.setdefault(sym, {})[float(g)] = (float(spot), int(pip))
            hist.append({'sym': sym, 'g': float(g), 'b': float(b), 't': t})
    return bars, spots, hist


def watcher_snapshots(d, series):
    """Watcher oracle/<SYM>_<g>_<epoch>.json proposals aligned against the archived ticks. A snapshot whose
    last_tick_epoch is not in the archive (the archive has not caught up yet, or a hole) is skipped: it
    cannot be aligned in primary mode, and it is aligned on a later analyze once the ticks are there."""
    recs, skipped = [], 0
    for f in sorted(glob.glob(os.path.join(glob.escape(d), 'oracle', '*.json'))):
        try:
            sym, gs, _ = os.path.basename(f)[:-5].rsplit('_', 2)
            obj = read_json(f)
            p = obj.get('proposal', obj)
            cd = p.get('contract_details') or {}
            if sym not in series or cd.get('ticks_stayed_in') is None:
                continue
            ep, px, pip = series[sym]
            if not np.any(ep == int(cd['last_tick_epoch'])):
                skipped += 1
                continue
            r = lib.oracle_align(ep, lib.to_pips(px, pip), float(cd['tick_size_barrier']), cd['ticks_stayed_in'],
                                 int(cd['last_tick_epoch']), eps_sweep=False, n_shuffle=20)
            r.update(sym=sym, g=float(gs), snapshot=os.path.basename(f))
            recs.append(r)
        except Exception as e:
            print(f'  watcher snapshot {f}: {type(e).__name__}: {e}')
    if skipped:
        print(f'  watcher snapshots: {skipped} skipped (last_tick_epoch not in the tick archive yet, or in a hole)')
    return recs


def a7_status(bars, hist, series, src):
    """A7: 4% barriers unchanged (vs the pre-registered record), or the analysed window is homogeneous."""
    rec = lib.load_prereg()['recorded']
    detail, status = [], 'PASS'
    for sym in lib.THRESH['decision_symbols']:
        b = (bars.get(sym) or {}).get(0.04)
        if b is None or sym not in series:
            return {'status': 'NOT_EVALUATED', 'detail': f'no 4% barrier or no ticks for {sym}'}
        ep = series[sym][0]
        t0, t1 = int(np.min(ep)), int(np.max(ep))
        inwin = [h['b'] for h in hist if h['sym'] == sym and abs(h['g'] - 0.04) < 1e-9
                 and h.get('t') is not None and t0 <= int(h['t']) <= t1]
        r = rec.get(sym, {}).get('0.04')
        same = r is not None and abs(b / r - 1) <= lib.recorded_tol(r) + 1e-12
        if len(set(inwin + [b])) > 1:
            status = 'FAIL'
            detail.append(f'{sym}: 4% barrier changed inside the window {sorted(set(inwin + [b]))}; split with --start/--end')
        elif same:
            detail.append(f'{sym}: {b!r} = recorded ({src})')
        else:
            # the latest quote up to 1 h into the window must already show b (an older quote may show the old b)
            early = sorted((h for h in hist if h['sym'] == sym and abs(h['g'] - 0.04) < 1e-9 and h.get('t') is not None
                            and int(h['t']) <= t0 + 3600), key=lambda h: int(h['t']))
            if early and early[-1]['b'] == b:
                detail.append(f'{sym}: {b!r} differs from recorded {r} but is constant over the window')
            else:
                status = 'FAIL'
                detail.append(f'{sym}: {b!r} differs from recorded {r}; change time unknown, split the analysis')
    return {'status': status, 'detail': '; '.join(detail)}


def run_analyze(args, outdir):
    print('\n== analyze')
    bars, spots, pips, hist, recs = {}, {}, {}, [], []
    src = []
    d = args.from_dir
    lad_path = args.ladder or (os.path.join(d, 'ladder.json') if d and os.path.exists(os.path.join(d, 'ladder.json')) else None)
    if lad_path:
        b_, s_, p_, h_ = ladder_inputs(read_json(lad_path))
        bars.update(b_)
        spots.update(s_)
        pips.update(p_)
        hist += h_
        src.append('ladder')
    if d and os.path.exists(os.path.join(d, 'quotes.jsonl')):
        b_, s_, h_ = quotes_log_inputs(os.path.join(d, 'quotes.jsonl'))
        for sym, v in b_.items():
            bars.setdefault(sym, {}).update(v)
        for sym, v in s_.items():
            spots.setdefault(sym, {}).update(v)
            pips.setdefault(sym, next(iter(v.values()))[1])
        hist += h_
        src.append('watcher quotes')
    series = {}
    if d:
        series.update(load_tick_dir(d, pips))
    if args.local_json:
        series.update(load_local_json(args.local_json, outdir))
    over = parse_barriers(args.barrier)
    for sym, v in over.items():
        bars.setdefault(sym, {}).update(v)
    if over:
        src.append('--barrier')
    if args.fill_lawA:
        prereg = lib.load_prereg()
        for sym, v in bars.items():
            N = lib.sym_N(sym)
            if N and 0.04 in v:
                for g in lib.RATES:
                    r = lib.law_a_ratio(N, g, prereg)
                    if g not in v and r:
                        v[g] = v[0.04] * r
                        print(f'  {sym} g={g}: barrier filled from law A: {v[g]:.6g}')
    if d:                                   # oracle on the whole archive, before any --symbols/--start/--end cut
        recs += watcher_snapshots(d, series)
    if args.symbols:
        series = {k: v for k, v in series.items() if k in args.symbols}
    if args.start or args.end:
        for sym in list(series):
            ep, px, pip = series[sym]
            m = np.ones(len(ep), bool)
            if args.start:
                m &= ep >= args.start
            if args.end:
                m &= ep <= args.end
            series[sym] = (ep[m], px[m], pip)
    series = {k: v for k, v in series.items() if len(v[0]) > 100}
    if not series:
        raise SystemExit('analyze: no tick series found (use --from-dir or --local-json)')
    # oracle records
    oracle_files = list(args.oracle or [])
    if d and not oracle_files:              # explicit --oracle files replace the ones saved in --from-dir
        oracle_files += sorted(glob.glob(os.path.join(glob.escape(d), 'oracle_align*.json')))
    for f in oracle_files:
        recs += read_json(f).get('records', [])
    a7 = a7_status(bars, hist, series, '+'.join(src) or 'none')
    print(f"  series: {', '.join(f'{k} ({len(v[0])})' for k, v in sorted(series.items()))}")
    print(f"  barriers from {' + '.join(src) or 'nowhere'}; oracle records {len(recs)}")
    A, lines = lib.run_analysis(series, bars, spots=spots, oracle_records=recs, a7_info=a7,
                                reps_ticks=args.reps_ticks, reps_model=args.reps_model)
    A['inputs'] = {'from_dir': d, 'local_json': args.local_json, 'barrier_sources': src, 'barriers': bars,
                   'oracle_files': oracle_files, 'start': args.start, 'end': args.end}
    write_json(os.path.join(outdir, 'analysis.json'), A)
    with open(os.path.join(outdir, 'summary.txt'), 'x') as fh:
        fh.write('\n'.join(lines) + '\n')
    print(f"  wrote {os.path.join(outdir, 'summary.txt')}")
    return A


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('cmd', choices=['ladder', 'collect', 'oracle', 'analyze', 'all'])
    ap.add_argument('--outdir', help='default ../results/accu_phase_<UTC>/')
    ap.add_argument('--symbols', nargs='+', help='ladder/collect/analyze symbols (default: spec lists)')
    ap.add_argument('--oracle-symbols', nargs='+', help=f'default {" ".join(ORACLE_SYMBOLS)}')
    ap.add_argument('--ticks', type=int, help='collect: override every per-symbol tick target')
    ap.add_argument('--oracle-ticks', type=int, default=20000, help='ticks fetched for the oracle alignment')
    ap.add_argument('--repeat', type=int, default=1, help='ladder: rounds (barrier-vs-spot series)')
    ap.add_argument('--interval', type=float, default=600, help='ladder: seconds between rounds')
    ap.add_argument('--from-dir', help='collect/oracle/analyze: reuse an earlier outdir or the watcher outdir')
    ap.add_argument('--local-json', nargs='+', help='analyze: repo json.gz tick files (converted to npz)')
    ap.add_argument('--barrier', nargs='+', action='extend', help='analyze: SYM=g:b overrides, e.g. CRASH500=0.04:4.7141e-6')
    ap.add_argument('--fill-lawA', action='store_true', help='analyze: fill missing rates from b_4%% x law-A ratio')
    ap.add_argument('--ladder', help='analyze: ladder.json to take barriers/spots from')
    ap.add_argument('--oracle', nargs='+', help='analyze: oracle_align*.json files to use instead of those in --from-dir')
    ap.add_argument('--start', type=int, help='analyze: first epoch (split at a barrier change)')
    ap.add_argument('--end', type=int, help='analyze: last epoch')
    ap.add_argument('--reps-ticks', type=int, help=f"bootstrap reps for tick statistics (default {lib.THRESH['boot_ticks']})")
    ap.add_argument('--reps-model', type=int, help=f"bootstrap reps for model refits (default {lib.THRESH['boot_model']})")
    args = ap.parse_args(argv)
    outdir = args.outdir or os.path.normpath(os.path.join(HERE, '..', 'results', f'accu_phase_{utc_tag()}'))
    os.makedirs(outdir, exist_ok=True)
    print(f'accu_phase_decide {args.cmd}  outdir {outdir}  (read-only: proposals and tick history only)')
    t0 = time.time()
    if args.cmd == 'ladder':
        run_ladder(args, outdir)
    elif args.cmd == 'collect':
        run_collect(args, outdir)
    elif args.cmd == 'oracle':
        run_oracle(args, outdir)
    elif args.cmd == 'analyze':
        run_analyze(args, outdir)
    else:
        run_ladder(args, outdir)
        s1 = oracle_snapshot(args, outdir, 'snap1')
        run_collect(args, outdir)
        s2 = oracle_snapshot(args, outdir, 'snap2')
        run_oracle(args, outdir, snaps=[s1, s2])
        args.from_dir = outdir
        run_analyze(args, outdir)
    print(f'done in {time.time() - t0:.0f}s -> {outdir}')
    return outdir


if __name__ == '__main__':
    main()
