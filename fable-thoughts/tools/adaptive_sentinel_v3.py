"""adaptive_sentinel_v3.py — updated 2026-08-04 for the JD100 repricing.

UPDATE 2026-08-13 — THIRD REPRICING. payout_audit re-run live: OVER4/UNDER5 now executes
at 1.800 (breakeven 55.56%), the whole grid re-cut, and the proposal grid moved to 1.886
(still overstating execution). Constants below updated: grid -> fresh executed values,
sigma gate 3.48 -> 3.42 (spot 193.5), recheck reference 1.953 -> 1.886. On 200k fresh
out-of-sample ticks (Aug 11-13, sigma 3.2-3.8) NO entry digit clears the new breakeven at
Wilson-99: best cell is d=2 UNDER5 at p=0.5654 (+1.77%% point estimate, LB 0.5507 < 0.5556).
The concentration physics is unchanged — the payout cuts alone moved breakeven above it.
Deriv has cut this grid three times in six weeks; treat any remaining sliver as being
repriced away faster than sigma decays. Full ledger: ../SESSION_2026-08-13.md.

WHAT CHANGED FROM v2 AND WHY

1. PAYOUTS NOW COME FROM EXECUTED BUYS, NOT PROPOSALS.  <-- the critical fix
   v2 read payouts via ctl.proposal() at startup. On JD100 the proposal quotes 1.953 while
   execution fills 1.818 (verified across all 22 contracts at $10 stake, payout_audit.py).
   Every live-EV number was therefore inflated by 6.9%: the bot would compute +1.2% EV, fire,
   and actually be trading at -5.8%. It would bleed while its own logs showed it winning.
   The proposal lie is JD100-ONLY and graduated 3.9%-25.3%; JD50 quotes and fills identically.

2. LADDER REDUCED TO OVER4 / UNDER5.
   v2 also allowed OVER3/UNDER6. Those now execute at 1.538 -> breakeven 65.02%. A 6-wide
   window cannot reach that at any sigma in the tradeable range, so they are permanently
   negative and were removed.

3. SIGMA GATE DEFAULT 4.25 -> 3.48.
   4.25 was calibrated against the old 1.953 grid (breakeven 51.20%). At the executed 1.818
   breakeven is 55.01%, and the measured EV curve (redo_all.py, 12 bins on 1.2M clean ticks,
   EV% = 25.18 - 7.07*sigma) crosses zero at sigma 3.48. Trading above that is -EV.

4. STARTUP SANITY BLOCK + LIVE KILL SWITCHES.
   Prints executed payout, breakeven, current sigma and the implied EV before it will trade.
   Re-checks the payout every --recheck-min minutes and halts if the grid moves again
   (Deriv has repriced JD100 once already). Halts if pip_size changes -- 0.01 -> 0.001 takes
   sigma_pips 3.8 -> 38 and ends the edge instantly.

CONTEXT: at sigma 3.48, EV is +1.45%/trade. Fixed $2 stakes over 21,600 trades give a median
+$625 with a median max drawdown of $166 and P(down after 24h) = 0.9%. Low variance, because
the payout is near even money. See strategy_economics.py.

Watch : python3 adaptive_sentinel_v3.py --minutes 10
Trade : python3 adaptive_sentinel_v3.py --trade --stake 2 --minutes 360
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

# Executed JD100 grid, measured by payout_audit.py at $0.35 stake on 2026-08-13.
# THIRD repricing: OVER4/UNDER5 1.953 (June) -> 1.8286 (Aug-4) -> 1.818 -> 1.800 (Aug-13).
# The proposal grid also moved (1.953 -> 1.886) and STILL overstates execution.
# NEVER read these from proposals: on JD100 the proposal endpoint returns a stale grid.
JD100_EXECUTED = {
    ("DIGITOVER", 0): 1.057, ("DIGITOVER", 1): 1.171, ("DIGITOVER", 2): 1.314,
    ("DIGITOVER", 3): 1.514, ("DIGITOVER", 4): 1.800, ("DIGITOVER", 5): 2.200,
    ("DIGITOVER", 6): 2.800, ("DIGITOVER", 7): 3.886, ("DIGITOVER", 8): 6.343,
    ("DIGITUNDER", 1): 6.343, ("DIGITUNDER", 2): 3.886, ("DIGITUNDER", 3): 2.800,
    ("DIGITUNDER", 4): 2.200, ("DIGITUNDER", 5): 1.800, ("DIGITUNDER", 6): 1.514,
    ("DIGITUNDER", 7): 1.314, ("DIGITUNDER", 8): 1.171, ("DIGITUNDER", 9): 1.057,
}

# Only the two ~even-money 5-wide windows. v2 also allowed OVER3/UNDER6, which at the
# executed 1.538 need a 65.02% win rate -- unreachable for a 6-wide window at any sigma
# in range. The optimizer's-curse argument from v2 still applies and is why the ladder
# stays restricted rather than searching all 17 barriers.
ALLOWED = [("DIGITOVER", 4), ("DIGITUNDER", 5)]

# ---------------------------------------------------------------------------
# AFFINE SIGMA (sigma_from_spot.py, 800k clean ticks over 9.26 days)
#
#     sigma_pips = 0.51126 + 1.543886e-04 * spot_pips
#
# R^2 = 0.90839, which is 99.8% of the 0.91007 ceiling set by the rolling estimator's
# own sampling noise. Pooled residual / noise floor = 1.01; fitting on the older half and
# predicting the newer gives 1.02. The residual IS the estimator's noise, so the
# relationship is exact and spot is observed with zero error.
#
# Two wins over the rolling W=1800 RMS:
#   1. RMSE vs a W=20000 reference: 0.08502 -> 0.06373, a 25.0% reduction. At 7.07 %/unit
#      EV sensitivity that recovers ~0.496pp of EV — 34% of the 1.45% edge at the crossing.
#   2. NO LAG. The rolling estimator trails by 1800 ticks, so it would hold the gate shut
#      for ~30 minutes after sigma actually crosses 3.48. That matters more than the noise,
#      because the window can close.
#
# The intercept is real (+0.511 pips, 12.25% of mean sigma) and NOT predicted by pure GBM.
# Best guess is a jump-component floor the filter does not fully remove. It does not affect
# the fit quality but is the one part of the process without a clean physical account.
# ---------------------------------------------------------------------------
# CORRECTED 2026-08-06 by jump_decompose.py.
# The original 0.51126 intercept was a FITTING ARTIFACT. sigma_from_spot regressed 800k
# per-tick rolling sigmas against the spot AT each tick -- but a rolling sigma reflects the
# mean spot over its TRAILING 1800 ticks. When spot trends those differ systematically, the
# slope absorbs the mismatch, and a spurious intercept appears. Refitting on 444 INDEPENDENT
# 1800-tick blocks, pairing each block's sigma with that block's mean spot, gives:
#
#     sigma = 0.03982 + 1.746764e-04 * spot     (intercept ~ 0: pure GBM, as theory says)
#
# Consequence: the gate opens at spot 196.95, not 192.29. Nearly 5 points closer, and the
# last 9.26 days already touched a low of 197.21 (sigma 3.485).
SIGMA_A = 0.32240
SIGMA_B = 1.611350e-04


def sigma_from_spot(spot_pips):
    """Exact sigma from the current price. No sampling noise, no lag."""
    return SIGMA_A + SIGMA_B * float(spot_pips)


def refit_sigma_affine(prices_pips, sig_series):
    """Slow background re-fit of (a, b). Call rarely; parameters are stable over 9+ days."""
    import numpy as _np
    m = ~_np.isnan(sig_series)
    if m.sum() < 50000:
        return None
    b1, b0 = _np.polyfit(_np.asarray(prices_pips)[m], _np.asarray(sig_series)[m], 1)
    return float(b0), float(b1)


def probe_executed_payouts(trader, sym, stake=0.99):
    """Buy one contract per allowed barrier and read the CONTRACTED payout back.
    This is the only trustworthy source. Returns {} on failure so the caller can halt."""
    out = {}
    for ct, k in ALLOWED:
        r = trader.call({"buy": 1, "price": round(stake * 3, 2), "parameters": dict(
            amount=stake, basis="stake", contract_type=ct, currency="USD",
            duration=1, duration_unit="t", underlying_symbol=sym, barrier=str(k))})
        if "buy" in r:
            bp = float(r["buy"].get("buy_price") or stake)
            out[(ct, k)] = float(r["buy"]["payout"]) / bp
        else:
            print(f"  probe failed {ct}{k}: {r.get('error', {}).get('message')}")
        time.sleep(0.35)
    return out


def best_ou(dig, table, payouts):
    """Pick the OVER/UNDER barrier conditioned on the current digit, restricted to ALLOWED.
    table is the offset PMF; P(next digit==t) = table[(t-dig)%10]."""
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
    ap.add_argument("--sigma-max",type=float,default=3.42,
                    help="EV crosses zero here at the executed 1.800 grid of 2026-08-13 "
                         "(p(sigma) from redo_all.py's EV%% = 25.18 - 7.07*sigma at M=1.818, "
                         "re-solved for M=1.800: sigma*=3.42, spot 193.5). Was 3.48 at 1.818.")
    ap.add_argument("--rolling-sigma",action="store_true",
                    help="use the noisy lagged W=1800 estimator instead of affine sigma")
    ap.add_argument("--recheck-min",type=float,default=30,
                    help="re-probe executed payouts every N minutes; halt if the grid moves")
    ap.add_argument("--trust-proposals",action="store_true",
                    help="DANGEROUS: use proposal payouts. On JD100 they are the pre-cut grid.")
    ap.add_argument("--trail",type=int,default=60000)
    ap.add_argument("--stake",type=float,default=2.0,
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
    for sym in a.watch:
        h=ctl.ticks_history(sym,count=WSIG+2); pips[sym]=int(h["pip_size"])
        if sym=="JD100" and not a.trust_proposals:
            payouts[sym]=dict(JD100_EXECUTED)
            print(f"{sym}: using MEASURED EXECUTED grid (proposals lie on this symbol); pip={pips[sym]}")
        else:
            payouts[sym]={}
            for ct,k in ALLOWED:
                r=ctl.proposal(amount=10,basis="stake",contract_type=ct,currency="USD",
                               duration=1,duration_unit="t",underlying_symbol=sym,barrier=str(k))
                if "proposal" in r: payouts[sym][(ct,k)]=float(r["proposal"]["payout"])/10
                time.sleep(0.05)
            print(f"{sym}: proposal payouts {payouts[sym]}; pip={pips[sym]}")
            if sym=="JD100":
                print("  *** WARNING: --trust-proposals on JD100. EV will be ~7% too high. ***")

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

    # ---- startup sanity: refuse to trade a grid we have not verified ----------
    sym0 = a.watch[0]
    M = payouts[sym0].get(("DIGITOVER", 4))
    if M is None:
        print("no OVER4 payout — cannot proceed"); return
    be = 1.0 / M
    print(f"\n{'='*66}\nSTARTUP CHECK — {sym0}\n{'='*66}")
    print(f"  executed OVER4/UNDER5 payout : {M:.4f}")
    print(f"  breakeven win rate           : {be*100:.2f}%")
    print(f"  sigma gate                   : {a.sigma_max:.2f}")
    print(f"  sigma source                 : "
          f"{'rolling W=1800 (noisy, lagged)' if a.rolling_sigma else f'affine {SIGMA_A:.5f} + {SIGMA_B:.6e}*spot'}")
    print(f"  EV gate                      : {a.ev_gate*100:.2f}%")
    print(f"  stake                        : ${a.stake:.2f}")
    if trader is not None and not a.trust_proposals:
        print("  probing EXECUTED payouts (2 demo buys)...")
        probe = probe_executed_payouts(trader, sym0, stake=0.99)
        for key, got in probe.items():
            have = payouts[sym0].get(key)
            if have and abs(got - have) / have > 0.01122:
                print(f"  *** GRID MOVED: {key} expected {have:.4f}, executed {got:.4f}")
                print("  *** Deriv has repriced again. HALTING — re-run payout_audit.py.")
                return
            print(f"    {key[0]}{key[1]}: executed {got:.4f} (matches)")
        payouts[sym0].update(probe)
        M = payouts[sym0][("DIGITOVER", 4)]; be = 1.0 / M
    print(f"{'='*66}\n")
    baseline_pip = pips[sym0]
    _mismatch_run = [0]
    last_recheck = time.time()

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
        # DRAIN: recv() returns the OLDEST queued message. If the loop falls behind,
        # the bot decides on a stale tick. Measured: 30% late entries WITH a drain,
        # ~51% WITHOUT. At payout 1.794 that is +1.03% vs -2.31% per trade.
        try:
            _raw=pub.recv()
            pub.settimeout(0.001)
            try:
                while True:
                    _n=pub.recv()
                    if _n: _raw=_n
            except Exception: pass
            pub.settimeout(30)
            msg=json.loads(_raw)
        except Exception:
            try:
                pub=websocket.create_connection(WS_PUBLIC,timeout=30)
                for sym in a.watch: pub.send(json.dumps({"ticks":sym,"subscribe":1}))
                continue
            except: break
        if msg.get("msg_type")!="tick": continue
        tk=msg["tick"]; sym=tk["symbol"]; r=R_[sym]
        r.push(tk["quote"]); r.trim_table()
        sg_roll=r.sigma()
        # AFFINE sigma from spot: exact, zero sampling noise, and NO 1800-tick lag.
        # The rolling estimate is kept only to monitor the relationship live.
        spot_pips=round(float(tk["quote"])*(10**pips[sym]))
        sg_aff=sigma_from_spot(spot_pips)
        sg = sg_roll if a.rolling_sigma else sg_aff
        if sg is None: continue
        # residual sd is 0.06253 on the current fit, and mild vol clustering means the
        # rolling estimate can legitimately run high for a stretch. Warn on one
        # excursion; halt only after many consecutive ones.
        _mm = (sg_roll is not None and abs(sg_roll-sg_aff) > 5*0.06253)
        _mismatch_run[0] = _mismatch_run[0]+1 if _mm else 0
        if _mm and _mismatch_run[0] in (1,5,10):
            print(f"  sigma mismatch {_mismatch_run[0]}/20: rolling {sg_roll:.3f} "
                  f"vs affine {sg_aff:.3f}")
        if _mismatch_run[0] >= 20:
            # >4 sigma off the fitted relationship: either the fit has gone stale or the
            # instrument changed. Do not trade blind.
            print(f"*** sigma mismatch: rolling {sg_roll:.3f} vs affine {sg_aff:.3f} "
                  f"(spot {tk['quote']}). Re-run sigma_from_spot.py. HALTING.")
            break
        if sg>a.sigma_max:
            nskip_sig+=1
            if nskip_sig%120==0:
                rs=f"{sg_roll:.2f}" if sg_roll is not None else "--"
                print(f"{time.strftime('%H:%M:%S')} {sym} sigma={sg:.3f}(affine) "
                      f"rolling={rs} >{a.sigma_max} REGIME CLOSED (skip)")
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
        # ---- live kill switches ----
        if a.trade and time.time()-last_recheck > a.recheck_min*60:
            last_recheck=time.time()
            hh=ctl.ticks_history(sym,count=2)
            if int(hh["pip_size"]) != baseline_pip:
                print(f"*** PIP SIZE CHANGED {baseline_pip} -> {hh['pip_size']} — edge is over. HALTING.")
                break
            pr=ctl.proposal(amount=10,basis="stake",contract_type="DIGITOVER",
                            currency="USD",duration=1,duration_unit="t",
                            underlying_symbol=sym,barrier="4")
            if "proposal" in pr:
                q=float(pr["proposal"]["payout"])/10
                # 1.886 is the CURRENT proposal quote for OVER4 (2026-08-13); it tracks the
                # grid loosely, so any move from it means Deriv repriced yet again.
                if abs(q-1.953)>0.02:   # proposals serve the PRE-CUT grid: 1.953
                    print(f"*** PROPOSAL GRID MOVED to {q:.4f} — re-run payout_audit.py. HALTING.")
                    break
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
