"""test_accu_phase.py -- offline acceptance tests for accu_phase_lib / accu_phase_decide (spec 4.5).

  1. oracle regression on the 2026-06-07 ticks_stayed_in snapshots (R_100, 1HZ100V, R_25)
  2. analyze --local-json on the repo's CRASH/BOOM json.gz files (CRASH500 band G = 1.00239, C4)
  3. knockout rule at exact-integer w (Decimal tie handling), rule variants, verdict logic
  4. mock-ws end to end: `all` (ladder + oracle + collect + analyze) against a fake DerivWS whose
     proposal / ticks_history responses are shaped like remaining_specs.json; authenticated (demo)
     proposals are sold a tighter barrier than public ones on CRASH1000 / CRASH500 / BOOM300N

Run: python3 -m unittest test_accu_phase -v      (no network; about a minute)
"""
import argparse, contextlib, gzip, io, itertools, json, math, os, sys, tempfile, unittest
from decimal import Decimal, ROUND_CEILING

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import accu_phase_lib as lib
import accu_phase_decide as apd
import derivfetch

REPO = os.path.normpath(os.path.join(HERE, '..', '..'))
FIX = os.path.join(HERE, 'accu_phase_data', 'fixtures')
LOCAL = os.environ.get('ACCU_LOCAL_DATA', os.path.join(REPO, 'data'))
R100_CSV = os.path.join(REPO, 'opus-thoughts', 'data', 'R_100.csv.gz')


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def read_text(path):
    with open(path) as fh:
        return fh.read()


def set_token(value):
    """Set (or with None unset) DERIV_TOKEN; returns the previous value for restore_token."""
    prev = os.environ.get('DERIV_TOKEN')
    if value is None:
        os.environ.pop('DERIV_TOKEN', None)
    else:
        os.environ['DERIV_TOKEN'] = value
    return prev


def restore_token(prev):
    set_token(prev)


# ------------------------------------------------------------------ 3. knockout rule
class TestKnockoutRule(unittest.TestCase):
    def brute(self, P, b):
        B = Decimal(repr(b))
        return np.array([abs(P[i + 1] - P[i]) < B * P[i] for i in range(len(P) - 1)])

    def test_b_frac(self):
        self.assertEqual(lib.b_frac(2.3454e-6), (23454, 10 ** 10))
        self.assertEqual(lib.b_frac(0.000612552024), (612552024, 10 ** 12))

    def test_exact_integer_w_is_a_touch(self):
        # b*P exactly 15 pips: a 15-pip move touches the barrier and knocks out, 14 survives
        b, P0 = 2.5e-6, 6_000_000
        self.assertEqual(Decimal(repr(b)) * P0, 15)
        P = np.array([P0, P0 + 15, P0 + 15 - 14, P0 + 15 - 14 + 16], np.int64)
        lv = lib.knockout(P[:-1], P[1:], b)
        self.assertEqual(int(lv['K'][0]), 15)
        self.assertEqual(float(lv['ph'][0]), 0.0)
        self.assertFalse(lv['stay'][0])                 # |d| = 15 = w -> knockout
        np.testing.assert_array_equal(lv['stay'], self.brute(P, b))

    def test_float_rounding_side_of_integer(self):
        # w lands a hair below/above an integer in float64; the exact value decides
        for b, P0 in ((1.1e-6, 10_000_000), (3.3e-6, 10_000_000), (4.7e-6, 10_000_000), (0.0007, 30_000),
                      (0.00013, 100_000), (0.00016, 300_000)):   # float: 12.999999999999998 / 48.00000000000001
            w_exact = Decimal(repr(b)) * P0
            self.assertEqual(w_exact, int(w_exact))
            k = int(w_exact)
            P = np.array([P0, P0 + k, P0 + k - (k - 1)], np.int64)
            lv = lib.knockout(P[:-1], P[1:], b)
            self.assertEqual(int(lv['K'][0]), k)
            self.assertFalse(lv['stay'][0], f'b={b} P={P0}: move of exactly w must knock out')
            np.testing.assert_array_equal(lv['stay'], self.brute(P, b))

    def test_random_against_decimal(self):
        rng = np.random.default_rng(1)
        for b in (2.3454e-6, 4.7141e-6, 0.000433139675, 0.000153137996):
            P = (3_000_000 if b < 1e-5 else 70_000) + np.cumsum(rng.integers(-40, 41, 3000))
            lv = lib.knockout(P[:-1], P[1:], b)
            np.testing.assert_array_equal(lv['stay'], self.brute(P, b))
            br, _ = lib.rule_breaches(P, b, ('raw_incl',))
            np.testing.assert_array_equal(br['raw_incl'], ~lv['stay'])

    def test_rule_variants_at_touch(self):
        # w = 30.9054, |d| = 31: raw_incl/ceil_incl knock out; ceil_strict survives (displayed 30.91 -> 31.0)
        b = 30.9054 / 71000
        P = np.array([71000, 71031], np.int64)
        br, lev = lib.rule_breaches(P, b)
        self.assertTrue(br['raw_incl'][0])
        self.assertTrue(br['ceil_incl'][0])
        self.assertFalse(br['ceil_strict'][0])
        self.assertTrue(lev['touch'][0])

    def test_runs_and_house_layout(self):
        br = np.array([0, 1, 0, 0, 1, 0, 1, 0, 0, 0], bool)
        runs, inprog, _ = lib.run_lengths(br)
        self.assertEqual(list(runs), [2, 1])
        self.assertEqual(inprog, 3)
        house, cov = lib.house_from_end(len(br), [9, 2, 1, 3])
        self.assertEqual(cov, 3)                        # the first run started before the feed
        np.testing.assert_array_equal(house[1:], br[1:].astype(np.int8))
        self.assertEqual(lib.backward_match(runs, inprog, [9, 2, 1, 3], cov), 3)


class TestModelAndVerdict(unittest.TestCase):
    def test_frozen_gmin_crash1000(self):
        law = lib.load_frozen()
        gm, v1, v2 = lib.g_min(0.04, 14, 2.3454e-6 / 1e-6, 1e-3, law)
        self.assertAlmostEqual(v1, 1.00165, places=5)   # synthesis: 1.00133 (internal) - 1.00165 (rect)
        self.assertAlmostEqual(v2, 1.00133, places=5)
        self.assertEqual(gm, v2)

    def test_law_a_ratio(self):
        pre = lib.load_prereg()
        for N in (1000, 500, 300):
            for g in (0.01, 0.03, 0.05):
                self.assertAlmostEqual(lib.law_a_ratio(N, g), lib.law_a_ratio(N, g, pre), places=6)

    def test_rounding_directions(self):
        self.assertEqual(lib.rounding_directions(0.000612552024, 331.63, 2, '0.204'), ['ceil'])
        self.assertIn('ceil', lib.rounding_directions(0.000433139675, 710.68, 2, '0.308'))

    def good_stats(self):
        return {'C4': {'z': 5.0, 'ratio_V1': 1.0, 'ratio_V2': 1.1, 'n_ticks': 1_000_000},
                'C5': {s: {'p_V1': 0.3, 'p_V2': 0.2} for s in lib.THRESH['decision_symbols']},
                'M_cells': [{'lo_V1': 1.0002, 'lo_V2': 1.0001, 'valid': True}],
                'M': {'V1': {'point': 1.0015, 'lo': 1.0005, 'hi': 1.0025, 'valid': True},
                      'V2': {'point': 1.0012, 'lo': 1.0003, 'hi': 1.0022, 'valid': True}},
                'D': {'point': 1.0014, 'lo': 1.0001, 'hi': 1.003, 'se': 0.0005, 'n_blocks': 20, 'valid': True, 'n': 300_000},
                'C3': {'T/2': 0.995, 'T/3': 0.996}, 'C1': 0.995, 'OR1': 'PASS', 'A7': 'PASS'}

    def test_verdicts(self):
        st = self.good_stats()
        self.assertEqual(lib.verdict(st)['label'], 'PASS-B')
        st['D'] = dict(st['D'], lo=0.999)
        self.assertEqual(lib.verdict(st)['label'], 'PASS-A')
        st['A7'] = 'FAIL'
        self.assertEqual(lib.verdict(st)['label'], 'INCONCLUSIVE')
        for key, val in (('D', dict(st['D'], hi=0.9995, point=0.999)), ('OR1', 'FAIL'),
                         ('C4', {'z': 0.5, 'ratio_V1': 1, 'ratio_V2': 1, 'n_ticks': 400_000}),
                         ('M', {'V1': {'point': .998, 'lo': .997, 'hi': .9995, 'valid': True},
                                'V2': {'point': .998, 'lo': .997, 'hi': .9992, 'valid': True}})):
            s2 = dict(self.good_stats(), **{key: val})
            self.assertEqual(lib.verdict(s2)['label'], 'KILL', key)
        s3 = dict(self.good_stats(), C3={'T/2': 0.9995, 'T/3': 0.99})
        v = lib.verdict(s3)
        self.assertEqual(v['label'], 'INCONCLUSIVE')
        self.assertFalse(v['checks']['A5'])
        s4 = dict(self.good_stats(), OR1='NOT_EVALUATED')
        self.assertEqual(lib.verdict(s4)['label'], 'INCONCLUSIVE')
        s5 = dict(self.good_stats(), OR1='NOT_EVALUATED', HALT=['CRASH1000 g=0.04 x: best rule matches 80%'])
        self.assertEqual(lib.verdict(s5)['label'], 'KILL')


# ------------------------------------------------------------------ 1. oracle regression
def load_der(name):
    rows = [l.split(',') for l in read_text(os.path.join(FIX, name)).split()[1:]]
    dec = max(len(r[1].split('.')[1]) for r in rows)
    return np.array([int(r[0]) for r in rows]), lib.to_pips([float(r[1]) for r in rows], dec)


def load_opus(path):
    with gzip.open(path, 'rt') as fh:
        pip = int(fh.readline().strip().split('=')[1])
        rows = [l.split(',') for l in fh.read().split()]
    return np.array([int(r[0]) for r in rows]), lib.to_pips([float(r[1]) for r in rows], pip)


@unittest.skipUnless(os.path.exists(R100_CSV), 'opus-thoughts/data/R_100.csv.gz not present')
class TestOracleRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = apd.read_json(os.path.join(FIX, 'oracle_snapshots_2026-06-07.json'))['accumulators']
        feeds = {'R_100': load_opus(R100_CSV), '1HZ100V': load_der('der_1HZ100V.csv'), 'R_25': load_der('der_R_25.csv')}
        cls.res = {}
        for sym, (t, P) in feeds.items():
            s = spec[sym]['barrier_info']
            cls.res[sym] = lib.oracle_align(t, P, s['tick_size_barrier'], s['ticks_stayed_in'], s['last_tick_epoch'])

    def test_r100_primary_99_plus_in_progress_19(self):
        r = self.res['R_100']
        self.assertEqual(r['mode'], 'primary')
        ri = r['rules']['raw_incl']
        self.assertEqual((ri['matched'], ri['covered'], ri['in_progress']), (100, 100, 19))
        self.assertTrue(ri['full'])
        self.assertEqual(ri['longest'], 99)             # 99 completed runs
        self.assertLessEqual(r['shuffle_null']['max_longest'], 4)
        self.assertAlmostEqual(r['eps_sweep']['eps_min'], -0.009, places=6)

    def test_list_trailing_feed_is_realigned(self):
        # The same snapshot with the feed running 1-2 ticks past the list's in-progress entry used to
        # score 0/100 on every rule; the bounded lag search recovers the full match, and no further.
        spec = apd.read_json(os.path.join(FIX, 'oracle_snapshots_2026-06-07.json'))['accumulators']['R_100']
        s = spec['barrier_info']
        t, P = load_opus(R100_CSV)
        keep = t <= s['last_tick_epoch']
        t, P = t[keep], P[keep]
        step = int(t[-1] - t[-2])
        for extra, lag in ((1, 1), (2, 2), (3, None)):
            te = np.concatenate([t, t[-1] + step * np.arange(1, extra + 1)])
            Pe = np.concatenate([P, np.repeat(P[-1], extra)])     # zero moves: no knockouts added
            r = lib.oracle_align(te, Pe, s['tick_size_barrier'], s['ticks_stayed_in'], int(te[-1]),
                                 eps_sweep=False, n_shuffle=2)
            ri = r['rules']['raw_incl']
            if lag is None:
                self.assertEqual(ri['matched'], 0)
            else:
                self.assertEqual((r['lag'], ri['matched'], ri['covered'], ri['full']), (lag, 100, 100, True))

    def test_completed_runs_scored_without_in_progress_entry(self):
        # An in-progress entry off by a few ticks no longer zeroes the rule; a wrong completed run still fails.
        spec = apd.read_json(os.path.join(FIX, 'oracle_snapshots_2026-06-07.json'))['accumulators']['R_100']
        s = spec['barrier_info']
        t, P = load_opus(R100_CSV)
        L = list(s['ticks_stayed_in'])
        bad_ip = L[:-1] + [L[-1] + 3]
        r = lib.oracle_align(t, P, s['tick_size_barrier'], bad_ip, s['last_tick_epoch'], eps_sweep=False, n_shuffle=2)
        ri = r['rules']['raw_incl']
        self.assertEqual((ri['matched'], ri['completed_matched'], ri['completed_full'], ri['in_progress_delta']),
                         (0, 99, True, -3))
        rec = dict(r, sym='CRASH1000', g=0.04, snapshot='t')
        self.assertEqual(lib.oracle_outcomes([rec])['OR1']['CRASH1000']['status'], 'PASS')
        bad_run = list(L)
        bad_run[-5] += 1
        r2 = lib.oracle_align(t, P, s['tick_size_barrier'], bad_run, s['last_tick_epoch'], eps_sweep=False, n_shuffle=2)
        self.assertFalse(r2['rules']['raw_incl']['completed_full'])
        self.assertEqual(r2['rules']['raw_incl']['completed_matched'], 3)
        rec2 = dict(r2, sym='CRASH1000', g=0.04, snapshot='t')
        self.assertEqual(lib.oracle_outcomes([rec2])['OR1']['CRASH1000']['status'], 'FAIL')

    def test_1hz100v_75_of_75(self):
        r = self.res['1HZ100V']
        self.assertEqual(r['mode'], 'sub')
        self.assertEqual((r['rules']['raw_incl']['matched'], r['rules']['raw_incl']['covered']), (75, 75))
        self.assertEqual(r['rules']['ceil_strict']['matched'], 44)
        self.assertEqual((r['eps_sweep']['eps_min'], r['eps_sweep']['eps_max']), (-0.0016, 0.0012))

    def test_r25_73_of_73(self):
        r = self.res['R_25']
        self.assertEqual((r['rules']['raw_incl']['matched'], r['rules']['raw_incl']['covered']), (73, 73))
        self.assertEqual(r['rules']['ceil_strict']['matched'], 53)

    def test_exact_touches_are_knockouts(self):
        touches = [(s, round(d['phase'], 4), d['house'], d['raw_incl'], d['ceil_strict'])
                   for s in ('1HZ100V', 'R_25') for d in self.res[s]['disagreements'] if d['exact_touch']]
        self.assertEqual(sorted((s, p) for s, p, *_ in touches),
                         [('1HZ100V', 0.9054), ('1HZ100V', 0.9604), ('R_25', 0.9633)])
        for s, p, house, raw, ceil_strict in touches:
            self.assertEqual((house, raw, ceil_strict), ('breach', 'breach', 'survive'))
        for s in ('1HZ100V', 'R_25'):              # OR-3 style events are all breaches
            self.assertTrue(all(e['house'] == 'breach' for e in self.res[s]['OR3_events']))


# ------------------------------------------------------------------ 2. analyze on local data
LOCAL_FILES = [os.path.join(LOCAL, f'{s}.json.gz') for s in ('CRASH1000', 'CRASH500', 'BOOM1000', 'BOOM500')]


@unittest.skipUnless(all(os.path.exists(f) for f in LOCAL_FILES), 'repo data/*.json.gz not present')
class TestAnalyzeLocal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = os.path.join(cls.tmp.name, 'run')
        with quiet():
            apd.main(['analyze', '--local-json', *LOCAL_FILES, '--barrier', 'CRASH1000=0.04:2.3454e-6',
                      'CRASH500=0.04:4.7141e-6', 'CRASH1000=0.03:2.447e-6', 'CRASH500=0.03:4.9222e-6',
                      'BOOM1000=0.03:2.4506e-6', 'BOOM500=0.03:4.9295e-6', '--outdir', out,
                      '--reps-ticks', '2000', '--reps-model', '300'])
        cls.A = apd.read_json(os.path.join(out, 'analysis.json'))
        cls.summary = read_text(os.path.join(out, 'summary.txt')).strip().split('\n')
        cls.out = out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_crash500_band_G(self):
        b = self.A['symbols']['CRASH500']['S3']['0.04']['bands']['[0,.125)']
        self.assertEqual(b['n'], 13826)
        self.assertAlmostEqual(b['G'], 1.00239, places=5)
        self.assertEqual(b['n_blocks'], 10)

    def test_c4_positive(self):
        c4 = self.A['decision']['C4']
        self.assertGreater(c4['contrast'], 0)
        self.assertGreaterEqual(c4['z'], 3)
        self.assertTrue(0.65 <= c4['ratio_V1'] <= 1.35)
        rb = c4['per_symbol']['CRASH500']['real_barrier']      # the synthesis' "+0.0176 (z 3.2)"
        self.assertAlmostEqual(rb['contrast'], 0.0176, places=4)
        self.assertTrue(3.0 <= rb['z_two_sample'] <= 3.5)

    def test_ticks_converted_and_verdict_line(self):
        self.assertTrue(os.path.exists(os.path.join(self.out, 'ticks', 'CRASH500.npz')))
        self.assertTrue(self.summary[-1].startswith('VERDICT: INCONCLUSIVE'))   # no oracle offline -> A6 open
        self.assertEqual(self.A['decision']['OR1'], 'NOT_EVALUATED')
        # --barrier overrides only: never verified against an authenticated quote
        self.assertEqual(self.A['decision']['A7'], 'FAIL')
        self.assertIn('not verified against authenticated terms', self.A['a7']['detail'])
        self.assertEqual(self.A['inputs']['barrier_sources'], ['override'])
        self.assertFalse(os.path.exists(os.path.join(self.out, 'analysis_public.json')))

    def test_watcher_csv_layout_gives_same_map(self):
        # ticks/<SYM>/<YYYY-MM-DD>.csv (header epoch,quote) + quotes.jsonl, as the watcher writes them
        d = os.path.join(self.tmp.name, 'watch')
        with gzip.open(LOCAL_FILES[1], 'rt') as fh:
            a = json.load(fh)
        os.makedirs(os.path.join(d, 'ticks', 'CRASH500'))
        days = {}
        for ep, px in a:
            days.setdefault(apd.utc_str(ep)[:10], []).append(f'{int(ep)},{px:.3f}')
        for day, rows in days.items():
            with open(os.path.join(d, 'ticks', 'CRASH500', f'{day}.csv'), 'w') as fh:
                fh.write('epoch,quote\n' + '\n'.join(rows) + '\n')
        with open(os.path.join(d, 'quotes.jsonl'), 'w') as fh:
            fh.write(json.dumps({'sym': 'CRASH500', 'g': 0.04, 'b': 4.7141e-6, 'spot': a[-1][1], 'pip': 3,
                                 'epoch': int(a[0][0])}) + '\n')
        with quiet():
            apd.main(['analyze', '--from-dir', d, '--outdir', os.path.join(d, 'out'), '--reps-ticks', '300',
                      '--reps-model', '100'])
        A2 = apd.read_json(os.path.join(d, 'out', 'analysis.json'))
        b = A2['symbols']['CRASH500']['S3']['0.04']['bands']['[0,.125)']
        self.assertEqual((b['n'], round(b['G'], 5)), (13826, 1.00239))
        self.assertIn('S7', A2['symbols']['CRASH500'])
        with self.assertRaises(FileExistsError):          # 'x' mode: nothing is ever overwritten
            with quiet():
                apd.main(['analyze', '--from-dir', d, '--outdir', os.path.join(d, 'out'), '--reps-ticks', '50',
                          '--reps-model', '20'])


# ------------------------------------------------------------------ 4. mock-ws end to end
class FakeMarket:
    """Synthetic Boom/Crash (universal step law) and R_100 feeds, law-A barriers (recorded values where
    the repo has them), and ticks_stayed_in computed from the feed with the repo rule."""
    SPEC = {'CRASH1000': (5700.0, 3, 1), 'CRASH500': (3000.0, 3, 1), 'BOOM500': (5400.0, 3, 1), 'R_100': (620.0, 2, 2)}
    MAXT = {0.01: 250, 0.02: 135, 0.03: 90, 0.04: 65, 0.05: 55}

    def __init__(self, n=40000, seed=11):
        rng = np.random.default_rng(seed)
        self.feed, self.bar = {}, {}
        rec = lib.load_prereg()['recorded']
        for sym, (S0, dec, iv) in self.SPEC.items():
            ep = 1790000000 + iv * np.arange(n)
            if lib.family(sym) == 'vol':
                sig = 1.0 / math.sqrt(365 * 86400 / iv)
                x = S0 * np.exp(np.cumsum(sig * rng.standard_normal(n)))
                for g in lib.RATES:
                    self.bar[(sym, g)] = float(f'{sig * lib.stats.norm.ppf((1 + lib.PG[g]) / 2):.9g}')
            else:
                N = lib.sym_N(sym)
                sgn = 1 if lib.family(sym) == 'crash' else -1
                Y = np.abs(rng.normal(lib.FN_MU, lib.FN_SG, n))
                spk = rng.random(n) < 1 / N
                rel = np.where(spk, -sgn * 1e-3 * Y, sgn * 1e-3 / N * Y)
                rel[0] = 0
                x = S0 * np.cumprod(1 + rel)
                for g in lib.RATES:
                    v = rec.get(sym, {}).get(str(g))
                    self.bar[(sym, g)] = float(v) if isinstance(v, float) else float(f'{lib.law_a_kappa(N, g) * 1e-3 / N:.5g}')
            self.feed[sym] = (ep, np.rint(x * 10 ** dec).astype(np.int64), dec)
        self.lists = {}

    def stayed_in(self, sym, g):
        if (sym, g) not in self.lists:
            ep, P, _ = self.feed[sym]
            br, _ = lib.rule_breaches(P[-25000:], self.bar[(sym, g)], ('raw_incl',))
            runs, inprog, _ = lib.run_lengths(br['raw_incl'])
            self.lists[(sym, g)] = [int(v) for v in runs[-99:]] + [inprog]
        return self.lists[(sym, g)]

    def history(self, p):
        sym = p['ticks_history']
        if sym not in self.feed:
            return {'echo_req': p, 'error': {'code': 'InvalidSymbol', 'message': 'Symbol is invalid.'}, 'msg_type': 'history'}
        ep, P, dec = self.feed[sym]
        end = int(ep[-1]) if p.get('end') == 'latest' else int(p['end'])
        start = int(p['start']) if 'start' in p else end - 86400
        idx = np.flatnonzero((ep >= start) & (ep <= end))[-int(p['count']):]
        return {'echo_req': p, 'msg_type': 'history', 'pip_size': dec,
                'history': {'prices': [round(int(v) * 10.0 ** -dec, dec) for v in P[idx]], 'times': ep[idx].tolist()}}

    def proposal(self, p, auth=False):
        sym, g = p['underlying_symbol'], p['growth_rate']
        if (sym, g) not in self.bar:
            return {'echo_req': p, 'msg_type': 'proposal',
                    'error': {'code': 'ContractBuyValidationError', 'message': 'Trading is not offered for this asset.'}}
        ep, P, dec = self.feed[sym]
        b = auth_barrier(sym, self.bar[(sym, g)]) if auth else self.bar[(sym, g)]   # stayed_in: public b
        spot = round(int(P[-1]) * 10.0 ** -dec, dec)
        dist = (Decimal(repr(b)) * Decimal(repr(spot))).quantize(Decimal(1).scaleb(-(dec + 1)), ROUND_CEILING)
        return {'echo_req': p, 'msg_type': 'proposal', 'proposal': {
            'ask_price': p['amount'],
            'contract_details': {'barrier_spot_distance': str(dist), 'high_barrier': str(Decimal(repr(spot)) + dist),
                                 'last_tick_epoch': int(ep[-1]), 'low_barrier': str(Decimal(repr(spot)) - dist),
                                 'maximum_payout': 6000, 'maximum_stake': '1000.00', 'maximum_ticks': self.MAXT[g],
                                 'minimum_stake': '1.00', 'tick_size_barrier': b,
                                 'tick_size_barrier_percentage': f'{b * 100:.5f}%', 'ticks_stayed_in': self.stayed_in(sym, g)},
            'date_expiry': 1812412799, 'date_start': int(ep[-1]) + 1, 'display_value': f"{p['amount']:.2f}",
            'id': 'mock-0000', 'payout': 0, 'spot': spot, 'spot_time': int(ep[-1]),
            'validation_params': {'max_payout': '6000.00', 'max_ticks': self.MAXT[g],
                                  'stake': {'max': '1000.00', 'min': '1.00'},
                                  'take_profit': {'max': '5990.00', 'min': '0.01'}}}}


MARKET = None
TIGHT = ('CRASH1000', 'CRASH500', 'BOOM300N')     # logged-in accounts are sold a tighter barrier here


def auth_barrier(sym, b):
    return float(f'{b * 0.975:.7g}') if sym in TIGHT else b


class FakeDerivWS:
    """Stands in for deriv_api.DerivWS: token '' is the public connection, a non-empty token with
    account_type 'demo' the authenticated one (same call()/close() surface, account filled in)."""
    calls = []
    auth_calls = []

    def __init__(self, token=None, app_id=None, timeout=30, account_type=None):
        assert token == '' or (token and account_type == 'demo'), 'public, or authenticated on the demo account'
        self.auth = bool(token)
        self.account = {'account_type': 'demo', 'account_id': 'VRTC0'} if self.auth else {}
        self._rid = itertools.count(1)

    def call(self, payload, retries=3):
        FakeDerivWS.calls.append(payload)
        if self.auth:
            FakeDerivWS.auth_calls.append(payload)
        assert 'buy' not in payload and 'sell' not in payload, 'read-only tool sent an order'
        if 'ticks_history' in payload:
            r = MARKET.history(payload)
        elif 'proposal' in payload:
            r = MARKET.proposal(payload, self.auth)
        else:
            r = {'error': {'code': 'UnrecognisedRequest', 'message': 'mock'}}
        r['req_id'] = next(self._rid)
        return r

    _call = call

    def close(self):
        pass


class TestMockEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global MARKET
        MARKET = FakeMarket()
        cls.saved = (apd.DerivWS, apd.PAUSE_PROPOSAL, apd.PAUSE_SYMBOL, derivfetch.SLEEP)
        cls.token = set_token('fake')
        apd.DerivWS, apd.PAUSE_PROPOSAL, apd.PAUSE_SYMBOL, derivfetch.SLEEP = FakeDerivWS, 0, 0, 0
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, 'all')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apd.main(['all', '--outdir', cls.out, '--symbols', 'CRASH1000', 'CRASH500', 'BOOM500', 'BOOM50', 'R_100',
                      '--oracle-symbols', 'CRASH1000', 'CRASH500', 'R_100', '--ticks', '30000',
                      '--reps-ticks', '300', '--reps-model', '100'])
        cls.stdout = buf.getvalue()

    @classmethod
    def tearDownClass(cls):
        apd.DerivWS, apd.PAUSE_PROPOSAL, apd.PAUSE_SYMBOL, derivfetch.SLEEP = cls.saved
        restore_token(cls.token)
        cls.tmp.cleanup()

    def load(self, name):
        return apd.read_json(os.path.join(self.out, name))

    def test_files_written(self):
        for f in ('ladder.json', 'oracle_snap1.json', 'oracle_snap2.json', 'oracle_align.json', 'collect.json',
                  'analysis.json', 'summary.txt', 'ticks/CRASH1000.npz', 'oracle_ticks/CRASH500.npz'):
            self.assertTrue(os.path.exists(os.path.join(self.out, f)), f)
        z = np.load(os.path.join(self.out, 'ticks', 'CRASH500.npz'))
        self.assertEqual((len(z['ep']), int(z['pip'])), (30000, 3))

    def test_ladder_checks(self):
        lad = self.load('ladder.json')
        ch = lad['checks']
        self.assertTrue(ch['L1']['ok'])
        # L2 compares the authenticated quote with the (public-era) recorded value: the Crash barriers
        # sold to a logged-in account are tighter, so they read CHANGED while the public quote matches
        l2 = {(r['sym'], r['g']): r for r in ch['L2']['rows']}
        self.assertFalse(ch['L2']['ok'])
        for key in (('CRASH1000', '0.04'), ('CRASH500', '0.04'), ('CRASH1000', '0.03')):
            self.assertEqual((l2[key]['ok'], l2[key]['public_ok'], l2[key]['terms']), (False, True, 'authenticated'))
        self.assertEqual((l2[('BOOM500', '0.03')]['ok'], l2[('BOOM500', '0.03')]['public_ok']), (True, True))
        self.assertIn('(public 2.3454e-06, = recorded)', self.stdout)
        self.assertTrue(ch['L3']['ok'])
        self.assertIn('ceil', ch['L4']['consistent_with_all'])
        self.assertNotIn('BOOM50', {k for k, v in lad['rounds'][0]['quotes'].items() if v})
        self.assertIn('error ContractBuyValidationError', self.stdout)       # server errors are echoed
        self.assertIn('error InvalidSymbol', self.stdout)
        d = lad['derived']['CRASH500']['0.04'][0]
        self.assertAlmostEqual(d['w'], auth_barrier('CRASH500', 4.7141e-6) * d['spot'] * 1000, places=9)

    def test_ladder_stores_authenticated_barrier(self):
        lad = self.load('ladder.json')
        self.assertEqual(lad['terms'], 'authenticated')
        qs = lad['rounds'][0]['quotes']
        for sym in ('CRASH1000', 'CRASH500', 'BOOM500', 'R_100'):
            for gs, q in qs[sym].items():
                pub = MARKET.bar[(sym, float(gs))]
                self.assertEqual((q['terms'], q['b'], q['b_public']), ('authenticated', auth_barrier(sym, pub), pub))
        self.assertEqual(qs['CRASH1000']['0.04']['b'], 2.286765e-06)
        self.assertEqual(lad['stake_check']['CRASH1000']['1'], 2.286765e-06)      # stake check: authenticated
        self.assertIn('0.04:2.286765e-06 (pub 2.3454e-06)', self.stdout)
        self.assertIn('[5/5 differ]', self.stdout)
        self.assertIn('differs from the public one on 10/20 quoted cells', self.stdout)
        # analysed set = authenticated; public set = b_public
        bars, _, _, hist = apd.ladder_inputs(lad)
        self.assertEqual(bars['CRASH1000'][0.04], 2.286765e-06)
        self.assertEqual(apd.ladder_public_bars(lad)['CRASH1000'][0.04], 2.3454e-06)
        self.assertEqual({h['terms'] for h in hist}, {'authenticated', 'public'})
        A = self.load('analysis.json')
        self.assertEqual(A['inputs']['barriers']['CRASH1000']['0.04'], 2.286765e-06)
        self.assertEqual(A['inputs']['public_barriers']['CRASH1000']['0.04'], 2.3454e-06)
        self.assertEqual(A['inputs']['barrier_sources'], ['authenticated ladder'])
        w = A['symbols']['CRASH1000']['S7']['0.04']
        self.assertAlmostEqual(w['w'], 2.286765e-06 * w['spot'] * 1000, places=9)
        self.assertIn('= latest authenticated quote', A['a7']['detail'])
        self.assertIn('authenticated history covers the window: yes', A['a7']['detail'])

    def test_side_by_side_block(self):
        A, P = self.load('analysis.json'), self.load('analysis_public.json')
        self.assertEqual(P['inputs']['barriers']['CRASH1000']['0.04'], 2.3454e-06)
        lines = read_text(os.path.join(self.out, 'summary.txt')).strip().split('\n')
        i = lines.index('=== public vs authenticated barriers (verdict uses authenticated)')
        block = lines[i:-1]
        self.assertTrue(lines[-1].startswith('VERDICT: '))
        self.assertEqual(lines[-1], f"VERDICT: {A['verdict']['label']} {'; '.join(A['verdict']['reasons'])}")
        self.assertEqual(sum(l.startswith('VERDICT:') for l in lines), 1)
        text = '\n'.join(block)
        self.assertIn('CRASH1000 g=0.04  b_pub 2.3454e-06  b_auth 2.286765e-06', text)
        self.assertIn('CRASH500 g=0.01', text)
        self.assertNotIn('BOOM500 g=', text)                         # same barrier on both: not listed
        for key in ('S3 [0,.125)', 'S3 [.05,.125)', 'S3 [.75,1)', 'eligible K', 'S6 choice', 'S7 ',
                    'pooled D', 'pooled M V1', 'pooled M V2', 'C4 at 4%'):
            self.assertIn(key, text)
        self.assertIn(f"verdict         pub {P['verdict']['label']} (information only)  auth {A['verdict']['label']}", text)
        self.assertEqual(A['public_vs_authenticated']['public_verdict'], P['verdict']['label'])
        # the two analyses really used different barriers
        s3 = lambda X: {k: (v['n'], v['G']) for k, v in X['symbols']['CRASH1000']['S3']['0.04']['bands'].items()}
        self.assertNotEqual(s3(A), s3(P))

    def test_oracle_alignment(self):
        o = self.load('oracle_align.json')
        recs = o['records']
        self.assertEqual(len(recs), 2 * 3 * 5)
        for r in recs:
            self.assertEqual(r['mode'], 'primary')
            self.assertTrue(r['rules']['raw_incl']['full'], (r['sym'], r['g']))
            self.assertGreaterEqual(r['covered'], 50)
        self.assertEqual(o['outcomes']['OR1']['status'], 'PASS')
        self.assertEqual(o['outcomes']['HALT'], [])
        self.assertIn(o['outcomes']['OR2']['decision'], ('EXTEND', 'KEEP'))
        self.assertIn(o['outcomes']['OR3']['status'], ('PASS', 'NO_EVENTS'))

    def test_analysis_and_verdict(self):
        A = self.load('analysis.json')
        self.assertEqual(A['decision']['OR1'], 'PASS')
        self.assertEqual(A['decision']['A7'], 'PASS')
        self.assertTrue(A['verdict']['checks']['A6'])
        self.assertIn('S7', A['symbols']['CRASH1000'])
        self.assertEqual(set(A['symbols']), {'CRASH1000', 'CRASH500', 'BOOM500', 'R_100'})
        last = read_text(os.path.join(self.out, 'summary.txt')).strip().split('\n')[-1]
        self.assertRegex(last, r'^VERDICT: (PASS-A|PASS-B|KILL|INCONCLUSIVE) ')

    def test_offline_reruns(self):
        # oracle / collect / analyze re-run from an earlier outdir without the network
        n_calls = len(FakeDerivWS.calls)
        out2 = os.path.join(self.tmp.name, 'rerun')
        with quiet():
            apd.main(['oracle', '--from-dir', self.out, '--outdir', out2])
            rep = apd.run_collect(argparse.Namespace(symbols=None, from_dir=self.out, ticks=None), out2)
            apd.main(['analyze', '--from-dir', self.out, '--oracle', os.path.join(out2, 'oracle_align.json'),
                      '--outdir', out2, '--reps-ticks', '100', '--reps-model', '50'])
        self.assertEqual(len(FakeDerivWS.calls), n_calls)
        o1, o2 = self.load('oracle_align.json'), apd.read_json(os.path.join(out2, 'oracle_align.json'))
        self.assertEqual([r['rules']['raw_incl']['matched'] for r in o1['records']],
                         [r['rules']['raw_incl']['matched'] for r in o2['records']])
        self.assertEqual(rep['CRASH500']['n'], 30000)
        A = apd.read_json(os.path.join(out2, 'analysis.json'))
        self.assertEqual(A['oracle']['n_records'], 30)           # --oracle replaces the outdir's alignment
        self.assertEqual(A['decision']['OR1'], 'PASS')

    def test_read_only(self):
        kinds = {next(k for k in ('proposal', 'ticks_history') if k in c) for c in FakeDerivWS.calls}
        self.assertEqual(kinds, {'proposal', 'ticks_history'})
        self.assertFalse(any('buy' in c or 'sell' in c for c in FakeDerivWS.calls))
        # the authenticated connection only quotes; tick history and pip sizes stay public
        self.assertTrue(FakeDerivWS.auth_calls)
        self.assertTrue(all('proposal' in c and c['contract_type'] == 'ACCU' for c in FakeDerivWS.auth_calls))
        # the oracle snapshots are public proposals (the list matches the public barrier)
        snap = self.load('oracle_snap1.json')['snaps']['CRASH1000']['0.04']
        self.assertEqual(snap['b'], 2.3454e-06)
        amounts = {c['amount'] for c in FakeDerivWS.calls if 'proposal' in c}
        self.assertEqual(amounts, {10, 1, 1.13, 100})


# ------------------------------------------------------------------ review regressions
def crash_feed(N, S0, n, seed, t0=1790002800, dec=3):
    """Synthetic Crash feed on the universal small-step law (t0 is a UTC hour start)."""
    rng = np.random.default_rng(seed)
    Y = np.abs(rng.normal(lib.FN_MU, lib.FN_SG, n))
    spk = rng.random(n) < 1 / N
    rel = np.where(spk, -1e-3 * Y, 1e-3 / N * Y)
    rel[0] = 0
    return t0 + np.arange(n), np.rint(S0 * np.cumprod(1 + rel) * 10 ** dec).astype(np.int64)


class TestReviewRegressions(unittest.TestCase):
    def test_sub_mode_records_do_not_decide(self):
        # the house follows raw_incl exactly, but the feed ends before last_tick_epoch (sub mode): that
        # used to fail OR-1 (KILL K4) and raise HALT; only primary records may decide
        b = 2.3454e-6
        ep, P = crash_feed(1000, 5700.0, 40000, 11)
        br, _ = lib.rule_breaches(P[-25000:], b, ('raw_incl',))
        runs, inprog, _ = lib.run_lengths(br['raw_incl'])
        L = [int(v) for v in runs[-99:]] + [inprog]
        recs = []
        for short in (0, 300, 3000):
            keep = ep <= ep[-1] - short
            r = lib.oracle_align(ep[keep], P[keep], b, L, int(ep[-1]), eps_sweep=False, n_shuffle=2)
            r.update(sym='CRASH1000', g=0.04, snapshot=f'short{short}')
            recs.append(r)
        self.assertEqual([r['mode'] for r in recs], ['primary', 'sub', 'sub'])
        self.assertTrue(recs[0]['rules']['raw_incl']['full'])
        self.assertFalse(recs[1]['rules']['raw_incl']['full'])      # covered counts runs the feed cannot hold
        oc = lib.oracle_outcomes(recs)
        self.assertEqual(oc['OR1']['CRASH1000'], {'status': 'PASS', 'qualifying_snapshots': 1, 'failures': []})
        self.assertEqual(oc['HALT'], [])
        oc = lib.oracle_outcomes(recs[1:])
        self.assertEqual((oc['OR1']['CRASH1000']['status'], oc['HALT'], oc['OR2']['n_events']), ('NOT_EVALUATED', [], 0))

    def test_a7_new_barrier_must_be_quoted_from_the_window_start(self):
        t0 = 1790000000
        series = {s: (np.arange(t0, t0 + 400000), None, 3) for s in lib.THRESH['decision_symbols']}
        old = {'CRASH1000': 2.28724e-6, 'CRASH500': 4.598554e-6}
        bars = {'CRASH1000': {0.04: 2.4e-6}, 'CRASH500': {0.04: 4.8e-6}}

        def hist(t_new):                    # old barrier quoted 100 s before the window, the new one at t_new
            return [{'sym': s, 'g': 0.04, 'b': b, 't': t, 'terms': 'authenticated'} for s in old
                    for t, b in ((t0 - 100, old[s]), (t_new, bars[s][0.04]))]
        a = apd.a7_status(bars, hist(t0 + 5000), series, 'test')
        self.assertEqual(a['status'], 'FAIL')           # the old barrier was in force when the window opened
        self.assertIn('changed inside the window', a['detail'])
        self.assertEqual(apd.a7_status(bars, hist(t0), series, 'test')['status'], 'PASS')   # split at the change
        a = apd.a7_status(bars, hist(t0 + 400001), series, 'test')     # new b quoted only after the window
        self.assertEqual(a['status'], 'FAIL')

    def test_a7_uses_authenticated_terms(self):
        t0 = 1790000000
        series = {s: (np.arange(t0, t0 + 400000), None, 3) for s in lib.THRESH['decision_symbols']}
        pub = {'CRASH1000': 2.3454e-6, 'CRASH500': 4.7141e-6}
        auth = {'CRASH1000': 2.28724e-6, 'CRASH500': 4.598554e-6}
        hist = [{'sym': s, 'g': 0.04, 'b': b, 't': t0 + 10, 'terms': terms} for s in pub
                for terms, b in (('public', pub[s]), ('authenticated', auth[s]))]
        a = apd.a7_status({s: {0.04: pub[s]} for s in pub}, hist, series, 'ladder')     # analysed = public
        self.assertEqual(a['status'], 'FAIL')
        self.assertIn('!= latest authenticated quote 2.28724e-06', a['detail'])
        a = apd.a7_status({s: {0.04: auth[s]} for s in auth}, hist, series, 'ladder')   # analysed = authenticated
        self.assertEqual(a['status'], 'PASS')
        self.assertIn('authenticated history covers the window: yes', a['detail'])
        self.assertIn('recorded 2.3454e-06, public 2.3454e-06', a['detail'])
        late = [dict(h, t=t0 + 500000) for h in hist]                  # quoted only after the window
        a = apd.a7_status({s: {0.04: auth[s]} for s in auth}, late, series, 'ladder')
        self.assertEqual(a['status'], 'PASS')
        self.assertIn('covers the window: no -- the barrier during the window is assumed, not observed', a['detail'])
        # a legacy (public-only) ladder: its barrier is analysed as a fallback, A7 fails
        lad = {'pip': {'CRASH1000': 3, 'CRASH500': 3}, 'rounds': [{'quotes': {
            s: {'0.04': {'b': pub[s], 'spot': 3000.0, 'spot_time': t0 + 10}} for s in pub}}]}
        bars, _, _, h = apd.ladder_inputs(lad)
        self.assertEqual(bars, {})
        self.assertEqual(apd.ladder_public_bars(lad), {s: {0.04: pub[s]} for s in pub})
        self.assertEqual({x['terms'] for x in h}, {'public'})
        a = apd.a7_status(apd.ladder_public_bars(lad), h, series, 'public ladder (legacy)')
        self.assertEqual(a['status'], 'FAIL')
        self.assertIn('analysed barrier not verified against authenticated terms', a['detail'])

    def test_legacy_ladder_falls_back_to_public_and_fails_a7(self):
        ep, P = crash_feed(500, 3000.0, 20000, 13)
        lad = {'pip': {'CRASH500': 3}, 'rounds': [{'quotes': {'CRASH500': {'0.04': {
            'b': 4.7141e-6, 'spot': P[-1] / 1000, 'spot_time': int(ep[-1])}}}}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'CRASH500.json.gz')
            with gzip.open(path, 'wt') as fh:
                json.dump([[int(t), p / 1000] for t, p in zip(ep, P)], fh)
            with open(os.path.join(tmp, 'ladder.json'), 'w') as fh:
                json.dump(lad, fh)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                apd.main(['analyze', '--local-json', path, '--ladder', os.path.join(tmp, 'ladder.json'),
                          '--outdir', os.path.join(tmp, 'out'), '--reps-ticks', '50', '--reps-model', '20'])
            A = apd.read_json(os.path.join(tmp, 'out', 'analysis.json'))
            self.assertFalse(os.path.exists(os.path.join(tmp, 'out', 'analysis_public.json')))
        self.assertEqual(A['inputs']['barriers']['CRASH500']['0.04'], 4.7141e-6)
        self.assertEqual(A['inputs']['legacy_public_fallback'], ['CRASH500'])
        self.assertIn('analysing the PUBLIC (legacy) barrier', buf.getvalue())
        self.assertIn('S3', A['symbols']['CRASH500'])

    def test_quotes_log_accepts_verbatim_proposal_response(self):
        resp = {'echo_req': {'proposal': 1, 'underlying_symbol': 'CRASH500', 'growth_rate': 0.04}, 'msg_type': 'proposal',
                'proposal': {'contract_details': {'tick_size_barrier': 4.7141e-06}, 'spot': 3000.123,
                             'spot_time': 1790000000}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'quotes.jsonl')
            with open(path, 'w') as fh:
                fh.write(json.dumps(resp) + '\n')
            bars, spots, hist = apd.quotes_log_inputs(path)
            pub = apd.quotes_public_bars(path)
        # a verbatim response has no 'terms': a legacy public quote, not an analysed barrier
        self.assertEqual(bars, {})
        self.assertEqual(pub, {'CRASH500': {0.04: 4.7141e-06}})
        self.assertEqual(hist, [{'sym': 'CRASH500', 'g': 0.04, 'b': 4.7141e-06, 't': 1790000000, 'terms': 'public'}])
        rec = {'sym': 'CRASH500', 'g': 0.04, 'b': 4.598554e-06, 'b_public': 4.7141e-06, 'terms': 'authenticated',
               'epoch': 1790000100, 'spot': 3000.1, 'pip': 3}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'quotes.jsonl')
            with open(path, 'w') as fh:
                fh.write(json.dumps(resp) + '\n' + json.dumps(rec) + '\n')
            bars, spots, hist = apd.quotes_log_inputs(path)
            pub = apd.quotes_public_bars(path)
        self.assertEqual(bars, {'CRASH500': {0.04: 4.598554e-06}})
        self.assertEqual(pub, {'CRASH500': {0.04: 4.7141e-06}})
        self.assertEqual(spots, {'CRASH500': {0.04: (3000.1, 3)}})
        self.assertEqual([(h['b'], h['terms']) for h in hist],
                         [(4.7141e-06, 'public'), (4.7141e-06, 'public'), (4.598554e-06, 'authenticated')])

    def test_ladder_needs_token(self):
        saved, prev = apd.DerivWS, set_token(None)
        apd.DerivWS = FakeDerivWS
        n = len(FakeDerivWS.calls)
        try:
            with tempfile.TemporaryDirectory() as tmp, quiet():
                for cmd in ('ladder', 'all'):
                    with self.assertRaises(SystemExit) as cm:
                        apd.main([cmd, '--outdir', os.path.join(tmp, cmd)])
                    self.assertIn('ladder needs DERIV_TOKEN (demo)', str(cm.exception))
                    self.assertFalse(os.path.exists(os.path.join(tmp, cmd)))
                with self.assertRaises(SystemExit):
                    apd.auth_ws()
        finally:
            apd.DerivWS = saved
            restore_token(prev)
        self.assertEqual(len(FakeDerivWS.calls), n)

    def test_auth_ws_refuses_a_real_account(self):
        class RealWS(FakeDerivWS):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.account = {'account_type': 'real', 'account_id': 'CR0'}
        saved, prev = apd.DerivWS, set_token('fake')
        apd.DerivWS = RealWS
        try:
            with self.assertRaises(SystemExit) as cm:
                apd.auth_ws()
            self.assertIn('did not open a demo account', str(cm.exception))
        finally:
            apd.DerivWS = saved
            restore_token(prev)

    def test_analyze_with_ticks_from_one_hour_only(self):
        # every tick in hours of one parity: the cross-fit (C5) is n/a instead of an IndexError
        ep, P = crash_feed(500, 3000.0, 3000, 12)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'CRASH500.json.gz')
            with gzip.open(path, 'wt') as fh:
                json.dump([[int(t), p / 1000] for t, p in zip(ep, P)], fh)
            with quiet():
                apd.main(['analyze', '--local-json', path, '--barrier', 'CRASH500=0.04:4.7141e-6',
                          '--outdir', os.path.join(tmp, 'out'), '--reps-ticks', '50', '--reps-model', '20'])
            A = apd.read_json(os.path.join(tmp, 'out', 'analysis.json'))
        self.assertIsNone(A['symbols']['CRASH500']['S4']['C5']['p_V1'])
        self.assertEqual(A['verdict']['label'], 'INCONCLUSIVE')

    def test_oracle_tick_fetch_echoes_server_error(self):
        class ClosedWS(FakeDerivWS):
            def call(self, payload, retries=3):
                return {'echo_req': payload, 'msg_type': 'history',
                        'error': {'code': 'MarketIsClosed', 'message': 'This market is presently closed.'}}
        saved = (apd.DerivWS, apd.PAUSE_SYMBOL)
        apd.DerivWS, apd.PAUSE_SYMBOL = ClosedWS, 0
        buf = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf):
                snap = {'label': 'snap1', 'snaps': {'CRASH500': {'0.04': {'b': 4.7141e-6, 'ticks_stayed_in': [1],
                                                                          'last_tick_epoch': 1}}}}
                apd.run_oracle(argparse.Namespace(from_dir=None, oracle_ticks=20000), tmp, snaps=[snap])
        finally:
            apd.DerivWS, apd.PAUSE_SYMBOL = saved
        self.assertIn('error MarketIsClosed: This market is presently closed.', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
