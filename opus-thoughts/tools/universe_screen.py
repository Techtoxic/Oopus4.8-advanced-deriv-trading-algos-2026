"""H6 + H2 phase 1: sweep EVERY active symbol -> which offer digits/accumulators,
then measure the tick-step physics (sigma in pips, step mod 10 concentration) for all
digit-enabled symbols. The structural-predictability screen nobody runs.

Run: python3 universe_screen.py  (no token needed)
Out: ../results/universe_screen.json + universe_screen.md
"""
import json, math, time, collections
from deriv_api import DerivWS, last_digit

OUT_JSON = "../results/universe_screen.json"
OUT_MD = "../results/universe_screen.md"


def step_stats(prices, pip):
    """sigma of pip-steps, |Delta mod 10| distribution, TV from uniform, parity-flip prob."""
    digs = [last_digit(p, pip) for p in prices]
    scale = 10 ** int(pip)
    steps = []
    prev = None
    for p in prices:
        v = round(float(p) * scale)
        if prev is not None:
            steps.append(v - prev)
        prev = v
    n = len(steps)
    if n < 100:
        return None
    mean = sum(steps) / n
    sigma = math.sqrt(sum((s - mean) ** 2 for s in steps) / n)
    mod = collections.Counter(s % 10 for s in steps)
    moddist = [mod.get(k, 0) / n for k in range(10)]
    tv = 0.5 * sum(abs(x - 0.1) for x in moddist)
    flips = sum(1 for a, b in zip(digs, digs[1:]) if (a % 2) != (b % 2))
    pflip = flips / (len(digs) - 1)
    digc = collections.Counter(digs)
    digdist = [digc.get(k, 0) / len(digs) for k in range(10)]
    tvd = 0.5 * sum(abs(x - 0.1) for x in digdist)
    # conditional next-digit max deviation
    trans = collections.Counter()
    rowc = collections.Counter()
    for a, b in zip(digs, digs[1:]):
        trans[(a, b)] += 1; rowc[a] += 1
    maxdev = 0.0
    for a in range(10):
        if rowc[a] < 50: continue
        for b in range(10):
            maxdev = max(maxdev, abs(trans[(a, b)] / rowc[a] - 0.1))
    return dict(n=n, sigma_pips=sigma, mean_step=mean, step_mod10=moddist, tv_mod10=tv,
                parity_flip=pflip, digit_dist=digdist, tv_digits=tvd, max_cond_dev=maxdev)


def main():
    ws = DerivWS(token="")  # public
    syms = ws.active_symbols()
    print(f"{len(syms)} active symbols")
    rows = []
    for s in syms:
        sym = s["symbol"]
        cf = ws.contracts_for(sym)
        cats = set()
        ticksize_ok = False
        if "contracts_for" in cf:
            for c in cf["contracts_for"].get("available", []):
                cats.add(c.get("contract_category"))
                if c.get("contract_category") == "digits" and c.get("expiry_type") == "tick":
                    ticksize_ok = True
        rows.append(dict(symbol=sym, name=s.get("display_name"), market=s.get("market"),
                         submarket=s.get("submarket"), pip=s.get("pip"), spot=s.get("spot"),
                         categories=sorted(x for x in cats if x), digits=("digits" in cats),
                         accumulator=("accumulator" in cats)))
        time.sleep(0.18)
    dig_syms = [r for r in rows if r["digits"]]
    print(f"digit-enabled: {len(dig_syms)} -> {[r['symbol'] for r in dig_syms]}")
    accu_syms = [r["symbol"] for r in rows if r["accumulator"]]
    print(f"accumulator-enabled: {len(accu_syms)} -> {accu_syms}")

    physics = {}
    for r in dig_syms:
        sym = r["symbol"]
        h = ws.ticks_history(sym, count=5000)
        if "history" not in h:
            print(sym, "history error", h.get("error")); continue
        prices = h["history"]["prices"]
        pip_dec = abs(round(math.log10(float(r["pip"]))))
        st = step_stats(prices, pip_dec)
        if st:
            st["pip_decimals"] = pip_dec
            st["spot"] = prices[-1]
            physics[sym] = st
            print(f"{sym:10s} sigma={st['sigma_pips']:9.1f} pips  tv_mod10={st['tv_mod10']:.4f}  "
                  f"parity_flip={st['parity_flip']:.4f}  max_cond_dev={st['max_cond_dev']:.4f}")
        time.sleep(0.3)

    with open(OUT_JSON, "w") as f:
        json.dump(dict(symbols=rows, physics=physics, ts=time.time()), f, indent=1)

    md = ["# Universe screen — every active symbol\n",
          f"\n{len(syms)} symbols; digit-enabled: {len(dig_syms)}; accumulator-enabled: {len(accu_syms)}\n",
          "\n## Digit symbols — tick-step physics (5k ticks each)\n",
          "\n| symbol | spot | pip dec | sigma (pips) | TV(step mod 10) | parity flip | max cond dev | TV(digits) |",
          "\n|---|---:|---:|---:|---:|---:|---:|---:|"]
    for sym, st in sorted(physics.items(), key=lambda kv: kv[1]["sigma_pips"]):
        md.append(f"\n| {sym} | {st['spot']} | {st['pip_decimals']} | {st['sigma_pips']:.1f} | "
                  f"{st['tv_mod10']:.4f} | {st['parity_flip']:.4f} | {st['max_cond_dev']:.4f} | {st['tv_digits']:.4f} |")
    md.append("\n\nsigma >= ~8 pips => step mod 10 is indistinguishable from uniform (wrapped-normal "
              "flatness ~ 2·exp(-2π²(σ/10)²)); structural conditioning is dead. sigma <= ~5 pips => "
              "exploitable concentration would appear in TV(step mod 10) and max_cond_dev.\n")
    md.append("\n## Non-digit symbols offering other exploitables\n")
    for r in rows:
        if not r["digits"] and r["categories"]:
            md.append(f"\n- {r['symbol']} ({r['name']}, {r['submarket']}): {', '.join(r['categories'])}")
    with open(OUT_MD, "w") as f:
        f.write("".join(md))
    print("wrote", OUT_JSON, OUT_MD)
    ws.close()


if __name__ == "__main__":
    main()
