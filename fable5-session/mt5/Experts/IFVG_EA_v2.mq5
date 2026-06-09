//+------------------------------------------------------------------+
//|                                                  FVG Inverse.mq5 |
//|                           Copyright 2025, Allan Munene Mutiiria. |
//|                                   https://t.me/Forex_Algo_Trader |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025, Allan Munene Mutiiria."
#property link      "https://t.me/Forex_Algo_Trader"
#property version   "1.00"

#include <Trade/Trade.mqh>

//+------------------------------------------------------------------+
//| Global Variables                                                 |
//+------------------------------------------------------------------+
CTrade obj_Trade;                                                 //--- Trade object
#define FVG_Prefix "IFVG REC "                                    //--- FVG prefix
// Normal FVGs
#define CLR_UP   clrGreen                                         // Green for normal up (Bullish FVG)
#define CLR_DOWN clrRed                                           // Red for normal down (Bearish FVG)
// Mitigated FVGs
#define CLR_MIT_UP   clrPurple                                    // Purple for mitigated up (Mitigated Bullish FVG)
#define CLR_MIT_DOWN clrOrange                                    // Orange for mitigated down (Mitigated Bearish FVG)
// Inverted FVGs
#define CLR_INV_UP   clrRed                                       // Red for inverted up (Bearish IFVG)
#define CLR_INV_DOWN clrGreen                                     // Green for inverted down (Bullish IFVG)

//+------------------------------------------------------------------+
//| Enums                                                            |
//+------------------------------------------------------------------+
enum TradeMode {                                                  // Define trade mode enum
   TradeOnce,                                                     // Trade Once
   LimitedTrades,                                                 // Limited Trades
   UnlimitedTrades                                                // Unlimited Trades
};

enum FVGState {                                                   // Define FVG state enum
   Normal,                                                        // Normal
   Mitigated,                                                     // Mitigated
   Inverted                                                       // Inverted
};

enum TrailingTypeEnum {                                           // Define enum for trailing stop types
   Trailing_None   = 0,                                           // None
   Trailing_Points = 2                                            // By Points
};

enum EntryModeEnum {                                              // Define enum for entry mode
   Entry_50Percent,                                               // 50% of IFVG Zone (Limit Order at Midpoint)
   Entry_ZoneTouch                                                // Touch of IFVG Zone (Market on Touch)
};

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
input group "EA GENERAL SETTINGS"
input double           inpLot                   = 0.01;           // Lotsize (used when RiskPercent=0)
input double           sl_dollars               = 10.0;           // [Legacy] Stop Loss in Dollars ($)
input double           tp_dollars               = 10.0;           // [Legacy] Take Profit in Dollars ($)

input group "V2 — STRUCTURE EXITS & RISK"
input bool             InpUseStructureExits     = true;           // SL beyond zone + RR TP (false = legacy $ exits)
input double           InpRiskPercent           = 1.0;            // Risk % of balance per trade (0 = fixed lot)
input double           InpSL_ATRBuffer          = 0.5;            // SL buffer beyond IFVG zone in ATR
input int              InpATRPeriod             = 14;             // ATR period
input double           InpTP_RR                 = 2.0;            // Take profit in R multiples
input bool             InpUseBreakeven          = true;           // Move SL to BE
input double           InpBE_TriggerR           = 1.0;            // BE trigger in R
input bool             InpUseGiveback           = true;           // Bank trade if it gives back peak profit
input double           InpGivebackArmR          = 1.5;            // Arm giveback at +X R
input double           InpGivebackPct           = 50;             // Close if profit < X% of peak

input group "V2 — FILTERS"
input bool             InpUseTrendFilter        = true;           // Only trade with HTF EMA trend
input ENUM_TIMEFRAMES  InpTrendTF               = PERIOD_H1;      // Trend timeframe
input int              InpTrendEMA              = 50;             // Trend EMA period
input double           InpMaxSpreadPoints       = 0;              // Max spread points (0 = off)
input int              minPts                   = 100;            // Minimum Gap Size in Points
input int              FVG_Rec_Ext_Bars         = 30;             // FVG Extension Bars
input bool             prt                      = true;           // Print Statements
input long             magic_number             = 123456789;      // Magic Number
input bool             ignoreOverlaps           = true;           // Ignore new FVGs that overlap existing ones
input TradeMode        tradeMode                = TradeOnce;      // Mode for trading FVGs
input int              maxTradesPerFVG          = 2;              // Maximum trades per FVG for LimitedTrades
input int              maxFVGs                  = 50;             // Maximum FVGs to track in array
input EntryModeEnum    EntryMode                = Entry_50Percent; // Entry Mode
input TrailingTypeEnum TrailingType             = Trailing_None;  // Trailing Stop Type
input double           Trailing_Stop_Dollars    = 5.0;            // Trailing Stop in Dollars ($)
input double           Min_Profit_To_Trail_Dollars = 8.0;         // Min Profit to Start Trailing in Dollars ($)

//+------------------------------------------------------------------+
//| Structure for FVG zone information                               |
//+------------------------------------------------------------------+
struct FVGZone {                                                  // Define FVG zone structure
   string   name;                                                 //--- Zone name
   datetime startTime;                                            //--- Start time
   datetime origEndTime;                                          //--- Original end time
   datetime mitTime;                                              //--- Mitigation time
   datetime retTime;                                              //--- Retrace time
   bool     signal;                                               //--- Signal flag
   bool     inverted;                                             //--- Inverted flag
   bool     mit;                                                  //--- Mitigated flag
   bool     ret;                                                  //--- Retraced flag
   bool     origUp;                                               //--- Original direction: true=Bullish FVG
   int      tradeCount;                                           //--- Trade count
   FVGState state;                                                //--- State
   bool     newSignal;                                            //--- New signal flag
   bool     pendingEntry;                                         //--- Armed for zone-touch entry
};
FVGZone fvgs[];                                                   //--- FVG zones array

//================= V2 ADDITIONS =====================================
int    g_hATR = INVALID_HANDLE;
int    g_hTrendEMA = INVALID_HANDLE;
double g_peakR[];
ulong  g_peakTickets[];

double V2_ATR()
{
   double a[1];
   return (CopyBuffer(g_hATR, 0, 1, 1, a) == 1) ? a[0] : 0;
}

int V2_Trend()   // +1 bull, -1 bear, 0 off/unknown
{
   if (!InpUseTrendFilter) return 0;
   double e[1];
   if (CopyBuffer(g_hTrendEMA, 0, 1, 1, e) != 1) return 0;
   double c = iClose(_Symbol, InpTrendTF, 1);
   if (c > e[0]) return +1;
   if (c < e[0]) return -1;
   return 0;
}

bool V2_SpreadOK()
{
   if (InpMaxSpreadPoints <= 0) return true;
   return (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / _Point <= InpMaxSpreadPoints;
}

double V2_NormalizeLot(double lot)
{
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if (st <= 0) st = 0.01;
   lot = MathFloor(lot / st) * st;
   return MathMax(mn, MathMin(mx, NormalizeDouble(lot, 2)));
}

double V2_CalcLot(double slPts)
{
   if (InpRiskPercent <= 0 || slPts <= 0) return V2_NormalizeLot(inpLot);
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if (tv <= 0 || ts <= 0) return V2_NormalizeLot(inpLot);
   double money = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   double perLot = slPts * _Point / ts * tv;
   return (perLot > 0) ? V2_NormalizeLot(money / perLot) : V2_NormalizeLot(inpLot);
}

//--- single entry point for ALL v2 trades
bool V2_Place(bool isBuy, bool isLimit, double limitPrice, double zoneLow, double zoneHigh, string cmt)
{
   if (!V2_SpreadOK()) return false;
   int tr = V2_Trend();
   if (InpUseTrendFilter && tr != 0 && ((isBuy && tr < 0) || (!isBuy && tr > 0)))
   {
      if (prt) Print("V2: ", isBuy ? "BUY" : "SELL", " blocked by HTF trend");
      return false;
   }
   if (!InpUseStructureExits)
   {  // legacy behavior
      if (isLimit)
         return isBuy ? obj_Trade.BuyLimit(inpLot, limitPrice, _Symbol, 0, 0, 0, 0, cmt)
                      : obj_Trade.SellLimit(inpLot, limitPrice, _Symbol, 0, 0, 0, 0, cmt);
      double p = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      return isBuy ? obj_Trade.Buy(inpLot, _Symbol, p, 0, 0, cmt)
                   : obj_Trade.Sell(inpLot, _Symbol, p, 0, 0, cmt);
   }
   double atr = V2_ATR();
   if (atr <= 0) return false;
   double entry = isLimit ? limitPrice
                          : (isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                   : SymbolInfoDouble(_Symbol, SYMBOL_BID));
   double sl = isBuy ? zoneLow - InpSL_ATRBuffer * atr
                     : zoneHigh + InpSL_ATRBuffer * atr;
   double slPts = MathAbs(entry - sl) / _Point;
   if (slPts <= 0) return false;
   double tp = isBuy ? entry + InpTP_RR * slPts * _Point
                     : entry - InpTP_RR * slPts * _Point;
   double lot = V2_CalcLot(slPts);
   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);
   if (isLimit)
      return isBuy ? obj_Trade.BuyLimit(lot, limitPrice, _Symbol, sl, tp, 0, 0, cmt)
                   : obj_Trade.SellLimit(lot, limitPrice, _Symbol, sl, tp, 0, 0, cmt);
   return isBuy ? obj_Trade.Buy(lot, _Symbol, entry, sl, tp, cmt)
                : obj_Trade.Sell(lot, _Symbol, entry, sl, tp, cmt);
}

//--- V2 per-position management: breakeven + giveback guard
void V2_Manage()
{
   if (!InpUseStructureExits) return;
   for (int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if (tk == 0) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if (PositionGetInteger(POSITION_MAGIC) != magic_number) continue;
      bool   isBuy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double op = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double cur = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double riskPts = (sl > 0) ? MathAbs(op - sl) / _Point : 0;
      if (riskPts <= 0) continue;
      double r = (isBuy ? cur - op : op - cur) / _Point / riskPts;
      int idx = -1;
      for (int k = 0; k < ArraySize(g_peakTickets); k++)
         if (g_peakTickets[k] == tk) { idx = k; break; }
      if (idx < 0)
      {
         idx = ArraySize(g_peakTickets);
         ArrayResize(g_peakTickets, idx + 1);
         ArrayResize(g_peakR, idx + 1);
         g_peakTickets[idx] = tk;
         g_peakR[idx] = r;
      }
      if (r > g_peakR[idx]) g_peakR[idx] = r;

      if (InpUseGiveback && g_peakR[idx] >= InpGivebackArmR &&
          r < g_peakR[idx] * InpGivebackPct / 100.0)
      {
         if (obj_Trade.PositionClose(tk) && prt)
            Print("V2 giveback close: peak ", DoubleToString(g_peakR[idx], 2), "R -> ", DoubleToString(r, 2), "R");
         continue;
      }
      if (InpUseBreakeven && r >= InpBE_TriggerR)
      {
         double be = isBuy ? op + 2 * _Point : op - 2 * _Point;
         bool needs = isBuy ? (sl < be - _Point) : (sl > be + _Point);
         if (needs) obj_Trade.PositionModify(tk, NormalizeDouble(be, _Digits), tp);
      }
   }
}
//================= END V2 ADDITIONS =================================

//+------------------------------------------------------------------+
//| Convert a dollar amount to price distance for current symbol     |
//|                                                                  |
//| Step-by-step:                                                    |
//|  tickValue  = broker $ profit for moving 1 lot by 1 tickSize    |
//|  valuePerPoint = tickValue / tickSize   ($ per 1.0 price move)  |
//|  dollarPerPoint = valuePerPoint * lot   ($ per point this trade) |
//|  priceDistance = dollars / dollarPerPoint                        |
//|                                                                  |
//| Example — Volatility 10 Index, lot=0.01:                         |
//|  tickValue=$0.10, tickSize=0.001                                 |
//|  valuePerPoint = 0.10/0.001 = 100 $/point per lot               |
//|  dollarPerPoint = 100 * 0.01 = 1.00 $/point                     |
//|  $10 SL → 10/1.00 = 10.0 price units distance                   |
//+------------------------------------------------------------------+
double DollarsToPoints(double dollars, double lot) {
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE); //--- $ profit per 1 lot per 1 tick
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);  //--- Price change per 1 tick
   if (tickValue <= 0 || tickSize <= 0 || lot <= 0) return 0;    //--- Guard divide-by-zero
   double valuePerPoint    = tickValue / tickSize;                //--- $ per 1.0 price move per 1 lot
   double dollarPerPoint   = valuePerPoint * lot;                 //--- $ per 1.0 price move this trade
   if (dollarPerPoint <= 0) return 0;                             //--- Guard zero result
   return NormalizeDouble(dollars / dollarPerPoint, _Digits);     //--- Price distance for requested $
}

//+------------------------------------------------------------------+
//| Get color based on state and direction                           |
//+------------------------------------------------------------------+
color GetFVGColor(bool isUp, FVGState currentState) {
   if (currentState == Normal)    return isUp ? CLR_UP     : CLR_DOWN;     //--- Return normal color
   if (currentState == Mitigated) return isUp ? CLR_MIT_UP : CLR_MIT_DOWN; //--- Return mitigated color
   if (currentState == Inverted)  return isUp ? CLR_INV_UP : CLR_INV_DOWN; //--- Return inverted color
   return clrNONE;                                                //--- Return none
}

//+------------------------------------------------------------------+
//| Print FVGs for debugging                                         |
//+------------------------------------------------------------------+
void PrintFVGs() {
   if (!prt) return;                                              //--- Return if no print
   Print("Current FVGs count: ", ArraySize(fvgs));                //--- Print count
   for (int i = 0; i < ArraySize(fvgs); i++) {                    //--- Iterate FVGs
      Print("FVG[", i, "] ", fvgs[i].name,
            " state=",       EnumToString(fvgs[i].state),
            " origUp=",      fvgs[i].origUp,
            " mit=",          fvgs[i].mit,
            " ret=",          fvgs[i].ret,
            " inverted=",     fvgs[i].inverted,
            " trades=",       fvgs[i].tradeCount,
            " newSig=",       fvgs[i].newSignal,
            " pending=",      fvgs[i].pendingEntry);              //--- Print details
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit() {
   obj_Trade.SetExpertMagicNumber(magic_number);                  //--- Set magic number
   g_hATR = iATR(_Symbol, _Period, InpATRPeriod);                 //--- V2: ATR handle
   g_hTrendEMA = iMA(_Symbol, InpTrendTF, InpTrendEMA, 0, MODE_EMA, PRICE_CLOSE); //--- V2: trend handle
   if (g_hATR == INVALID_HANDLE || g_hTrendEMA == INVALID_HANDLE) return INIT_FAILED; //--- V2 guard
   ObjectsDeleteAll(0, FVG_Prefix);                               //--- Delete all FVG objects
   ArrayResize(fvgs, 0);                                          //--- Reset array
   if (prt) Print("Init: Cleared all FVG objects and reset array."); //--- Log init
   // Log dollar conversion info on startup
   int visibleBars = (int)ChartGetInteger(0, CHART_VISIBLE_BARS); //--- Get visible bars
   if (prt) Print("Visible bars: ", visibleBars);                 //--- Log visible bars
   // Detect historical FVGs oldest to newest
   for (int i = visibleBars - 3; i >= 0; i--) {                   //--- Iterate bars old→new
      double low0      = iLow(_Symbol, _Period, i);               //--- Bar[i] low
      double high2     = iHigh(_Symbol, _Period, i + 2);          //--- Bar[i+2] high
      double gap_L0_H2 = NormalizeDouble((low0 - high2) / _Point, _Digits); //--- Up gap
      double high0     = iHigh(_Symbol, _Period, i);              //--- Bar[i] high
      double low2      = iLow(_Symbol, _Period, i + 2);           //--- Bar[i+2] low
      double gap_H0_L2 = NormalizeDouble((low2 - high0) / _Point, _Digits); //--- Down gap
      bool FVG_UP   = low0 > high2 && gap_L0_H2 > minPts;         //--- Bullish FVG
      bool FVG_DOWN = low2 > high0 && gap_H0_L2 > minPts;         //--- Bearish FVG
      if (!FVG_UP && !FVG_DOWN) continue;                         //--- Skip no FVG
      datetime time1  = iTime(_Symbol, _Period, i + 1);           //--- Middle bar time
      double   price1 = FVG_UP ? high2 : high0;                   //--- Zone boundary 1
      double   price2 = FVG_UP ? low0  : low2;                    //--- Zone boundary 2
      double   newLow  = MathMin(price1, price2);                 //--- Zone low
      double   newHigh = MathMax(price1, price2);                 //--- Zone high
      bool overlaps = false;                                      //--- Init overlap flag
      if (ignoreOverlaps) {                                       //--- Check overlaps
         for (int ex = 0; ex < ArraySize(fvgs); ex++) {           //--- Iterate existing
            double exLow  = MathMin(ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 0),
                                    ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 1)); //--- Existing low
            double exHigh = MathMax(ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 0),
                                    ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 1)); //--- Existing high
            if (MathMax(newLow, exLow) < MathMin(newHigh, exHigh)) { //--- Overlap check
               overlaps = true;                                   //--- Mark overlap
               break;                                             //--- Stop checking
            }
         }
      }
      if (overlaps) continue;                                     //--- Skip overlapping
      string   fvgNAME = FVG_Prefix + "(" + TimeToString(time1) + ")"; //--- Build name
      color    fvgClr  = FVG_UP ? CLR_UP : CLR_DOWN;              //--- Initial color
      datetime endTime = time1 + PeriodSeconds(_Period) * FVG_Rec_Ext_Bars; //--- End time
      CreateRec(fvgNAME, time1, price1, endTime, price2, fvgClr); //--- Draw rectangle
      int size = ArraySize(fvgs);                                 //--- Current array size
      if (size >= maxFVGs) {                                      //--- Check max capacity
         ArrayRemove(fvgs, 0, 1);                                 //--- Remove oldest
         size--;                                                  //--- Decrement size
      }
      ArrayResize(fvgs, size + 1);                                //--- Grow array
      fvgs[size].name         = fvgNAME;                          //--- Set name
      fvgs[size].startTime    = time1;                            //--- Set start time
      fvgs[size].origEndTime  = endTime;                          //--- Set end time
      fvgs[size].mitTime      = 0;                                //--- No mitigation yet
      fvgs[size].retTime      = 0;                                //--- No retrace yet
      fvgs[size].signal       = false;                            //--- No signal yet
      fvgs[size].inverted     = false;                            //--- Not inverted
      fvgs[size].mit          = false;                            //--- Not mitigated
      fvgs[size].ret          = false;                            //--- Not retraced
      fvgs[size].origUp       = FVG_UP;                           //--- Original direction
      fvgs[size].tradeCount   = 0;                                //--- No trades yet
      fvgs[size].state        = Normal;                           //--- Normal state
      fvgs[size].newSignal    = false;                            //--- No new signal
      fvgs[size].pendingEntry = false;                            //--- Not pending
      // Create label NOW after array is populated so origUp is known
      datetime midTime  = time1 + (endTime - time1) / 2;          //--- Mid time
      double   midPrice = (price1 + price2) / 2;                  //--- Mid price
      CreateLabel(fvgNAME, midTime, midPrice);                    //--- Label with correct direction
   }
   // Replay history on each FVG to set correct state
   for (int j = 0; j < ArraySize(fvgs); j++) {                    //--- Iterate FVGs
      ProcessHistoricalState(j);                                  //--- Replay history
   }
   if (prt) PrintFVGs();                                          //--- Print summary
   return(INIT_SUCCEEDED);                                        //--- Return success
}

//+------------------------------------------------------------------+
//| Replay historical bars to find mit → retrace → inversion        |
//|                                                                  |
//| IFVG cycle (3 sequential bars, each on a DIFFERENT bar):         |
//|  Bullish FVG (origUp=true):                                      |
//|    Mitigation : barLow  < fvgLow                                 |
//|    Retrace    : later barHigh > fvgLow                           |
//|    Inversion  : even later barClose < fvgLow → Bearish IFVG     |
//|  Bearish FVG (origUp=false):                                     |
//|    Mitigation : barHigh > fvgHigh                                |
//|    Retrace    : later barLow  < fvgHigh                          |
//|    Inversion  : even later barClose > fvgHigh → Bullish IFVG    |
//+------------------------------------------------------------------+
void ProcessHistoricalState(int idx) {
   string   fvgNAME = fvgs[idx].name;                             //--- Zone name
   int      startBar = iBarShift(_Symbol, _Period, fvgs[idx].startTime); //--- Bar index of zone start
   if (startBar < 0) return;                                      //--- Invalid bar, skip
   double fvgLow  = MathMin(ObjectGetDouble(0, fvgNAME, OBJPROP_PRICE, 0),
                             ObjectGetDouble(0, fvgNAME, OBJPROP_PRICE, 1)); //--- Zone low
   double fvgHigh = MathMax(ObjectGetDouble(0, fvgNAME, OBJPROP_PRICE, 0),
                             ObjectGetDouble(0, fvgNAME, OBJPROP_PRICE, 1)); //--- Zone high
   bool     isMit  = false;                                       //--- Mitigation flag
   bool     isRet  = false;                                       //--- Retrace flag
   bool     isSig  = false;                                       //--- Signal/inversion flag
   datetime mitTime = 0;                                          //--- Mitigation bar time
   datetime retTime = 0;                                          //--- Retrace bar time
   for (int k = startBar - 1; k >= 0; k--) {                      //--- Walk forward after zone
      double barLow    = iLow(_Symbol,   _Period, k);             //--- Bar low
      double barHigh   = iHigh(_Symbol,  _Period, k);             //--- Bar high
      double barClose  = iClose(_Symbol, _Period, k);             //--- Bar close
      datetime barTime = iTime(_Symbol,  _Period, k);             //--- Bar time
      // STEP 1: Mitigation
      if (!isMit) {                                               //--- Not yet mitigated
         bool broke = (fvgs[idx].origUp  && barLow  < fvgLow) ||
                      (!fvgs[idx].origUp && barHigh > fvgHigh);  //--- Far side broken
         if (broke) {                                             //--- Mitigation confirmed
            isMit   = true;                                      //--- Mark mitigated
            mitTime = barTime;                                    //--- Record time
            if (prt) Print("Hist Mit: ", fvgNAME, " t=", TimeToString(barTime)); //--- Log
         }
      }
      // STEP 2: Retrace — strictly after mitigation bar
      else if (!isRet && barTime > mitTime) {                     //--- After mitigation
         bool retraced = (fvgs[idx].origUp  && barHigh > fvgLow) ||
                         (!fvgs[idx].origUp && barLow  < fvgHigh); //--- Re-entered zone
         if (retraced) {                                          //--- Retrace confirmed
            isRet   = true;                                      //--- Mark retraced
            retTime = barTime;                                    //--- Record time
            if (prt) Print("Hist Ret: ", fvgNAME, " t=", TimeToString(barTime)); //--- Log
         }
      }
      // STEP 3: Inversion — strictly after retrace bar
      else if (!isSig && barTime > retTime) {                     //--- After retrace
         bool inverted = (fvgs[idx].origUp  && barClose < fvgLow) ||
                         (!fvgs[idx].origUp && barClose > fvgHigh); //--- Closed beyond far side
         if (inverted) {                                          //--- Inversion confirmed
            isSig = true;                                        //--- Mark inverted
            if (prt) Print("Hist Inv: ", fvgNAME, " t=", TimeToString(barTime)); //--- Log
         }
      }
   }
   fvgs[idx].mit          = isMit;                                //--- Store mitigation
   fvgs[idx].ret          = isRet;                                //--- Store retrace
   fvgs[idx].inverted     = isSig;                                //--- Store inversion
   fvgs[idx].signal       = isSig;                                //--- Store signal
   fvgs[idx].mitTime      = mitTime;                              //--- Store mit time
   fvgs[idx].retTime      = retTime;                              //--- Store ret time
   if (isSig)       fvgs[idx].state = Inverted;                   //--- Inverted state
   else if (isMit)  fvgs[idx].state = Mitigated;                  //--- Mitigated state
   else             fvgs[idx].state = Normal;                     //--- Normal state
   fvgs[idx].newSignal    = false;                                //--- No live signal on init
   fvgs[idx].pendingEntry = false;                                //--- No pending entry on init
   color finalClr = GetFVGColor(fvgs[idx].origUp, fvgs[idx].state); //--- Final color
   UpdateRec(fvgNAME, fvgs[idx].startTime, fvgLow, fvgs[idx].origEndTime, fvgHigh, finalClr); //--- Apply color
   if (mitTime > 0) DrawMitIcon(fvgNAME, mitTime, fvgHigh, fvgLow, fvgs[idx].origUp); //--- Draw mit icon
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   for (int i = 0; i < ArraySize(fvgs); i++) {                    //--- Iterate FVGs
      ObjectDelete(0, fvgs[i].name);                              //--- Delete zone rect
      ObjectDelete(0, fvgs[i].name + "_Label");                   //--- Delete label
      ObjectDelete(0, fvgs[i].name + "_MitIcon");                 //--- Delete mit icon
   }
   ArrayResize(fvgs, 0);                                          //--- Clear array
   ChartRedraw(0);                                                //--- Redraw chart
   if (prt) Print("Deinit: All FVG objects removed.");            //--- Log
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick() {
   if (PositionsTotal() > 0) {                                    //--- Positions open
      if (!InpUseStructureExits) MonitorPositions();              //--- Legacy $ exits only in legacy mode
      V2_Manage();                                                //--- V2: breakeven + giveback
      if (TrailingType == Trailing_Points) ApplyDollarsTrailing(); //--- Apply trailing if enabled
   }
   if (EntryMode == Entry_ZoneTouch) {                            //--- Zone touch: check every tick
      CheckZoneTouchEntry();                                      //--- Tick-level entry check
   }
   static datetime lastBarTime = 0;                               //--- Last processed bar
   datetime curBarTime = iTime(_Symbol, _Period, 0);              //--- Current bar open time
   if (curBarTime == lastBarTime) return;                         //--- Same bar, skip
   lastBarTime = curBarTime;                                      //--- Record new bar
   DetectFVGs();                                                  //--- Scan for new FVGs
   UpdateFVGs();                                                  //--- Advance FVG states
   TradeOnFVGs();                                                 //--- Execute 50% trades
   CleanupExpiredFVGs(curBarTime);                                //--- Remove expired zones
}

//+------------------------------------------------------------------+
//| Check floating PnL on every tick and close if SL or TP hit      |
//| profit >= +tp_dollars  → close as Take Profit                    |
//| profit <= -sl_dollars  → close as Stop Loss                      |
//+------------------------------------------------------------------+
void MonitorPositions() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {              //--- Reverse iterate
      if (PositionGetTicket(i) <= 0) continue;                    //--- Skip invalid ticket
      if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue; //--- Skip other symbols
      if (PositionGetInteger(POSITION_MAGIC) != magic_number) continue; //--- Skip other EAs
      double profit = PositionGetDouble(POSITION_PROFIT);         //--- Floating PnL in dollars
      ulong  ticket = PositionGetInteger(POSITION_TICKET);        //--- Position ticket
      if (profit >= tp_dollars) {                                 //--- Take profit hit
         if (prt) Print("TP HIT: ticket=", ticket, " PnL=$", profit, " >= +$", tp_dollars); //--- Log
         obj_Trade.PositionClose(ticket);                         //--- Close position
      } else if (profit <= -sl_dollars) {                         //--- Stop loss hit
         if (prt) Print("SL HIT: ticket=", ticket, " PnL=$", profit, " <= -$", sl_dollars); //--- Log
         obj_Trade.PositionClose(ticket);                         //--- Close position
      }
   }
}

//+------------------------------------------------------------------+
//| Apply Dollar-based Trailing Stop                                 |
//| Converts Trailing_Stop_Dollars and Min_Profit_To_Trail_Dollars   |
//| to price distance using DollarsToPoints() before applying        |
//+------------------------------------------------------------------+
void ApplyDollarsTrailing() {
   double trailDist  = DollarsToPoints(Trailing_Stop_Dollars, inpLot);      //--- Trail distance in price
   double minProfit  = DollarsToPoints(Min_Profit_To_Trail_Dollars, inpLot); //--- Min profit distance
   if (trailDist <= 0 || minProfit <= 0) return;                 //--- Guard invalid values
   for (int i = PositionsTotal() - 1; i >= 0; i--) {              //--- Reverse iterate positions
      if (PositionGetTicket(i) <= 0) continue;                    //--- Skip invalid
      if (PositionGetString(POSITION_SYMBOL)  != _Symbol)        continue; //--- Skip other symbols
      if (PositionGetInteger(POSITION_MAGIC)  != magic_number)   continue; //--- Skip other EAs
      double sl        = PositionGetDouble(POSITION_SL);          //--- Current SL
      double tp        = PositionGetDouble(POSITION_TP);          //--- Current TP
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);  //--- Open price
      ulong  ticket    = PositionGetInteger(POSITION_TICKET);     //--- Ticket
      if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) { //--- Buy position
         double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);    //--- Current bid
         double newSL = NormalizeDouble(bid - trailDist, _Digits); //--- Proposed SL
         if (newSL > sl && bid - openPrice > minProfit) {         //--- Trail conditions met
            obj_Trade.PositionModify(ticket, newSL, tp);          //--- Move SL up
            if (prt) Print("Trail BUY SL → ", newSL, " (trail=$", Trailing_Stop_Dollars, ")"); //--- Log
         }
      } else if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL) { //--- Sell position
         double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);    //--- Current ask
         double newSL = NormalizeDouble(ask + trailDist, _Digits); //--- Proposed SL
         if (newSL < sl && openPrice - ask > minProfit) {         //--- Trail conditions met
            obj_Trade.PositionModify(ticket, newSL, tp);          //--- Move SL down
            if (prt) Print("Trail SELL SL → ", newSL, " (trail=$", Trailing_Stop_Dollars, ")"); //--- Log
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Zone-Touch Entry: market order on first tick price enters zone   |
//| Bearish IFVG (origUp=true) : Sell when Bid re-enters zone        |
//| Bullish IFVG (origUp=false): Buy  when Ask re-enters zone        |
//+------------------------------------------------------------------+
void CheckZoneTouchEntry() {
   double Ask = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_ASK), _Digits); //--- Ask
   double Bid = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_BID), _Digits); //--- Bid
   for (int j = 0; j < ArraySize(fvgs); j++) {                    //--- Iterate zones
      if (!fvgs[j].pendingEntry) continue;                        //--- Skip unarmed zones
      if (fvgs[j].mitTime == 0)  continue;                        //--- Skip unmit zones
      if (tradeMode == TradeOnce     && fvgs[j].tradeCount >= 1)              { fvgs[j].pendingEntry = false; continue; } //--- Once limit
      if (tradeMode == LimitedTrades && fvgs[j].tradeCount >= maxTradesPerFVG) { fvgs[j].pendingEntry = false; continue; } //--- Limited limit
      double fvgLow  = MathMin(ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 0),
                                ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 1)); //--- Zone low
      double fvgHigh = MathMax(ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 0),
                                ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 1)); //--- Zone high
      if (fvgs[j].origUp) {                                       //--- Bearish IFVG: Sell on touch
         if (Bid >= fvgLow && Bid <= fvgHigh) {                   //--- Bid inside zone
            if (prt) Print("ZoneTouch SELL: ", fvgs[j].name, " Bid=", Bid);  //--- Log
            V2_Place(false, false, 0, fvgLow, fvgHigh, "IFVG Sell");                          //--- Sell
            fvgs[j].tradeCount++;                                 //--- Count trade
            fvgs[j].pendingEntry = false;                         //--- Disarm
            ResetFVGCycle(j, fvgLow, fvgHigh);                   //--- Reset cycle
         }
      } else {                                                    //--- Bullish IFVG: Buy on touch
         if (Ask >= fvgLow && Ask <= fvgHigh) {                   //--- Ask inside zone
            if (prt) Print("ZoneTouch BUY:  ", fvgs[j].name, " Ask=", Ask);  //--- Log
            V2_Place(true, false, 0, fvgLow, fvgHigh, "IFVG Buy");                           //--- Buy
            fvgs[j].tradeCount++;                                 //--- Count trade
            fvgs[j].pendingEntry = false;                         //--- Disarm
            ResetFVGCycle(j, fvgLow, fvgHigh);                   //--- Reset cycle
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Detect new FVGs on the last 3 closed bars                        |
//+------------------------------------------------------------------+
void DetectFVGs() {
   for (int i = 3; i >= 1; i--) {                                 //--- Check bars 3,2,1
      double low0      = iLow(_Symbol,  _Period, i);              //--- Bar[i] low
      double high2     = iHigh(_Symbol, _Period, i + 2);          //--- Bar[i+2] high
      double gap_L0_H2 = NormalizeDouble((low0 - high2) / _Point, _Digits); //--- Up gap size
      double high0     = iHigh(_Symbol, _Period, i);              //--- Bar[i] high
      double low2      = iLow(_Symbol,  _Period, i + 2);          //--- Bar[i+2] low
      double gap_H0_L2 = NormalizeDouble((low2 - high0) / _Point, _Digits); //--- Down gap size
      bool FVG_UP   = low0 > high2 && gap_L0_H2 > minPts;         //--- Bullish FVG
      bool FVG_DOWN = low2 > high0 && gap_H0_L2 > minPts;         //--- Bearish FVG
      if (!FVG_UP && !FVG_DOWN) continue;                         //--- No FVG, skip
      datetime time1  = iTime(_Symbol, _Period, i + 1);           //--- Middle candle time
      double   price1 = FVG_UP ? high2 : high0;                   //--- Zone boundary 1
      double   price2 = FVG_UP ? low0  : low2;                    //--- Zone boundary 2
      double   newLow  = MathMin(price1, price2);                 //--- Zone low
      double   newHigh = MathMax(price1, price2);                 //--- Zone high
      bool overlaps = false;                                      //--- Overlap flag
      if (ignoreOverlaps) {                                       //--- Overlap check
         for (int ex = 0; ex < ArraySize(fvgs); ex++) {           //--- Check existing
            double exLow  = MathMin(ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 0),
                                    ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 1)); //--- Existing low
            double exHigh = MathMax(ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 0),
                                    ObjectGetDouble(0, fvgs[ex].name, OBJPROP_PRICE, 1)); //--- Existing high
            if (MathMax(newLow, exLow) < MathMin(newHigh, exHigh)) { //--- Overlap detected
               overlaps = true;                                   //--- Mark overlap
               break;                                             //--- Stop checking
            }
         }
      }
      if (overlaps) continue;                                     //--- Skip overlapping
      string   fvgNAME = FVG_Prefix + "(" + TimeToString(time1) + ")"; //--- Zone name
      if (ObjectFind(0, fvgNAME) >= 0) continue;                  //--- Already exists, skip
      color    fvgClr  = FVG_UP ? CLR_UP : CLR_DOWN;              //--- Zone color
      datetime endTime = time1 + PeriodSeconds(_Period) * FVG_Rec_Ext_Bars; //--- Zone end
      CreateRec(fvgNAME, time1, price1, endTime, price2, fvgClr); //--- Draw zone
      int size = ArraySize(fvgs);                                 //--- Array size
      if (size >= maxFVGs) {                                      //--- At capacity
         ArrayRemove(fvgs, 0, 1);                                 //--- Remove oldest
         size--;                                                  //--- Adjust size
      }
      ArrayResize(fvgs, size + 1);                                //--- Grow array
      fvgs[size].name         = fvgNAME;                          //--- Name
      fvgs[size].startTime    = time1;                            //--- Start time
      fvgs[size].origEndTime  = endTime;                          //--- End time
      fvgs[size].mitTime      = 0;                                //--- No mit yet
      fvgs[size].retTime      = 0;                                //--- No ret yet
      fvgs[size].signal       = false;                            //--- No signal
      fvgs[size].inverted     = false;                            //--- Not inverted
      fvgs[size].mit          = false;                            //--- Not mitigated
      fvgs[size].ret          = false;                            //--- Not retraced
      fvgs[size].origUp       = FVG_UP;                           //--- Direction
      fvgs[size].tradeCount   = 0;                                //--- No trades
      fvgs[size].state        = Normal;                           //--- Normal state
      fvgs[size].newSignal    = false;                            //--- No signal
      fvgs[size].pendingEntry = false;                            //--- Not pending
      // Create label NOW after array is populated so origUp is known
      datetime midT  = time1 + (endTime - time1) / 2;             //--- Mid time
      double   midP  = (price1 + price2) / 2;                     //--- Mid price
      CreateLabel(fvgNAME, midT, midP);                           //--- Label with correct direction
      if (prt) Print("New FVG: ", fvgNAME, " origUp=", FVG_UP);  //--- Log
   }
}

//+------------------------------------------------------------------+
//| Advance FVG states on each new bar using bar[1] (just closed)    |
//+------------------------------------------------------------------+
void UpdateFVGs() {
   double   b1Close = iClose(_Symbol, _Period, 1);                //--- Bar[1] close
   double   b1Low   = iLow(_Symbol,   _Period, 1);                //--- Bar[1] low
   double   b1High  = iHigh(_Symbol,  _Period, 1);                //--- Bar[1] high
   datetime b1Time  = iTime(_Symbol,  _Period, 1);                //--- Bar[1] open time
   bool     removed = false;                                      //--- Removal flag
   for (int j = ArraySize(fvgs) - 1; j >= 0; j--) {               //--- Reverse iterate
      if (ObjectFind(0, fvgs[j].name) < 0) {                      //--- Object missing
         ArrayRemove(fvgs, j, 1);                                 //--- Remove from array
         removed = true;                                          //--- Flag removal
         continue;                                                //--- Next zone
      }
      double fvgLow  = MathMin(ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 0),
                                ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 1)); //--- Zone low
      double fvgHigh = MathMax(ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 0),
                                ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 1)); //--- Zone high
      // STEP 1: MITIGATION
      if (!fvgs[j].mit) {                                         //--- Not yet mitigated
         bool broke = (fvgs[j].origUp  && b1Low  < fvgLow) ||
                      (!fvgs[j].origUp && b1High > fvgHigh);     //--- Far side broken
         if (broke) {                                             //--- Mitigation event
            fvgs[j].mit      = true;                              //--- Mark mitigated
            fvgs[j].ret      = false;                             //--- Clear retrace flag
            fvgs[j].retTime  = 0;                                 //--- Clear retrace time
            fvgs[j].mitTime  = b1Time;                            //--- Record time
            fvgs[j].state    = Mitigated;                         //--- Set state
            fvgs[j].inverted = false;                             //--- Clear inversion flag
            color mitClr = GetFVGColor(fvgs[j].origUp, fvgs[j].state); //--- Mitigated color
            UpdateRec(fvgs[j].name, fvgs[j].startTime, fvgLow, fvgs[j].origEndTime, fvgHigh, mitClr); //--- Apply
            DrawMitIcon(fvgs[j].name, b1Time, fvgHigh, fvgLow, fvgs[j].origUp); //--- Draw icon
            if (prt) Print("Mitigated: ", fvgs[j].name, " t=", TimeToString(b1Time)); //--- Log
         }
      }
      // STEP 2: RETRACE — strictly after mitigation bar
      else if (fvgs[j].mit && !fvgs[j].ret && b1Time > fvgs[j].mitTime) { //--- After mitigation
         bool retraced = (fvgs[j].origUp  && b1High > fvgLow) ||  //--- Bullish: tip above zone low
                         (!fvgs[j].origUp && b1Low  < fvgHigh);  //--- Bearish: tip below zone high
         if (retraced) {                                          //--- Retrace confirmed
            fvgs[j].ret     = true;                               //--- Mark retraced
            fvgs[j].retTime = b1Time;                             //--- Record time
            if (prt) Print("Retraced: ", fvgs[j].name, " t=", TimeToString(b1Time)); //--- Log
         }
      }
      // STEP 3: INVERSION SIGNAL — strictly after retrace bar
      else if (fvgs[j].mit && fvgs[j].ret && !fvgs[j].inverted && b1Time > fvgs[j].retTime) { //--- After retrace
         bool inverted = (fvgs[j].origUp  && b1Close < fvgLow) ||  //--- Closed below → Bearish IFVG
                         (!fvgs[j].origUp && b1Close > fvgHigh);  //--- Closed above → Bullish IFVG
         if (inverted) {                                          //--- Inversion confirmed
            fvgs[j].inverted = true;                              //--- Mark inverted
            fvgs[j].state    = Inverted;                          //--- Set state
            color invClr = GetFVGColor(fvgs[j].origUp, fvgs[j].state); //--- Inverted color
            UpdateRec(fvgs[j].name, fvgs[j].startTime, fvgLow, fvgs[j].origEndTime, fvgHigh, invClr); //--- Apply
            if (prt) Print("INVERTED: ", fvgs[j].name, " origUp=", fvgs[j].origUp, " t=", TimeToString(b1Time)); //--- Log
            if (EntryMode == Entry_ZoneTouch) {                   //--- Zone touch mode
               fvgs[j].pendingEntry = true;                       //--- Arm tick entry
               fvgs[j].newSignal    = false;                      //--- No bar-close trade
            } else {                                              //--- 50% mode
               fvgs[j].newSignal = true;                          //--- Signal for bar-close trade
            }
         }
      }
   }
   if (removed && prt) PrintFVGs();                               //--- Log removals
}

//+------------------------------------------------------------------+
//| Execute trades at 50% midpoint — dollar-based SL and TP          |
//| SL/TP price distance = DollarsToPoints(sl_dollars / tp_dollars)  |
//| placed symmetrically from the actual entry price                 |
//+------------------------------------------------------------------+
void TradeOnFVGs() {
   if (EntryMode == Entry_ZoneTouch) return;                      //--- Not applicable in touch mode
   double Ask    = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_ASK), _Digits); //--- Ask
   double Bid    = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_BID), _Digits); //--- Bid
   for (int j = 0; j < ArraySize(fvgs); j++) {                    //--- Iterate zones
      if (!fvgs[j].newSignal || fvgs[j].mitTime == 0) continue;   //--- Skip no signal
      if (tradeMode == TradeOnce     && fvgs[j].tradeCount >= 1)              { fvgs[j].newSignal = false; continue; } //--- Once limit
      if (tradeMode == LimitedTrades && fvgs[j].tradeCount >= maxTradesPerFVG) { fvgs[j].newSignal = false; continue; } //--- Limited limit
      double fvgLow  = MathMin(ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 0),
                                ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 1)); //--- Zone low
      double fvgHigh = MathMax(ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 0),
                                ObjectGetDouble(0, fvgs[j].name, OBJPROP_PRICE, 1)); //--- Zone high
      double mid = NormalizeDouble((fvgLow + fvgHigh) / 2.0, _Digits); //--- 50% midpoint
      if (fvgs[j].origUp) {                                       //--- Bearish IFVG: SELL
         if (Bid >= mid) {                                        //--- Price at or above mid: market sell
            V2_Place(false, false, 0, fvgLow, fvgHigh, "IFVG Sell");                          //--- Market sell
            if (prt) Print("50% MKT SELL: ", fvgs[j].name, " Bid=", Bid);    //--- Log
         } else {                                                 //--- Price below mid: limit sell at mid
            V2_Place(false, true, mid, fvgLow, fvgHigh, "IFVG 50% SellLimit");    //--- Limit sell
            if (prt) Print("50% LMT SELL: ", fvgs[j].name, " mid=", mid);   //--- Log
         }
      } else {                                                    //--- Bullish IFVG: BUY
         if (Ask <= mid) {                                        //--- Price at or below mid: market buy
            V2_Place(true, false, 0, fvgLow, fvgHigh, "IFVG Buy");                           //--- Market buy
            if (prt) Print("50% MKT BUY:  ", fvgs[j].name, " Ask=", Ask);    //--- Log
         } else {                                                 //--- Price above mid: limit buy at mid
            V2_Place(true, true, mid, fvgLow, fvgHigh, "IFVG 50% BuyLimit");      //--- Limit buy
            if (prt) Print("50% LMT BUY:  ", fvgs[j].name, " mid=", mid);   //--- Log
         }
      }
      fvgs[j].tradeCount++;                                       //--- Increment trade count
      fvgs[j].newSignal = false;                                  //--- Clear signal
      ResetFVGCycle(j, fvgLow, fvgHigh);                         //--- Reset for next cycle
      if (prt) Print("Trade done: ", fvgs[j].name, " count=", fvgs[j].tradeCount); //--- Log
   }
}

//+------------------------------------------------------------------+
//| Reset FVG cycle after a trade so zone can invert again           |
//+------------------------------------------------------------------+
void ResetFVGCycle(int j, double fvgLow, double fvgHigh) {
   fvgs[j].mit          = false;                                  //--- Clear mitigation
   fvgs[j].ret          = false;                                  //--- Clear retrace
   fvgs[j].inverted     = false;                                  //--- Clear inversion
   fvgs[j].mitTime      = 0;                                      //--- Clear mit time
   fvgs[j].retTime      = 0;                                      //--- Clear ret time
   fvgs[j].pendingEntry = false;                                  //--- Clear pending
   fvgs[j].newSignal    = false;                                  //--- Clear signal
   fvgs[j].state        = Normal;                                 //--- Back to normal
   color resetClr = GetFVGColor(fvgs[j].origUp, Normal);          //--- Normal color
   UpdateRec(fvgs[j].name, fvgs[j].startTime, fvgLow, fvgs[j].origEndTime, fvgHigh, resetClr); //--- Apply
   double   midPrice = (fvgLow + fvgHigh) / 2;                    //--- Mid price
   datetime midTime  = fvgs[j].startTime + (fvgs[j].origEndTime - fvgs[j].startTime) / 2; //--- Mid time
   UpdateLabel(fvgs[j].name, midTime, midPrice);                  //--- Refresh label
}

//+------------------------------------------------------------------+
//| Remove expired zones from tracking array (keep drawn on chart)   |
//+------------------------------------------------------------------+
void CleanupExpiredFVGs(datetime curBarTime) {
   bool removed = false;                                          //--- Removal flag
   for (int j = ArraySize(fvgs) - 1; j >= 0; j--) {               //--- Reverse iterate
      if (curBarTime > fvgs[j].origEndTime) {                     //--- Zone expired
         if (prt) Print("Expired: ", fvgs[j].name);               //--- Log
         ArrayRemove(fvgs, j, 1);                                 //--- Remove from array
         removed = true;                                          //--- Flag removal
      }
   }
   if (removed && prt) PrintFVGs();                               //--- Log state
}

//+------------------------------------------------------------------+
//| Draw filled rectangle zone on chart                              |
//+------------------------------------------------------------------+
void CreateRec(string objName, datetime time1, double price1, datetime time2, double price2, color clr) {
   ObjectCreate(0, objName, OBJ_RECTANGLE, 0, time1, price1, time2, price2); //--- Create rect
   ObjectSetInteger(0, objName, OBJPROP_FILL,  true);             //--- Filled
   ObjectSetInteger(0, objName, OBJPROP_COLOR, clr);              //--- Color
   ObjectSetInteger(0, objName, OBJPROP_BACK,  false);            //--- Foreground
   // NOTE: label is created AFTER the fvgs[] array is populated so origUp is known
   ChartRedraw(0);                                                //--- Redraw
}

//+------------------------------------------------------------------+
//| Update existing rectangle zone                                   |
//+------------------------------------------------------------------+
void UpdateRec(string objName, datetime time1, double price1, datetime time2, double price2, color clr) {
   if (ObjectFind(0, objName) < 0) return;                        //--- Object missing, skip
   ObjectSetInteger(0, objName, OBJPROP_TIME,  0, time1);         //--- Time corner 1
   ObjectSetDouble(0,  objName, OBJPROP_PRICE, 0, price1);        //--- Price corner 1
   ObjectSetInteger(0, objName, OBJPROP_TIME,  1, time2);         //--- Time corner 2
   ObjectSetDouble(0,  objName, OBJPROP_PRICE, 1, price2);        //--- Price corner 2
   ObjectSetInteger(0, objName, OBJPROP_COLOR, clr);              //--- Color
   datetime midTime  = time1 + (time2 - time1) / 2;               //--- Mid time
   double   midPrice = (price1 + price2) / 2;                     //--- Mid price
   UpdateLabel(objName, midTime, midPrice);                       //--- Refresh label
   ChartRedraw(0);                                                //--- Redraw
}

//+------------------------------------------------------------------+
//| Create text label at zone centre                                 |
//+------------------------------------------------------------------+
void CreateLabel(string zoneName, datetime time, double price) {
   string lblName = zoneName + "_Label";                          //--- Label name
   ObjectCreate(0, lblName, OBJ_TEXT, 0, time, price);            //--- Create text object
   ObjectSetInteger(0, lblName, OBJPROP_ANCHOR, ANCHOR_CENTER);   //--- Centre anchor
   ObjectSetInteger(0, lblName, OBJPROP_COLOR,  clrBlack);        //--- Black text
   UpdateLabelText(lblName, zoneName);                            //--- Set text content
}

//+------------------------------------------------------------------+
//| Move label to new position                                       |
//+------------------------------------------------------------------+
void UpdateLabel(string zoneName, datetime time, double price) {
   string lblName = zoneName + "_Label";                          //--- Label name
   if (ObjectFind(0, lblName) < 0) return;                        //--- Missing, skip
   ObjectSetInteger(0, lblName, OBJPROP_TIME,  0, time);          //--- Update time
   ObjectSetDouble(0,  lblName, OBJPROP_PRICE, 0, price);         //--- Update price
   UpdateLabelText(lblName, zoneName);                            //--- Refresh text
}

//+------------------------------------------------------------------+
//| Set label text to reflect current zone state                     |
//+------------------------------------------------------------------+
void UpdateLabelText(string lblName, string zoneName) {
   string   text     = "";                                        //--- Text buffer
   int      tradeCnt = 0;                                         //--- Trade count
   FVGState state    = Normal;                                    //--- Zone state
   bool     origUp   = false;                                     //--- Zone direction
   for (int idx = 0; idx < ArraySize(fvgs); idx++) {              //--- Find matching zone
      if (fvgs[idx].name == zoneName) {                           //--- Match found
         tradeCnt = fvgs[idx].tradeCount;                         //--- Get trade count
         state    = fvgs[idx].state;                              //--- Get state
         origUp   = fvgs[idx].origUp;                             //--- Get direction
         break;                                                   //--- Stop searching
      }
   }
   switch (state) {                                               //--- Build label text
      case Normal:
         text = origUp ? "Bullish FVG" : "Bearish FVG";           //--- Normal label
         break;
      case Mitigated:
         text = origUp ? "Mitigated Bullish FVG" : "Mitigated Bearish FVG"; //--- Mitigated label
         break;
      case Inverted:
         text = origUp ? "Bearish Inversed FVG" : "Bullish Inversed FVG";   //--- Inverted label
         break;
   }
   if (tradeCnt > 0) text += " (Traded " + IntegerToString(tradeCnt) + "x)"; //--- Append count
   ObjectSetString(0, lblName, OBJPROP_TEXT, text);               //--- Apply text
}

//+------------------------------------------------------------------+
//| Draw mitigation marker arrow at zone boundary                    |
//+------------------------------------------------------------------+
void DrawMitIcon(string fvgNAME, datetime mitTime, double fvgHigh, double fvgLow, bool isUp) {
   string iconName  = fvgNAME + "_MitIcon";                       //--- Icon object name
   double iconPrice = isUp ? fvgLow : fvgHigh;                    //--- Position at broken boundary
   if (ObjectFind(0, iconName) >= 0) ObjectDelete(0, iconName);   //--- Remove if exists
   ObjectCreate(0, iconName, OBJ_ARROW, 0, mitTime, iconPrice);   //--- Create arrow
   ObjectSetInteger(0, iconName, OBJPROP_ARROWCODE, 251);         //--- Arrow style
   ObjectSetInteger(0, iconName, OBJPROP_COLOR,     clrBlue);     //--- Blue color
   ObjectSetInteger(0, iconName, OBJPROP_ANCHOR,    isUp ? ANCHOR_TOP : ANCHOR_BOTTOM); //--- Anchor side
   ChartRedraw(0);                                                //--- Redraw
}
//+------------------------------------------------------------------+