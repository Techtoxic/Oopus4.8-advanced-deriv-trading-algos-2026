//+------------------------------------------------------------------+
//|     Silver_MSNR_v531Plus_AEU_VisualOnly_CleanStudy_v2.mq5                 |
//|     HTF bias first, then 2 aligned setup signals can trade        |
//|                                                                  |
//|     Core preserved from v2_01/v2_00:                              |
//|     - Malaysian SNR body levels                                   |
//|     - HTF GAP SNR                                                  |
//|     - Liquidity Sweep / MISS / Engulfing / TL / QML / CRT         |
//|     - SL risk per order default 0.5%                               |
//|     - TP by DOL first, else STD/RR projection                      |
//|                                                                  |
//|     New in v2.02:                                                  |
//|     - Signals on same SNR zone are grouped                         |
//|     - One trade per confluence cluster                             |
//|     - Comment clearly shows confluence layers: L2+L4+L6 etc.       |
//+------------------------------------------------------------------+
#property strict
#property version   "5.31"

#include <Trade/Trade.mqh>
CTrade trade;

//====================================================================
// INPUTS
//====================================================================
input string          InpSymbol                 = "";       // blank = use tester/chart symbol, e.g. XAUUSDm
input ENUM_TIMEFRAMES InpExecTF                 = PERIOD_M5;    // v5.17: trade M5

input ENUM_TIMEFRAMES InpHTF_Weekly             = PERIOD_W1;
input ENUM_TIMEFRAMES InpHTF_Daily              = PERIOD_D1;
input ENUM_TIMEFRAMES InpHTF_H4                 = PERIOD_H4;
input ENUM_TIMEFRAMES InpHTF_H1                 = PERIOD_H1;

input double          RiskPercentPerOrder       = 1;      // SL mỗi lệnh = 1% tài khoản
input double          FixedLot                  = 0.00;     // 0 = auto lot by risk
input int             MagicNumber               = 26053102;
input int             SlippagePoints            = 50;
input double          MaxSpreadPoints           = 350;

// MQL5 validation / prop-firm safety controls
input double          MaxLotPerTrade             = 0.01;     // 0 = no hard cap; default is validation-safe
input double          MaxMarginUsagePercent      = 50.0;     // max free margin to use for a new order
input bool            SkipIfMarginNotEnough      = true;     // skip instead of sending an order that may return No Money
input bool            BlockWrongChartTimeframe   = true;     // trade only when chart timeframe equals InpExecTF

// giữ tinh thần không giới hạn tổng lệnh, nhưng mỗi cụm confluence chỉ mở 1 lệnh
input bool            NoTotalTradeLimit         = true;
input int             MaxTotalPositions         = 0;        // 0 = không giới hạn nếu NoTotalTradeLimit = true
input int             MaxConfluenceTradesPerBar = 2;        // cùng một nến tối đa 2 cụm tín hiệu       // tối đa số cụm confluence được vào trong 1 nến
input bool            AllowBuyAndSellSameBar    = true;

input int             HTFScanBars               = 220;
input int             SwingLookback             = 3;
input int             RecentSwingBars           = 25;

input double          TouchBufferPrice          = 0.25;
input double          SweepBufferPrice          = 0.10;
input double          EqualLevelTolerance       = 0.25;
input double          ConfluenceZonePrice       = 0.50;     // gom các level gần nhau thành một cụm
input double          FreshBodyBreakBuffer      = 0.05;

input double          SL_BufferPrice            = 0.30;      // M5/M15 tight invalidation buffer
input double          MinSLPrice                = 0.50;      // M5/M15 minimum valid SL
input double          MaxSLPrice                = 12.00;     // M5/M15 cho phép SL rộng hơn M1 nhưng vẫn lọc

input bool            UseDOLTargetFirst         = true;
input bool            RequireDOLToMeetMinRR      = false;    // v5.18: TP cuối theo tài liệu, ưu tiên DOL
input bool            AllowSTDTargetIfNoDOL      = true;     // nếu DOL không đủ/không có, dùng STD/RR projection
input double          MinRR                     = 2.00;      // v5.32: was 5.00 — main reason no trades fired
input double          FallbackRR                = 5.00;      // fallback tối thiểu nếu DOL không rõ      // Full TP 5R
input double          STD_Projection_Multiple   = 2.50;      // STD projection theo tài liệu, có thể chỉnh 2.5/4/5      // Full TP 5R

// Full TP test mode v5.13
input bool            UseFixedFullTP_RR          = false;    // v5.18: false = TP cuối theo DOL/STD tài liệu
input double          FixedFullTP_RR             = 5.00;     // chỉ dùng nếu bật UseFixedFullTP_RR

input bool            UseDocumentTPMode          = true;     // TP cuối theo tài liệu: DOL trước, không có thì STD/RR projection
input double          MinDocumentTP_RR           = 2.00;     // v5.32: was 5.00 — relaxed so DOL/STD targets qualify

input bool            UseBEInFullTPMode          = false;    // test sạch: không kéo BE trước TP


input bool            MoveSLToBEAtRR1           = false;    // v5.13: test sạch TP 5R, không kéo BE sớm
input double          BE_OffsetPrice            = 0.05;

input bool            UseSessionFilter          = false;
input int             SessionStartHourUTC7      = 6;
input int             SessionEndHourUTC7        = 23;
input int             ServerToUTC7_OffsetHours  = 0;

input bool            DrawLevels                = false;    // CleanStudy: tắt rừng line SNR phụ, logic vẫn giữ
input bool            PrintDebug                = true;

//====================================================================
// VISUAL ONLY DASHBOARD v1
// Không thay đổi logic vào lệnh, không đổi MagicNumber, không đổi session/risk.
//====================================================================
input bool            VO_ShowDashboard531        = true;
input bool            VO_ApplyChartTheme531      = true;
input bool            VO_ShowKeyLevels531        = true;
input bool            VO_ShowEQ531             = false;    // mặc định tắt EQ để chart sạch hơn
input bool            VO_ShowPDHPDL531           = true;
input bool            VO_ShowSessionHL531        = true;
input bool            VO_ShowNearestHTFSNR531    = true;
input int             VO_MaxSNRLines531          = 2;        // chỉ 1 kháng cự + 1 hỗ trợ gần nhất
input double          VO_MinLineDistance531     = 8.0;      // tránh line quá sát nhau
input double          VO_SNRNearPrice531         = 45.0;     // thu hẹp vùng SNR gần giá
input int             VO_DashboardX531           = 14;
input int             VO_DashboardY531           = 18;
input int             VO_DashboardW531           = 430;
input int             VO_DashboardH531           = 112;
input int             VO_UpdateSeconds531        = 20;
input bool            VO_NoForcedChartRedraw531  = true;


input bool            EnforceM5Only             = true;     // v5.17: chỉ cho chạy M5

// v5.16: 5/10 confirmation mode, no market trend/regime filter
input int             MinConfirmationsToTrade    = 4;        // v5.32: was 5
input bool            Use5of10Mode               = true;

// v5.20 whitelist combo mode, no fixed side. If combo appears in BUY idea, trade BUY; if appears in SELL idea, trade SELL.
input bool            Use5of10Whitelist          = false;    // v5.32: was true — exact-combo whitelist almost never matches
input bool            WhitelistAllowSuperset     = true;     // v5.23: allow 6+/10 superset of good combos

// Whitelist combos from v5.19 analysis, direction not fixed
input bool            WL_L1_L4_L5_L7_L8          = true;     // strongest: Touch + Engulf + GAP + QML + QML Engulf
input bool            WL_L1_L3_L4_L5_L10         = true;     // MISS + Engulf + GAP + CRT
input bool            WL_L1_L3_L4_L6_L9          = true;     // MISS + Engulf + TL + BO
input bool            WL_L1_L3_L6_L9_L10         = true;     // MISS + TL + BO + CRT
input bool            WL_L1_L2_L3_L4_L10         = true;     // Sweep + MISS + Engulf + CRT
input bool            WL_L1_L3_L4_L7_L8          = true;     // backup QML cluster

// Range spam lock
input bool            UseRangeSpamLock           = true;
input int             RangeLookbackBars          = 36;       // v5.23: shorter range box, more responsive
input double          RangeWidthMaxPrice         = 25.0;     // v5.23: detect wider ranges too
input int             MaxTradesPerRangeZone      = 4;        // v5.23: not too sparse, but still anti-spam
input double          RangeZoneMergePrice        = 6.0;      // v5.23: separate nearby range boxes slightly better

// Expansion / strong move mode
input bool            UseExpansionBoost          = true;
input int             ExpansionLookbackBars      = 24;       // M5 24 bars ~ 2 hours
input double          ExpansionRangeMinPrice     = 20.0;     // v5.23: catch more expansion starts
input double          ExpansionCandleBodyMin     = 3.2;      // v5.23: softer displacement threshold
input int             ExpansionMinConfirmations  = 5;        // still at least 5 confirmations
input double          ExpansionMaxSLPrice        = 16.0;     // allow wider structural SL in strong move

// v5.22 Premium/Discount + Range Break-Hold filter
input bool            UsePremiumDiscountFilter   = true;
input ENUM_TIMEFRAMES PDRangeTF                  = PERIOD_H1;
input int             PDLookbackBars             = 80;
input double          BuyMaxPDPosition           = 0.60;     // v5.23: BUY allowed up to 60% if setup is strong
input double          SellMinPDPosition          = 0.45;     // v5.23: SELL allowed from 45% upward
input bool            AllowBreakHoldOverridePD   = true;     // range break-hold may override PD filter

input bool            RequireRangeEdgeOrBreak    = true;     // in narrow range, only edge trade or break-hold
input double          RangeEdgeTradePercent      = 0.38;     // v5.23: edge zone wider, more trades

input bool            UseRangeBreakHold          = true;
input int             BreakHoldLookbackBars      = 48;       // prior range
input double          BreakHoldBufferPrice       = 1.0;
input double          BreakHoldBodyMinPrice      = 2.2;
input int             BreakHoldValidBars         = 10;       // v5.23: breakout signal remains valid longer

input bool            UseNormalStricterFilter    = true;
input int             NormalMinConfirmations     = 5;        // v5.23: allow normal if it has whitelist + 5/10

input bool            BlockWorstNormalBuySetups  = true;     // block NORMAL BUY groups that were worst in long test

// v5.24 Dual Mode: REACTION + MOMENTUM
input bool            UseDualMode                = true;

// REACTION lane: same DNA as whitelist/5-of-10, used for zone reaction setups.
input bool            EnableReactionLane         = true;
input int             ReactionMinConfirmations   = 4;        // v5.32: was 5 — 4/10 still strict

// MOMENTUM lane: catches strong trends that do not give enough classic setup confirmations.
input bool            EnableMomentumLane         = true;
input int             MomentumMinConfirmations   = 3;        // 3/5 momentum confirmations
input bool            MomentumBypassReactionWL   = true;     // momentum does not need classic whitelist
input bool            MomentumBypassPDFilter     = true;     // strong break can override PD
input bool            MomentumOnlyInExpansion    = true;     // safer: only expansion/break-hold
input int             MomentumRangeBars          = 48;       // range to break
input double          MomentumBreakBuffer        = 1.0;
input double          MomentumBodyMin            = 3.5;
input bool            MomentumNeedFVG            = true;
input bool            MomentumNeedPullbackHold   = false;    // first test loose, can tighten later
input int             MomentumHTFBars            = 24;
input double          MomentumHTFMoveMin         = 18.0;     // H1 movement threshold

// Trade volume controls for dual mode
input int             MaxMomentumTradesPerBar    = 1;
input int             MaxReactionTradesPerBar    = 2;

// v5.27 Momentum Filtered from v5.25 result analysis
input bool            UseMomentumFilterV527      = true;

// Momentum EXPANSION was too wide in v5.25.
// New rule: keep volume but only allow proven momentum clusters.
input bool            MomExpansionRequireM123M5  = true;     // require M1+M2+M3+M5 in EXPANSION
input bool            MomBreakHoldRequireM123    = true;     // require M1+M2+M3 in BREAK_HOLD
input bool            MomBlockL1L10Everywhere    = true;     // L1+L10 was the biggest loser
input bool            MomBlockBadExpansionGroups = true;

// Good momentum expansion groups from v5.25
input bool            MOM_EXP_SELL_L1_L7_L10     = true;     // SELL L1+L7+L10
input bool            MOM_EXP_SELL_L1_L7         = true;     // SELL L1+L7
input bool            MOM_EXP_BUY_L1_L6          = true;     // BUY L1+L6
input bool            MOM_EXP_BUY_L1_L6_L9       = true;     // BUY L1+L6+L9
input bool            MOM_EXP_BUY_L1             = true;     // small edge in v5.25, keep for volume

// Break-hold momentum groups. These were useful in v5.25.
input bool            MOM_BH_ALLOW_L1            = true;
input bool            MOM_BH_ALLOW_L1_L7         = true;
input bool            MOM_BH_ALLOW_L1_L10        = true;     // allowed only in BREAK_HOLD, blocked in EXPANSION
input bool            MOM_BH_ALLOW_ANY_M123M5    = true;     // allow stronger momentum with HTF pressure

// Reaction lane remains as v5.25/v5.23 benchmark
input bool            KeepReactionLaneV525       = true;

// v5.29 Strong-trend / long-cycle protection from 6-year test
input bool            DisableReactionRangeLock   = true;     // 6y: Reaction + RangeLock was the main hole
input bool            DisableNormalMarket        = false;    // v5.32: was true — this killed ~80% of all hours
input bool            DisableBuyInWeakHTF        = false;    // v5.32: was true — over-blocked momentum buys

// v5.29 Strong Trend Breakout Lane, independent from 5/10 clusters.
// Purpose: catch runaway days that classic SNR reaction misses.
input bool            EnableStrongTrendLane      = true;
input int             StrongTrendMaxTradesPerBar = 1;
input int             ST_RangeBars               = 36;       // M5 prior range
input double          ST_BreakBufferPrice        = 1.0;
input double          ST_BodyMinPrice            = 3.5;
input bool            ST_RequireFVG              = false;    // false first to avoid missing runaway candles
input bool            ST_RequireD1H4Align        = true;
input int             ST_D1Bars                  = 20;
input int             ST_H4Bars                  = 30;
input double          ST_D1MoveMin               = 45.0;
input double          ST_H4MoveMin               = 18.0;
input bool            ST_SellOnlyInHTFDown       = false;    // allow SELL corrections in parabolic gold
input bool            ST_AllowBuyOnlyDiscount    = true;
input double          ST_BuyMaxPDPosition        = 0.62;
input bool            ST_AllowSellOnlyPremium    = false;
input double          ST_SellMinPDPosition       = 0.45;
input double          ST_MaxSLPrice              = 22.0;

// v5.30 Trend-Armed Pullback Lane
input bool            EnableTrendArmedPullback   = true;
input int             TA_ValidBars               = 10;
input int             TA_MaxEntriesPerArm        = 2;
input bool            TA_DisableInstantSTSell    = true;
input bool            TA_AllowInstantSTBuy       = true;
input double          TA_RetestBufferPrice       = 1.2;
input double          TA_FVGCE_TolerancePrice    = 1.2;
input double          TA_MinPullbackPrice        = 2.0;
input double          TA_MaxPullbackPrice        = 28.0;
input double          TA_ContinuationBodyMin     = 1.8;
input bool            TA_RequireRetestOrFVG      = true;
input bool            TA_RequireContinuationCandle = true;
input bool            TA_SellNeedsHoldOrRetest   = true;
input bool            TA_BuyNeedsDiscountOrRetest = true;

// v5.31 Hybrid guard: v5_29 core + v5_25 boost + v5_23 safety
input bool            TA_EnablePullbackBuy       = true;     // keep only the profitable pullback side
input bool            TA_EnablePullbackSell      = false;    // v5_30 analysis: PB SELL was the main leak

input bool            UseSafetyGuard531          = true;
input int             MaxLosingDealsInRow531     = 14;       // after N losing OUT deals, pause
input int             PauseBarsAfterLossStreak531= 36;
input bool            UseBalanceDDGuard531       = true;
input double          MaxBalanceDDPausePct531    = 28.0;     // soft circuit breaker, not permanent stop
input int             PauseBarsAfterDD531        = 72;
input bool            ResetPeakAfterDDPause531   = true;

//====================================================================
// v5.31 PLUS - session + risk management only
// Giữ nguyên setup core của v5.31, không lọc lại setup như v5.38.
// MT5 server của Silver = GMT+0, Việt Nam = GMT+7.
//====================================================================
input bool            UseSessionPlus531          = true;

// Session theo giờ MT5/GMT+0 sau khi quy đổi từ GMT+7.
// Asia VN 06-11 = MT5 23-04
// Europe VN 14-18 = MT5 07-11
// US VN 19-23 = MT5 12-16
input bool            PlusTradeAsia              = true;     // Asia ON
input int             PlusAsiaStartHour          = 23;
input int             PlusAsiaEndHour            = 4;

input bool            PlusTradeEurope            = true;     // Europe ON
input int             PlusEuropeStartHour        = 7;
input int             PlusEuropeEndHour          = 11;

input bool            PlusTradeUS                = true;     // US ON
input int             PlusUSStartHour            = 12;
input int             PlusUSEndHour              = 16;

// Giờ bonus 05 MT5 = 12h VN từng rất mạnh trong thống kê tổng hợp
input bool            PlusTradeBonusHour         = false;    // OFF để test sạch 3 phiên chính
input int             PlusBonusStartHour         = 5;
input int             PlusBonusEndHour           = 6;

// Không trade ngoài các phiên trên
input bool            PlusBlockOutsideSessions   = true;

// Session clock control. Default is UTC because Silver's MT5 server is UTC+0.
// If broker server is UTC+2, set PlusServerUTCOffsetHours = 2.
input bool            PlusUseUTCSessionClock     = true;
input int             PlusServerUTCOffsetHours   = 0;
input bool            PlusAutoNYDST_US           = true;     // US session shifts automatically between UTC-4 and UTC-5
input int             PlusUSStartHourUTC_Summer  = 12;       // New York UTC-4 period
input int             PlusUSEndHourUTC_Summer    = 16;
input int             PlusUSStartHourUTC_Winter  = 13;       // New York UTC-5 period
input int             PlusUSEndHourUTC_Winter    = 17;

// Risk theo equity curve, nhưng KHÔNG khóa setup lõi.
input bool            UseRiskPlus531             = true;
input double          PlusRiskSafePercent        = 0.50;
input double          PlusRiskGrowthPercent      = 1.00;
input double          PlusRiskLockdownPercent    = 0.25;
input int             PlusEquityLookbackDeals    = 20;
input double          PlusGrowthScore            = 2.0;
input double          PlusLockdownScore          = -3.0;
input int             PlusMaxLosingDealsStreak   = 10;
input int             PlusLockdownBars           = 36;

// Optional: nếu muốn volume nhiều hơn, bật Mỹ trong input, không cần sửa code.










// Profit management v5.16
input bool            UsePartialAt4R             = true;
input double          PartialAtRR                = 4.00;     // đạt 4R chốt 20%
input double          PartialPercent             = 20.0;     // v5.28: chốt 20% ở 4R, giữ 80% về final
input bool            MoveSLAfterPartial         = true;
input double          SLAfterPartialLockRR       = 0.00;     // sau TP1 dời runner về BE



// Tester export fallback: writes CSV even when MT5 Results tab is blank
input bool            ExportCSVOnDeinit          = true;
input bool            ExportCSVToCommonFolder    = true;     // true: File -> Open Data Folder -> Common -> Files
input string          ExportCSVFileName          = "Silver_MSNR_v531Plus_AEU_Backtest_Deals.csv";
input bool            ExportSignalLogOnEntry     = true;     // ghi file ngay khi mở lệnh, dễ tìm hơn Results
input string          ExportSignalLogFileName    = "Silver_MSNR_v531Plus_AEU_Signal_Log.csv";
input string          ExportClosedTradeFileName  = "Silver_MSNR_v531Plus_AEU_Closed_Trades.csv";



// Exact best combo test mode - improved after M5 result
input bool            UseWhitelistCombos         = false;    // v5.16: không dùng whitelist, chỉ cần đủ 5/10 xác nhận
input bool            Enable_CORE                = true;
input bool            Enable_WATCH               = true;

// CORE combos from M5 result
input bool            WL_CORE_L1_L4_L6_L9        = true;     // best core: Touch + Engulf + TL + BO/Retest
input bool            WL_CORE_L1_L3_L4_L6_L9     = true;     // best superset: Touch + MISS + Engulf + TL + BO/Retest

// WATCH combos with positive but smaller sample
input bool            WL_WATCH_L1_L4_L5_L6_L9    = true;     // Touch + Engulf + GAP + TL + BO/Retest
input bool            WL_WATCH_L1_L2_L4_L5_L6    = true;     // Touch + Sweep + Engulf + GAP + TL
input bool            WL_WATCH_L1_L2_L3_L4_L5_L6 = true;     // Touch + Sweep + MISS + Engulf + GAP + TL

// Optional off: risky combo family from earlier result, disabled for clean test
input bool            Enable_EXTRA               = false;
input bool            WL_EXTRA_L1_L4_L6_L9_L10   = true;     // only test later if needed

// HTF Storyline Bias Filter - no EMA.
// Bias is built from HTF SNR rejection, DOL, failure to displace, premium/discount, CRT/FVG context.
input bool            UseBiasFilter             = false;    // v5: tạm tắt bias để tìm combo mạnh trước
input int             StoryBiasThreshold        = 2;        // BuyScore - SellScore >= 2 => BUY, <= -2 => SELL
input int             BiasMinAlignedSignals     = 2;        // trend mode: 2 setup signals same bias are enough
input int             SidewayMinSignals         = 3;        // sideway mode: need 3 setup signals
input double          SidewayRiskMultiplier     = 0.35;     // sideway risk = RiskPercentPerOrder * this
input double          SidewayTakeProfitRR       = 5.00;     // vẫn phải đạt RR 1:5     // sideway quick target
input bool            BlockCounterTrendInTrend  = true;
input bool            RequireSidewayRangeEdge   = true;     // sideway: buy discount edge, sell premium edge
input int             StoryRangeBarsH1          = 80;
input double          StoryLevelProximity       = 2.50;     // price distance to HTF SNR considered active
input double          DOLNearFactor             = 1.25;     // DOL scoring threshold vs opposite distance


// 10 setup layers. They are still evaluated separately, then grouped.
input bool            Enable_L1_SNR_Touch       = true;
input bool            Enable_L2_SNR_Sweep       = true;
input bool            Enable_L3_SNR_MISS        = true;
input bool            Enable_L4_SNR_Engulfing   = true;
input bool            Enable_L5_GAP_SNR_LTF_Eng = true;
input bool            Enable_L6_SNR_Trendline   = true;
input bool            Enable_L7_QML_SNR         = true;
input bool            Enable_L8_QML_Engulfing   = true;
input bool            Enable_L9_Breakout_SNR_TL = true;
input bool            Enable_L10_CRT_SNR        = true;

// Confluence grouping control
input int             MinSetupsForTrade         = 2;        // sau khi có bias, chỉ cần cụm 2 tín hiệu cùng hướng bias
input bool            PreferStrongerSLZone      = true;     // gom SL theo cụm: ngoài sweep/swing/level xa nhất

//====================================================================
// TYPES
//====================================================================
enum Direction
{
   DIR_NONE = 0,
   DIR_BUY  = 1,
   DIR_SELL = -1
};

enum SetupLayer
{
   LAYER_NONE = 0,
   LAYER_01_SNR_TOUCH,
   LAYER_02_SNR_SWEEP,
   LAYER_03_SNR_MISS,
   LAYER_04_SNR_ENGULFING,
   LAYER_05_GAP_SNR_LTF_ENG,
   LAYER_06_SNR_TRENDLINE,
   LAYER_07_QML_SNR,
   LAYER_08_QML_ENGULFING,
   LAYER_09_BREAKOUT_SNR_TL,
   LAYER_10_CRT_SNR
};

struct SNRLevel
{
   double price;
   int    dir;       // +1 support, -1 resistance
   string tf;
   bool   fresh;
   bool   gap;
   bool   flip;
   int    age;
   string name;
};

struct TradeSignal
{
   bool       valid;
   Direction dir;
   SetupLayer layer;
   double     entry;
   double     sl;
   double     tp;
   double     rr;
   double     level;
   string     reason;
   string     comment;
};

struct ClusterSignal
{
   bool      valid;
   Direction dir;
   double    level;
   double    minLevel;
   double    maxLevel;
   int       layerCount;
   int       layerMask;
   bool      hasGap;
   bool      hasFresh;
   string    tfList;
   string    levelNames;
   string    reason;
   double    entry;
   double    sl;
   double    tp;
   double    rr;
   string    comment;
};

SNRLevel levels[700];
int      levelCount     = 0;
datetime lastBarTime    = 0;
int      ordersThisBar  = 0;
string   TradeSymbol    = "";

ulong    PartialTickets[2000];
bool     PartialTaken[2000];
int      PartialTicketCount = 0;

double   RangeZoneCenters[500];
datetime RangeZoneTimes[500];
int      RangeZoneCounts[500];
int      RangeZoneCount = 0;

// v5.30 Trend-Armed state
bool     TrendArmedBuy = false;
bool     TrendArmedSell = false;
datetime TrendArmedBuyTime = 0;
datetime TrendArmedSellTime = 0;
int      TrendArmedBuyBars = 0;
int      TrendArmedSellBars = 0;
int      TrendArmedBuyEntries = 0;
int      TrendArmedSellEntries = 0;
double   TrendBuyBreakLevel = 0.0;
double   TrendSellBreakLevel = 0.0;
double   TrendBuyFVG_CE = 0.0;
double   TrendSellFVG_CE = 0.0;
double   TrendBuyExtreme = 0.0;
double   TrendSellExtreme = 0.0;
int      TrendBuyMask = 0;
int      TrendSellMask = 0;

// v5.31 Safety guard state
double   GuardPeakBalance531 = 0.0;
int      GuardLossDealStreak531 = 0;
datetime GuardPauseUntil531 = 0;
string   GuardPauseReason531 = "";

// v5.32 Fixed state
double   PlusEquityScores[200];
int      PlusEquityScoreCount = 0;
int      PlusLossDealStreak = 0;
datetime PlusLockdownUntil = 0;
double   PlusCurrentRiskPercent = 0.50;
string   PlusMode = "SAFE";




// Cluster storage per bar
ClusterSignal clusters[100];
int clusterCount = 0;
double InitialBalanceForCSV = 0.0;

// Visual-only dashboard state
datetime VO_LastUpdate531 = 0;
datetime VO_LastBar531 = 0;


//====================================================================
// TEXT HELPERS
//====================================================================
string LayerName(SetupLayer layer)
{
   switch(layer)
   {
      case LAYER_01_SNR_TOUCH:       return "L1 SNR Touch";
      case LAYER_02_SNR_SWEEP:       return "L2 SNR+Sweep";
      case LAYER_03_SNR_MISS:        return "L3 SNR+MISS";
      case LAYER_04_SNR_ENGULFING:   return "L4 SNR+Engulf";
      case LAYER_05_GAP_SNR_LTF_ENG: return "L5 GAP SNR=LTF ENG";
      case LAYER_06_SNR_TRENDLINE:   return "L6 SNR+TL";
      case LAYER_07_QML_SNR:         return "L7 QML SNR";
      case LAYER_08_QML_ENGULFING:   return "L8 QML+ENG";
      case LAYER_09_BREAKOUT_SNR_TL: return "L9 BO SNR+TL";
      case LAYER_10_CRT_SNR:         return "L10 CRT+SNR";
      default:                       return "None";
   }
}

string LayerCode(SetupLayer layer)
{
   switch(layer)
   {
      case LAYER_01_SNR_TOUCH:       return "L1";
      case LAYER_02_SNR_SWEEP:       return "L2";
      case LAYER_03_SNR_MISS:        return "L3";
      case LAYER_04_SNR_ENGULFING:   return "L4";
      case LAYER_05_GAP_SNR_LTF_ENG: return "L5";
      case LAYER_06_SNR_TRENDLINE:   return "L6";
      case LAYER_07_QML_SNR:         return "L7";
      case LAYER_08_QML_ENGULFING:   return "L8";
      case LAYER_09_BREAKOUT_SNR_TL: return "L9";
      case LAYER_10_CRT_SNR:         return "L10";
      default:                       return "";
   }
}

int LayerBit(SetupLayer layer)
{
   if(layer <= LAYER_NONE) return 0;
   return (1 << ((int)layer - 1));
}

string MaskToLayerText(int mask)
{
   string txt = "";
   for(int i = 1; i <= 10; i++)
   {
      int bit = 1 << (i - 1);
      if((mask & bit) != 0)
      {
         if(txt != "") txt += "+";
         txt += "L" + IntegerToString(i);
      }
   }
   return txt;
}

int CountMaskBits(int mask)
{
   int c = 0;
   for(int i = 0; i < 10; i++)
      if((mask & (1 << i)) != 0) c++;
   return c;
}

string TFToString(ENUM_TIMEFRAMES tf)
{
   if(tf == PERIOD_M1)  return "M1";
   if(tf == PERIOD_M5)  return "M5";
   if(tf == PERIOD_M15) return "M15";
   if(tf == PERIOD_M30) return "M30";
   if(tf == PERIOD_H1)  return "H1";
   if(tf == PERIOD_H4)  return "H4";
   if(tf == PERIOD_D1)  return "D1";
   if(tf == PERIOD_W1)  return "W1";
   return IntegerToString((int)tf);
}

double NormalizePrice(double p)
{
   int digits = (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS);
   return NormalizeDouble(p, digits);
}

TradeSignal EmptySignal()
{
   TradeSignal s;
   s.valid = false;
   s.dir = DIR_NONE;
   s.layer = LAYER_NONE;
   s.entry = 0.0;
   s.sl = 0.0;
   s.tp = 0.0;
   s.rr = 0.0;
   s.level = 0.0;
   s.reason = "";
   s.comment = "";
   return s;
}

ClusterSignal EmptyCluster()
{
   ClusterSignal c;
   c.valid = false;
   c.dir = DIR_NONE;
   c.level = 0.0;
   c.minLevel = 0.0;
   c.maxLevel = 0.0;
   c.layerCount = 0;
   c.layerMask = 0;
   c.hasGap = false;
   c.hasFresh = false;
   c.tfList = "";
   c.levelNames = "";
   c.reason = "";
   c.entry = 0.0;
   c.sl = 0.0;
   c.tp = 0.0;
   c.rr = 0.0;
   c.comment = "";
   return c;
}


bool ExecutionTimeframeOK()
{
   if(!EnforceM5Only)
      return true;

   return (InpExecTF == PERIOD_M5);
}

//====================================================================
// GENERAL FILTERS
//====================================================================
bool IsNewBar()
{
   datetime t = iTime(TradeSymbol, InpExecTF, 0);
   if(t != lastBarTime)
   {
      lastBarTime = t;
      ordersThisBar = 0;
      return true;
   }
   return false;
}

bool SessionOK()
{
   if(!UseSessionFilter) return true;

   datetime now = TimeCurrent() + ServerToUTC7_OffsetHours * 3600;
   MqlDateTime dt;
   TimeToStruct(now, dt);

   int h = dt.hour;
   if(SessionStartHourUTC7 <= SessionEndHourUTC7)
      return (h >= SessionStartHourUTC7 && h < SessionEndHourUTC7);

   return (h >= SessionStartHourUTC7 || h < SessionEndHourUTC7);
}

bool SpreadOK()
{
   double ask = SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(TradeSymbol, SYMBOL_BID);
   double point = SymbolInfoDouble(TradeSymbol, SYMBOL_POINT);
   if(point <= 0.0) return false;

   double spread = (ask - bid) / point;
   return spread <= MaxSpreadPoints;
}

int CountPositions()
{
   int count = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;

      if(PositionGetString(POSITION_SYMBOL) == TradeSymbol &&
         PositionGetInteger(POSITION_MAGIC) == MagicNumber)
      {
         count++;
      }
   }

   return count;
}

bool PositionLimitOK()
{
   if(NoTotalTradeLimit) return true;
   if(MaxTotalPositions <= 0) return true;
   return CountPositions() < MaxTotalPositions;
}

//====================================================================
// MALAYSIAN SNR / GAP SNR ENGINE
//====================================================================
void AddLevel(double price, int dir, ENUM_TIMEFRAMES tf, bool fresh, bool gap, bool flip, int age, string tag)
{
   if(levelCount >= ArraySize(levels)) return;
   if(price <= 0.0) return;

   for(int i = 0; i < levelCount; i++)
   {
      if(MathAbs(levels[i].price - price) <= EqualLevelTolerance && levels[i].dir == dir)
      {
         if(fresh && !levels[i].fresh) levels[i].fresh = true;
         if(gap) levels[i].gap = true;
         return;
      }
   }

   levels[levelCount].price = NormalizePrice(price);
   levels[levelCount].dir   = dir;
   levels[levelCount].tf    = TFToString(tf);
   levels[levelCount].fresh = fresh;
   levels[levelCount].gap   = gap;
   levels[levelCount].flip  = flip;
   levels[levelCount].age   = age;
   levels[levelCount].name  = tag;
   levelCount++;
}

bool BodyBreaksLevel(ENUM_TIMEFRAMES tf, int fromShift, int toShift, double level, int dir)
{
   for(int i = fromShift; i >= toShift; i--)
   {
      double o = iOpen(TradeSymbol, tf, i);
      double c = iClose(TradeSymbol, tf, i);

      if(dir == 1 && MathMax(o, c) < level - FreshBodyBreakBuffer)
         return true;

      if(dir == -1 && MathMin(o, c) > level + FreshBodyBreakBuffer)
         return true;
   }

   return false;
}

void ScanSNRFromTF(ENUM_TIMEFRAMES tf)
{
   int totalBars = Bars(TradeSymbol, tf);
   int bars = MathMin(HTFScanBars, totalBars - 5);
   if(bars <= 10) return;

   for(int i = bars; i >= 3; i--)
   {
      double o1 = iOpen(TradeSymbol, tf, i);
      double c1 = iClose(TradeSymbol, tf, i);
      double o2 = iOpen(TradeSymbol, tf, i - 1);
      double c2 = iClose(TradeSymbol, tf, i - 1);

      bool c1Bull = c1 > o1;
      bool c1Bear = c1 < o1;
      bool c2Bull = c2 > o2;
      bool c2Bear = c2 < o2;

      // Classic A resistance: bullish candle close to next bearish candle open.
      if(c1Bull && c2Bear)
      {
         double lvl = (c1 + o2) / 2.0;
         bool fresh = !BodyBreaksLevel(tf, i - 2, 1, lvl, -1);
         AddLevel(lvl, -1, tf, fresh, false, false, i, "Classic A Resistance");
      }

      // Classic V support: bearish candle close to next bullish candle open.
      if(c1Bear && c2Bull)
      {
         double lvl = (c1 + o2) / 2.0;
         bool fresh = !BodyBreaksLevel(tf, i - 2, 1, lvl, 1);
         AddLevel(lvl, 1, tf, fresh, false, false, i, "Classic V Support");
      }

      // Bullish body GAP SNR.
      if(MathMin(o2, c2) > MathMax(o1, c1))
      {
         double lvl = (MathMin(o2, c2) + MathMax(o1, c1)) / 2.0;
         bool fresh = !BodyBreaksLevel(tf, i - 2, 1, lvl, 1);
         AddLevel(lvl, 1, tf, fresh, true, false, i, "Bullish GAP SNR");
      }

      // Bearish body GAP SNR.
      if(MathMax(o2, c2) < MathMin(o1, c1))
      {
         double lvl = (MathMax(o2, c2) + MathMin(o1, c1)) / 2.0;
         bool fresh = !BodyBreaksLevel(tf, i - 2, 1, lvl, -1);
         AddLevel(lvl, -1, tf, fresh, true, false, i, "Bearish GAP SNR");
      }
   }
}

void BuildLevels()
{
   levelCount = 0;
   ScanSNRFromTF(InpHTF_Weekly);
   ScanSNRFromTF(InpHTF_Daily);
   ScanSNRFromTF(InpHTF_H4);
   ScanSNRFromTF(InpHTF_H1);
}

//====================================================================
// PRICE ACTION SIGNALS
//====================================================================
double RecentSwingHigh(ENUM_TIMEFRAMES tf, int lookback)
{
   double maxH = -DBL_MAX;
   int bars = Bars(TradeSymbol, tf);
   int maxLookback = MathMin(lookback + 2, bars - 2);

   for(int i = 2; i <= maxLookback; i++)
      maxH = MathMax(maxH, iHigh(TradeSymbol, tf, i));

   return maxH;
}

double RecentSwingLow(ENUM_TIMEFRAMES tf, int lookback)
{
   double minL = DBL_MAX;
   int bars = Bars(TradeSymbol, tf);
   int maxLookback = MathMin(lookback + 2, bars - 2);

   for(int i = 2; i <= maxLookback; i++)
      minL = MathMin(minL, iLow(TradeSymbol, tf, i));

   return minL;
}

bool RejectionAtLevel(Direction dir, double level)
{
   double o = iOpen(TradeSymbol, InpExecTF, 1);
   double c = iClose(TradeSymbol, InpExecTF, 1);
   double h = iHigh(TradeSymbol, InpExecTF, 1);
   double l = iLow(TradeSymbol, InpExecTF, 1);

   if(dir == DIR_BUY)
      return (l <= level + TouchBufferPrice && c > level && c > o);

   if(dir == DIR_SELL)
      return (h >= level - TouchBufferPrice && c < level && c < o);

   return false;
}

bool LiquiditySweep(Direction dir)
{
   double h = iHigh(TradeSymbol, InpExecTF, 1);
   double l = iLow(TradeSymbol, InpExecTF, 1);
   double c = iClose(TradeSymbol, InpExecTF, 1);

   double sh = RecentSwingHigh(InpExecTF, 20);
   double sl = RecentSwingLow(InpExecTF, 20);

   if(dir == DIR_SELL)
      return (h > sh + SweepBufferPrice && c < sh);

   if(dir == DIR_BUY)
      return (l < sl - SweepBufferPrice && c > sl);

   return false;
}

bool BullishEngulfing()
{
   double o1 = iOpen(TradeSymbol, InpExecTF, 2);
   double c1 = iClose(TradeSymbol, InpExecTF, 2);
   double o2 = iOpen(TradeSymbol, InpExecTF, 1);
   double c2 = iClose(TradeSymbol, InpExecTF, 1);

   return (c1 < o1 && c2 > o2 && c2 >= o1 && o2 <= c1);
}

bool BearishEngulfing()
{
   double o1 = iOpen(TradeSymbol, InpExecTF, 2);
   double c1 = iClose(TradeSymbol, InpExecTF, 2);
   double o2 = iOpen(TradeSymbol, InpExecTF, 1);
   double c2 = iClose(TradeSymbol, InpExecTF, 1);

   return (c1 > o1 && c2 < o2 && c2 <= o1 && o2 >= c1);
}

bool HiddenBullishEngulfing()
{
   double h1 = iHigh(TradeSymbol, InpExecTF, 2);
   double l1 = iLow(TradeSymbol, InpExecTF, 2);
   double l2 = iLow(TradeSymbol, InpExecTF, 1);
   double o2 = iOpen(TradeSymbol, InpExecTF, 1);
   double c2 = iClose(TradeSymbol, InpExecTF, 1);

   return (c2 > o2 && l2 < l1 && c2 > (h1 + l1) / 2.0);
}

bool HiddenBearishEngulfing()
{
   double h1 = iHigh(TradeSymbol, InpExecTF, 2);
   double l1 = iLow(TradeSymbol, InpExecTF, 2);
   double h2 = iHigh(TradeSymbol, InpExecTF, 1);
   double o2 = iOpen(TradeSymbol, InpExecTF, 1);
   double c2 = iClose(TradeSymbol, InpExecTF, 1);

   return (c2 < o2 && h2 > h1 && c2 < (h1 + l1) / 2.0);
}

bool Engulfing(Direction dir)
{
   if(dir == DIR_BUY)  return BullishEngulfing() || HiddenBullishEngulfing();
   if(dir == DIR_SELL) return BearishEngulfing() || HiddenBearishEngulfing();
   return false;
}

bool MSS(Direction dir)
{
   double o = iOpen(TradeSymbol, InpExecTF, 1);
   double c = iClose(TradeSymbol, InpExecTF, 1);
   double h = iHigh(TradeSymbol, InpExecTF, 1);
   double l = iLow(TradeSymbol, InpExecTF, 1);

   double body = MathAbs(c - o);
   double range = h - l;
   bool displacement = (range > 0.0 && body / range >= 0.55);

   double sh = RecentSwingHigh(InpExecTF, 10);
   double sl = RecentSwingLow(InpExecTF, 10);

   if(dir == DIR_BUY)
      return displacement && c > sh;

   if(dir == DIR_SELL)
      return displacement && c < sl;

   return false;
}

bool MISS(Direction dir, double level)
{
   int misses = 0;

   for(int i = 2; i <= 8; i++)
   {
      double h = iHigh(TradeSymbol, InpExecTF, i);
      double l = iLow(TradeSymbol, InpExecTF, i);

      if(dir == DIR_SELL && h < level - TouchBufferPrice && MathAbs(h - level) <= TouchBufferPrice * 5.0)
         misses++;

      if(dir == DIR_BUY && l > level + TouchBufferPrice && MathAbs(l - level) <= TouchBufferPrice * 5.0)
         misses++;
   }

   return misses >= 2;
}

bool TrendlineConfluence(Direction dir, double level)
{
   int found = 0;
   double p1 = 0.0, p2 = 0.0;
   int b1 = 0, b2 = 0;

   for(int i = 5; i < 120; i++)
   {
      bool swing = true;

      if(dir == DIR_SELL)
      {
         double h = iHigh(TradeSymbol, InpExecTF, i);
         for(int k = 1; k <= SwingLookback; k++)
         {
            if(iHigh(TradeSymbol, InpExecTF, i-k) >= h || iHigh(TradeSymbol, InpExecTF, i+k) >= h)
            {
               swing = false;
               break;
            }
         }

         if(swing)
         {
            if(found == 0) { p1 = h; b1 = i; found++; }
            else { p2 = h; b2 = i; found++; break; }
         }
      }

      if(dir == DIR_BUY)
      {
         double l = iLow(TradeSymbol, InpExecTF, i);
         for(int k = 1; k <= SwingLookback; k++)
         {
            if(iLow(TradeSymbol, InpExecTF, i-k) <= l || iLow(TradeSymbol, InpExecTF, i+k) <= l)
            {
               swing = false;
               break;
            }
         }

         if(swing)
         {
            if(found == 0) { p1 = l; b1 = i; found++; }
            else { p2 = l; b2 = i; found++; break; }
         }
      }
   }

   if(found < 2 || b1 == b2) return false;

   double slope = (p1 - p2) / (b1 - b2);
   double projected = p1 + slope * (1 - b1);
   double c = iClose(TradeSymbol, InpExecTF, 1);

   return (MathAbs(projected - c) <= TouchBufferPrice * 2.0 ||
           MathAbs(projected - level) <= TouchBufferPrice * 2.0);
}

bool QMLPattern(Direction dir)
{
   int hiNearShift = iHighest(TradeSymbol, InpExecTF, MODE_HIGH, 15, 2);
   int loNearShift = iLowest(TradeSymbol, InpExecTF, MODE_LOW, 15, 2);
   int hiFarShift  = iHighest(TradeSymbol, InpExecTF, MODE_HIGH, 15, 17);
   int loFarShift  = iLowest(TradeSymbol, InpExecTF, MODE_LOW, 15, 17);

   if(hiNearShift < 0 || loNearShift < 0 || hiFarShift < 0 || loFarShift < 0)
      return false;

   double hNear = iHigh(TradeSymbol, InpExecTF, hiNearShift);
   double lNear = iLow(TradeSymbol, InpExecTF, loNearShift);
   double hFar  = iHigh(TradeSymbol, InpExecTF, hiFarShift);
   double lFar  = iLow(TradeSymbol, InpExecTF, loFarShift);
   double c     = iClose(TradeSymbol, InpExecTF, 1);

   if(dir == DIR_SELL)
      return hNear > hFar && c < lFar;

   if(dir == DIR_BUY)
      return lNear < lFar && c > hFar;

   return false;
}

bool BreakoutRetest(Direction dir, double level)
{
   for(int i = 3; i <= 25; i++)
   {
      double o = iOpen(TradeSymbol, InpExecTF, i);
      double c = iClose(TradeSymbol, InpExecTF, i);

      if(dir == DIR_BUY && o < level && c > level)
      {
         double l = iLow(TradeSymbol, InpExecTF, 1);
         if(l <= level + TouchBufferPrice && iClose(TradeSymbol, InpExecTF, 1) > level)
            return true;
      }

      if(dir == DIR_SELL && o > level && c < level)
      {
         double h = iHigh(TradeSymbol, InpExecTF, 1);
         if(h >= level - TouchBufferPrice && iClose(TradeSymbol, InpExecTF, 1) < level)
            return true;
      }
   }

   return false;
}

bool CRTSignal(Direction dir)
{
   ENUM_TIMEFRAMES tf = PERIOD_H4;
   if(Bars(TradeSymbol, tf) < 10) return false;

   double prevH = iHigh(TradeSymbol, tf, 2);
   double prevL = iLow(TradeSymbol, tf, 2);
   double h = iHigh(TradeSymbol, tf, 1);
   double l = iLow(TradeSymbol, tf, 1);
   double c = iClose(TradeSymbol, tf, 1);

   if(dir == DIR_SELL)
      return h > prevH + SweepBufferPrice && c < prevH && c > prevL;

   if(dir == DIR_BUY)
      return l < prevL - SweepBufferPrice && c > prevL && c < prevH;

   return false;
}


//====================================================================
// HTF STORYLINE BIAS - LEVEL BY LEVEL
//====================================================================
double H1RangeHigh()
{
   return RecentSwingHigh(PERIOD_H1, StoryRangeBarsH1);
}

double H1RangeLow()
{
   return RecentSwingLow(PERIOD_H1, StoryRangeBarsH1);
}

bool InDiscount()
{
   double hi = H1RangeHigh();
   double lo = H1RangeLow();
   if(hi <= lo || hi == -DBL_MAX || lo == DBL_MAX)
      return false;

   double mid = (hi + lo) / 2.0;
   double price = (SymbolInfoDouble(TradeSymbol, SYMBOL_BID) + SymbolInfoDouble(TradeSymbol, SYMBOL_ASK)) / 2.0;
   return price <= mid;
}

bool InPremium()
{
   double hi = H1RangeHigh();
   double lo = H1RangeLow();
   if(hi <= lo || hi == -DBL_MAX || lo == DBL_MAX)
      return false;

   double mid = (hi + lo) / 2.0;
   double price = (SymbolInfoDouble(TradeSymbol, SYMBOL_BID) + SymbolInfoDouble(TradeSymbol, SYMBOL_ASK)) / 2.0;
   return price >= mid;
}

bool NearRangeEdge(Direction dir)
{
   double hi = H1RangeHigh();
   double lo = H1RangeLow();
   if(hi <= lo || hi == -DBL_MAX || lo == DBL_MAX)
      return false;

   double price = (SymbolInfoDouble(TradeSymbol, SYMBOL_BID) + SymbolInfoDouble(TradeSymbol, SYMBOL_ASK)) / 2.0;
   double range = hi - lo;
   if(range <= 0.0)
      return false;

   // Buy near lower 35%, sell near upper 35%.
   if(dir == DIR_BUY)
      return price <= lo + range * 0.35;

   if(dir == DIR_SELL)
      return price >= hi - range * 0.35;

   return false;
}

bool ActiveHTFRejection(Direction dir)
{
   // Uses scanned W1/D1/H4/H1 SNR levels. Fresh + close proximity + rejection at execution TF.
   for(int i = 0; i < levelCount; i++)
   {
      if(levels[i].dir != (dir == DIR_BUY ? 1 : -1))
         continue;

      bool htf = (levels[i].tf == "W1" || levels[i].tf == "D1" || levels[i].tf == "H4");
      if(!htf)
         continue;

      double price = (SymbolInfoDouble(TradeSymbol, SYMBOL_BID) + SymbolInfoDouble(TradeSymbol, SYMBOL_ASK)) / 2.0;
      if(MathAbs(price - levels[i].price) > StoryLevelProximity)
         continue;

      if(RejectionAtLevel(dir, levels[i].price))
         return true;
   }

   return false;
}

bool FailureToDisplaceUp()
{
   // Price raids previous day high but fails to close above it.
   double pdh = iHigh(TradeSymbol, PERIOD_D1, 1);
   double h1  = iHigh(TradeSymbol, InpExecTF, 1);
   double c1  = iClose(TradeSymbol, InpExecTF, 1);
   return (pdh > 0.0 && h1 > pdh + SweepBufferPrice && c1 < pdh);
}

bool FailureToDisplaceDown()
{
   // Price raids previous day low but fails to close below it.
   double pdl = iLow(TradeSymbol, PERIOD_D1, 1);
   double l1  = iLow(TradeSymbol, InpExecTF, 1);
   double c1  = iClose(TradeSymbol, InpExecTF, 1);
   return (pdl > 0.0 && l1 < pdl - SweepBufferPrice && c1 > pdl);
}

double NearestDOLAbove(double price)
{
   double candidates[4];
   candidates[0] = iHigh(TradeSymbol, PERIOD_D1, 1);
   candidates[1] = iHigh(TradeSymbol, PERIOD_W1, 1);
   candidates[2] = RecentSwingHigh(InpExecTF, 80);
   candidates[3] = RecentSwingHigh(PERIOD_H1, 40);

   double best = DBL_MAX;
   for(int i = 0; i < 4; i++)
      if(candidates[i] > price && candidates[i] < best)
         best = candidates[i];

   return best == DBL_MAX ? 0.0 : best;
}

double NearestDOLBelow(double price)
{
   double candidates[4];
   candidates[0] = iLow(TradeSymbol, PERIOD_D1, 1);
   candidates[1] = iLow(TradeSymbol, PERIOD_W1, 1);
   candidates[2] = RecentSwingLow(InpExecTF, 80);
   candidates[3] = RecentSwingLow(PERIOD_H1, 40);

   double best = -DBL_MAX;
   for(int i = 0; i < 4; i++)
      if(candidates[i] < price && candidates[i] > best)
         best = candidates[i];

   return best == -DBL_MAX ? 0.0 : best;
}

void StorylineScores(int &buyScore, int &sellScore)
{
   buyScore = 0;
   sellScore = 0;

   double price = (SymbolInfoDouble(TradeSymbol, SYMBOL_BID) + SymbolInfoDouble(TradeSymbol, SYMBOL_ASK)) / 2.0;

   // 1. HTF SNR rejection.
   if(ActiveHTFRejection(DIR_BUY))  buyScore += 2;
   if(ActiveHTFRejection(DIR_SELL)) sellScore += 2;

   // 2. Failure to displace over old high/low.
   if(FailureToDisplaceDown()) buyScore += 2;
   if(FailureToDisplaceUp())   sellScore += 2;

   // 3. Premium / Discount.
   if(InDiscount()) buyScore += 1;
   if(InPremium())  sellScore += 1;

   // 4. DOL clarity.
   double above = NearestDOLAbove(price);
   double below = NearestDOLBelow(price);
   double distUp = above > price ? above - price : 0.0;
   double distDn = below > 0.0 && below < price ? price - below : 0.0;

   if(distUp > 0.0 && (distDn <= 0.0 || distUp <= distDn * DOLNearFactor))
      buyScore += 1;

   if(distDn > 0.0 && (distUp <= 0.0 || distDn <= distUp * DOLNearFactor))
      sellScore += 1;

   // 5. CRT purge confirmation.
   if(CRTSignal(DIR_BUY))  buyScore += 1;
   if(CRTSignal(DIR_SELL)) sellScore += 1;
}

Direction MarketBias()
{
   if(!UseBiasFilter)
      return DIR_NONE;

   int buyScore = 0;
   int sellScore = 0;
   StorylineScores(buyScore, sellScore);

   if(buyScore - sellScore >= StoryBiasThreshold)
      return DIR_BUY;

   if(sellScore - buyScore >= StoryBiasThreshold)
      return DIR_SELL;

   return DIR_NONE; // sideway / unclear
}

string BiasText()
{
   Direction b = MarketBias();

   int buyScore = 0;
   int sellScore = 0;
   StorylineScores(buyScore, sellScore);

   string scoreText = StringFormat("B%d/S%d", buyScore, sellScore);

   if(b == DIR_BUY)  return "STORY_BUY_" + scoreText;
   if(b == DIR_SELL) return "STORY_SELL_" + scoreText;
   return "STORY_SIDEWAY_" + scoreText;
}

double EffectiveRiskPercent()
{
   if(!UseBiasFilter)
      return RiskPercentPerOrder;

   Direction b = MarketBias();
   if(b == DIR_NONE)
      return RiskPercentPerOrder * SidewayRiskMultiplier;

   return RiskPercentPerOrder;
}

//====================================================================
// DOL / TP / SL
//====================================================================
double PreviousDayHigh(){ return iHigh(TradeSymbol, PERIOD_D1, 1); }
double PreviousDayLow(){ return iLow(TradeSymbol, PERIOD_D1, 1); }
double PreviousWeekHigh(){ return iHigh(TradeSymbol, PERIOD_W1, 1); }
double PreviousWeekLow(){ return iLow(TradeSymbol, PERIOD_W1, 1); }

double FindDOLTarget(Direction dir, double entry)
{
   double candidates[8];
   ArrayInitialize(candidates, 0.0);

   candidates[0] = PreviousDayHigh();
   candidates[1] = PreviousDayLow();
   candidates[2] = PreviousWeekHigh();
   candidates[3] = PreviousWeekLow();
   candidates[4] = RecentSwingHigh(InpExecTF, 80);
   candidates[5] = RecentSwingLow(InpExecTF, 80);
   candidates[6] = RecentSwingHigh(PERIOD_H1, 40);
   candidates[7] = RecentSwingLow(PERIOD_H1, 40);

   double best = 0.0;

   if(dir == DIR_BUY)
   {
      double minAbove = DBL_MAX;
      for(int i = 0; i < 8; i++)
      {
         if(candidates[i] > entry && candidates[i] < minAbove)
         {
            minAbove = candidates[i];
            best = candidates[i];
         }
      }
   }

   if(dir == DIR_SELL)
   {
      double maxBelow = -DBL_MAX;
      for(int i = 0; i < 8; i++)
      {
         if(candidates[i] < entry && candidates[i] > maxBelow)
         {
            maxBelow = candidates[i];
            best = candidates[i];
         }
      }
   }

   return best;
}


double EffectiveMaxSL520()
{
   if(IsExpansion520() && ExpansionMaxSLPrice > MaxSLPrice)
      return ExpansionMaxSLPrice;

   return MaxSLPrice;
}

void BuildClusterTradePrices(ClusterSignal &sig)
{
   double ask = SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(TradeSymbol, SYMBOL_BID);
   sig.entry = (sig.dir == DIR_BUY ? ask : bid);

   double recentLow  = RecentSwingLow(InpExecTF, RecentSwingBars);
   double recentHigh = RecentSwingHigh(InpExecTF, RecentSwingBars);

   double prevLow = iLow(TradeSymbol, InpExecTF, 1);
   double prevHigh = iHigh(TradeSymbol, InpExecTF, 1);

   if(sig.dir == DIR_BUY)
   {
      double sl1 = sig.minLevel - SL_BufferPrice;
      double sl2 = recentLow - SL_BufferPrice;
      double sl3 = prevLow - SL_BufferPrice;

      sig.sl = -DBL_MAX;
      if(sl1 < sig.entry) sig.sl = MathMax(sig.sl, sl1);
      if(sl2 < sig.entry) sig.sl = MathMax(sig.sl, sl2);
      if(sl3 < sig.entry) sig.sl = MathMax(sig.sl, sl3);

      if(sig.sl == -DBL_MAX)
      {
         sig.valid = false;
         return;
      }

      double dist = sig.entry - sig.sl;
      if(dist < MinSLPrice)
         sig.sl = sig.entry - MinSLPrice;

      dist = sig.entry - sig.sl;
      if(dist > EffectiveMaxSL520())
      {
         sig.valid = false;
         return;
      }

      double risk = sig.entry - sig.sl;

      if(UseFixedFullTP_RR)
      {
         sig.tp = sig.entry + risk * FixedFullTP_RR;
         sig.rr = FixedFullTP_RR;
      }
      else
      {
         // Document TP mode:
         // 1) DOL first: PDH/PWH/swing/session liquidity from FindDOLTarget().
         // 2) If DOL unavailable or too near, use STD/RR projection as fallback.
         double dol = UseDOLTargetFirst ? FindDOLTarget(DIR_BUY, sig.entry) : 0.0;
         bool dolOK = (dol > sig.entry && (dol - sig.entry) / risk >= MinDocumentTP_RR);

         if(dolOK)
         {
            sig.tp = dol;
            sig.rr = (sig.tp - sig.entry) / risk;
         }
         else
         {
            if(RequireDOLToMeetMinRR && !AllowSTDTargetIfNoDOL)
            {
               sig.valid = false;
               return;
            }

            double fallbackRR = MathMax(MinDocumentTP_RR, MathMax(FallbackRR, STD_Projection_Multiple));
            sig.tp = sig.entry + risk * fallbackRR;
            sig.rr = fallbackRR;
         }
      }
   }

   if(sig.dir == DIR_SELL)
   {
      double sl1 = sig.maxLevel + SL_BufferPrice;
      double sl2 = recentHigh + SL_BufferPrice;
      double sl3 = prevHigh + SL_BufferPrice;

      sig.sl = DBL_MAX;
      if(sl1 > sig.entry) sig.sl = MathMin(sig.sl, sl1);
      if(sl2 > sig.entry) sig.sl = MathMin(sig.sl, sl2);
      if(sl3 > sig.entry) sig.sl = MathMin(sig.sl, sl3);

      if(sig.sl == DBL_MAX)
      {
         sig.valid = false;
         return;
      }

      double dist = sig.sl - sig.entry;
      if(dist < MinSLPrice)
         sig.sl = sig.entry + MinSLPrice;

      dist = sig.sl - sig.entry;
      if(dist > EffectiveMaxSL520())
      {
         sig.valid = false;
         return;
      }

      double risk = sig.sl - sig.entry;

      if(UseFixedFullTP_RR)
      {
         sig.tp = sig.entry - risk * FixedFullTP_RR;
         sig.rr = FixedFullTP_RR;
      }
      else
      {
         double dol = UseDOLTargetFirst ? FindDOLTarget(DIR_SELL, sig.entry) : 0.0;
         bool dolOK = (dol < sig.entry && dol > 0.0 && (sig.entry - dol) / risk >= MinDocumentTP_RR);

         if(dolOK)
         {
            sig.tp = dol;
            sig.rr = (sig.entry - sig.tp) / risk;
         }
         else
         {
            if(RequireDOLToMeetMinRR && !AllowSTDTargetIfNoDOL)
            {
               sig.valid = false;
               return;
            }

            double fallbackRR = MathMax(MinDocumentTP_RR, MathMax(FallbackRR, STD_Projection_Multiple));
            sig.tp = sig.entry - risk * fallbackRR;
            sig.rr = fallbackRR;
         }
      }
   }

   sig.entry = NormalizePrice(sig.entry);
   sig.sl    = NormalizePrice(sig.sl);
   sig.tp    = NormalizePrice(sig.tp);
}

//====================================================================
// LAYER CONDITIONS
//====================================================================
bool LayerTriggered(SNRLevel &lvl, SetupLayer layer, Direction dir)
{
   bool touch = RejectionAtLevel(dir, lvl.price);
   bool sweep = LiquiditySweep(dir);
   bool miss = MISS(dir, lvl.price);
   bool engulf = Engulfing(dir);
   bool tl = TrendlineConfluence(dir, lvl.price);
   bool qml = QMLPattern(dir);
   bool br = BreakoutRetest(dir, lvl.price);
   bool crt = CRTSignal(dir);

   switch(layer)
   {
      case LAYER_01_SNR_TOUCH:
         return Enable_L1_SNR_Touch && touch;

      case LAYER_02_SNR_SWEEP:
         return Enable_L2_SNR_Sweep && touch && sweep;

      case LAYER_03_SNR_MISS:
         return Enable_L3_SNR_MISS && touch && miss;

      case LAYER_04_SNR_ENGULFING:
         return Enable_L4_SNR_Engulfing && touch && engulf;

      case LAYER_05_GAP_SNR_LTF_ENG:
         return Enable_L5_GAP_SNR_LTF_Eng && lvl.gap && touch && engulf;

      case LAYER_06_SNR_TRENDLINE:
         return Enable_L6_SNR_Trendline && touch && tl;

      case LAYER_07_QML_SNR:
         return Enable_L7_QML_SNR && touch && qml;

      case LAYER_08_QML_ENGULFING:
         return Enable_L8_QML_Engulfing && touch && qml && engulf;

      case LAYER_09_BREAKOUT_SNR_TL:
         return Enable_L9_Breakout_SNR_TL && br && tl;

      case LAYER_10_CRT_SNR:
         return Enable_L10_CRT_SNR && touch && crt;

      default:
         return false;
   }
}

string LayerReason(SetupLayer layer)
{
   switch(layer)
   {
      case LAYER_01_SNR_TOUCH:       return "touch";
      case LAYER_02_SNR_SWEEP:       return "sweep";
      case LAYER_03_SNR_MISS:        return "MISS";
      case LAYER_04_SNR_ENGULFING:   return "engulf";
      case LAYER_05_GAP_SNR_LTF_ENG: return "gap+eng";
      case LAYER_06_SNR_TRENDLINE:   return "TL";
      case LAYER_07_QML_SNR:         return "QML";
      case LAYER_08_QML_ENGULFING:   return "QML+eng";
      case LAYER_09_BREAKOUT_SNR_TL: return "BO+TL";
      case LAYER_10_CRT_SNR:         return "CRT";
      default:                       return "";
   }
}

//====================================================================
// CONFLUENCE CLUSTERING
//====================================================================
bool SameCluster(ClusterSignal &c, SNRLevel &lvl, Direction dir)
{
   if(!c.valid) return false;
   if(c.dir != dir) return false;
   return MathAbs(c.level - lvl.price) <= ConfluenceZonePrice;
}

void AddToCluster(SNRLevel &lvl, SetupLayer layer, Direction dir)
{
   int bit = LayerBit(layer);

   // if same layer already exists in same zone, no need to duplicate it
   for(int i = 0; i < clusterCount; i++)
   {
      if(SameCluster(clusters[i], lvl, dir))
      {
         clusters[i].minLevel = MathMin(clusters[i].minLevel, lvl.price);
         clusters[i].maxLevel = MathMax(clusters[i].maxLevel, lvl.price);

         if((clusters[i].layerMask & bit) == 0)
         {
            clusters[i].layerMask |= bit;
            clusters[i].layerCount = CountMaskBits(clusters[i].layerMask);
            if(clusters[i].reason != "") clusters[i].reason += "+";
            clusters[i].reason += LayerReason(layer);
         }

         if(lvl.gap) clusters[i].hasGap = true;
         if(lvl.fresh) clusters[i].hasFresh = true;

         if(StringFind(clusters[i].tfList, lvl.tf) < 0)
            clusters[i].tfList += (clusters[i].tfList == "" ? "" : "/") + lvl.tf;

         if(StringFind(clusters[i].levelNames, lvl.name) < 0)
            clusters[i].levelNames += (clusters[i].levelNames == "" ? "" : ",") + lvl.name;

         // update representative level toward weighted average
         clusters[i].level = NormalizePrice((clusters[i].level + lvl.price) / 2.0);
         return;
      }
   }

   if(clusterCount >= ArraySize(clusters)) return;

   clusters[clusterCount] = EmptyCluster();
   clusters[clusterCount].valid = true;
   clusters[clusterCount].dir = dir;
   clusters[clusterCount].level = lvl.price;
   clusters[clusterCount].minLevel = lvl.price;
   clusters[clusterCount].maxLevel = lvl.price;
   clusters[clusterCount].layerMask = bit;
   clusters[clusterCount].layerCount = 1;
   clusters[clusterCount].hasGap = lvl.gap;
   clusters[clusterCount].hasFresh = lvl.fresh;
   clusters[clusterCount].tfList = lvl.tf;
   clusters[clusterCount].levelNames = lvl.name;
   clusters[clusterCount].reason = LayerReason(layer);
   clusterCount++;
}

void BuildConfluenceClusters()
{
   clusterCount = 0;

   SetupLayer layerList[10] =
   {
      LAYER_01_SNR_TOUCH,
      LAYER_02_SNR_SWEEP,
      LAYER_03_SNR_MISS,
      LAYER_04_SNR_ENGULFING,
      LAYER_05_GAP_SNR_LTF_ENG,
      LAYER_06_SNR_TRENDLINE,
      LAYER_07_QML_SNR,
      LAYER_08_QML_ENGULFING,
      LAYER_09_BREAKOUT_SNR_TL,
      LAYER_10_CRT_SNR
   };

   for(int l = 0; l < 10; l++)
   {
      SetupLayer layer = layerList[l];

      for(int i = 0; i < levelCount; i++)
      {
         Direction dir = (levels[i].dir == 1 ? DIR_BUY : DIR_SELL);

         if(LayerTriggered(levels[i], layer, dir))
            AddToCluster(levels[i], layer, dir);
      }
   }
}


//====================================================================
// EXACT BEST COMBO ENGINE v5.11
//====================================================================
bool MaskContains(int fullMask, int comboMask)
{
   if(WhitelistAllowSuperset)
      return (fullMask & comboMask) == comboMask;

   return fullMask == comboMask;
}

bool ComboEnabledAndMatched(int fullMask, int comboMask, bool enabled)
{
   return enabled && MaskContains(fullMask, comboMask);
}

string WhitelistComboTag(int fullMask)
{
   // Bit mapping:
   // L1=1, L2=2, L3=4, L4=8, L5=16, L6=32, L7=64, L8=128, L9=256, L10=512

   if(Enable_CORE)
   {
      if(ComboEnabledAndMatched(fullMask, 297, WL_CORE_L1_L4_L6_L9))
         return "WL_CORE_L1+L4+L6+L9";

      if(ComboEnabledAndMatched(fullMask, 301, WL_CORE_L1_L3_L4_L6_L9))
         return "WL_CORE_L1+L3+L4+L6+L9";
   }

   if(Enable_WATCH)
   {
      if(ComboEnabledAndMatched(fullMask, 313, WL_WATCH_L1_L4_L5_L6_L9))
         return "WL_WATCH_L1+L4+L5+L6+L9";

      if(ComboEnabledAndMatched(fullMask, 59, WL_WATCH_L1_L2_L4_L5_L6))
         return "WL_WATCH_L1+L2+L4+L5+L6";

      if(ComboEnabledAndMatched(fullMask, 63, WL_WATCH_L1_L2_L3_L4_L5_L6))
         return "WL_WATCH_L1+L2+L3+L4+L5+L6";
   }

   if(Enable_EXTRA)
   {
      if(ComboEnabledAndMatched(fullMask, 809, WL_EXTRA_L1_L4_L6_L9_L10))
         return "WL_EXTRA_L1+L4+L6+L9+L10";
   }

   return "";
}

bool WhitelistComboAllowed(int fullMask)
{
   if(!UseWhitelistCombos)
      return true;

   return WhitelistComboTag(fullMask) != "";
}


//====================================================================
// v5.20 5/10 WHITELIST + RANGE LOCK + EXPANSION
//====================================================================
bool MaskMatch520(int fullMask, int comboMask)
{
   if(WhitelistAllowSuperset)
      return (fullMask & comboMask) == comboMask;

   return fullMask == comboMask;
}

string WhitelistTag520(int layerMask)
{
   // Bit mapping:
   // L1=1, L2=2, L3=4, L4=8, L5=16, L6=32, L7=64, L8=128, L9=256, L10=512
   if(WL_L1_L4_L5_L7_L8  && MaskMatch520(layerMask, 217)) return "WL_L1+L4+L5+L7+L8";
   if(WL_L1_L3_L4_L5_L10 && MaskMatch520(layerMask, 541)) return "WL_L1+L3+L4+L5+L10";
   if(WL_L1_L3_L4_L6_L9  && MaskMatch520(layerMask, 301)) return "WL_L1+L3+L4+L6+L9";
   if(WL_L1_L3_L6_L9_L10 && MaskMatch520(layerMask, 805)) return "WL_L1+L3+L6+L9+L10";
   if(WL_L1_L2_L3_L4_L10 && MaskMatch520(layerMask, 527)) return "WL_L1+L2+L3+L4+L10";
   if(WL_L1_L3_L4_L7_L8  && MaskMatch520(layerMask, 205)) return "WL_L1+L3+L4+L7+L8";

   return "";
}

double RecentRangeHigh520(int bars)
{
   double hi = -DBL_MAX;
   for(int i = 1; i <= bars; i++)
      hi = MathMax(hi, iHigh(TradeSymbol, InpExecTF, i));
   return hi;
}

double RecentRangeLow520(int bars)
{
   double lo = DBL_MAX;
   for(int i = 1; i <= bars; i++)
      lo = MathMin(lo, iLow(TradeSymbol, InpExecTF, i));
   return lo;
}

bool IsNarrowRange520()
{
   double hi = RecentRangeHigh520(RangeLookbackBars);
   double lo = RecentRangeLow520(RangeLookbackBars);
   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return false;

   return (hi - lo) <= RangeWidthMaxPrice;
}

double CurrentRangeCenter520()
{
   double hi = RecentRangeHigh520(RangeLookbackBars);
   double lo = RecentRangeLow520(RangeLookbackBars);
   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return 0.0;
   return (hi + lo) / 2.0;
}

bool RangeSpamAllowed520()
{
   if(!UseRangeSpamLock)
      return true;

   if(!IsNarrowRange520())
      return true;

   double center = CurrentRangeCenter520();
   if(center <= 0.0)
      return true;

   datetime now = TimeCurrent();
   int barsSeconds = PeriodSeconds(InpExecTF);
   int maxAge = MathMax(1, RangeLookbackBars) * barsSeconds;

   for(int i = 0; i < RangeZoneCount; i++)
   {
      if((now - RangeZoneTimes[i]) > maxAge)
         continue;

      if(MathAbs(center - RangeZoneCenters[i]) <= RangeZoneMergePrice)
      {
         if(RangeZoneCounts[i] >= MaxTradesPerRangeZone)
            return false;

         RangeZoneCounts[i]++;
         RangeZoneTimes[i] = now;
         return true;
      }
   }

   if(RangeZoneCount < ArraySize(RangeZoneCenters))
   {
      RangeZoneCenters[RangeZoneCount] = center;
      RangeZoneTimes[RangeZoneCount] = now;
      RangeZoneCounts[RangeZoneCount] = 1;
      RangeZoneCount++;
   }

   return true;
}

bool IsExpansion520()
{
   if(!UseExpansionBoost)
      return false;

   double hi = RecentRangeHigh520(ExpansionLookbackBars);
   double lo = RecentRangeLow520(ExpansionLookbackBars);
   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return false;

   double range = hi - lo;
   double body = MathAbs(iClose(TradeSymbol, InpExecTF, 1) - iOpen(TradeSymbol, InpExecTF, 1));

   bool rangeOK = range >= ExpansionRangeMinPrice;
   bool candleOK = body >= ExpansionCandleBodyMin;

   return (rangeOK || candleOK);
}


//====================================================================
// v5.22 PREMIUM/DISCOUNT + RANGE BREAK-HOLD
//====================================================================
double TFRangeHigh522(ENUM_TIMEFRAMES tf, int bars, int startShift=1)
{
   double hi = -DBL_MAX;
   for(int i = startShift; i < startShift + bars; i++)
      hi = MathMax(hi, iHigh(TradeSymbol, tf, i));
   return hi;
}

double TFRangeLow522(ENUM_TIMEFRAMES tf, int bars, int startShift=1)
{
   double lo = DBL_MAX;
   for(int i = startShift; i < startShift + bars; i++)
      lo = MathMin(lo, iLow(TradeSymbol, tf, i));
   return lo;
}

double PDPosition522()
{
   double hi = TFRangeHigh522(PDRangeTF, PDLookbackBars, 1);
   double lo = TFRangeLow522(PDRangeTF, PDLookbackBars, 1);

   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return 0.50;

   double price = (SymbolInfoDouble(TradeSymbol, SYMBOL_BID) + SymbolInfoDouble(TradeSymbol, SYMBOL_ASK)) / 2.0;
   double pos = (price - lo) / (hi - lo);

   if(pos < 0.0) pos = 0.0;
   if(pos > 1.0) pos = 1.0;

   return pos;
}

bool RangeEdgeTradeOK522(Direction dir)
{
   double hi = RecentRangeHigh520(RangeLookbackBars);
   double lo = RecentRangeLow520(RangeLookbackBars);

   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return true;

   double price = (SymbolInfoDouble(TradeSymbol, SYMBOL_BID) + SymbolInfoDouble(TradeSymbol, SYMBOL_ASK)) / 2.0;
   double width = hi - lo;

   if(dir == DIR_BUY)
      return price <= lo + width * RangeEdgeTradePercent;

   if(dir == DIR_SELL)
      return price >= hi - width * RangeEdgeTradePercent;

   return false;
}

double PriorRangeHigh522()
{
   // exclude the latest completed candle; use older range to detect true break
   return TFRangeHigh522(InpExecTF, BreakHoldLookbackBars, 2);
}

double PriorRangeLow522()
{
   return TFRangeLow522(InpExecTF, BreakHoldLookbackBars, 2);
}

bool BreakHoldNow522(Direction dir)
{
   if(!UseRangeBreakHold)
      return false;

   double hi = PriorRangeHigh522();
   double lo = PriorRangeLow522();

   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return false;

   double c1 = iClose(TradeSymbol, InpExecTF, 1);
   double o1 = iOpen(TradeSymbol, InpExecTF, 1);
   double h1 = iHigh(TradeSymbol, InpExecTF, 1);
   double l1 = iLow(TradeSymbol, InpExecTF, 1);
   double body = MathAbs(c1 - o1);

   if(body < BreakHoldBodyMinPrice)
      return false;

   if(dir == DIR_BUY)
   {
      // breakout close above prior range, candle holds mostly outside
      return (c1 > hi + BreakHoldBufferPrice && l1 >= hi - BreakHoldBufferPrice);
   }

   if(dir == DIR_SELL)
   {
      // breakout close below prior range, candle holds mostly outside
      return (c1 < lo - BreakHoldBufferPrice && h1 <= lo + BreakHoldBufferPrice);
   }

   return false;
}

bool BreakHoldRecently522(Direction dir)
{
   if(!UseRangeBreakHold)
      return false;

   double hi = PriorRangeHigh522();
   double lo = PriorRangeLow522();

   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return false;

   int validBars = MathMax(1, BreakHoldValidBars);

   for(int s = 1; s <= validBars; s++)
   {
      double c = iClose(TradeSymbol, InpExecTF, s);
      double o = iOpen(TradeSymbol, InpExecTF, s);
      double h = iHigh(TradeSymbol, InpExecTF, s);
      double l = iLow(TradeSymbol, InpExecTF, s);
      double body = MathAbs(c - o);

      if(body < BreakHoldBodyMinPrice)
         continue;

      if(dir == DIR_BUY && c > hi + BreakHoldBufferPrice && l >= hi - BreakHoldBufferPrice)
         return true;

      if(dir == DIR_SELL && c < lo - BreakHoldBufferPrice && h <= lo + BreakHoldBufferPrice)
         return true;
   }

   return false;
}

bool PremiumDiscountOK522(Direction dir)
{
   if(!UsePremiumDiscountFilter)
      return true;

   bool breakHold = BreakHoldRecently522(dir);
   if(AllowBreakHoldOverridePD && breakHold)
      return true;

   double pos = PDPosition522();

   if(dir == DIR_BUY)
      return pos <= BuyMaxPDPosition;

   if(dir == DIR_SELL)
      return pos >= SellMinPDPosition;

   return false;
}

string MarketStateTag522(Direction dir)
{
   if(BreakHoldRecently522(dir))
      return "BREAK_HOLD";

   if(IsExpansion520())
      return "EXPANSION";

   if(IsNarrowRange520())
      return "RANGE_LOCK";

   return "NORMAL";
}


bool BlockedWorstNormalBuy520(string stateTag, Direction dir, int layerMask)
{
   if(!BlockWorstNormalBuySetups)
      return false;

   if(stateTag != "NORMAL" || dir != DIR_BUY)
      return false;

   // Worst groups from longer test:
   // L1+L4+L5+L7+L8 = 217
   // L1+L3+L4+L5+L10 = 541
   // L1+L3+L6+L9+L10 = 805
   // L1+L3+L4+L6+L9 = 301
   if(layerMask == 217) return true;
   if(layerMask == 541) return true;
   if(layerMask == 805) return true;
   if(layerMask == 301) return true;

   return false;
}


//====================================================================
// v5.24 DUAL MODE HELPERS: REACTION + MOMENTUM
//====================================================================
int MomentumMask522(Direction dir)
{
   int mask = 0;

   // M1: break prior range high/low.
   double hi = TFRangeHigh522(InpExecTF, MomentumRangeBars, 2);
   double lo = TFRangeLow522(InpExecTF, MomentumRangeBars, 2);
   double c1 = iClose(TradeSymbol, InpExecTF, 1);
   double h1 = iHigh(TradeSymbol, InpExecTF, 1);
   double l1 = iLow(TradeSymbol, InpExecTF, 1);
   double o1 = iOpen(TradeSymbol, InpExecTF, 1);
   double body = MathAbs(c1 - o1);

   if(hi != -DBL_MAX && lo != DBL_MAX && hi > lo)
   {
      if(dir == DIR_BUY && c1 > hi + MomentumBreakBuffer)
         mask |= 1;   // M1 break range high

      if(dir == DIR_SELL && c1 < lo - MomentumBreakBuffer)
         mask |= 1;   // M1 break range low
   }

   // M2: displacement candle.
   if(body >= MomentumBodyMin)
      mask |= 2;

   // M3: FVG / imbalance approximation on M5:
   // Bullish FVG: low[1] > high[3]
   // Bearish FVG: high[1] < low[3]
   double h3 = iHigh(TradeSymbol, InpExecTF, 3);
   double l3 = iLow(TradeSymbol, InpExecTF, 3);
   bool bullFVG = (l1 > h3);
   bool bearFVG = (h1 < l3);

   if(dir == DIR_BUY && bullFVG)
      mask |= 4;
   if(dir == DIR_SELL && bearFVG)
      mask |= 4;

   // M4: pullback/hold outside old range. Optional but useful.
   if(hi != -DBL_MAX && lo != DBL_MAX && hi > lo)
   {
      bool holdBuy = (dir == DIR_BUY && l1 >= hi - MomentumBreakBuffer);
      bool holdSell = (dir == DIR_SELL && h1 <= lo + MomentumBreakBuffer);

      if(holdBuy || holdSell)
         mask |= 8;
   }

   // M5: H1 move / directional pressure.
   double h1now = iClose(TradeSymbol, PERIOD_H1, 1);
   double h1past = iClose(TradeSymbol, PERIOD_H1, MomentumHTFBars);
   if(h1past > 0.0)
   {
      double move = h1now - h1past;
      if(dir == DIR_BUY && move >= MomentumHTFMoveMin)
         mask |= 16;
      if(dir == DIR_SELL && move <= -MomentumHTFMoveMin)
         mask |= 16;
   }

   return mask;
}

int CountSmallBits(int mask)
{
   int cnt = 0;
   for(int i = 0; i < 10; i++)
      if((mask & (1 << i)) != 0)
         cnt++;
   return cnt;
}

string MomentumMaskText522(int mask)
{
   string s = "";
   if((mask & 1)  != 0) s += (s == "" ? "M1" : "+M1");
   if((mask & 2)  != 0) s += (s == "" ? "M2" : "+M2");
   if((mask & 4)  != 0) s += (s == "" ? "M3" : "+M3");
   if((mask & 8)  != 0) s += (s == "" ? "M4" : "+M4");
   if((mask & 16) != 0) s += (s == "" ? "M5" : "+M5");
   return s;
}

bool MomentumAllowed522(Direction dir, string stateTag, int &momMask, string &momText)
{
   if(!EnableMomentumLane)
      return false;

   if(MomentumOnlyInExpansion && stateTag != "EXPANSION" && stateTag != "BREAK_HOLD")
      return false;

   momMask = MomentumMask522(dir);
   int cnt = CountSmallBits(momMask);

   if(MomentumNeedFVG && (momMask & 4) == 0)
      return false;

   if(MomentumNeedPullbackHold && (momMask & 8) == 0)
      return false;

   if(cnt < MomentumMinConfirmations)
      return false;

   momText = MomentumMaskText522(momMask);
   return true;
}

bool ReactionAllowed522(int layerCount, string wlTag)
{
   if(!EnableReactionLane)
      return false;

   if(layerCount < ReactionMinConfirmations)
      return false;

   if(Use5of10Whitelist && wlTag == "")
      return false;

   return true;
}


//====================================================================
// v5.27 MOMENTUM FILTER HELPERS
//====================================================================
bool HasM1M2M3(int momMask)
{
   return ((momMask & 1) != 0 && (momMask & 2) != 0 && (momMask & 4) != 0);
}

bool HasM1M2M3M5(int momMask)
{
   return (HasM1M2M3(momMask) && (momMask & 16) != 0);
}

bool MomentumLayerAllowed527(Direction dir, string stateTag, int layerMask, int momMask)
{
   if(!UseMomentumFilterV527)
      return true;

   // L1+L10 was catastrophic in v5.25, especially in EXPANSION.
   if(MomBlockL1L10Everywhere && layerMask == 513)
      return false;

   // EXPANSION momentum needs HTF pressure: M1+M2+M3+M5.
   if(stateTag == "EXPANSION")
   {
      if(MomExpansionRequireM123M5 && !HasM1M2M3M5(momMask))
         return false;

      // Block known bad expansion groups.
      if(MomBlockBadExpansionGroups)
      {
         if(dir == DIR_BUY  && layerMask == 193) return false; // BUY L1+L7
         if(dir == DIR_SELL && layerMask == 1)   return false; // SELL L1
         if(dir == DIR_BUY  && layerMask == 705) return false; // BUY L1+L7+L10
      }

      // Keep proven groups from v5.25.
      if(dir == DIR_SELL && MOM_EXP_SELL_L1_L7_L10 && layerMask == 705) return true; // L1+L7+L10
      if(dir == DIR_SELL && MOM_EXP_SELL_L1_L7     && layerMask == 193) return true; // L1+L7
      if(dir == DIR_BUY  && MOM_EXP_BUY_L1_L6      && layerMask == 33)  return true; // L1+L6
      if(dir == DIR_BUY  && MOM_EXP_BUY_L1_L6_L9   && layerMask == 289) return true; // L1+L6+L9
      if(dir == DIR_BUY  && MOM_EXP_BUY_L1         && layerMask == 1)   return true; // L1

      return false;
   }

   // BREAK_HOLD: allow more, but require real break + displacement + FVG.
   if(stateTag == "BREAK_HOLD")
   {
      if(MomBreakHoldRequireM123 && !HasM1M2M3(momMask))
         return false;

      if(MOM_BH_ALLOW_ANY_M123M5 && HasM1M2M3M5(momMask))
         return true;

      if(MOM_BH_ALLOW_L1     && layerMask == 1)   return true;
      if(MOM_BH_ALLOW_L1_L7  && layerMask == 193) return true;
      if(MOM_BH_ALLOW_L1_L10 && layerMask == 513) return true;

      // If it is an already strong reaction whitelist layer, allow momentum break-hold too.
      if(layerMask == 217 || layerMask == 729 || layerMask == 805 || layerMask == 543)
         return true;

      return false;
   }

   // Momentum should not operate in NORMAL/RANGE_LOCK except when stateTag is BREAK_HOLD.
   return false;
}

string MomentumFilterTag527(Direction dir, string stateTag, int layerMask, int momMask)
{
   string side = (dir == DIR_BUY ? "B" : "S");
   string state = stateTag;
   string mom = MomentumMaskText522(momMask);
   return "M527_" + side + "_" + state + "_" + IntegerToString(layerMask) + "_" + mom;
}


//====================================================================
// v5.29 STRONG TREND / RUNAWAY BREAKOUT LANE
//====================================================================
double CloseMove529(ENUM_TIMEFRAMES tf, int bars)
{
   double c1 = iClose(TradeSymbol, tf, 1);
   double cN = iClose(TradeSymbol, tf, bars);
   if(c1 <= 0.0 || cN <= 0.0)
      return 0.0;
   return c1 - cN;
}

bool HTFAlign529(Direction dir)
{
   if(!ST_RequireD1H4Align)
      return true;

   double d1 = CloseMove529(PERIOD_D1, ST_D1Bars);
   double h4 = CloseMove529(PERIOD_H4, ST_H4Bars);

   if(dir == DIR_BUY)
      return (d1 >= ST_D1MoveMin && h4 >= ST_H4MoveMin);

   if(dir == DIR_SELL)
   {
      if(ST_SellOnlyInHTFDown)
         return (d1 <= -ST_D1MoveMin && h4 <= -ST_H4MoveMin);

      // Gold often gives profitable SELL corrections even in weekly/D1 uptrend.
      // So allow SELL when H4 pressure is down OR when D1 is stretched enough.
      return (h4 <= -ST_H4MoveMin || d1 >= ST_D1MoveMin);
   }

   return false;
}

bool StrongTrendPDOK529(Direction dir)
{
   double pos = PDPosition522();

   if(dir == DIR_BUY && ST_AllowBuyOnlyDiscount)
      return pos <= ST_BuyMaxPDPosition;

   if(dir == DIR_SELL && ST_AllowSellOnlyPremium)
      return pos >= ST_SellMinPDPosition;

   return true;
}

int StrongTrendMask529(Direction dir)
{
   int mask = 0;

   double hi = TFRangeHigh522(InpExecTF, ST_RangeBars, 2);
   double lo = TFRangeLow522(InpExecTF, ST_RangeBars, 2);
   double c1 = iClose(TradeSymbol, InpExecTF, 1);
   double o1 = iOpen(TradeSymbol, InpExecTF, 1);
   double h1 = iHigh(TradeSymbol, InpExecTF, 1);
   double l1 = iLow(TradeSymbol, InpExecTF, 1);
   double body = MathAbs(c1 - o1);

   if(hi == -DBL_MAX || lo == DBL_MAX || hi <= lo)
      return 0;

   if(dir == DIR_BUY && c1 > hi + ST_BreakBufferPrice)
      mask |= 1; // S1 range break up

   if(dir == DIR_SELL && c1 < lo - ST_BreakBufferPrice)
      mask |= 1; // S1 range break down

   if(body >= ST_BodyMinPrice)
      mask |= 2; // S2 displacement

   double h3 = iHigh(TradeSymbol, InpExecTF, 3);
   double l3 = iLow(TradeSymbol, InpExecTF, 3);
   bool bullFVG = (l1 > h3);
   bool bearFVG = (h1 < l3);

   if(dir == DIR_BUY && bullFVG)
      mask |= 4; // S3 FVG
   if(dir == DIR_SELL && bearFVG)
      mask |= 4; // S3 FVG

   bool holdBuy = (dir == DIR_BUY && l1 >= hi - ST_BreakBufferPrice);
   bool holdSell = (dir == DIR_SELL && h1 <= lo + ST_BreakBufferPrice);
   if(holdBuy || holdSell)
      mask |= 8; // S4 hold outside old range

   if(HTFAlign529(dir))
      mask |= 16; // S5 HTF pressure

   return mask;
}

string StrongTrendMaskText529(int mask)
{
   string s = "";
   if((mask & 1)  != 0) s += (s == "" ? "S1" : "+S1");
   if((mask & 2)  != 0) s += (s == "" ? "S2" : "+S2");
   if((mask & 4)  != 0) s += (s == "" ? "S3" : "+S3");
   if((mask & 8)  != 0) s += (s == "" ? "S4" : "+S4");
   if((mask & 16) != 0) s += (s == "" ? "S5" : "+S5");
   return s;
}

bool StrongTrendAllowed529(Direction dir, int &mask)
{
   if(!EnableStrongTrendLane)
      return false;

   mask = StrongTrendMask529(dir);

   // Must have break + displacement + HTF pressure.
   if((mask & 1) == 0) return false;
   if((mask & 2) == 0) return false;
   if((mask & 16) == 0) return false;

   if(ST_RequireFVG && (mask & 4) == 0)
      return false;

   if(!StrongTrendPDOK529(dir))
      return false;

   return true;
}

void BuildStrongTrendTrade529(Direction dir, int stMask, ClusterSignal &c)
{
   c.valid = true;
   c.dir = dir;
   c.layerMask = 0;
   c.layerCount = CountSmallBits(stMask);
   c.hasGap = ((stMask & 4) != 0);
   c.hasFresh = false;
   c.tfList = "M5/H1/H4/D1";
   c.levelNames = "ST_BREAK";
   c.reason = "StrongTrendBreak";
   c.level = (dir == DIR_BUY ? TFRangeHigh522(InpExecTF, ST_RangeBars, 2) : TFRangeLow522(InpExecTF, ST_RangeBars, 2));
   c.minLevel = iLow(TradeSymbol, InpExecTF, 1);
   c.maxLevel = iHigh(TradeSymbol, InpExecTF, 1);

   double ask = SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(TradeSymbol, SYMBOL_BID);
   c.entry = (dir == DIR_BUY ? ask : bid);

   double recentLow  = RecentSwingLow(InpExecTF, RecentSwingBars);
   double recentHigh = RecentSwingHigh(InpExecTF, RecentSwingBars);

   if(dir == DIR_BUY)
   {
      c.sl = MathMin(iLow(TradeSymbol, InpExecTF, 1), recentLow) - SL_BufferPrice;
      double dist = c.entry - c.sl;
      if(dist < MinSLPrice)
         c.sl = c.entry - MinSLPrice;
      if((c.entry - c.sl) > ST_MaxSLPrice)
      {
         c.valid = false;
         return;
      }
      double risk = c.entry - c.sl;
      double dol = UseDOLTargetFirst ? FindDOLTarget(DIR_BUY, c.entry) : 0.0;
      if(dol > c.entry && (dol - c.entry) / risk >= MinDocumentTP_RR)
      {
         c.tp = dol;
         c.rr = (c.tp - c.entry) / risk;
      }
      else
      {
         double fallbackRR = MathMax(MinDocumentTP_RR, MathMax(FallbackRR, STD_Projection_Multiple));
         c.tp = c.entry + risk * fallbackRR;
         c.rr = fallbackRR;
      }
   }
   else
   {
      c.sl = MathMax(iHigh(TradeSymbol, InpExecTF, 1), recentHigh) + SL_BufferPrice;
      double dist = c.sl - c.entry;
      if(dist < MinSLPrice)
         c.sl = c.entry + MinSLPrice;
      if((c.sl - c.entry) > ST_MaxSLPrice)
      {
         c.valid = false;
         return;
      }
      double risk = c.sl - c.entry;
      double dol = UseDOLTargetFirst ? FindDOLTarget(DIR_SELL, c.entry) : 0.0;
      if(dol < c.entry && dol > 0.0 && (c.entry - dol) / risk >= MinDocumentTP_RR)
      {
         c.tp = dol;
         c.rr = (c.entry - c.tp) / risk;
      }
      else
      {
         double fallbackRR = MathMax(MinDocumentTP_RR, MathMax(FallbackRR, STD_Projection_Multiple));
         c.tp = c.entry - risk * fallbackRR;
         c.rr = fallbackRR;
      }
   }

   c.entry = NormalizePrice(c.entry);
   c.sl    = NormalizePrice(c.sl);
   c.tp    = NormalizePrice(c.tp);

   string side = (dir == DIR_BUY ? "B" : "S");
   c.comment = side + "|ST|BREAK|" + StrongTrendMaskText529(stMask);
   if(StringLen(c.comment) > 31)
      c.comment = StringSubstr(c.comment, 0, 31);
}


//====================================================================
// v5.30 TREND-ARMED PULLBACK LANE
//====================================================================
double BullFVG_CE530()
{
   double l1 = iLow(TradeSymbol, InpExecTF, 1);
   double h3 = iHigh(TradeSymbol, InpExecTF, 3);
   if(l1 > h3)
      return (l1 + h3) / 2.0;
   return 0.0;
}

double BearFVG_CE530()
{
   double h1 = iHigh(TradeSymbol, InpExecTF, 1);
   double l3 = iLow(TradeSymbol, InpExecTF, 3);
   if(h1 < l3)
      return (h1 + l3) / 2.0;
   return 0.0;
}

void ArmTrendBuy530(int stMask)
{
   if(!EnableTrendArmedPullback)
      return;

   TrendArmedBuy = true;
   TrendArmedBuyTime = iTime(TradeSymbol, InpExecTF, 1);
   TrendArmedBuyBars = 0;
   TrendArmedBuyEntries = 0;
   TrendBuyBreakLevel = TFRangeHigh522(InpExecTF, ST_RangeBars, 2);
   TrendBuyFVG_CE = BullFVG_CE530();
   TrendBuyExtreme = iHigh(TradeSymbol, InpExecTF, 1);
   TrendBuyMask = stMask;

   if(PrintDebug)
      Print("ARM TREND BUY: level=", DoubleToString(TrendBuyBreakLevel, _Digits),
            " | FVGCE=", DoubleToString(TrendBuyFVG_CE, _Digits),
            " | mask=", StrongTrendMaskText529(stMask));
}

void ArmTrendSell530(int stMask)
{
   if(!EnableTrendArmedPullback)
      return;

   TrendArmedSell = true;
   TrendArmedSellTime = iTime(TradeSymbol, InpExecTF, 1);
   TrendArmedSellBars = 0;
   TrendArmedSellEntries = 0;
   TrendSellBreakLevel = TFRangeLow522(InpExecTF, ST_RangeBars, 2);
   TrendSellFVG_CE = BearFVG_CE530();
   TrendSellExtreme = iLow(TradeSymbol, InpExecTF, 1);
   TrendSellMask = stMask;

   if(PrintDebug)
      Print("ARM TREND SELL: level=", DoubleToString(TrendSellBreakLevel, _Digits),
            " | FVGCE=", DoubleToString(TrendSellFVG_CE, _Digits),
            " | mask=", StrongTrendMaskText529(stMask));
}

void UpdateTrendArmAging530()
{
   if(TrendArmedBuy)
   {
      TrendArmedBuyBars++;
      TrendBuyExtreme = MathMax(TrendBuyExtreme, iHigh(TradeSymbol, InpExecTF, 1));

      if(TrendArmedBuyBars > TA_ValidBars || TrendArmedBuyEntries >= TA_MaxEntriesPerArm)
         TrendArmedBuy = false;
   }

   if(TrendArmedSell)
   {
      TrendArmedSellBars++;
      TrendSellExtreme = MathMin(TrendSellExtreme, iLow(TradeSymbol, InpExecTF, 1));

      if(TrendArmedSellBars > TA_ValidBars || TrendArmedSellEntries >= TA_MaxEntriesPerArm)
         TrendArmedSell = false;
   }
}

bool TrendBuyPullbackOK530()
{
   if(!TA_EnablePullbackBuy)
      return false;

   if(!TrendArmedBuy)
      return false;

   double low1 = iLow(TradeSymbol, InpExecTF, 1);
   double close1 = iClose(TradeSymbol, InpExecTF, 1);
   double open1 = iOpen(TradeSymbol, InpExecTF, 1);
   double body1 = MathAbs(close1 - open1);

   double pullback = TrendBuyExtreme - low1;
   bool enoughPullback = pullback >= TA_MinPullbackPrice && pullback <= TA_MaxPullbackPrice;

   bool retest = (low1 <= TrendBuyBreakLevel + TA_RetestBufferPrice && close1 > TrendBuyBreakLevel);
   bool fvgTap = (TrendBuyFVG_CE > 0.0 && low1 <= TrendBuyFVG_CE + TA_FVGCE_TolerancePrice && close1 > TrendBuyFVG_CE);
   bool continuation = (close1 > open1 && body1 >= TA_ContinuationBodyMin);

   bool pdOK = true;
   if(TA_BuyNeedsDiscountOrRetest)
      pdOK = (PDPosition522() <= ST_BuyMaxPDPosition || retest || fvgTap);

   bool locationOK = (!TA_RequireRetestOrFVG || retest || fvgTap);

   if(TA_RequireContinuationCandle)
      return enoughPullback && locationOK && continuation && pdOK;

   return enoughPullback && locationOK && pdOK;
}

bool TrendSellPullbackOK530()
{
   if(!TA_EnablePullbackSell)
      return false;

   if(!TrendArmedSell)
      return false;

   double high1 = iHigh(TradeSymbol, InpExecTF, 1);
   double close1 = iClose(TradeSymbol, InpExecTF, 1);
   double open1 = iOpen(TradeSymbol, InpExecTF, 1);
   double body1 = MathAbs(close1 - open1);

   double pullback = high1 - TrendSellExtreme;
   bool enoughPullback = pullback >= TA_MinPullbackPrice && pullback <= TA_MaxPullbackPrice;

   bool retestFail = (high1 >= TrendSellBreakLevel - TA_RetestBufferPrice && close1 < TrendSellBreakLevel);
   bool fvgTap = (TrendSellFVG_CE > 0.0 && high1 >= TrendSellFVG_CE - TA_FVGCE_TolerancePrice && close1 < TrendSellFVG_CE);
   bool continuation = (close1 < open1 && body1 >= TA_ContinuationBodyMin);

   bool locationOK = (!TA_RequireRetestOrFVG || retestFail || fvgTap);

   if(TA_SellNeedsHoldOrRetest && !(retestFail || fvgTap))
      return false;

   if(TA_RequireContinuationCandle)
      return enoughPullback && locationOK && continuation;

   return enoughPullback && locationOK;
}

void BuildTrendPullbackTrade530(Direction dir, ClusterSignal &c)
{
   c.valid = true;
   c.dir = dir;
   c.layerMask = 0;
   c.layerCount = 0;
   c.hasGap = false;
   c.hasFresh = false;
   c.tfList = "M5/H1/H4/D1";
   c.levelNames = "TA_PULLBACK";
   c.reason = "TrendArmedPullback";

   double ask = SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(TradeSymbol, SYMBOL_BID);
   c.entry = (dir == DIR_BUY ? ask : bid);

   double recentLow  = RecentSwingLow(InpExecTF, RecentSwingBars);
   double recentHigh = RecentSwingHigh(InpExecTF, RecentSwingBars);

   if(dir == DIR_BUY)
   {
      c.level = TrendBuyBreakLevel;
      c.minLevel = TrendBuyBreakLevel;
      c.maxLevel = TrendBuyExtreme;
      c.sl = MathMin(iLow(TradeSymbol, InpExecTF, 1), recentLow) - SL_BufferPrice;

      if((c.entry - c.sl) < MinSLPrice)
         c.sl = c.entry - MinSLPrice;
      if((c.entry - c.sl) > ST_MaxSLPrice)
      {
         c.valid = false;
         return;
      }

      double risk = c.entry - c.sl;
      double dol = UseDOLTargetFirst ? FindDOLTarget(DIR_BUY, c.entry) : 0.0;

      if(dol > c.entry && (dol - c.entry) / risk >= MinDocumentTP_RR)
      {
         c.tp = dol;
         c.rr = (c.tp - c.entry) / risk;
      }
      else
      {
         double fallbackRR = MathMax(MinDocumentTP_RR, MathMax(FallbackRR, STD_Projection_Multiple));
         c.tp = c.entry + risk * fallbackRR;
         c.rr = fallbackRR;
      }

      c.comment = "B|TA|PB|" + StrongTrendMaskText529(TrendBuyMask);
   }
   else
   {
      c.level = TrendSellBreakLevel;
      c.minLevel = TrendSellExtreme;
      c.maxLevel = TrendSellBreakLevel;
      c.sl = MathMax(iHigh(TradeSymbol, InpExecTF, 1), recentHigh) + SL_BufferPrice;

      if((c.sl - c.entry) < MinSLPrice)
         c.sl = c.entry + MinSLPrice;
      if((c.sl - c.entry) > ST_MaxSLPrice)
      {
         c.valid = false;
         return;
      }

      double risk = c.sl - c.entry;
      double dol = UseDOLTargetFirst ? FindDOLTarget(DIR_SELL, c.entry) : 0.0;

      if(dol < c.entry && dol > 0.0 && (c.entry - dol) / risk >= MinDocumentTP_RR)
      {
         c.tp = dol;
         c.rr = (c.entry - c.tp) / risk;
      }
      else
      {
         double fallbackRR = MathMax(MinDocumentTP_RR, MathMax(FallbackRR, STD_Projection_Multiple));
         c.tp = c.entry - risk * fallbackRR;
         c.rr = fallbackRR;
      }

      c.comment = "S|TA|PB|" + StrongTrendMaskText529(TrendSellMask);
   }

   c.entry = NormalizePrice(c.entry);
   c.sl    = NormalizePrice(c.sl);
   c.tp    = NormalizePrice(c.tp);

   if(StringLen(c.comment) > 31)
      c.comment = StringSubstr(c.comment, 0, 31);
}

void ScanTrendPullbackEntry530()
{
   if(!EnableTrendArmedPullback)
      return;

   if(ordersThisBar >= MaxConfluenceTradesPerBar)
      return;

   if(TrendBuyPullbackOK530())
   {
      ClusterSignal c;
      BuildTrendPullbackTrade530(DIR_BUY, c);
      if(c.valid && PositionLimitOK())
      {
         if(PlaceClusterTrade(c))
            TrendArmedBuyEntries++;
      }
   }

   if(ordersThisBar >= MaxConfluenceTradesPerBar)
      return;

   if(TrendSellPullbackOK530())
   {
      ClusterSignal c;
      BuildTrendPullbackTrade530(DIR_SELL, c);
      if(c.valid && PositionLimitOK())
      {
         if(PlaceClusterTrade(c))
            TrendArmedSellEntries++;
      }
   }
}

void ScanAndTradeStrongTrend529()
{
   if(!EnableStrongTrendLane)
      return;

   if(ordersThisBar >= MaxConfluenceTradesPerBar)
      return;

   int opened = 0;

   int maskBuy = 0;
   if(StrongTrendAllowed529(DIR_BUY, maskBuy))
   {
      ArmTrendBuy530(maskBuy);

      if(TA_AllowInstantSTBuy && opened < StrongTrendMaxTradesPerBar)
      {
         ClusterSignal c;
         BuildStrongTrendTrade529(DIR_BUY, maskBuy, c);
         if(c.valid && PositionLimitOK())
         {
            if(PlaceClusterTrade(c))
               opened++;
         }
      }
   }

   int maskSell = 0;
   if(StrongTrendAllowed529(DIR_SELL, maskSell))
   {
      if(TA_EnablePullbackSell)
         ArmTrendSell530(maskSell);

      bool sellHasHold = ((maskSell & 8) != 0);
      bool allowInstantSell = (!TA_DisableInstantSTSell && sellHasHold);

      if(allowInstantSell && opened < StrongTrendMaxTradesPerBar)
      {
         ClusterSignal c;
         BuildStrongTrendTrade529(DIR_SELL, maskSell, c);
         if(c.valid && PositionLimitOK())
         {
            if(PlaceClusterTrade(c))
               opened++;
         }
      }
   }
}


void PrepareClusterForTrade(ClusterSignal &c)
{
   c.valid = true;
   c.layerCount = CountMaskBits(c.layerMask);

   // v5.32: rejection funnel — every dead cluster tells you WHY (PrintDebug)
   #define MSNR_KILL(reason) { c.valid = false; if(PrintDebug) Print("FUNNEL: cluster ", (c.dir==DIR_BUY?"BUY":"SELL"), " @", DoubleToString(c.level,2), " layers=", c.layerCount, " killed by ", reason); return; }

   // v5.24 Dual Mode:
   // REACTION = classic 5/10 + whitelist + zone reaction logic.
   // MOMENTUM = 3/5 momentum confirmations for fast trend / break-hold / FVG continuation.
   // Both lanes still use the same risk model, TP1 4R partial, and document TP DOL/STD.

   bool expansion = IsExpansion520();
   string stateTag = MarketStateTag522(c.dir);
   bool breakHold = (stateTag == "BREAK_HOLD");

   string wlTag = WhitelistTag520(c.layerMask);
   string tradeMode = "";
   string setupText = "";
   int momMask = 0;
   string momText = "";

   bool reactionOK = ReactionAllowed522(c.layerCount, wlTag);
   bool momentumOK = MomentumAllowed522(c.dir, stateTag, momMask, momText);

   if(momentumOK && !MomentumLayerAllowed527(c.dir, stateTag, c.layerMask, momMask))
      momentumOK = false;

   if(momentumOK)
      momText = MomentumFilterTag527(c.dir, stateTag, c.layerMask, momMask);

   // v5.29 long-cycle protection
   if(DisableNormalMarket && stateTag == "NORMAL")
   {
      reactionOK = false;
      momentumOK = false;
   }

   if(DisableReactionRangeLock && stateTag == "RANGE_LOCK")
      reactionOK = false;

   if(DisableBuyInWeakHTF && c.dir == DIR_BUY && !HTFAlign529(DIR_BUY))
      momentumOK = false;

   if(UseDualMode)
   {
      // Momentum gets priority when it appears because its purpose is to catch strong one-way movement.
      if(momentumOK)
      {
         tradeMode = "MOMENTUM";
         setupText = momText;
      }
      else if(reactionOK)
      {
         tradeMode = "REACTION";
         setupText = wlTag;
      }
      else
         MSNR_KILL(StringFormat("both lanes: reaction(%d/%d conf%s) + momentum blocked [state=%s]",
                                c.layerCount, ReactionMinConfirmations,
                                Use5of10Whitelist && wlTag == "" ? ", no WL combo" : "", stateTag))
   }
   else
   {
      if(!reactionOK)
         MSNR_KILL(StringFormat("reaction lane: %d/%d confirmations%s", c.layerCount,
                                ReactionMinConfirmations,
                                Use5of10Whitelist && wlTag == "" ? " + no whitelist combo" : ""))
      tradeMode = "REACTION";
      setupText = wlTag;
   }

   // Reaction lane keeps full PD/range filters.
   // Momentum lane can bypass PD when strong break is confirmed.
   if(tradeMode == "REACTION" || !MomentumBypassPDFilter)
   {
      if(!PremiumDiscountOK522(c.dir))
         MSNR_KILL("premium/discount filter")
   }

   if(tradeMode == "REACTION")
   {
      if(RequireRangeEdgeOrBreak && IsNarrowRange520() && !RangeEdgeTradeOK522(c.dir) && !breakHold)
         MSNR_KILL("narrow range: not at edge, no break-hold")

      if(BlockedWorstNormalBuy520(stateTag, c.dir, c.layerMask))
         MSNR_KILL("worst-normal-buy block")
   }

   if(!RangeSpamAllowed520())
      MSNR_KILL("range spam lock")

   BuildClusterTradePrices(c);

   if(c.rr < MinRR)
      MSNR_KILL(StringFormat("RR %.2f < MinRR %.2f (SL=%.2f TP=%.2f)", c.rr, MinRR, c.sl, c.tp))

   string layers = MaskToLayerText(c.layerMask);
   string side = (c.dir == DIR_BUY ? "BUY" : "SELL");
   string shortMode = (tradeMode == "MOMENTUM" ? "MOM" : "REA");

   c.comment = StringFormat("%s | %s | %s | %s | %s | %.2f",
                            side,
                            shortMode,
                            stateTag,
                            setupText,
                            layers,
                            c.level);

   if(c.hasGap)
      c.comment += " | GAP";
   if(c.hasFresh)
      c.comment += " | Fresh";

   if(StringLen(c.comment) > 120)
      c.comment = StringSubstr(c.comment, 0, 120);
}


//====================================================================
// PARTIAL PROFIT MANAGER v5.25 FIX
//====================================================================
int PartialTicketIndex(ulong ticket)
{
   for(int i = 0; i < PartialTicketCount; i++)
   {
      if(PartialTickets[i] == ticket)
         return i;
   }

   if(PartialTicketCount < ArraySize(PartialTickets))
   {
      PartialTickets[PartialTicketCount] = ticket;
      PartialTaken[PartialTicketCount] = false;
      PartialTicketCount++;
      return PartialTicketCount - 1;
   }

   return -1;
}

double NormalizePartialVolume(double volume)
{
   double volMin  = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);
   double volMax  = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MAX);
   double volStep = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_STEP);

   if(volStep <= 0.0)
      return NormalizeDouble(volume, 2);

   double v = MathFloor(volume / volStep) * volStep;
   v = MathMax(volMin, MathMin(volMax, v));
   return NormalizeDouble(v, 2);
}

void ManagePartialAt4R()
{
   if(!UsePartialAt4R)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != TradeSymbol)
         continue;

      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      int idx = PartialTicketIndex(ticket);
      if(idx < 0)
         continue;

      if(PartialTaken[idx])
         continue;

      long type = PositionGetInteger(POSITION_TYPE);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double vol = PositionGetDouble(POSITION_VOLUME);

      double price = (type == POSITION_TYPE_BUY ?
                      SymbolInfoDouble(TradeSymbol, SYMBOL_BID) :
                      SymbolInfoDouble(TradeSymbol, SYMBOL_ASK));

      double risk = MathAbs(open - sl);
      if(risk <= 0.0)
         continue;

      double rrNow = 0.0;
      if(type == POSITION_TYPE_BUY)
         rrNow = (price - open) / risk;
      else if(type == POSITION_TYPE_SELL)
         rrNow = (open - price) / risk;

      if(rrNow < PartialAtRR)
         continue;

      double closeVol = NormalizePartialVolume(vol * PartialPercent / 100.0);
      double volMin = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);

      if(closeVol >= volMin && closeVol < vol)
      {
         bool closed = trade.PositionClosePartial(ticket, closeVol, SlippagePoints);
         if(closed)
         {
            PartialTaken[idx] = true;

            if(MoveSLAfterPartial)
            {
               double newSL = 0.0;

               if(type == POSITION_TYPE_BUY)
                  newSL = open + risk * SLAfterPartialLockRR + BE_OffsetPrice;
               else
                  newSL = open - risk * SLAfterPartialLockRR - BE_OffsetPrice;

               bool moveOK = false;
               if(type == POSITION_TYPE_BUY && newSL > sl && newSL < price)
                  moveOK = true;
               if(type == POSITION_TYPE_SELL && newSL < sl && newSL > price)
                  moveOK = true;

               if(moveOK)
                  trade.PositionModify(ticket, NormalizePrice(newSL), tp);
            }

            if(PrintDebug)
               Print("PARTIAL 4R: ticket=", ticket,
                     " | closedVol=", DoubleToString(closeVol, 2),
                     " | rr=", DoubleToString(rrNow, 2),
                     " | hold remaining to document TP");
         }
      }
      else
      {
         PartialTaken[idx] = true;
      }
   }
}

//====================================================================
// LOT + ORDER
//====================================================================
double CalcLot(double entry, double sl)
{
   if(FixedLot > 0.0)
      return FixedLot;

   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = balance * EffectiveRiskPlus531() / 100.0;

   double tickSize  = SymbolInfoDouble(TradeSymbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(TradeSymbol, SYMBOL_TRADE_TICK_VALUE);
   double volMin    = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);
   double volMax    = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MAX);
   double volStep   = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_STEP);

   if(tickSize <= 0.0 || tickValue <= 0.0 || volStep <= 0.0)
      return volMin;

   double slDist = MathAbs(entry - sl);
   double moneyPerLot = (slDist / tickSize) * tickValue;

   if(moneyPerLot <= 0.0)
      return volMin;

   double lot = riskMoney / moneyPerLot;
   lot = MathFloor(lot / volStep) * volStep;
   lot = MathMax(volMin, MathMin(volMax, lot));

   return NormalizeDouble(lot, 2);
}

double NormalizeVolume531(double lot)
{
   double volMin  = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);
   double volMax  = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MAX);
   double volStep = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_STEP);

   if(volStep <= 0.0)
      volStep = 0.01;

   if(MaxLotPerTrade > 0.0)
      lot = MathMin(lot, MaxLotPerTrade);

   lot = MathMax(volMin, MathMin(volMax, lot));
   lot = MathFloor(lot / volStep) * volStep;

   if(lot < volMin)
      return 0.0;

   return NormalizeDouble(lot, 2);
}

bool AdjustLotForMargin531(Direction dir, double price, double &lot)
{
   lot = NormalizeVolume531(lot);
   if(lot <= 0.0)
      return false;

   ENUM_ORDER_TYPE orderType = (dir == DIR_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   double marginRequired = 0.0;

   if(!OrderCalcMargin(orderType, TradeSymbol, lot, price, marginRequired))
   {
      Print("Margin check failed. Symbol=", TradeSymbol, " lot=", DoubleToString(lot, 2));
      return !SkipIfMarginNotEnough;
   }

   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double allowedMargin = freeMargin * MathMax(1.0, MathMin(100.0, MaxMarginUsagePercent)) / 100.0;

   if(marginRequired <= allowedMargin)
      return true;

   if(!SkipIfMarginNotEnough)
      return true;

   double volMin  = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);
   double volStep = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_STEP);

   if(volStep <= 0.0)
      volStep = 0.01;

   double affordableLot = lot * allowedMargin / marginRequired;
   affordableLot = MathFloor(affordableLot / volStep) * volStep;
   affordableLot = NormalizeVolume531(affordableLot);

   if(affordableLot < volMin || affordableLot <= 0.0)
   {
      Print("Skipped trade: not enough margin. Required=", DoubleToString(marginRequired, 2),
            " Allowed=", DoubleToString(allowedMargin, 2),
            " Free=", DoubleToString(freeMargin, 2),
            " Lot=", DoubleToString(lot, 2));
      return false;
   }

   double marginAfterAdjust = 0.0;
   if(!OrderCalcMargin(orderType, TradeSymbol, affordableLot, price, marginAfterAdjust))
      return false;

   if(marginAfterAdjust > allowedMargin)
      return false;

   if(PrintDebug)
      Print("Lot reduced by margin guard: ", DoubleToString(lot, 2), " -> ", DoubleToString(affordableLot, 2));

   lot = affordableLot;
   return true;
}

bool PlaceClusterTrade(ClusterSignal &c)
{
   if(!c.valid) return false;
   if(!PositionLimitOK()) return false;
   if(ordersThisBar >= MaxConfluenceTradesPerBar) return false;

   double lot = CalcLot(c.entry, c.sl);
   double orderPrice = (c.dir == DIR_BUY ? SymbolInfoDouble(TradeSymbol, SYMBOL_ASK) : SymbolInfoDouble(TradeSymbol, SYMBOL_BID));

   if(!AdjustLotForMargin531(c.dir, orderPrice, lot))
      return false;

   InitialBalanceForCSV = AccountInfoDouble(ACCOUNT_BALANCE);
   EnsureClosedTradeCSVHeader();

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);

   bool ok = false;

   if(c.dir == DIR_BUY)
      ok = trade.Buy(lot, TradeSymbol, 0.0, c.sl, c.tp, c.comment);

   if(c.dir == DIR_SELL)
      ok = trade.Sell(lot, TradeSymbol, 0.0, c.sl, c.tp, c.comment);

   if(ok)
   {
      ordersThisBar++;
      AppendSignalCSV(c, lot);

      if(PrintDebug)
      {
         Print("OPENED CLUSTER: ", c.comment,
               " | layers=", c.layerCount,
               " | lot=", DoubleToString(lot, 2),
               " | SL=", DoubleToString(c.sl, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
               " | TP=", DoubleToString(c.tp, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
               " | RR=", DoubleToString(c.rr, 2));
      }
   }
   else
   {
      if(PrintDebug)
         Print("ORDER FAILED: ", GetLastError(), " | ", c.comment);
   }

   return ok;
}

void ManageBE()
{
   if(!MoveSLToBEAtRR1) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;

      if(PositionGetString(POSITION_SYMBOL) != TradeSymbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      long type = PositionGetInteger(POSITION_TYPE);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);

      double bid = SymbolInfoDouble(TradeSymbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
      double price = (type == POSITION_TYPE_BUY ? bid : ask);

      double risk = MathAbs(open - sl);
      if(risk <= 0.0) continue;

      bool rr1 = false;
      if(type == POSITION_TYPE_BUY && price >= open + risk)
         rr1 = true;
      if(type == POSITION_TYPE_SELL && price <= open - risk)
         rr1 = true;

      if(rr1)
      {
         double newSL = (type == POSITION_TYPE_BUY ? open + BE_OffsetPrice : open - BE_OffsetPrice);

         bool shouldMove = false;
         if(type == POSITION_TYPE_BUY && sl < newSL)
            shouldMove = true;
         if(type == POSITION_TYPE_SELL && sl > newSL)
            shouldMove = true;

         if(shouldMove)
            trade.PositionModify(ticket, NormalizePrice(newSL), tp);
      }
   }
}



//====================================================================
// CSV PATH + ENTRY SIGNAL LOG
//====================================================================
string ExportFolderPath()
{
   if(ExportCSVToCommonFolder)
      return TerminalInfoString(TERMINAL_COMMONDATA_PATH) + "\\Files\\";

   return TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\";
}

void PrintExportPaths()
{
   Print("CSV folder path: ", ExportFolderPath());
   Print("Deals CSV target: ", ExportFolderPath(), ExportCSVFileName);
   Print("Signal CSV target: ", ExportFolderPath(), ExportSignalLogFileName);
   Print("Closed trades CSV target: ", ExportFolderPath(), ExportClosedTradeFileName);
}

void AppendSignalCSV(ClusterSignal &c, double lot)
{
   if(!ExportSignalLogOnEntry)
      return;

   int flags = FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI;
   if(ExportCSVToCommonFolder)
      flags |= FILE_COMMON;

   bool exists = FileIsExist(ExportSignalLogFileName, ExportCSVToCommonFolder ? FILE_COMMON : 0);

   int file = FileOpen(ExportSignalLogFileName, flags, ',');
   if(file == INVALID_HANDLE)
   {
      Print("Signal CSV open failed. Error=", GetLastError(), " | path=", ExportFolderPath(), ExportSignalLogFileName);
      return;
   }

   if(!exists || FileSize(file) == 0)
   {
      FileWrite(file,
                "time",
                "symbol",
                "side",
                "lot",
                "entry",
                "sl",
                "tp",
                "rr",
                "layers",
                "comment");
   }

   FileSeek(file, 0, SEEK_END);

   string side = (c.dir == DIR_BUY ? "BUY" : "SELL");
   string layers = MaskToLayerText(c.layerMask);

   FileWrite(file,
             TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
             TradeSymbol,
             side,
             DoubleToString(lot, 2),
             DoubleToString(c.entry, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
             DoubleToString(c.sl, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
             DoubleToString(c.tp, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
             DoubleToString(c.rr, 2),
             layers,
             c.comment);

   FileClose(file);
}


//====================================================================
// CLOSED TRADE RESULT LOG
//====================================================================
void EnsureClosedTradeCSVHeader()
{
   int flags = FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI;
   if(ExportCSVToCommonFolder)
      flags |= FILE_COMMON;

   bool exists = FileIsExist(ExportClosedTradeFileName, ExportCSVToCommonFolder ? FILE_COMMON : 0);
   int file = FileOpen(ExportClosedTradeFileName, flags, ',');

   if(file == INVALID_HANDLE)
   {
      Print("Closed trade CSV header failed. Error=", GetLastError(), " | path=", ExportFolderPath(), ExportClosedTradeFileName);
      return;
   }

   if(!exists || FileSize(file) == 0)
   {
      FileWrite(file,
                "close_time",
                "position_id",
                "symbol",
                "magic",
                "side",
                "open_time",
                "close_type",
                "volume",
                "open_price",
                "close_price",
                "sl",
                "tp",
                "rr_planned",
                "profit",
                "commission",
                "swap",
                "net_profit",
                "result",
                "comment",
                "layers",
                "wl_tag",
                "duration_minutes");
   }

   FileClose(file);
}

string ExtractWLTag(string comment)
{
   int p = StringFind(comment, "WL_");
   if(p < 0)
      return "";

   int end = StringFind(comment, " | ", p);
   if(end < 0)
      end = StringLen(comment);

   return StringSubstr(comment, p, end - p);
}

string ExtractLayersFromComment(string comment)
{
   // Comment shape:
   // BUY | WL_MAIN_L1+L3+L4 | L1+L3+L4 | H4/H1 | price | reason
   int first = StringFind(comment, " | ");
   if(first < 0) return "";

   int second = StringFind(comment, " | ", first + 3);
   if(second < 0) return "";

   int third = StringFind(comment, " | ", second + 3);
   if(third < 0) return "";

   return StringSubstr(comment, second + 3, third - (second + 3));
}

void AppendClosedTradeCSV(ulong closeDealTicket)
{
   if(!ExportSignalLogOnEntry)
      return;

   if(closeDealTicket == 0)
      return;

   long magic = HistoryDealGetInteger(closeDealTicket, DEAL_MAGIC);
   string sym = HistoryDealGetString(closeDealTicket, DEAL_SYMBOL);
   long entry = HistoryDealGetInteger(closeDealTicket, DEAL_ENTRY);

   if(magic != MagicNumber || sym != TradeSymbol)
      return;

   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT && entry != DEAL_ENTRY_OUT_BY)
      return;

   long positionId = HistoryDealGetInteger(closeDealTicket, DEAL_POSITION_ID);
   datetime closeTime = (datetime)HistoryDealGetInteger(closeDealTicket, DEAL_TIME);
   double closePrice = HistoryDealGetDouble(closeDealTicket, DEAL_PRICE);
   double closeVolume = HistoryDealGetDouble(closeDealTicket, DEAL_VOLUME);
   double closeProfit = HistoryDealGetDouble(closeDealTicket, DEAL_PROFIT);
   double closeCommission = HistoryDealGetDouble(closeDealTicket, DEAL_COMMISSION);
   double closeSwap = HistoryDealGetDouble(closeDealTicket, DEAL_SWAP);
   double closeFee = HistoryDealGetDouble(closeDealTicket, DEAL_FEE);
   double closeNet = closeProfit + closeCommission + closeSwap + closeFee;

   ulong openDeal = 0;
   double openPrice = 0.0;
   datetime openTime = 0;
   string openComment = "";
   string side = "";
   double openVolume = 0.0;

   int total = HistoryDealsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0 || t == closeDealTicket)
         continue;

      if((long)HistoryDealGetInteger(t, DEAL_POSITION_ID) != positionId)
         continue;

      long e = HistoryDealGetInteger(t, DEAL_ENTRY);
      if(e == DEAL_ENTRY_IN || e == DEAL_ENTRY_INOUT)
      {
         openDeal = t;
         openPrice = HistoryDealGetDouble(t, DEAL_PRICE);
         openTime = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
         openComment = HistoryDealGetString(t, DEAL_COMMENT);
         openVolume = HistoryDealGetDouble(t, DEAL_VOLUME);

         long type = HistoryDealGetInteger(t, DEAL_TYPE);
         if(type == DEAL_TYPE_BUY)
            side = "BUY";
         else if(type == DEAL_TYPE_SELL)
            side = "SELL";
         else
            side = DealTypeText(type);

         break;
      }
   }

   string finalComment = openComment;
   if(finalComment == "")
      finalComment = HistoryDealGetString(closeDealTicket, DEAL_COMMENT);

   double sl = 0.0, tp = 0.0, rr = 0.0;

   if(openDeal > 0)
   {
      // Find original order from open deal to recover SL/TP when broker stores it.
      ulong orderTicket = (ulong)HistoryDealGetInteger(openDeal, DEAL_ORDER);
      if(orderTicket > 0 && HistoryOrderSelect(orderTicket))
      {
         sl = HistoryOrderGetDouble(orderTicket, ORDER_SL);
         tp = HistoryOrderGetDouble(orderTicket, ORDER_TP);

         if(side == "BUY" && sl > 0.0 && tp > 0.0 && openPrice > sl)
            rr = (tp - openPrice) / (openPrice - sl);

         if(side == "SELL" && sl > 0.0 && tp > 0.0 && sl > openPrice)
            rr = (openPrice - tp) / (sl - openPrice);
      }
   }

   string closeType = "CLOSE";
   if(side == "BUY")
   {
      if(tp > 0.0 && closePrice >= tp - SymbolInfoDouble(TradeSymbol, SYMBOL_POINT) * 5)
         closeType = "TP";
      else if(sl > 0.0 && closePrice <= sl + SymbolInfoDouble(TradeSymbol, SYMBOL_POINT) * 5)
         closeType = "SL";
   }
   if(side == "SELL")
   {
      if(tp > 0.0 && closePrice <= tp + SymbolInfoDouble(TradeSymbol, SYMBOL_POINT) * 5)
         closeType = "TP";
      else if(sl > 0.0 && closePrice >= sl - SymbolInfoDouble(TradeSymbol, SYMBOL_POINT) * 5)
         closeType = "SL";
   }

   string result = closeNet > 0.0 ? "WIN" : (closeNet < 0.0 ? "LOSS" : "BE");

   int flags = FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI;
   if(ExportCSVToCommonFolder)
      flags |= FILE_COMMON;

   EnsureClosedTradeCSVHeader();

   int file = FileOpen(ExportClosedTradeFileName, flags, ',');
   if(file == INVALID_HANDLE)
   {
      Print("Closed trade CSV open failed. Error=", GetLastError(), " | path=", ExportFolderPath(), ExportClosedTradeFileName);
      return;
   }

   FileSeek(file, 0, SEEK_END);

   double duration = 0.0;
   if(openTime > 0)
      duration = (double)(closeTime - openTime) / 60.0;

   FileWrite(file,
             TimeToString(closeTime, TIME_DATE | TIME_SECONDS),
             (string)positionId,
             sym,
             (string)magic,
             side,
             openTime > 0 ? TimeToString(openTime, TIME_DATE | TIME_SECONDS) : "",
             closeType,
             DoubleToString(closeVolume > 0.0 ? closeVolume : openVolume, 2),
             DoubleToString(openPrice, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
             DoubleToString(closePrice, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
             DoubleToString(sl, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
             DoubleToString(tp, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
             DoubleToString(rr, 2),
             DoubleToString(closeProfit, 2),
             DoubleToString(closeCommission, 2),
             DoubleToString(closeSwap, 2),
             DoubleToString(closeNet, 2),
             result,
             finalComment,
             ExtractLayersFromComment(finalComment),
             ExtractWLTag(finalComment),
             DoubleToString(duration, 1));

   FileClose(file);

   if(PrintDebug)
      Print("CLOSED TRADE LOGGED: ", result, " | ", side, " | net=", DoubleToString(closeNet, 2), " | ", finalComment);
}

//====================================================================
// CSV EXPORT FALLBACK
//====================================================================
string DealEntryText(long entry)
{
   if(entry == DEAL_ENTRY_IN)      return "IN";
   if(entry == DEAL_ENTRY_OUT)     return "OUT";
   if(entry == DEAL_ENTRY_INOUT)   return "INOUT";
   if(entry == DEAL_ENTRY_OUT_BY)  return "OUT_BY";
   return IntegerToString((int)entry);
}

string DealTypeText(long type)
{
   if(type == DEAL_TYPE_BUY)        return "BUY";
   if(type == DEAL_TYPE_SELL)       return "SELL";
   if(type == DEAL_TYPE_BALANCE)    return "BALANCE";
   if(type == DEAL_TYPE_CREDIT)     return "CREDIT";
   if(type == DEAL_TYPE_CHARGE)     return "CHARGE";
   if(type == DEAL_TYPE_CORRECTION) return "CORRECTION";
   if(type == DEAL_TYPE_BONUS)      return "BONUS";
   if(type == DEAL_TYPE_COMMISSION) return "COMMISSION";
   return IntegerToString((int)type);
}

void ExportBacktestDealsCSV()
{
   if(!ExportCSVOnDeinit)
      return;

   datetime from = 0;
   datetime to = TimeCurrent() + 86400;

   if(!HistorySelect(from, to))
   {
      Print("CSV export failed: HistorySelect failed.");
      return;
   }

   int flags = FILE_WRITE | FILE_CSV | FILE_ANSI;
   if(ExportCSVToCommonFolder)
      flags |= FILE_COMMON;

   int file = FileOpen(ExportCSVFileName, flags, ',');
   if(file == INVALID_HANDLE)
   {
      Print("CSV export failed. FileOpen error: ", GetLastError(), " | file=", ExportCSVFileName);
      return;
   }

   FileWrite(file,
             "time",
             "deal_ticket",
             "order_ticket",
             "position_id",
             "symbol",
             "magic",
             "entry",
             "deal_type",
             "volume",
             "price",
             "profit",
             "commission",
             "swap",
             "fee",
             "comment");

   int total = HistoryDealsTotal();
   int exported = 0;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;

      string sym = HistoryDealGetString(ticket, DEAL_SYMBOL);
      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      long dealType = HistoryDealGetInteger(ticket, DEAL_TYPE);

      bool isBalanceEvent = (dealType == DEAL_TYPE_BALANCE || dealType == DEAL_TYPE_CREDIT);
      if(sym != TradeSymbol && !isBalanceEvent)
         continue;

      if(magic != MagicNumber && !isBalanceEvent)
         continue;

      datetime dealTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      ulong orderTicket = (ulong)HistoryDealGetInteger(ticket, DEAL_ORDER);
      long positionId = HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      double volume = HistoryDealGetDouble(ticket, DEAL_VOLUME);
      double price = HistoryDealGetDouble(ticket, DEAL_PRICE);
      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
      double commission = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      double swap = HistoryDealGetDouble(ticket, DEAL_SWAP);
      double fee = HistoryDealGetDouble(ticket, DEAL_FEE);
      string comment = HistoryDealGetString(ticket, DEAL_COMMENT);

      FileWrite(file,
                TimeToString(dealTime, TIME_DATE | TIME_SECONDS),
                (string)ticket,
                (string)orderTicket,
                (string)positionId,
                sym,
                (string)magic,
                DealEntryText(entry),
                DealTypeText(dealType),
                DoubleToString(volume, 2),
                DoubleToString(price, (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS)),
                DoubleToString(profit, 2),
                DoubleToString(commission, 2),
                DoubleToString(swap, 2),
                DoubleToString(fee, 2),
                comment);
      exported++;
   }

   FileClose(file);

   Print("CSV export completed: ", ExportFolderPath(), ExportCSVFileName, " | exported=", exported);
}

//====================================================================
// VISUALS
//====================================================================
void ClearDrawings()
{
   for(int i = ObjectsTotal(0, 0, -1) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, "MSNR_CONFL_") == 0)
         ObjectDelete(0, name);
   }
}

void DrawSNRLevels()
{
   if(!DrawLevels) return;

   ClearDrawings();

   for(int i = 0; i < levelCount; i++)
   {
      string obj = "MSNR_CONFL_" + IntegerToString(i) + "_" + levels[i].tf;

      ObjectCreate(0, obj, OBJ_HLINE, 0, 0, levels[i].price);
      ObjectSetInteger(0, obj, OBJPROP_STYLE, levels[i].gap ? STYLE_DASHDOT : STYLE_DASH);
      ObjectSetInteger(0, obj, OBJPROP_WIDTH, levels[i].fresh ? 2 : 1);
      ObjectSetInteger(0, obj, OBJPROP_COLOR, levels[i].dir == 1 ? clrDeepSkyBlue : clrTomato);
      ObjectSetString(0, obj, OBJPROP_TEXT, levels[i].tf + " " + levels[i].name + " " + (levels[i].fresh ? "Fresh" : "Used"));
   }
}

//====================================================================
// MAIN EXECUTION
//====================================================================
void ScanAndTradeConfluence()
{
   BuildConfluenceClusters();

   if(PrintDebug)
      Print("Clusters found: ", clusterCount);

   for(int i = 0; i < clusterCount; i++)
   {
      if(ordersThisBar >= MaxConfluenceTradesPerBar) return;
      if(!PositionLimitOK()) return;

      if(!AllowBuyAndSellSameBar && ordersThisBar > 0)
         return;

      PrepareClusterForTrade(clusters[i]);

      if(clusters[i].valid)
         PlaceClusterTrade(clusters[i]);
   }
}




//====================================================================
// v5.31 PLUS HELPERS
//====================================================================
bool HourInRange531Plus(int h, int startH, int endH)
{
   if(startH == endH)
      return true;

   if(startH < endH)
      return (h >= startH && h < endH);

   // crosses midnight, e.g. 23 -> 04
   return (h >= startH || h < endH);
}

int DayOfWeek531(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return dt.day_of_week;
}

int NthSundayDay531(int year, int month, int nth)
{
   MqlDateTime dt;
   dt.year = year;
   dt.mon = month;
   dt.day = 1;
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;

   datetime firstDay = StructToTime(dt);
   int firstDOW = DayOfWeek531(firstDay);
   int firstSunday = 1 + ((7 - firstDOW) % 7);
   return firstSunday + 7 * (nth - 1);
}

bool IsNewYorkDST531(datetime utcTime)
{
   MqlDateTime dt;
   TimeToStruct(utcTime, dt);

   if(dt.mon < 3 || dt.mon > 11)
      return false;
   if(dt.mon > 3 && dt.mon < 11)
      return true;

   int marchSecondSunday = NthSundayDay531(dt.year, 3, 2);
   int novemberFirstSunday = NthSundayDay531(dt.year, 11, 1);

   if(dt.mon == 3)
      return (dt.day >= marchSecondSunday);

   if(dt.mon == 11)
      return (dt.day < novemberFirstSunday);

   return false;
}

datetime PlusSessionClockTime531()
{
   if(!PlusUseUTCSessionClock)
      return TimeCurrent();

   // TimeCurrent is broker server time. Subtract broker UTC offset to get UTC clock.
   return TimeCurrent() - PlusServerUTCOffsetHours * 3600;
}

bool SessionPlusOK531()
{
   if(!UseSessionPlus531)
      return true;

   datetime sessionTime = PlusSessionClockTime531();

   MqlDateTime dt;
   TimeToStruct(sessionTime, dt);
   int h = dt.hour;

   bool ok = false;

   if(PlusTradeAsia && HourInRange531Plus(h, PlusAsiaStartHour, PlusAsiaEndHour))
      ok = true;

   if(PlusTradeEurope && HourInRange531Plus(h, PlusEuropeStartHour, PlusEuropeEndHour))
      ok = true;

   int usStart = PlusUSStartHour;
   int usEnd   = PlusUSEndHour;

   if(PlusAutoNYDST_US && PlusUseUTCSessionClock)
   {
      if(IsNewYorkDST531(sessionTime))
      {
         usStart = PlusUSStartHourUTC_Summer;
         usEnd   = PlusUSEndHourUTC_Summer;
      }
      else
      {
         usStart = PlusUSStartHourUTC_Winter;
         usEnd   = PlusUSEndHourUTC_Winter;
      }
   }

   if(PlusTradeUS && HourInRange531Plus(h, usStart, usEnd))
      ok = true;

   if(PlusTradeBonusHour && HourInRange531Plus(h, PlusBonusStartHour, PlusBonusEndHour))
      ok = true;

   if(PlusBlockOutsideSessions)
      return ok;

   return true;
}

void PushPlusScore531(double rLike)
{
   int maxN = ArraySize(PlusEquityScores);
   for(int i = maxN - 1; i > 0; i--)
      PlusEquityScores[i] = PlusEquityScores[i - 1];

   PlusEquityScores[0] = rLike;
   if(PlusEquityScoreCount < maxN)
      PlusEquityScoreCount++;
}

double PlusScore531()
{
   int n = MathMin(PlusEquityScoreCount, PlusEquityLookbackDeals);
   if(n <= 0)
      return 0.0;

   double s = 0.0;
   for(int i = 0; i < n; i++)
      s += PlusEquityScores[i];

   return s;
}

void UpdatePlusRiskFromDeal531(ulong dealTicket)
{
   if(!UseRiskPlus531)
      return;

   long entry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT)
      return;

   string sym = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
   long magic = HistoryDealGetInteger(dealTicket, DEAL_MAGIC);

   if(sym != TradeSymbol || magic != MagicNumber)
      return;

   double net = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
              + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION)
              + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
              + HistoryDealGetDouble(dealTicket, DEAL_FEE);

   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskUnit = MathMax(1.0, bal * PlusRiskSafePercent / 100.0);

   PushPlusScore531(net / riskUnit);

   if(net < 0.0)
      PlusLossDealStreak++;
   else if(net > 0.0)
      PlusLossDealStreak = 0;

   if(PlusLossDealStreak >= PlusMaxLosingDealsStreak)
   {
      int sec = PeriodSeconds(InpExecTF);
      if(sec <= 0)
         sec = 300;

      PlusLockdownUntil = TimeCurrent() + PlusLockdownBars * sec;
      PlusLossDealStreak = 0;
   }
}

double EffectiveRiskPlus531()
{
   if(!UseRiskPlus531)
      return EffectiveRiskPercent();

   if(TimeCurrent() < PlusLockdownUntil)
   {
      PlusMode = "LOCKDOWN";
      PlusCurrentRiskPercent = PlusRiskLockdownPercent;
      return PlusCurrentRiskPercent;
   }

   double score = PlusScore531();

   if(score >= PlusGrowthScore)
   {
      PlusMode = "GROWTH";
      PlusCurrentRiskPercent = PlusRiskGrowthPercent;
      return PlusCurrentRiskPercent;
   }

   if(score <= PlusLockdownScore)
   {
      PlusMode = "LOCKDOWN";
      PlusCurrentRiskPercent = PlusRiskLockdownPercent;
      return PlusCurrentRiskPercent;
   }

   PlusMode = "SAFE";
   PlusCurrentRiskPercent = PlusRiskSafePercent;
   return PlusCurrentRiskPercent;
}

//====================================================================
// v5.31 SAFETY GUARD
//====================================================================
void InitSafetyGuard531()
{
   GuardPeakBalance531 = AccountInfoDouble(ACCOUNT_BALANCE);
   GuardLossDealStreak531 = 0;
   GuardPauseUntil531 = 0;
   GuardPauseReason531 = "";
}

void PauseTrading531(int bars, string reason)
{
   int sec = PeriodSeconds(InpExecTF);
   if(sec <= 0)
      sec = 300;

   GuardPauseUntil531 = TimeCurrent() + bars * sec;
   GuardPauseReason531 = reason;

   if(ResetPeakAfterDDPause531)
      GuardPeakBalance531 = AccountInfoDouble(ACCOUNT_BALANCE);

   if(PrintDebug)
      Print("v5.31 GUARD PAUSE: ", reason, " until ", TimeToString(GuardPauseUntil531, TIME_DATE | TIME_SECONDS));
}

bool TradingGuardOK531()
{
   if(!UseSafetyGuard531)
      return true;

   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(GuardPeakBalance531 <= 0.0)
      GuardPeakBalance531 = bal;

   if(bal > GuardPeakBalance531)
      GuardPeakBalance531 = bal;

   if(TimeCurrent() < GuardPauseUntil531)
   {
      if(PrintDebug)
         Print("v5.31 Guard blocked trading: ", GuardPauseReason531);
      return false;
   }

   if(UseBalanceDDGuard531 && GuardPeakBalance531 > 0.0)
   {
      double ddPct = (GuardPeakBalance531 - bal) / GuardPeakBalance531 * 100.0;
      if(ddPct >= MaxBalanceDDPausePct531)
      {
         PauseTrading531(PauseBarsAfterDD531, "BalanceDD " + DoubleToString(ddPct, 1) + "%");
         return false;
      }
   }

   return true;
}

void UpdateLossStreakGuard531(ulong dealTicket)
{
   if(!UseSafetyGuard531)
      return;

   long entry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT)
      return;

   string sym = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
   long magic = HistoryDealGetInteger(dealTicket, DEAL_MAGIC);

   if(sym != TradeSymbol || magic != MagicNumber)
      return;

   double net = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
              + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION)
              + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
              + HistoryDealGetDouble(dealTicket, DEAL_FEE);

   if(net < 0.0)
      GuardLossDealStreak531++;
   else if(net > 0.0)
      GuardLossDealStreak531 = 0;

   if(GuardLossDealStreak531 >= MaxLosingDealsInRow531)
   {
      PauseTrading531(PauseBarsAfterLossStreak531, "LossStreak " + IntegerToString(GuardLossDealStreak531));
      GuardLossDealStreak531 = 0;
   }
}

//====================================================================
// TRADE TRANSACTION HOOK
//====================================================================
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;

   ulong dealTicket = trans.deal;
   if(dealTicket == 0)
      return;

   if(!HistoryDealSelect(dealTicket))
      return;

   AppendClosedTradeCSV(dealTicket);
   UpdateLossStreakGuard531(dealTicket);
   UpdatePlusRiskFromDeal531(dealTicket);
}

//====================================================================
// INIT / DEINIT / TICK
//====================================================================

//====================================================================
// VISUAL ONLY HELPERS v1
// This block draws dashboard/key levels only. It does not call trade.Buy/Sell,
// does not change filters, does not change risk, and does not modify signals.
//====================================================================
string VO_Name531(string suffix)
{
   return "SILVER_VO531_" + suffix;
}

void VO_DeleteObjects531()
{
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string n = ObjectName(0, i, -1, -1);
      if(StringFind(n, "SILVER_VO531_") == 0)
         ObjectDelete(0, n);
   }
}

void VO_ApplyTheme531()
{
   if(!VO_ApplyChartTheme531) return;

   ChartSetInteger(0, CHART_MODE, CHART_CANDLES);
   ChartSetInteger(0, CHART_SHOW_GRID, false);
   ChartSetInteger(0, CHART_COLOR_BACKGROUND, clrBlack);
   ChartSetInteger(0, CHART_COLOR_FOREGROUND, clrDimGray);
   ChartSetInteger(0, CHART_COLOR_GRID, clrBlack);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BULL, clrDodgerBlue);
   ChartSetInteger(0, CHART_COLOR_CHART_UP, clrDodgerBlue);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BEAR, clrWhite);
   ChartSetInteger(0, CHART_COLOR_CHART_DOWN, clrWhite);
   ChartSetInteger(0, CHART_COLOR_BID, clrDimGray);
   ChartSetInteger(0, CHART_COLOR_ASK, clrTomato);
   ChartSetInteger(0, CHART_COLOR_STOP_LEVEL, clrGold);
}

void VO_Rect531(string id, int x, int y, int w, int h, color bg, color border)
{
   string name = VO_Name531(id);
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, 0);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, border);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

void VO_Text531(string id, string text, int x, int y, int fs, color clr, string font = "Segoe UI")
{
   string name = VO_Name531(id);
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, 0);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fs);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

void VO_Pill531(string id, string text, int x, int y, int w, color bg, color clr)
{
   VO_Rect531("PILL_" + id, x, y, w, 20, bg, bg);
   VO_Text531("PILLTXT_" + id, text, x + 8, y + 3, 8, clr, "Segoe UI Semibold");
}

void VO_Line531(string id, double price, color clr, string label, ENUM_LINE_STYLE style = STYLE_SOLID, int width = 1)
{
   if(price <= 0.0) return;

   string name = VO_Name531("LINE_" + id);
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);

   ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TEXT, label);

   string lab = VO_Name531("LAB_" + id);
   if(ObjectFind(0, lab) < 0)
      ObjectCreate(0, lab, OBJ_TEXT, 0, TimeCurrent(), price);

   ObjectSetInteger(0, lab, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, lab, OBJPROP_FONTSIZE, 8);
   ObjectSetString(0, lab, OBJPROP_FONT, "Segoe UI Semibold");
   ObjectSetString(0, lab, OBJPROP_TEXT, label + "  " + DoubleToString(price, _Digits));
   ObjectSetInteger(0, lab, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, lab, OBJPROP_HIDDEN, true);
   ObjectMove(0, lab, 0, TimeCurrent() + PeriodSeconds(InpExecTF) * 10, price);
}

bool VO_HourInRange531(int h, int startH, int endH)
{
   if(startH == endH) return true;
   if(startH < endH) return (h >= startH && h < endH);
   return (h >= startH || h < endH);
}

bool VO_SessionHours531(string sess, int &startH, int &endH)
{
   if(sess == "ASIA")
   {
      startH = PlusAsiaStartHour;
      endH = PlusAsiaEndHour;
      return true;
   }

   if(sess == "EUROPE")
   {
      startH = PlusEuropeStartHour;
      endH = PlusEuropeEndHour;
      return true;
   }

   if(sess == "US")
   {
      startH = PlusUSStartHour;
      endH = PlusUSEndHour;

      if(PlusAutoNYDST_US && PlusUseUTCSessionClock)
      {
         if(IsNewYorkDST531(PlusSessionClockTime531()))
         {
            startH = PlusUSStartHourUTC_Summer;
            endH = PlusUSEndHourUTC_Summer;
         }
         else
         {
            startH = PlusUSStartHourUTC_Winter;
            endH = PlusUSEndHourUTC_Winter;
         }
      }
      return true;
   }

   if(sess == "BONUS")
   {
      startH = PlusBonusStartHour;
      endH = PlusBonusEndHour;
      return true;
   }

   return false;
}

string VO_CurrentSession531()
{
   datetime sessionTime = PlusSessionClockTime531();

   MqlDateTime dt;
   TimeToStruct(sessionTime, dt);
   int h = dt.hour;

   int startH = 0, endH = 0;

   if(PlusTradeAsia && VO_SessionHours531("ASIA", startH, endH) && VO_HourInRange531(h, startH, endH)) return "ASIA";
   if(PlusTradeEurope && VO_SessionHours531("EUROPE", startH, endH) && VO_HourInRange531(h, startH, endH)) return "EUROPE";
   if(PlusTradeUS && VO_SessionHours531("US", startH, endH) && VO_HourInRange531(h, startH, endH)) return "US";
   if(PlusTradeBonusHour && VO_SessionHours531("BONUS", startH, endH) && VO_HourInRange531(h, startH, endH)) return "BONUS";

   return "WAIT";
}

void VO_CurrentSessionHL531(double &sh, double &sl)
{
   sh = 0.0;
   sl = 0.0;

   string sess = VO_CurrentSession531();
   if(sess == "WAIT") return;

   int startH = 0, endH = 0;
   if(!VO_SessionHours531(sess, startH, endH))
      return;

   datetime sessionTime = PlusSessionClockTime531();

   MqlDateTime dt;
   TimeToStruct(sessionTime, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime sessionDayStart = StructToTime(dt);

   datetime t1SessionClock = sessionDayStart + startH * 3600;

   if(startH > endH)
   {
      if(sessionTime < sessionDayStart + endH * 3600)
         t1SessionClock = sessionDayStart - 86400 + startH * 3600;
   }

   datetime t1Server = t1SessionClock;
   if(PlusUseUTCSessionClock)
      t1Server = t1SessionClock + PlusServerUTCOffsetHours * 3600;

   int bars = Bars(TradeSymbol, InpExecTF);
   for(int i = 1; i < MathMin(bars, 700); i++)
   {
      datetime bt = iTime(TradeSymbol, InpExecTF, i);
      if(bt < t1Server || bt > TimeCurrent()) continue;

      double hi = iHigh(TradeSymbol, InpExecTF, i);
      double lo = iLow(TradeSymbol, InpExecTF, i);
      if(sh == 0.0 || hi > sh) sh = hi;
      if(sl == 0.0 || lo < sl) sl = lo;
   }
}

void VO_DrawKeyLevels531()
{
   if(!VO_ShowKeyLevels531) return;

   datetime bar0 = iTime(TradeSymbol, InpExecTF, 0);
   if(bar0 == VO_LastBar531) return;
   VO_LastBar531 = bar0;

   // Clear only visual-only lines, never EA trade objects.
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string n = ObjectName(0, i, -1, -1);
      if(StringFind(n, "SILVER_VO531_LINE_") == 0 || StringFind(n, "SILVER_VO531_LAB_") == 0)
         ObjectDelete(0, n);
   }

   double keyPrices[20];
   int keyCount = 0;

   if(VO_ShowPDHPDL531)
   {
      double pdh = iHigh(TradeSymbol, PERIOD_D1, 1);
      double pdl = iLow(TradeSymbol, PERIOD_D1, 1);

      if(pdh > 0.0)
      {
         VO_Line531("PDH", pdh, clrDeepSkyBlue, "PDH", STYLE_SOLID, 2);
         keyPrices[keyCount++] = pdh;
      }

      if(pdl > 0.0)
      {
         VO_Line531("PDL", pdl, clrOrange, "PDL", STYLE_SOLID, 2);
         keyPrices[keyCount++] = pdl;
      }

      if(VO_ShowEQ531 && pdh > 0.0 && pdl > 0.0)
      {
         double eq = (pdh + pdl) / 2.0;
         VO_Line531("EQ", eq, clrDimGray, "EQ 0.5", STYLE_DASH, 1);
         keyPrices[keyCount++] = eq;
      }
   }

   if(VO_ShowSessionHL531)
   {
      double sh, sl;
      VO_CurrentSessionHL531(sh, sl);

      bool okH = (sh > 0.0);
      bool okL = (sl > 0.0);

      for(int k = 0; k < keyCount; k++)
      {
         if(okH && MathAbs(sh - keyPrices[k]) < VO_MinLineDistance531) okH = false;
         if(okL && MathAbs(sl - keyPrices[k]) < VO_MinLineDistance531) okL = false;
      }

      if(okH)
      {
         VO_Line531("SESS_H", sh, clrMediumPurple, "Session High", STYLE_DASHDOT, 1);
         keyPrices[keyCount++] = sh;
      }

      if(okL)
      {
         VO_Line531("SESS_L", sl, clrMediumPurple, "Session Low", STYLE_DASHDOT, 1);
         keyPrices[keyCount++] = sl;
      }
   }

   if(VO_ShowNearestHTFSNR531)
   {
      double price = SymbolInfoDouble(TradeSymbol, SYMBOL_BID);

      double nearestRes = 0.0;
      double nearestSup = 0.0;
      string resTF = "";
      string supTF = "";

      for(int i = 0; i < levelCount; i++)
      {
         double lv = levels[i].price;
         if(lv <= 0.0) continue;
         if(MathAbs(lv - price) > VO_SNRNearPrice531) continue;

         bool tooClose = false;
         for(int k = 0; k < keyCount; k++)
            if(MathAbs(lv - keyPrices[k]) < VO_MinLineDistance531)
               tooClose = true;

         if(tooClose) continue;

         if(lv > price && (nearestRes == 0.0 || lv < nearestRes))
         {
            nearestRes = lv;
            resTF = levels[i].tf;
         }

         if(lv < price && (nearestSup == 0.0 || lv > nearestSup))
         {
            nearestSup = lv;
            supTF = levels[i].tf;
         }
      }

      if(nearestRes > 0.0 && VO_MaxSNRLines531 >= 1)
         VO_Line531("HTF_RES", nearestRes, (color)0x4FA3FF, "Nearest HTF RES " + resTF, STYLE_DOT, 2);

      if(nearestSup > 0.0 && VO_MaxSNRLines531 >= 2)
         VO_Line531("HTF_SUP", nearestSup, (color)0xF5C542, "Nearest HTF SUP " + supTF, STYLE_DOT, 2);
   }
}

string VO_SetupHint531()
{
   double close1 = iClose(TradeSymbol, InpExecTF, 1);
   double open1  = iOpen(TradeSymbol, InpExecTF, 1);
   double high1  = iHigh(TradeSymbol, InpExecTF, 1);
   double low1   = iLow(TradeSymbol, InpExecTF, 1);
   double body   = MathAbs(close1 - open1);

   if(body >= ExpansionCandleBodyMin)
      return (close1 > open1 ? "Bull displacement" : "Bear displacement");

   string sess = VO_CurrentSession531();
   if(sess == "WAIT") return "Waiting session";

   return "Scanning confluence";
}

void VO_UpdateDashboard531()
{
   if(!VO_ShowDashboard531) return;

   if(TimeCurrent() - VO_LastUpdate531 < VO_UpdateSeconds531)
      return;
   VO_LastUpdate531 = TimeCurrent();

   VO_ApplyTheme531();
   VO_DrawKeyLevels531();

   int x = VO_DashboardX531;
   int y = VO_DashboardY531;
   int w = VO_DashboardW531;
   int h = VO_DashboardH531;

   color bg      = (color)0x201713;
   color bg2     = (color)0x2B211C;
   color border  = clrDodgerBlue;
   color muted   = (color)0xA8A8A8;

   VO_Rect531("SHADOW", x + 4, y + 4, w, h, (color)0x101010, (color)0x101010);
   VO_Rect531("PANEL", x, y, w, h, bg, border);

   VO_Text531("TITLE", "SILVER MSNR", x + 14, y + 9, 12, clrAqua, "Segoe UI Semibold");
   VO_Text531("VER", "v5.31Plus AEU  |  Clean Study", x + 128, y + 12, 8, muted, "Segoe UI");

   string sess = VO_CurrentSession531();
   color sessColor = (sess == "US" ? clrTomato : (sess == "EUROPE" ? clrDeepSkyBlue : (sess == "ASIA" ? clrGold : muted)));
   VO_Pill531("SESSION", sess + (sess == "WAIT" ? "" : " ACTIVE"), x + w - 118, y + 10, 104, (sess == "WAIT" ? bg2 : sessColor), clrWhite);

   string modeTxt = (UseRiskPlus531 ? PlusMode : "FIXED");
   color modeClr = (modeTxt == "GROWTH" ? clrLimeGreen : (modeTxt == "LOCKDOWN" ? clrTomato : clrGold));

   VO_Text531("M1", "MODE", x + 14, y + 43, 8, muted);
   VO_Text531("M1V", modeTxt, x + 58, y + 43, 9, modeClr, "Segoe UI Semibold");

   VO_Text531("R1", "RISK", x + 142, y + 43, 8, muted);
   VO_Text531("R1V", DoubleToString(EffectiveRiskPlus531(), 2) + "%", x + 184, y + 43, 9, clrWhite, "Segoe UI Semibold");

   double spr = (SymbolInfoDouble(TradeSymbol, SYMBOL_ASK) - SymbolInfoDouble(TradeSymbol, SYMBOL_BID));
   VO_Text531("S1", "SPR", x + 282, y + 43, 8, muted);
   VO_Text531("S1V", DoubleToString(spr, 2), x + 318, y + 43, 9, (spr <= MaxSpreadPoints * _Point ? clrLimeGreen : clrTomato), "Segoe UI Semibold");

   VO_Text531("K1", "KEY LEVELS", x + 14, y + 68, 8, muted);
   VO_Text531("K1V", "PDH/PDL + Session H-L + nearest HTF SNR", x + 96, y + 68, 8, clrDeepSkyBlue, "Segoe UI");

   VO_Text531("H1", "BOT READ", x + 14, y + 93, 8, muted);
   VO_Text531("H1V", VO_SetupHint531(), x + 86, y + 93, 9, clrWhite, "Segoe UI Semibold");

   VO_Pill531("ASIA", "ASIA 23-04", x + w + 10, y + 8, 88, bg2, clrGold);
   VO_Pill531("EU", "EU 07-11", x + w + 10, y + 34, 88, bg2, clrDeepSkyBlue);
   string usPill = "US " + (IsNewYorkDST531(PlusSessionClockTime531()) ? "12-16" : "13-17");
   VO_Pill531("US", usPill, x + w + 10, y + 60, 88, bg2, clrTomato);

   if(!VO_NoForcedChartRedraw531)
      ChartRedraw(0);
}

void OnTimer()
{
   VO_UpdateDashboard531();
}


int OnInit()
{
   EventSetTimer(1);
   TradeSymbol = InpSymbol;
   if(TradeSymbol == "")
      TradeSymbol = _Symbol;

   if(!SymbolSelect(TradeSymbol, true))
   {
      Print("Cannot select symbol: ", TradeSymbol, " | Tester symbol: ", _Symbol);
      return INIT_FAILED;
   }

   if(!ExecutionTimeframeOK())
   {
      Print("Invalid execution timeframe: ", TFToString(InpExecTF), ". This version only allows M5.");
      return INIT_FAILED;
   }

   if(Bars(TradeSymbol, InpExecTF) < 50)
   {
      Print("Not enough bars on ", TradeSymbol, " ", TFToString(InpExecTF), ". Please download history or use a wider test range.");
      return INIT_FAILED;
   }

   InitialBalanceForCSV = AccountInfoDouble(ACCOUNT_BALANCE);
   EnsureClosedTradeCSVHeader();

   trade.SetExpertMagicNumber(MagicNumber);
   InitSafetyGuard531();

   BuildLevels();
   DrawSNRLevels();

   lastBarTime = iTime(TradeSymbol, InpExecTF, 0);

   Print("Silver_MSNR_v531Plus_AsiaEuropeUS initialized.");
   Print("Tester symbol: ", _Symbol, " | TradeSymbol: ", TradeSymbol, " | ExecTF: ", TFToString(InpExecTF));
   PrintExportPaths();
   Print("Levels: ", levelCount,
         " | Base risk per order: ", DoubleToString(RiskPercentPerOrder, 2),
         "% | v5.32 Fixed AEU: v5.31 core + Asia/Europe/US sessions ON + bonus OFF + risk curve | MinSetupsForTrade: ", MinSetupsForTrade);

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   VO_DeleteObjects531();
   ExportBacktestDealsCSV();
   ClearDrawings();
   Print("Silver_MSNR_v531Plus_AsiaEuropeUS stopped. Reason: ", reason);
}

void OnTick()
{
   VO_UpdateDashboard531();
   ManagePartialAt4R();

   if(BlockWrongChartTimeframe && _Period != InpExecTF)
   {
      if(PrintDebug)
         Print("Chart timeframe blocked. Attach EA to ", TFToString(InpExecTF),
               ". Current chart is ", TFToString((ENUM_TIMEFRAMES)_Period));
      return;
   }

   bool newbar = IsNewBar();
   if(!newbar) return;

   BuildLevels();
   DrawSNRLevels();

   if(!SessionOK())
   {
      if(PrintDebug) Print("Session filter blocked.");
      return;
   }

   if(!SpreadOK())
   {
      if(PrintDebug) Print("Spread too high.");
      return;
   }

   if(!SessionPlusOK531())
   {
      if(PrintDebug) Print("v5.32 Fixed session blocked.");
      return;
   }

   if(!TradingGuardOK531())
      return;

   ScanAndTradeConfluence();

   // v5.30: independent trend system
   // 1) detect and arm strong trend breaks
   // 2) enter on pullback / retest / FVG-CE continuation
   UpdateTrendArmAging530();
   ScanAndTradeStrongTrend529();
   ScanTrendPullbackEntry530();

   if(PrintDebug && ordersThisBar == 0)
      Print("No confluence, strong-trend or trend-pullback setup triggered. Levels scanned: ", levelCount);
}
//+------------------------------------------------------------------+