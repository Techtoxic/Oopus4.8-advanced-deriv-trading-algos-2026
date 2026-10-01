"""test_accu_phase_map.py -- offline tests for accu_phase_map.py (T2) and the watcher's --map mode.

Covered: the vol lattice model (continuous limit, sawtooth), windows (the old public K14 window on
CRASH1000 4% and none near spot at the authenticated barrier), the gates (OR-1, misfit), the pre-registered
window rules (open, spot_exit, retune, 30-minute dwell, censoring, near), the pooled in-window tick
statistic with the barrier in force per tick, the prereg hash check, and build -> watch -> evaluate on the
fake market of test_accu_phase_watch (no network, no orders).

Run: python3 -m unittest test_accu_phase_map -v
"""
import json, math, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
from scipy import stats
import accu_phase_lib as lib
import accu_phase_decide as apd
import accu_phase_map as apm
import accu_phase_watch as apw
from test_accu_phase_watch import WatchBase, FakeWS, read_jsonl

BC = {'kind': 'bc', 'm': 1e-6, 'lam': 1e-3}
PR = apm.prereg_t2()[0]


class TestModel(unittest.TestCase):
    def test_vol_continuous_limit_and_sawtooth(self):
        sr = apm.vol_sigma_rel('1HZ100V')
        z = stats.norm.ppf((1 + 0.9465) / 2)
        b = sr * z                                   # continuous P(stay) = 0.9465 at every spot
        far = apm.vol_pstay([400], [0.5], sr, b)[0]
        self.assertAlmostEqual(far, 0.9465, places=3)
        K = np.full(3, 10)
        p = apm.vol_pstay(K, np.array([0.02, 0.5, 0.98]), sr, b)
        self.assertTrue(p[0] > p[1] > p[2])          # survival falls through a level: the sawtooth
        self.assertGreater(1.05 * p[0], 1.001)       # just above an integer, G > 1 at low K
        self.assertLess(1.05 * p[1], 1.0)

    def test_windows_public_vs_authenticated_crash1000(self):
        pub = apm.windows(BC, 0.04, 2.3454e-6, 3, 2000, 12000, 1.001)
        self.assertIn(14, [w['K'] for w in pub])     # the old lead band, K14 near spot 6000
        k14 = [w for w in pub if w['K'] == 14][0]
        self.assertTrue(k14['w_lo'] - 14 < 0.125 and k14['w_hi'] - 14 > 0.05)
        auth = apm.windows(BC, 0.04, 2.28724e-6, 3, 2000, 12000, 1.001)
        self.assertTrue(auth and max(w['K'] for w in auth) <= 8)
        nr = apm.nearest(auth, 5750.0, 2.28724e-6, 3)
        self.assertGreater(nr['distance_levels'], 4)
        self.assertLess(nr['distance_pct'], -35)
        for w in auth:                               # spot and w bounds agree
            self.assertAlmostEqual(w['spot_lo'] * 2.28724e-6 * 1e3, w['w_lo'], places=9)

    def test_in_window_and_distance(self):
        wins = [{'w_lo': 6.05, 'w_hi': 6.12}, {'w_lo': 7.05, 'w_hi': 7.1}]
        self.assertIs(apm.in_window(wins, 6.06), wins[0])
        self.assertIsNone(apm.in_window(wins, 6.12))
        self.assertAlmostEqual(apm.w_distance(wins, 6.5), 0.38)
        self.assertEqual(apm.w_distance(wins, 7.07), 0.0)
        self.assertIsNone(apm.w_distance([], 1.0))


class TestGates(unittest.TestCase):
    def rec(self, g, covered, full, mode='primary'):
        return {'g': g, 'mode': mode, 'covered': covered, 'rules': {'raw_incl': {'completed_full': full, 'full': full}}}

    def test_oracle_gate(self):
        ok = [self.rec(g, 99, True) for g in (0.01, 0.02, 0.03)]
        self.assertTrue(apm.oracle_gate(ok, 3)['ok'])
        self.assertFalse(apm.oracle_gate(ok[:2] + [self.rec(0.03, 40, True)], 3)['ok'])      # too few qualify
        self.assertFalse(apm.oracle_gate(ok[:2] + [self.rec(0.03, 99, True, 'sub')], 3)['ok'])
        bad = apm.oracle_gate(ok + [self.rec(0.04, 99, False)], 3)
        self.assertEqual((bad['ok'], bad['failed_rates']), (False, [0.04]))

    def test_misfit_detects_a_wrong_model(self):
        sr = 1.0 / math.sqrt(365 * 86400)
        rng = np.random.default_rng(3)
        x = 740.0 * np.exp(np.cumsum(sr * rng.standard_normal(60000)))
        S = lib.prep_series('1HZ100V', np.arange(60000) + 1_790_000_000, np.round(x, 2), 2)
        b = sr * stats.norm.ppf((1 + 0.9465) / 2)
        good = apm.misfit({'kind': 'vol', 'sigma_rel': sr}, 0.05, b, S, 3.0)
        self.assertTrue(good['ok'], good)
        bad = apm.misfit({'kind': 'vol', 'sigma_rel': sr * 1.1}, 0.05, b, S, 3.0)
        self.assertFalse(bad['ok'])
        self.assertGreater(bad['z'], 3)              # the data survive more than a 10% wider model says


def tiny_map(model=BC, sym='CRASH1000', g=0.04, pip=3, spot=5700.0):
    return {'prereg_sha256': apm.prereg_t2()[1], 'symbols': {sym: {
        'model': model, 'pip': pip, 'cells': {str(g): {'armed': True, 'spot': spot}, '0.05': {'armed': False, 'spot': spot}}}}}


class TestTracker(unittest.TestCase):
    B = 2.28724e-6

    def spot_in(self, K=6):
        w = [x for x in apm.windows(BC, 0.04, self.B, 3, 2000, 6000, 1.001) if x['K'] == K][0]
        return round((w['w_lo'] + w['w_hi']) / 2 / self.B) / 1000

    def test_open_close_retune_dwell(self):
        tr = apm.WindowTracker(tiny_map(), PR)
        self.assertEqual(list(tr.cells), [('CRASH1000', 0.04)])            # unarmed 5% is not tracked
        s_in, s_out = self.spot_in(), 5750.0
        self.assertEqual(tr.on_quote('CRASH1000', 0.04, self.B, s_out, 0), [])
        ev = tr.on_quote('CRASH1000', 0.04, self.B, s_in, 100, 1000)
        self.assertEqual((ev[0]['state'], ev[0]['K']), ('open', 6))
        self.assertEqual(tr.on_quote('CRASH1000', 0.04, self.B, s_in, 700), [])   # still open: no event
        ev = tr.on_quote('CRASH1000', 0.04, self.B, s_out, 1000, 1900)
        self.assertEqual((ev[0]['state'], ev[0]['reason'], ev[0]['dwell_s'], ev[0]['qualifying']),
                         ('close', 'spot_exit', 900, False))
        # retune: the spot stays put, b tightens so the spot leaves the window
        tr.on_quote('CRASH1000', 0.04, self.B, s_in, 2000)
        ev = tr.on_quote('CRASH1000', 0.04, self.B * 0.98, s_in, 4000)
        self.assertEqual((ev[0]['reason'], ev[0]['dwell_s'], ev[0]['qualifying']), ('retune', 2000, True))
        self.assertEqual(tr.on_quote('CRASH500', 0.04, self.B, s_in, 5000), [])   # not in the map

    def test_near_and_replay_censoring(self):
        tr = apm.WindowTracker(tiny_map(), PR)
        s_in = self.spot_in()
        lvl = 1 / (self.B * 1e3)                      # one level in spot units
        self.assertTrue(tr.near('CRASH1000', {0.04: (self.B, s_in)}))
        self.assertTrue(tr.near('CRASH1000', {0.04: (self.B, s_in + 0.5 * lvl)}))
        self.assertFalse(tr.near('CRASH1000', {0.04: (self.B, 5750.0)}))
        rows = [{'sym': 'CRASH1000', 'g': 0.04, 'b': self.B, 'spot': s_in, 't': t, 'epoch': int(t)}
                for t in (0.0, 1000.0, 2000.0)]
        iv, t_last = apm.replay(apm.WindowTracker(tiny_map(), PR), rows)
        self.assertEqual((len(iv), iv[0]['censored'], iv[0]['dwell_s'], iv[0]['qualifying']), (1, True, 2000.0, True))

    def test_in_window_ticks_uses_the_barrier_in_force(self):
        tr = apm.WindowTracker(tiny_map(), PR)
        s_in = self.spot_in()
        P0 = int(round(s_in * 1000))
        ep = np.arange(1000, 1100)
        P = P0 + np.zeros(100, np.int64)
        P[50] = P0 + 20                               # one knockout at tick 49 -> 50 (|D| = 20 > w ~ 6)
        series = {'CRASH1000': (ep, P / 1000.0, 3)}
        rows = [{'sym': 'CRASH1000', 'g': 0.04, 'b': self.B, 'spot': s_in, 't': 1000.0, 'epoch': 1000},
                {'sym': 'CRASH1000', 'g': 0.04, 'b': self.B * 0.5, 'spot': s_in, 't': 1080.0, 'epoch': 1080}]
        iv = [{'sym': 'CRASH1000', 'g': 0.04, 'qualifying': True, 'censored': True, 'epoch_open': 1000, 't_open': 1000}]
        best = apm.in_window_ticks(tr, series, rows, iv)
        eps = sorted(e for _, e in best)
        self.assertEqual(eps[0], 1000)
        self.assertEqual(eps[-1], 1079)              # from 1080 the halved b puts the spot out of every window
        self.assertEqual(best[('CRASH1000', 1049)][1], 0.0)
        self.assertAlmostEqual(best[('CRASH1000', 1048)][1], 1.04)
        iv[0]['qualifying'] = False
        self.assertEqual(apm.in_window_ticks(tr, series, rows, iv), {})


class TestPreregHash(WatchBase):
    def test_watcher_refuses_a_map_on_another_prereg(self):
        m = tiny_map()
        m['prereg_sha256'] = '0' * 64
        path = os.path.join(self.tmp.name, 'm.json')
        with open(path, 'w') as fh:
            json.dump(m, fh)
        with self.assertRaises(SystemExit) as cm:
            self.make('--map', path)
        self.assertIn('rebuild the map', str(cm.exception))


class TestEndToEnd(WatchBase):
    def setUp(self):
        super().setUp()
        self.saved_apd = (apd.DerivWS, apd.PAUSE_PROPOSAL, apd.PAUSE_SYMBOL)
        apd.DerivWS, apd.PAUSE_PROPOSAL, apd.PAUSE_SYMBOL = FakeWS, 0, 0

    def tearDown(self):
        apd.DerivWS, apd.PAUSE_PROPOSAL, apd.PAUSE_SYMBOL = self.saved_apd
        super().tearDown()

    def test_build_watch_evaluate(self):
        mdir = os.path.join(self.tmp.name, 'map')
        pm = apm.main(['build', '--symbols', 'CRASH1000', 'R_100', '1HZ10V', '--ticks', '20000', '--outdir', mdir])
        self.assertEqual(pm['prereg_sha256'], apm.prereg_t2()[1])
        self.assertTrue(all('buy' not in c and 'sell' not in c for c in self.feed.calls))
        c1 = pm['symbols']['CRASH1000']
        self.assertTrue(c1['oracle']['ok'], c1['oracle'])
        self.assertEqual(c1['cells']['0.04']['b'], self.feed.auth_bar('CRASH1000', 0.04))   # authenticated b
        self.assertEqual(c1['cells']['0.04']['b_public'], self.feed.bar[('CRASH1000', 0.04)])
        self.assertTrue(c1['cells']['0.04']['armed'], c1['cells']['0.04'])
        self.assertLessEqual(abs(c1['cells']['0.04']['misfit']['z']), 3)
        self.assertTrue(pm['symbols']['R_100']['cells']['0.05']['armed'])
        x10 = pm['symbols']['1HZ10V']                 # sigma_pips ~ 17 at 9800 with pip 2: armed or excluded
        self.assertEqual(x10['model']['kind'], 'vol')
        # force a window at the fake's spot so the watcher opens one: shrink m until w is inside a window
        path = os.path.join(mdir, 'phase_map.json')
        pm = apd.read_json(path)
        b = pm['symbols']['CRASH1000']['cells']['0.04']['b']
        spot = pm['symbols']['CRASH1000']['cells']['0.04']['spot']
        m = 1e-6
        while apm.in_window(apm.windows({'kind': 'bc', 'm': m, 'lam': 1e-3}, 0.04, b, 3, spot / 2, spot * 2, 1.001),
                            apm.w_of(b, spot, 3)) is None and m > 1e-7:
            m *= 0.97
        pm['symbols']['CRASH1000']['model'] = {'kind': 'bc', 'm': m, 'lam': 1e-3}
        with open(path, 'w') as fh:
            json.dump(pm, fh)
        w = self.make('--map', path, '--symbols', 'CRASH1000', '--rates', '0.04', '--vol-symbols')
        self.assertIn('prereg_t2 sha256', self.stdout.getvalue())
        self.assertEqual(w.intervals['quotes'], apw.MAP_LOOP_S)
        self.assertIn('CRASH1000', w.symbols)
        w.connect()
        for _ in range(3):
            w.quote_round()
            self.clock.t += 60
        ev = read_jsonl(os.path.join(self.out, 'windows.jsonl'))
        self.assertEqual(ev[0]['state'], 'open')
        self.assertTrue(self.alerts('WINDOW'))
        self.assertLessEqual(w.sym_due['CRASH1000'] - self.clock.t, PR['watch']['near_quote_interval_s'])
        # restart resumes the open window from the quote log
        w2 = self.make('--map', path, '--symbols', 'CRASH1000', '--rates', '0.04', '--vol-symbols')
        self.assertIn(('CRASH1000', 0.04), w2.tracker.open)
        # archive the feed and evaluate
        now = int(self.clock.t)
        ep, P, dec = self.feed.feed['CRASH1000']
        mm = (ep > now - 7200) & (ep <= now)
        apw.append_archive(w.tdir, 'CRASH1000', ep[mm], P[mm] * 10.0 ** -dec, dec)
        edir = os.path.join(self.tmp.name, 'eval')
        res = apm.main(['evaluate', '--watch-dir', self.out, '--map', path, '--reps', '200', '--outdir', edir])
        self.assertEqual(res['verdict'], 'INTERIM')
        self.assertGreaterEqual(len(res['intervals']), 1)
        with open(os.path.join(edir, 'summary.txt')) as fh:
            self.assertRegex(fh.read(), r'VERDICT: INTERIM')
        self.assertIsNotNone(res['drift'])


if __name__ == '__main__':
    unittest.main()
