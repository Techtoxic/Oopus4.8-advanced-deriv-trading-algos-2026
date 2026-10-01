# Payout audit — JD100

Proposal payout vs EXECUTED payout (from the buy response, which carries the contracted payout).

**Quote and fill are taken from the SAME session.** That matters: the public socket and the authenticated socket quote DIFFERENT books on JD100. The public one still serves the old intact grid (OVER8 = 8.93); the authenticated one serves the restricted grid (OVER8 = 2.86, OVER0/1 and UNDER8/9 delisted). Earlier versions of this table quoted on the public socket and filled on the authenticated one, which produced 'PROPOSAL LIES' rows that were really tier and tick-state artifacts. See `endpoint_compare.py` and `payout_probe.py`, which show the same session quoting exactly what it fills (8/8 same-tick pairs, ratio 1.0000).

| contract | old | proposal | executed | exec/old | verdict |
|---|---:|---:|---:|---:|---|
| DIGITMATCH0 | 8.929 | 2.857142857142857 | 2.857142857142857 | 0.3200 | CUT 68.0% |
| DIGITDIFF0 | 1.0958 |  |  |  | buy err: This contract offers no  |
| DIGITOVER0 | 1.096 |  |  |  | buy err: This contract offers no  |
| DIGITOVER1 | 1.232 |  |  |  | buy err: This contract offers no  |
| DIGITOVER2 | 1.404 | 1.0571428571428572 | 1.0571428571428572 | 0.7530 | CUT 24.7% |
| DIGITOVER3 | 1.634 | 1.1714285714285715 | 1.1714285714285715 | 0.7169 | CUT 28.3% |
| DIGITOVER4 | 1.953 | 1.342857142857143 | 1.342857142857143 | 0.6876 | CUT 31.2% |
| DIGITOVER5 | 2.427 | 1.5428571428571431 | 1.5428571428571431 | 0.6357 | CUT 36.4% |
| DIGITOVER6 | 3.205 | 1.8285714285714287 | 1.8285714285714287 | 0.5705 | CUT 42.9% |
| DIGITOVER7 | 4.717 | 2.2285714285714286 | 2.2285714285714286 | 0.4725 | CUT 52.8% |
| DIGITOVER8 | 8.929 | 2.857142857142857 | 2.857142857142857 | 0.3200 | CUT 68.0% |
| DIGITUNDER1 | 8.929 | 2.857142857142857 | 2.857142857142857 | 0.3200 | CUT 68.0% |
| DIGITUNDER2 | 4.717 | 2.2285714285714286 | 2.2285714285714286 | 0.4725 | CUT 52.8% |
| DIGITUNDER3 | 3.205 | 1.8285714285714287 | 1.8285714285714287 | 0.5705 | CUT 42.9% |
| DIGITUNDER4 | 2.427 | 1.5428571428571431 | 1.5428571428571431 | 0.6357 | CUT 36.4% |
| DIGITUNDER5 | 1.953 | 1.342857142857143 | 1.342857142857143 | 0.6876 | CUT 31.2% |
| DIGITUNDER6 | 1.634 | 1.1714285714285715 | 1.1714285714285715 | 0.7169 | CUT 28.3% |
| DIGITUNDER7 | 1.404 | 1.0571428571428572 | 1.0571428571428572 | 0.7530 | CUT 24.7% |
| DIGITUNDER8 | 1.232 |  |  |  | buy err: This contract offers no  |
| DIGITUNDER9 | 1.096 |  |  |  | buy err: This contract offers no  |
| DIGITEVEN | 1.953 | 1.342857142857143 | 1.342857142857143 | 0.6876 | CUT 31.2% |
| DIGITODD | 1.953 | 1.342857142857143 | 1.342857142857143 | 0.6876 | CUT 31.2% |

## Restricted ladder

Negative EV at both previously measured win rates.
