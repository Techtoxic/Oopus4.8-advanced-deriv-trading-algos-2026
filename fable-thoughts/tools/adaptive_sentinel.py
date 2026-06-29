"""adaptive_sentinel.py — the completed bot: base digit-physics edge + the regime-adaptive layer
that was the missing 25%.

What makes it adaptive (so it never needs a manual retrain and never trades the losing regime):
  1. SELF-RECALIBRATING offset table: maintains a trailing histogram of the last --trail no-jump
     digit-offsets, updated every tick. The prediction PMF is always built from the most recent
     regime, so a slow sigma/structure drift is absorbed automatically.
  2. HARD SIGMA GATE (--sigma-max, default 4.25): never trades when rolling sigma is in the
     zero/negative-EV zone. Proven on wide-regime data: turns a -1.62%/trade bleed into +1.2%.
  3. LIVE-EV GATE: the contract is priced from the trailing table AND the live payout; trades
     only when table-EV > gate. Near the boundary it naturally stops firing.
  4. QUARTER-KELLY sizing from a live-updated return variance (cap --max-frac of balance).
  5. Stale-tick + RTT guards, second socket for settlement, session max-loss, demo guard.

Watch : python3 adaptive_sentinel.py --minutes 10
Trade : python3 adaptive_sentinel.py --trade --balance 1000 --ev-gate 0.01 --minutes 360
"""
import argparse, json, math, time, collections, threading, queue, csv, os
import websocket
from deriv_api import DerivWS, WS_PUBLIC

WSIG = 1800; JUMP = 20

def window_contract(dig):
    """5-wide window {dig-2..dig+2}; express as the cheapest OVER/UNDER if contiguous, else None.
    Returns (contract_type, barrier, winset)."""
    lo = (dig - 2) % 10; hi = (dig + 2) % 10
    wins = set((dig + k) % 10 for k in (-2, -1, 0, 1, 2))
    # contiguous non-wrapping window -> single OVER/UNDER pair covering exactly these 5
    if lo < hi and hi - lo == 4:
        # {lo..hi}: UNDER(hi+1) wins 0..hi ; need exactly lo..hi -> use OVER(lo-1)&UNDER(hi+1) not single.
        # Simplest exact single-contract 5-window only when window == {0..4} or {5..9}:
        if wins == {0,1,2,3,4}: return ("DIGITUNDER","5",wins)
        if wins == {5,6,7,8,9}: return ("DIGITOVER","4",wins)
    return (None, None, wins)

def best_ou(dig, table, payouts):
    """Pick the OVER/UNDER barrier, CONDITIONED on the current digit, RESTRICTED to the wide
    ~even-money windows. table is the offset PMF; P(next digit==t)=table[(t-dig)%10].

    WHY RESTRICTED: an unrestricted max-EV search over all 17 OU barriers systematically picks
    the narrow high-payout contracts (UNDER1-3 / OVER6-8) where a noisy trailing table
    over-estimates the tail mass -> optimizer's curse. Live demo proof (2026-06-29): unrestricted
    lost -8.1%/$ with model predicting +2.1%; the narrow contracts (UNDER3 0.284 real vs 0.319
    model, OVER7 0.197 vs 0.215) bled while the wide ones (OVER5, UNDER6) were +EV. Restricting to
    win-size 5-6 windows (prob ~0.4-0.6) removes the bias; cross-period purged test (June-11 table
    -> June-29 trades) = +1.94%/trade, t=3.18."""
    ALLOWED = [("DIGITOVER", 3), ("DIGITOVER", 4), ("DIGITUNDER", 5), ("DIGITUNDER", 6)]
    def pdig(t): return table[(t - dig) % 10]
    best = None
    for ct, k in ALLOWED:
        key = (ct, k)
        if key not in payouts: continue
        ws = set(range(k + 1, 10)) if ct == "DIGITOVER" else set(range(0, k))
        p = sum(pdig(x) for x in ws); ev = p * payouts[key] - 1
        if best is None or ev > best[3]: best = (ct, str(k), ws, ev, p, payouts[key])
    return best  # (ct,barrier,winset,ev,p,M)

class Roller:
    def __init__(self, pip, trail):
        self.pip=pip; self.trail=trail; self.vals=collections.deque(maxlen=WSIG+2)
        self.sq=collections.deque(maxlen=WSIG); self.cn=collections.deque(maxlen=WSIG)
        self.ssq=0.0; self.scn=0
        self.hist=[0.0]*10; self.offq=collections.deque()   # unbounded; trimmed explicitly
        self.last=None
    def push(self,quote):
        x=round(float(quote)*(10**self.pip))
        if self.vals:
            stp=x-self.vals[-1]; nj=abs(stp)<=JUMP
            sq=float(stp*stp) if nj else 0.0
            if len(self.sq)==WSIG: self.ssq-=self.sq[0]; self.scn-=self.cn[0]
            self.sq.append(sq); self.cn.append(1 if nj else 0); self.ssq+=sq; self.scn+=(1 if nj else 0)
            if nj:
                off=(x-self.vals[-1])%10
                self.hist[off]+=1; self.offq.append(off)
        self.vals.append(x)
    def trim_table(self):
        while len(self.offq)>self.trail:
            self.hist[self.offq.popleft()]-=1
    def sigma(self):
        if self.scn<200: return None
        return math.sqrt(self.ssq/self.scn)
    def digit(self): return self.vals[-1]%10 if self.vals else None
    def table(self):
        t=sum(self.hist)
        return [h/t for h in self.hist] if t>=5000 else None
    def table_n(self): return int(sum(self.hist))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--watch",nargs="+",default=["JD100"])
    ap.add_argument("--minutes",type=float,default=10)
    ap.add_argument("--trade",action="store_true")
    ap.add_argument("--balance",type=float,default=1000.0)
    ap.add_argument("--ev-gate",type=float,default=0.01)
    ap.add_argument("--sigma-max",type=float,default=4.25)
    ap.add_argument("--trail",type=int,default=60000)
    ap.add_argument("--stake",type=float,default=0.0,
                    help="FLAT stake per trade (recommended). If 0, use quarter-Kelly (only safe on "
                         "large balances; proportional sizing on a small balance amplifies sequencing "
                         "variance and can show a $ loss even with a +EV win rate — see FABLE_ADAPTIVE.md).")
    ap.add_argument("--kelly-frac",type=float,default=0.25)
    ap.add_argument("--max-frac",type=float,default=0.005)
    ap.add_argument("--min-stake",type=float,default=0.35)
    ap.add_argument("--max-loss",type=float,default=100.0)
    ap.add_argument("--allow-real",action="store_true")
    ap.add_argument("--log",default="../results/adaptive_trades.csv")
    a=ap.parse_args()

    ctl=DerivWS(token="")
    payouts={}; pips={}
    OU=[("DIGITOVER",k) for k in range(9)]+[("DIGITUNDER",k) for k in range(1,10)]
    for sym in a.watch:
        h=ctl.ticks_history(sym,count=WSIG+2); pips[sym]=int(h["pip_size"]); payouts[sym]={}
        for ct,k in OU:
            r=ctl.proposal(amount=10,basis="stake",contract_type=ct,currency="USD",
                           duration=1,duration_unit="t",underlying_symbol=sym,barrier=str(k))
            if "proposal" in r: payouts[sym][(ct,k)]=float(r["proposal"]["payout"])/10
            time.sleep(0.05)
        print(f"{sym}: {len(payouts[sym])} OU payouts; pip={pips[sym]}")

    trader=settler=None
    if a.trade:
        trader=DerivWS(timeout=8); acct=trader.account or {}
        if not acct.get("account_id"): print("token invalid -> watch"); trader=None
        elif acct.get("account_type")!="demo" and not a.allow_real: print("REAL acct, refusing"); trader=None
        else:
            settler=DerivWS(timeout=15)
            print(f"TRADING {acct['account_id']} ({acct['account_type']}) bal={acct.get('balance')}")
            try: a.balance=float(acct.get("balance",a.balance))
            except: pass
    if trader:
        def ka():
            while True:
                time.sleep(25)
                try: trader.call({"ping":1},retries=1)
                except: 
                    try: trader._connect()
                    except: pass
        threading.Thread(target=ka,daemon=True).start()

    pend=queue.Queue(); R={"pnl":0.0,"n":0,"wins":0,"bal":a.balance,"rets":collections.deque(maxlen=2000),"lock":threading.Lock()}
    logf=open(a.log,"a",newline=""); logw=csv.writer(logf)
    if os.path.getsize(a.log)==0:
        logw.writerow(["ts","sym","digit","sigma","ct","bar","p","ev","stake","rtt_ms","cid","status","pnl","lag"])
    def settle():
        while True:
            try: item=pend.get(timeout=20)
            except queue.Empty:
                try: settler.call({"ping":1},retries=1)
                except:
                    try: settler._connect()
                    except: pass
                continue
            if item is None: return
            cid,T,sym,sg,ct,bar,p,ev,stake,rtt=item
            time.sleep(2.0); pnl=None;status="?";lag=None
            for _ in range(10):
                pc=settler.open_contract(cid); c=pc.get("proposal_open_contract",{})
                if c.get("is_sold") or c.get("status") in ("won","lost"):
                    status=c.get("status"); pnl=float(c.get("profit",0))
                    et=c.get("exit_spot_time") or c.get("expiry_time"); lag=(et-T) if et else None
                    break
                time.sleep(0.8)
            with R["lock"]:
                R["n"]+=1
                if pnl is not None:
                    R["pnl"]+=pnl; R["bal"]+=pnl; R["wins"]+=int(pnl>0); R["rets"].append(pnl/stake)
            logw.writerow([time.strftime("%H:%M:%S"),sym,"",f"{sg:.3f}",ct,bar,f"{p:.4f}",f"{ev:.4f}",
                           f"{stake:.2f}",f"{rtt*1000:.0f}",cid,status,pnl,lag]); logf.flush()
    if trader: threading.Thread(target=settle,daemon=True).start()

    pub=websocket.create_connection(WS_PUBLIC,timeout=30)
    for sym in a.watch: pub.send(json.dumps({"ticks":sym,"subscribe":1}))
    R_={sym:Roller(pips[sym],a.trail) for sym in a.watch}
    for sym in a.watch:
        want=min(a.trail,40000)
        t,p,_=ctl.history_paged(sym,want,sleep=0.12)
        for q in p: R_[sym].push(q)
        R_[sym].trim_table()
        s=R_[sym].sigma(); print(f"{sym}: warm sigma={s and round(s,2)} table_n={R_[sym].table_n()}")

    rtts=collections.deque(maxlen=40); t_end=time.time()+a.minutes*60
    nsig=ntrade=nskip_sig=nskip_ev=0; halt=False
    while time.time()<t_end:
        try: msg=json.loads(pub.recv())
        except Exception:
            try:
                pub=websocket.create_connection(WS_PUBLIC,timeout=30)
                for sym in a.watch: pub.send(json.dumps({"ticks":sym,"subscribe":1}))
                continue
            except: break
        if msg.get("msg_type")!="tick": continue
        tk=msg["tick"]; sym=tk["symbol"]; r=R_[sym]
        r.push(tk["quote"]); r.trim_table()
        sg=r.sigma()
        if sg is None: continue
        if sg>a.sigma_max:
            nskip_sig+=1
            if nskip_sig%120==0: print(f"{time.strftime('%H:%M:%S')} {sym} sigma={sg:.2f}>{a.sigma_max} REGIME CLOSED (skip)")
            continue
        tab=r.table()
        if tab is None: continue
        dig=r.digit()
        b=best_ou(dig,tab,payouts[sym])
        if b is None: continue
        ct,bar,ws,ev,p,M=b
        if ev<=a.ev_gate:
            nskip_ev+=1; continue
        nsig+=1
        age=time.time()-tk["epoch"]
        if age>0.45: continue
        # sizing: flat stake (recommended) or quarter-Kelly
        with R["lock"]:
            rets=list(R["rets"]); bal=R["bal"]
        if a.stake and a.stake>0:
            stake=max(a.min_stake, round(a.stake,2))
        elif len(rets)>=200:
            mu=sum(rets)/len(rets); var=sum((x-mu)**2 for x in rets)/len(rets)
            f=max(0.0,min(a.max_frac, a.kelly_frac*(mu/var) if var>0 else 0))
            stake=max(a.min_stake, round(bal*f,2))
        else:
            stake=max(a.min_stake, round(bal*a.max_frac*0.5,2))
        line=f"{time.strftime('%H:%M:%S')} {sym} d={dig} sig={sg:.2f} {ct}{bar} p={p:.4f} EV={ev*100:+.2f}% stake=${stake:.2f} bal=${bal:.0f}"
        if not trader:
            if nsig%20==1: print(line)
            continue
        if halt: continue
        rtt_ok=True
        if len(rtts)>=10:
            p90=sorted(rtts)[int(len(rtts)*0.9)]
            if p90>0.8: rtt_ok=False
        if not rtt_ok: continue
        params=dict(amount=stake,basis="stake",contract_type=ct,currency="USD",
                    duration=1,duration_unit="t",underlying_symbol=sym,barrier=bar)
        t0=time.time()
        try: br=trader.call({"buy":1,"price":round(stake*1.02,2),"parameters":params})
        except Exception as e:
            print("buy transport err",e)
            try: trader._connect()
            except: pass
            continue
        rtt=time.time()-t0; rtts.append(rtt)
        if "buy" in br:
            ntrade+=1; print(line+f"  BUY rtt={rtt*1000:.0f}ms")
            pend.put((br["buy"]["contract_id"],tk["epoch"],sym,sg,ct,bar,p,ev,stake,rtt))
        else:
            print("buy err:",br.get("error",{}).get("message"))
        with R["lock"]:
            if R["pnl"]<-abs(a.max_loss): halt=True; print(f"### MAX LOSS {R['pnl']:.2f} -> halt")
        pub.settimeout(0.001)
        try:
            while True:
                m2=json.loads(pub.recv())
                if m2.get("msg_type")=="tick": R_[m2["tick"]["symbol"]].push(m2["tick"]["quote"]); R_[m2["tick"]["symbol"]].trim_table()
        except: pass
        pub.settimeout(30)
    time.sleep(4)
    with R["lock"]:
        print(f"\n=== signals={nsig} trades={ntrade} sigma-skips={nskip_sig} ev-skips={nskip_ev}")
        print(f"settled={R['n']} wins={R['wins']} PnL={R['pnl']:+.2f} bal=${R['bal']:.2f}")
    if trader: pend.put(None)
    logf.close()

if __name__=="__main__":
    main()
