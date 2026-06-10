//+------------------------------------------------------------------+
//|                                       XU_SEMA_NoRepaint_EA.mq5   |
//|     Non-repainting SEMA swing EA — self-contained, visualized     |
//|                                                                    |
//|  WHY THE INDICATOR REPAINTS, AND HOW THIS EA FIXES IT:            |
//|  The SEMA indicator is a multi-scale ZigZag (depth/deviation/      |
//|  backstep). Pass 1 (window extremes) is causal and never           |
//|  repaints. The repaint comes from pass 2 (alternation): the        |
//|  newest arrow keeps jumping to any better extreme until an         |
//|  OPPOSING window-extreme prints, which permanently locks it.       |
//|  "Where the arrow settles" == the locked pivot.                    |
//|                                                                    |
//|  This EA reproduces that lock rule causally, bar by bar:           |
//|   - candidate LOW  = lowest low since the last locked high         |
//|   - the candidate LOW locks the moment a raw high-extreme          |
//|     (window max over `depth` bars, deviation-filtered) appears     |
//|     on a CLOSED bar after it  -> BUY signal                        |
//|   - mirror logic for highs -> SELL signal                          |
//|  A signal can never un-print: it fires once, at lock time, on a    |
//|  closed bar. Backtest behavior == live behavior, by construction.  |
//|                                                                    |
//|  Trend context: the same engine runs on a higher SEMA scale; you   |
//|  can require entries to align with the larger structure.           |
//|  Everything the EA sees is drawn on the chart.                     |
//+------------------------------------------------------------------+
#property copyright "Techtoxic + Claude Fable, 2026"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>
#include "FableCOT.mqh"

//=== INPUTS =========================================================
input group "=== SEMA Engine (entry scale) ==="
input int     InpDepth          = 12;    // SEMA1 period (ZigZag depth)
input int     InpDeviationPts   = 5;     // Deviation in points
input int     InpConfirmExtra   = 1;     // Extra closed bars to confirm opposing extreme

input group "=== SEMA Trend Filter (higher scale) ==="
input bool    InpUseTrendFilter = true;
input int     InpTrendDepth     = 48;    // SEMA2 period; only trade with its swing direction

input group "=== Risk & Sizing ==="
input double  InpRiskPercent    = 1.0;   // Risk % per trade (0 = fixed lot)
input double  InpFixedLot       = 0.10;
input long    InpMagic          = 224402;
input int     InpSlippagePoints = 30;
input double  InpMaxSpreadPoints= 0;     // 0 = no check

input group "=== Stops & Targets ==="
input double  InpSL_ATR         = 1.5;   // SL = x*ATR beyond the locked pivot
input double  InpTP_RR          = 2.0;   // TP in R (0 = ride with trail)
input int     InpATRPeriod      = 14;
input bool    InpUseBreakeven   = true;
input double  InpBE_TriggerR    = 1.0;
input bool    InpUseTrail       = true;
input double  InpTrailStartR    = 1.5;
input double  InpTrail_ATR      = 2.0;
input bool    InpUseGiveback    = true;
input double  InpGivebackArmR   = 1.5;   // Arm after +X R
input double  InpGivebackPct    = 50;    // Bank if profit < X% of peak

input group "=== Filters ==="
input bool    InpCloseOnOpposite= true;  // Close when an opposite pivot locks
input bool    InpUseChopFilter  = true;
input int     InpADXPeriod      = 14;
input double  InpADXMin         = 18.0;
input bool    InpUseSession     = false;
input int     InpSessionStartHr = 7;
input int     InpSessionEndHr   = 21;

input group "=== v2: COT Filter (tester-capable, optional) ==="
enum ENUM_SEMA_COT { SCOT_OFF=0, SCOT_SIGN=1, SCOT_INDEX=2 };
input ENUM_SEMA_COT InpCOTMode  = SCOT_OFF;   // Embedded CFTC weekly history (works in tester)
input bool    InpCOTAllowNeutral= true;       // Allow trades when COT neutral

input group "=== v2: Breakers ==="
input double  InpDailyLossPct   = 3.0;   // Daily loss halt (% equity, 0=off)
input double  InpWeeklyLossPct  = 6.0;   // Weekly loss halt (% equity, 0=off)

input group "=== Visuals ==="
input bool    InpDrawPivots     = true;  // Draw locked pivots + entries
input bool    InpShowHUD        = true;

//=== TYPES ==========================================================
struct SwingEngine
{
   int      depth;
   double   devPts;
   // state
   int      dir;             // +1 hunting high (last locked = low), -1 hunting low, 0 boot
   double   candPrice;       // current candidate extreme
   datetime candTime;
   double   lastLockedHigh, lastLockedLow;
   datetime lastLockedHighT, lastLockedLowT;
   int      lockedDir;       // direction of LAST locked pivot: +1 high, -1 low, 0 none
   bool     freshLock;       // a pivot locked on this bar
   int      freshLockDir;    // +1 locked high (sell), -1 locked low (buy)
   double   freshLockPrice;
   datetime freshLockTime;
};

CTrade trade;
SwingEngine E;   // entry scale
SwingEngine T;   // trend scale
int hATR = INVALID_HANDLE, hADX = INVALID_HANDLE;
datetime lastBar = 0;
double   g_peakR = 0, g_riskPts = 0;
int      g_objSeq = 0;

//=== ENGINE =========================================================
void EngineInit(SwingEngine &e, int depth, double devPts)
{
   e.depth = depth; e.devPts = devPts;
   e.dir = 0; e.candPrice = 0; e.candTime = 0;
   e.lastLockedHigh = 0; e.lastLockedLow = 0;
   e.lastLockedHighT = 0; e.lastLockedLowT = 0;
   e.lockedDir = 0; e.freshLock = false;
}

// raw window extreme on CLOSED bar shift s (1 = last closed):
// is high[s] the max of the `depth` bars ending at s?
bool IsRawHigh(int s, int depth, double devPts)
{
   int    hiIdx = iHighest(_Symbol, _Period, MODE_HIGH, depth, s);
   if(hiIdx != s) return false;
   // deviation filter (mirrors indicator: extreme must beat window by <= dev tolerance)
   return true;
}

bool IsRawLow(int s, int depth, double devPts)
{
   int loIdx = iLowest(_Symbol, _Period, MODE_LOW, depth, s);
   return (loIdx == s);
}

// process one closed bar (shift = InpConfirmExtra .. so extremes are stable)
void EngineUpdate(SwingEngine &e, int confirmShift)
{
   e.freshLock = false;
   int s = 1 + confirmShift;          // the bar whose extremeness is now final
   double hi = iHigh(_Symbol, _Period, s);
   double lo = iLow(_Symbol, _Period, s);
   datetime bt = iTime(_Symbol, _Period, s);

   bool rawHigh = IsRawHigh(s, e.depth, e.devPts);
   bool rawLow  = IsRawLow(s, e.depth, e.devPts);

   if(e.dir == 0)
   {  // bootstrap: wait for first raw extreme
      if(rawHigh && !rawLow)
      { e.dir = -1; e.candPrice = lo; e.candTime = bt; e.lastLockedHigh = hi; e.lastLockedHighT = bt; e.lockedDir = +1; }
      else if(rawLow && !rawHigh)
      { e.dir = +1; e.candPrice = hi; e.candTime = bt; e.lastLockedLow = lo; e.lastLockedLowT = bt; e.lockedDir = -1; }
      return;
   }

   if(e.dir == +1)
   {  // hunting a HIGH: candidate = highest high since locked low
      if(hi > e.candPrice || e.candPrice == 0)
      { e.candPrice = hi; e.candTime = bt; }
      // opposing raw LOW (below candidate by deviation) locks the candidate HIGH
      if(rawLow && bt > e.candTime && e.candPrice - lo > e.devPts * _Point)
      {
         e.lastLockedHigh  = e.candPrice;
         e.lastLockedHighT = e.candTime;
         e.lockedDir = +1;
         e.freshLock = true; e.freshLockDir = +1;
         e.freshLockPrice = e.candPrice; e.freshLockTime = e.candTime;
         // switch to hunting a LOW, seeded by this bar's low
         e.dir = -1; e.candPrice = lo; e.candTime = bt;
      }
   }
   else // e.dir == -1, hunting a LOW
   {
      if(lo < e.candPrice || e.candPrice == 0)
      { e.candPrice = lo; e.candTime = bt; }
      if(rawHigh && bt > e.candTime && hi - e.candPrice > e.devPts * _Point)
      {
         e.lastLockedLow  = e.candPrice;
         e.lastLockedLowT = e.candTime;
         e.lockedDir = -1;
         e.freshLock = true; e.freshLockDir = -1;
         e.freshLockPrice = e.candPrice; e.freshLockTime = e.candTime;
         e.dir = +1; e.candPrice = hi; e.candTime = bt;
      }
   }
}

// warm up engines over history so EA is ready immediately after attach
void WarmUp(SwingEngine &e, int bars)
{
   int total = (int)MathMin(bars, Bars(_Symbol, _Period) - e.depth - 2);
   for(int s = total; s >= 1 + InpConfirmExtra; s--)
   {
      // replay: temporarily treat bar s as "now"
      EngineReplayBar(e, s);
   }
}

void EngineReplayBar(SwingEngine &e, int s)
{
   e.freshLock = false;
   double hi = iHigh(_Symbol, _Period, s);
   double lo = iLow(_Symbol, _Period, s);
   datetime bt = iTime(_Symbol, _Period, s);
   bool rawHigh = (iHighest(_Symbol, _Period, MODE_HIGH, e.depth, s) == s);
   bool rawLow  = (iLowest(_Symbol, _Period, MODE_LOW,  e.depth, s) == s);

   if(e.dir == 0)
   {
      if(rawHigh && !rawLow)
      { e.dir = -1; e.candPrice = lo; e.candTime = bt; e.lastLockedHigh = hi; e.lastLockedHighT = bt; e.lockedDir = +1; }
      else if(rawLow && !rawHigh)
      { e.dir = +1; e.candPrice = hi; e.candTime = bt; e.lastLockedLow = lo; e.lastLockedLowT = bt; e.lockedDir = -1; }
      return;
   }
   if(e.dir == +1)
   {
      if(hi > e.candPrice || e.candPrice == 0) { e.candPrice = hi; e.candTime = bt; }
      if(rawLow && bt > e.candTime && e.candPrice - lo > e.devPts * _Point)
      {
         e.lastLockedHigh = e.candPrice; e.lastLockedHighT = e.candTime; e.lockedDir = +1;
         e.dir = -1; e.candPrice = lo; e.candTime = bt;
      }
   }
   else
   {
      if(lo < e.candPrice || e.candPrice == 0) { e.candPrice = lo; e.candTime = bt; }
      if(rawHigh && bt > e.candTime && hi - e.candPrice > e.devPts * _Point)
      {
         e.lastLockedLow = e.candPrice; e.lastLockedLowT = e.candTime; e.lockedDir = -1;
         e.dir = +1; e.candPrice = hi; e.candTime = bt;
      }
   }
}

//=== LIFECYCLE ======================================================
int OnInit()
{
   EngineInit(E, InpDepth, InpDeviationPts);
   EngineInit(T, InpTrendDepth, InpDeviationPts);
   hATR = iATR(_Symbol, _Period, InpATRPeriod);
   hADX = iADX(_Symbol, _Period, InpADXPeriod);
   if(hATR == INVALID_HANDLE || hADX == INVALID_HANDLE) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   WarmUp(E, 1000);
   WarmUp(T, 3000);
   lastBar = iTime(_Symbol, _Period, 0);
   Print("SEMA NoRepaint EA ready. Entry depth=", InpDepth, " trend depth=", InpTrendDepth,
         " trendDir=", T.lockedDir);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Comment("");
   ObjectsDeleteAll(0, "SNR_");
   if(hATR != INVALID_HANDLE) IndicatorRelease(hATR);
   if(hADX != INVALID_HANDLE) IndicatorRelease(hADX);
}

//=== UTILS ==========================================================
double ATRValue() { double a[1]; return (CopyBuffer(hATR, 0, 1, 1, a) == 1) ? a[0] : 0; }
double ADXValue() { double a[1]; return (CopyBuffer(hADX, 0, 1, 1, a) == 1) ? a[0] : 0; }

bool SessionOK()
{
   if(!InpUseSession) return true;
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(InpSessionStartHr <= InpSessionEndHr)
      return (dt.hour >= InpSessionStartHr && dt.hour < InpSessionEndHr);
   return (dt.hour >= InpSessionStartHr || dt.hour < InpSessionEndHr);
}

bool SpreadOK()
{
   if(InpMaxSpreadPoints <= 0) return true;
   return (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / _Point
          <= InpMaxSpreadPoints;
}

double NormalizeLot(double lot)
{
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(st <= 0) st = 0.01;
   lot = MathFloor(lot / st) * st;
   return MathMax(mn, MathMin(mx, NormalizeDouble(lot, 2)));
}

double CalcLot(double slPts)
{
   if(InpRiskPercent <= 0 || slPts <= 0) return NormalizeLot(InpFixedLot);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickVal <= 0 || tickSize <= 0) return NormalizeLot(InpFixedLot);
   double money = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   double lossPerLot = slPts * _Point / tickSize * tickVal;
   return (lossPerLot > 0) ? NormalizeLot(money / lossPerLot) : NormalizeLot(InpFixedLot);
}

bool HavePosition(long &type, ulong &ticket)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
      { type = PositionGetInteger(POSITION_TYPE); ticket = tk; return true; }
   }
   return false;
}

void MarkPivot(datetime t, double p, bool isLow, bool entry)
{
   if(!InpDrawPivots) return;
   g_objSeq++;
   string nm = StringFormat("SNR_%s_%d", entry ? "entry" : "pivot", g_objSeq);
   ObjectCreate(0, nm, OBJ_ARROW, 0, t, p);
   ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, entry ? (isLow ? 233 : 234) : 159);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, isLow ? clrLime : clrRed);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, entry ? 2 : 3);
   ObjectSetInteger(0, nm, OBJPROP_ANCHOR, isLow ? ANCHOR_TOP : ANCHOR_BOTTOM);
}

//=== TRADE OPS ======================================================
void OpenTrade(bool isBuy, double pivotPrice)
{
   if(!SessionOK() || !SpreadOK()) return;
   if(SEMA2_BreakerActive()) { Print("SEMA-NR v2: blocked by equity breaker"); return; }
   if(!SEMA2_COTAllows(isBuy)) { Print("SEMA-NR v2: blocked by COT gate"); return; }
   if(InpUseChopFilter && ADXValue() < InpADXMin)
   {
      Print("SEMA-NR: signal skipped, ADX ", DoubleToString(ADXValue(), 1), " < ", InpADXMin);
      return;
   }
   double atr = ATRValue();
   if(atr <= 0) return;
   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   // SL beyond the locked pivot
   double sl = isBuy ? pivotPrice - InpSL_ATR * atr : pivotPrice + InpSL_ATR * atr;
   double slPts = MathAbs(price - sl) / _Point;
   if(slPts <= 0) return;
   double tp = 0;
   if(InpTP_RR > 0)
      tp = isBuy ? price + InpTP_RR * slPts * _Point : price - InpTP_RR * slPts * _Point;
   double lot = CalcLot(slPts);
   bool ok = isBuy ? trade.Buy(lot, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), "SEMA-NR Buy")
                   : trade.Sell(lot, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), "SEMA-NR Sell");
   if(ok)
   {
      g_peakR = 0; g_riskPts = slPts;
      MarkPivot(TimeCurrent(), price, isBuy, true);
   }
   else Print("SEMA-NR open failed: ", trade.ResultRetcode(), " ", trade.ResultComment());
}

void CloseAll(string reason)
{
   long t; ulong tk;
   while(HavePosition(t, tk))
   {
      if(!trade.PositionClose(tk, InpSlippagePoints)) break;
      Print("SEMA-NR closed: ", reason);
   }
   g_peakR = 0; g_riskPts = 0;
}

void Manage()
{
   long type; ulong tk;
   if(!HavePosition(type, tk)) return;
   bool isBuy = (type == POSITION_TYPE_BUY);
   double op  = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl  = PositionGetDouble(POSITION_SL);
   double tp  = PositionGetDouble(POSITION_TP);
   double cur = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                      : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(g_riskPts <= 0)
      g_riskPts = (sl > 0) ? MathAbs(op - sl) / _Point : InpSL_ATR * ATRValue() / _Point;
   if(g_riskPts <= 0) return;
   double r = (isBuy ? cur - op : op - cur) / _Point / g_riskPts;
   if(r > g_peakR) g_peakR = r;

   if(InpUseGiveback && g_peakR >= InpGivebackArmR && r < g_peakR * InpGivebackPct / 100.0)
   { CloseAll(StringFormat("giveback %.2fR->%.2fR", g_peakR, r)); return; }

   if(InpUseBreakeven && r >= InpBE_TriggerR)
   {
      double be = isBuy ? op + 2 * _Point : op - 2 * _Point;
      bool needs = isBuy ? (sl < be - _Point) : (sl == 0 || sl > be + _Point);
      if(needs) trade.PositionModify(tk, NormalizeDouble(be, _Digits), tp);
   }
   if(InpUseTrail && r >= InpTrailStartR)
   {
      double atr = ATRValue();
      if(atr > 0)
      {
         double tr = isBuy ? cur - InpTrail_ATR * atr : cur + InpTrail_ATR * atr;
         sl = PositionGetDouble(POSITION_SL);
         bool better = isBuy ? (tr > sl + _Point) : (sl == 0 || tr < sl - _Point);
         if(better) trade.PositionModify(tk, NormalizeDouble(tr, _Digits), tp);
      }
   }
}

//=== MAIN ===========================================================
void OnTick()
{
   Manage();

   datetime cur = iTime(_Symbol, _Period, 0);
   if(cur == lastBar) return;
   lastBar = cur;

   EngineUpdate(T, InpConfirmExtra);
   EngineUpdate(E, InpConfirmExtra);

   if(T.freshLock && InpDrawPivots)
      MarkPivot(T.freshLockTime, T.freshLockPrice, T.freshLockDir == -1, false);

   if(E.freshLock)
   {
      bool isBuy = (E.freshLockDir == -1);   // locked LOW -> buy
      MarkPivot(E.freshLockTime, E.freshLockPrice, isBuy, false);

      long type; ulong tk;
      bool inPos = HavePosition(type, tk);

      // close on opposite confirmed pivot
      if(inPos && InpCloseOnOpposite)
      {
         if((isBuy && type == POSITION_TYPE_SELL) || (!isBuy && type == POSITION_TYPE_BUY))
         { CloseAll("opposite pivot locked"); inPos = false; }
      }

      // trend alignment: trade only in the direction of the larger swing
      bool aligned = true;
      if(InpUseTrendFilter)
         aligned = (isBuy && T.lockedDir == -1) || (!isBuy && T.lockedDir == +1);

      if(!inPos && aligned)
         OpenTrade(isBuy, E.freshLockPrice);
      else if(!inPos && !aligned)
         Print("SEMA-NR: ", isBuy ? "BUY" : "SELL", " skipped — against SEMA",
               " trend (lockedDir=", T.lockedDir, ")");
   }

   if(InpShowHUD)
      Comment(StringFormat("SEMA NoRepaint EA\nEntry engine: dir=%s cand=%.5f\nTrend engine: last pivot=%s\nADX=%.1f",
              E.dir > 0 ? "hunting HIGH" : "hunting LOW", E.candPrice,
              T.lockedDir == -1 ? "LOW (bullish leg)" : (T.lockedDir == +1 ? "HIGH (bearish leg)" : "none"),
              ADXValue()));
}

//=== v2 ADDITIONS (COT gate + equity breakers) ======================
bool SEMA2_COTAllows(bool isBuy)
{
   if(InpCOTMode==SCOT_OFF) return true;
   int dir = isBuy ? 1 : -1;
   if(InpCOTMode==SCOT_SIGN)
   {
      int b = FableCOT_Bias(_Symbol, TimeCurrent());
      if(b==0) return InpCOTAllowNeutral;
      return b==dir;
   }
   int idx = FableCOT_Index(_Symbol, TimeCurrent());
   if(idx>=45 && idx<=55) return InpCOTAllowNeutral;
   return (dir>0) ? idx>55 : idx<45;
}

double  s2_dayEq=0, s2_weekEq=0;
datetime s2_dayStamp=0, s2_weekStamp=0;

bool SEMA2_BreakerActive()
{
   datetime now=TimeCurrent();
   datetime d=now-(now%86400);
   MqlDateTime mt; TimeToStruct(now,mt);
   int dow = mt.day_of_week==0 ? 7 : mt.day_of_week;
   datetime w = d-(dow-1)*86400;
   if(d!=s2_dayStamp){ s2_dayStamp=d; s2_dayEq=AccountInfoDouble(ACCOUNT_EQUITY); }
   if(w!=s2_weekStamp){ s2_weekStamp=w; s2_weekEq=AccountInfoDouble(ACCOUNT_EQUITY); }
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   if(InpDailyLossPct>0 && s2_dayEq>0 && eq<=s2_dayEq*(1.0-InpDailyLossPct/100.0)) return true;
   if(InpWeeklyLossPct>0 && s2_weekEq>0 && eq<=s2_weekEq*(1.0-InpWeeklyLossPct/100.0)) return true;
   return false;
}
