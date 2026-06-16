#!/bin/bash
# bot_loop.sh — keep sentinel_v2 running across its session windows for the
# multi-day evaluation. No secrets here: deriv_api.py already carries the
# throwaway token fallback. Touch /home/work/STOP to end.
cd /home/work/fable-thoughts/tools || exit 1
LOG=/home/work/metrics-engine/live/bot_stdout.log
while [ ! -f /home/work/STOP ]; do
  echo "$(date -u +%FT%TZ) === bot window start ===" >> "$LOG"
  python3 -u sentinel_v2.py --trade --stake 0.35 --ev-gate 0.01 --minutes 240 \
    --max-loss 40 --log /home/work/metrics-engine/live/sentinel_trades.csv >> "$LOG" 2>&1
  echo "$(date -u +%FT%TZ) === bot window ended, restarting in 3s ===" >> "$LOG"
  sleep 3
done
echo "$(date -u +%FT%TZ) STOP seen, bot loop exit" >> "$LOG"
