//+------------------------------------------------------------------+
//|                                         XU_SEMA_QTheory_EA.mq5   |
//|   Quarterly Theory + non-repainting SEMA — full trading system    |
//|                                                                    |
//|  Model (from your XU_SEMA_QFib_TZ_VP indicator, made tradeable):  |
//|   * Daily session is split by Fibonacci Time Zones:                |
//|       -4 = 00:00, -2.5 = 04:30, -2 = 06:00, 0 = 12:00,            |
//|       +2 = 18:00, +2.5 = 19:30, +4 = 24:00 (server time)          |
//|     -> regimes Omega, Beta, Gamma, Delta, Epsilon, Zeta.           |
//|   * Two anchor candles define the daily dealing ranges:            |
//|       Slot A = the 00:15 M15 candle, Slot B = the 06:15 M15.      |
//|     Their BODY projects QFib levels 0 / ±2.0 / ±2.5 / ±4.0.       |
//|                                                                    |
//|  ENTRY LOGIC (drafted for best conditions, ICT/SMC style):         |
//|   BUY  when, inside an allowed regime window:                      |
//|    1. price SWEEPS a QFib level below (wick below, closes back     |
//|       above within N bars)  -> liquidity purge,                    |
//|    2. then a non-repainting SEMA swing LOW locks (confirmed        |
//|       structure shift off the sweep),                              |
//|    3. optional alignment with the anchor candle's direction.       |
//|   SELL is the mirror image.                                        |
//|   SL  = beyond the sweep extreme (x ATR buffer).                   |
//|   TP  = next opposing QFib level, capped/floored by MinRR-MaxRR,   |
//|         plus breakeven + giveback protection.                      |
//|                                                                    |
//|  Everything is drawn: fib levels, regime shading, sweep markers,   |
//|  locked pivots, entries. No iCustom — fully self-contained.        |
//+------------------------------------------------------------------+
#property copyright "Techtoxic + Claude Fable 5, 2026"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//=== INPUTS =========================================================
input group "=== Quarterly Anchors ==="
input int     InpSlotAHour      = 0;     // Anchor A hour (server)
input int     InpSlotAMin       = 15;    // Anchor A minute
input int     InpSlotBHour      = 6;     // Anchor B hour
input int     InpSlotBMin       = 15;    // Anchor B minute
input bool    InpUseSlotA       = true;
input bool    InpUseSlotB       = true;
input bool    InpAlignWithAnchor= false; // Only trade in anchor-candle direction

input group "=== Trading Windows (regimes) ==="
input bool    InpTradeOmega     = false; // 00:00-04:30
input bool    InpTradeBeta      = false; // 04:30-06:00
input bool    InpTradeGamma     = true;  // 06:00-12:00
input bool    InpTradeDelta     = true;  // 12:00-18:00
input bool    InpTradeEpsilon   = false; // 18:00-19:30
input bool    InpTradeZeta      = false; // 19:30-24:00

input group "=== Sweep Detection ==="
input int     InpSweepWindow    = 12;    // Bars allowed between sweep and reclaim
input int     InpPivotWindow    = 20;    // Bars allowed between sweep and SEMA lock
input double  InpSweepMinPts    = 0;     // Min penetration depth in points (0=any)

input group "=== SEMA Engine ==="
input int     InpDepth          = 12;    // ZigZag depth (SEMA1)
input int     InpDeviationPts   = 5;

input group "=== Risk ==="
input double  InpRiskPercent    = 1.0;   // Risk % (0 = fixed lot)
input double  InpFixedLot       = 0.10;
input long    InpMagic          = 224403;
input int     InpSlippagePoints = 30;
input int     InpMaxTradesPerDay= 3;

input group "=== Stops & Targets ==="
input double  InpSL_ATRBuffer   = 0.5;   // SL = sweep extreme +/- x*ATR
input int     InpATRPeriod      = 14;
input double  InpMinRR          = 1.5;   // Reject setups offering less than this to target
input double  InpMaxRR          = 4.0;   // Cap TP at this RR
input bool    InpUseBreakeven   = true;
input double  InpBE_TriggerR    = 1.0;
input bool    InpUseGiveback    = true;
input double  InpGivebackArmR   = 1.5;
input double  InpGivebackPct    = 50;

input group "=== Visuals ==="
input bool    InpDraw           = true;
input color   InpBullFibColor   = clrDodgerBlue;
input color   InpBearFibColor   = clrOrangeRed;

//=== STRUCTS ========================================================
struct AnchorSet
{
   bool     valid;
   bool     bullish;
   datetime time;
   double   bodyO, bodyC;        // anchor body
   double   levels[8];           // +0,+2,+2.5,+4, -0(=opposite body edge),-2,-2.5,-4
   int      nLevels;
};

struct SweepState
{
   bool     armed;               // sweep happened, waiting for pivot lock
   bool     bullish;             // true: swept a low level -> hunting BUY
   double   extreme;             // sweep wick extreme
   double   sweptLevel;
   datetime when;
   int      barsSince;
};

// non-repainting SEMA engine (same lock rule as XU_SEMA_NoRepaint_EA)
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

CTrade trade;
SwingEngine E;
AnchorSet A, B;
SweepState SW;
int    hATR = INVALID_HANDLE;
datetime lastBar = 0, curDay = 0;
int    tradesToday = 0;
double g_peakR = 0, g_riskPts = 0;
int    g_seq = 0;

double FibMults[4] = {0.0, 2.0, 2.5, 4.0};

//=== SEMA ENGINE ====================================================
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

//=== TIME / REGIME ==================================================
datetime DayStart(datetime t)
{
   MqlDateTime dt; TimeToStruct(t, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
}

// minutes from midnight of the regime boundaries
int RegimeOf(datetime t)
{
   int m = (int)((t - DayStart(t)) / 60);
   if(m <  270) return 0;   // Omega   00:00-04:30
   if(m <  360) return 1;   // Beta    04:30-06:00
   if(m <  720) return 2;   // Gamma   06:00-12:00
   if(m < 1080) return 3;   // Delta   12:00-18:00
   if(m < 1170) return 4;   // Epsilon 18:00-19:30
   return 5;                // Zeta    19:30-24:00
}

bool RegimeAllowed(int r)
{
   switch(r)
   {
      case 0: return InpTradeOmega;
      case 1: return InpTradeBeta;
      case 2: return InpTradeGamma;
      case 3: return InpTradeDelta;
      case 4: return InpTradeEpsilon;
      case 5: return InpTradeZeta;
   }
   return false;
}

//=== ANCHORS ========================================================
void BuildAnchor(AnchorSet &a, int hr, int mn)
{
   a.valid = false;
   datetime ds = DayStart(TimeCurrent());
   datetime target = ds + hr * 3600 + mn * 60;
   if(TimeCurrent() < target + PeriodSeconds(PERIOD_M15)) return; // candle not closed yet
   int idx = iBarShift(_Symbol, PERIOD_M15, target, true);
   if(idx < 0) return;
   double o = iOpen(_Symbol, PERIOD_M15, idx);
   double c = iClose(_Symbol, PERIOD_M15, idx);
   if(MathAbs(c - o) < _Point) return;     // doji — indicator skips these too
   a.valid = true;
   a.bullish = (c >= o);
   a.time = target;
   a.bodyO = o; a.bodyC = c;
   double body = c - o;                    // signed
   a.nLevels = 0;
   for(int i = 0; i < 4; i++)
   {  // positive side: close + mult*body ; negative side: open - mult*body
      a.levels[a.nLevels++] = c + FibMults[i] * body;
      if(FibMults[i] > 0)
         a.levels[a.nLevels++] = o - FibMults[i] * body;
   }
   // also include the open itself (0 on negative side)
   a.levels[a.nLevels++] = o;
}

void DrawAnchor(AnchorSet &a, string tag)
{
   if(!InpDraw || !a.valid) return;
   color clr = a.bullish ? InpBullFibColor : InpBearFibColor;
   datetime ds = DayStart(TimeCurrent());
   string dtag = TimeToString(ds, TIME_DATE);
   for(int i = 0; i < a.nLevels; i++)
   {
      string nm = StringFormat("QT_%s_%s_L%d", dtag, tag, i);
      if(ObjectFind(0, nm) >= 0) continue;
      ObjectCreate(0, nm, OBJ_TREND, 0, a.time, a.levels[i], ds + 86400, a.levels[i]);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, nm, OBJPROP_STYLE, i == 0 ? STYLE_SOLID : STYLE_DOT);
      ObjectSetInteger(0, nm, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, nm, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
   }
   string box = StringFormat("QT_%s_%s_box", dtag, tag);
   if(ObjectFind(0, box) < 0)
   {
      ObjectCreate(0, box, OBJ_RECTANGLE, 0, a.time, a.bodyO,
                   a.time + PeriodSeconds(PERIOD_M15), a.bodyC);
      ObjectSetInteger(0, box, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, box, OBJPROP_FILL, true);
      ObjectSetInteger(0, box, OBJPROP_BACK, true);
      ObjectSetInteger(0, box, OBJPROP_SELECTABLE, false);
   }
}

//=== SWEEP DETECTION ================================================
// On each closed bar: did price wick through a QFib level and close back?
void DetectSweep()
{
   double hi1 = iHigh(_Symbol, _Period, 1);
   double lo1 = iLow(_Symbol, _Period, 1);
   double cl1 = iClose(_Symbol, _Period, 1);

   AnchorSet sets[2];
   sets[0] = A; sets[1] = B;
   for(int s = 0; s < 2; s++)
   {
      if(!sets[s].valid) continue;
      if((s == 0 && !InpUseSlotA) || (s == 1 && !InpUseSlotB)) continue;
      for(int i = 0; i < sets[s].nLevels; i++)
      {
         double lv = sets[s].levels[i];
         // bullish sweep: wick below level, close back above
         if(lo1 < lv - InpSweepMinPts * _Point && cl1 > lv)
         {
            SW.armed = true; SW.bullish = true;
            SW.extreme = lo1; SW.sweptLevel = lv;
            SW.when = iTime(_Symbol, _Period, 1); SW.barsSince = 0;
            MarkSweep(lv, true);
         }
         // bearish sweep: wick above level, close back below
         if(hi1 > lv + InpSweepMinPts * _Point && cl1 < lv)
         {
            SW.armed = true; SW.bullish = false;
            SW.extreme = hi1; SW.sweptLevel = lv;
            SW.when = iTime(_Symbol, _Period, 1); SW.barsSince = 0;
            MarkSweep(lv, false);
         }
      }
   }
}

void MarkSweep(double lv, bool bull)
{
   if(!InpDraw) return;
   g_seq++;
   string nm = StringFormat("QT_sweep_%d", g_seq);
   ObjectCreate(0, nm, OBJ_ARROW, 0, iTime(_Symbol, _Period, 1),
                bull ? iLow(_Symbol, _Period, 1) : iHigh(_Symbol, _Period, 1));
   ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, 251); // x
   ObjectSetInteger(0, nm, OBJPROP_COLOR, bull ? clrAqua : clrViolet);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, nm, OBJPROP_ANCHOR, bull ? ANCHOR_TOP : ANCHOR_BOTTOM);
}

//=== TARGET SELECTION ===============================================
double NextOpposingLevel(bool isBuy, double from)
{
   double best = 0;
   AnchorSet sets[2];
   sets[0] = A; sets[1] = B;
   for(int s = 0; s < 2; s++)
   {
      if(!sets[s].valid) continue;
      for(int i = 0; i < sets[s].nLevels; i++)
      {
         double lv = sets[s].levels[i];
         if(isBuy && lv > from + _Point)
            if(best == 0 || lv < best) best = lv;
         if(!isBuy && lv < from - _Point && lv > 0)
            if(best == 0 || lv > best) best = lv;
      }
   }
   return best;
}

//=== HELPERS ========================================================
double ATRValue() { double a[1]; return (CopyBuffer(hATR, 0, 1, 1, a) == 1) ? a[0] : 0; }

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

//=== ENTRY ==========================================================
void TryEnter(bool isBuy)
{
   long t; ulong tk;
   if(HavePosition(t, tk)) return;
   if(tradesToday >= InpMaxTradesPerDay) return;
   if(!RegimeAllowed(RegimeOf(TimeCurrent()))) return;

   // anchor direction alignment (optional)
   if(InpAlignWithAnchor)
   {
      AnchorSet act = B.valid ? B : A;
      if(act.valid && act.bullish != isBuy) return;
   }

   double atr = ATRValue();
   if(atr <= 0) return;
   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = isBuy ? SW.extreme - InpSL_ATRBuffer * atr
                     : SW.extreme + InpSL_ATRBuffer * atr;
   double slPts = MathAbs(price - sl) / _Point;
   if(slPts <= 0) return;

   // target: next opposing QFib level, bounded by MinRR..MaxRR
   double tgt = NextOpposingLevel(isBuy, price);
   double rr = 0;
   if(tgt > 0)
      rr = MathAbs(tgt - price) / (slPts * _Point);
   if(tgt == 0 || rr > InpMaxRR)
   {  // no level or absurdly far -> cap at MaxRR
      tgt = isBuy ? price + InpMaxRR * slPts * _Point
                  : price - InpMaxRR * slPts * _Point;
      rr = InpMaxRR;
   }
   if(rr < InpMinRR)
   {
      Print("QT: setup rejected, RR to next level only ", DoubleToString(rr, 2));
      return;
   }
   double lot = CalcLot(slPts);
   bool ok = isBuy ? trade.Buy(lot, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tgt, _Digits), "QT Buy")
                   : trade.Sell(lot, _Symbol, 0, NormalizeDouble(sl, _Digits), NormalizeDouble(tgt, _Digits), "QT Sell");
   if(ok)
   {
      tradesToday++;
      g_peakR = 0; g_riskPts = slPts;
      if(InpDraw)
      {
         g_seq++;
         string nm = StringFormat("QT_entry_%d", g_seq);
         ObjectCreate(0, nm, OBJ_ARROW, 0, TimeCurrent(), price);
         ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, isBuy ? 233 : 234);
         ObjectSetInteger(0, nm, OBJPROP_COLOR, isBuy ? clrLime : clrRed);
         ObjectSetInteger(0, nm, OBJPROP_WIDTH, 3);
      }
      Print("QT: ", isBuy ? "BUY" : "SELL", " sweep@", DoubleToString(SW.sweptLevel, _Digits),
            " SL=", DoubleToString(sl, _Digits), " TP=", DoubleToString(tgt, _Digits),
            " RR=", DoubleToString(rr, 2));
   }
}

//=== MANAGEMENT =====================================================
void Manage()
{
   long type; ulong tk;
   if(!HavePosition(type, tk)) return;
   bool isBuy = (type == POSITION_TYPE_BUY);
   double op = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl = PositionGetDouble(POSITION_SL);
   double tp = PositionGetDouble(POSITION_TP);
   double cur = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                      : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(g_riskPts <= 0) g_riskPts = (sl > 0) ? MathAbs(op - sl) / _Point : 0;
   if(g_riskPts <= 0) return;
   double r = (isBuy ? cur - op : op - cur) / _Point / g_riskPts;
   if(r > g_peakR) g_peakR = r;

   if(InpUseGiveback && g_peakR >= InpGivebackArmR && r < g_peakR * InpGivebackPct / 100.0)
   {
      if(trade.PositionClose(tk, InpSlippagePoints))
         Print("QT: giveback close peak=", DoubleToString(g_peakR, 2), "R now=", DoubleToString(r, 2), "R");
      g_peakR = 0; g_riskPts = 0;
      return;
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
   if(hATR == INVALID_HANDLE) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   SW.armed = false;
   curDay = DayStart(TimeCurrent());
   lastBar = iTime(_Symbol, _Period, 0);
   // warm SEMA engine
   int warm = (int)MathMin(1000, Bars(_Symbol, _Period) - InpDepth - 2);
   for(int s = warm; s >= 2; s--) EngineUpdate(E, s);
   Print("QTheory+SEMA EA ready.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Comment("");
   ObjectsDeleteAll(0, "QT_");
   if(hATR != INVALID_HANDLE) IndicatorRelease(hATR);
}

void OnTick()
{
   Manage();

   datetime cb = iTime(_Symbol, _Period, 0);
   if(cb == lastBar) return;
   lastBar = cb;

   // new day: reset anchors + counters
   datetime ds = DayStart(TimeCurrent());
   if(ds != curDay)
   {
      curDay = ds; tradesToday = 0;
      A.valid = false; B.valid = false; SW.armed = false;
   }
   if(InpUseSlotA && !A.valid) { BuildAnchor(A, InpSlotAHour, InpSlotAMin); DrawAnchor(A, "A"); }
   if(InpUseSlotB && !B.valid) { BuildAnchor(B, InpSlotBHour, InpSlotBMin); DrawAnchor(B, "B"); }

   EngineUpdate(E, 1);

   // sweep ages out
   if(SW.armed)
   {
      SW.barsSince++;
      if(SW.barsSince > InpPivotWindow) SW.armed = false;
   }
   DetectSweep();

   // confluence: armed sweep + fresh locked SEMA pivot in the same direction
   if(SW.armed && E.freshLock)
   {
      bool pivotIsBuy = (E.freshLockDir == -1);
      if(pivotIsBuy == SW.bullish && E.freshLockTime >= SW.when - PeriodSeconds(_Period))
      {
         TryEnter(SW.bullish);
         SW.armed = false;
      }
   }

   string reg[6] = {"Omega 00:00-04:30", "Beta 04:30-06:00", "Gamma 06:00-12:00",
                    "Delta 12:00-18:00", "Epsilon 18:00-19:30", "Zeta 19:30-24:00"};
   int r = RegimeOf(TimeCurrent());
   Comment(StringFormat("QTheory+SEMA EA\nRegime: %s (%s)\nAnchor A: %s   Anchor B: %s\nSweep armed: %s\nTrades today: %d/%d",
           reg[r], RegimeAllowed(r) ? "TRADING" : "blocked",
           A.valid ? (A.bullish ? "bull" : "bear") : "n/a",
           B.valid ? (B.bullish ? "bull" : "bear") : "n/a",
           SW.armed ? (SW.bullish ? "BULL" : "BEAR") : "no",
           tradesToday, InpMaxTradesPerDay));
}
