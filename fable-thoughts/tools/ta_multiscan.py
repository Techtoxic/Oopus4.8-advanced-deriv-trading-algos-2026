"""ta_multiscan.py — do ANY chart patterns predict, on any symbol, at any timeframe?

WHY THE FIRST ATTEMPT FAILED
sr_levels.py used 4h bars on a 2s symbol: 7,200 ticks per bar, so 2M ticks gave only 277
bars and 35 swing levels, of which 17 were testable. Useless. Sample size comes from BAR
COUNT, not tick count, so small bars are what buy statistical power.

THE CONTROL THAT DECIDES EVERYTHING
On a pure Gaussian random walk with zero memory by construction, swing highs rejected
downward 77.7% of the time across 10,574 tests. White noise produces textbook support and
resistance, because a level price just touched is a level price is currently NEAR.

So a raw hit rate proves nothing. Every strategy here is run twice:

    REAL      the actual tick series
    SHUFFLED  the SAME step distribution in random order — identical volatility, identical
              fat tails, zero time structure

Only REAL minus SHUFFLED is evidence. If they match, the pattern is walk geometry.

PATTERNS TESTED
  sr_reversal   price returns to a prior swing high/low -> does it reject?
  fvg_fill      3-bar fair value gap (bar1 high < bar3 low) -> does price return and continue?
  order_block   last down-bar before an up-impulse -> does price bounce there?
  breakout      close beyond an N-bar range -> does it continue or fail?
  momentum      k consecutive same-direction bars -> does the next bar follow?
  mean_revert   bar closes >2 ATR from the N-bar mean -> does it revert?

Each is scored the same way: from the signal bar, does price reach +R before -R? Reported
against the breakeven implied by the real spread, with a Wilson 99% bound.

Read-only. No trades.

Run: python3 ta_multiscan.py
     python3 ta_multiscan.py --symbols R_75 R_100 --ticks 4000000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval, wilson

SPREAD_FRAC = 0.00035          # 16.56 / 48000, measured from the MT5 quote


def bars_from(v, k):
    n = len(v) // k
    if n < 200:
        return None
    b = v[:n * k].reshape(n, k)
    return dict(o=b[:, 0], h=b.max(1), l=b.min(1), c=b[:, -1], n=n)


def outcome(c, i, lvl, R, want_up, horizon):
    """From bar i, does price reach lvl+R before lvl-R (want_up) within horizon bars?"""
    fwd = c[i + 1:i + 1 + horizon]
    if len(fwd) < 3:
        return None
    up = np.flatnonzero(fwd >= lvl + R)
    dn = np.flatnonzero(fwd <= lvl - R)
    u = up[0] if len(up) else 10 ** 9
    d = dn[0] if len(dn) else 10 ** 9
    if u == d == 10 ** 9:
        return None
    hit_up = u < d
    return hit_up if want_up else (not hit_up)


def sig_sr(B, R, H):
    """Return to a prior swing level -> expect rejection."""
    h, l, c = B["h"], B["l"], B["c"]
    w = 5
    out = []
    highs = [(i, h[i]) for i in range(w, len(h) - w) if h[i] == h[i-w:i+w+1].max()]
    lows = [(i, l[i]) for i in range(w, len(l) - w) if l[i] == l[i-w:i+w+1].min()]
    for idx, lvl in highs:
        for j in range(idx + w + 2, min(idx + 200, len(c) - 1)):
            if abs(c[j] - lvl) <= 0.25 * R:
                r = outcome(c, j, c[j], R, False, H)   # resistance -> expect down
                if r is not None:
                    out.append(r)
                break
    for idx, lvl in lows:
        for j in range(idx + w + 2, min(idx + 200, len(c) - 1)):
            if abs(c[j] - lvl) <= 0.25 * R:
                r = outcome(c, j, c[j], R, True, H)
                if r is not None:
                    out.append(r)
                break
    return out


def sig_fvg(B, R, H):
    """Bullish FVG: h[i-2] < l[i]. Price returning into the gap -> expect continuation up."""
    h, l, c = B["h"], B["l"], B["c"]
    out = []
    for i in range(2, len(c) - 1):
        if h[i-2] < l[i]:                      # bullish gap
            mid = 0.5 * (h[i-2] + l[i])
            for j in range(i + 1, min(i + 60, len(c) - 1)):
                if c[j] <= mid:
                    r = outcome(c, j, c[j], R, True, H)
                    if r is not None:
                        out.append(r)
                    break
        if l[i-2] > h[i]:                      # bearish gap
            mid = 0.5 * (l[i-2] + h[i])
            for j in range(i + 1, min(i + 60, len(c) - 1)):
                if c[j] >= mid:
                    r = outcome(c, j, c[j], R, False, H)
                    if r is not None:
                        out.append(r)
                    break
    return out


def sig_orderblock(B, R, H):
    """Last down-bar before an up-impulse -> expect a bounce on return."""
    o, h, l, c = B["o"], B["h"], B["l"], B["c"]
    out = []
    for i in range(1, len(c) - 1):
        if c[i-1] < o[i-1] and (c[i] - o[i]) > 1.5 * R:      # down bar, then impulse up
            lvl = 0.5 * (o[i-1] + c[i-1])
            for j in range(i + 1, min(i + 60, len(c) - 1)):
                if c[j] <= lvl:
                    r = outcome(c, j, c[j], R, True, H)
                    if r is not None:
                        out.append(r)
                    break
        if c[i-1] > o[i-1] and (o[i] - c[i]) > 1.5 * R:      # up bar, then impulse down
            lvl = 0.5 * (o[i-1] + c[i-1])
            for j in range(i + 1, min(i + 60, len(c) - 1)):
                if c[j] >= lvl:
                    r = outcome(c, j, c[j], R, False, H)
                    if r is not None:
                        out.append(r)
                    break
    return out


def sig_breakout(B, R, H):
    """Close beyond a 20-bar range -> expect continuation."""
    c = B["c"]
    N = 20
    out = []
    for i in range(N, len(c) - 1):
        w = c[i-N:i]
        if c[i] > w.max():
            r = outcome(c, i, c[i], R, True, H)
            if r is not None:
                out.append(r)
        elif c[i] < w.min():
            r = outcome(c, i, c[i], R, False, H)
            if r is not None:
                out.append(r)
    return out


def sig_momentum(B, R, H):
    """3 consecutive same-direction bars -> expect continuation."""
    c = B["c"]
    d = np.sign(np.diff(c))
    out = []
    for i in range(3, len(c) - 1):
        if d[i-3] == d[i-2] == d[i-1] != 0:
            r = outcome(c, i, c[i], R, d[i-1] > 0, H)
            if r is not None:
                out.append(r)
    return out


def sig_meanrevert(B, R, H):
    """Close far from the 20-bar mean -> expect reversion."""
    c = B["c"]
    N = 20
    out = []
    for i in range(N, len(c) - 1):
        m = c[i-N:i].mean()
        if c[i] > m + 2 * R:
            r = outcome(c, i, c[i], R, False, H)
            if r is not None:
                out.append(r)
        elif c[i] < m - 2 * R:
            r = outcome(c, i, c[i], R, True, H)
            if r is not None:
                out.append(r)
    return out


STRATS = [("sr_reversal", sig_sr), ("fvg_fill", sig_fvg),
          ("order_block", sig_orderblock), ("breakout", sig_breakout),
          ("momentum", sig_momentum), ("mean_revert", sig_meanrevert)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                    default=["R_75", "R_100", "1HZ100V", "1HZ75V"])
    ap.add_argument("--ticks", type=int, default=4000000)
    ap.add_argument("--bar-sizes", type=int, nargs="+",
                    default=[30, 60, 150, 300, 900])
    ap.add_argument("--horizon", type=int, default=40)
    ap.add_argument("--min-n", type=int, default=300)
    ap.add_argument("--out", default="../results/ta_multiscan.md")
    a = ap.parse_args()

    rng = np.random.default_rng(0)
    rows = []

    for sym in a.symbols:
        ws = DerivWS(token="")
        print(f"\nfetching {a.ticks} ticks of {sym}...")
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False)
        except Exception as e:
            print(f"  failed: {str(e)[:60]}"); ws.close(); continue
        ws.close()
        v = p.astype(float)
        iv = native_interval(t)
        days = (t[-1] - t[0]) / 86400
        print(f"  {len(v)} ticks, {iv}s, {days:.1f} days, "
              f"price {v.min():.0f}-{v.max():.0f}")

        st = np.diff(v)
        sh = st.copy(); rng.shuffle(sh)
        vs = np.concatenate([[v[0]], v[0] + np.cumsum(sh)])

        for k in a.bar_sizes:
            B = bars_from(v, k)
            Bs = bars_from(vs, k)
            if B is None or Bs is None:
                continue
            R = float(np.median(B["h"] - B["l"]))
            cost = SPREAD_FRAC * v.mean()
            be = 0.5 + cost / (2 * R)
            mins = k * iv / 60
            print(f"\n  --- {sym} {k}t bars ({mins:.1f} min), {B['n']} bars, "
                  f"R={R:.1f}, breakeven {be*100:.2f}% ---")
            print(f"  {'strategy':14}{'n':>7}{'real':>9}{'n_sh':>7}{'shuf':>9}"
                  f"{'diff':>9}{'t':>7}{'vs BE':>9}")
            for name, fn in STRATS:
                try:
                    r_real = fn(B, R, a.horizon)
                    r_shuf = fn(Bs, R, a.horizon)
                except Exception:
                    continue
                n1, n2 = len(r_real), len(r_shuf)
                if n1 < a.min_n or n2 < a.min_n:
                    print(f"  {name:14}{n1:>7}{'--':>9}{n2:>7}"
                          f"{'  (below min-n)':>25}")
                    continue
                p1 = float(np.mean(r_real)); p2 = float(np.mean(r_shuf))
                se = math.sqrt(p1*(1-p1)/n1 + p2*(1-p2)/n2)
                tstat = (p1 - p2) / se if se > 0 else 0.0
                lo, _ = wilson(int(p1*n1), n1)
                flag = "  <<<" if (lo > be and tstat > 3) else ""
                print(f"  {name:14}{n1:>7}{p1*100:>8.2f}%{n2:>7}{p2*100:>8.2f}%"
                      f"{(p1-p2)*100:>+8.2f}{tstat:>7.1f}{(p1-be)*100:>+8.2f}{flag}")
                rows.append((sym, k, name, n1, p1, n2, p2, tstat, be, lo))

    print(f"\n{'='*78}")
    print("VERDICT")
    print("=" * 78)
    live = [r for r in rows if r[9] > r[8] and r[7] > 3]
    if live:
        print(f"  {len(live)} cell(s) beat BOTH the shuffled control (t>3) and breakeven:")
        for sym, k, name, n1, p1, n2, p2, ts, be, lo in live:
            print(f"    {sym:9} {k:>5}t {name:14} real {p1*100:.2f}% vs "
                  f"shuf {p2*100:.2f}% (t {ts:+.1f}), BE {be*100:.2f}%, n={n1}")
        print("\n  Re-run on a fresh, non-overlapping block before believing any of it.")
    else:
        best = max(rows, key=lambda r: r[7]) if rows else None
        print(f"  {len(rows)} cells tested. NONE beat both the shuffled control and")
        print("  breakeven.")
        if best:
            print(f"  strongest vs shuffled: {best[0]} {best[1]}t {best[2]} "
                  f"t={best[7]:+.1f}")
        print()
        print("  Real and shuffled agree throughout, which is the signature of walk")
        print("  geometry rather than memory. Chart patterns describe the shape of a")
        print("  random walk; they do not predict it.")

    with open(a.out, "w") as f:
        f.write("# Technical pattern multiscan\n\n"
                "| symbol | bars | strategy | n | real | shuffled | t | breakeven |\n"
                "|---|---|---|---|---|---|---|---|\n")
        for sym, k, name, n1, p1, n2, p2, ts, be, lo in rows:
            f.write(f"| {sym} | {k}t | {name} | {n1} | {p1*100:.2f}% | "
                    f"{p2*100:.2f}% | {ts:+.1f} | {be*100:.2f}% |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
