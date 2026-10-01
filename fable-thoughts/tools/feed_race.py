"""feed_race.py — do Deriv's two gateways deliver the same tick at different times?

THE ONE HYPOTHESIS THE INFORMATION BOUND CANNOT TOUCH
info_bound.py showed I(past ; next) = 0.0023 bits against 0.0089 needed, and by the data
processing inequality that closes every model that is a function of the past — context
trees, bispectra, signatures, chaos reconstruction, recovering the generator.

But the bound assumes ONE feed. It says nothing about

    I(feed_A at t ; feed_B at t + delta)

If two gateways deliver the same tick at different times, the leading one shows you the
"future" of the lagging one. That is not prediction, it is infrastructure asymmetry, and no
information-theoretic argument about the price series rules it out.

WHY THIS VERSION RATHER THAN MT5
The obvious test is MT5 feed vs API feed, but MT5 needs the terminal and a separate stack.
This repo already talks to TWO different Deriv gateways:

    wss://ws.binaryws.com/websockets/v3      (the legacy endpoint, used by most tools here)
    wss://api.derivws.com/trading/v1/...     (the newer options gateway, in deriv_api.py)

Same underlying, different infrastructure, both reachable from Python. If there is delivery
asymmetry anywhere, this is the cheapest place to find it.

WHAT IS ACTUALLY BEING MEASURED
Deriv stamps each tick with a server epoch, so the DATA is identical by construction — that
is not the question. The question is when each gateway PUSHES it to you. So we record the
local arrival time (perf_counter, monotonic) of each tick on each connection and compare
arrival times for matched epochs.

  1. matched-epoch arrival delta: for each epoch seen on both feeds, t_A - t_B
  2. consistency: is one feed reliably first, or does it alternate?
  3. magnitude vs actionability: a lead is only tradeable if it exceeds the round trip to
     place an order. entry_tick_test measured a 137ms median round trip, so a 20ms lead is
     useless and a 400ms lead is not.
  4. tick-count asymmetry: does either feed skip or duplicate?

HONEST PRIOR
Both gateways almost certainly read from the same publisher, so the expected result is a
delta centred near zero with jitter from network paths rather than a systematic lead. But
it is cheap, it is the last untested structural hypothesis, and unlike everything else it
would be immediately actionable if real.

Read-only. Places no trades.

Run: python3 feed_race.py --symbol R_75 --seconds 180
"""
import argparse, json, ssl, threading, time
import numpy as np

try:
    import websocket
except ImportError:
    websocket = None

# CORRECTED 2026-08-28. The first version invented two gateway URLs; both failed
# (binaryws timed out, and a guessed /websockets/v3 path on derivws returned 404).
# deriv_api.py uses exactly ONE endpoint:
#
#     wss://api.derivws.com/trading/v1/options/ws/public
#
# So there is no second gateway to race, and the cross-gateway version of the
# hypothesis has no premise. What remains testable for free is the PER-CONNECTION
# variant: does the server push to two independent sockets in lockstep, or does each
# connection get serviced separately? If separately, one socket can lead the other,
# and the same asymmetry argument applies.
WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"
GATEWAYS = {
    "socket_A": WS_PUBLIC,
    "socket_B": WS_PUBLIC,
}


class FeedReader(threading.Thread):
    """Subscribes to one gateway and records (server_epoch, quote, local_arrival)."""

    def __init__(self, name, url, symbol, stop_evt):
        super().__init__(daemon=True)
        self.name, self.url, self.symbol = name, url, symbol
        self.stop_evt = stop_evt
        self.rows = []
        self.error = None
        self.connected_at = None

    def run(self):
        try:
            ws = websocket.create_connection(
                self.url, timeout=20, sslopt={"cert_reqs": ssl.CERT_NONE})
            self.connected_at = time.perf_counter()
            ws.send(json.dumps({"ticks": self.symbol, "subscribe": 1}))
            while not self.stop_evt.is_set():
                raw = ws.recv()
                now = time.perf_counter()
                m = json.loads(raw)
                tk = m.get("tick")
                if tk:
                    self.rows.append((int(tk["epoch"]), float(tk["quote"]), now))
            ws.close()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="R_75")
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--rtt-ms", type=float, default=137,
                    help="measured order round trip; a lead below this is not actionable")
    ap.add_argument("--out", default="../results/feed_race.md")
    a = ap.parse_args()

    if websocket is None:
        print("pip install websocket-client"); return

    stop = threading.Event()
    readers = [FeedReader(n, u, a.symbol, stop) for n, u in GATEWAYS.items()]
    print(f"racing {len(readers)} independent sockets to the SAME gateway")
    print(f"  {WS_PUBLIC}")
    print(f"  symbol {a.symbol}, {a.seconds:.0f}s\n")
    for r in readers:
        r.start()
        time.sleep(0.05)
    t0 = time.time()
    try:
        while time.time() - t0 < a.seconds:
            time.sleep(2)
            counts = " ".join(f"{r.name}:{len(r.rows)}" for r in readers)
            print(f"\r  {int(time.time()-t0):>4}s  {counts}   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    stop.set()
    time.sleep(1.5)
    print()

    for r in readers:
        if r.error:
            print(f"  {r.name}: ERROR {r.error}")
    live = [r for r in readers if len(r.rows) > 20]
    if len(live) < 2:
        print("\n  fewer than two feeds delivered data — cannot compare.")
        for r in readers:
            print(f"    {r.name}: {len(r.rows)} ticks, error={r.error}")
        return

    print(f"\n{'='*72}")
    print("FEED SUMMARY")
    print("=" * 72)
    maps = {}
    for r in live:
        d = {}
        for ep, q, ts in r.rows:
            d.setdefault(ep, (q, ts))
        maps[r.name] = d
        eps = sorted(d)
        gaps = np.diff(eps) if len(eps) > 1 else np.array([0])
        print(f"  {r.name:10} {len(r.rows):>6} msgs, {len(d):>6} unique epochs, "
              f"median gap {np.median(gaps):.0f}s, "
              f"dupes {len(r.rows)-len(d)}")

    A, B = live[0].name, live[1].name
    common = sorted(set(maps[A]) & set(maps[B]))
    print(f"\n  epochs on both feeds: {len(common)}")
    if len(common) < 20:
        print("  too few matched epochs to compare."); return

    # price agreement — should be exact
    mism = sum(1 for e in common if maps[A][e][0] != maps[B][e][0])
    print(f"  price mismatches on matched epochs: {mism} "
          f"({'feeds agree on values' if mism == 0 else 'FEEDS DISAGREE — investigate'})")

    d_ms = np.array([(maps[A][e][1] - maps[B][e][1]) * 1000 for e in common])

    print(f"\n{'='*72}")
    print(f"ARRIVAL DELTA  ({A} minus {B}, milliseconds)")
    print("=" * 72)
    print(f"  n {len(d_ms)}   mean {d_ms.mean():+.1f}   median {np.median(d_ms):+.1f}   "
          f"sd {d_ms.std():.1f}")
    for q in (1, 5, 25, 50, 75, 95, 99):
        print(f"    p{q:<3} {np.percentile(d_ms, q):+8.1f} ms")
    frac_a = float((d_ms < 0).mean())
    print(f"\n  {A} arrives first on {frac_a*100:.1f}% of ticks "
          f"({B} on {(1-frac_a)*100:.1f}%)")

    se = d_ms.std() / np.sqrt(len(d_ms))
    t_stat = d_ms.mean() / se if se > 0 else 0.0
    print(f"  SE of the mean {se:.2f} ms -> t = {t_stat:+.1f}")

    print(f"\n{'='*72}")
    print("VERDICT")
    print("=" * 72)
    lead = abs(d_ms.mean())
    consistent = max(frac_a, 1 - frac_a)
    if abs(t_stat) < 3:
        print(f"  No systematic lead. Mean delta {d_ms.mean():+.1f} ms is within noise")
        print(f"  (t = {t_stat:+.1f}). The server pushes to both sockets in lockstep.")
        print()
        print("  Per-connection asymmetry closed. Only the MT5-vs-API variant remains,")
        print("  and that needs the MetaTrader terminal — a much larger lift for a")
        print("  hypothesis whose cheap version just came back null.")
    elif lead < a.rtt_ms:
        print(f"  A systematic lead EXISTS: {lead:.1f} ms (t = {t_stat:+.1f}), "
              f"{consistent*100:.0f}% consistent.")
        print(f"  But it is smaller than the {a.rtt_ms:.0f} ms order round trip, so by the")
        print("  time you could act on it the tick has already arrived everywhere.")
        print("  Real, and not actionable.")
    else:
        faster = A if d_ms.mean() < 0 else B
        print(f"  *** {faster} LEADS by {lead:.1f} ms (t = {t_stat:+.1f}), "
              f"{consistent*100:.0f}% consistent ***")
        print(f"  That exceeds the {a.rtt_ms:.0f} ms round trip.")
        print()
        print("  BEFORE BELIEVING IT:")
        print("   1. re-run with the gateway order swapped — a fixed lead that follows")
        print("      the connection order is a client artifact, not a server property")
        print("   2. re-run at a different time of day and from a different network")
        print("   3. confirm the lead survives when both sockets are in separate processes")
        print("   4. only then ask whether a 1-tick contract can be placed inside it")

    with open(a.out, "w") as f:
        f.write(f"# Gateway race — {a.symbol}\n\n"
                f"{len(common)} matched epochs, price mismatches {mism}.\n\n"
                f"arrival delta ({A} - {B}): mean {d_ms.mean():+.1f} ms, "
                f"median {np.median(d_ms):+.1f}, sd {d_ms.std():.1f}, t {t_stat:+.1f}\n"
                f"{A} first on {frac_a*100:.1f}% of ticks\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
