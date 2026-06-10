//+------------------------------------------------------------------+
//|                                       FABLE_RegimeAllocator.mq5   |
//|  PRIVATE-FABLE #2 — a desk-style capital allocator that routes    |
//|  risk between the two engines this research program actually      |
//|  validated, based on live regime + each engine's own recent       |
//|  expectancy. It does not invent alpha; it ALLOCATES to proven     |
//|  alpha and de-allocates when an engine decays.                    |
//|                                                                   |
//|  ENGINES (both evidence-backed in this repo):                     |
//|   A. TREND  — Donchian-20 breakout + EMA-50 + ADX≥18, ATR-2 stop, |
//|      3-ATR chandelier, COT gate (cot_study: gate lifts mean PF    |
//|      0.98→1.22-1.28 across 9 pairs).                              |
//|   B. TSMOM  — 1/3/6/12-month sign-vote ensemble, weekly cadence,  |
//|      vol-targeted (validate_tsmom.py: gold Sharpe 0.39 IS /       |
//|      0.40 OOS; metals only).                                      |
//|                                                                   |
//|  ALLOCATION LOGIC (the institutional part):                       |
//|   • Regime gate: shock regime (ATR percentile > 97%) ⇒ ALL OFF.   |
//|     ADX/ER decide whether TREND may fire; |votes|≥3 for TSMOM.    |
//|   • Performance feedback: each engine's trailing 15-trade         |
//|     expectancy (R) sets its risk weight in [0.25 … 1.25]. An      |
//|     engine that stops working bleeds allocation instead of the    |
//|     account. Weights are floored, never zeroed — engines keep     |
//|     trading small so the estimate can recover (anti-whipsaw).     |
//|   • Shared risk budget: combined open risk ≤ InpMaxHeatPct of     |
//|     equity. Daily/weekly breakers above everything.               |
//|                                                                   |
//|  Run on XAUUSD D1/H4 first (where the evidence is). FX majors:    |
//|  evidence says expect little. Demo before money, always.          |
//+------------------------------------------------------------------+
#property copyright "2026 Fable — PRIVATE series"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>
#include "FableCOT.mqh"

input group "═══ Budget ═══"
input double InpBaseRiskPct   = 0.4;    // Base risk per trade (% equity)
input double InpMaxHeatPct    = 2.0;    // Max combined open risk (% equity)
input double InpDailyLossPct  = 3.0;    // Daily breaker
input double InpWeeklyLossPct = 6.0;    // Weekly breaker

input group "═══ Engine A: TREND ═══"
input bool   InpUseTrend      = true;
input int    InpDonchian      = 20;
input int    InpEMA           = 50;
input double InpADXMin        = 18.0;
input double InpSL_ATR        = 2.0;
input double InpTrail_ATR     = 3.0;
enum ENUM_RA_COT { RA_COT_OFF=0, RA_COT_EITHER=1 };
input ENUM_RA_COT InpCOTMode  = RA_COT_EITHER;

input group "═══ Engine B: TSMOM ═══"
input bool   InpUseTSMOM      = true;
input int    InpVoteMin       = 3;      // |votes| of 4 lookbacks required
input double InpTSMOM_SL_ATR  = 3.0;    // Catastrophe stop
input int    InpRebalDow      = 1;      // Weekly action day (1=Mon)

input group "═══ Regime & feedback ═══"
input int    InpATRPeriod     = 14;
input int    InpVolWindow     = 100;
input double InpShockPct      = 0.97;   // Vol percentile above = shock, all off
input double InpER_TrendMin   = 0.25;   // Min ER for TREND engine
input int    InpFeedbackN     = 15;     // Trades for expectancy feedback
input double InpWeightFloor   = 0.25;   // Min engine weight
input double InpWeightCap     = 1.25;   // Max engine weight

input group "═══ Misc ═══"
input long   InpMagicBase     = 484801; // Engine A = this, Engine B = this+1
input int    InpSlippagePts   = 30;
input bool   InpHUD           = true;

CTrade trade;
int hATR=INVALID_HANDLE, hADX=INVALID_HANDLE, hEMA=INVALID_HANDLE;
datetime g_lastBar=0;
double g_dayEq=0, g_weekEq=0;
datetime g_dayStamp=0, g_weekStamp=0;
bool g_haltDay=false, g_haltWeek=false;

// engine telemetry
double g_R_A[64]; int g_nA=0; double g_wA=1.0;
double g_R_B[64]; int g_nB=0; double g_wB=1.0;
// open-trade tracking for R attribution
double g_entryA=0, g_riskA=0; int g_dirA=0;
double g_entryB=0, g_riskB=0; int g_dirB=0;
string g_regime="init";

long MagicA(){ return InpMagicBase; }
long MagicB(){ return InpMagicBase+1; }

int OnInit()
{
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   hATR=iATR(_Symbol,_Period,InpATRPeriod);
   hADX=iADX(_Symbol,_Period,14);
   hEMA=iMA(_Symbol,_Period,InpEMA,0,MODE_EMA,PRICE_CLOSE);
   if(hATR==INVALID_HANDLE||hADX==INVALID_HANDLE||hEMA==INVALID_HANDLE) return INIT_FAILED;
   g_dayEq=AccountInfoDouble(ACCOUNT_EQUITY); g_weekEq=g_dayEq;
   g_dayStamp=TimeCurrent()-(TimeCurrent()%86400);
   g_weekStamp=WeekStart(TimeCurrent());
   ArrayInitialize(g_R_A,0); ArrayInitialize(g_R_B,0);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int r)
{
   if(hATR!=INVALID_HANDLE) IndicatorRelease(hATR);
   if(hADX!=INVALID_HANDLE) IndicatorRelease(hADX);
   if(hEMA!=INVALID_HANDLE) IndicatorRelease(hEMA);
   Comment("");
}

void OnTick()
{
   ManageTrails();
   RollBreakers();
   datetime cur=iTime(_Symbol,_Period,0);
   if(cur==g_lastBar){ if(InpHUD) Dash(); return; }
   g_lastBar=cur;

   // ---- regime ----
   double atr[], adx[], ema[], cl[], hi[], lo[], op[];
   ArraySetAsSeries(atr,true); ArraySetAsSeries(adx,true); ArraySetAsSeries(ema,true);
   ArraySetAsSeries(cl,true); ArraySetAsSeries(hi,true); ArraySetAsSeries(lo,true); ArraySetAsSeries(op,true);
   int need=MathMax(InpVolWindow+5, MathMax(InpDonchian+5, InpEMA+5));
   if(CopyBuffer(hATR,0,0,need,atr)<need) return;
   if(CopyBuffer(hADX,0,0,3,adx)<3) return;
   if(CopyBuffer(hEMA,0,0,3,ema)<3) return;
   if(CopyClose(_Symbol,_Period,0,need,cl)<need) return;
   if(CopyHigh(_Symbol,_Period,0,need,hi)<need) return;
   if(CopyLow(_Symbol,_Period,0,need,lo)<need) return;
   if(CopyOpen(_Symbol,_Period,0,need,op)<need) return;
   int s=1;
   double atrV=atr[s]; if(atrV<=0) return;

   int le=0; for(int k=s;k<s+InpVolWindow&&k<need;k++) if(atr[k]<=atr[s]) le++;
   double volPct=(double)le/InpVolWindow;
   double er=ER(cl,s,20);

   bool shock = volPct>InpShockPct;
   bool trendRegime = !shock && adx[s]>=InpADXMin && er>=InpER_TrendMin;
   g_regime = shock ? "SHOCK (all off)" : StringFormat("vol%%=%.2f ER=%.2f ADX=%.0f %s",
              volPct, er, adx[s], trendRegime?"TREND-OK":"chop");

   if(g_haltDay||g_haltWeek||shock){ if(InpHUD) Dash(); return; }

   // ---- engine weights from trailing expectancy ----
   g_wA = WeightFrom(g_R_A, g_nA);
   g_wB = WeightFrom(g_R_B, g_nB);

   // ---- Engine A: TREND ----
   if(InpUseTrend && trendRegime && !HasPos(MagicA()) && HeatOK())
   {
      double donHi=hi[s+1], donLo=lo[s+1];
      for(int k=s+1;k<=s+InpDonchian&&k<need;k++){ donHi=MathMax(donHi,hi[k]); donLo=MathMin(donLo,lo[k]); }
      int want=0;
      if(cl[s]>donHi && cl[s]>ema[s] && cl[s]>op[s]) want=1;
      else if(cl[s]<donLo && cl[s]<ema[s] && cl[s]<op[s]) want=-1;
      if(want!=0 && COTAllows(want))
         OpenEngine('A', want, atrV, InpSL_ATR, g_wA);
   }

   // ---- Engine B: TSMOM (weekly cadence, daily data via _Period if D1; else uses D1 series) ----
   if(InpUseTSMOM)
   {
      MqlDateTime mt; TimeToStruct(TimeCurrent(),mt);
      int votes=TSMOMVotes();
      int wantB=(MathAbs(votes)>=InpVoteMin)?(votes>0?1:-1):0;
      bool hasB=HasPos(MagicB());
      if(hasB && wantB==0) CloseEngine(MagicB(),"votes lost");
      else if(hasB)
      {
         int dirB=PosDir(MagicB());
         if(dirB!=0 && dirB!=wantB) { CloseEngine(MagicB(),"vote flip"); hasB=false; }
      }
      if(!hasB && wantB!=0 && mt.day_of_week==InpRebalDow && HeatOK())
      {
         double atrD=DailyATR();
         if(atrD>0) OpenEngine('B', wantB, atrD, InpTSMOM_SL_ATR, g_wB);
      }
   }
   if(InpHUD) Dash();
}

//------------------------------------------------------------------
int TSMOMVotes()
{
   double cl[];
   ArraySetAsSeries(cl,true);
   if(CopyClose(_Symbol,PERIOD_D1,0,260,cl)<260) return 0;
   int v=0; int looks[4]={21,63,126,252};
   for(int i=0;i<4;i++) v += (cl[1]>cl[1+looks[i]]) ? 1 : ((cl[1]<cl[1+looks[i]])?-1:0);
   return v;
}

double DailyATR()
{
   int h=iATR(_Symbol,PERIOD_D1,20);
   double a[1];
   if(h==INVALID_HANDLE||CopyBuffer(h,0,1,1,a)!=1) return 0;
   return a[0];
}

double ER(const double &cl[], int s, int n)
{
   if(s+n>=ArraySize(cl)) return 0;
   double net=MathAbs(cl[s]-cl[s+n]), sum=0;
   for(int k=s;k<s+n;k++) sum+=MathAbs(cl[k]-cl[k+1]);
   return sum>0?net/sum:0;
}

bool COTAllows(int dir)
{
   if(InpCOTMode==RA_COT_OFF) return true;
   int b=FableCOT_Bias(_Symbol,TimeCurrent());
   int idx=FableCOT_Index(_Symbol,TimeCurrent());
   bool signok=(b==dir)||(b==0);
   bool idxok=(dir>0)?(idx>=45):(idx<=55);
   return signok||idxok;
}

double WeightFrom(const double &arr[], int n)
{
   int m=MathMin(InpFeedbackN,n);
   if(m<6) return 1.0;
   double s=0;
   for(int i=n-m;i<n;i++) s+=arr[i];
   double e=s/m;  // expectancy in R
   double w=1.0+e;               // -0.5R exp -> 0.5 weight; +0.25R -> 1.25
   return MathMax(InpWeightFloor, MathMin(InpWeightCap, w));
}

void RecordR(char engine, double r)
{
   if(engine=='A'){ if(g_nA<64) g_R_A[g_nA++]=r; else { for(int i=0;i<63;i++) g_R_A[i]=g_R_A[i+1]; g_R_A[63]=r; } }
   else           { if(g_nB<64) g_R_B[g_nB++]=r; else { for(int i=0;i<63;i++) g_R_B[i]=g_R_B[i+1]; g_R_B[63]=r; } }
}

//------------------------------------------------------------------
void OpenEngine(char engine, int dir, double atrV, double slMult, double weight)
{
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK), bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entry=(dir>0)?ask:bid;
   double slDist=slMult*atrV;
   double sl=entry-dir*slDist;
   double riskPct=InpBaseRiskPct*weight;
   double lots=CalcLots(slDist,riskPct);
   if(lots<=0) return;
   trade.SetExpertMagicNumber(engine=='A'?MagicA():MagicB());
   bool ok=(dir>0)?trade.Buy(lots,_Symbol,0,NormalizeDouble(sl,_Digits),0,engine=='A'?"RA-Trend":"RA-TSMOM")
                  :trade.Sell(lots,_Symbol,0,NormalizeDouble(sl,_Digits),0,engine=='A'?"RA-Trend":"RA-TSMOM");
   if(ok)
   {
      if(engine=='A'){ g_entryA=entry; g_riskA=slDist; g_dirA=dir; }
      else           { g_entryB=entry; g_riskB=slDist; g_dirB=dir; }
      PrintFormat("ALLOC %c %s %.2f lots w=%.2f risk=%.2f%%", engine, dir>0?"BUY":"SELL", lots, weight, riskPct);
   }
}

void CloseEngine(long magic, string why)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=magic) continue;
      trade.PositionClose(tk);
   }
   Print("CLOSE engine magic ",magic," (",why,")");
}

void ManageTrails()
{
   // chandelier for engine A only (TSMOM uses catastrophe stop + weekly logic)
   double atr[1];
   if(CopyBuffer(hATR,0,1,1,atr)<1||atr[0]<=0) return;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=MagicA()) continue;
      long type=PositionGetInteger(POSITION_TYPE);
      int dir=(type==POSITION_TYPE_BUY)?1:-1;
      double cur=(dir>0)?SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      double sl=PositionGetDouble(POSITION_SL);
      double trail=(dir>0)?cur-InpTrail_ATR*atr[0]:cur+InpTrail_ATR*atr[0];
      bool better=(dir>0)?(trail>sl):(sl==0||trail<sl);
      if(better) trade.PositionModify(tk,NormalizeDouble(trail,_Digits),PositionGetDouble(POSITION_TP));
   }
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD) return;
   if(trans.symbol!=_Symbol) return;
   ulong deal=trans.deal;
   if(deal==0||!HistoryDealSelect(deal)) return;
   long mg=(long)HistoryDealGetInteger(deal,DEAL_MAGIC);
   if(mg!=MagicA() && mg!=MagicB()) return;
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_OUT) return;
   double px=HistoryDealGetDouble(deal,DEAL_PRICE);
   if(mg==MagicA() && g_dirA!=0 && g_riskA>0)
   { RecordR('A', g_dirA*(px-g_entryA)/g_riskA); g_dirA=0; }
   if(mg==MagicB() && g_dirB!=0 && g_riskB>0)
   { RecordR('B', g_dirB*(px-g_entryB)/g_riskB); g_dirB=0; }
}

//------------------------------------------------------------------
bool HasPos(long magic)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol && PositionGetInteger(POSITION_MAGIC)==magic)
         return true;
   }
   return false;
}

int PosDir(long magic)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol && PositionGetInteger(POSITION_MAGIC)==magic)
         return (PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY)?1:-1;
   }
   return 0;
}

bool HeatOK()
{
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq<=0) return false;
   double heat=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      long mg=(long)PositionGetInteger(POSITION_MAGIC);
      if(mg!=MagicA()&&mg!=MagicB()) continue;
      double op=PositionGetDouble(POSITION_PRICE_OPEN);
      double sl=PositionGetDouble(POSITION_SL);
      if(sl<=0) continue;
      double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
      double tickVal=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
      if(tickSize<=0||tickVal<=0) continue;
      heat+=MathAbs(op-sl)/tickSize*tickVal*PositionGetDouble(POSITION_VOLUME);
   }
   return heat < eq*InpMaxHeatPct/100.0;
}

double CalcLots(double slDist,double riskPct)
{
   double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickVal=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0||tickVal<=0||slDist<=0) return 0;
   double money=AccountInfoDouble(ACCOUNT_EQUITY)*riskPct/100.0;
   double lossPerLot=slDist/tickSize*tickVal;
   if(lossPerLot<=0) return 0;
   double lots=money/lossPerLot;
   double mn=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double mx=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double st=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(st>0) lots=MathFloor(lots/st)*st;
   lots=MathMax(mn,MathMin(mx,lots));
   if(lots*lossPerLot>AccountInfoDouble(ACCOUNT_EQUITY)*0.1) return 0;
   return lots;
}

datetime WeekStart(datetime t)
{
   MqlDateTime mt; TimeToStruct(t,mt);
   int dow=mt.day_of_week==0?7:mt.day_of_week;
   return (t-(t%86400))-(dow-1)*86400;
}

void RollBreakers()
{
   datetime now=TimeCurrent();
   datetime d=now-(now%86400);
   if(d!=g_dayStamp){ g_dayStamp=d; g_dayEq=AccountInfoDouble(ACCOUNT_EQUITY); g_haltDay=false; }
   datetime w=WeekStart(now);
   if(w!=g_weekStamp){ g_weekStamp=w; g_weekEq=AccountInfoDouble(ACCOUNT_EQUITY); g_haltWeek=false; }
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   if(InpDailyLossPct>0&&g_dayEq>0&&eq<=g_dayEq*(1.0-InpDailyLossPct/100.0)) g_haltDay=true;
   if(InpWeeklyLossPct>0&&g_weekEq>0&&eq<=g_weekEq*(1.0-InpWeeklyLossPct/100.0)) g_haltWeek=true;
}

void Dash()
{
   double eA=0,eB=0; int mA=MathMin(InpFeedbackN,g_nA), mB=MathMin(InpFeedbackN,g_nB);
   for(int i=g_nA-mA;i<g_nA;i++) eA+=g_R_A[i]; if(mA>0) eA/=mA;
   for(int i=g_nB-mB;i<g_nB;i++) eB+=g_R_B[i]; if(mB>0) eB/=mB;
   Comment(StringFormat(
      "FABLE RegimeAllocator [%s]\nregime: %s\nEngine A TREND: w=%.2f exp=%.2fR (n=%d) %s\nEngine B TSMOM: w=%.2f exp=%.2fR (n=%d) %s\nhaltD %d haltW %d",
      _Symbol, g_regime, g_wA, eA, g_nA, HasPos(MagicA())?"[POS]":"",
      g_wB, eB, g_nB, HasPos(MagicB())?"[POS]":"", g_haltDay?1:0, g_haltWeek?1:0));
}
//+------------------------------------------------------------------+
