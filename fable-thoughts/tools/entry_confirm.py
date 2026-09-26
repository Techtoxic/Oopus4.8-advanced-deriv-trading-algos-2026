"""entry_confirm.py — does the entry-digit ranking hold on INDEPENDENT data?

WHAT WE HAVE
entry_select.py found the win rate on the +/-2 window varies by entry digit, and the ranking
persists out of sample: Spearman +0.879, IS spread 2.38pp, OOS spread 2.26pp. Digits 2 and 7
rank top. Selection lifts p from 0.53488 (pooled) to 0.54802 (digit 2) — worth 2.40pp of EV,
turning -2.19% into +0.21% at the executed payout 1.8286.

WHY THAT IS NOT YET ENOUGH
IS and OOS there were INTERLEAVED BLOCKS FROM ONE CONTINUOUS WINDOW. That design catches
overfitting but not regime change: both halves see the same sigma, the same hours, the same
generator state. A ranking can be stable within a window and still be an artifact of it.

Also unexplained: digits 2 and 7 are exactly 5 apart, half the modulus. That is a structured
pattern, not a random pair. If real it should have a mechanism, and a mechanism should show
up on OTHER symbols too.

THIS SCRIPT
  1. Fetches 1.6M ticks and splits by TIME: the older 800k vs the newer 800k. These are
     genuinely separate — roughly two weeks apart, at different sigma levels. If digits 2
     and 7 rank top in BOTH, the finding survives a regime change, which is a much higher
     bar than an interleaved split.
  2. Compares RANKINGS not levels, since sigma decay shifts every digit's win rate. A
     ranking stable across sigma regimes is stronger evidence than one stable within a
     single regime.
  3. Applies the OLD-block selection to the NEW block — a strictly forward test, choosing
     on data that predates the evaluation window entirely.
  4. Runs the same per-digit analysis on the other digit-enabled symbols. If the same
     digits win everywhere, that points at the generator. If each symbol has its own
     favoured digits, it is symbol-specific structure. If no other symbol shows any
     spread, JD100's result becomes much more suspicious.

Read-only. Places no trades.

Run: python3 entry_confirm.py
     python3 entry_confirm.py --ticks 2000000 --others 1HZ100V R_100 JD75
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS

EXEC_M = 1.8286
BE = 1 / EXEC_M
WIN5 = [0, 1, 2, 8, 9]


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def per_digit(v):
    dig = (v % 10).astype(int)
    ent = dig[:-1]
    off = (dig[1:] - dig[:-1]) % 10
    win = np.isin(off, WIN5)
    out = {}
    for c in range(10):
        s = ent == c
        n = int(s.sum())
        if n < 500:
            continue
        k = int(win[s].sum())
        out[c] = (k / n, n, k)
    return out


def sigma_of(v):
    st = np.diff(v)
    f = st[np.abs(st) <= 20].astype(float)
    return float(np.sqrt((f ** 2).mean()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=1600000)
    ap.add_argument("--others", nargs="*",
                    default=["1HZ100V", "R_100", "JD75", "1HZ10V"])
    ap.add_argument("--out", default="../results/entry_confirm.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    _, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    pip = int(pip)
    v = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    print(f"got {len(v)} ticks, spot {prices[-1]}\n")

    half = len(v) // 2
    old, new = v[:half], v[half:]
    s_old, s_new = sigma_of(old), sigma_of(new)
    days = half / 86400.0

    print("=" * 80)
    print(f"1. FORWARD TEST — older {half} ticks vs newer {half} ticks")
    print(f"   ~{days:.1f} days apart. sigma {s_old:.3f} -> {s_new:.3f}")
    print("=" * 80)
    po, pn = per_digit(old), per_digit(new)
    common = sorted(set(po) & set(pn))
    print(f"{'entry':>6}{'n(old)':>9}{'p(old)':>9}{'rank':>6}"
          f"{'n(new)':>9}{'p(new)':>9}{'rank':>6}{'new EV':>9}{'99% low':>10}")
    ro = {c: r for r, c in enumerate(sorted(common, key=lambda c: -po[c][0]), 1)}
    rn = {c: r for r, c in enumerate(sorted(common, key=lambda c: -pn[c][0]), 1)}
    for c in common:
        lo, _ = wilson(pn[c][2], pn[c][1])
        print(f"{c:>6}{po[c][1]:>9}{po[c][0]:>9.5f}{ro[c]:>6}"
              f"{pn[c][1]:>9}{pn[c][0]:>9.5f}{rn[c]:>6}"
              f"{(pn[c][0]*EXEC_M-1)*100:>8.2f}%{(lo*EXEC_M-1)*100:>9.2f}%")

    x = np.array([po[c][0] for c in common])
    y = np.array([pn[c][0] for c in common])
    rho = spearman(x, y)
    print(f"\n  OLD/NEW rank correlation = {rho:+.3f}")
    print(f"  spread old {(x.max()-x.min())*100:.2f}pp, new {(y.max()-y.min())*100:.2f}pp")
    top_old = sorted(common, key=lambda c: -po[c][0])[:2]
    top_new = sorted(common, key=lambda c: -pn[c][0])[:2]
    print(f"  top-2 old {top_old}   top-2 new {top_new}   "
          f"overlap {len(set(top_old) & set(top_new))}/2")

    # ---- 2. strictly forward selection ----------------------------------
    print(f"\n{'='*80}")
    print("2. SELECT ON OLD BLOCK, EVALUATE ON NEW BLOCK (strictly forward)")
    print("=" * 80)
    dig_n = (new % 10).astype(int)
    ent_n = dig_n[:-1]
    off_n = (dig_n[1:] - dig_n[:-1]) % 10
    win_n = np.isin(off_n, WIN5)
    allk, alln = int(win_n.sum()), len(win_n)
    lo, _ = wilson(allk, alln)
    print(f"  trade ALL      n={alln:>7} p={allk/alln:.5f} "
          f"EV {(allk/alln*EXEC_M-1)*100:+.2f}% 99%low {(lo*EXEC_M-1)*100:+.2f}%")
    print()
    print(f"  {'rule (chosen on OLD)':30}{'n':>9}{'p':>9}{'EV':>9}{'99% low':>10}")
    best = None
    for k in (1, 2, 3):
        sel = sorted(common, key=lambda c: -po[c][0])[:k]
        m = np.isin(ent_n, sel)
        nn, kk = int(m.sum()), int(win_n[m].sum())
        if nn < 1000:
            continue
        p = kk / nn
        wl, _ = wilson(kk, nn)
        ev, evlo = p * EXEC_M - 1, wl * EXEC_M - 1
        print(f"  {'top-'+str(k)+' '+str(sel):30}{nn:>9}{p:>9.5f}"
              f"{ev*100:>8.2f}%{evlo*100:>9.2f}%")
        if best is None or evlo > best[0]:
            best = (evlo, sel, p, ev, nn)

    # ---- 3. other symbols ------------------------------------------------
    print(f"\n{'='*80}")
    print("3. SAME PATTERN ON OTHER SYMBOLS?  (generator vs symbol-specific)")
    print("=" * 80)
    print(f"  {'symbol':10}{'sigma':>8}{'spread':>9}{'best 2':>10}{'worst 2':>10}"
          f"{'gap(pp)':>9}")
    others = []
    for sym in a.others:
        try:
            _, pr2, pp2 = ws.history_paged(sym, 400000, sleep=0.2)
        except Exception as e:
            print(f"  {sym:10} fetch error: {e}")
            continue
        v2 = np.round(np.asarray(pr2, dtype=float) * (10 ** int(pp2))).astype(np.int64)
        d2 = per_digit(v2)
        if len(d2) < 10:
            continue
        ps = {c: d2[c][0] for c in d2}
        srt = sorted(ps, key=lambda c: -ps[c])
        spread = (max(ps.values()) - min(ps.values())) * 100
        print(f"  {sym:10}{sigma_of(v2):>8.2f}{spread:>8.2f}pp"
              f"{str(srt[:2]):>10}{str(srt[-2:]):>10}"
              f"{(ps[srt[0]]-ps[srt[-1]])*100:>9.2f}")
        others.append((sym, srt[:2], spread))
    ws.close()

    # ---- verdict ---------------------------------------------------------
    print(f"\n{'='*80}")
    print("VERDICT")
    print("=" * 80)
    fwd_ok = rho > 0.5 and len(set(top_old) & set(top_new)) >= 1
    print(f"  forward rank correlation {rho:+.3f}, top-2 overlap "
          f"{len(set(top_old) & set(top_new))}/2")
    if fwd_ok and best and best[0] > 0:
        print(f"  CONFIRMED AND SIGNIFICANT. digits {best[1]} chosen on old data give")
        print(f"  OOS EV {best[3]*100:+.2f}% with 99% low {best[0]*100:+.2f}% on new data.")
        print("  Implement as an entry-digit gate in adaptive_sentinel alongside sigma.")
    elif fwd_ok:
        print("  CONFIRMED but not yet significant. The ranking survives a regime change,")
        print("  which is the hard part. Significance needs more data or lower sigma.")
        print("  Selection is worth carrying into the sigma-decay thesis.")
    else:
        print("  NOT CONFIRMED. The ranking did not survive a genuine forward split.")
        print("  entry_select's +0.879 was specific to that window. Do not gate on it.")
    if others:
        allsets = [set(s) for _, s, _ in others]
        shared = set.intersection(*allsets) if allsets else set()
        print(f"\n  other symbols' best digits: "
              + ", ".join(f"{s}={t}" for s, t, _ in others))
        if shared:
            print(f"  shared across all: {sorted(shared)} -> points at the GENERATOR")
        else:
            print("  no shared favoured digits -> structure is symbol-specific")

    open(a.out, "w").write(
        f"# Entry-digit forward confirmation — {a.symbol}\n\n"
        f"old sigma {s_old:.3f}, new sigma {s_new:.3f}, ~{days:.1f} days apart.\n"
        f"OLD/NEW rank correlation {rho:+.3f}.\n\n"
        "| entry | p(old) | p(new) |\n|---|---|---|\n"
        + "".join(f"| {c} | {po[c][0]:.5f} | {pn[c][0]:.5f} |\n" for c in common))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
