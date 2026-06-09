//+------------------------------------------------------------------+
//|                                  GoldSilver_HedgeGuardian_EA.mq5 |
//|        The hedging EA done right — for XAUUSD / XAGUSD            |
//|                                                                    |
//|  Two genuinely different hedge engines in one EA:                  |
//|                                                                    |
//|  MODE 1 — ZONE RECOVERY (single symbol, e.g. XAUUSD):              |
//|   A directional seed trade (EMA+ADX trend signal). If price        |
//|   moves against it by an ATR-adaptive zone, an opposing hedge of   |
//|   `factor x` volume is opened, so the basket exits at a bounded    |
//|   outcome at either zone edge. Unlike the martingales that blow    |
//|   accounts: legs are HARD-CAPPED (default 4), the worst case is    |
//|   computed in advance and refused if it exceeds your risk cap,     |
//|   and a full-basket equity stop + daily loss cap + cooldown        |
//|   stand behind everything. Zone width adapts to ATR every cycle,   |
//|   so it stays calibrated as volatility changes (6 months or 6      |
//|   years from now).                                                 |
//|                                                                    |
//|  MODE 2 — RATIO PAIR HEDGE (XAU vs XAG, market-neutral):           |
//|   The gold/silver ratio mean-reverts. When its z-score over a      |
//|   rolling window stretches beyond +Z: SHORT gold + LONG silver     |
//|   (notional-matched); beyond -Z: the reverse. Exit at z≈0,         |
//|   stop at z-stop. You are hedged against metal-complex moves and   |
//|   trade only the spread. This is what "hedging" means at a desk.   |
//|                                                                    |
//|  Both engines share: equity guardian (max DD stop), daily loss     |
//|  cap, spread filter, session filter, full HUD.                     |
//+------------------------------------------------------------------+
#property copyright "Techtoxic + Claude Fable 5, 2026"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

enum ENUM_HEDGE_MODE { MODE_ZONE_RECOVERY, MODE_RATIO_PAIR };

//=== INPUTS =========================================================
input group "=== Engine ==="
input ENUM_HEDGE_MODE InpMode   = MODE_ZONE_RECOVERY;
input long    InpMagic          = 224404;
input int     InpSlippagePoints = 50;

input group "=== Equity Guardian (both modes) ==="
input double  InpMaxBasketLossPct = 3.0;  // Hard close-all if basket loss > % of balance
input double  InpDailyLossCapPct  = 5.0;  // Stop trading for the day beyond this loss
input int     InpCooldownBars     = 24;   // Bars to wait after a basket stop-out
input double  InpMaxSpreadPoints  = 600;  // Skip entries above this spread
input bool    InpUseSession       = false;
input int     InpSessionStartHr   = 1;
input int     InpSessionEndHr     = 23;

input group "=== Zone Recovery (Mode 1) ==="
input double  InpSeedRiskPct    = 0.5;   // Seed sizing: risk % at first zone edge
input double  InpZone_ATR       = 1.2;   // Zone width = x * ATR(H1)
input double  InpTarget_ATR     = 1.8;   // Profit target beyond zone = x * ATR(H1)
input double  InpHedgeFactor    = 2.0;   // Volume multiplier for each hedge leg
input int     InpMaxLegs        = 4;     // HARD cap on total legs (seed included)
input double  InpBasketTPPct    = 1.0;   // Also close basket at +% of balance
input bool    InpTrailBasket    = true;  // Trail basket profit once > 0.5 * target
input int     InpEMAFast        = 21;    // Seed signal
input int     InpEMASlow        = 55;
input int     InpADXPeriod      = 14;
input double  InpADXMin         = 18.0;  // No seed in chop
input int     InpATRPeriod      = 14;

input group "=== Ratio Pair (Mode 2) ==="
input string  InpGoldSymbol     = "XAUUSD";
input string  InpSilverSymbol   = "XAGUSD";
input ENUM_TIMEFRAMES InpRatioTF = PERIOD_H1;
input int     InpRatioWindow    = 200;   // Rolling window for ratio z-score
input double  InpZEntry         = 2.0;   // Enter when |z| >= this
input double  InpZExit          = 0.3;   // Exit when |z| <= this
input double  InpZStop          = 3.5;   // Stop when |z| >= this
input double  InpPairRiskPct    = 1.0;   // Notional sizing: % balance per leg pair
input int     InpPairMaxBars    = 400;   // Time stop in bars of RatioTF

//=== GLOBALS ========================================================
CTrade trade;
int hATR = INVALID_HANDLE, hADX = INVALID_HANDLE, hEMAf = INVALID_HANDLE, hEMAs = INVALID_HANDLE;
datetime lastBar = 0;
datetime cooldownUntil = 0;
datetime dayStamp = 0;
double   dayStartBalance = 0;
// zone recovery state
int      zr_legs = 0;
int      zr_dir0 = 0;          // seed direction +1/-1
double   zr_zoneTop = 0, zr_zoneBot = 0;
double   zr_basketPeak = 0;
// ratio pair state
int      rp_state = 0;         // 0 flat, +1 long-ratio (long gold/short silver), -1 short-ratio
datetime rp_entryTime = 0;

//=== LIFECYCLE ======================================================
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   if(InpMode == MODE_ZONE_RECOVERY)
   {
      hATR  = iATR(_Symbol, PERIOD_H1, InpATRPeriod);
      hADX  = iADX(_Symbol, _Period, InpADXPeriod);
      hEMAf = iMA(_Symbol, _Period, InpEMAFast, 0, MODE_EMA, PRICE_CLOSE);
      hEMAs = iMA(_Symbol, _Period, InpEMASlow, 0, MODE_EMA, PRICE_CLOSE);
      if(hATR == INVALID_HANDLE || hADX == INVALID_HANDLE ||
         hEMAf == INVALID_HANDLE || hEMAs == INVALID_HANDLE) return INIT_FAILED;
   }
   else
   {
      if(!SymbolSelect(InpGoldSymbol, true) || !SymbolSelect(InpSilverSymbol, true))
      {
         Print("HedgeGuardian: cannot select ", InpGoldSymbol, "/", InpSilverSymbol);
         return INIT_FAILED;
      }
   }
   dayStamp = DayOf(TimeCurrent());
   dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   RecountLegs();
   Print("HedgeGuardian ready. Mode=", EnumToString(InpMode));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Comment("");
   if(hATR != INVALID_HANDLE)  IndicatorRelease(hATR);
   if(hADX != INVALID_HANDLE)  IndicatorRelease(hADX);
   if(hEMAf != INVALID_HANDLE) IndicatorRelease(hEMAf);
   if(hEMAs != INVALID_HANDLE) IndicatorRelease(hEMAs);
}

//=== COMMON HELPERS =================================================
datetime DayOf(datetime t) { return t - (t % 86400); }

double Buf(int handle, int shift = 1)
{
   double a[1];
   return (CopyBuffer(handle, 0, shift, 1, a) == 1) ? a[0] : 0;
}

bool SessionOK()
{
   if(!InpUseSession) return true;
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(InpSessionStartHr <= InpSessionEndHr)
      return (dt.hour >= InpSessionStartHr && dt.hour < InpSessionEndHr);
   return (dt.hour >= InpSessionStartHr || dt.hour < InpSessionEndHr);
}

bool SpreadOK(string sym)
{
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt <= 0) return false;
   double spr = (SymbolInfoDouble(sym, SYMBOL_ASK) - SymbolInfoDouble(sym, SYMBOL_BID)) / pt;
   return spr <= InpMaxSpreadPoints;
}

double NormalizeLotSym(string sym, double lot)
{
   double mn = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double mx = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double st = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(st <= 0) st = 0.01;
   lot = MathFloor(lot / st) * st;
   return MathMax(mn, MathMin(mx, NormalizeDouble(lot, 2)));
}

double BasketProfit(string sym = "")
{
   double p = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(sym != "" && PositionGetString(POSITION_SYMBOL) != sym) continue;
      p += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   }
   return p;
}

void CloseBasket(string reason, string sym = "")
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(sym != "" && PositionGetString(POSITION_SYMBOL) != sym) continue;
      trade.PositionClose(tk, InpSlippagePoints);
   }
   Print("HedgeGuardian: basket closed — ", reason);
   zr_legs = 0; zr_dir0 = 0; zr_basketPeak = 0;
   rp_state = 0;
}

void RecountLegs()
{
   zr_legs = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
         zr_legs++;
   }
}

// equity guardian: returns false if trading must halt
bool GuardianOK()
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   // daily roll
   if(DayOf(TimeCurrent()) != dayStamp)
   {
      dayStamp = DayOf(TimeCurrent());
      dayStartBalance = bal;
   }
   // basket hard stop
   double bp = BasketProfit();
   if(bp < -InpMaxBasketLossPct / 100.0 * bal)
   {
      CloseBasket(StringFormat("MAX BASKET LOSS hit (%.2f)", bp));
      cooldownUntil = TimeCurrent() + InpCooldownBars * PeriodSeconds(_Period);
      return false;
   }
   // daily cap (realized + floating)
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(dayStartBalance > 0 && (dayStartBalance - eq) / dayStartBalance * 100.0 >= InpDailyLossCapPct)
   {
      if(PositionsTotal() > 0) CloseBasket("DAILY LOSS CAP");
      cooldownUntil = DayOf(TimeCurrent()) + 86400; // rest of day
      return false;
   }
   if(TimeCurrent() < cooldownUntil) return false;
   return true;
}

//====================================================================
//  MODE 1 — ZONE RECOVERY
//====================================================================
int SeedSignal()
{
   double f1 = Buf(hEMAf, 1), s1 = Buf(hEMAs, 1);
   double f2 = Buf(hEMAf, 2), s2 = Buf(hEMAs, 2);
   double adx = Buf(hADX, 1);
   if(adx < InpADXMin) return 0;
   if(f2 <= s2 && f1 > s1) return +1;   // fresh bullish cross
   if(f2 >= s2 && f1 < s1) return -1;
   return 0;
}

double WorstCaseLoss(double seedLot, double zonePts)
{
   // worst case at leg cap: alternating legs, geometric volumes.
   // money lost if price oscillates and we stop at MaxLegs with full zone adverse.
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickVal <= 0 || tickSize <= 0) return DBL_MAX;
   double ptVal = _Point / tickSize * tickVal;     // $ per point per lot
   double loss = 0, lot = seedLot;
   double netDir = 0;
   for(int i = 0; i < InpMaxLegs; i++)
   {
      loss += lot * zonePts * ptVal;               // each leg can lose ~zone width
      lot *= InpHedgeFactor;
   }
   return loss;   // conservative upper bound
}

void ZR_OpenSeed(int dir)
{
   double atr = Buf(hATR, 1);
   if(atr <= 0) return;
   double zonePts = InpZone_ATR * atr / _Point;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickVal <= 0 || tickSize <= 0) return;
   double ptVal = _Point / tickSize * tickVal;
   double seedLot = NormalizeLotSym(_Symbol, bal * InpSeedRiskPct / 100.0 / (zonePts * ptVal));
   if(seedLot <= 0) return;

   // refuse the cycle if worst case exceeds the basket cap — THE critical check
   double wc = WorstCaseLoss(seedLot, zonePts);
   double cap = bal * InpMaxBasketLossPct / 100.0;
   if(wc > cap)
   {
      double scale = cap / wc;
      seedLot = NormalizeLotSym(_Symbol, seedLot * scale);
      if(seedLot < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
      {
         Print("ZR: cycle refused — worst case $", DoubleToString(wc, 2),
               " exceeds cap $", DoubleToString(cap, 2), " even at min lot");
         return;
      }
      Print("ZR: seed scaled down to keep worst case under cap");
   }

   double price = dir > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                          : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   bool ok = dir > 0 ? trade.Buy(seedLot, _Symbol, 0, 0, 0, "ZR seed")
                     : trade.Sell(seedLot, _Symbol, 0, 0, 0, "ZR seed");
   if(!ok) { Print("ZR seed failed: ", trade.ResultRetcode()); return; }
   zr_dir0 = dir; zr_legs = 1; zr_basketPeak = 0;
   zr_zoneTop = dir > 0 ? price : price + zonePts * _Point;
   zr_zoneBot = dir > 0 ? price - zonePts * _Point : price;
   Print("ZR: seed ", dir > 0 ? "BUY" : "SELL", " ", DoubleToString(seedLot, 2),
         " zone [", DoubleToString(zr_zoneBot, _Digits), " .. ", DoubleToString(zr_zoneTop, _Digits), "]");
}

double NetLots(int &netDir)
{
   double buys = 0, sells = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
         buys += PositionGetDouble(POSITION_VOLUME);
      else
         sells += PositionGetDouble(POSITION_VOLUME);
   }
   netDir = buys > sells ? +1 : (sells > buys ? -1 : 0);
   return MathAbs(buys - sells);
}

void ZoneRecoveryTick()
{
   if(zr_legs == 0)
   {
      if(!SessionOK() || !SpreadOK(_Symbol)) return;
      int sig = SeedSignal();
      if(sig != 0) ZR_OpenSeed(sig);
      return;
   }

   // manage active cycle
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double bp  = BasketProfit(_Symbol);
   double atr = Buf(hATR, 1);
   if(atr <= 0) return;
   double targetMoney = MathMin(InpBasketTPPct / 100.0 * bal,
                                DBL_MAX);

   if(bp > zr_basketPeak) zr_basketPeak = bp;

   // basket take-profit
   if(bp >= targetMoney)
   { CloseBasket(StringFormat("basket TP +$%.2f", bp)); return; }

   // basket profit trail
   if(InpTrailBasket && zr_basketPeak >= 0.5 * targetMoney &&
      bp <= zr_basketPeak * 0.5 && bp > 0)
   { CloseBasket(StringFormat("basket trail: peak $%.2f -> $%.2f", zr_basketPeak, bp)); return; }

   // price target beyond the zone in current net direction
   int netDir; double net = NetLots(netDir);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double tgt = InpTarget_ATR * atr;
   if(netDir > 0 && bid >= zr_zoneTop + tgt)
   { CloseBasket("zone target up reached"); return; }
   if(netDir < 0 && ask <= zr_zoneBot - tgt)
   { CloseBasket("zone target down reached"); return; }

   // hedge trigger at opposite zone edge
   if(zr_legs < InpMaxLegs)
   {
      bool needSell = (netDir > 0 && bid <= zr_zoneBot);
      bool needBuy  = (netDir < 0 && ask >= zr_zoneTop);
      if(needSell || needBuy)
      {
         double newLot = NormalizeLotSym(_Symbol, net * InpHedgeFactor);
         bool ok = needBuy ? trade.Buy(newLot, _Symbol, 0, 0, 0, "ZR hedge " + (string)(zr_legs + 1))
                           : trade.Sell(newLot, _Symbol, 0, 0, 0, "ZR hedge " + (string)(zr_legs + 1));
         if(ok)
         {
            zr_legs++;
            Print("ZR: hedge leg ", zr_legs, "/", InpMaxLegs, " ",
                  needBuy ? "BUY" : "SELL", " ", DoubleToString(newLot, 2));
         }
      }
   }
   else
   {
      // legs exhausted: if price escapes the zone against the net basket, end the cycle
      if((netDir > 0 && bid <= zr_zoneBot - tgt) || (netDir < 0 && ask >= zr_zoneTop + tgt))
      {
         CloseBasket("max legs + zone escape: bounded loss taken");
         cooldownUntil = TimeCurrent() + InpCooldownBars * PeriodSeconds(_Period);
      }
   }
}

//====================================================================
//  MODE 2 — RATIO PAIR
//====================================================================
bool RatioZ(double &z, double &ratioNow)
{
   double g[], s[];
   ArraySetAsSeries(g, true); ArraySetAsSeries(s, true);
   if(CopyClose(InpGoldSymbol, InpRatioTF, 1, InpRatioWindow, g) != InpRatioWindow) return false;
   if(CopyClose(InpSilverSymbol, InpRatioTF, 1, InpRatioWindow, s) != InpRatioWindow) return false;
   double sum = 0, sum2 = 0;
   for(int i = 0; i < InpRatioWindow; i++)
   {
      if(s[i] <= 0) return false;
      double r = g[i] / s[i];
      sum += r; sum2 += r * r;
   }
   double mean = sum / InpRatioWindow;
   double var = sum2 / InpRatioWindow - mean * mean;
   if(var <= 0) return false;
   ratioNow = g[0] / s[0];
   z = (ratioNow - mean) / MathSqrt(var);
   return true;
}

double PairLot(string sym, double notional)
{
   double price = SymbolInfoDouble(sym, SYMBOL_ASK);
   double cs = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);
   if(price <= 0 || cs <= 0) return 0;
   return NormalizeLotSym(sym, notional / (price * cs));
}

void RatioPairTick()
{
   double z, ratio;
   if(!RatioZ(z, ratio)) return;

   if(rp_state == 0)
   {
      if(!SessionOK() || !SpreadOK(InpGoldSymbol) || !SpreadOK(InpSilverSymbol)) return;
      double notional = AccountInfoDouble(ACCOUNT_BALANCE) * InpPairRiskPct / 100.0 * 10;
      // 10x: riskPct refers to ~10% adverse spread move on the notional
      if(z >= InpZEntry)
      {  // ratio rich: short gold, long silver
         double lg = PairLot(InpGoldSymbol, notional);
         double ls = PairLot(InpSilverSymbol, notional);
         if(lg > 0 && ls > 0 &&
            trade.Sell(lg, InpGoldSymbol, 0, 0, 0, "RP short-ratio") &&
            trade.Buy(ls, InpSilverSymbol, 0, 0, 0, "RP short-ratio"))
         { rp_state = -1; rp_entryTime = TimeCurrent(); Print("RP: SHORT ratio z=", DoubleToString(z, 2)); }
      }
      else if(z <= -InpZEntry)
      {  // ratio cheap: long gold, short silver
         double lg = PairLot(InpGoldSymbol, notional);
         double ls = PairLot(InpSilverSymbol, notional);
         if(lg > 0 && ls > 0 &&
            trade.Buy(lg, InpGoldSymbol, 0, 0, 0, "RP long-ratio") &&
            trade.Sell(ls, InpSilverSymbol, 0, 0, 0, "RP long-ratio"))
         { rp_state = +1; rp_entryTime = TimeCurrent(); Print("RP: LONG ratio z=", DoubleToString(z, 2)); }
      }
      return;
   }

   // manage open pair
   bool exitNow = false;
   string why = "";
   if(MathAbs(z) <= InpZExit)                     { exitNow = true; why = "z reverted"; }
   if(rp_state == -1 && z >= InpZStop)            { exitNow = true; why = "z stop"; }
   if(rp_state == +1 && z <= -InpZStop)           { exitNow = true; why = "z stop"; }
   if(TimeCurrent() - rp_entryTime > InpPairMaxBars * PeriodSeconds(InpRatioTF))
                                                  { exitNow = true; why = "time stop"; }
   if(exitNow)
   {
      CloseBasket("RP exit: " + why + " (z=" + DoubleToString(z, 2) + ")");
   }
}

//=== MAIN ===========================================================
void OnTick()
{
   if(!GuardianOK())
   {
      Comment("HedgeGuardian: HALTED (guardian/cooldown active)");
      return;
   }
   if(InpMode == MODE_ZONE_RECOVERY)
   {
      ZoneRecoveryTick();
      int nd; double net = NetLots(nd);
      Comment(StringFormat("HedgeGuardian ZONE RECOVERY\nLegs: %d/%d  net %.2f lots %s\nZone: %.5f .. %.5f\nBasket P/L: $%.2f (peak $%.2f)",
              zr_legs, InpMaxLegs, net, nd > 0 ? "LONG" : (nd < 0 ? "SHORT" : "-"),
              zr_zoneBot, zr_zoneTop, BasketProfit(_Symbol), zr_basketPeak));
   }
   else
   {
      // ratio engine works on closed bars of RatioTF to avoid noise
      datetime cb = iTime(InpGoldSymbol, InpRatioTF, 0);
      if(cb != lastBar)
      {
         lastBar = cb;
         RatioPairTick();
      }
      double z = 0, ratio = 0;
      RatioZ(z, ratio);
      Comment(StringFormat("HedgeGuardian RATIO PAIR\nGold/Silver ratio: %.2f  z=%.2f\nState: %s\nPair P/L: $%.2f",
              ratio, z, rp_state == 0 ? "flat" : (rp_state > 0 ? "LONG ratio" : "SHORT ratio"),
              BasketProfit()));
   }
}
