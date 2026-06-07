#!/bin/bash
: "${DERIV_TOKEN:?set DERIV_TOKEN in env}"
echo "START A $(date +%T)"
python3 live_trader2.py --symbol 1HZ100V --contract DIGITEVEN --n 300 --stake 0.5 --decimals 2 --mult 1.953 --out /home/research/digits/data/live_even_1HZ100V.csv
echo "START B $(date +%T)"
python3 live_trader2.py --symbol 1HZ100V --contract DIGITOVER --barrier 0 --n 300 --stake 0.5 --decimals 2 --mult 1.096 --out /home/research/digits/data/live_over0_1HZ100V.csv
echo "DONE $(date +%T)"
