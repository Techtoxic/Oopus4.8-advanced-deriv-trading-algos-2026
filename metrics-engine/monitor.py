"""monitor.py — independent market-state logger for the Sentinel V2 metrics engine.

This does NOT trade and does NOT touch sentinel_v2.py. It opens its own public
websocket to JD100, recomputes the EXACT same rolling-sigma estimator the bot uses
(W=1800, jump>20 excluded, bin=0.1) and logs one row per tick:

    epoch, quote, digit, sigma, sigma_bin

Recording the realised next digit for every tick lets the analyzer compute the
true realised payoff of every possible contract each second (a clean, large-N edge
test on fresh live data, independent of which contract the bot actually picked).

Usage:
    python3 monitor.py --symbol JD100 --minutes 480 --out live/sigma_ticks.csv
"""
import argparse, csv, json, math, time, collections, os, sys
import websocket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fable-thoughts", "tools"))
from deriv_api import DerivWS, WS_PUBLIC  # noqa: E402

W = 1800        # rolling sigma window (steps) — identical to sentinel_v2
JUMP_THR = 20   # |step| > 20 pips excluded from sigma (jump)
BINW = 0.1      # sigma bin width


class Sigma:
    def __init__(self, pip):
        self.pip = pip
        self.vals = collections.deque(maxlen=W + 2)
        self.sq = collections.deque(maxlen=W)
        self.cn = collections.deque(maxlen=W)
        self.sum_sq = 0.0
        self.sum_cn = 0

    def push(self, quote):
        v = round(float(quote) * (10 ** self.pip))
        if self.vals:
            st = v - self.vals[-1]
            nj = 1 if abs(st) <= JUMP_THR else 0
            sq = float(st * st) if nj else 0.0
            if len(self.sq) == W:
                self.sum_sq -= self.sq[0]
                self.sum_cn -= self.cn[0]
            self.sq.append(sq)
            self.cn.append(nj)
            self.sum_sq += sq
            self.sum_cn += nj
        self.vals.append(v)
        return v

    def sigma(self):
        if self.sum_cn < 200:
            return None
        return math.sqrt(self.sum_sq / self.sum_cn)

    def digit(self):
        return self.vals[-1] % 10 if self.vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--minutes", type=float, default=480)
    ap.add_argument("--out", default="live/sigma_ticks.csv")
    a = ap.parse_args()

    ctl = DerivWS(token="")
    h = ctl.ticks_history(a.symbol, count=W + 2)
    pip = int(h["pip_size"])
    s = Sigma(pip)
    for q in h["history"]["prices"]:
        s.push(q)
    print(f"{a.symbol}: pip={pip} warm sigma={s.sigma() and round(s.sigma(),3)}", flush=True)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    new = not os.path.exists(a.out) or os.path.getsize(a.out) == 0
    f = open(a.out, "a", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(["epoch", "quote", "digit", "sigma", "sigma_bin"])

    pub = websocket.create_connection(WS_PUBLIC, timeout=30)
    pub.send(json.dumps({"ticks": a.symbol, "subscribe": 1}))
    t_end = time.time() + a.minutes * 60
    n = 0
    while time.time() < t_end:
        try:
            msg = json.loads(pub.recv())
        except Exception:
            try:
                pub = websocket.create_connection(WS_PUBLIC, timeout=30)
                pub.send(json.dumps({"ticks": a.symbol, "subscribe": 1}))
                continue
            except Exception:
                time.sleep(2)
                continue
        if msg.get("msg_type") != "tick":
            continue
        tk = msg["tick"]
        s.push(tk["quote"])
        sig = s.sigma()
        w.writerow([tk["epoch"], tk["quote"], s.digit(),
                    f"{sig:.4f}" if sig is not None else "",
                    round(sig / BINW) if sig is not None else ""])
        n += 1
        if n % 30 == 0:
            f.flush()
        if n % 300 == 0:
            print(f"{time.strftime('%H:%M:%S')} ticks={n} sigma={sig and round(sig,3)} "
                  f"digit={s.digit()} quote={tk['quote']}", flush=True)
    f.flush(); f.close()
    print(f"done: {n} ticks logged", flush=True)


if __name__ == "__main__":
    main()
