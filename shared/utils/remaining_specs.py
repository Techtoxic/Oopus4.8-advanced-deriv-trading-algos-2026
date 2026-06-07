"""
Collect real EV/specs for the remaining phases in one pass:
- Accumulators (ACCU): growth rate, tick-size barrier
- Multipliers (MULTUP): commission / stop-out structure
- Higher/Lower (CALL/PUT): payout & house edge for tick durations 1..5
- Touch/No-Touch (ONETOUCH/NOTOUCH): payout vs barrier distance
- Vanillas (VANILLALONGCALL): premium
Also paginate ~30k ticks for BOOM500/CRASH500 to get more spikes.
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.deriv_client import DerivClient
import numpy as np
from scipy import stats

def paginate_ticks(c, sym, total=30000, chunk=5000):
    out = []
    end = "latest"
    while len(out) < total:
        res = c.request({"ticks_history": sym, "count": chunk, "end": end, "style": "ticks"})
        h = res.get("history")
        if not h or not h["times"]:
            break
        batch = list(zip(h["times"], h["prices"]))
        if out and batch[-1][0] >= out[0][0]:
            # trim overlap
            batch = [b for b in batch if b[0] < out[0][0]]
        if not batch:
            break
        out = batch + out
        end = out[0][0] - 1
    return out

def spike_stats(sym, ticks):
    p = np.array([x[1] for x in ticks], float); r = np.diff(p); sd = np.std(r)
    idx = np.where(r > 5*sd)[0] if sym.startswith("BOOM") else np.where(r < -5*sd)[0]
    iv = np.diff(idx)
    d = {"symbol":sym,"n_ticks":len(p),"n_spikes":int(len(idx))}
    if len(iv) > 5:
        d.update(mean_interval=float(np.mean(iv)), median_interval=float(np.median(iv)),
                 std_interval=float(np.std(iv)), min_interval=int(np.min(iv)),
                 max_interval=int(np.max(iv)), CV=float(np.std(iv)/np.mean(iv)),
                 ks_geom_p=float(stats.kstest(iv-1,'expon',args=(0,np.mean(iv)-1)).pvalue))
        ivc = iv - iv.mean()
        d["interval_autocorr_lag1"] = float(np.sum(ivc[:-1]*ivc[1:])/np.sum(ivc*ivc))
    return d

def main():
    c = DerivClient(); c.connect()
    report = {}

    # ---- Accumulators ----
    accu = {}
    for sym in ["R_100","1HZ100V","R_25"]:
        res = c.proposal(amount=10, basis="stake", contract_type="ACCU",
                         currency="USD", symbol=sym, growth_rate=0.01)
        if "error" in res: accu[sym]={"error":res["error"]["message"]}; continue
        pr = res["proposal"]
        accu[sym] = {k: pr.get(k) for k in
                     ("ask_price","payout","spot","display_number_of_contracts","id")}
        accu[sym]["growth_rate"]=0.01
        accu[sym]["barrier_info"]=pr.get("contract_details") or pr.get("limit_order")
        accu[sym]["full"] = {k:v for k,v in pr.items() if k not in ("longcode",)}
    report["accumulators"]=accu

    # ---- Multipliers ----
    mult = {}
    for sym in ["R_100","BOOM500"]:
        res = c.proposal(amount=10, basis="stake", contract_type="MULTUP",
                         currency="USD", symbol=sym, multiplier=100)
        if "error" in res: mult[sym]={"error":res["error"]["message"]}; continue
        pr = res["proposal"]
        mult[sym]={k:pr.get(k) for k in ("ask_price","commission","spot","payout","limit_order")}
        mult[sym]["full"]={k:v for k,v in pr.items() if k!="longcode"}
    report["multipliers"]=mult

    # ---- Higher/Lower (CALL/PUT) edge over tick durations ----
    hl = {}
    for sym in ["R_100","1HZ100V"]:
        hl[sym]={}
        for dur in [1,2,3,5]:
            row={}
            for ct in ["CALL","PUT"]:
                res = c.proposal(amount=10, basis="stake", contract_type=ct, currency="USD",
                                 symbol=sym, duration=dur, duration_unit="t", barrier="+0.0")
                if "error" in res: row[ct]=res["error"]["message"]; continue
                payout=res["proposal"]["payout"]
                # at-the-money tick CALL/PUT ~ p_win 0.5
                row[ct]=dict(payout=payout, edge_pct=round((1-0.5*payout/10)*100,3))
            hl[sym][f"{dur}t"]=row
    report["higher_lower"]=hl

    # ---- Touch / No-Touch ----
    tnt={}
    for sym in ["R_100"]:
        tnt[sym]={}
        for off in ["+1.0","+2.0","+5.0","+10.0"]:
            row={}
            for ct in ["ONETOUCH","NOTOUCH"]:
                res=c.proposal(amount=10,basis="stake",contract_type=ct,currency="USD",
                               symbol=sym,duration=5,duration_unit="t",barrier=off)
                row[ct]=res["proposal"]["payout"] if "error" not in res else res["error"]["message"]
            tnt[sym][off]=row
    report["touch_no_touch"]=tnt

    # ---- Vanillas ----
    van={}
    for sym in ["R_100"]:
        res=c.proposal(amount=10,basis="stake",contract_type="VANILLALONGCALL",currency="USD",
                       symbol=sym,duration=5,duration_unit="t",barrier="+0.0")
        van[sym]=res.get("proposal",{}).get("longcode") or res.get("error",{}).get("message")
        van[sym+"_full"]={k:v for k,v in res.get("proposal",{}).items() if k!="longcode"} if "proposal" in res else None
    report["vanillas"]=van

    json.dump(report, open("/home/research/reports/remaining_specs.json","w"), indent=2)
    print(json.dumps(report, indent=2)[:4000])

    # ---- Paginated Boom/Crash spikes ----
    print("\n=== PAGINATED SPIKE STATS (more history) ===")
    bc={}
    for sym in ["BOOM500","CRASH500","BOOM1000","CRASH1000"]:
        t = paginate_ticks(c, sym, total=30000)
        s = spike_stats(sym, t)
        bc[sym]=s
        print(s)
    json.dump(bc, open("/home/research/boom-crash/spike_analysis_30k.json","w"), indent=2)
    c.close()

if __name__=="__main__":
    main()
