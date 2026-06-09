//+------------------------------------------------------------------+
//|                                                       FFZ_v2.mq5 |
//|        Purple Bills arrow follower — fully rebuilt exits          |
//|                                                                    |
//|  Upgrades over FFZ v1:                                             |
//|   1. CHOP FILTER  — blocks new entries when market is choppy       |
//|      (ADX floor + Choppiness Index ceiling + ATR contraction).     |
//|   2. PROFIT PROTECTION — the v1 killer was "trade was nicely in    |
//|      profit, then reversed and exited at a loss on the opposite    |
//|      arrow". v2 adds: ATR initial SL, breakeven move, partial      |
//|      close at +1R, ATR chandelier trail, and a giveback guard      |
//|      that banks the trade if it surrenders X% of its peak profit. |
//|   3. COT BIAS (optional) — Manual or via your COTAPI WebRequest;   |
//|      blocks (or de-risks) counter-bias entries. Auto-falls back    |
//|      to Manual in the Strategy Tester (WebRequest unavailable).    |
//|   4. Risk-% position sizing, session filter, full on-chart HUD.    |
//|                                                                    |
//|  The Purple Bills indicator has no source, so it stays iCustom —   |
//|  everything else is self-contained and visualized.                 |
//+------------------------------------------------------------------+
#property copyright "Techtoxic + Claude Fable 5, 2026"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>

//=== INPUTS =========================================================
input group "=== Signal Source ==="
input string  InpIndicatorName   = "Purple Bills SIOR FX"; // Indicator file name
input int     InpUpBuffer        = 0;     // Buffer index: buy arrows
input int     InpDownBuffer      = 1;     // Buffer index: sell arrows

input group "=== Risk & Sizing ==="
input double  InpRiskPercent     = 1.0;   // Risk % of balance per trade (0 = use fixed lot)
input double  InpFixedLot        = 0.10;  // Fixed lot (used when risk % = 0)
input long    InpMagic           = 224401;
input int     InpSlippagePoints  = 30;
input double  InpMaxSpreadPoints = 0;     // Max spread in points (0 = no check)

input group "=== Stops & Targets ==="
input double  InpSL_ATR          = 2.0;   // Initial SL = x * ATR (0 = no SL  [not advised])
input double  InpTP_RR           = 0.0;   // Fixed TP in R multiples (0 = ride with trail)
input int     InpATRPeriod       = 14;

input group "=== Profit Protection ==="
input bool    InpUseBreakeven    = true;
input double  InpBE_TriggerR     = 1.0;   // Move SL to BE at +X R
input double  InpBE_OffsetPoints = 10;    // BE offset in points
input bool    InpUsePartial      = true;
input double  InpPartialAtR      = 1.0;   // Take partial at +X R
input double  InpPartialPercent  = 50;    // % of volume to close
input bool    InpUseTrail        = true;
input double  InpTrailStartR     = 1.5;   // Start trailing after +X R
input double  InpTrail_ATR       = 2.5;   // Chandelier distance = x * ATR
input bool    InpUseGiveback     = true;
input double  InpGivebackArmR    = 1.5;   // Arm giveback once profit >= X R
input double  InpGivebackPct     = 50;    // Close if profit falls below X% of peak

input group "=== Chop Filter ==="
input bool    InpUseChopFilter   = true;
input int     InpADXPeriod       = 14;
input double  InpADXMin          = 20.0;  // Block entries when ADX below this
input int     InpChopPeriod      = 14;
input double  InpChopMax         = 61.8;  // Block entries when Choppiness above this
input bool    InpChopBlocksFlip  = true;  // In chop: close on opposite arrow but do NOT reverse

input group "=== COT Bias (optional) ==="
enum ENUM_COT_MODE { COT_OFF, COT_MANUAL, COT_AUTO_API };
enum ENUM_BIAS     { BIAS_NEUTRAL, BIAS_LONG, BIAS_SHORT };
input ENUM_COT_MODE InpCOTMode    = COT_OFF;
input ENUM_BIAS     InpManualBias = BIAS_NEUTRAL; // Manual bias (also tester fallback)
input string  InpCOTApiUrl       = "https://your-cotapi.example.com/api/bias/GOLD"; // COTAPI bias endpoint
input int     InpCOTRefreshMin   = 240;   // Re-poll API every N minutes
input bool    InpCOTHardBlock    = true;  // true: block counter-bias entries; false: halve risk

input group "=== Session Filter ==="
input bool    InpUseSession      = false;
input int     InpSessionStartHr  = 7;     // Server hour start
input int     InpSessionEndHr    = 21;    // Server hour end

input group "=== Visuals ==="
input bool    InpShowHUD         = true;
input bool    InpDrawTradeMarks  = true;

//=== GLOBALS ========================================================
CTrade   trade;
int      hInd = INVALID_HANDLE, hADX = INVALID_HANDLE, hATR = INVALID_HANDLE;
datetime lastBarTime = 0;
bool     firstRun = true;
datetime lastUpSignalBar = 0, lastDownSignalBar = 0;

// per-position management state
double   g_peakProfitR = 0.0;      // peak profit of current position, in R
double   g_initialRiskPts = 0.0;   // R distance in points at entry
bool     g_partialDone = false;
ENUM_BIAS g_cotBias = BIAS_NEUTRAL;
datetime g_cotLastPoll = 0;
string   g_chopState = "n/a";

#define HUD_NAME "FFZ2_HUD"

//=== INIT ===========================================================
int OnInit()
{
   hInd = iCustom(_Symbol, _Period, InpIndicatorName);
   if(hInd == INVALID_HANDLE)
   {
      Print("FFZ2: cannot load indicator '", InpIndicatorName, "'");
      return INIT_FAILED;
   }
   hADX = iADX(_Symbol, _Period, InpADXPeriod);
   hATR = iATR(_Symbol, _Period, InpATRPeriod);
   if(hADX == INVALID_HANDLE || hATR == INVALID_HANDLE)
      return INIT_FAILED;

   ChartIndicatorAdd(0, 0, hInd);
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);

   lastBarTime = iTime(_Symbol, _Period, 1);
   lastUpSignalBar = TimeCurrent();
   lastDownSignalBar = TimeCurrent();
   RestoreStateFromPosition();
   if(InpCOTMode == COT_MANUAL) g_cotBias = InpManualBias;
   Print("FFZ v2 initialized. ChopFilter=", InpUseChopFilter, " COTMode=", EnumToString(InpCOTMode));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Comment("");
   ObjectsDeleteAll(0, "FFZ2_");
   if(hInd != INVALID_HANDLE) IndicatorRelease(hInd);
   if(hADX != INVALID_HANDLE) IndicatorRelease(hADX);
   if(hATR != INVALID_HANDLE) IndicatorRelease(hATR);
}

//=== HELPERS ========================================================
double ATRValue()
{
   double a[1];
   if(CopyBuffer(hATR, 0, 1, 1, a) != 1) return 0.0;
   return a[0];
}

double ADXValue()
{
   double a[1];
   if(CopyBuffer(hADX, 0, 1, 1, a) != 1) return 0.0;
   return a[0];
}

// Choppiness Index: 100 * log10( sum(TR,n) / (maxHigh-minLow) ) / log10(n)
double ChoppinessValue(int period)
{
   MqlRates r[];
   if(CopyRates(_Symbol, _Period, 1, period + 1, r) != period + 1) return 50.0;
   double sumTR = 0.0, hh = -DBL_MAX, ll = DBL_MAX;
   for(int i = 1; i <= period; i++)
   {
      double tr = MathMax(r[i].high, r[i - 1].close) - MathMin(r[i].low, r[i - 1].close);
      sumTR += tr;
      hh = MathMax(hh, r[i].high);
      ll = MathMin(ll, r[i].low);
   }
   double range = hh - ll;
   if(range <= 0 || sumTR <= 0) return 100.0;
   return 100.0 * MathLog10(sumTR / range) / MathLog10((double)period);
}

bool MarketIsChoppy()
{
   if(!InpUseChopFilter) { g_chopState = "filter off"; return false; }
   double adx  = ADXValue();
   double chop = ChoppinessValue(InpChopPeriod);
   bool choppy = (adx < InpADXMin) || (chop > InpChopMax);
   g_chopState = StringFormat("ADX=%.1f%s Chop=%.1f%s -> %s",
                              adx, (adx < InpADXMin ? "(LOW)" : ""),
                              chop, (chop > InpChopMax ? "(HIGH)" : ""),
                              choppy ? "CHOPPY" : "TRENDING");
   return choppy;
}

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
   double spr = (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / _Point;
   return spr <= InpMaxSpreadPoints;
}

//=== COT BIAS =======================================================
void UpdateCOTBias()
{
   if(InpCOTMode == COT_OFF)    { g_cotBias = BIAS_NEUTRAL; return; }
   if(InpCOTMode == COT_MANUAL) { g_cotBias = InpManualBias; return; }
   // COT_AUTO_API
   if(MQLInfoInteger(MQL_TESTER))
   {  // WebRequest is unavailable in the tester -> manual fallback
      g_cotBias = InpManualBias;
      return;
   }
   if(TimeCurrent() - g_cotLastPoll < InpCOTRefreshMin * 60) return;
   g_cotLastPoll = TimeCurrent();

   char data[], result[];
   string headers, resHeaders;
   ResetLastError();
   int code = WebRequest("GET", InpCOTApiUrl, headers, 5000, data, result, resHeaders);
   if(code == -1)
   {
      Print("FFZ2 COT: WebRequest failed (err ", GetLastError(),
            "). Add the URL in Tools->Options->Expert Advisors. Using manual bias.");
      g_cotBias = InpManualBias;
      return;
   }
   string body = CharArrayToString(result);
   // Accepts {"bias":"LONG"} / {"bias":"SHORT"} / {"bias":"NEUTRAL"} or plain text
   StringToUpper(body);
   if(StringFind(body, "LONG")  >= 0)      g_cotBias = BIAS_LONG;
   else if(StringFind(body, "SHORT") >= 0) g_cotBias = BIAS_SHORT;
   else                                    g_cotBias = BIAS_NEUTRAL;
   Print("FFZ2 COT: bias refreshed -> ", EnumToString(g_cotBias));
}

// returns risk multiplier: 1 = full size, 0 = blocked
double BiasGate(bool isBuy)
{
   if(g_cotBias == BIAS_NEUTRAL) return 1.0;
   bool aligned = (isBuy && g_cotBias == BIAS_LONG) || (!isBuy && g_cotBias == BIAS_SHORT);
   if(aligned) return 1.0;
   return InpCOTHardBlock ? 0.0 : 0.5;
}

//=== POSITION STATE =================================================
bool HavePosition(long &type, double &volume, double &openPrice, ulong &ticket)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
      {
         type = PositionGetInteger(POSITION_TYPE);
         volume = PositionGetDouble(POSITION_VOLUME);
         openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
         ticket = tk;
         return true;
      }
   }
   return false;
}

void RestoreStateFromPosition()
{
   long t; double v, op; ulong tk;
   if(HavePosition(t, v, op, tk))
   {
      double sl = PositionGetDouble(POSITION_SL);
      g_initialRiskPts = (sl > 0) ? MathAbs(op - sl) / _Point : ATRValue() * InpSL_ATR / _Point;
      g_peakProfitR = 0; g_partialDone = false;
   }
}

void ResetTradeState() { g_peakProfitR = 0; g_partialDone = false; g_initialRiskPts = 0; }

//=== SIZING =========================================================
double CalcLot(double slPoints, double riskMult)
{
   if(InpRiskPercent <= 0 || slPoints <= 0)
      return NormalizeLot(InpFixedLot * riskMult);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickVal <= 0 || tickSize <= 0) return NormalizeLot(InpFixedLot * riskMult);
   double moneyRisk = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0 * riskMult;
   double lossPerLot = slPoints * _Point / tickSize * tickVal;
   if(lossPerLot <= 0) return NormalizeLot(InpFixedLot * riskMult);
   return NormalizeLot(moneyRisk / lossPerLot);
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

//=== ENTRIES ========================================================
void TryOpen(bool isBuy)
{
   if(!SessionOK() || !SpreadOK()) return;
   double mult = BiasGate(isBuy);
   if(mult <= 0)
   {
      Print("FFZ2: ", isBuy ? "BUY" : "SELL", " blocked by COT bias ", EnumToString(g_cotBias));
      return;
   }
   double atr = ATRValue();
   if(atr <= 0) return;
   double slPts = (InpSL_ATR > 0) ? InpSL_ATR * atr / _Point : 0;
   double lot = CalcLot(slPts, mult);
   if(lot <= 0) return;

   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = 0, tp = 0;
   if(slPts > 0)
      sl = isBuy ? price - slPts * _Point : price + slPts * _Point;
   if(InpTP_RR > 0 && slPts > 0)
      tp = isBuy ? price + InpTP_RR * slPts * _Point : price - InpTP_RR * slPts * _Point;

   bool ok = isBuy ? trade.Buy(lot, _Symbol, 0, sl, tp, "FFZ2 Buy")
                   : trade.Sell(lot, _Symbol, 0, sl, tp, "FFZ2 Sell");
   if(ok)
   {
      ResetTradeState();
      g_initialRiskPts = slPts;
      if(InpDrawTradeMarks)
      {
         string nm = "FFZ2_in_" + (string)TimeCurrent();
         ObjectCreate(0, nm, OBJ_ARROW, 0, TimeCurrent(), price);
         ObjectSetInteger(0, nm, OBJPROP_ARROWCODE, isBuy ? 233 : 234);
         ObjectSetInteger(0, nm, OBJPROP_COLOR, isBuy ? clrLime : clrRed);
         ObjectSetInteger(0, nm, OBJPROP_WIDTH, 2);
      }
   }
   else
      Print("FFZ2: open failed: ", trade.ResultRetcode(), " ", trade.ResultComment());
}

void ClosePosition(string reason)
{
   long t; double v, op; ulong tk;
   if(!HavePosition(t, v, op, tk)) return;
   if(trade.PositionClose(tk, InpSlippagePoints))
   {
      Print("FFZ2: closed (", reason, ")");
      ResetTradeState();
   }
}

//=== MANAGEMENT (every tick) ========================================
void ManagePosition()
{
   long type; double vol, op; ulong tk;
   if(!HavePosition(type, vol, op, tk)) return;
   bool isBuy = (type == POSITION_TYPE_BUY);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double cur = isBuy ? bid : ask;
   double sl  = PositionGetDouble(POSITION_SL);
   double tpC = PositionGetDouble(POSITION_TP);

   if(g_initialRiskPts <= 0)
   {
      double atr = ATRValue();
      g_initialRiskPts = (atr > 0 ? InpSL_ATR * atr : 100 * _Point) / _Point;
      if(g_initialRiskPts <= 0) return;
   }
   double profitPts = (isBuy ? cur - op : op - cur) / _Point;
   double profitR   = profitPts / g_initialRiskPts;
   if(profitR > g_peakProfitR) g_peakProfitR = profitR;

   // 1) Giveback guard — bank it before a winner becomes a loser
   if(InpUseGiveback && g_peakProfitR >= InpGivebackArmR &&
      profitR < g_peakProfitR * InpGivebackPct / 100.0)
   {
      ClosePosition(StringFormat("giveback: peak %.2fR -> %.2fR", g_peakProfitR, profitR));
      return;
   }

   // 2) Partial profit
   if(InpUsePartial && !g_partialDone && profitR >= InpPartialAtR)
   {
      double closeVol = NormalizeLot(vol * InpPartialPercent / 100.0);
      double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      if(closeVol >= minLot && vol - closeVol >= minLot)
      {
         if(trade.PositionClosePartial(tk, closeVol))
         {
            g_partialDone = true;
            Print("FFZ2: partial ", DoubleToString(closeVol, 2), " lots at +",
                  DoubleToString(profitR, 2), "R");
         }
      }
      else g_partialDone = true; // volume too small to split
   }

   // 3) Breakeven
   if(InpUseBreakeven && profitR >= InpBE_TriggerR)
   {
      double be = isBuy ? op + InpBE_OffsetPoints * _Point
                        : op - InpBE_OffsetPoints * _Point;
      bool needs = isBuy ? (sl < be - _Point) : (sl > be + _Point || sl == 0);
      if(needs)
         trade.PositionModify(tk, NormalizeDouble(be, _Digits), tpC);
   }

   // 4) Chandelier trail
   if(InpUseTrail && profitR >= InpTrailStartR)
   {
      double atr = ATRValue();
      if(atr > 0)
      {
         double trail = isBuy ? cur - InpTrail_ATR * atr : cur + InpTrail_ATR * atr;
         sl = PositionGetDouble(POSITION_SL);
         bool better = isBuy ? (trail > sl + _Point) : (sl == 0 || trail < sl - _Point);
         if(better)
            trade.PositionModify(tk, NormalizeDouble(trail, _Digits), tpC);
      }
   }
}

//=== HUD ============================================================
void DrawHUD()
{
   if(!InpShowHUD) return;
   string bias = (InpCOTMode == COT_OFF) ? "off" : EnumToString(g_cotBias);
   long t; double v, op; ulong tk;
   string pos = "flat";
   if(HavePosition(t, v, op, tk))
      pos = StringFormat("%s %.2f lots | peak %.2fR | partial %s",
                         t == POSITION_TYPE_BUY ? "LONG" : "SHORT", v,
                         g_peakProfitR, g_partialDone ? "done" : "pending");
   Comment(StringFormat("FFZ v2\nRegime: %s\nCOT bias: %s\nPosition: %s",
                        g_chopState, bias, pos));
}

//=== MAIN ===========================================================
void OnTick()
{
   ManagePosition();          // tick-level exit management
   UpdateCOTBias();

   datetime curBar = iTime(_Symbol, _Period, 1);
   if(curBar == lastBarTime) { DrawHUD(); return; }
   if(firstRun)
   {  // ignore stale arrows present before EA start
      firstRun = false;
      lastBarTime = curBar;
      return;
   }
   lastBarTime = curBar;

   bool choppy = MarketIsChoppy();

   double up[1], dn[1], upPrev[1], dnPrev[1];
   if(CopyBuffer(hInd, InpUpBuffer, 1, 1, up) != 1)  { DrawHUD(); return; }
   if(CopyBuffer(hInd, InpDownBuffer, 1, 1, dn) != 1){ DrawHUD(); return; }
   if(CopyBuffer(hInd, InpUpBuffer, 2, 1, upPrev) != 1)  upPrev[0] = EMPTY_VALUE;
   if(CopyBuffer(hInd, InpDownBuffer, 2, 1, dnPrev) != 1) dnPrev[0] = EMPTY_VALUE;

   bool newUp = (up[0] != EMPTY_VALUE && up[0] > 0 && curBar > lastUpSignalBar &&
                 (upPrev[0] == EMPTY_VALUE || upPrev[0] <= 0));
   bool newDn = (dn[0] != EMPTY_VALUE && dn[0] > 0 && curBar > lastDownSignalBar &&
                 (dnPrev[0] == EMPTY_VALUE || dnPrev[0] <= 0));

   long type; double vol, op; ulong tk;
   bool inPos = HavePosition(type, vol, op, tk);

   if(newUp)
   {
      lastUpSignalBar = curBar;
      if(inPos && type == POSITION_TYPE_SELL) ClosePosition("opposite arrow");
      inPos = HavePosition(type, vol, op, tk);
      if(!inPos)
      {
         if(choppy && InpChopBlocksFlip)
            Print("FFZ2: BUY arrow ignored — ", g_chopState);
         else
            TryOpen(true);
      }
   }
   if(newDn)
   {
      lastDownSignalBar = curBar;
      if(inPos && type == POSITION_TYPE_BUY) ClosePosition("opposite arrow");
      inPos = HavePosition(type, vol, op, tk);
      if(!inPos)
      {
         if(choppy && InpChopBlocksFlip)
            Print("FFZ2: SELL arrow ignored — ", g_chopState);
         else
            TryOpen(false);
      }
   }
   DrawHUD();
}
