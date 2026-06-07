//+------------------------------------------------------------------+
//|                                       AdaptiveSwingSignal.mq5     |
//|   Non-repainting chart companion to AdaptiveSwingTrader.          |
//|                                                                  |
//|   Plots the adaptive SuperTrend regime line and prints BUY/SELL  |
//|   arrows using EXACTLY the EA's closed-bar logic. Arrows are only |
//|   committed on CLOSED bars, so nothing repaints: a printed signal |
//|   never moves or disappears.                                      |
//|                                                                  |
//|   Indicators cannot call WebRequest, so the COT bias here is a    |
//|   manual input you set from the COT API / dashboard. The EA does  |
//|   the live COT fetch; this is your eyes-on confirmation tool.     |
//+------------------------------------------------------------------+
#property copyright "2026 — Adaptive Swing"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 7
#property indicator_plots   3

#property indicator_label1  "SwingTrend"
#property indicator_type1   DRAW_COLOR_LINE
#property indicator_color1  clrLime,clrRed
#property indicator_width1  2
#property indicator_label2  "Buy"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrAqua
#property indicator_width2  2
#property indicator_label3  "Sell"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrMagenta
#property indicator_width3  2

input group "════ Trend Regime ════"
input int    InpST_Period   = 10;
input double InpST_Mult     = 3.0;
input int    InpEMA_Trend   = 50;
input int    InpADX_Period  = 14;
input double InpADX_Min     = 18.0;
input group "════ Volatility Gate ════"
input int    InpATR_Period   = 14;
input int    InpATR_PctWindow= 100;
input double InpATR_PctMin    = 0.05;
input double InpATR_PctMax    = 0.97;
input group "════ Entries ════"
enum ENUM_ENTRY_MODE { ENTRY_PULLBACK=0, ENTRY_BREAKOUT=1, ENTRY_EITHER=2 };
input ENUM_ENTRY_MODE InpEntryMode = ENTRY_EITHER;
input int    InpEMA_Pullback = 20;
input int    InpPullbackLook = 6;
input int    InpDonchian     = 20;
input group "════ COT (manual) ════"
input int    InpManualCOT   = 0;        // 1 buy / -1 sell / 0 off — set from COT API
input bool   InpCOTFilter   = false;    // hide arrows that disagree with COT
input group "════ Display ════"
input int    InpMaxBars     = 2000;     // bars to compute
input bool   InpShowDash    = true;

double BufST[], BufCol[], BufBuy[], BufSell[], BufFU[], BufFL[], BufDir[];
int hATR=INVALID_HANDLE,hADX=INVALID_HANDLE,hEMAt=INVALID_HANDLE,hEMAp=INVALID_HANDLE;

int OnInit()
{
   SetIndexBuffer(0,BufST,INDICATOR_DATA);
   SetIndexBuffer(1,BufCol,INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2,BufBuy,INDICATOR_DATA);
   SetIndexBuffer(3,BufSell,INDICATOR_DATA);
   SetIndexBuffer(4,BufFU,INDICATOR_CALCULATIONS);
   SetIndexBuffer(5,BufFL,INDICATOR_CALCULATIONS);
   SetIndexBuffer(6,BufDir,INDICATOR_CALCULATIONS);
   PlotIndexSetInteger(1,PLOT_ARROW,233);
   PlotIndexSetInteger(2,PLOT_ARROW,234);
   PlotIndexSetDouble(1,PLOT_EMPTY_VALUE,EMPTY_VALUE);
   PlotIndexSetDouble(2,PLOT_EMPTY_VALUE,EMPTY_VALUE);

   hATR  = iATR(_Symbol,_Period,InpATR_Period);
   hADX  = iADX(_Symbol,_Period,InpADX_Period);
   hEMAt = iMA(_Symbol,_Period,InpEMA_Trend,0,MODE_EMA,PRICE_CLOSE);
   hEMAp = iMA(_Symbol,_Period,InpEMA_Pullback,0,MODE_EMA,PRICE_CLOSE);
   if(hATR==INVALID_HANDLE||hADX==INVALID_HANDLE||hEMAt==INVALID_HANDLE||hEMAp==INVALID_HANDLE)
      return INIT_FAILED;
   IndicatorSetString(INDICATOR_SHORTNAME,"Adaptive Swing Signal");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hATR); IndicatorRelease(hADX);
   IndicatorRelease(hEMAt); IndicatorRelease(hEMAp);
   ObjectsDeleteAll(0,"ASS_");
}

int OnCalculate(const int rates_total,const int prev_calculated,
                const datetime &time[],const double &open[],const double &high[],
                const double &low[],const double &close[],const long &tick_volume[],
                const long &volume[],const int &spread[])
{
   int win=MathMin(rates_total,InpMaxBars);
   if(win < InpATR_PctWindow+InpEMA_Trend+5) return prev_calculated;

   // work in series space (0 = newest)
   double atr[],adx[],emaT[],emaP[],hi[],lo[],cl[],op[];
   ArraySetAsSeries(atr,true);ArraySetAsSeries(adx,true);ArraySetAsSeries(emaT,true);ArraySetAsSeries(emaP,true);
   ArraySetAsSeries(hi,true);ArraySetAsSeries(lo,true);ArraySetAsSeries(cl,true);ArraySetAsSeries(op,true);
   if(CopyBuffer(hATR,0,0,win,atr)<win) return prev_calculated;
   if(CopyBuffer(hADX,0,0,win,adx)<win) return prev_calculated;
   if(CopyBuffer(hEMAt,0,0,win,emaT)<win) return prev_calculated;
   if(CopyBuffer(hEMAp,0,0,win,emaP)<win) return prev_calculated;
   if(CopyHigh(_Symbol,_Period,0,win,hi)<win) return prev_calculated;
   if(CopyLow(_Symbol,_Period,0,win,lo)<win) return prev_calculated;
   if(CopyClose(_Symbol,_Period,0,win,cl)<win) return prev_calculated;
   if(CopyOpen(_Symbol,_Period,0,win,op)<win) return prev_calculated;

   ArraySetAsSeries(BufST,true);ArraySetAsSeries(BufCol,true);
   ArraySetAsSeries(BufBuy,true);ArraySetAsSeries(BufSell,true);
   ArraySetAsSeries(BufFU,true);ArraySetAsSeries(BufFL,true);ArraySetAsSeries(BufDir,true);

   // SuperTrend over window (oldest=win-1 -> newest=0)
   int o=win-1;
   double hl2=(hi[o]+lo[o])/2.0;
   BufFU[o]=hl2+InpST_Mult*atr[o]; BufFL[o]=hl2-InpST_Mult*atr[o];
   BufST[o]=BufFU[o]; BufDir[o]=-1; BufCol[o]=1; BufBuy[o]=EMPTY_VALUE; BufSell[o]=EMPTY_VALUE;
   for(int i=win-2;i>=0;i--)
   {
      hl2=(hi[i]+lo[i])/2.0;
      double bUp=hl2+InpST_Mult*atr[i], bLo=hl2-InpST_Mult*atr[i];
      BufFU[i]=(bUp<BufFU[i+1]||cl[i+1]>BufFU[i+1])?bUp:BufFU[i+1];
      BufFL[i]=(bLo>BufFL[i+1]||cl[i+1]<BufFL[i+1])?bLo:BufFL[i+1];
      if(BufST[i+1]==BufFU[i+1]) BufST[i]=(cl[i]>BufFU[i])?BufFL[i]:BufFU[i];
      else                       BufST[i]=(cl[i]<BufFL[i])?BufFU[i]:BufFL[i];
      BufDir[i]=(BufST[i]==BufFL[i])?1:-1;
      BufCol[i]=(BufDir[i]==1)?0:1;
      BufBuy[i]=EMPTY_VALUE; BufSell[i]=EMPTY_VALUE;
   }

   // signals — ONLY on closed bars (i>=1). The forming bar (0) stays blank.
   for(int i=1;i<win-MathMax(InpDonchian,InpATR_PctWindow)-2;i++)
   {
      double atrPct=PctRank(atr,i,InpATR_PctWindow);
      bool volOK=(atrPct>=InpATR_PctMin && atrPct<=InpATR_PctMax);
      bool strong=adx[i]>=InpADX_Min;
      bool trendUp=(BufDir[i]==1)&&(cl[i]>emaT[i])&&strong&&volOK;
      bool trendDn=(BufDir[i]==-1)&&(cl[i]<emaT[i])&&strong&&volOK;
      if(!trendUp && !trendDn) continue;

      bool dipL=false,dipS=false;
      for(int k=i;k<i+InpPullbackLook && k<win;k++){ if(lo[k]<=emaP[k])dipL=true; if(hi[k]>=emaP[k])dipS=true; }
      bool bull=(cl[i]>op[i])&&(cl[i]>cl[i+1]);
      bool bear=(cl[i]<op[i])&&(cl[i]<cl[i+1]);
      double dHi=hi[i+1],dLo=lo[i+1];
      for(int k=i+1;k<=i+InpDonchian && k<win;k++){ dHi=MathMax(dHi,hi[k]); dLo=MathMin(dLo,lo[k]); }

      bool lP=trendUp&&dipL&&bull&&(cl[i]>=emaP[i]);
      bool sP=trendDn&&dipS&&bear&&(cl[i]<=emaP[i]);
      bool lB=trendUp&&(cl[i]>dHi)&&bull;
      bool sB=trendDn&&(cl[i]<dLo)&&bear;
      bool L,Sg;
      if(InpEntryMode==ENTRY_PULLBACK){L=lP;Sg=sP;}
      else if(InpEntryMode==ENTRY_BREAKOUT){L=lB;Sg=sB;}
      else {L=lP||lB;Sg=sP||sB;}

      if(InpCOTFilter && InpManualCOT!=0){ if(InpManualCOT==-1)L=false; if(InpManualCOT==1)Sg=false; }

      if(L)  BufBuy[i]=lo[i]-1.2*atr[i]*0.25;
      if(Sg) BufSell[i]=hi[i]+1.2*atr[i]*0.25;
   }

   if(InpShowDash) Dash(adx[1],PctRank(atr,1,InpATR_PctWindow),(int)BufDir[1]);
   return rates_total;
}

double PctRank(const double &arr[],int at,int window)
{
   int cnt=0,le=0;
   for(int k=at;k<at+window;k++){ if(k>=ArraySize(arr))break; cnt++; if(arr[k]<=arr[at])le++; }
   return cnt>0?(double)le/cnt:0.5;
}

void Dash(double adxV,double atrPct,int dir)
{
   string n="ASS_dash";
   string bias=InpManualCOT==1?"BUY":(InpManualCOT==-1?"SELL":"off");
   string txt=StringFormat("ADAPTIVE SWING  |  trend %s  ADX %.0f  ATR%%ile %.0f  COT(man) %s",
              dir==1?"UP":"DOWN",adxV,atrPct*100.0,bias);
   if(ObjectFind(0,n)<0)
   {
      ObjectCreate(0,n,OBJ_LABEL,0,0,0);
      ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER_LEFT_UPPER);
      ObjectSetInteger(0,n,OBJPROP_XDISTANCE,12);
      ObjectSetInteger(0,n,OBJPROP_YDISTANCE,20);
      ObjectSetInteger(0,n,OBJPROP_FONTSIZE,9);
      ObjectSetString(0,n,OBJPROP_FONT,"Consolas");
      ObjectSetInteger(0,n,OBJPROP_COLOR,clrGainsboro);
      ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   }
   ObjectSetString(0,n,OBJPROP_TEXT,txt);
}
//+------------------------------------------------------------------+
