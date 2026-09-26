"""tick_integrity.py — did history_paged actually return distinct ticks?

WHY
entry_confirm.py reported a forward-test rank correlation of +0.976 with top-2 overlap 2/2.
Two things in that output are not physically possible if the two blocks are independent:

  1. SIGMA DID NOT MOVE. 3.879 -> 3.877 across a supposed 9.3-day gap. At the measured
     -0.67%/day drag sigma should have fallen ~6%, and regime_revalidate measured a
     3.60-4.16 range across 1.2M ticks. Two halves of a monotonically decaying series
     cannot have identical mean sigma.

  2. PER-DIGIT WIN RATES REPRODUCE TOO TIGHTLY. digit 0: 0.52926 -> 0.52931.
     digit 6: 0.52780 -> 0.52734. At n~80k the SE on each p is 0.00176, so the SE of the
     old-vs-new difference is ~0.0025 and the expected mean |difference| is ~0.002.
     Observed mean is 0.00038 — five times smaller than sampling noise allows, across all
     ten digits simultaneously.

Independent samples cannot reproduce each other that precisely. The most likely cause is
that history_paged is capped and repeating a window rather than paging further back.

This checks the raw data directly. If it fails, entry_confirm's +0.976 is measuring one
block against itself and the forward test has to be redone against genuinely older data.

CHECKS
  1. epoch span vs tick count — 1.6M one-second ticks should span ~18.5 days
  2. duplicate epochs, and duplicate (epoch, price) pairs
  3. exact repeated subsequences between the two halves
  4. monotonicity of epochs, and gap distribution
  5. per-half sigma and spot, which must differ if the halves are genuinely separated

Read-only. Places no trades.

Run: python3 tick_integrity.py --ticks 1600000
"""
import argparse, math, datetime as dt
import numpy as np
from deriv_api import DerivWS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=1600000)
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    times, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    ws.close()
    t = np.asarray(times, dtype=np.int64)
    p = np.asarray(prices, dtype=float)
    pip = int(pip)
    print(f"returned {len(t)} ticks\n")

    # ---- 1. span ---------------------------------------------------------
    print("=" * 70)
    print("1. EPOCH SPAN")
    print("=" * 70)
    span = int(t.max() - t.min())
    exp_days = a.ticks / 86400.0
    print(f"  first {dt.datetime.utcfromtimestamp(int(t.min()))} UTC")
    print(f"  last  {dt.datetime.utcfromtimestamp(int(t.max()))} UTC")
    print(f"  span {span}s = {span/86400:.2f} days")
    print(f"  expected for {a.ticks} 1s ticks: ~{exp_days:.2f} days")
    ok_span = span > 0.7 * a.ticks
    print(f"  -> {'OK' if ok_span else 'FAIL: span far shorter than tick count implies'}")

    # ---- 2. duplicates ---------------------------------------------------
    print(f"\n{'=' * 70}")
    print("2. DUPLICATES")
    print("=" * 70)
    uniq_t = len(np.unique(t))
    print(f"  unique epochs      {uniq_t} / {len(t)}  ({uniq_t/len(t)*100:.2f}%)")
    pairs = np.unique(np.stack([t, np.round(p * 10 ** pip).astype(np.int64)]), axis=1)
    print(f"  unique (t,price)   {pairs.shape[1]} / {len(t)}  "
          f"({pairs.shape[1]/len(t)*100:.2f}%)")
    ok_dup = uniq_t > 0.99 * len(t)
    print(f"  -> {'OK' if ok_dup else 'FAIL: repeated epochs — data is being reused'}")

    # ---- 3. repeated subsequences between halves -------------------------
    print(f"\n{'=' * 70}")
    print("3. DO THE TWO HALVES SHARE DATA?")
    print("=" * 70)
    h = len(t) // 2
    s1, s2 = set(t[:h].tolist()), set(t[h:].tolist())
    inter = len(s1 & s2)
    print(f"  epochs in both halves: {inter}")
    v = np.round(p * 10 ** pip).astype(np.int64)
    print(f"  identical price sequences (first 1000 of each): "
          f"{'YES' if np.array_equal(v[:1000], v[h:h+1000]) else 'no'}")
    ok_split = inter == 0
    print(f"  -> {'OK: halves are disjoint' if ok_split else 'FAIL: halves overlap'}")

    # ---- 4. gaps ---------------------------------------------------------
    print(f"\n{'=' * 70}")
    print("4. ORDERING AND GAPS")
    print("=" * 70)
    d = np.diff(t)
    print(f"  monotonic increasing: {'yes' if (d > 0).all() else 'NO'}")
    print(f"  gap median {np.median(d):.0f}s, min {d.min()}s, max {d.max()}s")
    big = int((d > 60).sum())
    print(f"  gaps > 60s: {big}")
    if big:
        idx = np.argsort(d)[-5:][::-1]
        for i in idx:
            if d[i] > 60:
                print(f"    {d[i]:>8}s gap at "
                      f"{dt.datetime.utcfromtimestamp(int(t[i]))} UTC")

    # ---- 5. per-half stats -----------------------------------------------
    print(f"\n{'=' * 70}")
    print("5. PER-HALF STATISTICS (must differ if genuinely separated)")
    print("=" * 70)

    def stats(vv, tt):
        st = np.diff(vv)
        f = st[np.abs(st) <= 20].astype(float)
        return (math.sqrt((f ** 2).mean()), vv[0] / 10 ** pip, vv[-1] / 10 ** pip,
                dt.datetime.utcfromtimestamp(int(tt[0])),
                dt.datetime.utcfromtimestamp(int(tt[-1])))

    for lab, sl in (("older half", slice(0, h)), ("newer half", slice(h, None))):
        sg, s0, s1_, d0, d1 = stats(v[sl], t[sl])
        print(f"  {lab}: sigma {sg:.4f}  spot {s0:.2f} -> {s1_:.2f}")
        print(f"    {d0} -> {d1} UTC")
    sg_o = stats(v[:h], t[:h])[0]
    sg_n = stats(v[h:], t[h:])[0]
    drift_expected = 0.0067 * (span / 2 / 86400)
    obs = abs(sg_o - sg_n) / sg_o
    print(f"\n  sigma change {obs*100:.3f}%, expected ~{drift_expected*100:.2f}% "
          f"over half the span at -0.67%/day")
    ok_sig = obs > drift_expected * 0.3

    # ---- verdict ---------------------------------------------------------
    print(f"\n{'=' * 70}")
    print("VERDICT")
    print("=" * 70)
    checks = [("epoch span", ok_span), ("no duplicate epochs", ok_dup),
              ("halves disjoint", ok_split), ("sigma differs between halves", ok_sig)]
    for name, ok in checks:
        print(f"  {name:32} {'PASS' if ok else 'FAIL'}")
    if all(ok for _, ok in checks):
        print("\n  Data is clean. entry_confirm's forward test stands, though note the")
        print("  two halves are at nearly the same sigma, so it is a test across TIME")
        print("  and not across sigma regimes.")
    else:
        print("\n  DATA PROBLEM. entry_confirm's +0.976 may be measuring one block")
        print("  against itself. Redo the forward test against genuinely older data —")
        print("  either fetch with an explicit `end` epoch, or store today's block and")
        print("  re-run in a week against fresh ticks.")


if __name__ == "__main__":
    main()
