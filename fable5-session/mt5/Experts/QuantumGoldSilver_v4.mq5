//+------------------------------------------------------------------+
//|                  Quantum Gold & Silver Trader 4.0.mq5            |
//|   v4.0 (Claude Fable 5): fixed TP/SL ratio bug, partial close,    |
//|   giveback guard, spread filter. Original 3.2 logic preserved.    |
//|             Full Auto-Optimization for Strategy Tester           |
//|              Special settings for Gold and Silver                |
//+------------------------------------------------------------------+
#property copyright "Copyright 2023, MetaQuotes Software Corp."
#property link      "https://www.mql5.com"
#property version   "4.0"
#property description "AI-Enhanced Quantum Trading System for Gold and Silver"
#property script_show_inputs

#include <Trade\Trade.mqh>
#include <Trade\AccountInfo.mqh>
#include <Math\Stat\Math.mqh>

//+------------------------------------------------------------------+
//| Parameters for optimization                                      |
//+------------------------------------------------------------------+

input group "Quantum System"
input int      QuantumStates        = 17;      // Number of quantum states [5-50]
input double   QuantumDecayRate     = 0.03;    // State decay rate [0.01-0.1]
input double   StateUpdateRate      = 0.1;     // State update rate [0.05-0.3]
input double   StateMemoryFactor    = 0.9;     // State memory factor [0.8-0.99]

input group "AI Parameters"
input int      LearningPeriod       = 200;     // AI learning period [50-500]
input double   AdaptiveThreshold    = 0.65;    // Decision threshold [0.55-0.75]

input group "Indicator Settings"
input int      RSIPeriod            = 14;      // RSI period [7-21]
input int      ADXPeriod            = 14;      // ADX period [10-25]
input int      ATRPeriodInput       = 14;      // ATR period [10-20]
input int      FastMAPeriodInput    = 50;      // Fast MA period [20-100]
input int      SlowMAPeriodInput    = 200;     // Slow MA period [100-300]
input int      ChaosPeriod          = 20;      // Chaos calculation period [10-50]
input ENUM_TIMEFRAMES AnalysisTimeframe = PERIOD_M15; // Analysis timeframe

input group "AI Modules"
input bool     UseRSI               = true;    // Use RSI
input double   RSIWeight            = 1.0;     // RSI weight [0.1-2.0]
input bool     UseADX               = true;    // Use ADX
input double   ADXWeight            = 1.0;     // ADX weight [0.1-2.0]
input bool     UseMA                = true;    // Use MA
input double   MAWeight             = 1.0;     // MA weight [0.1-2.0]
input bool     UseChaosInAI         = true;    // Use chaos
input double   ChaosWeight          = 1.0;     // Chaos weight [0.1-2.0]
input bool     UseQuantumState      = true;    // Use quantum state
input double   QuantumStateWeight   = 1.0;     // Quantum state weight [0.1-2.0]

input group "Trading Logic"
input double   BaseRiskInput        = 0.5;     // Base risk (%) [0.1-1.0]
input double   MaxRisk              = 2.0;     // Max risk (%) [1.0-5.0]
input int      MinBarsBetweenTrades = 1;       // Min bars between trades [1-5]
input double   ATRMultiplierSL      = 1.5;     // ATR multiplier for SL [1.0-3.0]
input double   TPtoSLRatio          = 2.0;     // TP/SL ratio [1.5-3.0]

input group "===== v4.0 Profit Protection (new) ====="
input bool      V4_UsePartialClose      = true;     // Close part of position at +1R
input double    V4_PartialAtR           = 1.0;      // Partial trigger in R
input double    V4_PartialPercent       = 50;       // % volume to close
input bool      V4_UseGiveback          = true;     // Bank trade if it surrenders peak profit
input double    V4_GivebackArmR         = 1.5;      // Arm at +X R
input double    V4_GivebackPct          = 50;       // Close if profit < X% of peak
input double    V4_MaxSpreadPoints      = 0;        // Skip entries above this spread (0=off)

input group "Risk Management"
input double   RiskIncreaseFactor   = 1.2;     // Risk increase factor [1.1-1.5]
input double   RiskDecreaseFactor   = 0.7;     // Risk decrease factor [0.5-0.9]
input int      MaxConsecutiveLosses = 2;       // Max consecutive losses [1-5]

input group "Micro Account Settings"
input bool     MicroAccountMode     = true;    // Mode for small deposits
input double   MicroRiskCorrection  = 0.6;     // Risk correction [0.3-1.0]
input bool     IgnoreMarginLimitForMinLot = true;  // Ignore margin limit for min lot

input group "Signal Filters"
input bool     UseChaosFilter       = true;    // Market chaos filter
input double   ChaosThreshold       = 0.35;    // Chaos threshold [0.2-0.5]

input group "===== Deposit Protection System ====="
input bool    UseEquityProtection      = true;   // Enable equity protection
input double  MaxDailyDrawdownPercent  = 5.0;    // Max daily drawdown (%) [0-50]
input double  MaxTotalDrawdownPercent  = 20.0;   // Max total drawdown (%) [0-50]
input double  DrawdownBuffer           = 2.0;    // Drawdown buffer (%) [0-5]
input bool    UseDailyLossLimit        = true;   // Enable daily loss limit
input double  DailyLossLimitPercent    = 3.0;    // Daily loss limit (%) [0-10]
input double  DailyLossLimitAbsolute   = 50.0;   // Absolute daily loss limit [0-5000]
input double  DailyLossLimitATRMultiplier = 3.0; // ATR multiplier for loss limit [1.0-10.0]
input bool    UsePositionSizeLimit     = true;   // Limit position size
input double  MaxPositionSizePercent   = 2.0;    // Max position size (% of balance) [0.1-5]
input bool    UseHardStop              = false;  // Use hard stop loss (DISABLED)
input double  HardStopLevel            = 20.0;   // Hard stop level (%) [10-50]

input group "===== Advanced Quantum Trailing Stop ====="
input bool    UseQuantumTrailing       = true;   // Enable quantum trailing stop
input double  BaseTrailATRMultiplier   = 1.5;    // Base ATR multiplier [1.0-3.0]
input double  MaxTrailATRMultiplier    = 4.0;    // Max ATR multiplier [2.0-6.0]
input double  TrailActivationProfit    = 1.0;    // Activation at profit (in ATR) [0.5-3.0]
input double  ChaosSensitivity         = 0.7;    // Chaos sensitivity [0.3-1.5]
input double  QuantumStateInfluence    = 0.5;    // Quantum state influence [0.1-1.0]

input group "===== Auto-Optimization ====="
input bool    AutoOptimizeInTester     = true;   // Auto-optimization in tester
input int     OptimizationPasses       = 50;     // Number of optimization passes [10-200]

// Specific parameters for gold and silver
input group "==== Gold Settings (XAUUSD) ===="
input double   Gold_ATRMultiplierSL      = 1.5;     // ATR multiplier for SL
input double   Gold_TPtoSLRatio          = 2.0;     // TP/SL ratio
input double   Gold_BaseRiskInput        = 0.5;     // Base risk (%)
input int      Gold_MinBarsBetweenTrades = 1;       // Min bars between trades
input double   Gold_BaseTrailATRMultiplier = 1.5;   // Base ATR multiplier for trailing
input double   Gold_ChaosThreshold       = 0.35;    // Chaos threshold

input group "==== Silver Settings (XAGUSD) ===="
input double   Silver_ATRMultiplierSL      = 2.0;     // ATR multiplier for SL
input double   Silver_TPtoSLRatio          = 2.5;     // TP/SL ratio
input double   Silver_BaseRiskInput        = 0.3;     // Base risk (%)
input int      Silver_MinBarsBetweenTrades = 2;       // Min bars between trades
input double   Silver_BaseTrailATRMultiplier = 2.0;   // Base ATR multiplier for trailing
input double   Silver_ChaosThreshold       = 0.4;     // Chaos threshold

// Internal copies of parameters for optimization
int internalQuantumStates;
double internalQuantumDecayRate;
double internalStateUpdateRate;
double internalStateMemoryFactor;
int internalRSIPeriod;
int internalADXPeriod;
int internalATRPeriod;
int internalFastMAPeriod;
int internalSlowMAPeriod;
double internalBaseRisk;
double internalATRMultiplierSL;
double internalTPtoSLRatio;
int internalMinBarsBetweenTrades;
double internalBaseTrailATRMultiplier;
double internalChaosThreshold;

// Global variables
CTrade trade;

//--- v4.0 profit-protection state
double  v4PeakR[];
ulong   v4Tickets[];
bool    v4PartialDone[];

void V4_Manage()
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong tk = PositionGetTicket(i);
        if(tk <= 0) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
        bool   isBuy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
        double op  = PositionGetDouble(POSITION_PRICE_OPEN);
        double sl  = PositionGetDouble(POSITION_SL);
        double tp  = PositionGetDouble(POSITION_TP);
        double vol = PositionGetDouble(POSITION_VOLUME);
        double cur = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                           : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
        double riskPts = (sl > 0) ? MathAbs(op - sl) : 0;
        if(riskPts <= 0) continue;
        double r = (isBuy ? cur - op : op - cur) / riskPts;

        int idx = -1;
        for(int k = 0; k < ArraySize(v4Tickets); k++)
            if(v4Tickets[k] == tk) { idx = k; break; }
        if(idx < 0)
        {
            idx = ArraySize(v4Tickets);
            ArrayResize(v4Tickets, idx + 1);
            ArrayResize(v4PeakR, idx + 1);
            ArrayResize(v4PartialDone, idx + 1);
            v4Tickets[idx] = tk;
            v4PeakR[idx] = r;
            v4PartialDone[idx] = false;
        }
        if(r > v4PeakR[idx]) v4PeakR[idx] = r;

        if(V4_UseGiveback && v4PeakR[idx] >= V4_GivebackArmR &&
           r < v4PeakR[idx] * V4_GivebackPct / 100.0)
        {
            if(trade.PositionClose(tk))
                PrintFormat("v4 giveback close: peak %.2fR -> %.2fR", v4PeakR[idx], r);
            continue;
        }
        if(V4_UsePartialClose && !v4PartialDone[idx] && r >= V4_PartialAtR)
        {
            double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
            double st = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
            if(st <= 0) st = 0.01;
            double cv = MathFloor(vol * V4_PartialPercent / 100.0 / st) * st;
            if(cv >= mn && vol - cv >= mn)
            {
                if(trade.PositionClosePartial(tk, cv))
                {
                    v4PartialDone[idx] = true;
                    PrintFormat("v4 partial close %.2f lots at +%.2fR", cv, r);
                }
            }
            else v4PartialDone[idx] = true;
        }
    }
}

bool V4_SpreadOK()
{
    if(V4_MaxSpreadPoints <= 0) return true;
    double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
    if(pt <= 0) return true;
    return (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / pt
           <= V4_MaxSpreadPoints;
}
CAccountInfo accountInfo;
int quantumState = 0;
double waveFunction[];
double stateReturns[];
datetime lastTradeTime;
double positionMatrix[][2];
int lossCounter = 0;
double currentRisk = BaseRiskInput;
double aiWeights[];
double marketMatrix[][4];
int signalHistory[];
int rsiHandle, adxHandle, atrHandle, maFastHandle, maSlowHandle;

// Cache for indicators
double lastRSI = 0, lastADX = 0, lastATR = 0, lastFastMA = 0, lastSlowMA = 0;
datetime lastCacheTime;

// Variables for protection system
double initialEquity = 0;
double maxEquityToday = 0;
double minEquityToday = 0;
datetime lastEquityUpdate = 0;
double totalProfitToday = 0;
double dailyLossLimit;
bool tradingHalted = false;
int marginErrorCount = 0;
datetime lastErrorTime = 0;

// For trailing stop
datetime lastTrailTime = 0;
double lastTrailPrice = 0;
double lastTrailSl = 0;

// Internal variable for micro account mode
bool internalMicroMode;

// For auto-optimization
bool isFirstRun = true;
double bestParams[][2];
bool optimizationCompleted = false;

//+------------------------------------------------------------------+
//| Returns error description                                        |
//+------------------------------------------------------------------+
string ErrorDescription(int error_code)
{
    switch(error_code)
    {
        case 0:     return ("No error");
        case 1:     return ("No error, but result unknown");
        case 2:     return ("General error");
        case 3:     return ("Invalid parameters");
        case 4:     return ("Trade server busy");
        case 5:     return ("Old client version");
        case 6:     return ("No connection to trade server");
        case 7:     return ("Insufficient rights");
        case 8:     return ("Too frequent requests");
        case 9:     return ("Invalid operation");
        case 64:    return ("Account blocked");
        case 65:    return ("Invalid account number");
        case 128:   return ("Trade timeout expired");
        case 129:   return ("Invalid price");
        case 130:   return ("Invalid stops");
        case 131:   return ("Invalid volume");
        case 132:   return ("Market closed");
        case 133:   return ("Trading disabled");
        case 134:   return ("Insufficient funds");
        case 135:   return ("Price changed");
        case 136:   return ("No prices");
        case 137:   return ("Broker busy");
        case 138:   return ("New prices");
        case 139:   return ("Order blocked");
        case 140:   return ("Only buy allowed");
        case 141:   return ("Too many requests");
        case 145:   return ("Modification denied");
        case 146:   return ("Trading subsystem busy");
        case 147:   return ("Expiration date denied");
        case 148:   return ("Open and pending orders limit reached");
        case 4756:  return ("Invalid stops");
        default:    return ("Unknown error: " + IntegerToString(error_code));
    }
}

//+------------------------------------------------------------------+
//| Auto-correction of parameters                                    |
//+------------------------------------------------------------------+
void AutoCorrectParameters()
{
    // Correction of MA periods
    if(internalFastMAPeriod >= internalSlowMAPeriod)
    {
        internalSlowMAPeriod = internalFastMAPeriod + 10;
        PrintFormat("Auto-correction: SlowMAPeriod changed from %d to %d (must be > FastMAPeriod)", 
                    internalSlowMAPeriod, internalSlowMAPeriod);
    }

    // Correction of ATR period
    if(internalATRPeriod < 10)
    {
        internalATRPeriod = 10;
        PrintFormat("Auto-correction: ATRPeriod changed from %d to %d (minimum 10)", internalATRPeriod, internalATRPeriod);
    }
    
    // Correction of quantum states
    if(internalQuantumStates < 5)
    {
        PrintFormat("Warning: QuantumStates=%d is too small (recommended >=5)", internalQuantumStates);
    }
    
    // Correction of risk
    if(internalBaseRisk > MaxRisk)
    {
        internalBaseRisk = MaxRisk;
        PrintFormat("Auto-correction: BaseRisk changed from %.2f to %.2f (cannot be > MaxRisk)", internalBaseRisk, internalBaseRisk);
    }
}

//+------------------------------------------------------------------+
//| Calculate mean value                                             |
//+------------------------------------------------------------------+
double MathMean(double &array[])
{
    double sum = 0.0;
    int count = ArraySize(array);
    if(count == 0) return 0;
    
    for(int i = 0; i < count; i++)
        sum += array[i];
    return sum / count;
}

//+------------------------------------------------------------------+
//| Calculate standard deviation                                     |
//+------------------------------------------------------------------+
double MathStandardDeviation(double &array[])
{
    double mean = MathMean(array);
    double sum = 0.0;
    int count = ArraySize(array);
    if(count == 0) return 0;
    
    for(int i = 0; i < count; i++)
        sum += MathPow(array[i] - mean, 2);
    return MathSqrt(sum / count);
}

//+------------------------------------------------------------------+
//| Calculate market chaos                                           |
//+------------------------------------------------------------------+
double CalculateChaos()
{
    double returns[];
    int bars = MathMin(ChaosPeriod, Bars(_Symbol, AnalysisTimeframe));
    if(bars < ChaosPeriod) return 0.5;
    
    ArrayResize(returns, ChaosPeriod);
    for(int i = 0; i < ChaosPeriod; i++) 
    {
        returns[i] = MathLog(iClose(_Symbol, AnalysisTimeframe, i)) - 
                     MathLog(iClose(_Symbol, AnalysisTimeframe, i+1));
    }
    
    double mean = MathMean(returns);
    double sd = MathStandardDeviation(returns);
    return (sd != 0) ? MathAbs(mean/sd) : 0.5;
}

//+------------------------------------------------------------------+
//| Get indicator value by handle                                    |
//+------------------------------------------------------------------+
double GetIndicatorValue(int handle, int buffer=0, int shift=0)
{
    double value[1];
    if(CopyBuffer(handle, buffer, shift, 1, value) != 1)
    {
        Print("Error getting indicator data: ", GetLastError());
        return EMPTY_VALUE;
    }
    return value[0];
}

//+------------------------------------------------------------------+
//| Initialize AI system                                             |
//+------------------------------------------------------------------+
void InitAISystem()
{
    ArrayResize(aiWeights, 6);
    ArrayInitialize(aiWeights, 0.5);
    
    // Auto-correct parameters before initialization
    AutoCorrectParameters();
    
    // Load saved AI weights
    string fileName = "QuantumAI_"+_Symbol+".txt";
    int fileHandle = FileOpen(fileName, FILE_READ|FILE_TXT|FILE_COMMON);
    if(fileHandle != INVALID_HANDLE)
    {
        for(int i = 0; i < 6 && !FileIsEnding(fileHandle); i++)
            aiWeights[i] = StringToDouble(FileReadString(fileHandle));
        FileClose(fileHandle);
        Print("AI weights successfully loaded from file");
    }
    else
    {
        Print("Weights file not found, using default values");
    }
    
    ArrayResize(marketMatrix, LearningPeriod);
    ArrayResize(signalHistory, LearningPeriod);
    ArrayInitialize(signalHistory, 0);
    
    // Create indicator handles
    rsiHandle = iRSI(_Symbol, AnalysisTimeframe, internalRSIPeriod, PRICE_CLOSE);
    adxHandle = iADX(_Symbol, AnalysisTimeframe, internalADXPeriod);
    atrHandle = iATR(_Symbol, AnalysisTimeframe, internalATRPeriod);
    maFastHandle = iMA(_Symbol, AnalysisTimeframe, internalFastMAPeriod, 0, MODE_SMA, PRICE_CLOSE);
    maSlowHandle = iMA(_Symbol, AnalysisTimeframe, internalSlowMAPeriod, 0, MODE_SMA, PRICE_CLOSE);
    
    // Check handle creation
    if(rsiHandle == INVALID_HANDLE || adxHandle == INVALID_HANDLE || 
       atrHandle == INVALID_HANDLE || maFastHandle == INVALID_HANDLE || 
       maSlowHandle == INVALID_HANDLE)
    {
        Print("Error creating indicators: ", GetLastError());
    }
}

//+------------------------------------------------------------------+
//| Adaptive AI learning                                             |
//+------------------------------------------------------------------+
void UpdateAI(int signal, double profit)
{
    // Update signal history
    for(int i = LearningPeriod-1; i > 0; i--)
        signalHistory[i] = signalHistory[i-1];
    signalHistory[0] = (profit > 0) ? signal : -signal;
    
    // Calculate new weights
    double rsi = GetIndicatorValue(rsiHandle) / 100.0;
    double adx = GetIndicatorValue(adxHandle, 0) / 100.0;
    double atr = GetIndicatorValue(atrHandle) / 100.0;
    double chaos = CalculateChaos();
    
    // Update market matrix
    for(int i = LearningPeriod-1; i > 0; i--)
    {
        marketMatrix[i][0] = marketMatrix[i-1][0];
        marketMatrix[i][1] = marketMatrix[i-1][1];
        marketMatrix[i][2] = marketMatrix[i-1][2];
        marketMatrix[i][3] = marketMatrix[i-1][3];
    }
    marketMatrix[0][0] = rsi;
    marketMatrix[0][1] = adx;
    marketMatrix[0][2] = atr;
    marketMatrix[0][3] = chaos;
    
    // Adapt weights
    double successRate = 0;
    int count = 0;
    for(int i = 0; i < MathMin(100, LearningPeriod); i++)
    {
        if(signalHistory[i] != 0)
        {
            count++;
            if(signalHistory[i] > 0) successRate++;
        }
    }
    successRate = (count > 0) ? successRate/count : 0.5;
    
    double adjustment = (successRate - 0.5) * 0.1;
    for(int i = 0; i < 6; i++)
        aiWeights[i] = MathMin(1.0, MathMax(0.1, aiWeights[i] + adjustment));
}

//+------------------------------------------------------------------+
//| Update indicator cache                                           |
//+------------------------------------------------------------------+
void UpdateIndicatorCache()
{
    datetime currentTime = iTime(_Symbol, AnalysisTimeframe, 0);
    if(currentTime != lastCacheTime)
    {
        lastRSI = GetIndicatorValue(rsiHandle);
        lastADX = GetIndicatorValue(adxHandle, 0);
        lastATR = GetIndicatorValue(atrHandle);
        lastFastMA = GetIndicatorValue(maFastHandle);
        lastSlowMA = GetIndicatorValue(maSlowHandle);
        lastCacheTime = currentTime;
    }
}

//+------------------------------------------------------------------+
//| Generate trading signal                                          |
//+------------------------------------------------------------------+
int GenerateTradeSignal()
{
    if(tradingHalted) return -1; // Block signals when halted
    
    UpdateIndicatorCache();
    
    // Check data validity
    if(lastRSI == EMPTY_VALUE || lastADX == EMPTY_VALUE || 
       lastATR == EMPTY_VALUE || lastFastMA == EMPTY_VALUE || 
       lastSlowMA == EMPTY_VALUE)
    {
        Print("Insufficient indicator data");
        return -1;
    }
    
    double chaos = CalculateChaos();
    
    // Chaos filter
    if(UseChaosFilter && chaos < internalChaosThreshold)
        return -1;
    
    // Quantum collapse
    quantumState = MathRand() % internalQuantumStates;
    
    // Modular AI system
    double signalStrength = 0;
    
    if(UseRSI)
        signalStrength += RSIWeight * aiWeights[0] * (lastRSI - 50.0) / 50.0;
    
    if(UseADX)
        signalStrength += ADXWeight * aiWeights[1] * (lastADX - 20.0) / 30.0;
    
    if(UseMA)
        signalStrength += MAWeight * aiWeights[2] * (lastFastMA - lastSlowMA) / SymbolInfoDouble(_Symbol, SYMBOL_POINT);
    
    if(UseChaosInAI)
        signalStrength += ChaosWeight * aiWeights[3] * chaos;
    
    if(UseQuantumState)
        signalStrength += QuantumStateWeight * aiWeights[4] * (quantumState - internalQuantumStates/2.0) / internalQuantumStates;
    
    // Generate signal
    if(signalStrength > AdaptiveThreshold) return ORDER_TYPE_BUY;
    if(signalStrength < -AdaptiveThreshold) return ORDER_TYPE_SELL;
    
    return -1;
}

//+------------------------------------------------------------------+
//| Adaptive risk management                                         |
//+------------------------------------------------------------------+
double CalculateAdaptiveRisk()
{
    double chaos = CalculateChaos();
    double risk = internalBaseRisk * (1.0 + MathSin(chaos * M_PI));
    
    // Correction for micro deposits
    if(internalMicroMode) 
    {
        double equity = accountInfo.Equity();
        if(equity < 1000) 
        {
            risk *= MicroRiskCorrection * MathSqrt(equity/1000);
        }
    }
    
    return MathMin(MaxRisk, MathMax(0.1, risk));
}

//+------------------------------------------------------------------+
//| Improved price normalization                                     |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
    double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    if(tickSize == 0) return price; // Protection against division by zero
    
    // Calculate number of decimal places
    int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
    
    // Normalize to tick size
    return NormalizeDouble(MathRound(price / tickSize) * tickSize, digits);
}

//+------------------------------------------------------------------+
//| Check SL/TP levels validity                                      |
//+------------------------------------------------------------------+
bool CheckStopLossTakeprofit(int type, double currentPrice, double sl, double tp)
{
    if(sl <= 0 && tp <= 0) return true;
    
    double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
    int stopLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
    double minDist = stopLevel * point;
    
    if(minDist <= 0) minDist = 10 * point; // Protective value
    
    // Add 20% ATR buffer
    double atr = GetIndicatorValue(atrHandle);
    if(atr != EMPTY_VALUE && atr > 0)
    {
        minDist = MathMax(minDist, atr * 0.2);
    }

    if(type == POSITION_TYPE_BUY)
    {
        if(sl > 0)
        {
            // SL must be below current price
            if(sl >= currentPrice - minDist)
            {
                PrintFormat("Error: SL=%.5f too close to current price=%.5f (min_dist=%.5f)", sl, currentPrice, minDist);
                return false;
            }
        }
        if(tp > 0)
        {
            // TP must be above current price
            if(tp <= currentPrice + minDist)
            {
                PrintFormat("Error: TP=%.5f too close to current price=%.5f (min_dist=%.5f)", tp, currentPrice, minDist);
                return false;
            }
        }
    }
    else if(type == POSITION_TYPE_SELL)
    {
        if(sl > 0)
        {
            // SL must be above current price
            if(sl <= currentPrice + minDist)
            {
                PrintFormat("Error: SL=%.5f too close to current price=%.5f (min_dist=%.5f)", sl, currentPrice, minDist);
                return false;
            }
        }
        if(tp > 0)
        {
            // TP must be below current price
            if(tp >= currentPrice - minDist)
            {
                PrintFormat("Error: TP=%.5f too close to current price=%.5f (min_dist=%.5f)", tp, currentPrice, minDist);
                return false;
            }
        }
    }
    
    return true;
}

//+------------------------------------------------------------------+
//| Update daily equity data                                         |
//+------------------------------------------------------------------+
void UpdateDailyEquity()
{
    datetime current = TimeCurrent();
    MqlDateTime now;
    TimeToStruct(current, now);
    
    static MqlDateTime lastDay;
    static bool firstRun = true;
    
    if(firstRun)
    {
        firstRun = false;
        lastDay = now;
        initialEquity = accountInfo.Equity();
        maxEquityToday = initialEquity;
        minEquityToday = initialEquity;
        totalProfitToday = 0;
        lastEquityUpdate = current;
        
        // Dynamic limit based on volatility
        double atr = GetIndicatorValue(atrHandle);
        double atrBasedLimit = (atr != EMPTY_VALUE) ? atr * DailyLossLimitATRMultiplier : DailyLossLimitAbsolute;
        
        dailyLossLimit = MathMin(
            accountInfo.Balance() * (DailyLossLimitPercent / 100.0), 
            MathMax(DailyLossLimitAbsolute, atrBasedLimit)
        );
        tradingHalted = false;
        return;
    }
    
    // If new day started, reset
    if(now.day != lastDay.day || now.mon != lastDay.mon || now.year != lastDay.year)
    {
        initialEquity = accountInfo.Equity();
        maxEquityToday = initialEquity;
        minEquityToday = initialEquity;
        totalProfitToday = 0;
        lastDay = now;
        lastEquityUpdate = current;
        
        // Dynamic limit based on volatility
        double atr = GetIndicatorValue(atrHandle);
        double atrBasedLimit = (atr != EMPTY_VALUE) ? atr * DailyLossLimitATRMultiplier : DailyLossLimitAbsolute;
        
        dailyLossLimit = MathMin(
            accountInfo.Balance() * (DailyLossLimitPercent / 100.0), 
            MathMax(DailyLossLimitAbsolute, atrBasedLimit)
        );
        tradingHalted = false;
        Print("New trading day started. Protection parameters reset.");
        return;
    }
    
    // Update max and min equity
    double equity = accountInfo.Equity();
    if(equity > maxEquityToday || maxEquityToday == 0) maxEquityToday = equity;
    if(equity < minEquityToday || minEquityToday == 0) minEquityToday = equity;
    
    // Update total daily profit
    if(current - lastEquityUpdate >= 60)
    {
        totalProfitToday = equity - initialEquity;
        lastEquityUpdate = current;
    }
}

//+------------------------------------------------------------------+
//| Check drawdown limits                                            |
//+------------------------------------------------------------------+
bool CheckDrawdownLimits()
{
    if(!UseEquityProtection) return true;
    if(tradingHalted) return false;
    
    double equity = accountInfo.Equity();
    double balance = accountInfo.Balance();
    
    // Protection against zero balance
    if(balance <= 0) balance = 0.01;
    
    // Total drawdown
    double totalDrawdown = (balance - equity) / balance * 100.0;
    if(totalDrawdown >= MaxTotalDrawdownPercent)
    {
        PrintFormat("Max total drawdown exceeded: %.2f%% >= %.2f%%. Trading stopped.", 
                    totalDrawdown, MaxTotalDrawdownPercent);
        tradingHalted = true;
        return false;
    }
    else if (totalDrawdown >= (MaxTotalDrawdownPercent - DrawdownBuffer))
    {
        PrintFormat("Warning: total drawdown approaching limit: %.2f%% (limit %.2f%%, buffer %.2f%%)", 
                    totalDrawdown, MaxTotalDrawdownPercent, DrawdownBuffer);
    }
    
    // Daily drawdown
    double dailyDrawdown = 0.0;
    if (maxEquityToday > 0)
        dailyDrawdown = (maxEquityToday - equity) / maxEquityToday * 100.0;
    
    if (maxEquityToday > 0 && dailyDrawdown >= MaxDailyDrawdownPercent)
    {
        PrintFormat("Max daily drawdown exceeded: %.2f%% >= %.2f%%. Trading paused until next day.", 
                    dailyDrawdown, MaxDailyDrawdownPercent);
        tradingHalted = true;
        return false;
    }
    else if (maxEquityToday > 0 && dailyDrawdown >= (MaxDailyDrawdownPercent - DrawdownBuffer))
    {
        PrintFormat("Warning: daily drawdown approaching limit: %.2f%% (limit %.2f%%, buffer %.2f%%)", 
                    dailyDrawdown, MaxDailyDrawdownPercent, DrawdownBuffer);
    }
    
    return true;
}

//+------------------------------------------------------------------+
//| Check daily loss limit                                           |
//+------------------------------------------------------------------+
bool CheckDailyLossLimit()
{
    if(!UseDailyLossLimit) return true;
    if(tradingHalted) return false;
    
    double todayProfit = totalProfitToday;
    if(todayProfit >= 0) return true;
    
    double loss = -todayProfit;
    if(loss >= dailyLossLimit)
    {
        double atr = GetIndicatorValue(atrHandle);
        PrintFormat("Daily loss limit reached: %.2f >= %.2f. Current ATR: %.2f. Trading stopped until next day.",
                    loss, dailyLossLimit, atr);
        tradingHalted = true;
        EventSetTimer(3600); // Auto-recovery after 1 hour
        return false;
    }
    return true;
}

//+------------------------------------------------------------------+
//| Check position size                                              |
//+------------------------------------------------------------------+
bool CheckPositionSize(double lotSize, double price)
{
    if(!UsePositionSizeLimit) return true;
    
    // Calculate position value in deposit currency
    double margin_required;
    if(!OrderCalcMargin(
        ORDER_TYPE_BUY, // type for calculation (doesn't affect value)
        _Symbol,
        lotSize,
        price,
        margin_required
    )) {
        Print("Error calculating margin: ", GetLastError());
        return true; // Allow trade on error
    }
    
    // Calculate allowed limit
    double max_allowed_percent = MaxPositionSizePercent;
    double max_allowed_margin = accountInfo.Equity() * (max_allowed_percent / 100.0);
    
    // Check if exceeded
    if(margin_required > max_allowed_margin)
    {
        // Check if can ignore for minimum lot on micro account
        if (IgnoreMarginLimitForMinLot && internalMicroMode && 
            lotSize <= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
        {
            PrintFormat("Warning: minimum lot (%.2f) requires margin (%.2f %s), exceeding limit (%.2f %s, %.1f%%). Allowed by setting.",
                        lotSize, margin_required, accountInfo.Currency(),
                        max_allowed_margin, accountInfo.Currency(), max_allowed_percent);
            return true;
        }
        else
        {
            PrintFormat("Position size exceeded: %.2f %s > %.2f %s (%.1f%% of equity)",
                margin_required, accountInfo.Currency(),
                max_allowed_margin, accountInfo.Currency(),
                max_allowed_percent
            );
            return false;
        }
    }
    return true;
}

//+------------------------------------------------------------------+
//| Quantum adaptive trailing stop                                   |
//+------------------------------------------------------------------+
void ApplyQuantumTrailing()
{
    if(!UseQuantumTrailing) 
    {
        static int tickCount = 0;
        if(tickCount % 100 == 0) Print("Quantum trailing stop disabled");
        tickCount++;
        return;
    }
    
    // Limit modification frequency
    if(TimeCurrent() - lastTrailTime < 30) return;
    
    double atr = GetIndicatorValue(atrHandle);
    if(atr <= 0) 
    {
        Print("Error getting ATR for trailing stop");
        return;
    }
    
    double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
    double currentAsk = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
    double currentBid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
    double chaos = CalculateChaos();
    
    // Calculate dynamic multiplier
    double trailMultiplier = internalBaseTrailATRMultiplier + 
                           (MaxTrailATRMultiplier - internalBaseTrailATRMultiplier) * 
                           MathMin(1.0, chaos * ChaosSensitivity + 
                                   quantumState * QuantumStateInfluence / internalQuantumStates);
    
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket <= 0) 
        {
            Print("Error getting position ticket. Code: ", GetLastError());
            continue;
        }
        
        string symbol = PositionGetString(POSITION_SYMBOL);
        if(symbol != _Symbol) continue;

        double currentSl = PositionGetDouble(POSITION_SL);
        double positionOpen = PositionGetDouble(POSITION_PRICE_OPEN);
        int positionType = (int)PositionGetInteger(POSITION_TYPE);
        double currentPrice = (positionType == POSITION_TYPE_BUY) ? currentBid : currentAsk;
        
        // Calculate activation level
        double activationLevel = positionType == POSITION_TYPE_BUY ? 
            positionOpen + TrailActivationProfit * atr : 
            positionOpen - TrailActivationProfit * atr;
        
        // Calculate target stop
        double proposedSl = positionType == POSITION_TYPE_BUY ? 
            currentBid - trailMultiplier * atr : 
            currentAsk + trailMultiplier * atr;
        
        // Quantum correction
        double stateCorrection = (quantumState - internalQuantumStates/2.0) * atr * 0.05;
        proposedSl += positionType == POSITION_TYPE_BUY ? -stateCorrection : stateCorrection;
        
        // Adaptive protection
        double minAllowedDistance = MathMax(
            SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point,
            atr * 0.3
        );
        
        // Check activation
        if((positionType == POSITION_TYPE_BUY && currentPrice < activationLevel) ||
           (positionType == POSITION_TYPE_SELL && currentPrice > activationLevel))
        {
            continue;
        }
        
        // Check minimum change (minimum 3 points)
        double minChange = 3 * point;
        if(MathAbs(proposedSl - currentSl) < minChange) 
        {
            PrintFormat("SL change too small: %.5f->%.5f. Skipping", currentSl, proposedSl);
            continue;
        }
        
        // Check position improvement
        bool canModify = false;
        if(positionType == POSITION_TYPE_BUY)
        {
            if(proposedSl > currentSl && proposedSl < currentBid - minAllowedDistance)
            {
                canModify = true;
            }
        }
        else
        {
            if(proposedSl < currentSl && proposedSl > currentAsk + minAllowedDistance)
            {
                canModify = true;
            }
        }
        
        if(canModify)
        {
            // Volatility filter
            double priceChange = MathAbs(currentPrice - positionOpen);
            if(priceChange < atr * 0.3) continue;
            
            // Additional check for minimum distance
            if(!CheckStopLossTakeprofit(positionType, currentPrice, proposedSl, PositionGetDouble(POSITION_TP)))
            {
                Print("New SL failed validation. Modification cancelled.");
                continue;
            }
            
            // Check parameter change
            if(MathAbs(proposedSl - lastTrailSl) < 10*point && MathAbs(currentPrice - lastTrailPrice) < 10*point)
                continue;
                
            // Modify stop
            double newSl = NormalizePrice(proposedSl);
            if(trade.PositionModify(ticket, newSl, PositionGetDouble(POSITION_TP)))
            {
                // Update quantum state
                quantumState = (quantumState + 1) % internalQuantumStates;
                
                PrintFormat("Trailing stop updated: Type=%s, Old SL=%.5f, New SL=%.5f",
                            EnumToString((ENUM_POSITION_TYPE)positionType), currentSl, newSl);
                            
                // Update last modification time
                lastTrailTime = TimeCurrent();
                lastTrailSl = newSl;
                lastTrailPrice = currentPrice;
            }
            else
            {
                uint errorCode = GetLastError();
                Print("Trailing stop modification error: ", ErrorDescription(errorCode));
                
                // Special handling for "Invalid stops" error
                if(errorCode == 4756)
                {
                    double adjustedSl = newSl + (positionType==POSITION_TYPE_BUY ? -point : point);
                    adjustedSl = NormalizePrice(adjustedSl);
                    
                    if(CheckStopLossTakeprofit(positionType, currentPrice, adjustedSl, PositionGetDouble(POSITION_TP)))
                    {
                        if(trade.PositionModify(ticket, adjustedSl, PositionGetDouble(POSITION_TP)))
                        {
                            Print("SL adjusted by 1 point");
                            lastTrailTime = TimeCurrent();
                        }
                    }
                }
            }
        }
    }
}

//+------------------------------------------------------------------+
//| Hard stop loss for all positions                                 |
//+------------------------------------------------------------------+
void CheckHardStop()
{
    if(!UseHardStop) return;
    
    double equity = accountInfo.Equity();
    double balance = accountInfo.Balance();
    double drawdown = (balance - equity) / balance * 100.0;
    
    if(drawdown >= HardStopLevel)
    {
        PrintFormat("Hard stop activated! Drawdown: %.2f%% >= %.2f%%", 
                    drawdown, HardStopLevel);
        
        // Close all positions
        for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
            ulong ticket = PositionGetTicket(i);
            if(ticket > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol)
            {
                trade.PositionClose(ticket);
            }
        }
        
        // Send notification
        SendNotification("QuantumTrader: Hard stop activated! All positions closed.");
        
        // Stop expert advisor
        ExpertRemove();
    }
}

//+------------------------------------------------------------------+
//| Calculate position size with margin consideration                |
//+------------------------------------------------------------------+
double CalculateLotSize(double riskPercent, double price, double slDistance)
{
    double balance = accountInfo.Balance();
    double riskAmount = balance * (riskPercent / 100.0);
    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double volumeStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    
    if(slDistance == 0 || tickValue == 0) return minLot;

    // Base lot calculation
    double lotSize = riskAmount / (slDistance / SymbolInfoDouble(_Symbol, SYMBOL_POINT) * tickValue);
    lotSize = MathRound(lotSize / volumeStep) * volumeStep;
    
    // Min/max lot limits
    lotSize = MathMin(lotSize, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX));
    lotSize = MathMax(lotSize, minLot);
    lotSize = NormalizeDouble(lotSize, 2);

    // Check margin availability
    double marginRequired;
    if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lotSize, price, marginRequired))
    {
        double freeMargin = accountInfo.FreeMargin();
        
        // Gradually decrease lot on insufficient margin
        while(marginRequired > freeMargin && lotSize > minLot)
        {
            lotSize = NormalizeDouble(lotSize - volumeStep, 2);
            if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lotSize, price, marginRequired)) break;
        }
        
        // If even minimum lot unavailable
        if(marginRequired > freeMargin)
        {
            // For micro account: if allowed to ignore limit for minimum lot, try minimum lot
            if(internalMicroMode && IgnoreMarginLimitForMinLot)
            {
                if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, minLot, price, marginRequired))
                {
                    if(marginRequired <= freeMargin)
                    {
                        return minLot;
                    }
                }
            }
            PrintFormat("Insufficient margin for minimum lot %.2f. Required: %.2f %s, Available: %.2f %s",
                        minLot, marginRequired, accountInfo.Currency(),
                        freeMargin, accountInfo.Currency());
            return -1;
        }
    }
    return lotSize;
}

//+------------------------------------------------------------------+
//| Run auto-optimization in tester                                  |
//+------------------------------------------------------------------+
void RunAutoOptimization()
{
    if(!MQLInfoInteger(MQL_TESTER) || !AutoOptimizeInTester) 
        return;

    Print("Starting auto-optimization...");
    
    // List of optimizable parameters
    string params[] = {
        "QuantumStates", "QuantumDecayRate", "StateUpdateRate", "StateMemoryFactor",
        "RSIPeriod", "ADXPeriod", "ATRPeriod", "FastMAPeriod", "SlowMAPeriod",
        "BaseRisk", "ATRMultiplierSL", "TPtoSLRatio"
    };
    
    // Random search for best parameters
    ArrayResize(bestParams, ArraySize(params));
    double bestResult = -DBL_MAX;
    
    for(int pass = 0; pass < OptimizationPasses; pass++)
    {
        // Generate random parameters
        internalQuantumStates = MathRand() % 46 + 5; // 5-50
        internalQuantumDecayRate = MathRand()/32767.0*0.09 + 0.01; // 0.01-0.1
        internalStateUpdateRate = MathRand()/32767.0*0.25 + 0.05; // 0.05-0.3
        internalStateMemoryFactor = MathRand()/32767.0*0.19 + 0.8; // 0.8-0.99
        internalRSIPeriod = MathRand() % 15 + 7; // 7-21
        internalADXPeriod = MathRand() % 16 + 10; // 10-25
        internalATRPeriod = MathRand() % 11 + 10; // 10-20
        internalFastMAPeriod = MathRand() % 81 + 20; // 20-100
        internalSlowMAPeriod = MathRand() % 201 + 100; // 100-300
        internalBaseRisk = MathRand()/32767.0*0.9 + 0.1; // 0.1-1.0
        internalATRMultiplierSL = MathRand()/32767.0*2.0 + 1.0; // 1.0-3.0
        internalTPtoSLRatio = MathRand()/32767.0*1.5 + 1.5; // 1.5-3.0
        
        // Correct parameters
        AutoCorrectParameters();
        
        // Run test with current parameters
        double result = OnTester();
        
        // Save best result
        if(result > bestResult)
        {
            bestResult = result;
            bestParams[0][1] = internalQuantumStates;
            bestParams[1][1] = internalQuantumDecayRate;
            bestParams[2][1] = internalStateUpdateRate;
            bestParams[3][1] = internalStateMemoryFactor;
            bestParams[4][1] = internalRSIPeriod;
            bestParams[5][1] = internalADXPeriod;
            bestParams[6][1] = internalATRPeriod;
            bestParams[7][1] = internalFastMAPeriod;
            bestParams[8][1] = internalSlowMAPeriod;
            bestParams[9][1] = internalBaseRisk;
            bestParams[10][1] = internalATRMultiplierSL;
            bestParams[11][1] = internalTPtoSLRatio;
        }
    }
    
    // Apply best parameters
    internalQuantumStates = (int)bestParams[0][1];
    internalQuantumDecayRate = bestParams[1][1];
    internalStateUpdateRate = bestParams[2][1];
    internalStateMemoryFactor = bestParams[3][1];
    internalRSIPeriod = (int)bestParams[4][1];
    internalADXPeriod = (int)bestParams[5][1];
    internalATRPeriod = (int)bestParams[6][1];
    internalFastMAPeriod = (int)bestParams[7][1];
    internalSlowMAPeriod = (int)bestParams[8][1];
    internalBaseRisk = bestParams[9][1];
    internalATRMultiplierSL = bestParams[10][1];
    internalTPtoSLRatio = bestParams[11][1];
    
    // Correct parameters
    AutoCorrectParameters();
    
    Print("Auto-optimization completed! Best result: ", bestResult);
    SaveOptimizedParams(); // Save parameters
    
    optimizationCompleted = true;
}

//+------------------------------------------------------------------+
//| Save optimized parameters                                        |
//+------------------------------------------------------------------+
void SaveOptimizedParams()
{
    int handle = FileOpen("OptimizedParams_"+_Symbol+".bin", FILE_WRITE|FILE_BIN);
    if(handle == INVALID_HANDLE) return;
    
    FileWriteInteger(handle, internalQuantumStates);
    FileWriteDouble(handle, internalQuantumDecayRate);
    FileWriteDouble(handle, internalStateUpdateRate);
    FileWriteDouble(handle, internalStateMemoryFactor);
    FileWriteInteger(handle, internalRSIPeriod);
    FileWriteInteger(handle, internalADXPeriod);
    FileWriteInteger(handle, internalATRPeriod);
    FileWriteInteger(handle, internalFastMAPeriod);
    FileWriteInteger(handle, internalSlowMAPeriod);
    FileWriteDouble(handle, internalBaseRisk);
    FileWriteDouble(handle, internalATRMultiplierSL);
    FileWriteDouble(handle, internalTPtoSLRatio);
    
    FileClose(handle);
    Print("Optimized parameters saved");
}

//+------------------------------------------------------------------+
//| Load optimized parameters                                        |
//+------------------------------------------------------------------+
void LoadOptimizedParams()
{
    int handle = FileOpen("OptimizedParams_"+_Symbol+".bin", FILE_READ|FILE_BIN);
    if(handle == INVALID_HANDLE) return;
    
    internalQuantumStates = FileReadInteger(handle);
    internalQuantumDecayRate = FileReadDouble(handle);
    internalStateUpdateRate = FileReadDouble(handle);
    internalStateMemoryFactor = FileReadDouble(handle);
    internalRSIPeriod = FileReadInteger(handle);
    internalADXPeriod = FileReadInteger(handle);
    internalATRPeriod = FileReadInteger(handle);
    internalFastMAPeriod = FileReadInteger(handle);
    internalSlowMAPeriod = FileReadInteger(handle);
    internalBaseRisk = FileReadDouble(handle);
    internalATRMultiplierSL = FileReadDouble(handle);
    internalTPtoSLRatio = FileReadDouble(handle);
    
    FileClose(handle);
    Print("Optimized parameters loaded");
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
    MathSrand(GetTickCount());
    
    // Initialize internal parameters
    internalQuantumStates = QuantumStates;
    internalQuantumDecayRate = QuantumDecayRate;
    internalStateUpdateRate = StateUpdateRate;
    internalStateMemoryFactor = StateMemoryFactor;
    internalRSIPeriod = RSIPeriod;
    internalADXPeriod = ADXPeriod;
    internalATRPeriod = ATRPeriodInput;
    internalFastMAPeriod = FastMAPeriodInput;
    internalSlowMAPeriod = SlowMAPeriodInput;
    internalBaseRisk = BaseRiskInput;
    internalATRMultiplierSL = ATRMultiplierSL;
    internalTPtoSLRatio = TPtoSLRatio;
    internalMinBarsBetweenTrades = MinBarsBetweenTrades;
    internalBaseTrailATRMultiplier = BaseTrailATRMultiplier;
    internalChaosThreshold = ChaosThreshold;
    
    ArrayResize(waveFunction, internalQuantumStates);
    ArrayResize(stateReturns, internalQuantumStates);
    ArrayResize(positionMatrix, internalQuantumStates);
    ArrayInitialize(waveFunction, 1.0/internalQuantumStates);
    ArrayInitialize(stateReturns, 0);
    
    // Auto-load parameters
    if(AutoOptimizeInTester && MQLInfoInteger(MQL_TESTER))
        LoadOptimizedParams();
    
    // Correct parameters
    AutoCorrectParameters();

    // Override parameters for instrument
    string symbol = Symbol();
    if (symbol == "XAUUSD" || symbol == "GOLD")
    {
        internalATRMultiplierSL = Gold_ATRMultiplierSL;
        internalTPtoSLRatio = Gold_TPtoSLRatio;
        internalBaseRisk = Gold_BaseRiskInput;
        internalMinBarsBetweenTrades = Gold_MinBarsBetweenTrades;
        internalBaseTrailATRMultiplier = Gold_BaseTrailATRMultiplier;
        internalChaosThreshold = Gold_ChaosThreshold;
    }
    else if (symbol == "XAGUSD" || symbol == "SILVER")
    {
        internalATRMultiplierSL = Silver_ATRMultiplierSL;
        internalTPtoSLRatio = Silver_TPtoSLRatio;
        internalBaseRisk = Silver_BaseRiskInput;
        internalMinBarsBetweenTrades = Silver_MinBarsBetweenTrades;
        internalBaseTrailATRMultiplier = Silver_BaseTrailATRMultiplier;
        internalChaosThreshold = Silver_ChaosThreshold;
    }
    
    InitAISystem();
    lastTradeTime = 0;
    lossCounter = 0;
    lastCacheTime = 0;
    lastTrailTime = 0;
    lastTrailSl = 0;
    lastTrailPrice = 0;
    marginErrorCount = 0;
    lastErrorTime = 0;
    
    // Initialize protection system
    UpdateDailyEquity();
    
    // For $500 deposit set minimum threshold $300
    if(accountInfo.Equity() <20) 
    {
        tradingHalted = true;
        Print("Deposit below $300. Trading will be stopped.");
    }
    
    // Auto-activate micro mode
    internalMicroMode = MicroAccountMode;
    if(accountInfo.Equity() < 500)
    {
        internalMicroMode = true;
        Print("Auto-activation of micro account mode: deposit < $500");
    }
    
    Print("Expert advisor successfully initialized for trading on ", symbol);
    PrintFormat("Deposit: $%.2f", accountInfo.Equity());
    PrintFormat("Base risk: %.1f%%, Max risk: %.1f%%", internalBaseRisk, MaxRisk);
    PrintFormat("Max position size: %.1f%%", MaxPositionSizePercent);
    PrintFormat("Deposit protection: %s", UseEquityProtection ? "Enabled" : "Disabled");
    
    isFirstRun = true;
    optimizationCompleted = false;
    
    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
    // Check minimum deposit ($300)
    if(accountInfo.Equity() < 10) 
    {
        Print("STOP! Deposit below $300. Trading stopped.");
        tradingHalted = true;
        return;
    }
    
    // Check trading status
    if(tradingHalted) 
    {
        static datetime lastAlert = 0;
        if(TimeCurrent() - lastAlert > 3600)
        {
            Print("Trading paused by protection system");
            lastAlert = TimeCurrent();
        }
        return;
    }
    
    // Run auto-optimization on first run in tester
    if(isFirstRun && MQLInfoInteger(MQL_TESTER) && AutoOptimizeInTester && !optimizationCompleted)
    {
        RunAutoOptimization();
        isFirstRun = false;
        return; // Skip first tick after optimization
    }
    
    // Apply quantum trailing stop
    ApplyQuantumTrailing();

    // v4.0: partial close + giveback guard (every tick)
    V4_Manage();
    
    // Check hard stop
    CheckHardStop();
    
    static datetime lastBarTime = 0;
    datetime currentBarTime = iTime(_Symbol, AnalysisTimeframe, 0);
    
    // Work only on new bar open
    if(currentBarTime == lastBarTime) return;
    lastBarTime = currentBarTime;
    
    // Update equity data
    UpdateDailyEquity();
    
    // Check limits before trading
    if(!CheckDrawdownLimits()) return;
    if(!CheckDailyLossLimit()) return;
    
    // Generate signal
    int signal = GenerateTradeSignal();
    if(signal < 0) return;

    // v4.0: spread filter
    if(!V4_SpreadOK()) return;
    
    // Protection against frequent trades
    if(TimeCurrent() - lastTradeTime < internalMinBarsBetweenTrades * PeriodSeconds(AnalysisTimeframe)) 
        return;
    
    // Calculate trade parameters
    double atr = lastATR;
    double price = (signal == ORDER_TYPE_BUY) ? 
         SymbolInfoDouble(_Symbol, SYMBOL_ASK) : 
         SymbolInfoDouble(_Symbol, SYMBOL_BID);
    
    // Calculate SL and TP
    double sl = (signal == ORDER_TYPE_BUY) ? 
         price - atr * internalATRMultiplierSL : 
         price + atr * internalATRMultiplierSL;
    
    // v4.0 FIX: TP must be a multiple of the SL DISTANCE, not raw ATR.
    // Old code: tp = price ± atr * ratio  -> real RR was ratio/ATRMultSL (e.g. 1.33 not 2.0)
    double slDist = atr * internalATRMultiplierSL;
    double tp = (signal == ORDER_TYPE_BUY) ? 
         price + slDist * internalTPtoSLRatio : 
         price - slDist * internalTPtoSLRatio;
    
    // Ensure minimum distance for TP
    double minTpDistance = atr * 0.5; // 50% of ATR
    if(MathAbs(price - tp) < minTpDistance)
    {
        tp = (signal == ORDER_TYPE_BUY) ? 
             price + minTpDistance : 
             price - minTpDistance;
        PrintFormat("TP correction: Set minimum distance %.2f points", minTpDistance);
    }
    
    // Normalize prices
    price = NormalizePrice(price);
    sl = NormalizePrice(sl);
    tp = NormalizePrice(tp);
    
    // Validate SL/TP
    double checkPrice = (signal == ORDER_TYPE_BUY) ? 
         SymbolInfoDouble(_Symbol, SYMBOL_ASK) : 
         SymbolInfoDouble(_Symbol, SYMBOL_BID);
    
    if(!CheckStopLossTakeprofit(signal == ORDER_TYPE_BUY ? POSITION_TYPE_BUY : POSITION_TYPE_SELL, 
                               checkPrice, sl, tp))
    {
        Print("Invalid SL/TP levels. Trade rejected.");
        return;
    }
    
    // Adaptive risk
    currentRisk = CalculateAdaptiveRisk();
    
    // Calculate position size with margin consideration
    double slDistance = MathAbs(price - sl);
    double lotSize = CalculateLotSize(currentRisk, price, slDistance);
    
    // Check for calculation error
    if(lotSize < 0)
    {
        Print("Trade impossible: insufficient margin even for minimum lot");
        marginErrorCount++;
        lastErrorTime = TimeCurrent();
        
        if(marginErrorCount >= 3)
        {
            tradingHalted = true;
            Print("Trading paused: 3 consecutive margin errors");
            EventSetTimer(3600); // Auto-resume after 1 hour
        }
        return;
    }
    else
    {
        marginErrorCount = 0; // Reset counter on successful calculation
    }
    
    // Additional check for micro accounts
    if(internalMicroMode)
    {
        double maxMicroLot = accountInfo.FreeMargin() / (price * 0.01); // Simplified calculation
        if(lotSize > maxMicroLot)
        {
            lotSize = MathMin(lotSize, maxMicroLot);
            PrintFormat("Lot correction for micro account: %.2f", lotSize);
        }
    }
    
    // Final check before opening
    double marginCheck = accountInfo.MarginCheck(_Symbol, 
        (signal == ORDER_TYPE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,
        lotSize, price);
        
    if(marginCheck > accountInfo.FreeMargin())
    {
        Print("Margin error: required ", marginCheck, " ", accountInfo.Currency(), 
              ", available ", accountInfo.FreeMargin());
        return;
    }
    
    // Check position size
    if(!CheckPositionSize(lotSize, price))
    {
        Print("Position size exceeds set limits. Trade rejected.");
        return;
    }
    
    // Open position
    bool success = false;
    if(signal == ORDER_TYPE_BUY)
        success = trade.Buy(lotSize, _Symbol, price, sl, tp, "Quantum AI Buy");
    else
        success = trade.Sell(lotSize, _Symbol, price, sl, tp, "Quantum AI Sell");

    if(success) 
    {
        lastTradeTime = TimeCurrent();
        PrintFormat("Trade: %s Lot: %.2f Risk: %.1f%% ATR: %.2f",
                    EnumToString((ENUM_ORDER_TYPE)signal),
                    lotSize,
                    currentRisk,
                    atr);
        
        // Daily report
        MqlDateTime timeStruct;
        TimeCurrent(timeStruct);
        if(timeStruct.hour == 8)
        {
            double equity = accountInfo.Equity();
            double riskPerTrade = equity * (currentRisk/100);
            SendNotification("QuantumTrader: Deposit="+DoubleToString(equity,2)+
                             " | Risk/trade="+DoubleToString(riskPerTrade,2));
        }
    }
    else
    {
        uint errorCode = GetLastError();
        Print("Order opening error: Code ", errorCode, " - ", ErrorDescription(errorCode));
        currentRisk = MathMax(0.1, currentRisk * RiskDecreaseFactor);
    }
    
    isFirstRun = false;
}

//+------------------------------------------------------------------+
//| Timer event handler                                              |
//+------------------------------------------------------------------+
void OnTimer()
{
    if(tradingHalted)
    {
        // Reduce risk by 30% on recovery
        currentRisk = MathMax(0.1, currentRisk * 0.7);
        tradingHalted = false;
        marginErrorCount = 0;
        Print("Trading resumed. Risk reduced to ", currentRisk, "%");
    }
}

//+------------------------------------------------------------------+
//| System update after trade                                        |
//+------------------------------------------------------------------+
void OnTrade()
{
    if(HistoryDealSelect(HistoryDealsTotal()-1))
    {
        double profit = HistoryDealGetDouble(HistoryDealGetTicket(HistoryDealsTotal()-1), DEAL_PROFIT);
        int type = (int)HistoryDealGetInteger(HistoryDealGetTicket(HistoryDealsTotal()-1), DEAL_TYPE);
        int signal = (type == DEAL_TYPE_BUY) ? 1 : -1;
        
        UpdateAI(signal, profit);
        
        // Update quantum states
        stateReturns[quantumState] = stateReturns[quantumState] * internalStateMemoryFactor 
                                   + profit * internalStateUpdateRate;
        
        // Risk adaptation
        if(profit < 0) 
        {
            lossCounter++;
            if(lossCounter >= MaxConsecutiveLosses) 
                currentRisk = MathMax(0.1, currentRisk * RiskDecreaseFactor);
        }
        else
        {
            lossCounter = 0;
            currentRisk = MathMin(MaxRisk, currentRisk * RiskIncreaseFactor);
        }
        
        // Update daily profit data
        UpdateDailyEquity();
    }
}

//+------------------------------------------------------------------+
//| Tester function with automatic 70/30 OOS validation              |
//+------------------------------------------------------------------+
double OnTester()
{
    HistorySelect(0, TimeCurrent());
    int total_deals = HistoryDealsTotal();
    if(total_deals <= 0) return 0;

    datetime first_deal_time = (datetime)HistoryDealGetInteger(HistoryDealGetTicket(0), DEAL_TIME);
    datetime last_deal_time = (datetime)HistoryDealGetInteger(HistoryDealGetTicket(total_deals-1), DEAL_TIME);
    
    datetime IS_end = first_deal_time + (datetime)((last_deal_time - first_deal_time) * 0.7);
    datetime OOS_start = IS_end + 1;
    datetime OOS_end = last_deal_time;
    
    if(OOS_start >= last_deal_time) {
        Print("Warning: OOS period too small. Using last 30% of data.");
        OOS_start = first_deal_time + (datetime)((last_deal_time - first_deal_time) * 0.7);
        OOS_end = last_deal_time;
    }

    double deposit = 10000;
    double balance = deposit;
    double max_balance = deposit;
    
    double IS_dd = 0;
    double IS_profit = 0;
    double IS_positive = 0, IS_negative = 0;
    int IS_trades = 0;
    double IS_max_profit = -DBL_MAX, IS_min_profit = DBL_MAX;
    
    double OOS_dd = 0;
    double OOS_profit = 0;
    double OOS_positive = 0, OOS_negative = 0;
    int OOS_trades = 0;
    double OOS_max_profit = -DBL_MAX, OOS_min_profit = DBL_MAX;
    
    for(int i = 0; i < total_deals; i++)
    {
        ulong ticket = HistoryDealGetTicket(i);
        if(ticket == 0) continue;
        
        datetime time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
        double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
        double commission = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
        double swap = HistoryDealGetDouble(ticket, DEAL_SWAP);
        double total = profit + commission + swap;
        
        balance += total;
        
        if(balance > max_balance) max_balance = balance;
        double dd = max_balance - balance;
        
        if(time <= IS_end)
        {
            IS_trades++;
            IS_profit += total;
            
            if(total > 0) IS_positive += total;
            else IS_negative -= total;
            
            if(total > IS_max_profit) IS_max_profit = total;
            if(total < IS_min_profit) IS_min_profit = total;
            
            if(dd > IS_dd) IS_dd = dd;
        }
        else if(time >= OOS_start && time <= OOS_end)
        {
            OOS_trades++;
            OOS_profit += total;
            
            if(total > 0) OOS_positive += total;
            else OOS_negative -= total;
            
            if(total > OOS_max_profit) OOS_max_profit = total;
            if(total < OOS_min_profit) OOS_min_profit = total;
            
            if(dd > OOS_dd) OOS_dd = dd;
        }
    }
    
    double IS_pf = (IS_negative > 0) ? IS_positive / IS_negative : 100;
    double IS_rf = (IS_dd > 0) ? IS_profit / IS_dd : IS_profit * 100;
    double IS_win_rate = (IS_trades > 0) ? (double)IS_positive/(IS_positive+IS_negative) : 0;
    double IS_avg_win = (IS_positive > 0 && IS_win_rate > 0) ? IS_positive/(IS_trades * IS_win_rate) : 0;
    double IS_avg_loss = (IS_negative > 0 && (1-IS_win_rate) > 0) ? IS_negative/(IS_trades * (1-IS_win_rate)) : 0;
    double IS_payoff = (IS_avg_loss != 0) ? IS_avg_win/IS_avg_loss : 10;
    double IS_sharpe = (IS_avg_loss > 0) ? (IS_avg_win - IS_avg_loss) / MathSqrt(0.5*(IS_avg_win*IS_avg_win + IS_avg_loss*IS_avg_loss)) : 10;
    
    double OOS_pf = (OOS_negative > 0) ? OOS_positive / OOS_negative : 100;
    double OOS_rf = (OOS_dd > 0) ? OOS_profit / OOS_dd : OOS_profit * 100;
    double OOS_win_rate = (OOS_trades > 0) ? (double)OOS_positive/(OOS_positive+OOS_negative) : 0;
    double OOS_avg_win = (OOS_positive > 0 && OOS_win_rate > 0) ? OOS_positive/(OOS_trades * OOS_win_rate) : 0;
    double OOS_avg_loss = (OOS_negative > 0 && (1-OOS_win_rate) > 0) ? OOS_negative/(OOS_trades * (1-OOS_win_rate)) : 0;
    double OOS_payoff = (OOS_avg_loss != 0) ? OOS_avg_win/OOS_avg_loss : 10;
    double OOS_sharpe = (OOS_avg_loss > 0) ? (OOS_avg_win - OOS_avg_loss) / MathSqrt(0.5*(OOS_avg_win*OOS_avg_win + OOS_avg_loss*OOS_avg_loss)) : 10;
    
    if(OOS_trades < 20) return -1e9;
    if(OOS_profit <= 0) return -1e8;
    if(IS_profit <= 0) return -1e7;
    if(OOS_rf < 1.0) return -1e6;
    
    double score = 0;
    
    score += 15.0 * MathLog(OOS_rf + 1);
    score += 10.0 * MathLog(OOS_pf + 1);
    score += 7.5 * MathLog(OOS_sharpe + 1);
    score += 7.5 * MathLog(OOS_payoff + 1);
    
    double consistency = 1.0;
    consistency *= 0.7 + 0.3 * MathMin(OOS_win_rate / IS_win_rate, 1.5);
    consistency *= 0.7 + 0.3 * MathMin(OOS_payoff / IS_payoff, 1.5);
    consistency *= 0.7 + 0.3 * MathMin(OOS_rf / IS_rf, 1.5);
    score += 30.0 * consistency;
    
    double stability = 1.0;
    stability -= 0.2 * MathAbs(IS_win_rate - OOS_win_rate);
    stability -= 0.2 * MathAbs(IS_payoff - OOS_payoff);
    stability -= 0.1 * MathAbs(IS_sharpe - OOS_sharpe);
    stability -= 0.1 * (OOS_max_profit - OOS_min_profit) / (OOS_avg_win + OOS_avg_loss);
    score += 20.0 * MathMax(0, stability);
    
    double risk_penalty = 1.0;
    risk_penalty -= 0.3 * MathMin(1.0, OOS_dd / deposit);
    risk_penalty -= 0.2 * MathMin(1.0, (OOS_max_profit - OOS_min_profit) / (OOS_avg_win + OOS_avg_loss));
    score *= MathMax(0.5, risk_penalty);
    
    score *= 1.0 + 0.1 * MathLog(1 + OOS_trades);
    
    return score;
}

//+------------------------------------------------------------------+
//| Deinitialization function                                        |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    // Save AI state
    string fileName = "QuantumAI_"+_Symbol+".txt";
    int handle = FileOpen(fileName, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
    if(handle != INVALID_HANDLE)
    {
        for(int i = 0; i < 6; i++)
            FileWrite(handle, DoubleToString(aiWeights[i], 8));
        FileClose(handle);
        Print("AI weights successfully saved");
    }
    else
    {
        Print("Error saving AI weights: ", GetLastError());
    }
    
    // Save optimized parameters
    if(optimizationCompleted && AutoOptimizeInTester && MQLInfoInteger(MQL_TESTER))
    {
        SaveOptimizedParams();
    }
    
    // Release indicator handles
    if(rsiHandle != INVALID_HANDLE) IndicatorRelease(rsiHandle);
    if(adxHandle != INVALID_HANDLE) IndicatorRelease(adxHandle);
    if(atrHandle != INVALID_HANDLE) IndicatorRelease(atrHandle);
    if(maFastHandle != INVALID_HANDLE) IndicatorRelease(maFastHandle);
    if(maSlowHandle != INVALID_HANDLE) IndicatorRelease(maSlowHandle);
    
    // Save protection state
    if(reason == REASON_INITFAILED || reason == REASON_PROGRAM)
    {
        Print("Expert advisor stopped by protection. Manual restart required.");
    }
    
    // Kill timer
    EventKillTimer();
}
//+------------------------------------------------------------------+