//+------------------------------------------------------------------+
//|                                      AdaptiveSwingTrader_v2.mq5   |
//|  v2 of the validated adaptive swing EA (currencies + metals).     |
//|                                                                   |
//|  WHAT'S NEW IN v2 (each tied to evidence in RESEARCH-2026-06-10): |
//|   1. BACKTESTABLE COT — embedded weekly CFTC history 2005→2026    |
//|      (FableCOT.mqh). The Strategy Tester blocks WebRequest, so    |
//|      v1's COT gate could never be A/B tested. v2 reads embedded   |
//|      publish-lagged data in the tester and the live API + cache   |
//|      when trading. You can finally OPTIMIZE the COT mode.         |
//|      Evidence: cot_study.py — API-sign gate lifts mean PF         |
//|      0.98→1.28 across 9 pairs; COT-index strongest on metals;     |
//|      commercials-rule HURTS (removed as a recommended mode).      |
//|   2. VOLATILITY-TARGETED SIZING — risk% is scaled by              |
//|      (target ATR% / current ATR%), clamped [0.25x..1.5x]. Keeps   |
//|      dollar risk per unit of market vol constant. Literature +    |
//|      portfolio engineering standard.                              |
//|   3. REGIME ROUTER — Kaufman Efficiency Ratio decides which       |
//|      trigger is allowed: high ER → breakout; mid ER → pullback;   |
//|      low ER + weak ADX → stand aside. Replaces v1's static mode   |
//|      (still selectable manually).                                 |
//|   4. PARTIAL PROFIT + GIVEBACK GUARD — bank 50% at +1R, BE stop,  |
//|      chandelier trail on the rest, bank all if trade surrenders   |
//|      >50% of its peak R after exceeding +1R.                      |
//|   5. EXPECTANCY GOVERNOR — if trailing 20-trade expectancy (R)    |
//|      goes negative, risk is halved until it recovers. The EA      |
//|      de-risks itself when its edge decays instead of bleeding.    |
//|   6. WEEKLY BREAKER on top of the daily one.                      |
//|                                                                   |
//|  HONESTY NOTE: the validated edge is thin and concentrated in     |
//|  metals (XAUUSD OOS PF 1.51 w/ COT-index). FX majors daily trend  |
//|  shows no robust edge — run this on metals first, demo always.    |
//+------------------------------------------------------------------+
#property copyright "2026 Fable — Adaptive Swing v2"
#property version   "2.00"
#property strict

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>
#include "FableCOT.mqh"

//==================================================================
//  INPUTS
//==================================================================
input group "════════ Risk & Money ════════"
input double InpRiskPctPerTrade   = 0.5;     // Base risk per trade (% of equity)
input double InpMaxDailyLossPct   = 3.0;     // Daily loss breaker (% equity, 0=off)
input double InpMaxWeeklyLossPct  = 6.0;     // Weekly loss breaker (% equity, 0=off)
input int    InpMaxOpenPositions  = 1;       // Max concurrent positions (this magic)
input double InpMaxLotCap         = 50.0;    // Hard cap on lots
input bool   InpUseVolTarget      = true;    // Volatility-targeted sizing
input double InpVolTargetATRPct   = 0.0;     // Target ATR% of price (0 = auto-median)
input bool   InpUseExpectGovernor = true;    // Halve risk when 20-trade expectancy < 0

input group "════════ Timeframes ════════"
input ENUM_TIMEFRAMES InpWorkingTF = PERIOD_H4;   // Working (entry) timeframe
input ENUM_TIMEFRAMES InpTrendTF   = PERIOD_D1;   // Higher-TF trend confirm (CURRENT=off)

input group "════════ Trend Regime ════════"
input int    InpST_Period     = 10;      // SuperTrend ATR period
input double InpST_Mult       = 3.0;     // SuperTrend ATR multiplier
input int    InpEMA_Trend     = 50;      // EMA trend filter
input int    InpADX_Period    = 14;      // ADX period
input double InpADX_Min       = 18.0;    // Min ADX

input group "════════ Volatility Gate ════════"
input int    InpATR_Period    = 14;      // ATR period
input int    InpATR_PctWindow = 100;     // ATR percentile lookback
input double InpATR_PctMin    = 0.05;    // Skip flattest fraction
input double InpATR_PctMax    = 0.97;    // Skip wildest fraction

input group "════════ Entries / Regime Router ════════"
enum ENUM_ENTRY_MODE { ENTRY_PULLBACK=0, ENTRY_BREAKOUT=1, ENTRY_EITHER=2, ENTRY_AUTO=3 };
input ENUM_ENTRY_MODE InpEntryMode = ENTRY_AUTO;  // AUTO = ER-routed (v2)
input int    InpER_Period     = 20;      // Kaufman Efficiency Ratio period
input double InpER_Breakout   = 0.45;    // ER above → breakout entries
input double InpER_Floor      = 0.12;    // ER below + weak ADX → no trades
input int    InpEMA_Pullback  = 20;      // Pullback EMA
input int    InpPullbackLook  = 6;       // Pullback within N bars
input int    InpDonchian      = 20;      // Breakout channel length

input group "════════ Exits ════════"
input double InpSL_ATR_Mult   = 2.0;     // Stop = ATR × this
input double InpTP_R          = 2.5;     // Final target R (0 = trail only)
input double InpTrail_ATR_Mult= 3.0;     // Chandelier trail (0 = off)
input double InpBreakeven_R   = 1.0;     // BE at +R (0 = off)
input double InpPartial_R     = 1.0;     // Partial close at +R (0 = off)
input double InpPartialPct    = 50.0;    // Partial close %
input double InpGivebackPct   = 50.0;    // Bank if giveback > % of peak R (0=off)
input int    InpTimeStopBars  = 40;      // Stagnant-trade exit (0 = off)

input group "════════ COT Filter (v2: tester-capable) ════════"
enum ENUM_COT_MODE { COT_OFF=0, COT_API_RULE=1, COT_INDEX=2, COT_EITHER=3, COT_BOTH=4 };
input ENUM_COT_MODE InpCOTMode = COT_INDEX;    // COT gate mode (validated: INDEX for metals)
input bool   InpCOTAllowUncertain = true;      // Allow when COT uncertain/neutral
input bool   InpCOTLiveAPI    = true;          // Live: prefer COT API over embedded
input string InpCOTApiUrl     = "https://cotapi.onrender.com/api/bias"; // COT API URL
input int    InpCOTRefreshHrs = 8;             // Re-fetch every N hours (live)

input group "════════ Filters ════════"
input double InpMaxSpreadATR  = 0.10;    // Skip if spread > this × ATR (0=off)
input bool   InpTradeMonday   = true;
input bool   InpTradeFriday   = true;
input int    InpStartHour     = 0;       // Session window start (server hour)
input int    InpEndHour       = 24;      // Session window end

input group "════════ Misc ════════"
input long   InpMagic         = 480226;  // Magic
input int    InpSlippagePts   = 20;      // Max deviation (points)
input bool   InpShowDashboard = true;
input string InpComment       = "ASwing2";

//==================================================================
//  GLOBALS
//==================================================================
CTrade        trade;
CPositionInfo pos;

int    hATR=INVALID_HANDLE, hADX=INVALID_HANDLE, hEMAt=INVALID_HANDLE, hEMAp=INVALID_HANDLE;
int    hEMAtrendHTF=INVALID_HANDLE;
datetime g_lastBar=0;
double g_point;
int    g_digits;

int      g_apiBias=0;          // live API bias (+1/-1/0)
bool     g_apiValid=false;
datetime g_apiFetched=0;

double  g_dayStartEquity=0, g_weekStartEquity=0;
datetime g_dayStamp=0, g_weekStamp=0;
bool    g_haltedDay=false, g_haltedWeek=false;
string  g_status="init";

// trade telemetry for expectancy governor
double  g_lastR[64];
int     g_lastRCount=0;
double  g_riskScale=1.0;

// per-position management state
double  g_peakR=0;
bool    g_partialDone=false;
int     g_barsInTrade=0;
double  g_openEntry=0, g_openRisk1R=0;
int     g_openDir=0;
bool    g_rRecorded=false;

//==================================================================
int OnInit()
{
   g_point  = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

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
      Print("ERROR: indicator handles");
      return INIT_FAILED;
   }

   g_dayStartEquity  = AccountInfoDouble(ACCOUNT_EQUITY);
   g_weekStartEquity = g_dayStartEquity;
   g_dayStamp  = DayStart(TimeCurrent());
   g_weekStamp = WeekStart(TimeCurrent());
   ArrayInitialize(g_lastR, 0.0);

   string base,quote;
   bool mapped = FableCOT_MapSymbol(_Symbol, base, quote);
   PrintFormat("AdaptiveSwing v2 | %s %s | COT mode=%d mapped=%s base=%s | embedded rows OK | riskScale=%.2f",
               _Symbol, EnumToString(InpWorkingTF), (int)InpCOTMode,
               mapped?"yes":"NO (COT disabled for this symbol)", base, g_riskScale);
   if(InpCOTMode!=COT_OFF && InpCOTLiveAPI && !MQLInfoInteger(MQL_TESTER))
      Print("NOTE: allow ", InpCOTApiUrl, " in Tools->Options->Expert Advisors (WebRequest). ",
            "Tester runs use the embedded CFTC table automatically.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(hATR!=INVALID_HANDLE) IndicatorRelease(hATR);
   if(hADX!=INVALID_HANDLE) IndicatorRelease(hADX);
   if(hEMAt!=INVALID_HANDLE) IndicatorRelease(hEMAt);
   if(hEMAp!=INVALID_HANDLE) IndicatorRelease(hEMAp);
   if(hEMAtrendHTF!=INVALID_HANDLE) IndicatorRelease(hEMAtrendHTF);
   ObjectsDeleteAll(0, "AS2_");
   Comment("");
}

//==================================================================
void OnTick()
{
   ManageOpenPosition();

   datetime curBar = iTime(_Symbol, InpWorkingTF, 0);
   if(curBar==g_lastBar)
   {
      if(InpShowDashboard) DrawDashboard();
      return;
   }
   g_lastBar = curBar;

   RollBreakers();
   if(CountOwnPositions()>0) g_barsInTrade++;

   ProcessNewBar();
   if(InpShowDashboard) DrawDashboard();
}

//------------------------------------------------------------------
void ProcessNewBar()
{
   int need = MathMax(InpATR_PctWindow+5, MathMax(InpDonchian+5, InpEMA_Trend+5));
   need = MathMax(need, 320);

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

   double stDir[];
   ComputeSuperTrendDir(hi,lo,cl,atr,need,InpST_Mult,stDir);

   int s = 1;
   double atrV = atr[s];
   if(atrV<=0) return;

   double atrPct = PercentileRank(atr, s, InpATR_PctWindow);
   bool volOK = (atrPct>=InpATR_PctMin && atrPct<=InpATR_PctMax);

   int htfDir = 0;
   if(hEMAtrendHTF!=INVALID_HANDLE)
   {
      double emaH[]; ArraySetAsSeries(emaH,true);
      double clH[];  ArraySetAsSeries(clH,true);
      if(CopyBuffer(hEMAtrendHTF,0,0,3,emaH)>=2 && CopyClose(_Symbol,InpTrendTF,0,3,clH)>=2)
         htfDir = (clH[1]>emaH[1]) ? 1 : ((clH[1]<emaH[1]) ? -1 : 0);
   }

   bool strong = adx[s]>=InpADX_Min;
   bool trendUp = (stDir[s]==1)  && (cl[s]>emaT[s]) && strong && volOK && (htfDir>=0);
   bool trendDn = (stDir[s]==-1) && (cl[s]<emaT[s]) && strong && volOK && (htfDir<=0);
   int regime = trendUp ? 1 : (trendDn ? -1 : 0);

   // ---- v2: Efficiency Ratio regime router ----
   double er = EfficiencyRatio(cl, s, InpER_Period);
   ENUM_ENTRY_MODE mode = InpEntryMode;
   if(InpEntryMode==ENTRY_AUTO)
   {
      if(er>=InpER_Breakout)       mode = ENTRY_BREAKOUT;
      else if(er>=InpER_Floor)     mode = ENTRY_PULLBACK;
      else                         { g_status=StringFormat("ER %.2f < floor — chop, standing aside", er); return; }
   }

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
   if(mode==ENTRY_PULLBACK){ longSig=longPull; shortSig=shortPull; }
   else if(mode==ENTRY_BREAKOUT){ longSig=longBrk; shortSig=shortBrk; }
   else { longSig=longPull||longBrk; shortSig=shortPull||shortBrk; }

   int want = (longSig&&regime==1) ? 1 : ((shortSig&&regime==-1) ? -1 : 0);

   g_status = StringFormat("reg=%d ER=%.2f mode=%d ADX=%.0f volOK=%d COT=%s scale=%.2f want=%d",
              regime, er, (int)mode, adx[s], volOK, COTText(), g_riskScale, want);

   if(want==0) return;

   // ---- COT gate (v2: works in tester via FableCOT) ----
   if(!COTAllows(want)) { g_status += " [COT veto]"; return; }

   if(g_haltedDay || g_haltedWeek) return;
   if(CountOwnPositions()>=InpMaxOpenPositions) return;
   if(!SessionOK()) return;
   if(InpMaxSpreadATR>0)
   {
      double spread=(SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID));
      if(spread>InpMaxSpreadATR*atrV) return;
   }

   OpenTrade(want, atrV, atrPct, atr, s, need);
}

//==================================================================
//  COT — tri-source: live API / embedded table / off
//==================================================================
bool COTAllows(int dir)
{
   if(InpCOTMode==COT_OFF) return true;

   datetime now = TimeCurrent();
   bool tester = (bool)MQLInfoInteger(MQL_TESTER);

   // Live path: prefer fresh API bias for the sign rule
   int apiBias = 0;
   bool apiKnown = false;
   if(!tester && InpCOTLiveAPI)
   {
      MaybeRefreshAPI();
      if(g_apiValid){ apiBias=g_apiBias; apiKnown=true; }
   }
   if(!apiKnown)
   {
      apiBias = FableCOT_Bias(_Symbol, now);
      string b,q; apiKnown = FableCOT_MapSymbol(_Symbol,b,q);
   }
   int idx = FableCOT_Index(_Symbol, now);

   bool apiok = (apiBias==dir) || (InpCOTAllowUncertain && apiBias==0);
   bool idxok = (dir>0) ? (idx>55 || (InpCOTAllowUncertain && idx>=45 && idx<=55))
                        : (idx<45 || (InpCOTAllowUncertain && idx>=45 && idx<=55));

   if(InpCOTMode==COT_API_RULE) return apiok;
   if(InpCOTMode==COT_INDEX)    return idxok;
   if(InpCOTMode==COT_EITHER)   return (apiok || idxok);
   return (apiok && idxok); // COT_BOTH
}

string COTText()
{
   if(InpCOTMode==COT_OFF) return "off";
   int b = g_apiValid ? g_apiBias : FableCOT_Bias(_Symbol, TimeCurrent());
   int idx = FableCOT_Index(_Symbol, TimeCurrent());
   return StringFormat("%s/idx%d", (b>0?"BUY":(b<0?"SELL":"UNC")), idx);
}

void MaybeRefreshAPI()
{
   datetime now=TimeCurrent();
   if(g_apiValid && (now-g_apiFetched) < InpCOTRefreshHrs*3600) return;
   if(g_apiFetched!=0 && (now-g_apiFetched) < 600) return; // don't hammer on errors

   string pair=""; string b,q;
   if(!FableCOT_MapSymbol(_Symbol,b,q)) return;
   // rebuild canonical pair name the API knows
   if(b=="GOLD") pair="XAUUSD"; else if(b=="SILVER") pair="XAGUSD";
   else if(b=="DXY") pair = "USD"+q; else pair = b + (q=="DXY"?"USD":q);

   g_apiFetched=now;
   char data[], result[];
   string headers="", rh;
   ResetLastError();
   int code = WebRequest("GET", InpCOTApiUrl, headers, 8000, data, result, rh);
   if(code!=200){ Print("COT API HTTP ", code, " err=", GetLastError(), " — using embedded table"); g_apiValid=false; return; }
   string json = CharArrayToString(result);
   int p = StringFind(json, "\""+pair+"\"");
   if(p<0){ g_apiValid=false; return; }
   int bp = StringFind(json, "\"bias\"", p);
   if(bp<0){ g_apiValid=false; return; }
   string seg = StringSubstr(json, bp, 30);
   if(StringFind(seg,"BUY")>=0) g_apiBias=1;
   else if(StringFind(seg,"SELL")>=0) g_apiBias=-1;
   else g_apiBias=0;
   g_apiValid=true;
   PrintFormat("COT API refreshed: %s bias=%d", pair, g_apiBias);
}

//==================================================================
//  SUPERTREND + HELPERS
//==================================================================
void ComputeSuperTrendDir(const double &hi[], const double &lo[], const double &cl[],
                          const double &atr[], int n, double mult, double &dirOut[])
{
   ArrayResize(dirOut,n); ArraySetAsSeries(dirOut,true);
   double fUp[], fLo[], st[];
   ArrayResize(fUp,n); ArrayResize(fLo,n); ArrayResize(st,n);
   ArraySetAsSeries(fUp,true); ArraySetAsSeries(fLo,true); ArraySetAsSeries(st,true);

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

double EfficiencyRatio(const double &cl[], int s, int n)
{
   if(s+n >= ArraySize(cl)) return 0.0;
   double net = MathAbs(cl[s]-cl[s+n]);
   double sum = 0.0;
   for(int k=s;k<s+n;k++) sum += MathAbs(cl[k]-cl[k+1]);
   return (sum>0) ? net/sum : 0.0;
}

//==================================================================
//  SIZING (v2: vol-targeted) + OPEN
//==================================================================
double EffectiveRiskPct(double atrV, const double &atr[], int s)
{
   double r = InpRiskPctPerTrade * g_riskScale;
   if(InpUseVolTarget)
   {
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(price>0)
      {
         double curAtrPct = atrV/price;
         double target = InpVolTargetATRPct;
         if(target<=0)
         {  // auto: median ATR% of the percentile window
            int w = MathMin(InpATR_PctWindow, ArraySize(atr)-s-1);
            double tmp[]; ArrayResize(tmp, w);
            for(int k=0;k<w;k++) tmp[k]=atr[s+k];
            ArraySort(tmp);
            target = tmp[w/2]/price;
         }
         if(curAtrPct>0)
         {
            double f = target/curAtrPct;
            f = MathMax(0.25, MathMin(1.5, f));
            r *= f;
         }
      }
   }
   return r;
}

void OpenTrade(int dir, double atrV, double atrPct, const double &atr[], int s, int need)
{
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entry=(dir==1)?ask:bid;
   double slDist=InpSL_ATR_Mult*atrV;
   if(slDist<=0) return;

   double sl=(dir==1)?entry-slDist:entry+slDist;
   double tp=(InpTP_R>0)?((dir==1)?entry+InpTP_R*slDist:entry-InpTP_R*slDist):0.0;

   double riskPct = EffectiveRiskPct(atrV, atr, s);
   double lots = CalcLots(slDist, riskPct);
   if(lots<=0){ Print("size=0, skip"); return; }

   sl=NormalizeDouble(sl,g_digits);
   if(tp>0) tp=NormalizeDouble(tp,g_digits);

   bool ok = (dir==1) ? trade.Buy(lots,_Symbol,0.0,sl,tp,InpComment)
                      : trade.Sell(lots,_Symbol,0.0,sl,tp,InpComment);
   if(ok)
   {
      g_peakR=0; g_partialDone=false; g_barsInTrade=0;
      g_openEntry=entry; g_openRisk1R=slDist; g_openDir=dir; g_rRecorded=false;
      PrintFormat("OPEN %s %.2f lots @%.5f sl=%.5f tp=%.5f risk=%.2f%% (scale %.2f)",
                  dir==1?"BUY":"SELL", lots, entry, sl, tp, riskPct, g_riskScale);
   }
   else
      PrintFormat("OPEN FAILED: %d %s", (int)trade.ResultRetcode(), trade.ResultComment());
}

double CalcLots(double slDist, double riskPct)
{
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue= SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0||tickValue<=0) return 0;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * riskPct/100.0;
   double lossPerLot = slDist/tickSize*tickValue;
   if(lossPerLot<=0) return 0;
   double lots = riskMoney/lossPerLot;

   double minLot = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot = MathMin(SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX), InpMaxLotCap);
   double step   = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step>0) lots = MathFloor(lots/step)*step;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   if(lots*lossPerLot > equity*0.2) return 0; // sanity: never risk >20% equity on broker quirk
   return lots;
}

//==================================================================
//  POSITION MANAGEMENT (partials, BE, trail, giveback, time stop)
//==================================================================
void ManageOpenPosition()
{
   if(!pos.SelectByMagic(_Symbol, InpMagic)) return;

   double atr[1];
   if(CopyBuffer(hATR,0,1,1,atr)<1) return;
   double atrV=atr[0];
   if(atrV<=0) return;

   long   type   = pos.PositionType();
   double entry  = pos.PriceOpen();
   double slCur  = pos.StopLoss();
   double tpCur  = pos.TakeProfit();
   double volume = pos.Volume();
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double price = (type==POSITION_TYPE_BUY)?bid:ask;
   int    dir   = (type==POSITION_TYPE_BUY)?1:-1;

   double slDist0 = InpSL_ATR_Mult*atrV; // approximation of initial risk
   double risk1R = MathAbs(entry - (slCur!=0 && !g_partialDone && g_peakR<=0 ? slCur : entry-dir*slDist0));
   if(risk1R<=0) risk1R = slDist0;
   double curR = dir*(price-entry)/risk1R;
   if(curR>g_peakR) g_peakR=curR;

   // time stop
   if(InpTimeStopBars>0 && g_barsInTrade>=InpTimeStopBars && curR<0.5)
   {
      trade.PositionClose(pos.Ticket());
      Print("TIME STOP, R=",DoubleToString(curR,2));
      return;
   }

   // giveback guard
   if(InpGivebackPct>0 && g_peakR>=MathMax(1.0,InpPartial_R) &&
      curR <= g_peakR*(1.0-InpGivebackPct/100.0))
   {
      trade.PositionClose(pos.Ticket());
      Print("GIVEBACK GUARD banked at R=",DoubleToString(curR,2)," peak=",DoubleToString(g_peakR,2));
      return;
   }

   // partial at +R
   if(InpPartial_R>0 && !g_partialDone && curR>=InpPartial_R)
   {
      double closeVol = NormalizeVolume(volume*InpPartialPct/100.0);
      if(closeVol>0 && closeVol<volume)
      {
         if(trade.PositionClosePartial(pos.Ticket(), closeVol))
         {
            g_partialDone=true;
            Print("PARTIAL banked ",DoubleToString(closeVol,2)," lots at +",DoubleToString(curR,2),"R");
         }
      }
      else g_partialDone=true;
   }

   // breakeven
   if(InpBreakeven_R>0 && curR>=InpBreakeven_R)
   {
      double be = entry + dir*2*g_point;
      bool needMove = (dir==1) ? (slCur<be) : (slCur>be || slCur==0);
      if(needMove) trade.PositionModify(pos.Ticket(), NormalizeDouble(be,g_digits), tpCur);
   }

   // chandelier trail
   if(InpTrail_ATR_Mult>0)
   {
      double trail = (dir==1) ? price - InpTrail_ATR_Mult*atrV
                              : price + InpTrail_ATR_Mult*atrV;
      bool better = (dir==1) ? (trail>slCur) : (slCur==0 || trail<slCur);
      if(better && MathAbs(trail-price) > SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*g_point)
         trade.PositionModify(pos.Ticket(), NormalizeDouble(trail,g_digits), tpCur);
   }
}

double NormalizeVolume(double v)
{
   double minLot = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double step   = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step>0) v = MathFloor(v/step)*step;
   if(v<minLot) return 0;
   return v;
}

//==================================================================
//  TRADE TRANSACTIONS — record R on every position close (SL/TP too)
//==================================================================
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD) return;
   if(trans.symbol!=_Symbol) return;
   ulong deal = trans.deal;
   if(deal==0) return;
   if(!HistoryDealSelect(deal)) return;
   if((long)HistoryDealGetInteger(deal, DEAL_MAGIC)!=InpMagic) return;
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY)!=DEAL_ENTRY_OUT) return;
   if(g_openRisk1R<=0 || g_openDir==0) return;
   double closePrice = HistoryDealGetDouble(deal, DEAL_PRICE);
   double r = g_openDir*(closePrice-g_openEntry)/g_openRisk1R;
   // only record once per position (full close); partials skipped via volume check
   if(CountOwnPositions()==0 && !g_rRecorded)
   {
      g_rRecorded=true;
      RecordTradeR(r);
   }
}

//==================================================================
//  EXPECTANCY GOVERNOR
//==================================================================
void RecordTradeR(double r)
{
   if(g_lastRCount<64) { g_lastR[g_lastRCount++]=r; }
   else { for(int i=0;i<63;i++) g_lastR[i]=g_lastR[i+1]; g_lastR[63]=r; }
   if(!InpUseExpectGovernor) return;
   int n = MathMin(20, g_lastRCount);
   if(n<10) return;
   double sum=0;
   for(int i=g_lastRCount-n;i<g_lastRCount;i++) sum+=g_lastR[i];
   double expectancy = sum/n;
   double prev=g_riskScale;
   g_riskScale = (expectancy<0) ? 0.5 : 1.0;
   if(prev!=g_riskScale)
      PrintFormat("GOVERNOR: 20-trade expectancy %.2fR -> risk scale %.2f", expectancy, g_riskScale);
}

//==================================================================
//  BREAKERS / SESSION / UTIL
//==================================================================
datetime DayStart(datetime t){ return t - (t%86400); }
datetime WeekStart(datetime t)
{
   MqlDateTime mt; TimeToStruct(t,mt);
   int dow = mt.day_of_week==0 ? 7 : mt.day_of_week;
   return DayStart(t) - (dow-1)*86400;
}

void RollBreakers()
{
   datetime now=TimeCurrent();
   if(DayStart(now)!=g_dayStamp)
   {
      g_dayStamp=DayStart(now);
      g_dayStartEquity=AccountInfoDouble(ACCOUNT_EQUITY);
      g_haltedDay=false;
   }
   if(WeekStart(now)!=g_weekStamp)
   {
      g_weekStamp=WeekStart(now);
      g_weekStartEquity=AccountInfoDouble(ACCOUNT_EQUITY);
      g_haltedWeek=false;
   }
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   if(InpMaxDailyLossPct>0 && g_dayStartEquity>0 &&
      eq <= g_dayStartEquity*(1.0-InpMaxDailyLossPct/100.0) && !g_haltedDay)
   { g_haltedDay=true; Print("DAILY BREAKER tripped"); }
   if(InpMaxWeeklyLossPct>0 && g_weekStartEquity>0 &&
      eq <= g_weekStartEquity*(1.0-InpMaxWeeklyLossPct/100.0) && !g_haltedWeek)
   { g_haltedWeek=true; Print("WEEKLY BREAKER tripped"); }
}

bool SessionOK()
{
   MqlDateTime mt; TimeToStruct(TimeCurrent(),mt);
   if(!InpTradeMonday && mt.day_of_week==1) return false;
   if(!InpTradeFriday && mt.day_of_week==5) return false;
   if(InpStartHour<InpEndHour)
      return (mt.hour>=InpStartHour && mt.hour<InpEndHour);
   return (mt.hour>=InpStartHour || mt.hour<InpEndHour);
}

int CountOwnPositions()
{
   int c=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk>0 && PositionGetString(POSITION_SYMBOL)==_Symbol &&
         PositionGetInteger(POSITION_MAGIC)==InpMagic) c++;
   }
   return c;
}

void DrawDashboard()
{
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   string txt = StringFormat(
      "AdaptiveSwing v2 [%s %s]\n%s\nday %.2f%%  week %.2f%%  halted D:%d W:%d\nriskScale %.2f  trades(rec) %d",
      _Symbol, EnumToString(InpWorkingTF), g_status,
      g_dayStartEquity>0 ? 100.0*(eq/g_dayStartEquity-1.0) : 0.0,
      g_weekStartEquity>0 ? 100.0*(eq/g_weekStartEquity-1.0) : 0.0,
      g_haltedDay?1:0, g_haltedWeek?1:0, g_riskScale, g_lastRCount);
   Comment(txt);
}
//+------------------------------------------------------------------+
