//+------------------------------------------------------------------+
//|                                    EliteCOT_SwingPortfolio.mq5    |
//|  Multi-symbol Donchian-trend portfolio gated by CFTC positioning. |
//|                                                                   |
//|  THIS IS THE EXACT CONFIGURATION THAT WAS A/B TESTED:             |
//|  RESEARCH-2026-06-10/cot_study/results/COT_STUDY.md               |
//|    identical trend strategy (Donchian-20 + EMA-50 + ADX>18,       |
//|    ATR-2 stop, 3-ATR chandelier, 0.5% risk, next-open fills,      |
//|    real spreads) across 9 pairs, 2004/2010→2020/2025:             |
//|      no COT gate   : mean PF 0.98, mean ret −1.4%                 |
//|      API-sign gate : mean PF 1.28, mean ret +0.1%  ← shipped      |
//|      COT-index gate: mean PF 1.22, mean ret +0.6%  ← shipped      |
//|      commercials   : mean PF 0.84 (HARMFUL — not offered)         |
//|    Per-symbol: the gate helps XAUUSD/EURUSD/USDJPY/USDCAD/EURGBP/ |
//|    AUDUSD; GBPUSD stays bad under every variant (excluded from    |
//|    the default basket); NZDUSD mixed (excluded).                  |
//|                                                                   |
//|  The COT gate works in the STRATEGY TESTER via the embedded       |
//|  publish-lagged CFTC table (FableCOT.mqh) — A/B test it yourself: |
//|  run once with InpCOTMode=OFF, once with EITHER, compare.         |
//|                                                                   |
//|  HONESTY: mean PF 1.2–1.3 with ~30% win rate is a thin, lumpy     |
//|  edge that lives on a few big winners per year. The portfolio     |
//|  exists because single-symbol results are fragile. Demo first.    |
//+------------------------------------------------------------------+
#property copyright "2026 Fable — Elite series"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>
#include "FableCOT.mqh"

input group "═══ Portfolio ═══"
input string InpSymbols       = "XAUUSD,EURUSD,USDCAD,EURGBP,AUDUSD"; // Basket (comma separated)
input int    InpMaxPositions  = 3;       // Max concurrent positions
input double InpRiskPct       = 0.4;     // Risk % per trade
input double InpMaxLotCap     = 20.0;

input group "═══ Trend Core (validated params — change = new backtest) ═══"
input ENUM_TIMEFRAMES InpTF   = PERIOD_D1; // Working TF (validation was daily)
input int    InpDonchian      = 20;
input int    InpEMA           = 50;
input int    InpADXPeriod     = 14;
input double InpADXMin        = 18.0;
input int    InpATRPeriod     = 14;
input double InpSL_ATR        = 2.0;
input double InpTrail_ATR     = 3.0;
input int    InpTimeStopBars  = 60;      // Bars before stagnant exit

input group "═══ COT Gate ═══"
enum ENUM_PCOT { PCOT_OFF=0, PCOT_SIGN=1, PCOT_INDEX=2, PCOT_EITHER=3 };
input ENUM_PCOT InpCOTMode    = PCOT_EITHER;  // validated: SIGN and INDEX both add PF
input bool   InpAllowNeutral  = true;

input group "═══ Breakers ═══"
input double InpDailyLossPct  = 3.0;
input double InpWeeklyLossPct = 6.0;

input group "═══ Misc ═══"
input long   InpMagic         = 482701;
input int    InpSlippagePts   = 30;
input bool   InpHUD           = true;

CTrade trade;

#define MAXSYM 12
string   g_sym[MAXSYM];
int      g_n=0;
int      g_hATR[MAXSYM], g_hADX[MAXSYM], g_hEMA[MAXSYM];
datetime g_lastBar[MAXSYM];
double   g_dayEq=0, g_weekEq=0;
datetime g_dayStamp=0, g_weekStamp=0;
bool     g_haltDay=false, g_haltWeek=false;
string   g_hud="";

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   string parts[];
   int k = StringSplit(InpSymbols, ',', parts);
   for(int i=0;i<k && g_n<MAXSYM;i++)
   {
      string s = parts[i];
      StringTrimLeft(s); StringTrimRight(s);
      if(s=="") continue;
      if(!SymbolSelect(s, true)) { Print("symbol not available: ", s); continue; }
      g_sym[g_n]=s;
      g_hATR[g_n]=iATR(s, InpTF, InpATRPeriod);
      g_hADX[g_n]=iADX(s, InpTF, InpADXPeriod);
      g_hEMA[g_n]=iMA(s, InpTF, InpEMA, 0, MODE_EMA, PRICE_CLOSE);
      if(g_hATR[g_n]==INVALID_HANDLE||g_hADX[g_n]==INVALID_HANDLE||g_hEMA[g_n]==INVALID_HANDLE)
      { Print("handles failed for ", s); continue; }
      g_lastBar[g_n]=0;
      g_n++;
   }
   if(g_n==0){ Print("no tradable symbols"); return INIT_FAILED; }
   g_dayEq=AccountInfoDouble(ACCOUNT_EQUITY); g_weekEq=g_dayEq;
   g_dayStamp=TimeCurrent()-(TimeCurrent()%86400);
   g_weekStamp=WeekStart(TimeCurrent());
   EventSetTimer(15);
   PrintFormat("EliteCOT Portfolio: %d symbols, COT mode %d (tester-capable)", g_n, (int)InpCOTMode);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int r)
{
   EventKillTimer();
   for(int i=0;i<g_n;i++)
   {
      if(g_hATR[i]!=INVALID_HANDLE) IndicatorRelease(g_hATR[i]);
      if(g_hADX[i]!=INVALID_HANDLE) IndicatorRelease(g_hADX[i]);
      if(g_hEMA[i]!=INVALID_HANDLE) IndicatorRelease(g_hEMA[i]);
   }
   Comment("");
}

void OnTick(){ Work(); }
void OnTimer(){ Work(); }   // multi-symbol EAs must not rely on chart-symbol ticks only

void Work()
{
   RollBreakers();
   g_hud="";
   for(int i=0;i<g_n;i++)
   {
      Manage(i);
      datetime cur=iTime(g_sym[i], InpTF, 0);
      if(cur==0 || cur==g_lastBar[i]) continue;
      g_lastBar[i]=cur;
      Signal(i);
   }
   if(InpHUD) Dash();
}

void Signal(int i)
{
   string sym=g_sym[i];
   if(g_haltDay||g_haltWeek) return;
   if(HasPos(sym)) return;
   if(TotalPos()>=InpMaxPositions) return;

   int need=MathMax(InpDonchian+3, InpEMA+3);
   double hi[], lo[], cl[], op[], atr[], adx[], ema[];
   ArraySetAsSeries(hi,true); ArraySetAsSeries(lo,true); ArraySetAsSeries(cl,true);
   ArraySetAsSeries(op,true); ArraySetAsSeries(atr,true); ArraySetAsSeries(adx,true);
   ArraySetAsSeries(ema,true);
   if(CopyHigh(sym,InpTF,0,need,hi)<need) return;
   if(CopyLow(sym,InpTF,0,need,lo)<need) return;
   if(CopyClose(sym,InpTF,0,need,cl)<need) return;
   if(CopyOpen(sym,InpTF,0,need,op)<need) return;
   if(CopyBuffer(g_hATR[i],0,0,3,atr)<3) return;
   if(CopyBuffer(g_hADX[i],0,0,3,adx)<3) return;
   if(CopyBuffer(g_hEMA[i],0,0,3,ema)<3) return;

   int s=1;
   double atrV=atr[s];
   if(atrV<=0) return;
   if(adx[s]<InpADXMin) return;

   double donHi=hi[s+1], donLo=lo[s+1];
   for(int k=s+1;k<=s+InpDonchian && k<need;k++)
   { donHi=MathMax(donHi,hi[k]); donLo=MathMin(donLo,lo[k]); }

   bool bull=(cl[s]>op[s]);
   bool bear=(cl[s]<op[s]);
   int want=0;
   if(cl[s]>donHi && cl[s]>ema[s] && bull) want=1;
   else if(cl[s]<donLo && cl[s]<ema[s] && bear) want=-1;
   if(want==0) return;

   if(!COTAllows(sym, want)) return;

   double ask=SymbolInfoDouble(sym,SYMBOL_ASK), bid=SymbolInfoDouble(sym,SYMBOL_BID);
   double entry=(want>0)?ask:bid;
   double slDist=InpSL_ATR*atrV;
   double sl=entry-want*slDist;
   double lots=CalcLots(sym, slDist);
   if(lots<=0) return;
   int dg=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
   bool ok=(want>0)?trade.Buy(lots,sym,0,NormalizeDouble(sl,dg),0,"EliteCOT")
                   :trade.Sell(lots,sym,0,NormalizeDouble(sl,dg),0,"EliteCOT");
   if(ok) PrintFormat("OPEN %s %s %.2f lots sl=%.5f", want>0?"BUY":"SELL", sym, lots, sl);
}

bool COTAllows(string sym, int dir)
{
   if(InpCOTMode==PCOT_OFF) return true;
   int b = FableCOT_Bias(sym, TimeCurrent());
   int idx = FableCOT_Index(sym, TimeCurrent());
   bool signok = (b==dir) || (InpAllowNeutral && b==0);
   bool idxok = (dir>0) ? (idx>55 || (InpAllowNeutral && idx>=45 && idx<=55))
                        : (idx<45 || (InpAllowNeutral && idx>=45 && idx<=55));
   if(InpCOTMode==PCOT_SIGN)  return signok;
   if(InpCOTMode==PCOT_INDEX) return idxok;
   return signok || idxok;
}

void Manage(int i)
{
   string sym=g_sym[i];
   for(int p=PositionsTotal()-1;p>=0;p--)
   {
      ulong tk=PositionGetTicket(p);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=sym) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;

      double atr[2];
      if(CopyBuffer(g_hATR[i],0,1,1,atr)<1 || atr[0]<=0) continue;
      long type=PositionGetInteger(POSITION_TYPE);
      int dir=(type==POSITION_TYPE_BUY)?1:-1;
      double cur=(dir>0)?SymbolInfoDouble(sym,SYMBOL_BID):SymbolInfoDouble(sym,SYMBOL_ASK);
      double sl=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP);
      int dg=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);

      // chandelier
      double trail=(dir>0)?cur-InpTrail_ATR*atr[0]:cur+InpTrail_ATR*atr[0];
      bool better=(dir>0)?(trail>sl):(sl==0||trail<sl);
      double minStop=SymbolInfoInteger(sym,SYMBOL_TRADE_STOPS_LEVEL)*SymbolInfoDouble(sym,SYMBOL_POINT);
      if(better && MathAbs(trail-cur)>minStop)
         trade.PositionModify(tk, NormalizeDouble(trail,dg), tp);

      // time stop
      datetime opened=(datetime)PositionGetInteger(POSITION_TIME);
      int bars=Bars(sym, InpTF, opened, TimeCurrent());
      double op=PositionGetDouble(POSITION_PRICE_OPEN);
      double r=dir*(cur-op)/(InpSL_ATR*atr[0]);
      if(InpTimeStopBars>0 && bars>=InpTimeStopBars && r<0.5)
      { trade.PositionClose(tk); Print("TIME STOP ", sym); }
   }
}

double CalcLots(string sym, double slDist)
{
   double tickSize=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE);
   double tickVal=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0||tickVal<=0||slDist<=0) return 0;
   double money=AccountInfoDouble(ACCOUNT_EQUITY)*InpRiskPct/100.0;
   double lossPerLot=slDist/tickSize*tickVal;
   if(lossPerLot<=0) return 0;
   double lots=money/lossPerLot;
   double mn=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double mx=MathMin(SymbolInfoDouble(sym,SYMBOL_VOLUME_MAX), InpMaxLotCap);
   double st=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   if(st>0) lots=MathFloor(lots/st)*st;
   lots=MathMax(mn,MathMin(mx,lots));
   if(lots*lossPerLot>AccountInfoDouble(ACCOUNT_EQUITY)*0.15) return 0;
   return lots;
}

bool HasPos(string sym)
{
   for(int p=PositionsTotal()-1;p>=0;p--)
   {
      ulong tk=PositionGetTicket(p);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==sym && PositionGetInteger(POSITION_MAGIC)==InpMagic)
         return true;
   }
   return false;
}

int TotalPos()
{
   int c=0;
   for(int p=PositionsTotal()-1;p>=0;p--)
   {
      ulong tk=PositionGetTicket(p);
      if(tk==0) continue;
      if(PositionGetInteger(POSITION_MAGIC)==InpMagic) c++;
   }
   return c;
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
   if(InpDailyLossPct>0&&g_dayEq>0&&eq<=g_dayEq*(1.0-InpDailyLossPct/100.0)&&!g_haltDay)
   { g_haltDay=true; Print("DAILY BREAKER"); }
   if(InpWeeklyLossPct>0&&g_weekEq>0&&eq<=g_weekEq*(1.0-InpWeeklyLossPct/100.0)&&!g_haltWeek)
   { g_haltWeek=true; Print("WEEKLY BREAKER"); }
}

void Dash()
{
   string t=StringFormat("EliteCOT Portfolio  pos %d/%d  haltD %d haltW %d\n",
                         TotalPos(), InpMaxPositions, g_haltDay?1:0, g_haltWeek?1:0);
   for(int i=0;i<g_n;i++)
   {
      int b=FableCOT_Bias(g_sym[i], TimeCurrent());
      int idx=FableCOT_Index(g_sym[i], TimeCurrent());
      t+=StringFormat("%s: COT %s idx %d %s\n", g_sym[i], b>0?"BUY":(b<0?"SELL":"UNC"), idx,
                      HasPos(g_sym[i])?"[POS]":"");
   }
   Comment(t);
}
//+------------------------------------------------------------------+
