# Liability-steering simulation — what your 512-combo theory predicts

Crowd: 60% of MATCH flow on 2 hottest digits, DIFF martingales on most-overdue digit, parity chasers on EVEN, uniform OVER flow. Liability per digit = sum(stake x payout).

| steering | P(hit hottest) | P(hit coldest) | hazard@overdue(k=25) | comment |
|---|---:|---:|---:|---|
| fair RNG (null) | 0.0994 | 0.1003 | 0.1001 | flat = undetectable |
| 5% of ticks steered | 0.0944 | 0.0967 | 0.1019 | deviates from 0.1000 |
| 15% steered | 0.0846 | 0.0877 | 0.1122 | deviates from 0.1000 |
| full steering | 0.0000 | 0.0000 | 0.0685 | deviates from 0.1000 |

Measured on real ticks (edge_tests.py, 200k+ per symbol): P(hottest)=0.1010, P(coldest)=0.1019, hazard flat 0.098–0.104 at every k.

Detection power: even 5% steering against this crowd mix shifts P(hit hottest) by >1pp — our sample resolves 0.2pp at 99% confidence. **Conclusion: steering of any economically meaningful size is excluded by the data.** Deriv doesn't need to cheat; the payout grid already takes 1.4–10.7% of every stake.
