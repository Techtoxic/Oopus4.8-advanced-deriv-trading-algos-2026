#!/usr/bin/env python3
"""
Generates FableCOT.mqh — embedded weekly CFTC COT history for MT5 EAs.

Solves the user's core pain: MT5 Strategy Tester blocks WebRequest, making
COT-filtered EAs impossible to backtest. This embeds the REAL weekly history
(sign of non-commercial net + 3y rolling COT-index percentile, publish-lagged
+3 days) as compact arrays. In the tester the EA reads embedded history for
the exact simulated date; live it can still hit the COT API and fall back to
the embedded table.

Source: repo CSVs fetched from CFTC Socrata (legacy futures-only, 1986→present).
"""
import os
from datetime import datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
COTDIR = os.path.normpath(os.path.join(HERE, "..", "..", "currencies-metals-swing", "research", "data", "cot"))
OUT = os.path.normpath(os.path.join(HERE, "..", "..", "MQL5-ENHANCED", "FableCOT.mqh"))

ASSETS = ["GOLD", "SILVER", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "DXY"]
START = "2005-01-01"   # embed 2005→present (covers any realistic backtest)


def cot_index(s: pd.Series, weeks=156) -> pd.Series:
    lo = s.rolling(weeks, min_periods=26).min()
    hi = s.rolling(weeks, min_periods=26).max()
    return ((s - lo) / (hi - lo + 1e-12) * 100).clip(0, 100)


def main():
    blocks, counts = [], {}
    for a in ASSETS:
        df = pd.read_csv(os.path.join(COTDIR, f"{a}.csv"), parse_dates=["date"]).sort_values("date")
        df["idx"] = cot_index(df["noncomm_net"])
        df["avail"] = df["date"] + pd.Timedelta(days=3)
        df = df[df["avail"] >= START].dropna(subset=["idx"])
        dates = ",".join(f"D'{d:%Y.%m.%d}'" for d in df["avail"])
        # pack: sign in {-1,0,1} and idx 0..100  -> value = sign*1000 + idx
        vals = ",".join(str(int((1 if n > 0 else (-1 if n < 0 else 0)) * 1000 + round(i)))
                        for n, i in zip(df["noncomm_net"], df["idx"]))
        counts[a] = len(df)
        blocks.append(f"const datetime FCOT_{a}_T[{len(df)}] = {{{dates}}};\n"
                      f"const int      FCOT_{a}_V[{len(df)}] = {{{vals}}};")
    gen = datetime.utcnow().strftime("%Y-%m-%d")
    body = "\n".join(blocks)
    counts_s = ", ".join(f"{k}:{v}" for k, v in counts.items())

    mqh = f"""//+------------------------------------------------------------------+
//| FableCOT.mqh — embedded weekly CFTC COT history (auto-generated)  |
//| Generated {gen} from CFTC legacy futures-only reports.            |
//| Rows: {counts_s}
//|                                                                    |
//| WHY: the Strategy Tester blocks WebRequest, so COT-filtered EAs    |
//| could never be backtested. This include embeds the real publish-  |
//| lagged weekly history (sign of non-commercial net + 3y COT-index  |
//| percentile). Tester reads embedded data for the simulated date;   |
//| live code may still prefer the COT API and use this as fallback.  |
//|                                                                    |
//| API-style pair rule (matches cotapi.onrender.com/api/bias):        |
//|   BUY  = base specs net-long  AND quote specs net-short            |
//|   SELL = base specs net-short AND quote specs net-long             |
//|   else UNCERTAIN (0)                                               |
//| COT-index rule (validated in RESEARCH-2026-06-10/cot_study):       |
//|   long-friendly  when base 3y percentile > 55                      |
//|   short-friendly when base 3y percentile < 45                      |
//+------------------------------------------------------------------+
#property strict

{body}

struct FableCOTRow {{ int sign; int idx; bool found; }};

// packed value: sign*1000 + idx  (sign in -1/0/1, idx 0..100)
FableCOTRow FableCOT_Lookup(const datetime &times[], const int &vals[], int n, datetime when)
  {{
   FableCOTRow r; r.sign=0; r.idx=50; r.found=false;
   if(n<=0 || when < times[0]) return r;
   int lo=0, hi=n-1, best=-1;
   while(lo<=hi)
     {{
      int mid=(lo+hi)/2;
      if(times[mid] <= when) {{ best=mid; lo=mid+1; }} else hi=mid-1;
     }}
   if(best<0) return r;
   int v = vals[best];
   r.sign = (v>=500) ? 1 : (v<=-500 ? -1 : 0);
   r.idx  = MathAbs(v - r.sign*1000);
   r.found = true;
   return r;
  }}

FableCOTRow FableCOT_Asset(string asset, datetime when)
  {{
   FableCOTRow r; r.sign=0; r.idx=50; r.found=false;
   if(asset=="GOLD")   return FableCOT_Lookup(FCOT_GOLD_T,   FCOT_GOLD_V,   ArraySize(FCOT_GOLD_V),   when);
   if(asset=="SILVER") return FableCOT_Lookup(FCOT_SILVER_T, FCOT_SILVER_V, ArraySize(FCOT_SILVER_V), when);
   if(asset=="EUR")    return FableCOT_Lookup(FCOT_EUR_T,    FCOT_EUR_V,    ArraySize(FCOT_EUR_V),    when);
   if(asset=="GBP")    return FableCOT_Lookup(FCOT_GBP_T,    FCOT_GBP_V,    ArraySize(FCOT_GBP_V),    when);
   if(asset=="JPY")    return FableCOT_Lookup(FCOT_JPY_T,    FCOT_JPY_V,    ArraySize(FCOT_JPY_V),    when);
   if(asset=="AUD")    return FableCOT_Lookup(FCOT_AUD_T,    FCOT_AUD_V,    ArraySize(FCOT_AUD_V),    when);
   if(asset=="CAD")    return FableCOT_Lookup(FCOT_CAD_T,    FCOT_CAD_V,    ArraySize(FCOT_CAD_V),    when);
   if(asset=="NZD")    return FableCOT_Lookup(FCOT_NZD_T,    FCOT_NZD_V,    ArraySize(FCOT_NZD_V),    when);
   if(asset=="DXY")    return FableCOT_Lookup(FCOT_DXY_T,    FCOT_DXY_V,    ArraySize(FCOT_DXY_V),    when);
   return r;
  }}

// Map an MT5 symbol (with arbitrary broker suffix) to base/quote CFTC assets.
bool FableCOT_MapSymbol(string symbol, string &base, string &quote)
  {{
   string s = symbol; StringToUpper(s);
   if(StringFind(s,"XAU")>=0)            {{ base="GOLD";   quote="DXY"; return true; }}
   if(StringFind(s,"XAG")>=0)            {{ base="SILVER"; quote="DXY"; return true; }}
   string majors[8] = {{"EUR","GBP","JPY","AUD","CAD","NZD","CHF","USD"}};
   // find first and second currency codes in the symbol text
   string found1="", found2="";
   for(int i=0;i<8;i++)
     {{
      int p = StringFind(s, majors[i]);
      if(p>=0)
        {{
         if(found1=="" ) {{ found1=majors[i]; }}
         else if(majors[i]!=found1) {{ found2=majors[i]; break; }}
        }}
     }}
   if(found1=="" || found2=="") return false;
   base = (found1=="USD") ? "DXY" : found1;
   quote = (found2=="USD") ? "DXY" : found2;
   if(base=="CHF" || quote=="CHF") return false; // CHF not embedded
   return true;
  }}

// API-style sign bias: +1 BUY / -1 SELL / 0 UNCERTAIN
int FableCOT_Bias(string symbol, datetime when)
  {{
   string b,q;
   if(!FableCOT_MapSymbol(symbol,b,q)) return 0;
   FableCOTRow rb = FableCOT_Asset(b, when);
   FableCOTRow rq = FableCOT_Asset(q, when);
   if(!rb.found || !rq.found) return 0;
   if(rb.sign>0 && rq.sign<0) return  1;
   if(rb.sign<0 && rq.sign>0) return -1;
   return 0;
  }}

// Base-asset 3y COT-index percentile (0..100), 50 if unknown
int FableCOT_Index(string symbol, datetime when)
  {{
   string b,q;
   if(!FableCOT_MapSymbol(symbol,b,q)) return 50;
   FableCOTRow rb = FableCOT_Asset(b, when);
   return rb.found ? rb.idx : 50;
  }}

// Convenience trade-permission: does COT allow this direction?
// mode: 0=off(always true) 1=api-sign rule 2=cot-index rule 3=either 4=both
bool FableCOT_Allows(string symbol, datetime when, int direction, int mode, bool allow_uncertain=true)
  {{
   if(mode==0 || direction==0) return true;
   int bias = FableCOT_Bias(symbol, when);
   int idx  = FableCOT_Index(symbol, when);
   bool apiok = (bias==direction) || (allow_uncertain && bias==0);
   bool idxok = (direction>0) ? (idx>55 || (allow_uncertain && idx>=45 && idx<=55))
                              : (idx<45 || (allow_uncertain && idx>=45 && idx<=55));
   if(mode==1) return apiok;
   if(mode==2) return idxok;
   if(mode==3) return (apiok || idxok);
   return (apiok && idxok);
  }}
"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(mqh)
    print("written", OUT, f"{os.path.getsize(OUT)/1024:.0f} KB", counts)


if __name__ == "__main__":
    main()
