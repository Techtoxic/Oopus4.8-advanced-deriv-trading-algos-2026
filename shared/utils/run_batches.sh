#!/bin/bash
: "${DERIV_TOKEN:?set DERIV_TOKEN in env}"
echo "=== BATCH A: DIGITEVEN p=0.5 (edge 2.35%) ==="
python3 live_trader.py --symbol 1HZ100V --contract DIGITEVEN --n 300 --stake 0.5 --decimals 2 --pwin 0.5 --out /home/research/digits/data/live_even_1HZ100V.csv
echo "=== BATCH B: DIGITOVER bar0 p=0.9 (edge 1.36%) ==="
python3 live_trader.py --symbol 1HZ100V --contract DIGITOVER --barrier 0 --n 300 --stake 0.5 --decimals 2 --pwin 0.9 --out /home/research/digits/data/live_over0_1HZ100V.csv
echo "=== ALL BATCHES DONE ==="
