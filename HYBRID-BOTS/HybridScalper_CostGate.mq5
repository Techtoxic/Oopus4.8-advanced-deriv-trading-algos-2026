//+------------------------------------------------------------------+
//|                                      HybridScalper_CostGate.mq5   |
//|  A scalper that refuses to scalp when the math says no.            |
//|                                                                    |
//|  WHY THIS DESIGN (evidence):                                       |
//|   fable5-session/deriv/FINDINGS_FABLE5.md measured a real 1-minute |
//|   mean-reversion signal in FX (USDJPY lag-1 autocorr −0.112,       |
//|   z = −10.6) — and showed it is ~10× SMALLER than Deriv's costs.   |
//|   On an MT5 raw-spread account (0.5–1.5 bps round trip) the same   |
//|   signal is BORDERLINE tradeable. Borderline means: only trade     |
//|   when the measured edge actually exceeds the measured cost, and   |
//|   stand down otherwise. Most scalpers die because they never do    |
//|   this arithmetic. This EA does it continuously, live.             |
//|                                                                    |
//|  HOW IT WORKS:                                                     |
//|   1. Rolling lag-1 autocorrelation of M-bar returns (default M5).  |
//|      Signal regime requires ac1 < 0 with |z| ≥ InpMinZ.            |
//|   2. Expected favorable move per trade ≈ |ac1| × mean|return|.     |
//|   3. Live cost = current spread + slippage allowance, in bps.      |
//|   4. ARMED only when edge_bps ≥ InpEdgeCostRatio × cost_bps.       |
//|   5. When armed: fade the previous bar (z-score entry vs EMA),     |
//|      tight ATR stop, reversion target, time stop, BE.              |
//|   6. Spread-percentile gate: only trade when spread is in the      |
//|      cheapest InpSpreadPctMax of its rolling distribution.         |
//|                                                                    |
//|  STATUS: EXPERIMENTAL. The arming logic is the product. Run it on  |
//|  a RAW/zero-spread account or it will (correctly) never trade.     |
//|  Demo first. If it never arms on your broker, your costs are too   |
//|  high for scalping — that is the honest answer, not a bug.         |
//+------------------------------------------------------------------+
#property copyright "2026 Fable — Hybrid series"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

input group "═══ Edge/Cost Gate ═══"
input int    InpACWindow       = 600;    // Bars for autocorr estimate
input double InpMinZ           = 3.0;    // Min |z| of ac1 to consider signal real
input double InpEdgeCostRatio  = 1.5;    // Edge must exceed cost × this
input double InpSlippageBps    = 0.3;    // Slippage allowance (bps, round trip)
input int    InpSpreadWindow   = 500;    // Spread distribution window (ticks)
input double InpSpreadPctMax   = 0.30;   // Trade only in cheapest fraction of spreads

input group "═══ Entry/Exit ═══"
input double InpZEntry         = 2.0;    // |z-score| of close vs EMA to fade
input int    InpEMA            = 20;     // Mean EMA
input int    InpATR            = 14;     // ATR period
input double InpStopATR        = 1.2;    // Stop = ATR × this
input int    InpTimeStopBars   = 12;     // Exit after N bars regardless
input double InpBE_R           = 0.7;    // Breakeven at +R

input group "═══ Risk ═══"
input double InpRiskPct        = 0.25;   // Risk % per trade (scalps = small)
input double InpDailyLossPct   = 2.0;    // Daily halt (% equity)
input int    InpMaxTradesDay   = 30;     // Max trades/day
input long   InpMagic          = 483301;
input int    InpSlippagePts    = 10;
input bool   InpHUD            = true;

CTrade trade;
int hATR=INVALID_HANDLE, hEMA=INVALID_HANDLE;
datetime g_lastBar=0;
double  g_spreads[];
int     g_spreadN=0;
double  g_dayEq=0; datetime g_dayStamp=0; bool g_halt=false; int g_tradesToday=0;
string  g_state="warmup";
double  g_entry=0, g_risk=0; int g_dir=0, g_barsIn=0;

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   hATR=iATR(_Symbol,_Period,InpATR);
   hEMA=iMA(_Symbol,_Period,InpEMA,0,MODE_EMA,PRICE_CLOSE);
   if(hATR==INVALID_HANDLE||hEMA==INVALID_HANDLE) return INIT_FAILED;
   ArrayResize(g_spreads, InpSpreadWindow);
   g_dayEq=AccountInfoDouble(ACCOUNT_EQUITY);
   g_dayStamp=TimeCurrent()-(TimeCurrent()%86400);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int r){ if(hATR!=INVALID_HANDLE) IndicatorRelease(hATR); if(hEMA!=INVALID_HANDLE) IndicatorRelease(hEMA); Comment(""); }

void OnTick()
{
   // track spread distribution every tick
   double spr = SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID);
   g_spreads[g_spreadN % InpSpreadWindow] = spr;
   g_spreadN++;

   Manage();
   RollDay();

   datetime cur=iTime(_Symbol,_Period,0);
   if(cur==g_lastBar){ if(InpHUD) Dash(); return; }
   g_lastBar=cur;
   if(HasPos()) { g_barsIn++; if(InpHUD) Dash(); return; }
   if(g_halt || g_tradesToday>=InpMaxTradesDay){ if(InpHUD) Dash(); return; }

   // ---- 1-2: signal regime + edge estimate ----
   double ac1, zac, meanAbsRet;
   if(!Autocorr(ac1, zac, meanAbsRet)){ g_state="warmup"; if(InpHUD) Dash(); return; }
   double edge_bps = MathAbs(ac1) * meanAbsRet * 10000.0;

   // ---- 3: live cost ----
   double price=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   if(price<=0){ return; }
   double cost_bps = (spr/price)*10000.0 + InpSlippageBps;

   bool signalReal = (ac1<0 && MathAbs(zac)>=InpMinZ);
   bool edgeCovers = (edge_bps >= InpEdgeCostRatio*cost_bps);
   bool sprCheap   = SpreadCheap(spr);

   if(!signalReal){ g_state=StringFormat("DISARMED: ac1=%.3f z=%.1f (no MR regime)",ac1,zac); if(InpHUD) Dash(); return; }
   if(!edgeCovers){ g_state=StringFormat("DISARMED: edge %.2fbps < %.1fx cost %.2fbps",edge_bps,InpEdgeCostRatio,cost_bps); if(InpHUD) Dash(); return; }
   if(!sprCheap)  { g_state="DISARMED: spread not in cheap regime"; if(InpHUD) Dash(); return; }

   // ---- 5: armed — fade extension from EMA ----
   double ema[], atr[], cl[];
   ArraySetAsSeries(ema,true); ArraySetAsSeries(atr,true); ArraySetAsSeries(cl,true);
   if(CopyBuffer(hEMA,0,0,2,ema)<2) return;
   if(CopyBuffer(hATR,0,0,2,atr)<2) return;
   if(CopyClose(_Symbol,_Period,0,3,cl)<3) return;
   double atrV=atr[1];
   if(atrV<=0) return;
   double z=(cl[1]-ema[1])/atrV;

   int dir=0;
   if(z>=InpZEntry && cl[1]>cl[2]) dir=-1;      // stretched up → fade short
   else if(z<=-InpZEntry && cl[1]<cl[2]) dir=1; // stretched down → fade long
   g_state=StringFormat("ARMED: edge %.2f vs cost %.2f bps | z=%.2f",edge_bps,cost_bps,z);
   if(dir==0){ if(InpHUD) Dash(); return; }

   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK), bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entry=(dir>0)?ask:bid;
   double slDist=InpStopATR*atrV;
   double sl=entry-dir*slDist;
   double tp=ema[1];   // reversion target: the mean itself
   if(dir>0 && tp<=entry) tp=entry+slDist;      // degenerate guard
   if(dir<0 && tp>=entry) tp=entry-slDist;

   double lots=CalcLots(slDist);
   if(lots<=0){ if(InpHUD) Dash(); return; }
   bool ok=(dir>0)?trade.Buy(lots,_Symbol,0,NormalizeDouble(sl,_Digits),NormalizeDouble(tp,_Digits),"CostGate")
                  :trade.Sell(lots,_Symbol,0,NormalizeDouble(sl,_Digits),NormalizeDouble(tp,_Digits),"CostGate");
   if(ok){ g_entry=entry; g_dir=dir; g_risk=slDist; g_barsIn=0; g_tradesToday++; }
   if(InpHUD) Dash();
}

bool Autocorr(double &ac1, double &z, double &meanAbsRet)
{
   double cl[];
   ArraySetAsSeries(cl,true);
   int need=InpACWindow+2;
   if(CopyClose(_Symbol,_Period,1,need,cl)<need) return false;
   int n=need-1;
   double rets[]; ArrayResize(rets,n);
   double sum=0, sumAbs=0;
   for(int i=0;i<n;i++)
   {
      if(cl[i+1]<=0) return false;
      rets[i]=MathLog(cl[i]/cl[i+1]);
      sum+=rets[i]; sumAbs+=MathAbs(rets[i]);
   }
   double mean=sum/n;
   meanAbsRet=sumAbs/n;
   double num=0, den=0;
   for(int i=0;i<n-1;i++) num+=(rets[i]-mean)*(rets[i+1]-mean);
   for(int i=0;i<n;i++)   den+=(rets[i]-mean)*(rets[i]-mean);
   if(den<=0) return false;
   ac1=num/den;
   z=ac1*MathSqrt((double)n);
   return true;
}

bool SpreadCheap(double cur)
{
   int n=MathMin(g_spreadN, InpSpreadWindow);
   if(n<50) return false;
   int le=0;
   for(int i=0;i<n;i++) if(g_spreads[i]<=cur) le++;
   return ((double)le/n) <= InpSpreadPctMax;
}

double CalcLots(double slDist)
{
   double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickVal=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0||tickVal<=0||slDist<=0) return 0;
   double money=AccountInfoDouble(ACCOUNT_EQUITY)*InpRiskPct/100.0;
   double lossPerLot=slDist/tickSize*tickVal;
   if(lossPerLot<=0) return 0;
   double lots=money/lossPerLot;
   double mn=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double mx=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double st=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(st>0) lots=MathFloor(lots/st)*st;
   return MathMax(mn,MathMin(mx,lots))>=mn ? MathMax(mn,MathMin(mx,lots)) : 0;
}

bool HasPos()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol && PositionGetInteger(POSITION_MAGIC)==InpMagic)
         return true;
   }
   return false;
}

void Manage()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol||PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      long type=PositionGetInteger(POSITION_TYPE);
      double op=PositionGetDouble(POSITION_PRICE_OPEN);
      double sl=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP);
      int dir=(type==POSITION_TYPE_BUY)?1:-1;
      double cur=(dir>0)?SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      double r=(g_risk>0)? dir*(cur-op)/g_risk : 0;

      // time stop
      if(InpTimeStopBars>0 && g_barsIn>=InpTimeStopBars)
      { trade.PositionClose(tk); continue; }

      // breakeven
      if(InpBE_R>0 && r>=InpBE_R)
      {
         double be=op+dir*2*_Point;
         bool need=(dir>0)?(sl<be):(sl>be||sl==0);
         if(need) trade.PositionModify(tk,NormalizeDouble(be,_Digits),tp);
      }
   }
}

void RollDay()
{
   datetime now=TimeCurrent();
   datetime d=now-(now%86400);
   if(d!=g_dayStamp){ g_dayStamp=d; g_dayEq=AccountInfoDouble(ACCOUNT_EQUITY); g_halt=false; g_tradesToday=0; }
   if(InpDailyLossPct>0 && g_dayEq>0 &&
      AccountInfoDouble(ACCOUNT_EQUITY)<=g_dayEq*(1.0-InpDailyLossPct/100.0)) g_halt=true;
}

void Dash()
{
   Comment(StringFormat("CostGate Scalper [%s %s]\n%s\ntrades today %d/%d  halted %d",
           _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period), g_state, g_tradesToday, InpMaxTradesDay, g_halt?1:0));
}
//+------------------------------------------------------------------+
