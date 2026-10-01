"""derivfetch.py — the ONE correct way to pull tick history. Import this everywhere.

THE BUG THIS REPLACES
deriv_api.history_paged() silently loops. Asked for 1.6M ticks of JD100 it returned 86,401
unique epochs (5.44%) — the same day repeated ~18 times, with min inter-tick gap -86401s.

Cause: ticks_history takes an optional `start` parameter which, for style=ticks, DEFAULTS
TO ONE DAY AGO. Paging with only `end` set meant that once `end` moved earlier than that
implicit default, the requested window was empty and the server returned the latest data
again. Sending an explicit `start` fixes it: 400,000 unique ticks over 4.63 days at 100%
density, verified.

WHAT THE BUG COST
  chi2 scales linearly with replication, so randomness_battery's 661.5 was really 36.0 and
  battery_v2's 675.6 was really 98.0, against a null mean of 81. The "z = +47 entry/offset
  dependence" was replication.

  Wilson intervals were too narrow by sqrt(k): 2.1x on 400k runs, 3.0x on 800k, 3.7x on
  regime_revalidate's 1.2M.

  IS/OOS splits put COPIES OF THE SAME TICKS on both sides, which is why entry_select got
  rho +0.879 and entry_confirm +0.976. On clean data the per-digit spread scaled as 1/sqrt(n)
  (2.42pp -> 0.94pp vs 1.12pp predicted) — the signature of noise. That lead is dead.

Only cross_symbol.py (60k, under the cap) was unaffected.

USAGE
    from derivfetch import fetch_ticks
    t, p, pip = fetch_ticks(ws, "JD100", 1_200_000)     # raises if integrity fails

Always returns strictly increasing, deduplicated epochs. Verifies before returning.
"""
import time, math, datetime as dt
import numpy as np

PAGE = 1000          # server caps at 1000 per request regardless of `count`
SLEEP = 0.12


class IntegrityError(RuntimeError):
    pass


def fetch_ticks(ws, symbol, target, page=PAGE, verbose=True, strict=True):
    """
    Page backward with an EXPLICIT start window. Returns (epochs, prices, pip),
    ascending and deduplicated. Raises IntegrityError if the result is degenerate.
    """
    seen = {}
    end = "latest"
    pip = None
    stall = 0
    calls = 0
    max_calls = int(target / page * 1.6) + 40
    t0 = time.time()

    while len(seen) < target and calls < max_calls:
        req = {"ticks_history": symbol, "count": page, "end": end, "style": "ticks"}
        if isinstance(end, int):
            # explicit window — THE FIX. without this, start defaults to 1 day ago
            # and any `end` older than that yields an empty window.
            req["start"] = int(end - page * 3 - 120)
        r = ws.call(req)
        calls += 1
        h = r.get("history")
        if not h:
            if verbose:
                print(f"  stop at page {calls}: "
                      f"{r.get('error', {}).get('message', 'no history')}")
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
                if verbose:
                    print(f"  stop: no new data for 3 pages at "
                          f"{dt.datetime.fromtimestamp(oldest, dt.UTC):%Y-%m-%d %H:%M} UTC")
                break
        else:
            stall = 0

        if verbose and calls % 100 == 0:
            rate = len(seen) / max(time.time() - t0, 1e-9)
            print(f"  {len(seen):>8}/{target} unique  back to "
                  f"{dt.datetime.fromtimestamp(oldest, dt.UTC):%Y-%m-%d %H:%M} UTC"
                  f"  ({rate:.0f}/s)")
        end = oldest - 1
        time.sleep(SLEEP)

    ks = np.array(sorted(seen), dtype=np.int64)
    vs = np.array([seen[int(k)] for k in ks], dtype=float)
    pip = pip if pip is not None else 2

    # ---- integrity checks: never return silently-degenerate data ----------
    if len(ks) == 0:
        raise IntegrityError("no ticks returned")
    span = int(ks[-1] - ks[0])
    density = len(ks) / max(span, 1)
    dup_free = len(ks) == len(set(ks.tolist()))
    monotonic = bool((np.diff(ks) > 0).all())
    if verbose:
        print(f"  -> {len(ks)} unique ticks, "
              f"{dt.datetime.fromtimestamp(int(ks[0]), dt.UTC):%Y-%m-%d %H:%M} -> "
              f"{dt.datetime.fromtimestamp(int(ks[-1]), dt.UTC):%Y-%m-%d %H:%M} UTC, "
              f"{span/86400:.2f} days, density {density*100:.1f}%")
    if not (dup_free and monotonic):
        raise IntegrityError("returned epochs are not unique/monotonic")
    if strict and len(ks) < target * 0.5:
        print(f"  WARNING: only {len(ks)} of {target} requested "
              f"({len(ks)/target*100:.0f}%) — server history limit reached")
    return ks, vs, pip


def native_interval(t):
    """
    Median inter-tick gap. NOT every symbol is 1/sec: the R_* series tick every 2s.
    Assuming 1s there yields zero contiguous pairs (density 50% is the giveaway).
    """
    d = np.diff(t)
    d = d[d > 0]
    return int(np.median(d)) if len(d) else 1


def contiguous_pairs(t, v, interval=None):
    """Mask of positions i where t[i+1] is exactly one native tick after t[i]."""
    iv = interval if interval else native_interval(t)
    return np.diff(t) == iv


def jump_threshold(v, mask=None, pct=99.5):
    """
    Adaptive jump cutoff. A fixed 20-pip filter is calibrated for JD100 at sigma ~3.9;
    on JD25/JD50 (sigma 11-13) it rejects almost every step, the rolling window never
    reaches its minimum count, and sigma comes back all-NaN. Use a high percentile of
    |step| instead so the cutoff scales with the symbol.
    """
    st = np.diff(v)
    if mask is not None:
        st = st[mask]
    a = np.abs(st.astype(float))
    a = a[a > 0]
    if len(a) < 100:
        return 20.0
    return float(max(np.percentile(a, pct), 3.0))


def sigma_series(v, mask=None, w=1800, jump=None):
    """Causal rolling RMS of jump-filtered steps. sig[i] uses steps strictly before i."""
    if jump is None:
        jump = jump_threshold(v, mask)
    st = np.diff(v)
    if mask is not None:
        st = np.where(mask, st, 0)
    f = np.where(np.abs(st) <= jump, st.astype(float) ** 2, 0.0)
    c = np.where(np.abs(st) <= jump, 1.0, 0.0)
    if mask is not None:
        c = np.where(mask, c, 0.0)
    cs = np.concatenate([[0.0], np.cumsum(f)])
    cc = np.concatenate([[0.0], np.cumsum(c)])
    out = np.full(len(st), np.nan)
    for i in range(w + 1, len(st)):
        n = cc[i] - cc[i - w]
        if n >= 200:
            out[i] = math.sqrt((cs[i] - cs[i - w]) / n)
    return out


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def noise_range_pct(se_pp, k=10, pct=95):
    """
    Expected RANGE of k iid normals, at the given percentile — the correct threshold
    for 'is this spread real'. Comparing against the MEAN range (3.08*se) is wrong:
    half of pure-noise samples exceed their own mean. That error made clean_fetch print
    SURVIVES twice on data that was noise.
    """
    mean_r = 3.08 * se_pp
    sd_r = 0.90 * se_pp
    z = {90: 1.282, 95: 1.645, 99: 2.326}.get(pct, 1.645)
    return mean_r + z * sd_r
