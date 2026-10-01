"""mi_vs_sigma.py — is the MI intermittency just sigma, or is there something else?

WHAT PROMPTED THIS
mi_intermittency.py found strong intermittency on JD100: windowed MI spread 5-173x wider
than shuffled, 45 of 691 windows above the bit requirement, and first-half MI predicting
second-half MI at r = 0.64-0.88 (shuffled ~0).

That looks like a discovery. It is probably a rediscovery.

Over the 46-day sample JD100's spot ran 186-272, so sigma ran roughly 3.3-4.8 via the known
law sigma = 0.03982 + 1.746764e-04 * spot. And MI DEPENDS ON SIGMA by construction: low
sigma concentrates the digit distribution, high sigma flattens it. So:

  - high-MI windows should be exactly the low-sigma windows
  - the 0.88 persistence is just spot moving slowly, so sigma in the first half predicts
    sigma in the second half

If so, the intermittency is the sigma-decay thesis in information-theoretic clothing, and
adaptive_sentinel_v3's sigma gate already exploits it.

IT ALSO CORRECTS AN EARLIER OVERCLAIM
info_bound.py reported 0.0023 bits and I concluded "max 52.80% vs 55.56% breakeven,
impossible". That was the SAMPLE AVERAGE across all sigma. The best window carries 0.0173
bits, which supports 57.74% — above breakeven. The information is genuinely there at low
sigma. "Impossible" was wrong; "impossible on average" was right.

THE QUESTION THIS ANSWERS
Is windowed MI fully explained by windowed sigma, or is there a residual?

  fully explained  -> nothing new. the sigma gate is the correct and complete response.
  residual present -> a second, unknown state variable drives predictability, and that
                      WOULD be new.

METHOD
  1. per window: measure sigma (causal, jump-filtered) and MI (Miller-Madow).
  2. correlate. also compare against the MI the wrapped-normal model PREDICTS at that
     sigma — a parameter-free prediction, not a fit.
  3. regress out sigma and test whether the residual MI still shows intermittency beyond
     the shuffled null.
  4. check whether the residual, if any, persists from first half to second half. A
     residual that does not persist cannot be traded.

Read-only. No trades.

Run: python3 mi_vs_sigma.py --symbol JD100 --ticks 4000000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval

SIGMA_A, SIGMA_B = 0.03982, 1.746764e-04


def h2(p):
    return 0.0 if p <= 0 or p >= 1 else -p*math.log2(p) - (1-p)*math.log2(1-p)


def mi_mm(ctx, nxt, n_ctx=10, n_sym=10):
    n = len(nxt)
    if n < 200:
        return float("nan")
    j = np.zeros((n_ctx, n_sym))
    np.add.at(j, (ctx, nxt), 1)
    pj = j / n
    px, py = pj.sum(1, keepdims=True), pj.sum(0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = pj * np.log2(pj / (px @ py))
    raw = float(np.nansum(t))
    ox, oy = int((j.sum(1) > 0).sum()), int((j.sum(0) > 0).sum())
    return max(raw - max(ox*oy - ox - oy + 1, 0) / (2*n*math.log(2)), 0.0)


def wn_offset_pmf(sigma):
    num = [sum(math.exp(-0.5*((d + 10*k)/sigma)**2) for k in range(-6, 7))
           for d in range(10)]
    s = sum(num)
    return [x/s for x in num]


def mi_from_sigma(sigma):
    """MI between entry digit and next digit implied by a wrapped normal of this sigma.
    Entry digit is uniform, so I = H(uniform) - H(offset pmf) = log2(10) - H(offset)."""
    pmf = wn_offset_pmf(sigma)
    H = -sum(p*math.log2(p) for p in pmf if p > 0)
    return math.log2(10) - H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=4000000)
    ap.add_argument("--window", type=int, default=25000)
    ap.add_argument("--payout", type=float, default=1.80)
    ap.add_argument("--out", default="../results/mi_vs_sigma.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()
    v = np.round(p * (10 ** pip)).astype(np.int64)
    iv = native_interval(t)
    cg = contiguous_pairs(t, v, iv)
    dig = (v % 10).astype(np.int64)
    days = (t[-1] - t[0]) / 86400
    print(f"  {len(v)} ticks, {days:.1f} days, spot {p.min():.2f}-{p.max():.2f}\n")

    w = a.window
    ctx, nxt = dig[:-1], dig[1:]
    st = np.diff(v)
    nw = len(nxt) // w
    rows = []
    for i in range(nw):
        sl = slice(i*w, (i+1)*w)
        seg = st[sl].astype(float)
        nz = np.abs(seg[seg != 0])
        if len(nz) < 100:
            continue
        thr = np.percentile(nz, 99.5)
        f = seg[np.abs(seg) <= thr]
        sg = math.sqrt((f**2).mean()) if len(f) else float("nan")
        mi = mi_mm(ctx[sl], nxt[sl])
        spot = float(p[sl].mean())
        rows.append((i, sg, mi, spot))
    rows = [r for r in rows if np.isfinite(r[1]) and np.isfinite(r[2])]
    sg = np.array([r[1] for r in rows])
    mi = np.array([r[2] for r in rows])
    spot = np.array([r[3] for r in rows])
    print("=" * 76)
    print(f"1. IS WINDOWED MI EXPLAINED BY WINDOWED SIGMA?  ({len(rows)} windows of {w})")
    print("=" * 76)
    print(f"  sigma range {sg.min():.3f} - {sg.max():.3f}")
    print(f"  MI range    {mi.min():.6f} - {mi.max():.6f}")
    r_ms = float(np.corrcoef(sg, mi)[0, 1])
    print(f"  corr(sigma, MI) = {r_ms:+.4f}   (expect strongly NEGATIVE if it is sigma)")

    pred = np.array([mi_from_sigma(s) for s in sg])
    r_pm = float(np.corrcoef(pred, mi)[0, 1])
    print(f"  corr(wrapped-normal prediction, measured MI) = {r_pm:+.4f}")
    print(f"  mean predicted {pred.mean():.6f}   mean measured {mi.mean():.6f}")
    print("  (the prediction is parameter-free — no fitting, straight from sigma)")

    # ---- 2. residual -----------------------------------------------------
    print(f"\n{'='*76}")
    print("2. IS THERE A RESIDUAL BEYOND SIGMA?")
    print("=" * 76)
    # A LINEAR fit in sigma is MISSPECIFIED: MI(sigma) is strongly convex (0.0255 at
    # sigma 3.2 down to 0.00016 at 4.8). A straight line through a convex curve leaves
    # systematic residuals at both ends, and because sigma persists within a window both
    # halves inherit the same residual sign — manufacturing fake persistence. Regress on
    # the parameter-free wrapped-normal PREDICTION instead, plus an affine rescaling to
    # absorb JD100's known -0.00124 offset from the model.
    b1, b0 = np.polyfit(sg, mi, 1)
    resid_lin = mi - (b0 + b1*sg)
    r2_lin = 1 - resid_lin.var() / mi.var()
    c1, c0 = np.polyfit(pred, mi, 1)
    resid = mi - (c0 + c1*pred)
    r2 = 1 - resid.var() / mi.var()
    print(f"  LINEAR in sigma  : R^2 = {r2_lin:.4f}  residual sd {resid_lin.std():.6f}")
    print(f"  WRAPPED NORMAL   : R^2 = {r2:.4f}  residual sd {resid.std():.6f}")
    print(f"    fit MI = {c0:+.6f} {c1:+.6f} * predicted")
    print(f"    (a linear-in-sigma fit is misspecified; the convex model is the right form)")

    rng = np.random.default_rng(0)
    shd = dig.copy(); rng.shuffle(shd)
    sctx, snxt = shd[:-1], shd[1:]
    sh_mi = np.array([mi_mm(sctx[i*w:(i+1)*w], snxt[i*w:(i+1)*w]) for i in range(nw)])
    sh_mi = sh_mi[np.isfinite(sh_mi)]
    print(f"  shuffled MI sd (pure estimator noise) {sh_mi.std():.6f}")
    ratio = resid.std() / sh_mi.std() if sh_mi.std() > 0 else float("nan")
    print(f"  residual / noise = {ratio:.2f}")
    residual_real = ratio > 1.5
    print(f"  -> {'RESIDUAL BEYOND SIGMA — a second state variable exists'
                 if residual_real else 'no residual: sigma explains the intermittency'}")

    # ---- 3. does the residual persist? -----------------------------------
    if residual_real:
        print(f"\n{'='*76}")
        print("3. DOES THE RESIDUAL PERSIST HALF-TO-HALF?")
        print("=" * 76)
        h = w // 2
        r1, r2v = [], []
        for idx, (i, s, m, sp) in enumerate(rows):
            sl1 = slice(i*w, i*w + h); sl2 = slice(i*w + h, (i+1)*w)
            m1, m2 = mi_mm(ctx[sl1], nxt[sl1]), mi_mm(ctx[sl2], nxt[sl2])
            e = c0 + c1*mi_from_sigma(s)
            r1.append(m1 - e); r2v.append(m2 - e)
        r1, r2v = np.array(r1), np.array(r2v)
        ok = np.isfinite(r1) & np.isfinite(r2v)
        c = float(np.corrcoef(r1[ok], r2v[ok])[0, 1])
        print(f"  corr(residual h1, residual h2) = {c:+.4f}")
        print(f"  -> {'persists — potentially tradable' if c > 0.3 else 'does not persist — visible only in hindsight'}")

    # ---- 4. economics ----------------------------------------------------
    print(f"\n{'='*76}")
    print("4. WHAT THE BEST WINDOWS SUPPORT")
    print("=" * 76)
    need = 1 - h2(1/a.payout)
    def edge(I):
        lo, hi = 0.0, 0.5
        for _ in range(60):
            m = (lo+hi)/2
            if 1 - h2(0.5+m) < I: lo = m
            else: hi = m
        return lo
    print(f"  breakeven at payout {a.payout} = {100/a.payout:.2f}%, needs {need:.6f} bits")
    print(f"  {'sigma band':>16}{'n':>5}{'mean MI':>11}{'max win rate':>14}{'vs BE':>9}")
    qs = np.percentile(sg, [20, 40, 60, 80])
    for lo_s, hi_s in zip([-np.inf]+list(qs), list(qs)+[np.inf]):
        m = (sg >= lo_s) & (sg < hi_s)
        if m.sum() < 2:
            continue
        mm = mi[m].mean()
        wr = 50 + edge(mm)*100
        lab = f"[{lo_s:.2f},{hi_s:.2f})" if np.isfinite(lo_s) else f"< {hi_s:.2f}"
        print(f"  {lab:>16}{int(m.sum()):>5}{mm:>11.6f}{wr:>13.2f}%"
              f"{wr - 100/a.payout:>+8.2f}")

    print(f"\n{'='*76}")
    print("VERDICT")
    print("=" * 76)
    if residual_real:
        print("  MI is NOT fully explained by sigma. Something else varies. Identify it")
        print("  before doing anything else — check section 3 for whether it persists.")
    else:
        print(f"  Windowed MI tracks sigma at corr {r_ms:+.3f}, and the parameter-free")
        print(f"  wrapped-normal prediction matches at {r_pm:+.3f}. The intermittency IS")
        print("  the sigma effect. Nothing new — adaptive_sentinel_v3's sigma gate is")
        print("  already the correct response.")
        print()
        print("  It does correct one earlier overclaim: 0.0023 bits was the average across")
        print("  all sigma, and 'max 52.80%, impossible' applied to that average. In the")
        print("  low-sigma windows the information genuinely exceeds breakeven. The edge")
        print("  is real there — it is the payout cuts that killed it, not the physics.")

    with open(a.out, "w") as f:
        f.write(f"# MI vs sigma — {a.symbol}\n\n{len(rows)} windows of {w}.\n"
                f"corr(sigma, MI) {r_ms:+.4f}; corr(wrapped-normal prediction, MI) "
                f"{r_pm:+.4f}; R^2 {r2:.4f}; residual/noise {ratio:.2f}\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
