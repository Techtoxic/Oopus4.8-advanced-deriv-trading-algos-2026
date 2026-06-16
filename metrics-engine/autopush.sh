#!/bin/bash
# autopush.sh — every INTERVAL seconds: refresh dashboard + push live results to GitHub
# so an ephemeral-VM death never loses more than INTERVAL of data. Strips any
# credential from git output. Touch /home/work/STOP to end.
INTERVAL="${1:-1800}"
cd /home/work || exit 1
LOG=/home/work/autopush.log
while [ ! -f /home/work/STOP ]; do
  sleep "$INTERVAL"
  ( cd /home/work/metrics-engine && python3 analyze.py >/dev/null 2>&1 )
  cd /home/work
  git add metrics-engine/live metrics-engine/out 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -q -m "autopush: live results $(date -u +%FT%TZ)" 2>/dev/null
    git push origin metrics-engine-2026-06-16 2>&1 | sed 's#//[^@]*@#//***@#g' >> "$LOG"
    echo "$(date -u +%FT%TZ) pushed" >> "$LOG"
  fi
done
echo "$(date -u +%FT%TZ) STOP file seen, exiting" >> "$LOG"
