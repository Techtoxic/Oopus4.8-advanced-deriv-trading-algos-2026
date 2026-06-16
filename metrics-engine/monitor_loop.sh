#!/bin/bash
# monitor_loop.sh — keep the independent sigma/tick logger running across windows.
cd /home/work/metrics-engine || exit 1
LOG=/home/work/metrics-engine/live/monitor_stdout.log
while [ ! -f /home/work/STOP ]; do
  echo "$(date -u +%FT%TZ) === monitor window start ===" >> "$LOG"
  python3 -u monitor.py --symbol JD100 --minutes 240 \
    --out /home/work/metrics-engine/live/sigma_ticks.csv >> "$LOG" 2>&1
  echo "$(date -u +%FT%TZ) === monitor window ended, restarting in 3s ===" >> "$LOG"
  sleep 3
done
echo "$(date -u +%FT%TZ) STOP seen, monitor loop exit" >> "$LOG"
