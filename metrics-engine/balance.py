"""balance.py — Objective 1: real Deriv balance + CSV reconciliation (no CSV-only PnL trust).

Pulls the ACTUAL demo balance over the authenticated WebSocket ({"balance":1}) and reconciles
it against the trade-log cumulative PnL. Flags any discrepancy > $1.

The lesson from the prior runs: CSV PnL and the real balance diverge when in-flight contracts
settle on Deriv during a process restart and never hit the CSV. Reconciliation is only exact if
a baseline balance is snapshotted at the first trade — so this tool can write/read that baseline.

    python3 balance.py                         # print live balance
    python3 balance.py --snapshot              # record baseline (balance + epoch) to out/baseline.json
    python3 balance.py --reconcile [--start S] [--csv P]   # reconcile CSV PnL vs balance move
"""
import sys, os, csv, json, time, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "fable-thoughts", "tools"))
from deriv_api import DerivWS  # noqa


def live_balance():
    ws = DerivWS()
    r = ws.call({"balance": 1})
    ws.close()
    if "error" in r:
        raise RuntimeError(r["error"])
    b = r["balance"]
    return {"balance": float(b["balance"]), "currency": b.get("currency"),
            "loginid": b.get("loginid"), "epoch": int(time.time())}


def csv_pnl(csv_path):
    rows = list(csv.DictReader(open(csv_path)))
    settled = [r for r in rows if r.get("status") in ("won", "lost")]
    s = sum(float(r["pnl"]) for r in settled)
    return s, len(settled), len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(HERE, "live", "sentinel_trades.csv"))
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--start", type=float, default=None,
                    help="baseline balance at first trade; else read out/baseline.json")
    a = ap.parse_args()
    bpath = os.path.join(HERE, "out", "baseline.json")

    bal = live_balance()
    print(f"LIVE balance: ${bal['balance']:.2f} {bal['currency']}  ({bal['loginid']})")

    if a.snapshot:
        os.makedirs(os.path.dirname(bpath), exist_ok=True)
        json.dump(bal, open(bpath, "w"), indent=2)
        print(f"baseline snapshot written -> {os.path.relpath(bpath, HERE)}")
        return

    if a.reconcile:
        pnl, nset, ntot = csv_pnl(a.csv)
        start = a.start
        if start is None and os.path.exists(bpath):
            start = json.load(open(bpath)).get("balance")
        print(f"CSV: {nset}/{ntot} settled, cumulative PnL = ${pnl:+.2f}")
        if start is None:
            print("No baseline balance available — cannot reconcile a historical run that never "
                  "snapshotted its start. (This is exactly the gap this tool closes going forward: "
                  "run --snapshot before trading, --reconcile after.)")
            return
        moved = bal["balance"] - start
        disc = moved - pnl
        print(f"balance moved: ${moved:+.2f} (from ${start:.2f})")
        print(f"DISCREPANCY (balance_move - csv_pnl) = ${disc:+.2f}")
        print("FLAG: |discrepancy| > $1 — CSV does NOT match real balance" if abs(disc) > 1.0
              else "OK: CSV reconciles with real balance within $1")


if __name__ == "__main__":
    main()
