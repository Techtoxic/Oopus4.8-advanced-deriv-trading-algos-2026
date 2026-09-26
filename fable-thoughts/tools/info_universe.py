"""info_universe.py — the information bound across EVERY symbol family.

THE GAP THIS FILLS
info_bound.py measured I(past ; next) = 0.0023 bits and closed the predictive search — but
it ran on JD100 ONLY. Every other test in this repo swept all 17 symbols; the information
bound was measured on one.

THE SHARP VERSION OF THE TEST
Raw MI per symbol is not very informative on its own: the wrapped-normal model already
predicts MI from sigma alone, parameter-free, and predicts near-zero for every high-sigma
symbol. So the useful quantity is the EXCESS:

    excess = measured MI  -  MI predicted by that symbol's own sigma

Excess is structure the physics does not explain. On JD100 this ratio came out at 0.9048
(measured runs slightly BELOW the model). If any symbol shows measured well ABOVE its
prediction, that is a genuine anomaly and the first one this project would have found.

Three controls, because every apparent finding in this repo has died to one:
  - shuffled null per symbol (destroys time order, preserves the digit marginal)
  - Miller-Madow bias correction (raw plug-in MI at k=4 reads 8x the corrected value)
  - the wrapped-normal prediction is computed from sigma, never fitted to the MI

FAMILIES COVERED
  Jump indices      JD10 JD25 JD50 JD75 JD100      (jump-diffusion, decaying spot)
  Volatility 1s     1HZ10V 1HZ25V 1HZ50V 1HZ75V 1HZ100V
  Volatility 2s     R_10 R_25 R_50 R_75 R_100
  Daily reset       RDBULL RDBEAR                  (reset to 1000.0000 each midnight)
  Step indices      stpRNG stpRNG2..5              (FIXED step size — the interesting case)
  Boom/Crash        BOOM1000 CRASH1000 etc         (deliberately asymmetric spike structure)

The step and boom/crash families matter most here. Step indices move by a FIXED amount every
tick, so their digit process is a lattice walk with exact parity structure rather than a
diffusion — the wrapped normal does not apply and the MI could legitimately differ. Boom and
Crash have an engineered asymmetry (many small moves, rare large spike), which is real
structure by construction; the question is whether it is PREDICTABLE structure.

Read-only. No trades. Rate-limited: fetches sequentially with pauses.

Run: python3 info_universe.py
     python3 info_universe.py --ticks 300000 --symbols stpRNG BOOM1000 RDBULL
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval

FAMILIES = {
    "jump":     ["JD10", "JD25", "JD50", "JD75", "JD100"],
    "vol_1s":   ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"],
    "vol_2s":   ["R_10", "R_25", "R_50", "R_75", "R_100"],
    "reset":    ["RDBULL", "RDBEAR"],
    "step":     ["stpRNG", "stpRNG2", "stpRNG3", "stpRNG4", "stpRNG5"],
    "boom":     ["BOOM300N", "BOOM500", "BOOM1000", "CRASH300N", "CRASH500", "CRASH1000"],
}


def h2(p):
    return 0.0 if p <= 0 or p >= 1 else -p*math.log2(p) - (1-p)*math.log2(1-p)


def edge_for_bits(I):
    lo, hi = 0.0, 0.5
    for _ in range(60):
        m = (lo + hi) / 2
        if 1 - h2(0.5 + m) < I:
            lo = m
        else:
            hi = m
    return lo


def mi_mm(ctx, nxt, n_ctx=10, n_sym=10):
    n = len(nxt)
    if n < 500:
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


def wn_mi(sigma):
    """MI implied by a wrapped normal of this sigma. Entry digit uniform, so
    I = log2(10) - H(offset pmf)."""
    if not np.isfinite(sigma) or sigma <= 0:
        return float("nan")
    num = [sum(math.exp(-0.5*((d + 10*k)/sigma)**2) for k in range(-6, 7))
           for d in range(10)]
    s = sum(num)
    pmf = [x/s for x in num]
    H = -sum(p*math.log2(p) for p in pmf if p > 0)
    return math.log2(10) - H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--ticks", type=int, default=300000)
    ap.add_argument("--payout", type=float, default=1.80)
    ap.add_argument("--pause", type=float, default=3.0)
    ap.add_argument("--out", default="../results/info_universe.md")
    a = ap.parse_args()

    if a.symbols:
        todo = [("custom", s) for s in a.symbols]
    else:
        todo = [(f, s) for f, syms in FAMILIES.items() for s in syms]

    need = 1 - h2(1.0 / a.payout)
    print(f"breakeven at payout {a.payout} = {100/a.payout:.2f}%, needs {need:.6f} bits\n")
    print(f"{'family':9}{'symbol':10}{'n':>8}{'iv':>4}{'sigma':>11}"
          f"{'measured':>11}{'predicted':>11}{'ratio':>8}{'shuf':>10}{'max WR':>9}")
    print("-" * 92)

    rows = []
    for fam, sym in todo:
        ws = DerivWS(token="")
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False, strict=False)
        except Exception as e:
            print(f"{fam:9}{sym:10}  fetch failed: {str(e)[:44]}")
            ws.close(); time.sleep(a.pause); continue
        ws.close()
        if len(t) < 20000:
            print(f"{fam:9}{sym:10}{len(t):>8}  too few ticks")
            time.sleep(a.pause); continue

        v = np.round(p * (10 ** pip)).astype(np.int64)
        iv = native_interval(t)
        cg = contiguous_pairs(t, v, iv)
        dig = (v % 10).astype(np.int64)
        st = np.diff(v)[cg].astype(float)
        nz = np.abs(st[st != 0])
        if len(nz) < 500:
            print(f"{fam:9}{sym:10}{len(t):>8}  degenerate steps")
            time.sleep(a.pause); continue
        thr = np.percentile(nz, 99.5)
        f = st[np.abs(st) <= thr]
        sg = math.sqrt((f**2).mean()) if len(f) else float("nan")

        ctx, nxt = dig[:-1], dig[1:]
        mi = mi_mm(ctx, nxt)
        rng = np.random.default_rng(0)
        sh = dig.copy(); rng.shuffle(sh)
        mi_s = mi_mm(sh[:-1], sh[1:])
        pred = wn_mi(sg)
        ratio = (mi / pred) if (np.isfinite(pred) and pred > 1e-9) else float("nan")
        wr = 50 + edge_for_bits(max(mi - mi_s, 0.0)) * 100

        rows.append(dict(fam=fam, sym=sym, n=len(t), iv=iv, sg=sg, mi=mi,
                         pred=pred, ratio=ratio, sh=mi_s, wr=wr))
        flag = ""
        if np.isfinite(ratio) and ratio > 1.5 and mi > mi_s * 3:
            flag = "  <<< EXCESS"
        print(f"{fam:9}{sym:10}{len(t):>8}{iv:>4}{sg:>11.2f}"
              f"{mi:>11.6f}{pred:>11.6f}{ratio:>8.2f}{mi_s:>10.6f}{wr:>8.2f}%{flag}")
        time.sleep(a.pause)

    if not rows:
        print("\nno symbols measured"); return

    print(f"\n{'='*92}")
    print("EXCESS OVER THE WRAPPED-NORMAL PREDICTION")
    print("=" * 92)
    print("  measured/predicted near 1 means the physics fully explains the information.")
    print("  JD100 previously came in at 0.90 (measured slightly BELOW the model).")
    print()
    ok = [r for r in rows if np.isfinite(r["ratio"])]
    for fam in dict.fromkeys(r["fam"] for r in ok):
        g = [r for r in ok if r["fam"] == fam]
        rs = [r["ratio"] for r in g]
        print(f"  {fam:9} n={len(g)}  ratio {min(rs):.2f}-{max(rs):.2f}  "
              f"median {sorted(rs)[len(rs)//2]:.2f}")

    print(f"\n{'='*92}")
    print("VERDICT")
    print("=" * 92)
    live = [r for r in rows if r["wr"] > 100/a.payout]
    excess = [r for r in ok if r["ratio"] > 1.5 and r["mi"] > r["sh"] * 3]
    if live:
        print(f"  {len(live)} symbol(s) where the information alone clears breakeven:")
        for r in live:
            print(f"    {r['sym']:10} sigma {r['sg']:.2f}, MI {r['mi']:.6f}, "
                  f"max WR {r['wr']:.2f}% vs {100/a.payout:.2f}%")
        print("  Check the EXECUTED payout for those symbols — this used a flat "
              f"{a.payout}.")
    if excess:
        print(f"\n  {len(excess)} symbol(s) with MI ABOVE the wrapped-normal prediction:")
        for r in excess:
            print(f"    {r['sym']:10} measured {r['mi']:.6f} vs predicted "
                  f"{r['pred']:.6f} (ratio {r['ratio']:.2f})")
        print("  This is structure the model does not explain. For step indices the model")
        print("  legitimately does not apply (fixed step size, lattice parity), so check")
        print("  that first. For any other family it is a genuine anomaly — re-run on a")
        print("  fresh block before believing it.")
    if not live and not excess:
        print("  No symbol clears breakeven on information alone, and none exceeds its")
        print("  own wrapped-normal prediction. The bound measured on JD100 generalises:")
        print("  every family carries only the information its sigma implies, and only")
        print("  JD100's sigma is low enough for that to be a meaningful amount.")

    with open(a.out, "w") as f:
        f.write(f"# Information bound across families\n\nbreakeven needs {need:.6f} bits.\n\n"
                "| family | symbol | n | iv | sigma | measured | predicted | ratio | shuffled | max WR |\n"
                "|---|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['fam']} | {r['sym']} | {r['n']} | {r['iv']}s | {r['sg']:.2f} "
                    f"| {r['mi']:.6f} | {r['pred']:.6f} | {r['ratio']:.2f} "
                    f"| {r['sh']:.6f} | {r['wr']:.2f}% |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
