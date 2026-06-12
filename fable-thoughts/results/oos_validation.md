# OUT-OF-SAMPLE validation — JD100, 65201 fresh ticks (after opus cut), gate=0.02

spot range 228.21..264.61; trades fired: 3497

- total PnL ($1 stakes): **-58.55** over 3497 trades = **-1.67%/trade** (model-EV avg +2.25%)
- win rate 0.4358; t-stat -0.87
- contracts: {('DIGITUNDER', 4): 865, ('DIGITOVER', 5): 864, ('DIGITUNDER', 5): 692, ('DIGITOVER', 4): 691, ('DIGITOVER', 6): 193, ('DIGITUNDER', 3): 192}

## Calibration: model P vs realized win rate (chosen contract, by sigma)

| sigma | n | model P | realized P | realized EV |
|---:|---:|---:|---:|---:|
| 4.0 | 127 | 0.3678 | 0.3386 | -33.87% |
| 4.1 | 3370 | 0.4535 | 0.4395 | -14.17% |

## PnL by hour (UTC)

- 06-12 05:00: -17.4
- 06-12 06:00: +11.5
- 06-12 07:00: -71.9
- 06-12 08:00: +10.8
- 06-12 09:00: +8.5
