#!/usr/bin/env python3
"""accu_phase_watch.py -- read-only live watcher for the phase-gated Crash ACCU lead (spec 4.3).

WHY A WATCHER
Band occupancy depends on spot, and the history endpoint only reaches back about 4.6 days, so the
2-3 weeks of ticks the decision needs (about 270k in-band ticks) must be archived as they happen.
The watcher also logs every quote, so a barrier change (criterion A7) is caught with its time, and it
saves ticks_stayed_in snapshots for the knockout-rule oracle (OR-1..3).

LOOP (one persistent public DerivWS(token=""); on any exception: close, sleep 5 s, reconnect)
  quotes   every --quote-interval s: an ACCU proposal per (sym, g) -> quotes.jsonl. Alerts:
           IN_BAND enter/exit (cell G_min >= 1.0005 and phase in the band), BARRIER_CHANGE (b differs
           from the previous quote, or from the pre-registered value at first sight), MAXTICKS_CHANGE
  oracle   every --oracle-interval s: the same round, also saving each full proposal to
           oracle/<SYM>_<g>_<last_tick_epoch>.json
  ticks    every --tick-interval s: the last 1500 ticks -> ticks/<SYM>/<YYYY-MM-DD>.csv (epoch,quote),
           epochs newer than the archive only. A gap is refetched with target gap + 1500 (at most
           20000; a refetch cut short by a server error is retried for up to 3 rounds); what still
           cannot be filled is logged as GAP_UNFILLED
  hourly   pip_size of every symbol (PIP_CHANGE); sigma_pips of R_100 / 1HZ100V / 1HZ10V (VOL_REGIME
           below 9.6)
  analyze  every 24 h: accu_phase_decide.py analyze --from-dir <outdir> on the whole archive, run in
           the background; its VERDICT line is appended to summary.log
Alerts go to alerts.jsonl and stdout; every printed line also goes to watch.log. Restarting on the same
--outdir resumes: archive ends, first-seen barriers, max ticks, pips and band states are read back.

RUN (from fable-thoughts/tools; needs websocket-client>=1.6, numpy, scipy; no token, no orders)
  python3 accu_phase_watch.py --hours 0                         # forever; Ctrl+C stops cleanly
  python3 accu_phase_watch.py --cells ../results/accu_phase_<UTC>/analysis.json
Default --outdir is ../results/accu_phase_watch/ (fixed, so a restart continues the same archive).
Send back weekly: summary.log, alerts.jsonl, quotes.jsonl (barriers and spots: analyze needs it), and
zips of oracle/ and ticks/. The repo .gitignore skips *.log: if you send them through git, add
summary.log (and watch.log) with `git add -f`.
"""
import argparse, contextlib, datetime as dt, glob, io, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import accu_phase_lib as lib
import accu_phase_decide as apd
from derivfetch import IntegrityError, fetch_ticks, native_interval

DerivWS = None                  # imported on first connect (tests substitute a fake)
PAUSE_PROPOSAL = 0.15
PAUSE_SYMBOL = 1.0
RECONNECT_S = 5
RETRY_S = 60                    # a task that raised runs again after min(its interval, 60 s)
LOOP_S = 1.0
HOURLY_S = 3600
TICK_FETCH = 1500
TICK_REFILL_MAX = 20000
REFILL_RETRIES = 3              # rounds a refill cut short by a server error is retried before GAP_UNFILLED
ANALYZE_MAX_S = 4 * 3600        # a background analyze running longer is stopped (the archive waits for it)
VOL_SYMBOLS = ('R_100', '1HZ100V', '1HZ10V')
VOL_SIGMA_PIPS_MIN = 9.6
DECIDE = os.path.join(HERE, 'accu_phase_decide.py')
TASKS = ('hourly', 'oracle', 'quotes', 'ticks', 'analyze')   # run in this order when due together
UTC = dt.timezone.utc


def utc_str(t):
    return dt.datetime.fromtimestamp(int(t), UTC).strftime('%Y-%m-%d %H:%M:%S')


# ------------------------------------------------------------------ tick archive
def day_file(tdir, sym, epoch):
    return os.path.join(tdir, sym, dt.datetime.fromtimestamp(int(epoch), UTC).strftime('%Y-%m-%d') + '.csv')


def archive_last_epoch(sdir):
    """Newest archived epoch of one symbol, None when empty. A partial last line (a hard kill mid-write)
    is cut off first so the archive stays parseable."""
    for f in sorted(glob.glob(os.path.join(glob.escape(sdir), '*.csv')), reverse=True):   # '[' in --outdir
        with open(f, 'rb+') as fh:
            data = fh.read()
            if data and not data.endswith(b'\n'):
                data = data[:data.rfind(b'\n') + 1]
                fh.truncate(len(data))
        eps = [int(l.split(b',')[0]) for l in data.split(b'\n') if l[:1].isdigit()]
        if eps:
            return max(eps)
    return None


def append_archive(tdir, sym, ep, px, pip):
    """Append ticks (all newer than the archive) to the per-UTC-day CSV files, prices at pip decimals."""
    ep = np.asarray(ep, np.int64)
    os.makedirs(os.path.join(tdir, sym), exist_ok=True)
    day = ep // 86400
    for d in np.unique(day):
        m = day == d
        path = day_file(tdir, sym, int(d) * 86400)
        head = '' if os.path.exists(path) and os.path.getsize(path) > 0 else 'epoch,quote\n'
        rows = ''.join(f'{int(e)},{p:.{pip}f}\n' for e, p in zip(ep[m], np.asarray(px)[m]))
        with open(path, 'a') as fh:
            fh.write(head + rows)
    return int(len(ep))


def load_cells(path):
    """--cells analysis.json: symbols with an eligible cell, their fitted step-law parameters, the band D used."""
    A = apd.read_json(path)
    syms, models = [], {}
    for sym, R in (A.get('symbols') or {}).items():
        el = R.get('eligibility') or {}
        if any(c.get('eligible') for cells in el.values() for c in cells.values()):
            syms.append(sym)
        mp = (R.get('S1') or {}).get('model')
        if mp and mp.get('m') and mp.get('lam') is not None:
            models[sym] = mp
    return syms, models, tuple(A.get('band_used') or lib.THRESH['band_core'])


# ------------------------------------------------------------------ watcher
class Watcher:
    def __init__(self, args, clock=time.time, sleep=time.sleep):
        self.a = args
        self.clock, self.sleep = clock, sleep
        self.outdir = os.path.abspath(args.outdir)
        self.tdir = os.path.join(self.outdir, 'ticks')
        for sub in ('ticks', 'oracle'):
            os.makedirs(os.path.join(self.outdir, sub), exist_ok=True)
        self.law = lib.load_frozen()
        self.recorded = {(s, float(g)): v for s, per in lib.load_prereg()['recorded'].items()
                         for g, v in per.items() if isinstance(v, (int, float))}
        self.models, self.band, extra = {}, tuple(lib.THRESH['band_core']), []
        if args.cells:
            extra, self.models, self.band = load_cells(args.cells)
        self.symbols = list(dict.fromkeys(list(args.symbols) + extra))
        self.rates = [float(g) for g in args.rates]
        self.vol_symbols = list(args.vol_symbols)
        self.ws = None
        self.proc = None                        # the running background analyze: Popen, dir, stdout file, start
        self.first_b, self.last_b, self.last_maxt, self.in_band = {}, {}, {}, {}
        self.pip, self.vol_below, self.last_epoch, self.cell_cache = {}, {}, {}, {}
        self.refill_tries = {}
        self.n = dict(quotes=0, alerts=0, ticks=0, oracle=0, errors=0, connects=0, analyses=0)
        self.intervals = {'hourly': HOURLY_S, 'oracle': args.oracle_interval, 'quotes': args.quote_interval,
                          'ticks': args.tick_interval, 'analyze': args.analyze_interval}
        now = self.clock()
        self.due = {k: now for k in TASKS}
        self.due['analyze'] = now + args.analyze_interval
        self.restore()

    # ---------------------------------------------------------------- output
    def log(self, msg):
        t = self.clock()
        try:                                    # best effort: the error handlers log too, so this must not raise
            print(f'[{utc_str(t)[11:]}] {msg}', flush=True)
        except (OSError, ValueError):           # stdout gone: closed terminal, dead pipe, full disk
            pass
        try:
            with open(os.path.join(self.outdir, 'watch.log'), 'a') as fh:
                fh.write(f'{utc_str(t)} {msg}\n')
        except OSError:
            pass

    def append_jsonl(self, name, rec):
        with open(os.path.join(self.outdir, name), 'a') as fh:
            fh.write(json.dumps(lib.jsonable(rec)) + '\n')

    def alert(self, kind, **kw):
        t = self.clock()
        self.append_jsonl('alerts.jsonl', {'utc': utc_str(t), 't': round(t, 3), 'type': kind, **kw})
        self.n['alerts'] += 1
        self.log(f'ALERT {kind} ' + ' '.join(f'{k}={v}' for k, v in kw.items() if v is not None))

    def echo(self, fn, *a):
        """Run an accu_phase_decide helper; what it prints (server errors) goes to the log too."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                return fn(*a)
        finally:
            for line in buf.getvalue().splitlines():
                self.log(line.strip())

    # ---------------------------------------------------------------- state
    def restore(self):
        """Resume from an earlier run in the same outdir (the logs are append-only, so they are the state)."""
        path = os.path.join(self.outdir, 'quotes.jsonl')
        nq = 0
        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    try:
                        q = json.loads(line)
                        key = (q['sym'], float(q['g']))
                    except (ValueError, KeyError, TypeError):
                        continue
                    nq += 1
                    if q.get('b') is not None:
                        self.first_b.setdefault(key, q['b'])
                        self.last_b[key] = q['b']
                    if q.get('maximum_ticks') is not None:
                        self.last_maxt[key] = q['maximum_ticks']
                    if 'in_band' in q:
                        self.in_band[key] = bool(q['in_band'])
                    if q.get('pip') is not None:
                        self.pip[q['sym']] = int(q['pip'])
        for sym in self.symbols:
            self.last_epoch[sym] = archive_last_epoch(os.path.join(self.tdir, sym))
        slog = os.path.join(self.outdir, 'summary.log')
        if os.path.exists(slog) and self.intervals['analyze'] > 0:
            self.due['analyze'] = os.path.getmtime(slog) + self.intervals['analyze']
        if nq or any(v is not None for v in self.last_epoch.values()):
            self.log(f'resumed {self.outdir}: {nq} logged quotes; archive ends ' + ', '.join(
                f"{s} {utc_str(e) if e else 'empty'}" for s, e in self.last_epoch.items()))

    def model(self, sym):
        """Step-law parameters for G_min: the --cells analysis' S1 fit, else nominal m = 1e-3/N and
        lambda = 1/N on standard Boom/Crash; None on vol indices and on N-series (m would be a guess)."""
        if sym in self.models:
            return self.models[sym]
        if lib.family(sym) == 'vol' or lib.is_nseries(sym):
            return None
        return lib.model_params(sym)

    def cells(self, sym, g, b, K):
        """Frozen-law eligibility (lib.eligibility, band [.05,.125), quoted b) of levels K-5..K+5."""
        mp = self.model(sym)
        if mp is None:
            return None
        out = {}
        for k in range(max(1, K - 5), K + 6):
            key = (sym, g, b, k)
            if key not in self.cell_cache:
                self.cell_cache[key] = lib.eligibility({g: b}, {g: [k]}, mp, self.law)[g][k]
            out[k] = self.cell_cache[key]
        return out

    # ---------------------------------------------------------------- network
    def connect(self):
        global DerivWS
        if DerivWS is None:
            from deriv_api import DerivWS as _W
            DerivWS = _W
        self.ws = DerivWS(token="", timeout=15)
        self.n['connects'] += 1
        self.log(f"connected (public, read-only) #{self.n['connects']}")

    def drop(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None

    def check_pip(self, sym):
        """One-tick history request: pip_size (PIP_CHANGE on a change) and the last price."""
        r = self.echo(apd.request, self.ws, {"ticks_history": sym, "count": 1, "end": "latest", "style": "ticks"},
                      f'{sym} pip_size')
        self.sleep(PAUSE_PROPOSAL)
        if r.get('pip_size') is None:
            return self.pip.get(sym), None
        pip, old = int(r['pip_size']), self.pip.get(sym)
        if old is not None and old != pip:
            self.alert('PIP_CHANGE', sym=sym, previous=old, pip=pip)
        self.pip[sym] = pip
        prices = (r.get('history') or {}).get('prices') or []
        return pip, (float(prices[-1]) if prices else None)

    # ---------------------------------------------------------------- 1-2. quotes and oracle snapshots
    def quote_round(self, save_oracle=False):
        marks = []
        for sym in self.symbols:
            parts = []
            for g in self.rates:
                q = self.echo(apd.quote, self.ws, sym, g)
                self.sleep(PAUSE_PROPOSAL)
                if not q or q.get('b') is None:
                    continue
                st = self.on_quote(sym, g, q)
                if save_oracle:
                    self.save_oracle(sym, g, q)
                if 'K' in st:
                    parts.append(f"{g * 100:.0f}%:K{st['K']}/{st['phase']:.3f}{'*' if st.get('in_band') else ''}")
            marks.append(f"{sym} {' '.join(parts) or 'no quote'}")
        self.log(('oracle+quotes ' if save_oracle else 'quotes ') + ' | '.join(marks))

    def on_quote(self, sym, g, q):
        b, spot = q['b'], q.get('spot')
        pip = self.pip.get(sym)
        if pip is None:
            pip = self.check_pip(sym)[0]
        st = self.cell_state(sym, g, b, spot, pip)
        pip = self.pip.get(sym, pip)
        t = self.clock()
        self.append_jsonl('quotes.jsonl', {
            'utc': utc_str(t), 't': round(t, 3), 'epoch': q.get('spot_time'), 'sym': sym, 'g': g, 'b': b,
            'b_repr': q.get('b_repr'), 'spot': spot, 'pip': pip, 'maximum_ticks': q.get('maximum_ticks'),
            'barrier_spot_distance': q.get('barrier_spot_distance'), 'last_tick_epoch': q.get('last_tick_epoch'), **st})
        self.n['quotes'] += 1
        self.check_barrier(sym, g, b, q)
        self.check_maxticks(sym, g, q)
        self.check_band(sym, g, b, q, pip, st)
        return st

    def cell_state(self, sym, g, b, spot, pip):
        """w, K, phase at the quoted spot (exact at integer w), and the cell's G_min / eligibility / in-band."""
        if spot is None or pip is None:
            return {}
        try:
            P = lib.to_pips([float(spot)], pip)
        except AssertionError:                  # spot off the pip lattice: pip_size changed, re-read it
            pip = self.check_pip(sym)[0]
            try:
                P = lib.to_pips([float(spot)], pip)
            except (AssertionError, TypeError):
                self.log(f'{sym}: spot {spot} is not on the 1e-{pip} lattice; band state skipped')
                return {}
        w, K, ph = lib.barrier_levels(P, b)
        K, ph = int(K[0]), float(ph[0])
        out = {'w': float(w[0]), 'K': K, 'phase': ph}
        cells = self.cells(sym, g, b, K)
        if cells is not None:
            c = cells.get(K, {})
            lo, hi = self.band
            out.update(gmin=c.get('gmin'), eligible=bool(c.get('eligible')),
                       in_band=bool(c.get('eligible') and lo <= ph < hi))
        return out

    def check_barrier(self, sym, g, b, q):
        key = (sym, g)
        rec = self.recorded.get(key)
        off_rec = rec is not None and abs(b / rec - 1) > lib.recorded_tol(rec) + 1e-12
        common = dict(sym=sym, g=g, b=b, b_repr=repr(b), epoch=q.get('spot_time'), frozen=rec)
        if key not in self.first_b:
            self.first_b[key] = b
            if off_rec:
                self.alert('BARRIER_CHANGE', **common, previous=None, first_seen=b,
                           reason='first quote differs from the pre-registered value (A7: split the analysis)')
        elif b != self.last_b.get(key):
            self.alert('BARRIER_CHANGE', **common, previous=self.last_b.get(key), first_seen=self.first_b[key],
                       equals_first_seen=b == self.first_b[key], equals_frozen=None if rec is None else not off_rec,
                       reason='b changed (A7: split the analysis at this epoch with --start/--end)')
        self.last_b[key] = b

    def check_maxticks(self, sym, g, q):
        mt = q.get('maximum_ticks')
        if mt is None:
            return
        prev = self.last_maxt.get((sym, g))
        if prev is not None and int(mt) != int(prev):
            self.alert('MAXTICKS_CHANGE', sym=sym, g=g, previous=prev, maximum_ticks=mt, epoch=q.get('spot_time'))
        self.last_maxt[(sym, g)] = mt

    def check_band(self, sym, g, b, q, pip, st):
        if 'in_band' not in st:
            return
        key = (sym, g)
        prev, now = self.in_band.get(key), st['in_band']
        self.in_band[key] = now
        if now == bool(prev):
            return
        ne = lib.next_entry(sym, g, b, float(q['spot']), pip, self.cells(sym, g, b, st['K']))
        self.alert('IN_BAND', sym=sym, g=g, state='enter' if now else 'exit', K=st['K'], phase=round(st['phase'], 5),
                   gmin=round(st['gmin'], 6) if st.get('gmin') is not None else None, spot=q['spot'],
                   band=list(self.band), epoch=q.get('spot_time'), next_eligible_entry=ne['next_eligible_entry'])

    def save_oracle(self, sym, g, q):
        if q.get('ticks_stayed_in') is None:
            return
        ep = q.get('last_tick_epoch') or q.get('spot_time')
        path = os.path.join(self.outdir, 'oracle', f'{sym}_{g}_{ep}.json')
        try:
            with open(path, 'x') as fh:
                json.dump(lib.jsonable({'sym': sym, 'g': g, 'pip': self.pip.get(sym), 'saved_utc': utc_str(self.clock()),
                                        'proposal': q['proposal']}), fh)
            self.n['oracle'] += 1
        except FileExistsError:
            pass

    # ---------------------------------------------------------------- 3. tick archive
    def tick_archive(self):
        notes = []
        for sym in self.symbols:
            try:
                notes.append(self.archive_symbol(sym))
            except IntegrityError as e:         # the server returned no ticks for this symbol
                notes.append(f'{sym} none ({e})')
            self.sleep(PAUSE_SYMBOL)
        self.log('ticks ' + ', '.join(notes))

    def archive_symbol(self, sym):
        last = self.last_epoch.get(sym)
        ep, px, pip = fetch_ticks(self.ws, sym, TICK_FETCH, verbose=False, strict=False)
        iv = native_interval(ep)
        note = ''
        if last is not None and ep[0] > last + iv:
            gap = int((ep[0] - last) // iv) - 1
            target = min(gap + TICK_FETCH, TICK_REFILL_MAX)
            ep, px, pip = fetch_ticks(self.ws, sym, target, verbose=False, strict=False)
            tries = self.refill_tries.get(sym, 0)
            # the refetch stopped short of its target (a server error part-way): append nothing, so the gap
            # stays open, and refill again next round; after REFILL_RETRIES rounds report it as unfilled
            if ep[0] > last + iv and len(ep) < target and tries < REFILL_RETRIES:
                self.refill_tries[sym] = tries + 1
                return (f'{sym} +0 (refill stopped at {len(ep)}/{target} ticks; gap kept open, '
                        f'retry {tries + 1}/{REFILL_RETRIES} next round)')
            self.refill_tries.pop(sym, None)
            if ep[0] > last + iv:
                miss = int((ep[0] - last) // iv) - 1
                self.alert('GAP_UNFILLED', sym=sym, last_archived=int(last), first_fetched=int(ep[0]),
                           missing_ticks=miss, from_utc=utc_str(last), to_utc=utc_str(ep[0]), refetch_target=target)
                note = f' (GAP of {miss} ticks unfilled)'
            else:
                note = f' (gap of {gap} ticks refilled)'
        new = ep > last if last is not None else np.ones(len(ep), bool)
        try:
            n = append_archive(self.tdir, sym, ep[new], px[new], pip)
        except Exception:
            # a write failed part-way (e.g. the 2nd day file of a batch across midnight is locked): resume
            # from what reached the disk, so the retry does not append the first day's rows again
            self.last_epoch[sym] = archive_last_epoch(os.path.join(self.tdir, sym))
            raise
        if n:
            self.last_epoch[sym] = int(ep[new][-1])
        self.n['ticks'] += n
        return f'{sym} +{n}{note}'

    # ---------------------------------------------------------------- 4. hourly pip and vol checks
    def hourly(self):
        notes = []
        for sym in dict.fromkeys(self.symbols + self.vol_symbols):
            pip, last = self.check_pip(sym)
            note = f'{sym} pip {pip}'
            sp = lib.vol_sigma_pips(sym, last, pip) if (sym in self.vol_symbols and pip is not None
                                                        and last is not None) else None
            if sp is not None:
                note += f' sigma_pips {sp:.2f}'
                below, prev = sp < VOL_SIGMA_PIPS_MIN, self.vol_below.get(sym)
                if below != bool(prev):
                    self.alert('VOL_REGIME', sym=sym, state='below' if below else 'above', sigma_pips=round(sp, 3),
                               threshold=VOL_SIGMA_PIPS_MIN, spot=last, pip=pip)
                self.vol_below[sym] = below
            notes.append(note)
        self.log('hourly ' + ', '.join(notes))

    # ---------------------------------------------------------------- 5. daily analyze
    def start_analyze(self):
        t = self.clock()
        adir = os.path.join(self.outdir, 'analyze', dt.datetime.fromtimestamp(int(t), UTC).strftime('%Y%m%dT%H%M%SZ'))
        os.makedirs(adir)
        cmd = [sys.executable, DECIDE, 'analyze', '--from-dir', self.outdir, '--outdir', adir]
        for flag, v in (('--reps-ticks', self.a.reps_ticks), ('--reps-model', self.a.reps_model)):
            if v:
                cmd += [flag, str(v)]
        out = open(os.path.join(adir, 'stdout.txt'), 'x')
        self.proc = {'p': subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=HERE), 'dir': adir,
                     'out': out, 't0': t}
        self.log(f'analyze started in the background -> {adir} (tick archiving waits until it ends)')

    def poll_analyze(self, wait=False):
        """When the background analyze has finished, append its VERDICT line to summary.log (stopping it
        first if it has run longer than ANALYZE_MAX_S)."""
        if self.proc is None:
            return
        adir = self.proc['dir']
        rc = self.proc['p'].wait() if wait else self.proc['p'].poll()
        note = ''
        if rc is None:
            if self.clock() - self.proc['t0'] <= ANALYZE_MAX_S:
                return
            self.stop_analyze()
            rc, note = 'timeout', f' after {ANALYZE_MAX_S / 3600:g} h'
        else:
            self.proc['out'].close()
            self.proc = None
        self.n['analyses'] += 1
        line = f"analyze rc={rc}{note}, no verdict (see {os.path.join(adir, 'stdout.txt')})"
        spath = os.path.join(adir, 'summary.txt')
        if os.path.exists(spath):
            with open(spath) as fh:
                v = [l.strip() for l in fh if l.startswith('VERDICT:')]
            if v:
                line = v[-1]
        dtxt = ''
        try:
            D = apd.read_json(os.path.join(adir, 'analysis.json')).get('decision', {}).get('D') or {}
            if D:
                dtxt = (f"D {lib._f(D.get('point'), 5)} [{lib._f(D.get('lo'), 5)}, {lib._f(D.get('hi'), 5)}] "
                        f"n_inband {D.get('n')} blocks {D.get('n_blocks')} | ")
        except (OSError, ValueError):
            pass
        entry = f"{utc_str(self.clock())} {os.path.relpath(adir, self.outdir)} {dtxt}{line}"
        with open(os.path.join(self.outdir, 'summary.log'), 'a') as fh:
            fh.write(entry + '\n')
        self.log('summary.log <- ' + entry)

    def stop_analyze(self):
        if self.proc is None:
            return
        p = self.proc['p']
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        self.proc['out'].close()
        self.log(f"analyze in {self.proc['dir']} stopped")
        self.proc = None

    # ---------------------------------------------------------------- loop
    def step(self):
        """Run every task that is due. An exception closes the connection, waits 5 s and reconnects."""
        self.poll_analyze()
        for name in TASKS:
            iv = self.intervals[name]
            now = self.clock()
            if iv <= 0 or now < self.due[name]:
                continue
            if name in ('ticks', 'analyze') and self.proc is not None:
                continue                        # analyze is reading the archive; ticks refill afterwards
            self.due[name] = now + iv
            try:
                if name == 'analyze':
                    self.start_analyze()
                    continue
                if self.ws is None:
                    self.connect()
                if name == 'hourly':
                    self.hourly()
                elif name == 'oracle':
                    self.quote_round(save_oracle=True)
                    self.due['quotes'] = self.clock() + self.intervals['quotes']
                elif name == 'quotes':
                    self.quote_round()
                else:
                    self.tick_archive()
            except Exception as e:
                self.n['errors'] += 1
                self.log(f'{name}: {type(e).__name__}: {str(e)[:200]} -- closing, reconnect in {RECONNECT_S}s')
                self.drop()
                self.sleep(RECONNECT_S)
                self.due[name] = self.clock() + min(iv, RETRY_S)

    def run(self, hours=0.0):
        t_end = self.clock() + hours * 3600 if hours and hours > 0 else None
        self.log(f"accu_phase_watch -> {self.outdir}  symbols {' '.join(self.symbols)}  rates "
                 f"{' '.join(f'{g:g}' for g in self.rates)}  band [{self.band[0]}, {self.band[1]})  intervals "
                 + ' '.join(f'{k} {v:g}s' for k, v in self.intervals.items())
                 + f"  {'until ' + utc_str(t_end) + ' UTC' if t_end else 'forever (Ctrl+C stops)'}")
        try:
            while t_end is None or self.clock() < t_end:
                try:
                    self.step()
                except KeyboardInterrupt:
                    raise
                except Exception as e:          # never crash: anything outside a task too
                    self.n['errors'] += 1
                    self.log(f'loop: {type(e).__name__}: {str(e)[:200]} -- closing, reconnect in {RECONNECT_S}s')
                    self.drop()
                    self.sleep(RECONNECT_S)
                self.sleep(LOOP_S)
            self.log(f'--hours {hours:g} reached')
            if self.proc is not None:
                self.log('waiting for the running analyze to finish')
                self.poll_analyze(wait=True)
        except KeyboardInterrupt:
            self.log('interrupted (Ctrl+C), shutting down')
            self.stop_analyze()
        finally:
            self.drop()
            self.log('stopped: ' + ' '.join(f'{k} {v}' for k, v in self.n.items()))
        return self.n


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--symbols', nargs='+', default=['CRASH1000', 'CRASH500'],
                    help='watched symbols (quotes, oracle snapshots, tick archive)')
    ap.add_argument('--cells', help='analysis.json from accu_phase_decide: adds the symbols with an eligible cell '
                                    'and uses its fitted step law and D band for G_min / IN_BAND')
    ap.add_argument('--rates', nargs='+', type=float, default=list(lib.RATES))
    ap.add_argument('--quote-interval', type=float, default=120, help='seconds between quote rounds')
    ap.add_argument('--oracle-interval', type=float, default=1800, help='seconds between oracle snapshots')
    ap.add_argument('--tick-interval', type=float, default=600, help='seconds between tick-archive updates')
    ap.add_argument('--analyze-interval', type=float, default=86400, help='seconds between analyze runs (0: never)')
    ap.add_argument('--vol-symbols', nargs='*', default=list(VOL_SYMBOLS), help='hourly sigma_pips check')
    ap.add_argument('--hours', type=float, default=0, help='stop after this many hours (0: run forever)')
    ap.add_argument('--outdir', default=os.path.normpath(os.path.join(HERE, '..', 'results', 'accu_phase_watch')))
    ap.add_argument('--reps-ticks', type=int, help='analyze bootstrap reps for tick statistics (default 5000)')
    ap.add_argument('--reps-model', type=int, help='analyze bootstrap reps for model refits (default 1000)')
    return ap


def main(argv=None, clock=time.time, sleep=time.sleep):
    global DerivWS
    args = build_parser().parse_args(argv)
    if DerivWS is None:                         # fail fast rather than retrying an import forever
        try:
            from deriv_api import DerivWS
        except ImportError as e:
            raise SystemExit(f'accu_phase_watch needs websocket-client>=1.6 (pip install websocket-client): {e}')
    try:
        w = Watcher(args, clock, sleep)
        w.run(args.hours)
    except KeyboardInterrupt:
        print('interrupted')
        return None
    return w


if __name__ == '__main__':
    main()
