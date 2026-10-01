# Universe screen — every active symbol

92 symbols; digit-enabled: 20; accumulator-enabled: 27

## Digit symbols — tick-step physics (5k ticks each)

| symbol | spot | pip dec | sigma (pips) | TV(step mod 10) | parity flip | max cond dev | TV(digits) |
|---|---:|---:|---:|---:|---:|---:|---:|
| JD100 | 260.84 | 2 | 4.8 | 0.0219 | 0.4903 | 0.0354 | 0.0176 |
| R_100 | 354.9 | 2 | 8.9 | 0.0229 | 0.5193 | 0.0361 | 0.0210 |
| 1HZ100V | 714.04 | 2 | 12.6 | 0.0183 | 0.5061 | 0.0379 | 0.0220 |
| 1HZ10V | 10172.35 | 2 | 17.8 | 0.0115 | 0.5041 | 0.0282 | 0.0194 |
| 1HZ75V | 5327.38 | 2 | 71.1 | 0.0187 | 0.5003 | 0.0426 | 0.0242 |
| JD75 | 8017.74 | 2 | 112.2 | 0.0157 | 0.5045 | 0.0387 | 0.0202 |
| R_50 | 95.8909 | 4 | 119.9 | 0.0173 | 0.5093 | 0.0404 | 0.0092 |
| R_10 | 4891.477 | 3 | 123.2 | 0.0185 | 0.5005 | 0.0347 | 0.0164 |
| JD10 | 95707.97 | 2 | 174.5 | 0.0127 | 0.4889 | 0.0390 | 0.0192 |
| R_25 | 2796.291 | 3 | 175.2 | 0.0123 | 0.5025 | 0.0393 | 0.0182 |
| 1HZ15V | 12600.766 | 3 | 339.7 | 0.0131 | 0.4961 | 0.0379 | 0.0076 |
| 1HZ30V | 7729.407 | 3 | 417.3 | 0.0207 | 0.4991 | 0.0472 | 0.0346 |
| JD50 | 53506.89 | 2 | 634.5 | 0.0203 | 0.5013 | 0.0345 | 0.0200 |
| JD25 | 119723.82 | 2 | 893.0 | 0.0163 | 0.4957 | 0.0368 | 0.0132 |
| 1HZ90V | 8145.34 | 3 | 1302.6 | 0.0137 | 0.5027 | 0.0327 | 0.0188 |
| 1HZ50V | 273947.75 | 2 | 2403.4 | 0.0231 | 0.5081 | 0.0371 | 0.0176 |
| 1HZ25V | 784813.46 | 2 | 3427.9 | 0.0185 | 0.5005 | 0.0363 | 0.0120 |
| RDBEAR | 1129.5798 | 4 | 4425.8 | 0.0155 | 0.5033 | 0.0456 | 0.0112 |
| RDBULL | 1064.6335 | 4 | 4675.6 | 0.0115 | 0.4901 | 0.0257 | 0.0158 |
| R_75 | 34509.4786 | 4 | 65000.7 | 0.0131 | 0.4895 | 0.0392 | 0.0150 |

sigma >= ~8 pips => step mod 10 is indistinguishable from uniform (wrapped-normal flatness ~ 2·exp(-2π²(σ/10)²)); structural conditioning is dead. sigma <= ~5 pips => exploitable concentration would appear in TV(step mod 10) and max_cond_dev.

## Non-digit symbols offering other exploitables

- WLDAUD (AUD Basket, forex_basket): callput, callputequal, multiplier
- frxAUDCAD (AUD/CAD, minor_pairs): callput, callputequal
- frxAUDCHF (AUD/CHF, minor_pairs): callput, callputequal
- frxAUDJPY (AUD/JPY, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxAUDNZD (AUD/NZD, minor_pairs): callput, callputequal
- frxAUDUSD (AUD/USD, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- OTC_AS51 (Australia 200, asia_oceania_OTC): callput, endsinout, staysinout, touchnotouch
- cryBTCUSD (BTC/USD, non_stable_coin): multiplier
- BOOM50 (Boom 50 Index, crash_index): accumulator, multiplier
- BOOM150N (Boom 150 Index, crash_index): accumulator, multiplier
- BOOM300N (Boom 300 Index, crash_index): accumulator, multiplier
- BOOM500 (Boom 500 Index, crash_index): accumulator, multiplier
- BOOM600 (Boom 600 Index, crash_index): accumulator, multiplier
- BOOM900 (Boom 900 Index, crash_index): accumulator, multiplier
- BOOM1000 (Boom 1000 Index, crash_index): accumulator, multiplier
- CRASH50 (Crash 50 Index, crash_index): accumulator, multiplier
- CRASH150N (Crash 150 Index, crash_index): accumulator, multiplier
- CRASH300N (Crash 300 Index, crash_index): accumulator, multiplier
- CRASH500 (Crash 500 Index, crash_index): accumulator, multiplier
- CRASH600 (Crash 600 Index, crash_index): accumulator, multiplier
- CRASH900 (Crash 900 Index, crash_index): accumulator, multiplier
- CRASH1000 (Crash 1000 Index, crash_index): accumulator, multiplier
- cryETHUSD (ETH/USD, non_stable_coin): multiplier
- WLDEUR (EUR Basket, forex_basket): callput, callputequal, multiplier
- frxEURAUD (EUR/AUD, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxEURCAD (EUR/CAD, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxEURCHF (EUR/CHF, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxEURGBP (EUR/GBP, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxEURJPY (EUR/JPY, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxEURNZD (EUR/NZD, minor_pairs): callput, callputequal
- frxEURUSD (EUR/USD, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- OTC_SX5E (Euro 50, europe_OTC): callput, endsinout, staysinout, touchnotouch
- OTC_FCHI (France 40, europe_OTC): callput, endsinout, staysinout, touchnotouch
- WLDGBP (GBP Basket, forex_basket): callput, callputequal, multiplier
- frxGBPAUD (GBP/AUD, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxGBPCAD (GBP/CAD, minor_pairs): callput, callputequal
- frxGBPCHF (GBP/CHF, minor_pairs): callput, callputequal
- frxGBPJPY (GBP/JPY, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxGBPNOK (GBP/NOK, minor_pairs): callput, callputequal
- frxGBPNZD (GBP/NZD, minor_pairs): callput, callputequal
- frxGBPUSD (GBP/USD, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- OTC_GDAXI (Germany 40, europe_OTC): callput, endsinout, staysinout, touchnotouch
- WLDXAU (Gold Basket, commodity_basket): callput, callputequal, multiplier
- frxXAUUSD (Gold/USD, metals): callput, endsinout, multiplier, staysinout, touchnotouch
- OTC_HSI (Hong Kong 50, asia_oceania_OTC): callput, endsinout, staysinout, touchnotouch
- OTC_N225 (Japan 225, asia_oceania_OTC): callput, endsinout, staysinout, touchnotouch
- frxNZDJPY (NZD/JPY, minor_pairs): callput, callputequal
- frxNZDUSD (NZD/USD, minor_pairs): callput, callputequal
- OTC_AEX (Netherlands 25, europe_OTC): callput, endsinout, staysinout, touchnotouch
- frxXPDUSD (Palladium/USD, metals): callput
- frxXPTUSD (Platinum/USD, metals): callput
- RB100 (Range Break 100 Index, range_index): multiplier
- RB200 (Range Break 200 Index, range_index): multiplier
- frxXAGUSD (Silver/USD, metals): callput, endsinout, multiplier, staysinout, touchnotouch
- stpRNG (Step Index 100, step_index): callput, callputequal, multiplier
- stpRNG2 (Step Index 200, step_index): callput, callputequal, multiplier
- stpRNG3 (Step Index 300, step_index): callput, callputequal, multiplier
- stpRNG4 (Step Index 400, step_index): callput, callputequal, multiplier
- stpRNG5 (Step Index 500, step_index): callput, callputequal, multiplier
- OTC_SSMI (Swiss 20, europe_OTC): callput, endsinout, staysinout, touchnotouch
- OTC_FTSE (UK 100, europe_OTC): callput, endsinout, staysinout, touchnotouch
- OTC_SPC (US 500, americas_OTC): callput, endsinout, staysinout, touchnotouch
- OTC_NDX (US Tech 100, americas_OTC): callput, endsinout, staysinout, touchnotouch
- WLDUSD (USD Basket, forex_basket): callput, callputequal, multiplier
- frxUSDCAD (USD/CAD, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxUSDCHF (USD/CHF, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxUSDJPY (USD/JPY, major_pairs): callput, callputequal, endsinout, multiplier, staysinout, touchnotouch
- frxUSDMXN (USD/MXN, minor_pairs): callput, callputequal
- frxUSDNOK (USD/NOK, minor_pairs): callput, callputequal
- frxUSDPLN (USD/PLN, minor_pairs): callput, callputequal
- frxUSDSEK (USD/SEK, minor_pairs): callput, callputequal
- OTC_DJI (Wall Street 30, americas_OTC): callput, endsinout, staysinout, touchnotouch