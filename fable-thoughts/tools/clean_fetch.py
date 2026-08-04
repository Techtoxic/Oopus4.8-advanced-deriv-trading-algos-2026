"""clean_fetch.py — tick fetcher that actually pages backward, plus a clean re-run
of the entry-digit test.

THE ACTUAL BUG (corrected 2026-08-04, second pass)
An earlier version of this file claimed Deriv serves only 1 day of tick history. That was
WRONG and was my own error. Per the API docs, ticks_history takes a `start` parameter and
for style=ticks it DEFAULTS TO ONE DAY AGO. Paging with only `end` set meant that once
`end` moved earlier than that implicit `start`, the requested window was empty and the
server returned the latest data again -- producing the ~18x looping tick_integrity.py
measured. The fix is to send an explicit `start` with every request.

THE BUG
tick_integrity.py showed history_paged returns only 87,022 UNIQUE epochs out of 1,600,000
requested — 5.44%. It loops roughly one day of data ~18 times (min inter-tick gap -86401s
confirms it wraps rather than paging back).

CONSEQUENCES, all of which invalidate results from this session:

  chi2 scales linearly with duplication: chi2_obs = k * chi2_true. With k = 18.39,
  randomness_battery's test C (661.5) corrects to 36.0 and battery_v2's (675.6) to 98.0,
  against a null mean of 81. The z = +47 "entry/offset dependence" was replication.

  The block-permutation null could not catch this. chi2 under independence has mean = df
  regardless of n, so the null correctly sat at 81.5 while the observed was inflated by k.
  It validates pairing, not sample size.

  Wilson intervals are too narrow by sqrt(k): 2.14x for 400k-tick runs, 3.03x for
  entry_select's 800k, 4.29x for entry_confirm's 1.6M. Only cross_symbol (60k, under the
  cap) is unaffected — its independence result stands.

  entry_select's rho = +0.879 and entry_confirm's +0.976 are inflated because interleaved
  and split halves both contain COPIES OF THE SAME TICKS.

THE FIX
Deriv's ticks_history accepts an explicit `end` epoch. Page backward by setting
end = (earliest epoch seen) - 1 on each request, and stop when a page returns nothing new.
This script does that, deduplicates by epoch, and reports true coverage.

Then it re-runs the entry-digit test on genuinely unique ticks so we find out whether
anything survives.

Read-only. Places no trades.

Run: python3 clean_fetch.py --symbol JD100 --target 400000
"""
import argparse, math, time, datetime as dt
import numpy as np
from deriv_api import DerivWS

EXEC_M = 1.8286
BE = 1 / EXEC_M
WIN5 = [0, 1, 2, 8, 9]


def fetch_backward(ws, sym, target, page=5000, max_calls=400):
    """Page backward with explicit `end`. Returns (epochs, prices, pip) deduped, ascending."""
    seen = {}
    end = "latest"
    pip = None
    calls = 0
    stall = 0
    while len(seen) < target and calls < max_calls:
        # CRITICAL: `start` defaults to 1 DAY AGO for style=ticks (per the API docs).
        # Leaving it unset was the bug: once `end` moved earlier than that default the
        # window was empty and the server returned the latest data again, which is the
        # looping tick_integrity.py measured. Always send an explicit `start`.
        start = (end - page * 2 - 60) if isinstance(end, int) else None
        req = {"ticks_history": sym, "count": page, "end": end, "style": "ticks"}
        if start is not None:
            req["start"] = int(start)
        r = ws.call(req)
        calls += 1
        h = r.get("history")
        if not h:
            print(f"  page {calls}: no history — {r.get('error', {}).get('message')}")
            break
        if pip is None:
            pip = int(r.get("pip_size", 2))
        ts, ps = h.get("times", []), h.get("prices", [])
        if not ts:
            break
        before = len(seen)
        for t_, p_ in zip(ts, ps):
            seen[int(t_)] = float(p_)
        gained = len(seen) - before
        oldest = min(int(x) for x in ts)
        if gained == 0:
            stall += 1
            if stall >= 3:
                print(f"  page {calls}: no new data 3x — server will not go further back")
                break
        else:
            stall = 0
        if calls % 20 == 0 or gained == 0:
            print(f"  page {calls}: +{gained} new, total {len(seen)}, "
                  f"oldest {dt.datetime.fromtimestamp(oldest, dt.UTC):%Y-%m-%d %H:%M}")
        end = oldest - 1
        if calls % 20 == 0:
            import datetime as _dt
            print(f"    (window start={start}, "
                  f"{_dt.datetime.fromtimestamp(oldest, _dt.UTC):%Y-%m-%d %H:%M} UTC)")
        time.sleep(0.15)
    ks = sorted(seen)
    return np.array(ks, dtype=np.int64), np.array([seen[k] for k in ks]), pip or 2


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--target", type=int, default=400000)
    ap.add_argument("--out", default="../results/clean_fetch.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"paging backward for {a.target} unique ticks of {a.symbol}...")
    t, p, pip = fetch_backward(ws, a.symbol, a.target)
    ws.close()

    print(f"\n{'='*72}")
    print("COVERAGE")
    print("=" * 72)
    span = int(t.max() - t.min())
    print(f"  unique ticks {len(t)}")
    print(f"  {dt.datetime.fromtimestamp(int(t.min()), dt.UTC):%Y-%m-%d %H:%M} -> "
          f"{dt.datetime.fromtimestamp(int(t.max()), dt.UTC):%Y-%m-%d %H:%M} UTC")
    print(f"  span {span/86400:.2f} days, density {len(t)/max(span,1)*100:.1f}% of 1/sec")
    if len(t) < a.target * 0.5:
        print(f"  NOTE: server capped us at {len(t)}. That is the real history limit.")

    v = np.round(p * (10 ** pip)).astype(np.int64)
    # only use contiguous 1s runs so the offset is a genuine next-tick offset
    step_ok = np.diff(t) == 1
    dig = (v % 10).astype(int)
    ent = dig[:-1][step_ok]
    off = ((dig[1:] - dig[:-1]) % 10)[step_ok]
    win = np.isin(off, WIN5)
    n = len(ent)
    st = np.diff(v)[step_ok]
    sg = float(np.sqrt((st[np.abs(st) <= 20].astype(float) ** 2).mean()))
    print(f"  contiguous 1s pairs {n} (dropped {int((~step_ok).sum())} across gaps)")
    print(f"  sigma_pips {sg:.3f}")

    # ---- entry-digit test on CLEAN data ---------------------------------
    print(f"\n{'='*72}")
    print(f"ENTRY-DIGIT WIN RATE, UNIQUE TICKS ONLY  (payout {EXEC_M}, BE {BE*100:.2f}%)")
    print("=" * 72)
    half = n // 2
    A = slice(0, half)
    B = slice(half, n)
    print(f"{'entry':>6}{'n(A)':>8}{'p(A)':>9}{'n(B)':>8}{'p(B)':>9}"
          f"{'all n':>8}{'all p':>9}{'vs BE':>10}{'99% low':>10}")
    pa, pb, rows = [], [], []
    for c in range(10):
        m = ent == c
        ma, mb = m[A], m[B]
        na, nb = int(ma.sum()), int(mb.sum())
        if na < 300 or nb < 300:
            continue
        va, vb = float(win[A][ma].mean()), float(win[B][mb].mean())
        k, nn = int(win[m].sum()), int(m.sum())
        pp = k / nn
        lo, _ = wilson(k, nn)
        pa.append(va); pb.append(vb)
        rows.append((c, pp, nn, lo))
        print(f"{c:>6}{na:>8}{va:>9.5f}{nb:>8}{vb:>9.5f}"
              f"{nn:>8}{pp:>9.5f}{(pp-BE)*100:>+9.3f}pp{(lo*EXEC_M-1)*100:>9.2f}%")

    rho = spearman(np.array(pa), np.array(pb))
    print(f"\n  half-A / half-B rank correlation = {rho:+.3f}")
    print(f"  (entry_select reported +0.879 and entry_confirm +0.976 on duplicated data)")
    spread = (max(r[1] for r in rows) - min(r[1] for r in rows)) * 100
    se = math.sqrt(0.535 * 0.465 / (n / 10)) * 100
    exp_range = 3.08 * se
    print(f"  spread {spread:.2f}pp   noise-only expected range {exp_range:.2f}pp "
          f"(SE per digit {se:.2f}pp)")

    print(f"\n{'='*72}")
    print("VERDICT")
    print("=" * 72)
    real = rho > 0.5 and spread > exp_range
    if real:
        top = sorted(rows, key=lambda r: -r[1])[:2]
        print(f"  SURVIVES. ranking holds (rho {rho:+.3f}) and spread {spread:.2f}pp "
              f"exceeds the {exp_range:.2f}pp noise band.")
        print(f"  best digits: {[r[0] for r in top]}")
        for c, pp, nn, lo in top:
            print(f"    digit {c}: p={pp:.5f} n={nn} EV {(pp*EXEC_M-1)*100:+.2f}% "
                  f"99% low {(lo*EXEC_M-1)*100:+.2f}%")
    else:
        print(f"  DOES NOT SURVIVE. rho {rho:+.3f}, spread {spread:.2f}pp vs "
              f"{exp_range:.2f}pp noise band.")
        print("  The entry-digit finding was an artifact of duplicated ticks.")
        print("  Genuine per-digit structure, if any, is below what this sample resolves.")

    open(a.out, "w").write(
        f"# Clean fetch — {a.symbol}\n\n{len(t)} unique ticks, "
        f"{span/86400:.2f} days, sigma {sg:.3f}.\n"
        f"half-A/half-B rho {rho:+.3f}, spread {spread:.2f}pp vs noise band "
        f"{exp_range:.2f}pp.\n\n| entry | n | p | vs BE |\n|---|---|---|---|\n"
        + "".join(f"| {c} | {nn} | {pp:.5f} | {(pp-BE)*100:+.3f}pp |\n"
                 for c, pp, nn, _ in rows))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
