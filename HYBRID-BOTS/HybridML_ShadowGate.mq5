//+------------------------------------------------------------------+
//|                                        HybridML_ShadowGate.mq5    |
//|  Online logistic-regression trade filter that must EARN the       |
//|  right to veto trades — by default it only shadows.               |
//|                                                                   |
//|  WHY SHADOW MODE IS THE DESIGN (evidence):                        |
//|   • Prior session: daily ML trade filter FAILED purged walk-      |
//|     forward (AUC≈0.49, ML_REPORT.md).                             |
//|   • This session: H1 next-bar online logistic nets ≈ −cost        |
//|     (validate_online_ml.py — gross expectancy ~0 on EURUSD/       |
//|     XAUUSD/USDJPY, 2015→2026, 70k+ bars each).                    |
//|   Conclusion: shipping an ML EA that CLAIMS edge would be fraud.  |
//|   What CAN be useful: an online learner that filters an already-  |
//|   validated strategy's entries, A/B-tested against itself on      |
//|   YOUR symbol, live, with both equity curves visible. If the      |
//|   gated curve dominates for months, you enable enforcement.       |
//|                                                                   |
//|  HOW IT WORKS:                                                    |
//|   • Core strategy: the validated Donchian+EMA+ADX trend entry     |
//|     (same as EliteCOT) — this is what actually trades.            |
//|   • ML gate: online logistic regression (SGD) on 12 features     |
//|     (lagged returns, ATR percentile, ER, hour encoding). Learns   |
//|     from every closed trade's R outcome. Strictly causal.         |
//|   • SHADOW (default): all signals trade; the gate's verdicts and  |
//|     both virtual equity curves are tracked and displayed.         |
//|   • ENFORCE: only when you flip InpEnforce after the shadow       |
//|     stats convince you. The EA itself shows you the evidence.     |
//+------------------------------------------------------------------+
#property copyright "2026 Fable — Hybrid series"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

input group "═══ Core strategy (validated trend) ═══"
input int    InpDonchian     = 20;
input int    InpEMA          = 50;
input double InpADXMin       = 18.0;
input double InpSL_ATR       = 2.0;
input double InpTrail_ATR    = 3.0;
input double InpRiskPct      = 0.4;

input group "═══ ML shadow gate ═══"
input bool   InpEnforce      = false;   // false = shadow only (default!)
input double InpGateThresh   = 0.45;    // veto if P(win) below this
input double InpLearnRate    = 0.02;
input int    InpWarmupTrades = 25;      // no enforcement before N learned trades

input group "═══ Safety ═══"
input double InpDailyLossPct = 3.0;
input long   InpMagic        = 483401;
input int    InpSlippagePts  = 30;
input bool   InpHUD          = true;

CTrade trade;
int hATR=INVALID_HANDLE,hADX=INVALID_HANDLE,hEMA=INVALID_HANDLE;
datetime g_lastBar=0;
double g_dayEq=0; datetime g_dayStamp=0; bool g_halt=false;

#define NFEAT 13
double g_w[NFEAT];          // logistic weights (last = bias)
int    g_learned=0;
// pending example: features at entry, filled with outcome at close
double g_pendX[NFEAT];
bool   g_pendActive=false;
double g_entry=0,g_risk=0; int g_dir=0;
double g_pendP=0.5;
// shadow A/B equity (in R units)
double g_eqAll=0, g_eqGated=0;
int    g_nAll=0, g_nGated=0, g_nVetoed=0;

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   hATR=iATR(_Symbol,_Period,14);
   hADX=iADX(_Symbol,_Period,14);
   hEMA=iMA(_Symbol,_Period,InpEMA,0,MODE_EMA,PRICE_CLOSE);
   if(hATR==INVALID_HANDLE||hADX==INVALID_HANDLE||hEMA==INVALID_HANDLE) return INIT_FAILED;
   ArrayInitialize(g_w,0.0);
   g_dayEq=AccountInfoDouble(ACCOUNT_EQUITY);
   g_dayStamp=TimeCurrent()-(TimeCurrent()%86400);
   Print("ML ShadowGate: enforce=",InpEnforce," (shadow mode collects evidence first)");
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
   Trail();
   RollDay();
   datetime cur=iTime(_Symbol,_Period,0);
   if(cur==g_lastBar){ if(InpHUD) Dash(); return; }
   g_lastBar=cur;
   if(g_halt||HasPos()){ if(InpHUD) Dash(); return; }

   int need=MathMax(InpDonchian+3,InpEMA+3); need=MathMax(need,80);
   double hi[],lo[],cl[],op[],atr[],adx[],ema[];
   ArraySetAsSeries(hi,true);ArraySetAsSeries(lo,true);ArraySetAsSeries(cl,true);
   ArraySetAsSeries(op,true);ArraySetAsSeries(atr,true);ArraySetAsSeries(adx,true);ArraySetAsSeries(ema,true);
   if(CopyHigh(_Symbol,_Period,0,need,hi)<need) return;
   if(CopyLow(_Symbol,_Period,0,need,lo)<need) return;
   if(CopyClose(_Symbol,_Period,0,need,cl)<need) return;
   if(CopyOpen(_Symbol,_Period,0,need,op)<need) return;
   if(CopyBuffer(hATR,0,0,need,atr)<need) return;
   if(CopyBuffer(hADX,0,0,3,adx)<3) return;
   if(CopyBuffer(hEMA,0,0,3,ema)<3) return;
   int s=1;
   double atrV=atr[s]; if(atrV<=0) return;
   if(adx[s]<InpADXMin){ if(InpHUD) Dash(); return; }

   double donHi=hi[s+1],donLo=lo[s+1];
   for(int k=s+1;k<=s+InpDonchian&&k<need;k++){donHi=MathMax(donHi,hi[k]);donLo=MathMin(donLo,lo[k]);}
   int want=0;
   if(cl[s]>donHi&&cl[s]>ema[s]&&cl[s]>op[s]) want=1;
   else if(cl[s]<donLo&&cl[s]<ema[s]&&cl[s]<op[s]) want=-1;
   if(want==0){ if(InpHUD) Dash(); return; }

   // ---- ML gate evaluation ----
   double x[NFEAT];
   BuildFeatures(cl,atr,adx,s,need,want,x);
   double p=Predict(x);
   bool gateSaysNo = (p<InpGateThresh);
   bool enforced = InpEnforce && g_learned>=InpWarmupTrades;

   if(gateSaysNo) g_nVetoed++;
   if(enforced && gateSaysNo)
   {
      Print("GATE VETO: P(win)=",DoubleToString(p,3)," < ",InpGateThresh);
      if(InpHUD) Dash();
      return;
   }

   // trade (core strategy)
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entry=(want>0)?ask:bid;
   double slDist=InpSL_ATR*atrV;
   double sl=entry-want*slDist;
   double lots=CalcLots(slDist);
   if(lots<=0) return;
   bool ok=(want>0)?trade.Buy(lots,_Symbol,0,NormalizeDouble(sl,_Digits),0,"MLShadow")
                   :trade.Sell(lots,_Symbol,0,NormalizeDouble(sl,_Digits),0,"MLShadow");
   if(ok)
   {
      ArrayCopy(g_pendX,x);
      g_pendActive=true; g_pendP=p;
      g_entry=entry; g_risk=slDist; g_dir=want;
   }
   if(InpHUD) Dash();
}

void BuildFeatures(const double &cl[],const double &atr[],const double &adx[],
                   int s,int need,int dir,double &x[])
{
   int lags[6]={1,2,3,6,12,24};
   for(int i=0;i<6;i++)
   {
      int k=lags[i];
      x[i]=(s+k<need && cl[s+k]>0)?MathLog(cl[s]/cl[s+k])/(0.001+atr[s]/cl[s]):0;
   }
   int le=0; int w=MathMin(60,need-s-1);
   for(int k=s;k<s+w;k++) if(atr[k]<=atr[s]) le++;
   x[6]=(double)le/w - 0.5;
   double net=MathAbs(cl[s]-cl[s+20]),sum=0;
   for(int k=s;k<s+20;k++) sum+=MathAbs(cl[k]-cl[k+1]);
   x[7]=(sum>0?net/sum:0)-0.3;
   x[8]=adx[s]/50.0-0.5;
   MqlDateTime mt; TimeToStruct(TimeCurrent(),mt);
   x[9]=MathSin(2*M_PI*mt.hour/24.0);
   x[10]=MathCos(2*M_PI*mt.hour/24.0);
   x[11]=dir;       // direction as a feature (learns long/short asymmetry)
   x[12]=1.0;       // bias
}

double Predict(const double &x[])
{
   double z=0;
   for(int i=0;i<NFEAT;i++) z+=g_w[i]*x[i];
   z=MathMax(-30.0,MathMin(30.0,z));
   return 1.0/(1.0+MathExp(-z));
}

void Learn(const double &x[],double label)
{
   double p=Predict(x);
   for(int i=0;i<NFEAT;i++) g_w[i]+=InpLearnRate*(label-p)*x[i];
   g_learned++;
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD) return;
   if(trans.symbol!=_Symbol) return;
   ulong deal=trans.deal;
   if(deal==0||!HistoryDealSelect(deal)) return;
   if((long)HistoryDealGetInteger(deal,DEAL_MAGIC)!=InpMagic) return;
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_OUT) return;
   if(!g_pendActive||g_dir==0||g_risk<=0) return;
   double px=HistoryDealGetDouble(deal,DEAL_PRICE);
   double r=g_dir*(px-g_entry)/g_risk;
   // learn
   Learn(g_pendX, r>0?1.0:0.0);
   // shadow A/B bookkeeping
   g_eqAll+=r; g_nAll++;
   if(g_pendP>=InpGateThresh){ g_eqGated+=r; g_nGated++; }
   g_pendActive=false; g_dir=0;
}

void Trail()
{
   double atr[1];
   if(CopyBuffer(hATR,0,1,1,atr)<1||atr[0]<=0) return;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      long type=PositionGetInteger(POSITION_TYPE);
      int dir=(type==POSITION_TYPE_BUY)?1:-1;
      double cur=(dir>0)?SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      double sl=PositionGetDouble(POSITION_SL);
      double trail=(dir>0)?cur-InpTrail_ATR*atr[0]:cur+InpTrail_ATR*atr[0];
      bool better=(dir>0)?(trail>sl):(sl==0||trail<sl);
      if(better) trade.PositionModify(tk,NormalizeDouble(trail,_Digits),PositionGetDouble(POSITION_TP));
   }
}

bool HasPos()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol&&PositionGetInteger(POSITION_MAGIC)==InpMagic)
         return true;
   }
   return false;
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
   return MathMax(mn,MathMin(mx,lots));
}

void RollDay()
{
   datetime now=TimeCurrent();
   datetime d=now-(now%86400);
   if(d!=g_dayStamp){ g_dayStamp=d; g_dayEq=AccountInfoDouble(ACCOUNT_EQUITY); g_halt=false; }
   if(InpDailyLossPct>0&&g_dayEq>0&&AccountInfoDouble(ACCOUNT_EQUITY)<=g_dayEq*(1.0-InpDailyLossPct/100.0))
      g_halt=true;
}

void Dash()
{
   double avgAll=(g_nAll>0)?g_eqAll/g_nAll:0;
   double avgGated=(g_nGated>0)?g_eqGated/g_nGated:0;
   Comment(StringFormat(
      "ML ShadowGate [%s] %s\nlearned trades: %d (warmup %d)\nA/B: ALL %d trades exp %.2fR | GATE-APPROVED %d exp %.2fR | vetoed %d\nverdict so far: %s",
      _Symbol, InpEnforce?"ENFORCING":"SHADOW",
      g_learned, InpWarmupTrades,
      g_nAll, avgAll, g_nGated, avgGated, g_nVetoed,
      (g_nAll<20)?"insufficient data":
      (avgGated>avgAll+0.05?"gate ADDING value":(avgGated<avgAll-0.05?"gate HURTING":"no difference yet"))));
}
//+------------------------------------------------------------------+
