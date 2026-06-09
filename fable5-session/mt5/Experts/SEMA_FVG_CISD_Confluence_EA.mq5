//+------------------------------------------------------------------+
//|                                  SEMA_FVG_CISD_Confluence_EA.mq5 |
//|     Multi-TF FVG + non-repainting SEMA + CISD — one solid EA      |
//|                                                                    |
//|  The three tools you trade by hand, fused into one causal system:  |
//|                                                                    |
//|  1. MULTI-TIMEFRAME FVG (from your fvg+max indicator logic):       |
//|     unmitigated fair value gaps are detected on up to 3 higher     |
//|     timeframes (default H1 + H4). They are the LOCATION filter:    |
//|     the EA only hunts where a fresh HTF imbalance sits.            |
//|                                                                    |
//|  2. SEMA (non-repainting): when price taps a bullish HTF FVG and   |
//|     a SEMA swing LOW locks (causal ZigZag confirmation - the       |
//|     "settled arrow"), structure has shifted off the zone.          |
//|                                                                    |
//|  3. CISD (change in state of delivery): final trigger — price      |
//|     must CLOSE above the open of the bearish delivery sequence     |
//|     that swept into the low (mirror for sells). No close = no      |
//|     trade. This kills most fake taps.                              |
//|                                                                    |
//|  Entry = location (FVG) + structure (SEMA lock) + confirmation     |
//|  (CISD close). SL beyond pivot/zone, TP = RR or next opposing      |
//|  HTF FVG. Breakeven, giveback guard, partial close, ADX chop       |
//|  filter, session filter. Everything drawn on the chart.            |
//+------------------------------------------------------------------+
#property copyright "Techtoxic + Claude Fable 5, 2026"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//=== INPUTS =========================================================
input group "=== Multi-TF FVG ==="
input ENUM_TIMEFRAMES InpFVG_TF1   = PERIOD_H1;
input bool            InpFVG_Use1  = true;
input ENUM_TIMEFRAMES InpFVG_TF2   = PERIOD_H4;
input bool            InpFVG_Use2  = true;
input ENUM_TIMEFRAMES InpFVG_TF3   = PERIOD_D1;
input bool            InpFVG_Use3  = false;
input int             InpFVG_MinPts    = 50;    // Min gap size in points
input int             InpFVG_MaxAge    = 200;   // Max zone age in HTF bars
input int             InpFVG_MaxZones  = 30;    // Max tracked zones

input group "=== SEMA Engine ==="
input int     InpDepth          = 12;    // ZigZag depth
input int     InpDeviationPts   = 5;

input group "=== CISD Confirmation ==="
input bool    InpUseCISD        = true;  // Require CISD close to enter
input int     InpCISD_MaxBars   = 15;    // Max bars after pivot lock to wait for CISD
input int     InpCISD_MaxSeq    = 10;    // Max delivery-candle sequence to scan

input group "=== Risk & Sizing ==="
input double  InpRiskPercent    = 1.0;
input double  InpFixedLot       = 0.10;
input long    InpMagic          = 224405;
input int     InpSlippagePoints = 30;
input double  InpMaxSpreadPoints= 0;
input int     InpMaxTradesPerDay= 4;

input group "=== Stops & Targets ==="
input double  InpSL_ATRBuffer   = 0.75;  // SL beyond pivot (ATR mult)
input int     InpATRPeriod      = 14;
input double  InpTP_RR          = 2.5;   // TP in R (or next opposing FVG if closer & >= MinRR)
input double  InpMinRR          = 1.2;
input bool    InpUseBreakeven   = true;
input double  InpBE_TriggerR    = 1.0;
input bool    InpUsePartial     = true;
input double  InpPartialAtR     = 1.0;
input double  InpPartialPercent = 50;
input bool    InpUseGiveback    = true;
input double  InpGivebackArmR   = 1.5;
input double  InpGivebackPct    = 50;

input group "=== Filters ==="
input bool    InpUseChopFilter  = true;
input int     InpADXPeriod      = 14;
input double  InpADXMin         = 18.0;
input bool    InpUseSession     = false;
input int     InpSessionStartHr = 7;
input int     InpSessionEndHr   = 21;

input group "=== Visuals ==="
input bool    InpDraw           = true;
input color   InpBullFVGColor   = C'10,60,30';
input color   InpBearFVGColor   = C'70,15,15';

//=== TYPES ==========================================================
struct FVG
{
   bool     used;
   bool     bullish;
   double   top, bottom;
   datetime born;
   ENUM_TIMEFRAMES tf;
   bool     mitigated;        // fully passed through
   string   objName;
};

struct SwingEngine
{
   int      depth;
   double   devPts;
   int      dir;
   double   candPrice;
   datetime candTime;
   int      lockedDir;
   bool     freshLock;
   int      freshLockDir;
   double   freshLockPrice;
   datetime freshLockTime;
};

// pending setup: pivot locked inside FVG, waiting for CISD
struct PendingSetup
{
   bool     active;
   bool     bullish;
   double   pivotPrice;
   datetime pivotTime;
   double   cisdLevel;        // close beyond this = trigger
   int      barsWaiting;
   int      fvgIdx;
};

CTrade trade;
SwingEngine E;
FVG    zones[];
PendingSetup PS;
int    hATR = INVALID_HANDLE, hADX = INVALID_HANDLE;
datetime lastBar = 0, curDay = 0;
int    tradesToday = 0;
double g_peakR = 0, g_riskPts = 0;
bool   g_partialDone = false;
int    g_seq = 0;

//=== SEMA ENGINE (same causal lock rule as XU_SEMA_NoRepaint_EA) ====
void EngineInit(SwingEngine &e, int depth, double devPts)
{
   e.depth = depth; e.devPts = devPts;
   e.dir = 0; e.candPrice = 0; e.candTime = 0;
   e.lockedDir = 0; e.freshLock = false;
}

void EngineUpdate(SwingEngine &e, int s)
{
   e.freshLock = false;
   double hi = iHigh(_Symbol, _Period, s);
   double lo = iLow(_Symbol, _Period, s);
   datetime bt = iTime(_Symbol, _Period, s);
   bool rawHigh = (iHighest(_Symbol, _Period, MODE_HIGH, e.depth, s) == s);
   bool rawLow  = (iLowest(_Symbol, _Period, MODE_LOW,  e.depth, s) == s);

   if(e.dir == 0)
   {
      if(rawHigh && !rawLow)      { e.dir = -1; e.candPrice = lo; e.candTime = bt; e.lockedDir = +1; }
      else if(rawLow && !rawHigh) { e.dir = +1; e.candPrice = hi; e.candTime = bt; e.lockedDir = -1; }
      return;
   }
   if(e.dir == +1)
   {
      if(hi > e.candPrice || e.candPrice == 0) { e.candPrice = hi; e.candTime = bt; }
      if(rawLow && bt > e.candTime && e.candPrice - lo > e.devPts * _Point)
      {
         e.lockedDir = +1; e.freshLock = true; e.freshLockDir = +1;
         e.freshLockPrice = e.candPrice; e.freshLockTime = e.candTime;
         e.dir = -1; e.candPrice = lo; e.candTime = bt;
      }
   }
   else
   {
      if(lo < e.candPrice || e.candPrice == 0) { e.candPrice = lo; e.candTime = bt; }
      if(rawHigh && bt > e.candTime && hi - e.candPrice > e.devPts * _Point)
      {
         e.lockedDir = -1; e.freshLock = true; e.freshLockDir = -1;
         e.freshLockPrice = e.candPrice; e.freshLockTime = e.candTime;
         e.dir = +1; e.candPrice = hi; e.candTime = bt;
      }
   }
}

//=== FVG ENGINE =====================================================
void ScanFVGs(ENUM_TIMEFRAMES tf)
{
   // scan recent closed HTF bars for 3-bar gaps
   int maxScan = 60;
   for(int i = 1; i <= maxScan; i++)
   {
      double lo0 = iLow(_Symbol, tf, i);
      double hi2 = iHigh(_Symbol, tf, i + 2);
      double hi0 = iHigh(_Symbol, tf, i);
      double lo2 = iLow(_Symbol, tf, i + 2);
      datetime t1 = iTime(_Symbol, tf, i + 1);
      if(t1 == 0) break;

      bool up = (lo0 > hi2) && (lo0 - hi2) / _Point >= InpFVG_MinPts;
      bool dn = (lo2 > hi0) && (lo2 - hi0) / _Point >= InpFVG_MinPts;
      if(!up && !dn) continue;

      double top = up ? lo0 : lo2;
      double bot = up ? hi2 : hi0;

      // already tracked?
      bool known = false;
      for(int z = 0; z < ArraySize(zones); z++)
         if(zones[z].used && zones[z].born == t1 && zones[z].tf == tf) { known = true; break; }
      if(known) continue;

      // mitigated already? (price fully traded through since birth)
      // quick check with current TF history: lowest low / highest high since t1
      int sinceBars = iBarShift(_Symbol, _Period, t1, false);
      if(sinceBars > 1)
      {
         int extIdx = up ? iLowest(_Symbol, _Period, MODE_LOW, sinceBars, 1)
                         : iHighest(_Symbol, _Period, MODE_HIGH, sinceBars, 1);
         if(up && iLow(_Symbol, _Period, extIdx) < bot) continue;   // bullish gap filled
         if(dn && iHigh(_Symbol, _Period, extIdx) > top) continue;  // bearish gap filled
      }

      // insert
      int slot = -1;
      for(int z = 0; z < ArraySize(zones); z++)
         if(!zones[z].used) { slot = z; break; }
      if(slot < 0)
      {
         if(ArraySize(zones) >= InpFVG_MaxZones)
         {  // recycle oldest
            int oldest = 0; datetime ot = LONG_MAX;
            for(int z = 0; z < ArraySize(zones); z++)
               if(zones[z].born < ot) { ot = zones[z].born; oldest = z; }
            if(InpDraw && zones[oldest].objName != "") ObjectDelete(0, zones[oldest].objName);
            slot = oldest;
         }
         else
         {
            slot = ArraySize(zones);
            ArrayResize(zones, slot + 1);
         }
      }
      zones[slot].used = true;
      zones[slot].bullish = up;
      zones[slot].top = top;
      zones[slot].bottom = bot;
      zones[slot].born = t1;
      zones[slot].tf = tf;
      zones[slot].mitigated = false;
      if(InpDraw)
      {
         g_seq++;
         string nm = StringFormat("SFC_fvg_%d", g_seq);
         zones[slot].objName = nm;
         ObjectCreate(0, nm, OBJ_RECTANGLE, 0, t1, bot,
                      t1 + PeriodSeconds(tf) * 40, top);
         ObjectSetInteger(0, nm, OBJPROP_COLOR, up ? InpBullFVGColor : InpBearFVGColor);
         ObjectSetInteger(0, nm, OBJPROP_FILL, true);
         ObjectSetInteger(0, nm, OBJPROP_BACK, true);
         ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
         ObjectSetString(0, nm, OBJPROP_TOOLTIP,
                         StringFormat("%s FVG %s", EnumToString(tf), up ? "bull" : "bear"));
      }
   }
}

void UpdateZones()
{
   double hi1 = iHigh(_Symbol, _Period, 1);
   double lo1 = iLow(_Symbol, _Period, 1);
   for(int z = 0; z < ArraySize(zones); z++)
   {
      if(!zones[z].used || zones[z].mitigated) continue;
      // expire old zones
      if(TimeCurrent() - zones[z].born > InpFVG_MaxAge * PeriodSeconds(zones[z].tf))
      { zones[z].mitigated = true; continue; }
      // mitigation: full pass-through
      if(zones[z].bullish && lo1 < zones[z].bottom) zones[z].mitigated = true;
      if(!zones[z].bullish && hi1 > zones[z].top)   zones[z].mitigated = true;
   }
}

// is price (bar 1) inside / tapping a live zone of given direction?
int ZoneTapped(bool bullish)
{
   double hi1 = iHigh(_Symbol, _Period, 1);
   double lo1 = iLow(_Symbol, _Period, 1);
   for(int z = 0; z < ArraySize(zones); z++)
   {
      if(!zones[z].used || zones[z].mitigated || zones[z].bullish != bullish) continue;
      if(bullish && lo1 <= zones[z].top && lo1 >= zones[z].bottom) return z;
      if(!bullish && hi1 >= zones[z].bottom && hi1 <= zones[z].top) return z;
   }
   return -1;
}

// nearest opposing zone edge for TP
double NextOpposingZone(bool isBuy, double from)
{
   double best = 0;
   for(int z = 0; z < ArraySize(zones); z++)
   {
      if(!zones[z].used || zones[z].mitigated) continue;
      if(isBuy && !zones[z].bullish && zones[z].bottom > from)
         if(best == 0 || zones[z].bottom < best) best = zones[z].bottom;
      if(!isBuy && zones[z].bullish && zones[z].top < from)
         if(best == 0 || zones[z].top > best) best = zones[z].top;
   }
   return best;
}

//=== CISD ===========================================================
// level to close beyond: open of the delivery sequence into the pivot.
// bullish: find consecutive bearish candles ending at/near the pivot bar,
// CISD level = open of the first candle of that bearish run.
double CISDLevel(bool bullish, datetime pivotTime)
{
   int p = iBarShift(_Symbol, _Period, pivotTime, false);
   if(p < 0) return 0;
   int i = p;
   // walk back while candles deliver INTO the pivot (bearish for a low)
   int steps = 0;
   double level = 0;
   while(steps < InpCISD_MaxSeq)
   {
      double o = iOpen(_Symbol, _Period, i);
      double c = iClose(_Symbol, _Period, i);
      bool delivering = bullish ? (c < o) : (c > o);
      if(!delivering) break;
      level = o;
      i++; steps++;
   }
   if(level == 0)   // pivot bar itself wasn't a delivery candle; use its open
      level = iOpen(_Symbol, _Period, p);
   return level;
}

//=== HELPERS ========================================================
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
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0) return NormalizeLot(InpFixedLot);
   double money = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   double perLot = slPts * _Point / ts * tv;
   return (perLot > 0) ? NormalizeLot(money / perLot) : NormalizeLot(InpFixedLot);
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

//=== TRADING ========================================================
void Enter(bool isBuy)
{
   long t; ulong tk;
   if(HavePosition(t, tk)) return;
   if(tradesToday >= InpMaxTradesPerDay) return;
   if(!SessionOK() || !SpreadOK()) return;
   if(InpUseChopFilter && ADXValue() < InpADXMin)
   { Print("SFC: entry skipped, ADX=", DoubleToString(ADXValue(), 1)); return; }

   double atr = ATRValue();
   if(atr <= 0) return;
   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = isBuy ? PS.pivotPrice - InpSL_ATRBuffer * atr
                     : PS.pivotPrice + InpSL_ATRBuffer * atr;
   double slPts = MathAbs(price - sl) / _Point;
   if(slPts <= 0) return;

   double tp = isBuy ? price + InpTP_RR * slPts * _Point
                     : price - InpTP_RR * slPts * _Point;
   double opp = NextOpposingZone(isBuy, price);
   if(opp > 0)
   {
      double rrOpp = MathAbs(opp - price) / (slPts * _Point);
      if(rrOpp >= InpMinRR && rrOpp < InpTP_RR)
         tp = opp;   // take the closer, still-worthwhile structural target
      else if(rrOpp < InpMinRR)
      { Print("SFC: rejected — opposing FVG too close (RR ", DoubleToString(rrOpp, 2), ")"); return; }
   }
   double lot = CalcLot(slPts);
   bool ok = isBuy ? trade.Buy(lot, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), "SFC Buy")
                   : trade.Sell(lot, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits), "SFC Sell");
   if(ok)
   {
      tradesToday++;
      g_peakR = 0; g_riskPts = slPts; g_partialDone = false;
      if(InpDraw)
      {
         g_seq++;
         string nm = StringFormat("SFC_entry_%d", g_seq);
         ObjectCreate(0, nm, OBJ_ARROW, 0, TimeCurrent(), price);
         ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, isBuy ? 233 : 234);
         ObjectSetInteger(0, nm, OBJPROP_COLOR, isBuy ? clrLime : clrRed);
         ObjectSetInteger(0, nm, OBJPROP_WIDTH, 3);
      }
   }
}

void Manage()
{
   long type; ulong tk;
   if(!HavePosition(type, tk)) return;
   bool isBuy = (type == POSITION_TYPE_BUY);
   double op = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl = PositionGetDouble(POSITION_SL);
   double tp = PositionGetDouble(POSITION_TP);
   double vol = PositionGetDouble(POSITION_VOLUME);
   double cur = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                      : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(g_riskPts <= 0) g_riskPts = (sl > 0) ? MathAbs(op - sl) / _Point : 0;
   if(g_riskPts <= 0) return;
   double r = (isBuy ? cur - op : op - cur) / _Point / g_riskPts;
   if(r > g_peakR) g_peakR = r;

   if(InpUseGiveback && g_peakR >= InpGivebackArmR && r < g_peakR * InpGivebackPct / 100.0)
   {
      if(trade.PositionClose(tk, InpSlippagePoints))
         Print("SFC: giveback close peak=", DoubleToString(g_peakR, 2), "R");
      g_peakR = 0; g_riskPts = 0;
      return;
   }
   if(InpUsePartial && !g_partialDone && r >= InpPartialAtR)
   {
      double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double cv = NormalizeLot(vol * InpPartialPercent / 100.0);
      if(cv >= mn && vol - cv >= mn)
      {
         if(trade.PositionClosePartial(tk, cv)) g_partialDone = true;
      }
      else g_partialDone = true;
   }
   if(InpUseBreakeven && r >= InpBE_TriggerR)
   {
      double be = isBuy ? op + 2 * _Point : op - 2 * _Point;
      bool needs = isBuy ? (sl < be - _Point) : (sl == 0 || sl > be + _Point);
      if(needs) trade.PositionModify(tk, NormalizeDouble(be, _Digits), tp);
   }
}

//=== LIFECYCLE ======================================================
int OnInit()
{
   EngineInit(E, InpDepth, InpDeviationPts);
   hATR = iATR(_Symbol, _Period, InpATRPeriod);
   hADX = iADX(_Symbol, _Period, InpADXPeriod);
   if(hATR == INVALID_HANDLE || hADX == INVALID_HANDLE) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   PS.active = false;
   curDay = TimeCurrent() - (TimeCurrent() % 86400);
   // warm SEMA
   int warm = (int)MathMin(1000, Bars(_Symbol, _Period) - InpDepth - 2);
   for(int s = warm; s >= 2; s--) EngineUpdate(E, s);
   lastBar = iTime(_Symbol, _Period, 0);
   Print("SEMA+FVG+CISD Confluence EA ready.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Comment("");
   ObjectsDeleteAll(0, "SFC_");
   if(hATR != INVALID_HANDLE) IndicatorRelease(hATR);
   if(hADX != INVALID_HANDLE) IndicatorRelease(hADX);
}

void OnTick()
{
   Manage();

   datetime cb = iTime(_Symbol, _Period, 0);
   if(cb == lastBar) return;
   lastBar = cb;

   datetime ds = TimeCurrent() - (TimeCurrent() % 86400);
   if(ds != curDay) { curDay = ds; tradesToday = 0; }

   if(InpFVG_Use1) ScanFVGs(InpFVG_TF1);
   if(InpFVG_Use2) ScanFVGs(InpFVG_TF2);
   if(InpFVG_Use3) ScanFVGs(InpFVG_TF3);
   UpdateZones();

   EngineUpdate(E, 1);

   // STAGE 1: pivot locks while price is tapping a matching HTF FVG
   if(E.freshLock)
   {
      bool isBuy = (E.freshLockDir == -1);
      int z = ZoneTapped(isBuy);
      // also accept if the PIVOT ITSELF sat inside the zone
      if(z < 0)
      {
         for(int k = 0; k < ArraySize(zones); k++)
         {
            if(!zones[k].used || zones[k].mitigated || zones[k].bullish != isBuy) continue;
            if(E.freshLockPrice >= zones[k].bottom && E.freshLockPrice <= zones[k].top)
            { z = k; break; }
         }
      }
      if(z >= 0)
      {
         PS.active = true;
         PS.bullish = isBuy;
         PS.pivotPrice = E.freshLockPrice;
         PS.pivotTime = E.freshLockTime;
         PS.barsWaiting = 0;
         PS.fvgIdx = z;
         PS.cisdLevel = InpUseCISD ? CISDLevel(isBuy, E.freshLockTime) : 0;
         if(InpDraw)
         {
            g_seq++;
            string nm = StringFormat("SFC_pivot_%d", g_seq);
            ObjectCreate(0, nm, OBJ_ARROW, 0, PS.pivotTime, PS.pivotPrice);
            ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, 159);
            ObjectSetInteger(0, nm, OBJPROP_COLOR, isBuy ? clrLime : clrRed);
            ObjectSetInteger(0, nm, OBJPROP_WIDTH, 3);
            if(InpUseCISD && PS.cisdLevel > 0)
            {
               g_seq++;
               string ln = StringFormat("SFC_cisd_%d", g_seq);
               ObjectCreate(0, ln, OBJ_TREND, 0, PS.pivotTime, PS.cisdLevel,
                            PS.pivotTime + 20 * PeriodSeconds(_Period), PS.cisdLevel);
               ObjectSetInteger(0, ln, OBJPROP_COLOR, clrYellow);
               ObjectSetInteger(0, ln, OBJPROP_STYLE, STYLE_DASH);
            }
         }
         Print("SFC: setup armed — ", isBuy ? "BULL" : "BEAR",
               " pivot in ", EnumToString(zones[z].tf), " FVG",
               InpUseCISD ? StringFormat(", CISD level %.5f", PS.cisdLevel) : "");
         if(!InpUseCISD)
         {
            Enter(PS.bullish);
            PS.active = false;
         }
      }
   }

   // STAGE 2: CISD confirmation
   if(PS.active && InpUseCISD)
   {
      PS.barsWaiting++;
      double cl1 = iClose(_Symbol, _Period, 1);
      bool confirmed = PS.bullish ? (cl1 > PS.cisdLevel) : (cl1 < PS.cisdLevel);
      // invalidation: zone got mitigated or price broke the pivot
      bool dead = zones[PS.fvgIdx].mitigated ||
                  (PS.bullish && cl1 < PS.pivotPrice) ||
                  (!PS.bullish && cl1 > PS.pivotPrice) ||
                  PS.barsWaiting > InpCISD_MaxBars;
      if(confirmed)
      {
         Enter(PS.bullish);
         PS.active = false;
      }
      else if(dead)
      {
         Print("SFC: setup expired/invalidated");
         PS.active = false;
      }
   }

   int live = 0;
   for(int z = 0; z < ArraySize(zones); z++)
      if(zones[z].used && !zones[z].mitigated) live++;
   Comment(StringFormat("SEMA+FVG+CISD EA\nLive HTF FVGs: %d\nPending setup: %s\nADX: %.1f\nTrades today: %d/%d",
           live, PS.active ? (PS.bullish ? "BULL awaiting CISD" : "BEAR awaiting CISD") : "none",
           ADXValue(), tradesToday, InpMaxTradesPerDay));
}
