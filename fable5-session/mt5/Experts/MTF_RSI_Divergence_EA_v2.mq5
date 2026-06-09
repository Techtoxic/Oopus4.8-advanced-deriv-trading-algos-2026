//+------------------------------------------------------------------+
//|                              MTF_RSI_Divergence_EA.mq5           |
//|      Multi-Timeframe RSI Divergence EA with Sync Dashboard       |
//|                                                                  |
//|  Converted from MTF_RSI_Divergence indicator v3.10               |
//|  All indicator logic + visualization preserved exactly.          |
//|                                                                  |
//|  Trades on confirmed divergence signals.                         |
//|  Closes via floating PnL (USD) - no fixed price SL/TP.          |
//+------------------------------------------------------------------+
#property copyright   "MTF RSI Divergence EA"
#property version     "1.00"
#property strict

#include <Trade\Trade.mqh>

//=== INPUTS =============================================================
input group "=== Trade Settings ==="
input double LotSize         = 0.01;  // Lot size (used when RiskPercent=0)
input double TakeProfitUSD   = 10.0;  // [Legacy mode] close at floating profit USD
input double StopLossUSD     = 5.0;   // [Legacy mode] close at floating loss USD
input ulong  MagicNumber     = 20250510;
input bool   CloseOpposite   = true;  // Close opposite-direction trade on new signal

input group "=== V2 Structure Exits & Risk ==="
input bool   UseStructureExits = true;  // SL at divergence pivot + RR TP (false = legacy USD)
input double RiskPercent       = 1.0;   // Risk % of balance (0 = fixed LotSize)
input double SL_ATRBuffer      = 0.5;   // SL buffer beyond divergence pivot (ATR mult)
input int    ATRPeriod         = 14;
input double TP_RR             = 2.0;   // Take profit in R
input bool   UseBreakeven      = true;
input double BE_TriggerR       = 1.0;
input bool   UseGiveback       = true;  // Bank trade if it surrenders peak profit
input double GivebackArmR      = 1.5;
input double GivebackPct       = 50;

input group "=== V2 Signal Quality ==="
input bool   OnlySyncedSignals = false; // Trade only multi-TF synced divergences
input double SyncRiskMult      = 1.5;   // Risk multiplier for synced signals (conviction sizing)
input bool   SkipHiddenDivs    = false; // Skip hidden divergences (keep regular only)

input group "=== RSI Settings ==="
input int                RSI_Period  = 14;
input ENUM_APPLIED_PRICE RSI_Price   = PRICE_CLOSE;

input group "=== Pivot Detection ==="
input int  PivotLookback  = 5;    // Bars each side for pivot confirmation (no repaint)
input int  DivLookback    = 150;  // Bars back to scan and keep on chart

input group "=== Divergence Filters ==="
input bool ShowRegularBull   = true;
input bool ShowRegularBear   = true;
input bool ShowHiddenBull    = true;
input bool ShowHiddenBear    = true;
input int  MinBarsBetween    = 3;
input double MinRSIDiff      = 2.0;   // Minimum RSI difference between pivots
input double MinPriceDiffPct = 0.05;  // Minimum price move % between pivots

input group "=== Visual ==="
input int   DotSize          = 2;
input int   NormalLineWidth  = 1;
input int   SyncedLineWidth  = 3;
input color NormalBullCol    = clrWhite;
input color NormalBearCol    = clrTomato;
input color SyncedBullCol    = clrTeal;
input color SyncedBearCol    = clrCrimson;
input color PriceHighDotCol  = clrOrange;
input color PriceLowDotCol   = clrLimeGreen;
input color RSIHighDotCol    = clrOrangeRed;
input color RSILowDotCol     = clrDodgerBlue;

input group "=== Dashboard ==="
input bool  ShowDashboard  = true;
input int   DashX          = 20;
input int   DashY          = 30;

input group "=== Alerts ==="
input bool  AlertOnSync    = true;
input bool  AlertPopup     = false;
input bool  AlertMobile    = false;

//=======================================================================
// STRUCTS  (unchanged from indicator)
//=======================================================================
struct DivInfo
{
   bool     detected;
   bool     isBull;
   bool     isHidden;
   double   strength;
   datetime timeA;
   datetime timeB;
   double   rsiA;
   double   rsiB;
};

struct SyncPair
{
   ENUM_TIMEFRAMES higherTF;
   ENUM_TIMEFRAMES lowerTF;
   string          label;
};

//=======================================================================
// GLOBALS
//=======================================================================
double   RSIBuffer[];       // Current-TF RSI values (as-series)
int      rsiHandle;         // Handle used for chart sub-window display
int      prevBars  = 0;
datetime lastAlert = 0;
string   PFX       = "MTFDIV_";
int      RSIWin    = 1;     // Sub-window index where RSI is displayed

SyncPair syncPairs[6];

ENUM_TIMEFRAMES allTF[8];
int      rsiHandles[8];     // RSI handles for each of the 8 timeframes
DivInfo  tfDivBull[8];
DivInfo  tfDivBear[8];

// Dashboard colours
color  DASH_BG       = C'15,15,25';
color  DASH_BORDER   = C'40,40,70';
color  DASH_TITLE    = clrWhite;
color  DASH_NEUTRAL  = C'160,160,180';
color  DASH_BULL     = clrLimeGreen;
color  DASH_BEAR     = clrTomato;
color  DASH_SYNC_B   = clrTeal;
color  DASH_SYNC_S   = clrCrimson;
color  DASH_GOLD     = C'218,165,32';

// Trade objects
CTrade trade;

// Signal tracking – only fire when a genuinely new confirmed divergence appears
datetime lastBullSigTime = 0;
datetime lastBearSigTime = 0;

//=======================================================================
int OnInit()
{
   // ---- V2: ATR handle for structure exits ----
   v2ATRHandle = iATR(_Symbol, PERIOD_CURRENT, ATRPeriod);
   if(v2ATRHandle == INVALID_HANDLE)
   {
      Alert("MTF RSI Div EA v2: ATR handle failed");
      return INIT_FAILED;
   }

   // ---- RSI handle for current chart TF (also shown in sub-window) ----
   rsiHandle = iRSI(_Symbol, PERIOD_CURRENT, RSI_Period, RSI_Price);
   if(rsiHandle == INVALID_HANDLE)
   {
      Alert("MTF RSI Div EA: RSI handle failed");
      return INIT_FAILED;
   }

   // Add RSI indicator to chart sub-window so visualization matches indicator
   RSIWin = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL); // new window = current count
   if(!ChartIndicatorAdd(0, RSIWin, rsiHandle))
   {
      RSIWin = 1;  // fallback
      ChartIndicatorAdd(0, RSIWin, rsiHandle);
   }

   // Draw 70/30 horizontal lines on the RSI sub-window
   string lvl70 = PFX + "LVL70";
   string lvl30 = PFX + "LVL30";
   for(int w=0; w<2; w++)
   {
      string nm  = (w==0) ? lvl70 : lvl30;
      double val = (w==0) ? 70.0  : 30.0;
      if(ObjectCreate(0, nm, OBJ_HLINE, RSIWin, 0, val))
      {
         ObjectSetInteger(0, nm, OBJPROP_COLOR, clrDimGray);
         ObjectSetInteger(0, nm, OBJPROP_STYLE, STYLE_DOT);
         ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, nm, OBJPROP_HIDDEN,     true);
      }
   }

   // ---- Timeframe list ----
   allTF[0]=PERIOD_MN1; allTF[1]=PERIOD_W1;  allTF[2]=PERIOD_D1;  allTF[3]=PERIOD_H4;
   allTF[4]=PERIOD_H1;  allTF[5]=PERIOD_M15; allTF[6]=PERIOD_M5;  allTF[7]=PERIOD_M1;

   // ---- Sync pairs ----
   syncPairs[0].higherTF=PERIOD_MN1; syncPairs[0].lowerTF=PERIOD_D1;  syncPairs[0].label="MN -> D1 ";
   syncPairs[1].higherTF=PERIOD_W1;  syncPairs[1].lowerTF=PERIOD_H4;  syncPairs[1].label="W1 -> H4 ";
   syncPairs[2].higherTF=PERIOD_D1;  syncPairs[2].lowerTF=PERIOD_H1;  syncPairs[2].label="D1 -> H1 ";
   syncPairs[3].higherTF=PERIOD_H4;  syncPairs[3].lowerTF=PERIOD_M15; syncPairs[3].label="H4 -> M15";
   syncPairs[4].higherTF=PERIOD_H1;  syncPairs[4].lowerTF=PERIOD_M5;  syncPairs[4].label="H1 -> M5 ";
   syncPairs[5].higherTF=PERIOD_M15; syncPairs[5].lowerTF=PERIOD_M1;  syncPairs[5].label="M15-> M1 ";

   // ---- RSI handles for all 8 timeframes ----
   for(int i=0; i<8; i++)
   {
      rsiHandles[i]         = iRSI(_Symbol, allTF[i], RSI_Period, RSI_Price);
      tfDivBull[i].detected = false;
      tfDivBear[i].detected = false;
   }

   // ---- Trade config ----
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   ChartRedraw(0);
   return INIT_SUCCEEDED;
}

//=======================================================================
void OnDeinit(const int reason)
{
   // Remove all drawn objects
   ObjectsDeleteAll(0, PFX);
   // Remove the 70/30 lines we created with full names
   ObjectDelete(0, PFX + "LVL70");
   ObjectDelete(0, PFX + "LVL30");

   if(rsiHandle != INVALID_HANDLE) IndicatorRelease(rsiHandle);
   for(int i=0; i<8; i++)
      if(rsiHandles[i] != INVALID_HANDLE) IndicatorRelease(rsiHandles[i]);

   ChartRedraw(0);
}

//=======================================================================
// OnTick – main loop
//=======================================================================
void OnTick()
{
   // ----------------------------------------------------------------
   // 1) Manage open trades every tick (PnL-based close)
   // ----------------------------------------------------------------
   ManageOpenTrades();

   // ----------------------------------------------------------------
   // 2) Only re-scan + redraw on a new bar (performance guard)
   // ----------------------------------------------------------------
   int curBars = Bars(_Symbol, PERIOD_CURRENT);
   if(curBars == prevBars) return;
   prevBars = curBars;

   int total = curBars;
   if(total < RSI_Period + PivotLookback*2 + 10) return;

   // ---- Copy current-TF RSI into global buffer ----
   int needed = MathMin(DivLookback + PivotLookback*2 + 10, total - RSI_Period - 2);
   ArraySetAsSeries(RSIBuffer, true);
   if(CopyBuffer(rsiHandle, 0, 0, needed, RSIBuffer) <= 0) return;

   // ---- Copy current-TF price / time arrays ----
   double high[], low[];
   datetime time[];
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low,  true);
   ArraySetAsSeries(time, true);
   if(CopyHigh (_Symbol, PERIOD_CURRENT, 0, needed, high) <= 0) return;
   if(CopyLow  (_Symbol, PERIOD_CURRENT, 0, needed, low)  <= 0) return;
   if(CopyTime (_Symbol, PERIOD_CURRENT, 0, needed, time) <= 0) return;

   // ----------------------------------------------------------------
   // 3) Scan all 8 timeframes (populates tfDivBull / tfDivBear)
   // ----------------------------------------------------------------
   ScanAllTimeframes();

   // ----------------------------------------------------------------
   // 4) Redraw everything (identical to indicator's OnCalculate)
   // ----------------------------------------------------------------
   ObjectsDeleteAll(0, PFX);
   DrawCurrentTF(time, high, low, needed);
   if(ShowDashboard) DrawDashboard();
   ChartRedraw(0);

   // ----------------------------------------------------------------
   // 5) Check for new trade signals on the CURRENT timeframe
   // ----------------------------------------------------------------
   CheckAndTrade(time, high, low, needed);
}

//=======================================================================
// TRADE MANAGEMENT  – close by floating PnL in USD
//=======================================================================
void ManageOpenTrades()
{
   if(UseStructureExits) { V2_Manage(); return; }
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString (POSITION_SYMBOL) != _Symbol)     continue;
      if(PositionGetInteger(POSITION_MAGIC)  != (long)MagicNumber) continue;

      // Floating PnL = profit + swap (commission already deducted at open)
      double pnl = PositionGetDouble(POSITION_PROFIT)
                 + PositionGetDouble(POSITION_SWAP);

      if(pnl >= TakeProfitUSD)
      {
         trade.PositionClose(ticket);
         Print("MTF EA: TP hit. Ticket=", ticket, "  PnL=", DoubleToString(pnl,2));
      }
      else if(pnl <= -StopLossUSD)
      {
         trade.PositionClose(ticket);
         Print("MTF EA: SL hit. Ticket=", ticket, "  PnL=", DoubleToString(pnl,2));
      }
   }
}

//=======================================================================
// SIGNAL CHECK + TRADE ENTRY
//=======================================================================
void CheckAndTrade(const datetime &time[], const double &high[],
                   const double &low[], int total)
{
   ENUM_TIMEFRAMES curTF = Period();
   int scanBars = MathMin(DivLookback + PivotLookback*2 + 5, total - 1);

   // ---- Check sync status (same logic as DrawCurrentTF) ----
   bool syncedBull = false, syncedBear = false;
   for(int p=0; p<6; p++)
   {
      if(syncPairs[p].lowerTF == curTF)
      {
         int hIdx = GetTFIndex(syncPairs[p].higherTF);
         if(hIdx >= 0)
         {
            if(tfDivBull[hIdx].detected) syncedBull = true;
            if(tfDivBear[hIdx].detected) syncedBear = true;
         }
      }
   }

   // ---- Pivot detection ----
   int pivH[], pivL[];
   ArrayResize(pivH, 0);
   ArrayResize(pivL, 0);

   for(int i=PivotLookback; i<scanBars; i++)
   {
      if(IsPivotHigh(high, i, PivotLookback)){ int s=ArraySize(pivH); ArrayResize(pivH,s+1); pivH[s]=i; }
      if(IsPivotLow (low,  i, PivotLookback)){ int s=ArraySize(pivL); ArrayResize(pivL,s+1); pivL[s]=i; }
   }

   // ---- Latest bear divergence (a==0) ----
   int nh = ArraySize(pivH);
   if(nh >= 2)
   {
      int bA=pivH[0], bB=pivH[1];
      if(bB-bA >= MinBarsBetween)
      {
         double pA=high[bA], pB=high[bB];
         double rA=RSIBuffer[bA], rB=RSIBuffer[bB];
         double rDiff=MathAbs(rA-rB);
         double pDiff=(pB>0)?MathAbs(pA-pB)/pB*100.0:0;

         if(rDiff >= MinRSIDiff && pDiff >= MinPriceDiffPct)
         {
            bool regBear = ShowRegularBear && pA > pB && rA < rB;
            bool hidBear = ShowHiddenBear  && pA < pB && rA > rB;

            if((regBear || hidBear) && time[bA] != lastBearSigTime)
            {
               if(SkipHiddenDivs && hidBear) {}
               else
               {
                  lastBearSigTime = time[bA];
                  OpenTrade(false, syncedBear, hidBear, high[bA]);
               }
            }
         }
      }
   }

   // ---- Latest bull divergence (a==0) ----
   int nl = ArraySize(pivL);
   if(nl >= 2)
   {
      int bA=pivL[0], bB=pivL[1];
      if(bB-bA >= MinBarsBetween)
      {
         double pA=low[bA], pB=low[bB];
         double rA=RSIBuffer[bA], rB=RSIBuffer[bB];
         double rDiff=MathAbs(rA-rB);
         double pDiff=(pB>0)?MathAbs(pA-pB)/pB*100.0:0;

         if(rDiff >= MinRSIDiff && pDiff >= MinPriceDiffPct)
         {
            bool regBull = ShowRegularBull && pA < pB && rA > rB;
            bool hidBull = ShowHiddenBull  && pA > pB && rA < rB;

            if((regBull || hidBull) && time[bA] != lastBullSigTime)
            {
               if(SkipHiddenDivs && hidBull) {}
               else
               {
                  lastBullSigTime = time[bA];
                  OpenTrade(true, syncedBull, hidBull, low[bA]);
               }
            }
         }
      }
   }
}

//=======================================================================
// OPEN TRADE
//=======================================================================
void OpenTrade(bool isBull, bool isSynced, bool isHidden, double pivotPrice)
{
   // V2: option to require multi-TF sync
   if(OnlySyncedSignals && !isSynced) return;

   // Optionally close the opposing direction first
   if(CloseOpposite) CloseByDirection(!isBull);

   // Avoid duplicate trades in the same direction
   if(HasOpenTrade(isBull)) return;

   double price = isBull ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   string kind = (isSynced ? "[SYNC] " : "") + (isHidden ? "Hidden " : "Regular ")
               + (isBull ? "Bullish" : "Bearish") + " Div";

   double sl = 0, tp = 0, lot = LotSize;
   if(UseStructureExits)
   {
      double a[1];
      double atr = (CopyBuffer(v2ATRHandle, 0, 1, 1, a) == 1) ? a[0] : 0;
      if(atr <= 0) return;
      sl = isBull ? pivotPrice - SL_ATRBuffer * atr
                  : pivotPrice + SL_ATRBuffer * atr;
      double slPts = MathAbs(price - sl) / _Point;
      if(slPts <= 0) return;
      tp = isBull ? price + TP_RR * slPts * _Point
                  : price - TP_RR * slPts * _Point;
      double mult = (isSynced ? SyncRiskMult : 1.0);
      lot = V2_CalcLot(slPts, mult);
      sl = NormalizeDouble(sl, _Digits);
      tp = NormalizeDouble(tp, _Digits);
   }

   if(isBull)
      trade.Buy (lot, _Symbol, price, sl, tp, kind);
   else
      trade.Sell(lot, _Symbol, price, sl, tp, kind);

   if(trade.ResultRetcode() == TRADE_RETCODE_DONE ||
      trade.ResultRetcode() == TRADE_RETCODE_PLACED)
      Print("MTF EA v2: Opened ", kind, "  Lot=", lot, "  Price=", price,
            "  SL=", sl, "  TP=", tp);
   else
      Print("MTF EA v2: Order failed (", trade.ResultRetcodeDescription(), ")");
}

//=======================================================================
// V2 HELPERS
//=======================================================================
int    v2ATRHandle = INVALID_HANDLE;
double v2PeakR[];
ulong  v2PeakTk[];

double V2_NormalizeLot(double lot)
{
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(st <= 0) st = 0.01;
   lot = MathFloor(lot / st) * st;
   return MathMax(mn, MathMin(mx, NormalizeDouble(lot, 2)));
}

double V2_CalcLot(double slPts, double mult)
{
   if(RiskPercent <= 0 || slPts <= 0) return V2_NormalizeLot(LotSize * mult);
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0) return V2_NormalizeLot(LotSize * mult);
   double money = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0 * mult;
   double perLot = slPts * _Point / ts * tv;
   return (perLot > 0) ? V2_NormalizeLot(money / perLot) : V2_NormalizeLot(LotSize * mult);
}

void V2_Manage()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
      bool   isBuy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double op  = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl  = PositionGetDouble(POSITION_SL);
      double tp  = PositionGetDouble(POSITION_TP);
      double cur = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                         : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double riskPts = (sl > 0) ? MathAbs(op - sl) / _Point : 0;
      if(riskPts <= 0) continue;
      double r = (isBuy ? cur - op : op - cur) / _Point / riskPts;
      int idx = -1;
      for(int k = 0; k < ArraySize(v2PeakTk); k++)
         if(v2PeakTk[k] == tk) { idx = k; break; }
      if(idx < 0)
      {
         idx = ArraySize(v2PeakTk);
         ArrayResize(v2PeakTk, idx + 1);
         ArrayResize(v2PeakR, idx + 1);
         v2PeakTk[idx] = tk;
         v2PeakR[idx] = r;
      }
      if(r > v2PeakR[idx]) v2PeakR[idx] = r;

      if(UseGiveback && v2PeakR[idx] >= GivebackArmR &&
         r < v2PeakR[idx] * GivebackPct / 100.0)
      {
         if(trade.PositionClose(tk))
            Print("MTF EA v2: giveback close peak=", DoubleToString(v2PeakR[idx], 2),
                  "R now=", DoubleToString(r, 2), "R");
         continue;
      }
      if(UseBreakeven && r >= BE_TriggerR)
      {
         double be = isBuy ? op + 2 * _Point : op - 2 * _Point;
         bool needs = isBuy ? (sl < be - _Point) : (sl > be + _Point);
         if(needs) trade.PositionModify(tk, NormalizeDouble(be, _Digits), tp);
      }
   }
}

//=======================================================================
// HELPERS: position queries
//=======================================================================
bool HasOpenTrade(bool isBull)
{
   ENUM_POSITION_TYPE typ = isBull ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetString (POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)  != (long)MagicNumber) continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == typ) return true;
   }
   return false;
}

void CloseByDirection(bool isBull)
{
   ENUM_POSITION_TYPE typ = isBull ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetString (POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)  != (long)MagicNumber) continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == typ)
         trade.PositionClose(t);
   }
}

//=======================================================================
// ============================================================
//  ALL CODE BELOW IS UNCHANGED FROM THE INDICATOR  (verbatim)
// ============================================================

int GetTFIndex(ENUM_TIMEFRAMES tf)
{
   for(int i=0; i<8; i++) if(allTF[i]==tf) return i;
   return -1;
}

string TFLabel(ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_MN1: return "MN ";
      case PERIOD_W1:  return "W1 ";
      case PERIOD_D1:  return "D1 ";
      case PERIOD_H4:  return "H4 ";
      case PERIOD_H1:  return "H1 ";
      case PERIOD_M15: return "M15";
      case PERIOD_M5:  return "M5 ";
      case PERIOD_M1:  return "M1 ";
      default:         return "?  ";
   }
}

bool IsPivotHigh(const double &h[], int bar, int lb)
{
   int sz = ArraySize(h);
   if(bar-lb < 0 || bar+lb >= sz) return false;
   double v = h[bar];
   for(int i=1; i<=lb; i++) if(h[bar-i]>=v || h[bar+i]>=v) return false;
   return true;
}

bool IsPivotLow(const double &l[], int bar, int lb)
{
   int sz = ArraySize(l);
   if(bar-lb < 0 || bar+lb >= sz) return false;
   double v = l[bar];
   for(int i=1; i<=lb; i++) if(l[bar-i]<=v || l[bar+i]<=v) return false;
   return true;
}

//=======================================================================
void ScanAllTimeframes()
{
   for(int t=0; t<8; t++)
   {
      tfDivBull[t].detected = false;
      tfDivBear[t].detected = false;

      int handle = rsiHandles[t];
      if(handle == INVALID_HANDLE) continue;

      int bars   = Bars(_Symbol, allTF[t]);
      int needed = MathMin(DivLookback + PivotLookback*2 + 10, bars - RSI_Period - 2);
      if(needed < PivotLookback*2 + 5) continue;

      double rsiTF[], hTF[], lTF[];
      datetime tTF[];
      ArraySetAsSeries(rsiTF, true);
      ArraySetAsSeries(hTF,   true);
      ArraySetAsSeries(lTF,   true);
      ArraySetAsSeries(tTF,   true);

      if(CopyBuffer(rsiHandles[t], 0, 0, needed, rsiTF) <= 0) continue;
      if(CopyHigh(_Symbol, allTF[t], 0, needed, hTF)   <= 0) continue;
      if(CopyLow (_Symbol, allTF[t], 0, needed, lTF)   <= 0) continue;
      if(CopyTime(_Symbol, allTF[t], 0, needed, tTF)   <= 0) continue;

      int pivH[], pivL[];
      ArrayResize(pivH, 0);
      ArrayResize(pivL, 0);

      int scanEnd = MathMin(needed - PivotLookback - 1, ArraySize(hTF) - PivotLookback - 1);
      for(int i=PivotLookback; i<scanEnd; i++)
      {
         if(IsPivotHigh(hTF, i, PivotLookback)){ int s=ArraySize(pivH); ArrayResize(pivH,s+1); pivH[s]=i; }
         if(IsPivotLow (lTF, i, PivotLookback)){ int s=ArraySize(pivL); ArrayResize(pivL,s+1); pivL[s]=i; }
      }

      // Bear divergences
      int nh = ArraySize(pivH);
      for(int a=0; a<nh-1; a++)
      {
         int bA=pivH[a], bB=pivH[a+1];
         if(bB-bA < MinBarsBetween) continue;
         double pA=hTF[bA], pB=hTF[bB];
         double rA=rsiTF[bA], rB=rsiTF[bB];
         double rDiff = MathAbs(rA-rB);
         double pDiff = (pB > 0) ? MathAbs(pA-pB)/pB*100.0 : 0;
         if(rDiff < MinRSIDiff || pDiff < MinPriceDiffPct) continue;

         bool regBear = ShowRegularBear && pA > pB && rA < rB;
         bool hidBear = ShowHiddenBear  && pA < pB && rA > rB;
         if(regBear || hidBear)
         {
            tfDivBear[t].detected = true;
            tfDivBear[t].isBull   = false;
            tfDivBear[t].isHidden = hidBear;
            tfDivBear[t].strength = rDiff;
            tfDivBear[t].timeA    = tTF[bA];
            tfDivBear[t].timeB    = tTF[bB];
            tfDivBear[t].rsiA     = rA;
            tfDivBear[t].rsiB     = rB;
            break;
         }
      }

      // Bull divergences
      int nl = ArraySize(pivL);
      for(int a=0; a<nl-1; a++)
      {
         int bA=pivL[a], bB=pivL[a+1];
         if(bB-bA < MinBarsBetween) continue;
         double pA=lTF[bA], pB=lTF[bB];
         double rA=rsiTF[bA], rB=rsiTF[bB];
         double rDiff = MathAbs(rA-rB);
         double pDiff = (pB > 0) ? MathAbs(pA-pB)/pB*100.0 : 0;
         if(rDiff < MinRSIDiff || pDiff < MinPriceDiffPct) continue;

         bool regBull = ShowRegularBull && pA < pB && rA > rB;
         bool hidBull = ShowHiddenBull  && pA > pB && rA < rB;
         if(regBull || hidBull)
         {
            tfDivBull[t].detected = true;
            tfDivBull[t].isBull   = true;
            tfDivBull[t].isHidden = hidBull;
            tfDivBull[t].strength = rDiff;
            tfDivBull[t].timeA    = tTF[bA];
            tfDivBull[t].timeB    = tTF[bB];
            tfDivBull[t].rsiA     = rA;
            tfDivBull[t].rsiB     = rB;
            break;
         }
      }
   }
}

//=======================================================================
void DrawCurrentTF(const datetime &time[], const double &high[],
                   const double &low[], int total)
{
   ENUM_TIMEFRAMES curTF = Period();

   bool syncedBull = false, syncedBear = false;
   for(int p=0; p<6; p++)
   {
      if(syncPairs[p].lowerTF == curTF)
      {
         int hIdx = GetTFIndex(syncPairs[p].higherTF);
         if(hIdx >= 0)
         {
            if(tfDivBull[hIdx].detected) syncedBull = true;
            if(tfDivBear[hIdx].detected) syncedBear = true;
         }
      }
   }

   int scanBars = MathMin(DivLookback + PivotLookback*2 + 5, total - RSI_Period - 2);

   int pivH[], pivL[];
   ArrayResize(pivH, 0);
   ArrayResize(pivL, 0);

   for(int i=PivotLookback; i<scanBars; i++)
   {
      if(IsPivotHigh(high, i, PivotLookback)){ int s=ArraySize(pivH); ArrayResize(pivH,s+1); pivH[s]=i; }
      if(IsPivotLow (low,  i, PivotLookback)){ int s=ArraySize(pivL); ArrayResize(pivL,s+1); pivL[s]=i; }
   }

   for(int k=0; k<ArraySize(pivH); k++) DrawDot(pivH[k], true,  time, high, low);
   for(int k=0; k<ArraySize(pivL); k++) DrawDot(pivL[k], false, time, high, low);

   int nh = ArraySize(pivH);
   for(int a=0; a<nh-1; a++)
   {
      int bA=pivH[a], bB=pivH[a+1];
      if(bB-bA < MinBarsBetween) continue;
      double pA=high[bA], pB=high[bB];
      double rA=RSIBuffer[bA], rB=RSIBuffer[bB];
      double rDiff=MathAbs(rA-rB);
      double pDiff=(pB>0)?MathAbs(pA-pB)/pB*100.0:0;
      if(rDiff < MinRSIDiff || pDiff < MinPriceDiffPct) continue;

      bool regBear = ShowRegularBear && pA > pB && rA < rB;
      bool hidBear = ShowHiddenBear  && pA < pB && rA > rB;
      if(!regBear && !hidBear) continue;

      bool isSynced = (a == 0) && syncedBear;
      color  lc = isSynced ? SyncedBearCol : NormalBearCol;
      int    lw = isSynced ? SyncedLineWidth : NormalLineWidth;
      string tag = "B" + IntegerToString(bA) + "x" + IntegerToString(bB);
      DrawDiv(bA, bB, true, hidBear, isSynced, lc, lw, time, high, low, tag, rDiff);

      if(a==0 && AlertOnSync && isSynced)
         DoAlert(false, hidBear, time[bA]);
   }

   int nl = ArraySize(pivL);
   for(int a=0; a<nl-1; a++)
   {
      int bA=pivL[a], bB=pivL[a+1];
      if(bB-bA < MinBarsBetween) continue;
      double pA=low[bA], pB=low[bB];
      double rA=RSIBuffer[bA], rB=RSIBuffer[bB];
      double rDiff=MathAbs(rA-rB);
      double pDiff=(pB>0)?MathAbs(pA-pB)/pB*100.0:0;
      if(rDiff < MinRSIDiff || pDiff < MinPriceDiffPct) continue;

      bool regBull = ShowRegularBull && pA < pB && rA > rB;
      bool hidBull = ShowHiddenBull  && pA > pB && rA < rB;
      if(!regBull && !hidBull) continue;

      bool isSynced = (a == 0) && syncedBull;
      color  lc = isSynced ? SyncedBullCol : NormalBullCol;
      int    lw = isSynced ? SyncedLineWidth : NormalLineWidth;
      string tag = "U" + IntegerToString(bA) + "x" + IntegerToString(bB);
      DrawDiv(bA, bB, false, hidBull, isSynced, lc, lw, time, high, low, tag, rDiff);

      if(a==0 && AlertOnSync && isSynced)
         DoAlert(true, hidBull, time[bA]);
   }
}

//=======================================================================
void DrawDot(int bar, bool isHigh, const datetime &time[],
             const double &high[], const double &low[])
{
   string pn = PFX + "PD" + (isHigh ? "H" : "L") + IntegerToString(bar);
   double pv = isHigh ? high[bar] : low[bar];
   color  pc = isHigh ? PriceHighDotCol : PriceLowDotCol;
   if(ObjectCreate(0, pn, OBJ_ARROW, 0, time[bar], pv))
   {
      ObjectSetInteger(0, pn, OBJPROP_ARROWCODE,  159);
      ObjectSetInteger(0, pn, OBJPROP_COLOR,      pc);
      ObjectSetInteger(0, pn, OBJPROP_WIDTH,      DotSize);
      ObjectSetInteger(0, pn, OBJPROP_ANCHOR,     isHigh ? ANCHOR_BOTTOM : ANCHOR_TOP);
      ObjectSetInteger(0, pn, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, pn, OBJPROP_HIDDEN,     true);
   }
   string rn = PFX + "RD" + (isHigh ? "H" : "L") + IntegerToString(bar);
   double rv = RSIBuffer[bar];
   color  rc = isHigh ? RSIHighDotCol : RSILowDotCol;
   if(ObjectCreate(0, rn, OBJ_ARROW, RSIWin, time[bar], rv))
   {
      ObjectSetInteger(0, rn, OBJPROP_ARROWCODE,  159);
      ObjectSetInteger(0, rn, OBJPROP_COLOR,      rc);
      ObjectSetInteger(0, rn, OBJPROP_WIDTH,      DotSize);
      ObjectSetInteger(0, rn, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, rn, OBJPROP_HIDDEN,     true);
   }
}

//=======================================================================
void DrawDiv(int bA, int bB, bool isBear, bool isHidden, bool isSynced,
             color lc, int lw, const datetime &time[],
             const double &high[], const double &low[],
             string tag, double strength)
{
   color  ac  = lc;
   int    aco = isBear ? 242 : 241;
   ENUM_LINE_STYLE lstyle = isSynced ? STYLE_SOLID : STYLE_DASH;

   double pA = isBear ? high[bA] : low[bA];
   double pB = isBear ? high[bB] : low[bB];
   double rA = RSIBuffer[bA];
   double rB = RSIBuffer[bB];

   // Price line
   string lp = PFX + "LP" + tag;
   if(ObjectCreate(0, lp, OBJ_TREND, 0, time[bB], pB, time[bA], pA))
   {
      ObjectSetInteger(0, lp, OBJPROP_COLOR,      lc);
      ObjectSetInteger(0, lp, OBJPROP_WIDTH,      lw);
      ObjectSetInteger(0, lp, OBJPROP_STYLE,      lstyle);
      ObjectSetInteger(0, lp, OBJPROP_RAY_RIGHT,  false);
      ObjectSetInteger(0, lp, OBJPROP_RAY_LEFT,   false);
      ObjectSetInteger(0, lp, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lp, OBJPROP_HIDDEN,     true);
   }

   // RSI line
   string lr = PFX + "LR" + tag;
   if(ObjectCreate(0, lr, OBJ_TREND, RSIWin, time[bB], rB, time[bA], rA))
   {
      ObjectSetInteger(0, lr, OBJPROP_COLOR,      lc);
      ObjectSetInteger(0, lr, OBJPROP_WIDTH,      lw);
      ObjectSetInteger(0, lr, OBJPROP_STYLE,      lstyle);
      ObjectSetInteger(0, lr, OBJPROP_RAY_RIGHT,  false);
      ObjectSetInteger(0, lr, OBJPROP_RAY_LEFT,   false);
      ObjectSetInteger(0, lr, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lr, OBJPROP_HIDDEN,     true);
   }

   // Price arrow
   string ap = PFX + "AP" + tag;
   if(ObjectCreate(0, ap, OBJ_ARROW, 0, time[bA], pA))
   {
      ObjectSetInteger(0, ap, OBJPROP_ARROWCODE,  aco);
      ObjectSetInteger(0, ap, OBJPROP_COLOR,      ac);
      ObjectSetInteger(0, ap, OBJPROP_WIDTH,      isSynced ? 4 : 2);
      ObjectSetInteger(0, ap, OBJPROP_ANCHOR,     isBear ? ANCHOR_BOTTOM : ANCHOR_TOP);
      ObjectSetInteger(0, ap, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, ap, OBJPROP_HIDDEN,     true);
   }

   // RSI arrow
   string ar = PFX + "AR" + tag;
   if(ObjectCreate(0, ar, OBJ_ARROW, RSIWin, time[bA], rA))
   {
      ObjectSetInteger(0, ar, OBJPROP_ARROWCODE,  aco);
      ObjectSetInteger(0, ar, OBJPROP_COLOR,      ac);
      ObjectSetInteger(0, ar, OBJPROP_WIDTH,      isSynced ? 4 : 2);
      ObjectSetInteger(0, ar, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, ar, OBJPROP_HIDDEN,     true);
   }

   // Strength label
   string lb = PFX + "LB" + tag;
   string lblTxt = (isSynced ? "* " : "") + (isHidden ? "H " : "R ") + DoubleToString(strength, 1);
   if(ObjectCreate(0, lb, OBJ_TEXT, 0, time[bA], pA))
   {
      ObjectSetString (0, lb, OBJPROP_TEXT,       lblTxt);
      ObjectSetInteger(0, lb, OBJPROP_COLOR,      ac);
      ObjectSetInteger(0, lb, OBJPROP_FONTSIZE,   isSynced ? 8 : 7);
      ObjectSetInteger(0, lb, OBJPROP_ANCHOR,     isBear ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, lb, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lb, OBJPROP_HIDDEN,     true);
   }
}

//=======================================================================
// DASHBOARD  (unchanged from indicator)
//=======================================================================
void MakeRect(string name, int x, int y, int w, int h, color bg, color border)
{
   string n = PFX + "BG_" + name;
   if(ObjectCreate(0, n, OBJ_RECTANGLE_LABEL, 0, 0, 0))
   {
      ObjectSetInteger(0, n, OBJPROP_XDISTANCE,   x);
      ObjectSetInteger(0, n, OBJPROP_YDISTANCE,   y);
      ObjectSetInteger(0, n, OBJPROP_XSIZE,       w);
      ObjectSetInteger(0, n, OBJPROP_YSIZE,       h);
      ObjectSetInteger(0, n, OBJPROP_BGCOLOR,     bg);
      ObjectSetInteger(0, n, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, n, OBJPROP_COLOR,       border);
      ObjectSetInteger(0, n, OBJPROP_WIDTH,       1);
      ObjectSetInteger(0, n, OBJPROP_BACK,        true);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE,  false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN,      true);
   }
}

void MakeLabel(string name, int x, int y, string txt, color clr, int sz=8)
{
   string n = PFX + "LB_" + name;
   if(ObjectCreate(0, n, OBJ_LABEL, 0, 0, 0))
   {
      ObjectSetInteger(0, n, OBJPROP_XDISTANCE,  x);
      ObjectSetInteger(0, n, OBJPROP_YDISTANCE,  y);
      ObjectSetString (0, n, OBJPROP_TEXT,        txt);
      ObjectSetInteger(0, n, OBJPROP_COLOR,       clr);
      ObjectSetInteger(0, n, OBJPROP_FONTSIZE,    sz);
      ObjectSetString (0, n, OBJPROP_FONT,        "Consolas");
      ObjectSetInteger(0, n, OBJPROP_CORNER,      CORNER_LEFT_UPPER);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE,  false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN,      true);
   }
}

string StrBar(double s)
{
   int lvl = (int)MathMin(5, MathMax(1, MathRound(s / 5.0)));
   string b = "";
   for(int i=0; i<lvl; i++) b += "|";
   for(int i=lvl; i<5; i++) b += ".";
   return b;
}

void DrawDashboard()
{
   int x  = DashX;
   int y  = DashY;
   int rh = 15;
   int w  = 300;
   int totalRows = 12 + 6 + 10;
   int h  = totalRows * rh + 10;

   MakeRect("MAIN", x, y, w, h, DASH_BG, DASH_BORDER);

   int cx = x + 8;
   int cy = y + 6;

   MakeLabel("TITLE", cx, cy, "  MTF RSI DIVERGENCE EA v1.0", DASH_TITLE, 9);
   cy += rh + 2;
   MakeLabel("SEP1",  cx, cy, "-------------------------------", DASH_BORDER, 7);
   cy += rh - 3;

   MakeLabel("CH1", cx,       cy, "PAIR     ", DASH_NEUTRAL, 8);
   MakeLabel("CH2", cx + 85,  cy, "BULL",      DASH_BULL,    8);
   MakeLabel("CH3", cx + 155, cy, "BEAR",      DASH_BEAR,    8);
   MakeLabel("CH4", cx + 225, cy, "SYNC",      DASH_GOLD,    8);
   cy += rh;

   ENUM_TIMEFRAMES curTF = Period();

   for(int p=0; p<6; p++)
   {
      int hIdx = GetTFIndex(syncPairs[p].higherTF);
      int lIdx = GetTFIndex(syncPairs[p].lowerTF);

      bool hBull = (hIdx>=0) && tfDivBull[hIdx].detected;
      bool hBear = (hIdx>=0) && tfDivBear[hIdx].detected;
      bool lBull = (lIdx>=0) && tfDivBull[lIdx].detected;
      bool lBear = (lIdx>=0) && tfDivBear[lIdx].detected;
      bool syncBull = hBull && lBull;
      bool syncBear = hBear && lBear;
      bool isCurPair = (syncPairs[p].lowerTF==curTF || syncPairs[p].higherTF==curTF);

      color rowCol = isCurPair ? clrWhite : DASH_NEUTRAL;
      string pid = IntegerToString(p);

      MakeLabel("PR"+pid, cx, cy, syncPairs[p].label, rowCol, 8);

      string bullTxt = hBull ? StrBar(tfDivBull[hIdx].strength)+(tfDivBull[hIdx].isHidden?"H":"R") : "-----";
      color  bullCol = syncBull ? DASH_SYNC_B : (hBull ? DASH_BULL : DASH_NEUTRAL);
      MakeLabel("PB"+pid, cx+85, cy, bullTxt, bullCol, 8);

      string bearTxt = hBear ? StrBar(tfDivBear[hIdx].strength)+(tfDivBear[hIdx].isHidden?"H":"R") : "-----";
      color  bearCol = syncBear ? DASH_SYNC_S : (hBear ? DASH_BEAR : DASH_NEUTRAL);
      MakeLabel("PS"+pid, cx+155, cy, bearTxt, bearCol, 8);

      string syncTxt = "  -";
      color  syncCol = DASH_NEUTRAL;
      if(syncBull) { syncTxt = "* BULL"; syncCol = DASH_SYNC_B; }
      if(syncBear) { syncTxt = "* BEAR"; syncCol = DASH_SYNC_S; }
      if(syncBull && syncBear) { syncTxt = "* BOTH"; syncCol = DASH_GOLD; }
      MakeLabel("PY"+pid, cx+225, cy, syncTxt, syncCol, 8);

      cy += rh - 1;
   }

   cy += 2;
   MakeLabel("SEP2", cx, cy, "-------------------------------", DASH_BORDER, 7);
   cy += rh - 3;

   MakeLabel("RH", cx, cy, "TF    RSI    DIV STATE", DASH_NEUTRAL, 8);
   cy += rh;

   for(int t=0; t<8; t++)
   {
      double rArr[];
      ArraySetAsSeries(rArr, true);
      double curRSI = 50.0;
      if(rsiHandles[t] != INVALID_HANDLE)
         if(CopyBuffer(rsiHandles[t], 0, 0, 3, rArr) > 0) curRSI = rArr[0];

      bool isCurTF = (allTF[t] == curTF);
      color tfCol  = isCurTF ? clrWhite : DASH_NEUTRAL;
      color rsiCol = (curRSI > 70) ? DASH_BEAR : ((curRSI < 30) ? DASH_BULL : tfCol);

      string divTxt = "  none";
      color  divCol = DASH_NEUTRAL;
      if(tfDivBull[t].detected && tfDivBear[t].detected) { divTxt = "^ v BOTH"; divCol = DASH_GOLD; }
      else if(tfDivBull[t].detected) { divTxt = "^  BULL"; divCol = DASH_BULL; }
      else if(tfDivBear[t].detected) { divTxt = "v  BEAR"; divCol = DASH_BEAR; }

      string tid = IntegerToString(t);
      MakeLabel("TL"+tid, cx,     cy, TFLabel(allTF[t]),              tfCol,  8);
      MakeLabel("TV"+tid, cx+35,  cy, StringFormat("%-6.1f", curRSI), rsiCol, 8);
      MakeLabel("TD"+tid, cx+100, cy, divTxt,                         divCol, 8);
      cy += rh - 1;
   }

   cy += 3;
   string footer = "  TF: " + TFLabel(curTF) + "  RSI(" + IntegerToString(RSI_Period) + ")  Pivot:" + IntegerToString(PivotLookback);
   MakeLabel("FOOT", cx, cy, footer, DASH_NEUTRAL, 7);
}

//=======================================================================
void DoAlert(bool isBull, bool isHidden, datetime barTime)
{
   if(!AlertPopup && !AlertMobile) return;
   if(barTime == lastAlert) return;
   lastAlert = barTime;
   string kind = (isHidden ? "Hidden " : "Regular ") + (isBull ? "Bullish" : "Bearish");
   string msg  = _Symbol + " " + EnumToString(Period()) + " | SYNCED RSI Div: " + kind;
   if(AlertPopup)  Alert(msg);
   if(AlertMobile) SendNotification(msg);
}
//+------------------------------------------------------------------+