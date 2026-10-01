#!/bin/bash
# Supervisor: keeps sentinel_v2 alive; restarts it if its stdout goes silent >120s.
# Usage: ./run_supervised.sh <until_epoch> <stake> <gate> [logtag]
UNTIL=${1:?until epoch}; STAKE=${2:-1}; GATE=${3:-0.015}; TAG=${4:-supervised}
cd "$(dirname "$0")"
while [ "$(date +%s)" -lt "$UNTIL" ]; do
  LEFT_MIN=$(( ( UNTIL - $(date +%s) ) / 60 ))
  [ "$LEFT_MIN" -lt 1 ] && break
  OUT=../results/run_${TAG}_stdout.log
  python3 -u sentinel_v2.py --trade --stake "$STAKE" --ev-gate "$GATE" --minutes "$LEFT_MIN" \
      --max-loss 100 --log ../results/sentinel_v2_${TAG}.csv >> "$OUT" 2>&1 &
  PID=$!
  echo "$(date -u +%H:%M:%S) supervisor: launched pid=$PID for ${LEFT_MIN}min" >> ../results/supervisor.log
  while kill -0 $PID 2>/dev/null; do
    sleep 30
    AGE=$(( $(date +%s) - $(stat -c %Y "$OUT") ))
    if [ "$AGE" -gt 120 ]; then
      echo "$(date -u +%H:%M:%S) supervisor: stdout stale ${AGE}s -> kill+relaunch" >> ../results/supervisor.log
      kill -9 $PID 2>/dev/null; sleep 3; break
    fi
    if [ "$(date +%s)" -ge "$UNTIL" ]; then kill $PID 2>/dev/null; sleep 5; break; fi
  done
done
echo "$(date -u +%H:%M:%S) supervisor: done" >> ../results/supervisor.log
