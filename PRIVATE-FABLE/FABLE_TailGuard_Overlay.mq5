//+------------------------------------------------------------------+
//|                                      FABLE_TailGuard_Overlay.mq5  |
//|  PRIVATE-FABLE #1 — an account-level risk desk, not a strategy.   |
//|                                                                   |
//|  WHAT INSTITUTIONS HAVE THAT RETAIL EAs DON'T is not a secret     |
//|  indicator — it is a RISK DESK that sits ABOVE every strategy     |
//|  and can override all of them. This EA is that desk. It trades    |
//|  nothing. Attach it to ONE chart; it supervises the WHOLE         |
//|  account: every position, every magic, every symbol — including  |
//|  your other EAs (FFZ, SEMA, AdaptiveSwing, manual trades).        |
//|                                                                   |
//|  CONTROLS (each provable in the tester / on demo):               |
//|   1. DRAWDOWN LADDER — at −L1% from equity high-water mark it     |
//|      halves every position; at −L2% it flattens the account and   |
//|      locks trading for a cool-off (closes anything new).          |
//|   2. PORTFOLIO HEAT CAP — total open risk (entry→SL distance in   |
//|      money; ATR-estimated if no SL) may not exceed H% of equity.  |
//|      Worst offenders are trimmed first.                           |
//|   3. CORRELATION CLUSTERS — "XAUUSD,XAGUSD,AUDUSD|EURUSD,EURGBP,  |
//|      GBPUSD" style groups; max N net positions per cluster.       |
//|   4. NO-STOP POLICE — any position without a stop-loss gets one   |
//|      at K×ATR(D1) automatically. The #1 retail account-killer.    |
//|   5. MARGIN GUARD — margin level below M% ⇒ close worst position  |
//|      until healthy.                                               |
//|   6. WEEKEND FLAT (optional) — flatten N minutes before Friday    |
//|      close.                                                       |
//|                                                                   |
//|  WHY IT MATTERS (evidence): every blow-up pattern in the 97-EA    |
//|  legacy audit (DIAGNOSIS.md) — martingale stacks, no stops,       |
//|  correlated triple exposure — is structurally blocked here.      |
//|  This overlay would have capped the HedgeGuardian floating-loss   |
//|  spiral you saw, by construction.                                 |
//+------------------------------------------------------------------+
#property copyright "2026 Fable — PRIVATE series"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

input group "═══ 1. Drawdown ladder ═══"
input double InpDD_HalvePct    = 6.0;    // −% from HWM: halve all positions (0=off)
input double InpDD_FlattenPct  = 10.0;   // −% from HWM: flatten + lock (0=off)
input int    InpLockHours      = 24;     // Lock duration after flatten
input bool   InpUseBalanceHWM  = false;  // false = equity HWM (recommended)

input group "═══ 2. Portfolio heat ═══"
input double InpMaxHeatPct     = 4.0;    // Max total open risk (% equity, 0=off)

input group "═══ 3. Correlation clusters ═══"
input string InpClusters       = "XAUUSD,XAGUSD,AUDUSD|EURUSD,GBPUSD,EURGBP|USDJPY,EURJPY,GBPJPY";
input int    InpMaxPerCluster  = 2;      // Max positions per cluster (0=off)

input group "═══ 4. No-stop police ═══"
input bool   InpForceStops     = true;
input double InpForceStopATR   = 4.0;    // Auto-SL at K×ATR(D1)

input group "═══ 5. Margin guard ═══"
input double InpMinMarginLevel = 300.0;  // Close worst below this margin level % (0=off)

input group "═══ 6. Weekend flat ═══"
input bool   InpWeekendFlat    = false;
input int    InpFridayCloseHr  = 21;     // Server hour Friday to flatten at

input group "═══ Misc ═══"
input bool   InpDryRun         = false;  // true = log interventions, don't execute
input bool   InpHUD            = true;

CTrade trade;
double   g_hwm=0;
datetime g_lockUntil=0;
int      g_interventions=0;
string   g_lastAction="none";

int OnInit()
{
   trade.SetDeviationInPoints(50);
   g_hwm = InpUseBalanceHWM ? AccountInfoDouble(ACCOUNT_BALANCE) : AccountInfoDouble(ACCOUNT_EQUITY);
   EventSetTimer(5);
   Print("TailGuard active. DryRun=", InpDryRun, " — supervising ALL magics/symbols on this account.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int r){ EventKillTimer(); Comment(""); }
void OnTick(){ Supervise(); }
void OnTimer(){ Supervise(); }

void Supervise()
{
   double eq = InpUseBalanceHWM ? AccountInfoDouble(ACCOUNT_BALANCE) : AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq>g_hwm) g_hwm=eq;
   double ddPct = (g_hwm>0) ? 100.0*(1.0-eq/g_hwm) : 0;

   // 0) lock window: flatten anything that appears while locked
   if(TimeCurrent()<g_lockUntil)
   {
      if(PositionsTotal()>0) FlattenAll("lock active");
      if(InpHUD) Dash(ddPct);
      return;
   }

   // 1) drawdown ladder
   if(InpDD_FlattenPct>0 && ddPct>=InpDD_FlattenPct)
   {
      FlattenAll(StringFormat("DD %.1f%% >= flatten level", ddPct));
      g_lockUntil=TimeCurrent()+InpLockHours*3600;
      if(InpHUD) Dash(ddPct);
      return;
   }
   static bool halved=false;
   if(InpDD_HalvePct>0 && ddPct>=InpDD_HalvePct)
   {
      if(!halved){ HalveAll(StringFormat("DD %.1f%% >= halve level", ddPct)); halved=true; }
   }
   else if(ddPct < InpDD_HalvePct*0.5) halved=false;

   // 4) no-stop police (before heat calc so heat uses real stops)
   if(InpForceStops) ForceStops();

   // 2) heat cap
   if(InpMaxHeatPct>0) EnforceHeat(eq);

   // 3) cluster caps
   if(InpMaxPerCluster>0) EnforceClusters();

   // 5) margin guard
   if(InpMinMarginLevel>0)
   {
      double ml=AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
      if(ml>0 && ml<InpMinMarginLevel) CloseWorst(StringFormat("margin level %.0f%% < %.0f%%", ml, InpMinMarginLevel));
   }

   // 6) weekend flat
   if(InpWeekendFlat)
   {
      MqlDateTime mt; TimeToStruct(TimeCurrent(), mt);
      if(mt.day_of_week==5 && mt.hour>=InpFridayCloseHr && PositionsTotal()>0)
         FlattenAll("weekend flat");
   }

   if(InpHUD) Dash(ddPct);
}

//------------------------------------------------------------------
double PositionRiskMoney(ulong tk)
{
   if(!PositionSelectByTicket(tk)) return 0;
   string sym=PositionGetString(POSITION_SYMBOL);
   double vol=PositionGetDouble(POSITION_VOLUME);
   double op=PositionGetDouble(POSITION_PRICE_OPEN);
   double sl=PositionGetDouble(POSITION_SL);
   long type=PositionGetInteger(POSITION_TYPE);
   double dist;
   if(sl>0) dist=MathAbs(op-sl);
   else
   {
      int h=iATR(sym, PERIOD_D1, 14);
      double a[1];
      dist = (h!=INVALID_HANDLE && CopyBuffer(h,0,1,1,a)==1) ? InpForceStopATR*a[0] : op*0.02;
   }
   double tickSize=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE);
   double tickVal=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0||tickVal<=0) return 0;
   return dist/tickSize*tickVal*vol;
}

void EnforceHeat(double eq)
{
   double cap=eq*InpMaxHeatPct/100.0;
   double heat=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk>0) heat+=PositionRiskMoney(tk);
   }
   int guard=0;
   while(heat>cap && PositionsTotal()>0 && guard<20)
   {
      guard++;
      // trim the largest-risk position by half (or close if tiny)
      ulong worst=0; double worstRisk=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong tk=PositionGetTicket(i);
         if(tk==0) continue;
         double r=PositionRiskMoney(tk);
         if(r>worstRisk){ worstRisk=r; worst=tk; }
      }
      if(worst==0) break;
      if(!PositionSelectByTicket(worst)) break;
      string sym=PositionGetString(POSITION_SYMBOL);
      double vol=PositionGetDouble(POSITION_VOLUME);
      double half=NormalizeVol(sym, vol/2.0);
      Intervene(StringFormat("HEAT %.0f > cap %.0f: trimming %s (risk %.0f)", heat, cap, sym, worstRisk));
      if(!InpDryRun)
      {
         if(half>0 && half<vol) trade.PositionClosePartial(worst, half);
         else trade.PositionClose(worst);
      }
      else break; // dry run: log once
      // recompute
      heat=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong tk=PositionGetTicket(i);
         if(tk>0) heat+=PositionRiskMoney(tk);
      }
   }
}

void EnforceClusters()
{
   string clusters[];
   int nc=StringSplit(InpClusters, '|', clusters);
   for(int c=0;c<nc;c++)
   {
      string membs[];
      int nm=StringSplit(clusters[c], ',', membs);
      // count positions in this cluster, newest first
      ulong newest=0; datetime newestT=0; int count=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong tk=PositionGetTicket(i);
         if(tk==0) continue;
         string sym=PositionGetString(POSITION_SYMBOL);
         bool inCluster=false;
         for(int m=0;m<nm;m++)
         {
            string ms=membs[m]; StringTrimLeft(ms); StringTrimRight(ms);
            if(StringFind(sym, ms)>=0){ inCluster=true; break; }
         }
         if(!inCluster) continue;
         count++;
         datetime t=(datetime)PositionGetInteger(POSITION_TIME);
         if(t>newestT){ newestT=t; newest=tk; }
      }
      if(count>InpMaxPerCluster && newest>0)
      {
         Intervene(StringFormat("CLUSTER %d: %d positions > max %d, closing newest", c, count, InpMaxPerCluster));
         if(!InpDryRun) trade.PositionClose(newest);
      }
   }
}

void ForceStops()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetDouble(POSITION_SL)>0) continue;
      string sym=PositionGetString(POSITION_SYMBOL);
      int h=iATR(sym, PERIOD_D1, 14);
      double a[1];
      if(h==INVALID_HANDLE || CopyBuffer(h,0,1,1,a)!=1 || a[0]<=0) continue;
      long type=PositionGetInteger(POSITION_TYPE);
      double op=PositionGetDouble(POSITION_PRICE_OPEN);
      int dg=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
      double sl=(type==POSITION_TYPE_BUY)? op-InpForceStopATR*a[0] : op+InpForceStopATR*a[0];
      Intervene(StringFormat("NO-STOP POLICE: %s ticket %I64u gets SL %.5f", sym, tk, sl));
      if(!InpDryRun) trade.PositionModify(tk, NormalizeDouble(sl,dg), PositionGetDouble(POSITION_TP));
   }
}

void CloseWorst(string why)
{
   ulong worst=0; double worstPnl=DBL_MAX;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      double pnl=PositionGetDouble(POSITION_PROFIT);
      if(pnl<worstPnl){ worstPnl=pnl; worst=tk; }
   }
   if(worst>0)
   {
      Intervene("MARGIN GUARD ("+why+"): closing worst position");
      if(!InpDryRun) trade.PositionClose(worst);
   }
}

void HalveAll(string why)
{
   Intervene("DD LADDER ("+why+"): halving all positions");
   if(InpDryRun) return;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      string sym=PositionGetString(POSITION_SYMBOL);
      double vol=PositionGetDouble(POSITION_VOLUME);
      double half=NormalizeVol(sym, vol/2.0);
      if(half>0 && half<vol) trade.PositionClosePartial(tk, half);
   }
}

void FlattenAll(string why)
{
   Intervene("FLATTEN ("+why+")");
   if(InpDryRun) return;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk>0) trade.PositionClose(tk);
   }
}

double NormalizeVol(string sym, double v)
{
   double mn=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double st=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   if(st>0) v=MathFloor(v/st)*st;
   return (v>=mn)?v:0;
}

void Intervene(string what)
{
   g_interventions++;
   g_lastAction=what;
   Print("TailGuard #", g_interventions, ": ", what, InpDryRun?" [DRY RUN]":"");
}

void Dash(double ddPct)
{
   double heat=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk>0) heat+=PositionRiskMoney(tk);
   }
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   Comment(StringFormat(
      "FABLE TailGuard %s\nHWM %.2f | DD %.2f%% | heat %.0f (%.1f%% eq, cap %.1f%%)\npositions %d | interventions %d\nlast: %s%s",
      InpDryRun?"[DRY RUN]":"[ARMED]", g_hwm, ddPct, heat, eq>0?100*heat/eq:0, InpMaxHeatPct,
      PositionsTotal(), g_interventions, g_lastAction,
      TimeCurrent()<g_lockUntil?"\n*** LOCKED ***":""));
}
//+------------------------------------------------------------------+
