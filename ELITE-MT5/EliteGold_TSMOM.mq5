//+------------------------------------------------------------------+
//|                                          EliteGold_TSMOM.mq5      |
//|  Time-Series Momentum ensemble on METALS with volatility          |
//|  targeting, COT-index tilt and a drawdown governor.               |
//|                                                                   |
//|  EVIDENCE (RESEARCH-2026-06-10/backtests/validate_tsmom.py):      |
//|    XAUUSD weekly TSMOM ensemble (1/3/6/12m votes, 10% vol target, |
//|    costs charged): Sharpe 0.39 in-sample (2004-2017) and 0.40     |
//|    out-of-sample (2018-2025) — the effect is present and STABLE   |
//|    on gold. +COT-index tilt: Sharpe 0.41, maxDD -34%→-32%.        |
//|    The SAME rule on EURUSD/GBPUSD/USDJPY does NOT survive OOS —   |
//|    so this EA is for XAUUSD (and XAGUSD with reduced size), not   |
//|    FX. Academic basis: Moskowitz, Ooi & Pedersen (2012).          |
//|                                                                   |
//|  Position logic (weekly cadence, daily bars):                     |
//|    vote = sign(21d ret)+sign(63d)+sign(126d)+sign(252d)           |
//|    |votes|>=2 required; size = equity × volTarget / realizedVol;  |
//|    catastrophe stop 3×ATR(20); flip/flat on vote loss;            |
//|    COT-index tilt halves size against extreme spec positioning;   |
//|    governor halves size in a >10% strategy drawdown.              |
//|                                                                   |
//|  Honest expectations: Sharpe ~0.4 ≈ years with -25% drawdowns.    |
//|  Size accordingly. This is a slow, robust harvest — not a money   |
//|  printer. Backtest with real ticks, then demo, then small.        |
//+------------------------------------------------------------------+
#property copyright "2026 Fable — Elite series"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>
#include "FableCOT.mqh"

input group "════ Core ════"
input double InpVolTargetAnn   = 10.0;   // Target annualized vol (% of equity)
input int    InpVoteMin        = 2;      // Min |ensemble votes| to hold (1..4)
input double InpMaxLeverage    = 3.0;    // Cap on notional/equity
input double InpCatStopATR     = 3.0;    // Catastrophe stop ATR(20) mult
input int    InpRebalanceDow   = 1;      // Rebalance day (1=Mon..5=Fri)
input double InpMinRebalPct    = 15.0;   // Min position change % to act (reduces churn)

input group "════ COT tilt ════"
enum ENUM_TILT { TILT_OFF=0, TILT_ON=1 };
input ENUM_TILT InpCOTTilt     = TILT_ON;   // Halve size vs extreme opposing positioning
input int    InpIdxLow        = 45;      // Long side scaled down if COT-idx below
input int    InpIdxHigh       = 55;      // Short side scaled down if COT-idx above

input group "════ Governor & safety ════"
input double InpGovernorDD     = 10.0;   // Strategy DD% to halve size (0=off)
input double InpDailyLossPct   = 4.0;    // Daily equity loss halt (%)
input double InpMaxSpreadATR   = 0.15;   // Spread gate (× daily ATR)

input group "════ Misc ════"
input long   InpMagic          = 482601;
input int    InpSlippagePts    = 30;
input string InpComment        = "EliteTSMOM";
input bool   InpDashboard      = true;

CTrade trade;
CPositionInfo pos;
int hATR=INVALID_HANDLE;
datetime g_lastDay=0;
double g_hwm=0;            // strategy high-water mark (equity)
double g_dayStartEq=0; datetime g_dayStamp=0; bool g_halted=false;
string g_status="init";

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   hATR = iATR(_Symbol, PERIOD_D1, 20);
   if(hATR==INVALID_HANDLE) return INIT_FAILED;
   g_hwm = AccountInfoDouble(ACCOUNT_EQUITY);
   g_dayStartEq = g_hwm; g_dayStamp = TimeCurrent() - (TimeCurrent()%86400);
   string b,q;
   if(!FableCOT_MapSymbol(_Symbol,b,q) || (b!="GOLD" && b!="SILVER"))
      Print("WARNING: EliteGold_TSMOM is validated for XAUUSD/XAGUSD. '",_Symbol,"' maps to ",b,
            " — the documented evidence does NOT cover it.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason){ if(hATR!=INVALID_HANDLE) IndicatorRelease(hATR); Comment(""); }

void OnTick()
{
   RollDay();
   ManageCatStop();

   datetime today = iTime(_Symbol, PERIOD_D1, 0);
   if(today==g_lastDay){ if(InpDashboard) Dash(); return; }
   g_lastDay = today;

   MqlDateTime mt; TimeToStruct(today, mt);
   bool rebalDay = (mt.day_of_week==InpRebalanceDow);
   int vote = Votes();
   int want = (MathAbs(vote)>=InpVoteMin) ? (vote>0?1:-1) : 0;

   // exit immediately on signal loss any day; enter/resize only on rebalance day
   double curLots = NetLots();
   if(want==0 && curLots!=0.0){ CloseAll("vote lost"); if(InpDashboard) Dash(); return; }
   if(!rebalDay && ((want>0&&curLots>0)||(want<0&&curLots<0)||(want==0&&curLots==0)))
   { if(InpDashboard) Dash(); return; }
   if(g_halted){ if(InpDashboard) Dash(); return; }

   double targetLots = TargetLots(want);
   Rebalance(want, targetLots);
   if(InpDashboard) Dash();
}

int Votes()
{
   double cl[]; ArraySetAsSeries(cl,true);
   if(CopyClose(_Symbol, PERIOD_D1, 0, 260, cl)<260) return 0;
   int v=0;
   int looks[4]={21,63,126,252};
   for(int i=0;i<4;i++)
   {
      double r = cl[1]-cl[1+looks[i]];
      v += (r>0) ? 1 : (r<0 ? -1 : 0);
   }
   return v;
}

double RealizedVolAnn()
{
   double cl[]; ArraySetAsSeries(cl,true);
   if(CopyClose(_Symbol, PERIOD_D1, 0, 23, cl)<23) return 0;
   double sum=0, sum2=0; int n=0;
   for(int i=1;i<=21;i++)
   {
      if(cl[i+1]<=0) continue;
      double r = MathLog(cl[i]/cl[i+1]);
      sum+=r; sum2+=r*r; n++;
   }
   if(n<10) return 0;
   double var = (sum2 - sum*sum/n)/(n-1);
   return MathSqrt(MathMax(var,0)) * MathSqrt(252.0);
}

double TargetLots(int dir)
{
   if(dir==0) return 0;
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double vol = RealizedVolAnn();
   if(vol<=0.01) return 0;
   double targetNotional = eq * (InpVolTargetAnn/100.0) / vol;
   targetNotional = MathMin(targetNotional, eq*InpMaxLeverage);

   // COT tilt
   if(InpCOTTilt==TILT_ON)
   {
      int idx = FableCOT_Index(_Symbol, TimeCurrent());
      if(dir>0 && idx<InpIdxLow)  targetNotional*=0.5;
      if(dir<0 && idx>InpIdxHigh) targetNotional*=0.5;
   }
   // drawdown governor
   double eqNow = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eqNow>g_hwm) g_hwm=eqNow;
   if(InpGovernorDD>0 && g_hwm>0 && eqNow < g_hwm*(1.0-InpGovernorDD/100.0))
      targetNotional*=0.5;

   double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(price<=0||contract<=0) return 0;
   double lots = targetNotional/(price*contract);
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step>0) lots=MathFloor(lots/step)*step;
   return MathMax(0.0, MathMin(lots, maxLot)) >= minLot ? MathMin(MathMax(lots,minLot),maxLot) : 0.0;
}

double NetLots()
{
   double net=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      double v=PositionGetDouble(POSITION_VOLUME);
      net += (PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY) ? v : -v;
   }
   return net;
}

void CloseAll(string why)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      trade.PositionClose(tk);
   }
   Print("FLAT (",why,")");
}

void Rebalance(int dir, double target)
{
   double cur = NetLots();
   double want = dir*target;
   double diff = want - cur;
   double price=SymbolInfoDouble(_Symbol,SYMBOL_BID);

   // spread gate
   double atrD[1];
   if(CopyBuffer(hATR,0,1,1,atrD)==1 && atrD[0]>0 && InpMaxSpreadATR>0)
   {
      double spr=SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID);
      if(spr > InpMaxSpreadATR*atrD[0]) { g_status="spread gate"; return; }
   }
   if(MathAbs(cur)>0 && MathAbs(diff) < MathAbs(want)*InpMinRebalPct/100.0) return; // churn guard
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   if(MathAbs(diff)<minLot) return;

   if(cur!=0 && (dir==0 || (cur>0)!=(want>0))) { CloseAll("flip"); cur=0; diff=want; }

   if(diff>0)      trade.Buy(NormalizeLots(diff), _Symbol, 0,0,0, InpComment);
   else if(diff<0) trade.Sell(NormalizeLots(-diff), _Symbol, 0,0,0, InpComment);
   PrintFormat("REBALANCE dir=%d target=%.2f cur=%.2f", dir, want, cur);
}

double NormalizeLots(double v)
{
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   if(step>0) v=MathFloor(v/step)*step;
   return MathMax(minLot, MathMin(maxLot, v));
}

void ManageCatStop()
{
   double net=NetLots();
   if(net==0) return;
   double atrD[1];
   if(CopyBuffer(hATR,0,1,1,atrD)<1 || atrD[0]<=0) return;
   double price=SymbolInfoDouble(_Symbol,SYMBOL_BID);

   // average entry of own positions
   double sumV=0,sumPV=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      double v=PositionGetDouble(POSITION_VOLUME);
      sumV+=v; sumPV+=v*PositionGetDouble(POSITION_PRICE_OPEN);
   }
   if(sumV<=0) return;
   double avg=sumPV/sumV;
   if(net>0 && price < avg - InpCatStopATR*atrD[0]) CloseAll("catastrophe stop");
   if(net<0 && price > avg + InpCatStopATR*atrD[0]) CloseAll("catastrophe stop");
}

void RollDay()
{
   datetime now=TimeCurrent();
   datetime d = now-(now%86400);
   if(d!=g_dayStamp){ g_dayStamp=d; g_dayStartEq=AccountInfoDouble(ACCOUNT_EQUITY); g_halted=false; }
   if(InpDailyLossPct>0 && g_dayStartEq>0 &&
      AccountInfoDouble(ACCOUNT_EQUITY) <= g_dayStartEq*(1.0-InpDailyLossPct/100.0))
      g_halted=true;
}

void Dash()
{
   int idx = FableCOT_Index(_Symbol, TimeCurrent());
   Comment(StringFormat("EliteGold TSMOM [%s]\nvotes=%d net=%.2f lots vol=%.1f%% cotIdx=%d halted=%d\n%s",
           _Symbol, Votes(), NetLots(), 100*RealizedVolAnn(), idx, g_halted?1:0, g_status));
}
//+------------------------------------------------------------------+
