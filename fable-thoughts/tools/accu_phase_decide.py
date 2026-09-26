#!/usr/bin/env python3
"""accu_phase_decide.py -- read-only GO / KILL / INCONCLUSIVE decision for the phase-gated Crash ACCU lead.

THE CLAIM UNDER TEST (ACCU_PHASE_LATTICE.md)
On CRASH1000 and CRASH500 the knockout compares an integer-pip move with a continuous barrier
w = b*P_prev, so per-tick survival is a sawtooth in the phase of w. At any growth rate whose level
K = floor(w) is low enough, G = (1+g)*P(stay) is above 1 while the phase is in [0.05, 0.125).
Pick whichever g is in band at the current spot. The criteria below were fixed before any fresh data.

SUBCOMMANDS (read-only: proposals and tick history, no buy or sell anywhere)
  ladder   barrier ladder, 14 Boom/Crash + R_100/1HZ100V/1HZ10V x g = 1-5%, stake check; L1-L4. Every cell
           is quoted twice: on an authenticated DEMO connection (DERIV_TOKEN) -- the barrier a logged-in
           account is sold, which is tighter than the public one on some symbols and is what is analysed --
           and on the public connection (b_public, diagnostic only)
  collect  tick history -> ticks/<SYM>.npz (600k CRASH1000/500, 300k the rest, 150k on N=50); public
  oracle   ticks_stayed_in snapshots (8 symbols x 5 rates), the last 20k ticks, rule alignment; OR-1..3; public
  analyze  S1-S7, D, M, C1-C5 and the verdict on the authenticated barriers -> analysis.json, summary.txt
           (last line VERDICT: ...); when public barriers are known too, the same analysis on them is shown
           side by side (analysis_public.json; information only, the verdict uses the authenticated ones)
  all      ladder, oracle snapshot 1, collect, oracle snapshot 2, 20k-tick fetch, alignment, analyze
           (about 60-75 minutes)
ladder and all need DERIV_TOKEN set to a token with a DEMO account (the proposals are read-only; the
token is used for nothing else); history and oracle stay on the public connection.

RUN (from fable-thoughts/tools; needs websocket-client>=1.6, numpy, scipy)
  DERIV_TOKEN=<demo token> python3 accu_phase_decide.py all
  DERIV_TOKEN=<demo token> python3 accu_phase_decide.py ladder --repeat 6 --interval 600   # barrier-vs-spot series
  python3 accu_phase_decide.py analyze --from-dir ../results/accu_phase_<UTC>
  python3 accu_phase_decide.py analyze --from-dir <watcher outdir>       # ticks/<SYM>/<date>.csv archive
  python3 accu_phase_decide.py analyze --local-json ../../data/CRASH500.json.gz ../../data/CRASH1000.json.gz \\
          --barrier CRASH500=0.04:4.598554e-6 CRASH1000=0.04:2.28724e-6  # offline, repo json.gz format
          (--barrier overrides are not verified against authenticated quotes, so A7 fails without a ladder)
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


def g7(x):
    return 'n/a' if x is None else f'{x:.7g}'


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
TOKEN_MSG = ('ladder needs DERIV_TOKEN (demo) to read the barrier a logged-in account is sold; '
             'tick history stays public')


def ws_class():
    global DerivWS
    if DerivWS is None:
        from deriv_api import DerivWS as _W
        DerivWS = _W
    return DerivWS


def new_ws():
    """Public connection: tick history, pip size, oracle snapshots."""
    return ws_class()(token="", timeout=15)


def need_token():
    token = os.environ.get('DERIV_TOKEN')
    if not token:
        raise SystemExit(TOKEN_MSG)
    return token


def auth_ws():
    """Authenticated DEMO connection: ACCU proposals on the terms a logged-in account is sold (a tighter
    tick_size_barrier than the public quote on some symbols). Proposals only; nothing is ever bought."""
    client = ws_class()(token=need_token(), account_type='demo', timeout=15)
    if client.account.get('account_type') != 'demo':
        client.close()
        raise SystemExit(f"DERIV_TOKEN did not open a demo account (got {client.account.get('account_type')!r})")
    return client


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
    need_token()
    print(f'\n== ladder: {len(syms)} symbols x {len(lib.RATES)} rates, {args.repeat} round(s); '
          'authenticated (demo) quotes, public in brackets')
    rounds, pips = [], {}
    n_cells = n_diff = 0
    for rep in range(args.repeat):
        if rep:
            print(f'  sleeping {args.interval}s before round {rep + 1}')
            time.sleep(args.interval)
        rnd = {'utc': utc_tag(), 'quotes': {}}
        for sym in syms:
            ws = wa = None
            try:
                ws, wa = new_ws(), auth_ws()
                if sym not in pips:
                    pips[sym] = get_pip(ws, sym)
                qs = {}
                for g in lib.RATES:
                    q = quote(wa, sym, g)
                    time.sleep(PAUSE_PROPOSAL)
                    qp = quote(ws, sym, g)
                    time.sleep(PAUSE_PROPOSAL)
                    if q and 'b' in q:
                        q['terms'] = 'authenticated'
                        q['b_public'] = qp.get('b') if qp else None
                        qs[str(g)] = q
                rnd['quotes'][sym] = qs
                diff = sum(q['b_public'] is not None and q['b_public'] != q['b'] for q in qs.values())
                n_cells, n_diff = n_cells + len(qs), n_diff + diff
                print(f"  {sym:10s} pip {pips[sym]}  " + '  '.join(
                    f"{g}:{q['b']:.7g} (pub {g7(q['b_public'])})"
                    for g, q in qs.items()) + f'  [{diff}/{len(qs)} differ]')
            except Exception as e:
                print(f'  {sym}: {type(e).__name__}: {str(e)[:160]}')
            finally:
                for c in (ws, wa):
                    if c is not None:
                        c.close()
            time.sleep(PAUSE_SYMBOL)
        rounds.append(rnd)
    print(f'  authenticated barrier differs from the public one on {n_diff}/{n_cells} quoted cells')
    stake = {}
    for sym in ('CRASH1000', 'CRASH500'):
        if sym not in syms:
            continue
        wa = None
        try:
            wa = auth_ws()
            stake[sym] = {}
            for amt in STAKES:
                q = quote(wa, sym, 0.04, amt)
                time.sleep(PAUSE_PROPOSAL)
                stake[sym][str(amt)] = q.get('b') if q else None
            last = rounds[-1]['quotes'].get(sym, {}).get('0.04')
            stake[sym]['10'] = last.get('b') if last else None
        except Exception as e:
            print(f'  stake check {sym}: {type(e).__name__}: {str(e)[:160]}')
        finally:
            if wa is not None:
                wa.close()
    lad = {'utc': utc_tag(), 'terms': 'authenticated', 'rounds': rounds, 'pip': pips, 'stake_check': stake}
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
                row = {'round': i, 'b': b, 'b_public': q.get('b_public'), 'terms': q.get('terms', 'public'), 'spot': spot, 'spot_time': q.get('spot_time'), 'w': w, 'K': K,
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
    print('  L2  recorded barriers unchanged (authenticated quote vs the recorded value; the recorded values '
          'are public-era quotes, so the public quote is shown too)')
    l2 = []
    same = lambda b, v: b is not None and abs(b / v - 1) <= lib.recorded_tol(v) + 1e-12
    for sym, per in prereg['recorded'].items():
        for gs, v in per.items():
            if not isinstance(v, (int, float)):
                continue
            q = (der.get(sym, {}).get(gs) or [None])[-1]
            if q is None:
                l2.append({'sym': sym, 'g': gs, 'recorded': v, 'quoted': None, 'ok': None})
                print(f'      {sym:10s} g={gs}  recorded {v:.5g}  quoted n/a')
                continue
            ok, bp = same(q['b'], v), q.get('b_public')
            l2.append({'sym': sym, 'g': gs, 'recorded': v, 'quoted': q['b'], 'terms': q['terms'], 'rel': q['b'] / v - 1,
                       'ok': ok, 'public': bp, 'public_ok': same(bp, v) if bp is not None else None})
            print(f"      {sym:10s} g={gs}  recorded {v:.5g}  {q['terms']} {q['b']!r}  {'ok' if ok else 'CHANGED'}"
                  f"  (public {bp!r}{'' if bp is None else ', = recorded' if same(bp, v) else ', differs from recorded'})")
    print('  L3  barrier independent of stake (4%, authenticated)')
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


def terms_of(q):
    """'authenticated' for a quote taken on the logged-in (demo) connection; a quote without the field is
    from the public-only era (legacy ladder / watcher log)."""
    return 'authenticated' if q.get('terms') == 'authenticated' else 'public'


def ladder_inputs(lad):
    """Analysed barriers (authenticated quotes only), spots and pips from the ladder, plus the (time, b, terms)
    history of every round. A legacy ladder (public quotes, no 'terms') gives history only: its barriers
    are public ones (ladder_public_bars)."""
    bars, spots, hist = {}, {}, []
    pips = {k: v for k, v in lad.get('pip', {}).items() if v is not None}
    for rnd in lad.get('rounds', []):
        for sym, qs in rnd['quotes'].items():
            for gs, q in qs.items():
                if q.get('b') is None:
                    continue
                terms, g, t = terms_of(q), float(gs), q.get('spot_time')
                if terms == 'authenticated':
                    bars.setdefault(sym, {})[g] = q['b']
                    if q.get('b_public') is not None:
                        hist.append({'sym': sym, 'g': g, 'b': q['b_public'], 't': t, 'terms': 'public'})
                if q.get('spot') is not None and sym in pips:
                    spots.setdefault(sym, {})[g] = (float(q['spot']), pips[sym])
                hist.append({'sym': sym, 'g': g, 'b': q['b'], 't': t, 'terms': terms})
    return bars, spots, pips, hist


def ladder_public_bars(lad):
    """{sym: {g: public b}} from the last round quoting it: b_public, or b of a legacy (public) quote."""
    out = {}
    for rnd in lad.get('rounds', []):
        for sym, qs in rnd['quotes'].items():
            for gs, q in qs.items():
                b = q.get('b_public') if terms_of(q) == 'authenticated' else q.get('b')
                if b is not None:
                    out.setdefault(sym, {})[float(gs)] = float(b)
    return out


def quote_records(path):
    """Watcher quotes.jsonl rows as dicts (sym, g, b, b_public, terms, t, spot, pip). Accepts the raw
    proposal shape (contract_details.tick_size_barrier, a verbatim ws.call() response) or flat keys."""
    out = []
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
            bp = q.get('b_public')
            out.append({'sym': sym, 'g': float(g), 'b': float(b), 'terms': terms_of(q),
                        'b_public': None if bp is None else float(bp),
                        't': q.get('epoch', q.get('t', q.get('spot_time', p.get('spot_time')))),
                        'spot': q.get('spot', p.get('spot')), 'pip': q.get('pip', q.get('pip_size'))})
    return out


def quotes_log_inputs(path):
    """Watcher quotes.jsonl: latest authenticated b and spot per (sym, g) plus the full (time, b, terms)
    history. Records without 'terms' are legacy public quotes: history only (quotes_public_bars)."""
    bars, spots, hist = {}, {}, []
    for r in quote_records(path):
        sym, g = r['sym'], r['g']
        if r['terms'] == 'authenticated':
            bars.setdefault(sym, {})[g] = r['b']
            if r['b_public'] is not None:
                hist.append({'sym': sym, 'g': g, 'b': r['b_public'], 't': r['t'], 'terms': 'public'})
        if r['spot'] is not None and r['pip'] is not None:
            spots.setdefault(sym, {})[g] = (float(r['spot']), int(r['pip']))
        hist.append({'sym': sym, 'g': g, 'b': r['b'], 't': r['t'], 'terms': r['terms']})
    return bars, spots, hist


def quotes_public_bars(path):
    """{sym: {g: latest public b}} from quotes.jsonl: b_public, or b of a legacy (public) record."""
    out = {}
    for r in quote_records(path):
        b = r['b_public'] if r['terms'] == 'authenticated' else r['b']
        if b is not None:
            out.setdefault(r['sym'], {})[r['g']] = b
    return out


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
    """A7: the analysed 4% barrier is the one a logged-in account is sold (equal to the latest authenticated
    quote) and did not change over the tick window: the barrier in force at the window start (the latest
    authenticated quote at or before t0) and every authenticated quote inside it show one value. The
    pre-registered (public-era) recorded value and the public quote are reported, information only."""
    rec = lib.load_prereg()['recorded']
    detail, status = [], 'PASS'
    for sym in lib.THRESH['decision_symbols']:
        b = (bars.get(sym) or {}).get(0.04)
        if b is None or sym not in series:
            return {'status': 'NOT_EVALUATED', 'detail': f'no 4% barrier or no ticks for {sym}'}
        ep = series[sym][0]
        t0, t1 = int(np.min(ep)), int(np.max(ep))
        h4 = [h for h in hist if h['sym'] == sym and abs(h['g'] - 0.04) < 1e-9]
        timed = lambda hs: sorted((h for h in hs if h.get('t') is not None), key=lambda h: int(h['t']))
        auth = [h for h in h4 if h.get('terms') == 'authenticated']
        pub = timed(h for h in h4 if h.get('terms') != 'authenticated')
        info = f"recorded {rec.get(sym, {}).get('0.04')!r}, public {pub[-1]['b'] if pub else None!r}"
        if not auth:
            status = 'FAIL'
            detail.append(f'{sym}: {b!r} ({src}): analysed barrier not verified against authenticated terms '
                          f'(no authenticated 4% quote; {info})')
            continue
        at = timed(auth)
        latest = (at or auth)[-1]['b']
        if abs(latest / b - 1) > 1e-9:
            status = 'FAIL'
            detail.append(f'{sym}: analysed {b!r} ({src}) != latest authenticated quote {latest!r} ({info})')
            continue
        before = [h for h in at if int(h['t']) <= t0]
        inwin = {h['b'] for h in at if t0 < int(h['t']) <= t1} | ({before[-1]['b']} if before else set())
        if len(inwin | {latest}) > 1:           # a change inside the window, or after it (b not yet in force)
            inwin |= {latest}
            status = 'FAIL'
            detail.append(f'{sym}: authenticated 4% barrier changed inside the window {sorted(inwin)}; '
                          f'split with --start/--end ({info})')
            continue
        cov = any(int(h['t']) <= t1 for h in at)
        detail.append(f"{sym}: {b!r} = latest authenticated quote ({src}); authenticated history covers the window: "
                      + ('yes' if cov else 'no -- the barrier during the window is assumed, not observed') + f' ({info})')
    return {'status': status, 'detail': '; '.join(detail)}


def merge_bars(dst, src):
    for sym, v in src.items():
        dst.setdefault(sym, {}).update(v)


def fill_law_a(bars, say=print):
    prereg = lib.load_prereg()
    for sym, v in bars.items():
        N = lib.sym_N(sym)
        if N and 0.04 in v:
            for g in lib.RATES:
                r = lib.law_a_ratio(N, g, prereg)
                if g not in v and r:
                    v[g] = v[0.04] * r
                    say(f'  {sym} g={g}: barrier filled from law A: {v[g]:.6g}')


def eligible_range(R, gs):
    ks = sorted(int(k) for k, c in ((R.get('eligibility') or {}).get(gs) or {}).items() if c.get('eligible'))
    return 'none' if not ks else f'K{ks[0]}' if len(ks) == 1 else f'K{ks[0]}..K{ks[-1]}'


def side_by_side(A, P, bars, pub_bars, diff):
    """Compact comparison of the analysis on the public barriers (P) and on the authenticated ones (A)."""
    L = ['', '=== public vs authenticated barriers (verdict uses authenticated)']
    for sym in sorted(diff):
        Ra, Rp = A['symbols'].get(sym, {}), P['symbols'].get(sym, {})
        for g in sorted(diff[sym]):
            gs, g2 = str(g), f'{g:.2f}'
            L.append(f'{sym} g={g2}  b_pub {pub_bars[sym][g]!r}  b_auth {bars[sym][g]!r}')
            for band in ('[0,.125)', '[.05,.125)', '[.75,1)'):
                cell = lambda R: ((R.get('S3') or {}).get(gs) or {}).get('bands', {}).get(band)
                f = lambda c: f"n={c['n']} G={c['G']:.5f}" if c else 'n/a'
                L.append(f'   S3 {band:11s} pub {f(cell(Rp)):22s} auth {f(cell(Ra))}')
            L.append(f'   eligible K     pub {eligible_range(Rp, gs):22s} auth {eligible_range(Ra, gs)}')
            s7 = lambda R: ((R.get('S7') or {}).get(g2))
            f7 = lambda c: (f"K{c['K']} phase {c['phase']:.4f} in-band {'yes' if c['in_band'] else 'no'}"
                            f"{' ELIGIBLE' if c['eligible_now'] else ''}") if c else 'n/a'
            L.append(f'   S7             pub {f7(s7(Rp)):40s} auth {f7(s7(Ra))}')
        f6 = lambda c: (f"n={c['n']} {c['mean_ret_halfup']:+.4f} [{lib._f(c['ci99_halfup'][0], 4)}, "
                        f"{lib._f(c['ci99_halfup'][1], 4)}]") if c and c.get('n') else 'n/a'
        L.append(f"   S6 choice mean/trade 99%  pub {f6((Rp.get('S6') or {}).get('choice'))}  "
                 f"auth {f6((Ra.get('S6') or {}).get('choice'))}")
    da, dp = A.get('decision', {}), P.get('decision', {})
    fci = lambda d: f"{lib._f(d.get('point'), 5)} [{lib._f(d.get('lo'), 5)}, {lib._f(d.get('hi'), 5)}]" if d else 'n/a'
    L.append(f"pooled D        pub {fci(dp.get('D'))}  auth {fci(da.get('D'))}")
    for k in ('V1', 'V2'):
        L.append(f"pooled M {k}     pub {fci((dp.get('M') or {}).get(k))}  auth {fci((da.get('M') or {}).get(k))}")
    f4 = lambda c: f"{c['contrast']:+.5f} z {c['z']:.2f}" if c else 'n/a'
    L.append(f"C4 at 4%        pub {f4(dp.get('C4'))}  auth {f4(da.get('C4'))}")
    L.append(f"verdict         pub {P['verdict']['label']} (information only)  auth {A['verdict']['label']}")
    return L


def run_analyze(args, outdir):
    print('\n== analyze')
    bars, pub, spots, pips, hist, recs = {}, {}, {}, {}, [], []
    src = []
    d = args.from_dir
    lad_path = args.ladder or (os.path.join(d, 'ladder.json') if d and os.path.exists(os.path.join(d, 'ladder.json')) else None)
    if lad_path:
        lad = read_json(lad_path)
        b_, s_, p_, h_ = ladder_inputs(lad)
        merge_bars(bars, b_)
        merge_bars(pub, ladder_public_bars(lad))
        spots.update(s_)
        pips.update(p_)
        hist += h_
        src.append('authenticated ladder' if b_ else 'public ladder (legacy)')
    if d and os.path.exists(os.path.join(d, 'quotes.jsonl')):
        qpath = os.path.join(d, 'quotes.jsonl')
        b_, s_, h_ = quotes_log_inputs(qpath)
        merge_bars(bars, b_)
        merge_bars(pub, quotes_public_bars(qpath))
        for sym, v in s_.items():
            spots.setdefault(sym, {}).update(v)
            pips.setdefault(sym, next(iter(v.values()))[1])
        hist += h_
        src.append('authenticated watcher quotes' if b_ else 'public watcher quotes (legacy)')
    # a symbol with public quotes only (legacy inputs) falls back to them; A7 then fails on it
    legacy = sorted(s for s in pub if s not in bars)
    for sym in legacy:
        bars[sym] = dict(pub[sym])
    if legacy:
        print(f"  no authenticated quote for {', '.join(legacy)}: analysing the PUBLIC (legacy) barrier; "
              'A7 fails on a decision symbol')
    series = {}
    if d:
        series.update(load_tick_dir(d, pips))
    if args.local_json:
        series.update(load_local_json(args.local_json, outdir))
    pub_run = {s: dict(v) for s, v in bars.items()}        # public barriers where known, else the analysed ones
    merge_bars(pub_run, pub)
    over = parse_barriers(args.barrier)
    merge_bars(bars, over)
    for sym, v in over.items():
        pub_run.setdefault(sym, {})
        for g, b in v.items():
            pub_run[sym].setdefault(g, b)
    if over:
        src.append('override')
    if args.fill_lawA:
        fill_law_a(bars)
        fill_law_a(pub_run, lambda s: None)
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
    kw = dict(spots=spots, oracle_records=recs, a7_info=a7, reps_ticks=args.reps_ticks, reps_model=args.reps_model)
    A, lines = lib.run_analysis(series, bars, **kw)
    A['inputs'] = {'from_dir': d, 'local_json': args.local_json, 'barrier_sources': src, 'terms': 'authenticated',
                   'barriers': bars, 'public_barriers': pub, 'legacy_public_fallback': legacy,
                   'oracle_files': oracle_files, 'start': args.start, 'end': args.end}
    diff = {s: [g for g, b in bars.get(s, {}).items() if pub_run.get(s, {}).get(g, b) != b] for s in series}
    diff = {s: v for s, v in diff.items() if v}
    block = []
    if diff:
        P, _ = lib.run_analysis(series, pub_run, log=lambda s: None, **kw)
        P['inputs'] = dict(A['inputs'], terms='public', barriers=pub_run)
        block = side_by_side(A, P, bars, pub_run, diff)
        A['public_vs_authenticated'] = {'cells': diff, 'public_verdict': P['verdict']['label'],
                                        'lines': block[2:]}
        write_json(os.path.join(outdir, 'analysis_public.json'), P)
        for line in block:
            print(line)
        print(lines[-1])
    elif pub:
        print('  public barriers equal the analysed ones on every analysed cell: no side-by-side')
    write_json(os.path.join(outdir, 'analysis.json'), A)
    with open(os.path.join(outdir, 'summary.txt'), 'x') as fh:
        fh.write('\n'.join(lines[:-1] + block + lines[-1:]) + '\n')
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
    if args.cmd in ('ladder', 'all'):
        need_token()                        # before any file or connection
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
