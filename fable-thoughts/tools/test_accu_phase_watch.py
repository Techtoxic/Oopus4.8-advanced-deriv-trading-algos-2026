"""test_accu_phase_watch.py -- offline tests for accu_phase_watch.py (spec 4.3), no network.

A fake DerivWS serves a synthetic market that moves with a fake clock: Crash feeds that follow the
universal step law, vol-index feeds, recorded / law-A barriers, and ticks_stayed_in computed from the feed
with the repo rule. Covered: IN_BAND enter/exit, BARRIER_CHANGE / MAXTICKS_CHANGE (and resume after a
restart), gap refill and GAP_UNFILLED, archive append without duplicates (day rollover, restart, partial
line), reconnect after exceptions, PIP_CHANGE / VOL_REGIME, --hours, Ctrl+C, the daily background analyze,
and that the watcher's files are what accu_phase_decide analyze reads. Review regressions: a snapshot newer
than the archive, a refill cut short, a failed write across midnight, '[' in --outdir, a dead stdout.

Run: python3 -m unittest test_accu_phase_watch -v
"""
import contextlib, errno, glob, io, json, math, os, re, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import accu_phase_lib as lib
import accu_phase_decide as apd
import accu_phase_watch as apw
import derivfetch

MIDNIGHT = 1790035200                  # a UTC midnight
SPEC = {'CRASH1000': (5700.0, 3, 1), 'CRASH500': (3000.0, 3, 1),
        'R_100': (620.0, 2, 2), '1HZ100V': (740.0, 2, 1), '1HZ10V': (9800.0, 2, 1)}
MAXT = {0.01: 250, 0.02: 135, 0.03: 90, 0.04: 65, 0.05: 55}


class FakeClock:
    def __init__(self, t, stop_at=None):
        self.t = float(t)
        self.sleeps = []
        self.stop_at = stop_at          # sleeping past this time raises KeyboardInterrupt (Ctrl+C)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        if self.stop_at is not None and self.t > self.stop_at:
            raise KeyboardInterrupt


class FakeFeed:
    """Synthetic market; ticks exist from t0 - back to t0 + ahead, served up to the clock's current second."""

    def __init__(self, clock, t0, back=60000, ahead=80000, seed=5):
        self.clock = clock
        rng = np.random.default_rng(seed)
        rec = lib.load_prereg()['recorded']
        self.feed, self.bar, self.maxt = {}, {}, {}
        for sym, (S0, dec, iv) in SPEC.items():
            ep = np.arange(t0 - back, t0 + ahead, iv, dtype=np.int64)
            n = len(ep)
            if lib.family(sym) == 'vol':
                sig = int(re.search(r'(\d+)', sym.replace('1HZ', '')).group(1)) / 100 / math.sqrt(365 * 86400 / iv)
                x = S0 * np.exp(np.cumsum(sig * rng.standard_normal(n)))
                for g in lib.RATES:
                    self.bar[(sym, g)] = float(f'{sig * lib.stats.norm.ppf((1 + lib.PG[g]) / 2):.9g}')
            else:
                N = lib.sym_N(sym)
                Y = np.abs(rng.normal(lib.FN_MU, lib.FN_SG, n))
                spk = rng.random(n) < 1 / N
                rel = np.where(spk, -1e-3 * Y, 1e-3 / N * Y)
                rel[0] = 0
                x = S0 * np.cumprod(1 + rel)
                for g in lib.RATES:
                    v = rec.get(sym, {}).get(str(g))
                    self.bar[(sym, g)] = float(v) if isinstance(v, float) else float(f'{lib.law_a_kappa(N, g) * 1e-3 / N:.5g}')
            self.feed[sym] = (ep, np.rint(x * 10 ** dec).astype(np.int64), dec)
            for g in lib.RATES:
                self.maxt[(sym, g)] = MAXT[g]
        self.spot, self.latest, self.pip = {}, {}, {}      # overrides: proposal spot, 1-tick price, pip_size
        self.fail_calls = self.fail_connect = 0
        self.calls = []

    def px(self, sym, i):
        ep, P, dec = self.feed[sym]
        return round(int(P[i]) * 10.0 ** -dec, dec)

    def history(self, p):
        sym = p['ticks_history']
        if sym not in self.feed:
            return {'echo_req': p, 'error': {'code': 'InvalidSymbol', 'message': 'Symbol is invalid.'}}
        ep, P, dec = self.feed[sym]
        now = int(self.clock())
        end = now if p.get('end') == 'latest' else min(int(p['end']), now)
        start = int(p['start']) if 'start' in p else end - 86400
        idx = np.flatnonzero((ep >= start) & (ep <= end))[-int(p['count']):]
        prices = [self.px(sym, i) for i in idx]
        if int(p['count']) == 1 and sym in self.latest:
            prices = [self.latest[sym]]
        return {'echo_req': p, 'msg_type': 'history', 'pip_size': self.pip.get(sym, dec),
                'history': {'prices': prices, 'times': ep[idx].tolist()}}

    def proposal(self, p):
        sym, g = p['underlying_symbol'], p['growth_rate']
        ep, P, dec = self.feed[sym]
        i = int(np.searchsorted(ep, int(self.clock()), 'right')) - 1
        b = self.bar[(sym, g)]
        stay = lib.knockout(P[i - 5000:i], P[i - 4999:i + 1], b)['stay']
        runs, inprog, _ = lib.run_lengths(~stay)
        spot = self.spot.get(sym, self.px(sym, i))
        return {'echo_req': p, 'msg_type': 'proposal', 'proposal': {
            'ask_price': p['amount'], 'spot': spot, 'spot_time': int(ep[i]), 'id': 'mock',
            'contract_details': {'tick_size_barrier': b, 'maximum_ticks': self.maxt[(sym, g)],
                                 'barrier_spot_distance': f'{b * spot:.{dec + 1}f}', 'last_tick_epoch': int(ep[i]),
                                 'ticks_stayed_in': [int(v) for v in runs[-99:]] + [inprog]}}}


class FakeWS:
    """Stands in for deriv_api.DerivWS(token="", timeout=15): call() / close(); can fail on demand."""
    feed = None
    made = closed = 0

    def __init__(self, token=None, app_id=None, timeout=30, account_type=None):
        assert token == '', 'the watcher must use the public connection'
        if FakeWS.feed.fail_connect > 0:
            FakeWS.feed.fail_connect -= 1
            raise OSError('mock: network is unreachable')
        FakeWS.made += 1

    def call(self, payload, retries=3):
        f = FakeWS.feed
        assert 'buy' not in payload and 'sell' not in payload, 'read-only tool sent an order'
        f.calls.append(payload)
        if f.fail_calls > 0:
            f.fail_calls -= 1
            raise ConnectionError('mock: connection reset by peer')
        if 'ticks_history' in payload:
            return f.history(payload)
        if 'proposal' in payload:
            return f.proposal(payload)
        return {'error': {'code': 'UnrecognisedRequest', 'message': 'mock'}}

    _call = call

    def close(self):
        FakeWS.closed += 1


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


class WatchBase(unittest.TestCase):
    T0 = MIDNIGHT + 20000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, 'watch')
        self.clock = FakeClock(self.T0)
        self.feed = FakeFeed(self.clock, self.T0)
        FakeWS.feed, FakeWS.made, FakeWS.closed = self.feed, 0, 0
        self.saved = (apw.DerivWS, apw.PAUSE_PROPOSAL, apw.PAUSE_SYMBOL, derivfetch.SLEEP)
        apw.DerivWS, apw.PAUSE_PROPOSAL, apw.PAUSE_SYMBOL, derivfetch.SLEEP = FakeWS, 0, 0, 0
        self.stdout = io.StringIO()
        self.redir = contextlib.redirect_stdout(self.stdout)
        self.redir.__enter__()

    def tearDown(self):
        self.redir.__exit__(None, None, None)
        apw.DerivWS, apw.PAUSE_PROPOSAL, apw.PAUSE_SYMBOL, derivfetch.SLEEP = self.saved
        self.tmp.cleanup()

    def make(self, *argv):
        args = apw.build_parser().parse_args(['--outdir', self.out, *argv])
        return apw.Watcher(args, self.clock, self.clock.sleep)

    def alerts(self, kind=None):
        return [a for a in read_jsonl(os.path.join(self.out, 'alerts.jsonl')) if kind is None or a['type'] == kind]

    def archive(self, sym):
        files = sorted(glob.glob(os.path.join(glob.escape(os.path.join(self.out, 'ticks', sym)), '*.csv')))
        ep, px, dec = apd.read_csv_archive(files)
        return ep, px, dec, files


class TestQuotesAndAlerts(WatchBase):
    def spot_at(self, w, b=2.3454e-6):
        return round(w / b) / 1000                     # CRASH1000 spot whose 4% level is w = b*P

    def test_in_band_enter_exit(self):
        w = self.make('--symbols', 'CRASH1000', '--rates', '0.03', '0.04')
        w.connect()
        for level in (14.6, 14.08, 14.09, 14.2, 20.08):      # out, in, in, out, K=20 in phase but not eligible
            self.feed.spot['CRASH1000'] = self.spot_at(level)
            w.quote_round()
            self.clock.t += 120
        al = self.alerts('IN_BAND')
        self.assertEqual([(a['state'], a['sym'], a['g'], a['K']) for a in al],
                         [('enter', 'CRASH1000', 0.04, 14), ('exit', 'CRASH1000', 0.04, 14)])
        self.assertGreaterEqual(al[0]['gmin'], lib.THRESH['eligible_gmin'])
        self.assertTrue(0.05 <= al[0]['phase'] < 0.125)
        self.assertEqual(al[0]['band'], [0.05, 0.125])
        self.assertIsNotNone(al[1]['next_eligible_entry'])
        q4 = [q for q in read_jsonl(os.path.join(self.out, 'quotes.jsonl')) if q['g'] == 0.04]
        self.assertEqual([q['in_band'] for q in q4], [False, True, True, False, False])
        self.assertEqual((q4[-1]['K'], q4[-1]['eligible']), (20, False))
        self.assertTrue(0.05 <= q4[-1]['phase'] < 0.125)
        self.assertIn('ALERT IN_BAND', self.stdout.getvalue())
        # a restart resumes the band state: entering again alerts once, staying in band does not
        w2 = self.make('--symbols', 'CRASH1000', '--rates', '0.04')
        w2.connect()
        self.feed.spot['CRASH1000'] = self.spot_at(14.08)
        w2.quote_round()
        w3 = self.make('--symbols', 'CRASH1000', '--rates', '0.04')
        w3.connect()
        self.feed.spot['CRASH1000'] = self.spot_at(14.1)
        w3.quote_round()
        self.assertEqual([a['state'] for a in self.alerts('IN_BAND')], ['enter', 'exit', 'enter'])

    def test_barrier_and_maxticks_change(self):
        w = self.make('--symbols', 'CRASH1000', 'CRASH500', '--rates', '0.04')
        w.connect()
        self.feed.bar[('CRASH500', 0.04)] = 4.8e-6            # differs from the pre-registered 4.7141e-6
        w.quote_round()
        bc = self.alerts('BARRIER_CHANGE')
        self.assertEqual([(a['sym'], a['b'], a['frozen'], a['previous']) for a in bc],
                         [('CRASH500', 4.8e-6, 4.7141e-6, None)])
        self.feed.bar[('CRASH1000', 0.04)] = 2.36e-6
        self.feed.maxt[('CRASH1000', 0.04)] = 60
        self.clock.t += 120
        w.quote_round()
        self.clock.t += 120
        w.quote_round()                                        # unchanged: no new alerts
        bc = self.alerts('BARRIER_CHANGE')
        self.assertEqual(len(bc), 2)
        self.assertEqual((bc[1]['sym'], bc[1]['previous'], bc[1]['b'], bc[1]['first_seen']),
                         ('CRASH1000', 2.3454e-6, 2.36e-6, 2.3454e-6))
        self.assertEqual((bc[1]['equals_first_seen'], bc[1]['equals_frozen']), (False, False))
        self.assertIsNotNone(bc[1]['epoch'])
        mt = self.alerts('MAXTICKS_CHANGE')
        self.assertEqual([(a['sym'], a['previous'], a['maximum_ticks']) for a in mt], [('CRASH1000', 65, 60)])
        # restart on the same outdir: the last b is read back, so a revert is still reported
        self.feed.bar[('CRASH1000', 0.04)] = 2.3454e-6
        w2 = self.make('--symbols', 'CRASH1000', 'CRASH500', '--rates', '0.04')
        w2.connect()
        w2.quote_round()
        bc = self.alerts('BARRIER_CHANGE')
        self.assertEqual(len(bc), 3)
        self.assertEqual((bc[2]['previous'], bc[2]['equals_first_seen'], bc[2]['equals_frozen']), (2.36e-6, True, True))
        # the quote log is what accu_phase_decide analyze reads (latest b, spot, pip, change history)
        bars, spots, hist = apd.quotes_log_inputs(os.path.join(self.out, 'quotes.jsonl'))
        self.assertEqual(bars['CRASH1000'][0.04], 2.3454e-6)
        self.assertEqual(spots['CRASH500'][0.04][1], 3)
        self.assertEqual(sorted({h['b'] for h in hist if h['sym'] == 'CRASH1000'}), [2.3454e-6, 2.36e-6])

    def test_pip_change_and_vol_regime(self):
        self.assertAlmostEqual(lib.vol_sigma_pips('R_100', 381, 2), 9.595, places=3)
        self.assertAlmostEqual(lib.vol_sigma_pips('1HZ100V', 539, 2), 9.598, places=3)
        self.assertAlmostEqual(lib.vol_sigma_pips('1HZ10V', 5390, 2), 9.598, places=3)
        self.assertIsNone(lib.vol_sigma_pips('CRASH1000', 5700, 3))
        w = self.make('--symbols', 'CRASH1000', '--rates', '0.04')
        w.connect()
        self.feed.latest['R_100'] = 380.0                     # sigma_pips 9.57 < 9.6
        w.hourly()
        self.assertEqual([(a['sym'], a['state']) for a in self.alerts('VOL_REGIME')], [('R_100', 'below')])
        self.assertEqual(self.alerts('PIP_CHANGE'), [])
        self.feed.pip['CRASH1000'] = 2
        self.feed.latest['R_100'] = 620.0
        self.clock.t += 3600
        w.hourly()
        w.hourly()
        self.assertEqual([(a['sym'], a['state']) for a in self.alerts('VOL_REGIME')], [('R_100', 'below'), ('R_100', 'above')])
        self.assertEqual([(a['sym'], a['previous'], a['pip']) for a in self.alerts('PIP_CHANGE')], [('CRASH1000', 3, 2)])


class TestTickArchive(WatchBase):
    T0 = MIDNIGHT - 900                        # the archive crosses a UTC midnight

    def test_append_without_duplicates_day_rollover_restart(self):
        w = self.make('--symbols', 'CRASH1000')
        w.connect()
        for _ in range(3):                                     # overlapping 1500+ tick windows every 600 s
            w.tick_archive()
            self.clock.t += 600
        ep, px, dec, files = self.archive('CRASH1000')
        self.assertEqual([os.path.basename(f) for f in files], ['2026-09-21.csv', '2026-09-22.csv'])
        for f in files:
            with open(f) as fh:
                text = fh.read()
            self.assertTrue(text.startswith('epoch,quote\n'))
            self.assertEqual(text.count('epoch'), 1)
        self.assertTrue(np.all(np.diff(ep) == 1), 'archive must be contiguous, unique and ascending')
        self.assertEqual(dec, 3)
        fe, P, _ = self.feed.feed['CRASH1000']
        i = np.searchsorted(fe, ep)
        np.testing.assert_array_equal(lib.to_pips(px, 3), P[i])
        # a hard kill left a partial line: the restart cuts it off and resumes after the last full tick
        with open(files[-1], 'a') as fh:
            fh.write('17900')
        w2 = self.make('--symbols', 'CRASH1000')
        self.assertEqual(w2.last_epoch['CRASH1000'], int(ep[-1]))
        with open(files[-1], 'rb') as fh:
            self.assertTrue(fh.read().endswith(b'\n'))
        w2.connect()
        self.clock.t += 300
        w2.tick_archive()
        ep2 = self.archive('CRASH1000')[0]
        self.assertTrue(np.all(np.diff(ep2) == 1))
        self.assertEqual(ep2[0], ep[0])
        self.assertEqual(int(ep2[-1]), int(self.clock.t))
        # accu_phase_decide reads the archive layout directly
        series = apd.load_tick_dir(self.out, {})
        self.assertEqual(len(series['CRASH1000'][0]), len(ep2))
        self.assertEqual(series['CRASH1000'][2], 3)

    def test_gap_refill_and_unfilled_gap(self):
        w = self.make('--symbols', 'CRASH1000')
        w.connect()
        w.tick_archive()
        ep0 = self.archive('CRASH1000')[0]
        self.clock.t += 5000                                   # missed several updates: refetch gap + 1500
        w.tick_archive()
        ep1 = self.archive('CRASH1000')[0]
        self.assertTrue(np.all(np.diff(ep1) == 1))
        self.assertEqual((ep1[0], int(ep1[-1])), (ep0[0], int(self.clock.t)))
        self.assertEqual(self.alerts('GAP_UNFILLED'), [])
        self.assertIn('refilled', self.stdout.getvalue())
        self.clock.t += 30000                                  # longer than the 20000-tick refill cap
        w.tick_archive()
        ep2 = self.archive('CRASH1000')[0]
        gaps = self.alerts('GAP_UNFILLED')
        self.assertEqual(len(gaps), 1)
        g = gaps[0]
        self.assertEqual(g['last_archived'], int(ep1[-1]))
        self.assertEqual(g['refetch_target'], apw.TICK_REFILL_MAX)
        d = np.diff(ep2)
        self.assertTrue(np.all(d > 0))
        self.assertEqual(int(np.sum(d != 1)), 1)               # exactly the one logged hole
        self.assertEqual(int(d[d != 1][0]) - 1, g['missing_ticks'])
        self.assertEqual(int(ep2[-1]), int(self.clock.t))
        self.assertGreaterEqual(int(ep2[-1]) - g['first_fetched'] + 1, apw.TICK_REFILL_MAX)

    def test_refill_cut_short_is_retried(self):
        w = self.make('--symbols', 'CRASH1000')
        w.connect()
        w.tick_archive()
        hist, cut = self.feed.history, {'t': None}

        def flaky(p):                                          # the server refuses pages older than cut['t']
            if cut['t'] is not None and isinstance(p.get('end'), int) and p['end'] < cut['t']:
                return {'echo_req': p, 'error': {'code': 'RateLimit', 'message': 'mock: rate limit reached'}}
            return hist(p)
        self.feed.history = flaky
        self.clock.t += 5000
        cut['t'] = self.clock.t - 2000                         # the refill stops after ~2000 of ~6500 ticks
        w.tick_archive()
        self.assertIn('gap kept open, retry 1/3', self.stdout.getvalue())
        cut['t'] = None
        self.clock.t += 600
        w.tick_archive()                                       # the next round refills the whole gap
        ep = self.archive('CRASH1000')[0]
        self.assertTrue(np.all(np.diff(ep) == 1))
        self.assertEqual(int(ep[-1]), int(self.clock.t))
        self.assertEqual(self.alerts('GAP_UNFILLED'), [])
        self.clock.t += 5000                                   # a refill that keeps failing is reported
        for _ in range(apw.REFILL_RETRIES + 1):               # after REFILL_RETRIES rounds, and archiving resumes
            cut['t'] = self.clock.t - 2000
            w.tick_archive()
            self.clock.t += 600
        self.assertEqual(len(self.alerts('GAP_UNFILLED')), 1)
        ep = self.archive('CRASH1000')[0]
        self.assertEqual(int(np.sum(np.diff(ep) != 1)), 1)
        self.assertEqual(int(ep[-1]), int(self.clock.t) - 600)

    def test_failed_write_across_midnight_leaves_no_duplicates(self):
        w = self.make('--symbols', 'CRASH1000')
        w.connect()
        w.tick_archive()                                       # archive up to T0 (day 1)
        self.clock.t += 1200                                   # next batch: day-1 rows, then day-2 rows
        day2 = os.path.basename(apw.day_file('', 'CRASH1000', MIDNIGHT))
        armed = {'on': True}

        def flaky_open(f, mode='r', *a, **k):                  # e.g. an AV scanner holds the new file
            if armed['on'] and str(f).endswith(day2) and 'a' in mode:
                armed['on'] = False
                raise PermissionError(13, 'mock: the file is being used by another process')
            return open(f, mode, *a, **k)
        apw.open = flaky_open
        try:
            with self.assertRaises(PermissionError):
                w.tick_archive()
        finally:
            del apw.open
        w.tick_archive()                                       # the retry
        ep = self.archive('CRASH1000')[0]
        self.assertTrue(np.all(np.diff(ep) == 1))
        self.assertEqual(int(ep[-1]), int(self.clock.t))

    def test_outdir_with_glob_characters(self):
        self.out = os.path.join(self.tmp.name, 'Deriv [watch]')
        w = self.make('--symbols', 'CRASH1000')
        w.connect()
        w.tick_archive()
        self.clock.t += 300
        w2 = self.make('--symbols', 'CRASH1000')               # restart: the archive end is read back
        self.assertEqual(w2.last_epoch['CRASH1000'], int(self.clock.t) - 300)
        w2.connect()
        w2.tick_archive()
        ep = apd.load_tick_dir(self.out, {})['CRASH1000'][0]
        self.assertTrue(np.all(np.diff(ep) == 1))
        self.assertEqual(int(ep[-1]), int(self.clock.t))


class TestLoop(WatchBase):
    def test_reconnect_after_exception(self):
        w = self.make('--symbols', 'CRASH1000', '--rates', '0.04', '--vol-symbols', '--analyze-interval', '0')
        self.feed.fail_connect = 1                             # the first connect fails
        w.step()
        self.assertEqual((w.n['errors'], FakeWS.made), (1, 1))
        self.assertIn(apw.RECONNECT_S, self.clock.sleeps)
        self.assertEqual(len(read_jsonl(os.path.join(self.out, 'quotes.jsonl'))), 1)   # the oracle round ran
        self.assertIsNotNone(w.last_epoch['CRASH1000'])
        closed = FakeWS.closed
        self.feed.fail_calls = 1                               # the connection drops mid-round
        self.clock.t += 125
        w.step()
        self.assertEqual(w.n['errors'], 2)
        self.assertEqual(FakeWS.closed, closed + 1)
        self.assertIsNotNone(w.ws)                             # reconnected for the next task in the same step
        self.assertEqual(FakeWS.made, 2)
        self.clock.t += apw.RETRY_S + 1
        w.step()                                               # the failed task is retried and succeeds
        self.assertEqual(w.n['errors'], 2)
        self.assertGreaterEqual(len(read_jsonl(os.path.join(self.out, 'quotes.jsonl'))), 2)
        log = self.stdout.getvalue()
        self.assertIn('OSError: mock: network is unreachable -- closing, reconnect in 5s', log)
        self.assertIn('ConnectionError: mock: connection reset by peer -- closing, reconnect in 5s', log)
        with open(os.path.join(self.out, 'watch.log')) as fh:
            self.assertIn('connected (public, read-only) #2', fh.read())

    def test_run_hours_limit_and_oracle_files(self):
        w = apw.main(['--outdir', self.out, '--symbols', 'CRASH1000', '--rates', '0.04', '--hours', '0.1',
                      '--analyze-interval', '0'], self.clock, self.clock.sleep)
        self.assertGreaterEqual(self.clock.t, self.T0 + 360)
        self.assertEqual(w.n['errors'], 0)
        self.assertEqual(w.n['quotes'], 3)                     # t = 0 (oracle round), 120, 240 s
        self.assertIsNone(w.ws)
        self.assertGreaterEqual(FakeWS.closed, 1)
        files = glob.glob(os.path.join(self.out, 'oracle', 'CRASH1000_0.04_*.json'))
        self.assertEqual(len(files), 1)
        # the snapshot and the archive are what analyze's oracle reader expects: primary mode, rule confirmed
        recs = apd.watcher_snapshots(self.out, apd.load_tick_dir(self.out, {}))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['mode'], 'primary')
        self.assertTrue(recs[0]['rules']['raw_incl']['full'])
        self.assertGreaterEqual(recs[0]['covered'], 50)
        with open(os.path.join(self.out, 'watch.log')) as fh:
            text = fh.read()
        self.assertIn('--hours 0.1 reached', text)
        self.assertIn('stopped: quotes 3', text)
        kinds = {next(k for k in ('proposal', 'ticks_history') if k in c) for c in self.feed.calls}
        self.assertEqual(kinds, {'proposal', 'ticks_history'})   # read-only requests only

    def test_ctrl_c_stops_cleanly(self):
        self.clock.stop_at = self.T0 + 200
        w = apw.main(['--outdir', self.out, '--symbols', 'CRASH1000', '--rates', '0.04', '--analyze-interval', '0'],
                     self.clock, self.clock.sleep)
        self.assertIsNotNone(w)
        self.assertIsNone(w.ws)
        with open(os.path.join(self.out, 'watch.log')) as fh:
            lines = fh.read().strip().split('\n')
        self.assertTrue(any('interrupted (Ctrl+C)' in l for l in lines))
        self.assertIn('stopped:', lines[-1])

    def test_dead_stdout_does_not_stop_the_loop(self):
        class DeadStdout(io.StringIO):                          # closed terminal, dead pipe, full disk
            def write(self, s):
                raise OSError(errno.ENOSPC, 'No space left on device')
        with contextlib.redirect_stdout(DeadStdout()):
            w = apw.main(['--outdir', self.out, '--symbols', 'CRASH1000', '--rates', '0.04', '--vol-symbols',
                          '--analyze-interval', '0', '--hours', '0.2'], self.clock, self.clock.sleep)
        self.assertEqual(w.n['errors'], 0)
        with open(os.path.join(self.out, 'watch.log')) as fh:
            self.assertIn('--hours 0.2 reached', fh.read())


class TestOracleVsArchive(WatchBase):
    def test_snapshot_newer_than_archive_is_skipped(self):
        # a snapshot taken after the last tick round cannot be aligned in primary mode; it used to align in
        # sub mode and fail OR-1 (KILL K4 + HALT) although the house follows the rule
        w = self.make('--symbols', 'CRASH1000', 'CRASH500', '--rates', '0.04', '--vol-symbols')
        w.connect()
        w.quote_round(save_oracle=True)
        w.tick_archive()
        self.clock.t += 300
        w.quote_round(save_oracle=True)                        # the archive is now 300 s behind this snapshot
        self.assertEqual(len(os.listdir(os.path.join(self.out, 'oracle'))), 4)
        recs = apd.watcher_snapshots(self.out, apd.load_tick_dir(self.out, {}))
        self.assertEqual(len(recs), 2)
        self.assertIn('2 skipped', self.stdout.getvalue())
        self.assertTrue(all(r['mode'] == 'primary' and r['rules']['raw_incl']['full'] for r in recs))
        oc = lib.oracle_outcomes(recs)
        self.assertNotEqual(oc['OR1']['status'], 'FAIL')
        self.assertEqual(oc['HALT'], [])
        w.tick_archive()                                       # once the archive catches up, all 4 align
        recs = apd.watcher_snapshots(self.out, apd.load_tick_dir(self.out, {}))
        self.assertEqual([r['mode'] for r in recs], ['primary'] * 4)
        # an --end split before the newer snapshots still aligns them on the whole archive
        with contextlib.redirect_stdout(io.StringIO()):
            apd.main(['analyze', '--from-dir', self.out, '--outdir', os.path.join(self.out, 'split'),
                      '--end', str(self.T0 - 300), '--reps-ticks', '50', '--reps-model', '20'])
        A = apd.read_json(os.path.join(self.out, 'split', 'analysis.json'))
        self.assertEqual((A['oracle']['n_records'], A['oracle']['n_primary']), (4, 4))
        self.assertNotEqual(A['decision']['OR1'], 'FAIL')


class TestDailyAnalyze(WatchBase):
    def test_background_analyze_appends_verdict(self):
        w = self.make('--symbols', 'CRASH1000', 'CRASH500', '--rates', '0.03', '0.04', '--vol-symbols',
                      '--reps-ticks', '100', '--reps-model', '30')
        self.assertEqual(w.due['analyze'], self.T0 + 86400)
        w.connect()
        w.quote_round(save_oracle=True)
        now = int(self.clock.t)
        for sym in ('CRASH1000', 'CRASH500'):                  # 8 h of archive straight from the feed
            ep, P, dec = self.feed.feed[sym]
            m = (ep > now - 28800) & (ep <= now)
            apw.append_archive(w.tdir, sym, ep[m], P[m] * 10.0 ** -dec, dec)
        w.start_analyze()
        self.assertIsNotNone(w.proc)

        real, w.proc = w.proc, dict(w.proc, p=Running())      # while analyze runs, tick archiving waits
        w.due.update(hourly=math.inf, oracle=math.inf, quotes=math.inf, ticks=0)
        w.step()
        self.assertEqual((w.n['ticks'], w.due['ticks']), (0, 0))
        w.proc = real
        w.poll_analyze(wait=True)
        self.assertIsNone(w.proc)
        with open(os.path.join(self.out, 'summary.log')) as fh:
            lines = fh.read().strip().split('\n')
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r'analyze/\d{8}T\d{6}Z D \S+ \[.*\| VERDICT: (PASS-A|PASS-B|KILL|INCONCLUSIVE) ')
        adir = glob.glob(os.path.join(self.out, 'analyze', '*'))[0]
        A = apd.read_json(os.path.join(adir, 'analysis.json'))
        self.assertEqual(A['oracle']['n_records'], 4)          # the watcher's oracle/ snapshots were aligned
        self.assertEqual(A['decision']['OR1'], 'PASS')
        self.assertEqual(A['decision']['A7'], 'PASS')          # barriers from quotes.jsonl, unchanged
        self.assertEqual(set(A['symbols']), {'CRASH1000', 'CRASH500'})
        # a restart schedules the next analyze 24 h after the last summary.log entry
        w2 = self.make('--symbols', 'CRASH1000')
        self.assertAlmostEqual(w2.due['analyze'], os.path.getmtime(os.path.join(self.out, 'summary.log')) + 86400)

    def test_hung_analyze_is_stopped(self):
        w = self.make('--symbols', 'CRASH1000', '--vol-symbols')
        adir = os.path.join(self.out, 'analyze', 'hung')
        os.makedirs(adir)
        p = Running()
        w.proc = {'p': p, 'dir': adir, 'out': open(os.path.join(adir, 'stdout.txt'), 'x'), 't0': self.clock.t}
        self.clock.t += apw.ANALYZE_MAX_S - 1
        w.poll_analyze()
        self.assertIsNotNone(w.proc)
        self.clock.t += 2
        w.poll_analyze()
        self.assertIsNone(w.proc)
        self.assertTrue(p.terminated)
        with open(os.path.join(self.out, 'summary.log')) as fh:
            self.assertIn('analyze rc=timeout after 4 h, no verdict', fh.read())


class TestCells(WatchBase):
    def test_cells_file_adds_symbols_model_and_band(self):
        A = {'band_used': [0.0, 0.125], 'symbols': {
            'CRASH500': {'eligibility': {'0.04': {'14': {'gmin': 1.0013, 'eligible': True}}},
                         'S1': {'model': {'N': 500, 'm': 2e-6, 'lam': 0.002}}},
            'BOOM300N': {'eligibility': {'0.03': {'20': {'gmin': 1.0006, 'eligible': True}}},
                         'S1': {'model': {'N': 300, 'm': 2.5 * 1e-3 / 300, 'lam': 1 / 300}}},
            'BOOM500': {'eligibility': {'0.03': {'20': {'gmin': 0.999, 'eligible': False}}}}}}
        path = os.path.join(self.tmp.name, 'analysis.json')
        with open(path, 'w') as fh:
            json.dump(A, fh)
        w = self.make('--cells', path)
        self.assertEqual(w.symbols, ['CRASH1000', 'CRASH500', 'BOOM300N'])
        self.assertEqual(w.band, (0.0, 0.125))
        self.assertEqual(w.model('BOOM300N')['m'], 2.5 * 1e-3 / 300)       # the fitted N-series step
        self.assertEqual(w.model('CRASH1000'), lib.model_params('CRASH1000'))
        w0 = self.make()
        self.assertIsNone(w0.model('BOOM300N'))                              # N-series: m unknown without --cells
        self.assertIsNone(w0.model('R_100'))
        law = lib.load_frozen()
        c = w.cells('CRASH500', 0.04, 4.7141e-6, 14)[14]
        self.assertAlmostEqual(c['gmin'], lib.g_min(0.04, 14, 4.7141e-6 / 2e-6, 0.002, law)[0], places=12)


class Running:
    """Stands in for a Popen that has not finished."""
    terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


if __name__ == '__main__':
    unittest.main()
