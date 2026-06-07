//+------------------------------------------------------------------+
//|                                       AdaptiveSwingTrader.mq5     |
//|   Adaptive, non-repainting SWING EA for FX majors & metals.      |
//|                                                                  |
//|   Built to replace the static / hardcoded / scalping / martingale|
//|   patterns found across the legacy bots. Everything that used to |
//|   be a fixed pip value is now a function of live volatility (ATR)|
//|   and account risk. Decisions are taken ONLY on a closed bar of  |
//|   the working timeframe (no repaint, no tick-noise).             |
//|                                                                  |
//|   Signal stack (all closed-bar):                                 |
//|     1. Trend regime  : SuperTrend(ATR) direction + ADX strength  |
//|                        + EMA trend filter + volatility-regime gate|
//|     2. Entry trigger : pullback-to-dynamic-S&R OR Donchian break |
//|     3. COT bias gate : WebRequest to the user's COT API          |
//|                        (https://cotapi.onrender.com/api/bias)    |
//|   Risk: ATR stop, R-multiple target, chandelier trail, breakeven,|
//|         %-equity volatility sizing, daily-loss breaker. NO grid, |
//|         NO martingale, ONE position per symbol.                  |
//|                                                                  |
//|   Research that justifies the design lives in                    |
//|   ../research (Python walk-forward + COT study). Read it: the    |
//|   honest edge is thin and concentrated in metals. Demo-test first|
//+------------------------------------------------------------------+
#property copyright "2026 — Adaptive Swing (currencies + metals)"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>

//==================================================================
//  INPUTS
//==================================================================
input group "════════ Risk & Money ════════"
input double InpRiskPctPerTrade   = 0.5;     // Risk per trade (% of equity)
input double InpMaxDailyLossPct   = 3.0;     // Daily loss breaker (% equity, 0=off)
input int    InpMaxOpenPositions  = 1;       // Max concurrent positions (this EA/magic)
input double InpMaxLotCap         = 50.0;    // Hard cap on lots (safety)
input double InpMinLot            = 0.0;     // Floor on lots (0 = broker min)

input group "════════ Timeframes ════════"
input ENUM_TIMEFRAMES InpWorkingTF = PERIOD_H4;   // Working (entry) timeframe
input ENUM_TIMEFRAMES InpTrendTF   = PERIOD_D1;   // Higher-TF trend confirmation (PERIOD_CURRENT=off)

input group "════════ Trend Regime (adaptive) ════════"
input int    InpST_Period     = 10;      // SuperTrend ATR period
input double InpST_Mult       = 3.0;     // SuperTrend ATR multiplier
input int    InpEMA_Trend     = 50;      // EMA trend filter
input int    InpADX_Period    = 14;      // ADX period
input double InpADX_Min       = 18.0;    // Min ADX (below = chop, stand aside)

input group "════════ Volatility Regime Gate ════════"
input int    InpATR_Period    = 14;      // ATR period (stops & sizing)
input int    InpATR_PctWindow = 100;     // Lookback for ATR percentile
input double InpATR_PctMin    = 0.05;    // Skip flattest fraction (0..1)
input double InpATR_PctMax    = 0.97;    // Skip wildest fraction (0..1)

input group "════════ Entries ════════"
enum ENUM_ENTRY_MODE { ENTRY_PULLBACK=0, ENTRY_BREAKOUT=1, ENTRY_EITHER=2 };
input ENUM_ENTRY_MODE InpEntryMode = ENTRY_EITHER;   // Entry style
input int    InpEMA_Pullback  = 20;      // Dynamic pullback / S&R EMA
input int    InpPullbackLook  = 6;       // Pullback must occur within N bars
input int    InpDonchian      = 20;      // Breakout channel length

input group "════════ Exits (adaptive) ════════"
input double InpSL_ATR_Mult   = 2.0;     // Stop = entry ± ATR×this
input double InpTP_R          = 2.5;     // Take profit at R-multiple (0 = pure trail)
input double InpTrail_ATR_Mult= 3.0;     // Chandelier trail distance (0 = off)
input double InpBreakeven_R   = 1.0;     // Move stop to BE after +R (0 = off)
input int    InpTimeStopBars  = 40;      // Close a stagnant trade after N bars (0 = off)

input group "════════ COT Bias Filter ════════"
input bool   InpUseCOT        = true;    // Require COT bias agreement
input bool   InpCOTAllowUncertain = true;// Allow trades when COT = UNCERTAIN
input string InpCOTApiUrl     = "https://cotapi.onrender.com/api/bias"; // COT API (add to allowed URLs!)
input string InpCOTPairOverride = "";    // Force pair e.g. "XAUUSD" (blank = auto from symbol)
input int    InpCOTRefreshHrs = 8;       // Re-fetch COT every N hours
enum ENUM_BIAS_FALLBACK { BIAS_BLOCK=0, BIAS_ALLOW=1, BIAS_MANUAL=2 };
input ENUM_BIAS_FALLBACK InpCOTFallback = BIAS_ALLOW; // If API unreachable
input int    InpManualBias    = 0;       // Manual bias if fallback=MANUAL (1 buy / -1 sell / 0 none)

input group "════════ Filters ════════"
input double InpMaxSpreadATR  = 0.10;    // Skip if spread > this × ATR (0=off)
input bool   InpTradeMonday   = true;
input bool   InpTradeFriday   = true;
input int    InpStartHour     = 0;       // Server-hour window start (0..23)
input int    InpEndHour       = 24;      // Server-hour window end

input group "════════ Misc ════════"
input long   InpMagic         = 480026;  // Magic number
input int    InpSlippagePts   = 20;      // Max deviation (points)
input bool   InpShowDashboard = true;    // On-chart status panel
input string InpComment       = "AdaptiveSwing";

//==================================================================
//  GLOBALS
//==================================================================
CTrade        trade;
CPositionInfo pos;

int    hATR=INVALID_HANDLE, hADX=INVALID_HANDLE, hEMAt=INVALID_HANDLE, hEMAp=INVALID_HANDLE;
int    hATRtrend=INVALID_HANDLE, hEMAtrendHTF=INVALID_HANDLE;
datetime g_lastBar=0;
double g_point, g_tickSize, g_tickValue;
int    g_digits;
string g_cotPair="";
int    g_cotBias=0;            // +1 buy / -1 sell / 0 uncertain-or-none
bool   g_cotValid=false;
datetime g_cotFetched=0;
double g_dayStartEquity=0;
datetime g_dayStamp=0;
bool   g_haltedToday=false;
string g_status="init";

//==================================================================
//  INIT
//==================================================================
int OnInit()
{
   g_point    = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   g_tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   g_tickValue= SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(g_tickSize<=0) g_tickSize=g_point;

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);

   hATR  = iATR(_Symbol, InpWorkingTF, InpATR_Period);
   hADX  = iADX(_Symbol, InpWorkingTF, InpADX_Period);
   hEMAt = iMA(_Symbol, InpWorkingTF, InpEMA_Trend,   0, MODE_EMA, PRICE_CLOSE);
   hEMAp = iMA(_Symbol, InpWorkingTF, InpEMA_Pullback,0, MODE_EMA, PRICE_CLOSE);
   if(InpTrendTF!=PERIOD_CURRENT && InpTrendTF!=InpWorkingTF)
      hEMAtrendHTF = iMA(_Symbol, InpTrendTF, InpEMA_Trend, 0, MODE_EMA, PRICE_CLOSE);

   if(hATR==INVALID_HANDLE||hADX==INVALID_HANDLE||hEMAt==INVALID_HANDLE||hEMAp==INVALID_HANDLE)
   {
      Print("ERROR: failed to create indicator handles");
      return INIT_FAILED;
   }

   g_cotPair = ResolveCOTPair();
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_dayStamp = DayStart(TimeCurrent());

   PrintFormat("AdaptiveSwing init | %s %s | COT pair=%s | risk=%.2f%% | NO martingale",
               _Symbol, EnumToString(InpWorkingTF), g_cotPair, InpRiskPctPerTrade);
   if(InpUseCOT)
      Print("NOTE: add ", InpCOTApiUrl,
            " to Tools→Options→Expert Advisors→Allow WebRequest, or COT uses the fallback.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(hATR!=INVALID_HANDLE) IndicatorRelease(hATR);
   if(hADX!=INVALID_HANDLE) IndicatorRelease(hADX);
   if(hEMAt!=INVALID_HANDLE) IndicatorRelease(hEMAt);
   if(hEMAp!=INVALID_HANDLE) IndicatorRelease(hEMAp);
   if(hEMAtrendHTF!=INVALID_HANDLE) IndicatorRelease(hEMAtrendHTF);
   ObjectsDeleteAll(0, "ASD_");
}

//==================================================================
//  MAIN — closed-bar driven (non-repainting)
//==================================================================
void OnTick()
{
   // manage open trades every tick for responsive trailing exits
   ManageOpenPosition();

   datetime curBar = iTime(_Symbol, InpWorkingTF, 0);
   if(curBar==g_lastBar)
   {
      if(InpShowDashboard) DrawDashboard();
      return;
   }
   g_lastBar = curBar;          // new closed bar just formed

   RollDailyBreaker();
   if(InpUseCOT) MaybeRefreshCOT();

   ProcessNewBar();
   if(InpShowDashboard) DrawDashboard();
}

//------------------------------------------------------------------
void ProcessNewBar()
{
   // ---- pull closed-bar indicator values (shift 1) ----
   int need = MathMax(InpATR_PctWindow+5, MathMax(InpDonchian+5, InpEMA_Trend+5));
   need = MathMax(need, 320);   // enough for SuperTrend convergence

   double atr[], adx[], emaT[], emaP[];
   double hi[], lo[], cl[], op[];
   ArraySetAsSeries(atr,true); ArraySetAsSeries(adx,true);
   ArraySetAsSeries(emaT,true); ArraySetAsSeries(emaP,true);
   ArraySetAsSeries(hi,true); ArraySetAsSeries(lo,true);
   ArraySetAsSeries(cl,true); ArraySetAsSeries(op,true);

   if(CopyBuffer(hATR,0,0,need,atr)<need) return;
   if(CopyBuffer(hADX,0,0,need,adx)<need) return;
   if(CopyBuffer(hEMAt,0,0,need,emaT)<need) return;
   if(CopyBuffer(hEMAp,0,0,need,emaP)<need) return;
   if(CopyHigh(_Symbol,InpWorkingTF,0,need,hi)<need) return;
   if(CopyLow(_Symbol,InpWorkingTF,0,need,lo)<need) return;
   if(CopyClose(_Symbol,InpWorkingTF,0,need,cl)<need) return;
   if(CopyOpen(_Symbol,InpWorkingTF,0,need,op)<need) return;

   // ---- SuperTrend direction on closed bar ----
   double stDir[];
   ComputeSuperTrendDir(hi,lo,cl,atr,need,InpST_Mult,stDir); // stDir as series

   int s = 1; // last closed bar
   double atrV = atr[s];
   if(atrV<=0) return;

   // ---- volatility regime gate ----
   double atrPct = PercentileRank(atr, s, InpATR_PctWindow);
   bool volOK = (atrPct>=InpATR_PctMin && atrPct<=InpATR_PctMax);

   // ---- HTF trend confirmation (optional) ----
   int htfDir = 0;
   if(hEMAtrendHTF!=INVALID_HANDLE)
   {
      double emaH[]; ArraySetAsSeries(emaH,true);
      double clH[]; ArraySetAsSeries(clH,true);
      if(CopyBuffer(hEMAtrendHTF,0,0,3,emaH)>=2 && CopyClose(_Symbol,InpTrendTF,0,3,clH)>=2)
         htfDir = (clH[1]>emaH[1]) ? 1 : ((clH[1]<emaH[1]) ? -1 : 0);
   }

   // ---- regime ----
   bool strong = adx[s]>=InpADX_Min;
   bool trendUp = (stDir[s]==1)  && (cl[s]>emaT[s]) && strong && volOK && (htfDir>=0);
   bool trendDn = (stDir[s]==-1) && (cl[s]<emaT[s]) && strong && volOK && (htfDir<=0);
   int regime = trendUp ? 1 : (trendDn ? -1 : 0);

   // ---- entry triggers ----
   bool dippedLong=false, dippedShort=false;
   for(int k=s;k<s+InpPullbackLook && k<need;k++)
   {
      if(lo[k]<=emaP[k]) dippedLong=true;
      if(hi[k]>=emaP[k]) dippedShort=true;
   }
   bool bullBar = (cl[s]>op[s]) && (cl[s]>cl[s+1]);
   bool bearBar = (cl[s]<op[s]) && (cl[s]<cl[s+1]);

   double donHi=hi[s+1], donLo=lo[s+1];
   for(int k=s+1;k<=s+InpDonchian && k<need;k++){ donHi=MathMax(donHi,hi[k]); donLo=MathMin(donLo,lo[k]); }

   bool longPull  = trendUp && dippedLong  && bullBar && (cl[s]>=emaP[s]);
   bool shortPull = trendDn && dippedShort && bearBar && (cl[s]<=emaP[s]);
   bool longBrk   = trendUp && (cl[s]>donHi) && bullBar;
   bool shortBrk  = trendDn && (cl[s]<donLo) && bearBar;

   bool longSig, shortSig;
   if(InpEntryMode==ENTRY_PULLBACK){ longSig=longPull; shortSig=shortPull; }
   else if(InpEntryMode==ENTRY_BREAKOUT){ longSig=longBrk; shortSig=shortBrk; }
   else { longSig=longPull||longBrk; shortSig=shortPull||shortBrk; }

   int want = (longSig&&regime==1) ? 1 : ((shortSig&&regime==-1) ? -1 : 0);

   g_status = StringFormat("regime=%d ST=%d ADX=%.0f volOK=%d COT=%s want=%d",
              regime,(int)stDir[s],adx[s],volOK, BiasText(g_cotBias), want);

   if(want==0) return;

   // ---- COT bias gate ----
   if(InpUseCOT)
   {
      int b = EffectiveBias();
      if(b==9) return;                 // fallback=BLOCK -> stand aside
      if(want==1 && b==-1) return;
      if(want==-1 && b==1) return;
      if(b==0 && !InpCOTAllowUncertain) return;
   }

   // ---- guards ----
   if(g_haltedToday) return;
   if(CountOwnPositions()>=InpMaxOpenPositions) return;
   if(!SessionOK()) return;
   if(InpMaxSpreadATR>0)
   {
      double spread=(SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID));
      if(spread>InpMaxSpreadATR*atrV) return;
   }

   OpenTrade(want, atrV);
}

//==================================================================
//  SUPER TREND (direction series, +1 up / -1 down)
//==================================================================
void ComputeSuperTrendDir(const double &hi[], const double &lo[], const double &cl[],
                          const double &atr[], int n, double mult, double &dirOut[])
{
   ArrayResize(dirOut,n); ArraySetAsSeries(dirOut,true);
   double fUp[], fLo[], st[];
   ArrayResize(fUp,n); ArrayResize(fLo,n); ArrayResize(st,n);
   ArraySetAsSeries(fUp,true); ArraySetAsSeries(fLo,true); ArraySetAsSeries(st,true);

   // oldest bar = index n-1 (series). Seed there, iterate to newest (0).
   int o=n-1;
   double hl2=(hi[o]+lo[o])/2.0;
   fUp[o]=hl2+mult*atr[o]; fLo[o]=hl2-mult*atr[o]; st[o]=fUp[o]; dirOut[o]=-1;
   for(int i=n-2;i>=0;i--)
   {
      hl2=(hi[i]+lo[i])/2.0;
      double bUp=hl2+mult*atr[i], bLo=hl2-mult*atr[i];
      fUp[i]=(bUp<fUp[i+1] || cl[i+1]>fUp[i+1]) ? bUp : fUp[i+1];
      fLo[i]=(bLo>fLo[i+1] || cl[i+1]<fLo[i+1]) ? bLo : fLo[i+1];
      if(st[i+1]==fUp[i+1]) st[i]=(cl[i]>fUp[i]) ? fLo[i] : fUp[i];
      else                  st[i]=(cl[i]<fLo[i]) ? fUp[i] : fLo[i];
      dirOut[i]=(st[i]==fLo[i]) ? 1 : -1;
   }
}

double PercentileRank(const double &arr[], int at, int window)
{
   int cnt=0, le=0;
   for(int k=at;k<at+window;k++){ if(k>=ArraySize(arr)) break; cnt++; if(arr[k]<=arr[at]) le++; }
   return (cnt>0) ? (double)le/cnt : 0.5;
}

//==================================================================
//  ORDER OPEN / SIZE
//==================================================================
void OpenTrade(int dir, double atrV)
{
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entry=(dir==1)?ask:bid;
   double slDist=InpSL_ATR_Mult*atrV;
   if(slDist<=0) return;

   double sl=(dir==1)?entry-slDist:entry+slDist;
   double tp=0;
   if(InpTP_R>0) tp=(dir==1)?entry+InpTP_R*slDist:entry-InpTP_R*slDist;

   // respect broker minimum stop distance
   long stopsLevel=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double minDist=stopsLevel*g_point;
   if(minDist>0 && slDist<minDist){ slDist=minDist; sl=(dir==1)?entry-slDist:entry+slDist; }

   double lots=CalcLots(slDist);
   if(lots<=0){ Print("size=0, skip"); return; }

   sl=NormalizeDouble(sl,g_digits);
   tp=NormalizeDouble(tp,g_digits);

   bool ok=(dir==1)? trade.Buy(lots,_Symbol,0.0,sl,tp,InpComment)
                   : trade.Sell(lots,_Symbol,0.0,sl,tp,InpComment);
   if(!ok)
      PrintFormat("Order FAILED dir=%d lots=%.2f sl=%.5f tp=%.5f ret=%d %s",
                  dir,lots,sl,tp,trade.ResultRetcode(),trade.ResultRetcodeDescription());
   else
      PrintFormat("OPEN %s %.2f lots @~%.5f SL %.5f TP %.5f | ATR=%.5f COT=%s",
                  dir==1?"BUY":"SELL",lots,entry,sl,tp,atrV,BiasText(g_cotBias));
}

double CalcLots(double slDist)
{
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney=equity*InpRiskPctPerTrade/100.0;
   double tickVal=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double tickSz =SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tickSz<=0) tickSz=g_point;
   if(tickVal<=0) tickVal=1.0;
   double moneyPerPricePerLot=tickVal/tickSz;          // loss for 1.0 price move, 1 lot
   double lossPerLot=slDist*moneyPerPricePerLot;
   if(lossPerLot<=0) return 0;
   double lots=riskMoney/lossPerLot;

   double minL=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxL=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step<=0) step=0.01;
   lots=MathFloor(lots/step)*step;
   double floorL=(InpMinLot>0)?InpMinLot:minL;
   if(lots<floorL) lots=floorL;
   if(lots>maxL) lots=maxL;
   if(InpMaxLotCap>0 && lots>InpMaxLotCap) lots=InpMaxLotCap;
   return lots;
}

//==================================================================
//  POSITION MANAGEMENT (trail / breakeven / time / flip)
//==================================================================
void ManageOpenPosition()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Symbol()!=_Symbol || pos.Magic()!=InpMagic) continue;

      int dir=(pos.PositionType()==POSITION_TYPE_BUY)?1:-1;
      double entry=pos.PriceOpen();
      double curSL=pos.StopLoss();
      double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
      double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      double price=(dir==1)?bid:ask;

      double atr[]; ArraySetAsSeries(atr,true);
      if(CopyBuffer(hATR,0,0,3,atr)<2) continue;
      double atrV=atr[1];
      double initRisk=InpSL_ATR_Mult*atrV;
      double newSL=curSL;

      // breakeven
      if(InpBreakeven_R>0)
      {
         if(dir==1 && price>=entry+InpBreakeven_R*initRisk) newSL=MathMax(newSL,entry);
         if(dir==-1&& price<=entry-InpBreakeven_R*initRisk) newSL=(newSL==0)?entry:MathMin(newSL,entry);
      }
      // chandelier trail off the close extreme since we run each tick: use current price extreme proxy
      if(InpTrail_ATR_Mult>0)
      {
         if(dir==1)  newSL=MathMax(newSL, price-InpTrail_ATR_Mult*atrV);
         else        newSL=(newSL==0)?(price+InpTrail_ATR_Mult*atrV):MathMin(newSL,price+InpTrail_ATR_Mult*atrV);
      }
      // never loosen, never cross price; normalize
      if(dir==1 && newSL>0 && newSL<price && (curSL==0 || newSL>curSL+g_point*0.5))
         trade.PositionModify(_Symbol,NormalizeDouble(newSL,g_digits),pos.TakeProfit());
      if(dir==-1&& newSL>0 && newSL>price && (curSL==0 || newSL<curSL-g_point*0.5))
         trade.PositionModify(_Symbol,NormalizeDouble(newSL,g_digits),pos.TakeProfit());

      // time stop
      if(InpTimeStopBars>0)
      {
         int barsHeld=iBarShift(_Symbol,InpWorkingTF,(datetime)pos.Time());
         if(barsHeld>=InpTimeStopBars){ trade.PositionClose(_Symbol); continue; }
      }
   }
}

//==================================================================
//  COT BIAS (WebRequest + parse + cache + fallback)
//==================================================================
void MaybeRefreshCOT()
{
   if(g_cotFetched>0 && (TimeCurrent()-g_cotFetched) < InpCOTRefreshHrs*3600) return;
   FetchCOT();
}

int EffectiveBias()
{
   if(g_cotValid) return g_cotBias;
   switch(InpCOTFallback)
   {
      case BIAS_BLOCK:  return 9;     // 9 = block everything (no agreement possible)
      case BIAS_MANUAL: return InpManualBias;
      default:          return 0;     // ALLOW: treat as uncertain
   }
}

void FetchCOT()
{
   if(g_cotPair=="") { g_cotValid=false; return; }
   char post[]; char result[]; string headers;
   string reqHeaders="";
   int timeout=8000;
   ResetLastError();
   int code=WebRequest("GET",InpCOTApiUrl,reqHeaders,timeout,post,result,headers);
   if(code==-1)
   {
      PrintFormat("COT WebRequest blocked/failed (err %d). Add %s to allowed URLs. Using fallback.",
                  GetLastError(),InpCOTApiUrl);
      g_cotValid=false; g_cotFetched=TimeCurrent(); return;
   }
   if(code!=200){ PrintFormat("COT HTTP %d, fallback.",code); g_cotValid=false; g_cotFetched=TimeCurrent(); return; }

   string body=CharArrayToString(result,0,WHOLE_ARRAY,CP_UTF8);
   int bias=ParseBiasFor(body,g_cotPair);
   if(bias==-99){ Print("COT: pair ",g_cotPair," not found in response, fallback."); g_cotValid=false; }
   else { g_cotBias=bias; g_cotValid=true;
          PrintFormat("COT updated: %s -> %s",g_cotPair,BiasText(bias)); }
   g_cotFetched=TimeCurrent();
}

// Find {"pair":"XXX", ... "bias":"BUY|SELL|UNCERTAIN"} and return +1/-1/0, or -99 if absent.
int ParseBiasFor(const string body,const string pair)
{
   string key="\"pair\":\""+pair+"\"";
   int p=StringFind(body,key);
   if(p<0) return -99;
   int b=StringFind(body,"\"bias\":\"",p);
   if(b<0) return -99;
   b+=8;
   int e=StringFind(body,"\"",b);
   if(e<0) return -99;
   string v=StringSubstr(body,b,e-b);
   if(v=="BUY")  return 1;
   if(v=="SELL") return -1;
   return 0;
}

string BiasText(int b){ return b==1?"BUY":(b==-1?"SELL":(b==9?"BLOCK":"UNCERTAIN")); }

// Map the chart symbol to a COT pair name the API understands.
string ResolveCOTPair()
{
   if(InpCOTPairOverride!="") return InpCOTPairOverride;
   string s=_Symbol; StringToUpper(s);
   // strip common broker suffixes/prefixes -> keep first 6 alpha or gold/silver
   string alpha="";
   for(int i=0;i<StringLen(s);i++){ ushort c=StringGetCharacter(s,i); if((c>='A'&&c<='Z')) alpha+=ShortToString(c); }
   if(StringFind(alpha,"XAU")>=0 || StringFind(alpha,"GOLD")>=0) return "XAUUSD";
   if(StringFind(alpha,"XAG")>=0 || StringFind(alpha,"SILVER")>=0) return "XAGUSD";
   if(StringLen(alpha)>=6) return StringSubstr(alpha,0,6);
   return alpha;
}

//==================================================================
//  DAILY LOSS BREAKER / SESSION
//==================================================================
datetime DayStart(datetime t){ MqlDateTime d; TimeToStruct(t,d); d.hour=0; d.min=0; d.sec=0; return StructToTime(d); }

void RollDailyBreaker()
{
   datetime ds=DayStart(TimeCurrent());
   if(ds!=g_dayStamp){ g_dayStamp=ds; g_dayStartEquity=AccountInfoDouble(ACCOUNT_EQUITY); g_haltedToday=false; }
   if(InpMaxDailyLossPct>0)
   {
      double eq=AccountInfoDouble(ACCOUNT_EQUITY);
      double ddPct=(g_dayStartEquity-eq)/g_dayStartEquity*100.0;
      if(ddPct>=InpMaxDailyLossPct && !g_haltedToday)
      {
         g_haltedToday=true;
         PrintFormat("DAILY LOSS BREAKER hit (-%.2f%%). No new trades today.",ddPct);
      }
   }
}

bool SessionOK()
{
   MqlDateTime d; TimeToStruct(TimeCurrent(),d);
   if(!InpTradeMonday && d.day_of_week==1) return false;
   if(!InpTradeFriday && d.day_of_week==5) return false;
   if(d.hour<InpStartHour || d.hour>=InpEndHour) return false;
   return true;
}

int CountOwnPositions()
{
   int n=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
      if(pos.SelectByIndex(i) && pos.Symbol()==_Symbol && pos.Magic()==InpMagic) n++;
   return n;
}

//==================================================================
//  DASHBOARD
//==================================================================
void DrawDashboard()
{
   string n="ASD_panel";
   string txt=StringFormat("ADAPTIVE SWING | %s %s | %s | COT %s:%s(%s) | Eq %.0f DayPnL %.2f%% %s",
        _Symbol, EnumToString(InpWorkingTF), g_status,
        g_cotPair, BiasText(g_cotValid?g_cotBias:EffectiveBias()), g_cotValid?"live":"fallback",
        AccountInfoDouble(ACCOUNT_EQUITY),
        (AccountInfoDouble(ACCOUNT_EQUITY)-g_dayStartEquity)/g_dayStartEquity*100.0,
        g_haltedToday?"[HALTED]":"");
   if(ObjectFind(0,n)<0)
   {
      ObjectCreate(0,n,OBJ_LABEL,0,0,0);
      ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER_LEFT_UPPER);
      ObjectSetInteger(0,n,OBJPROP_XDISTANCE,12);
      ObjectSetInteger(0,n,OBJPROP_YDISTANCE,22);
      ObjectSetInteger(0,n,OBJPROP_FONTSIZE,9);
      ObjectSetString(0,n,OBJPROP_FONT,"Consolas");
      ObjectSetInteger(0,n,OBJPROP_COLOR,clrGainsboro);
      ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   }
   ObjectSetString(0,n,OBJPROP_TEXT,txt);
}
//+------------------------------------------------------------------+
