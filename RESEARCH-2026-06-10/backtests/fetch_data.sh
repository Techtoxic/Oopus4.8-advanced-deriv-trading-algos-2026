#!/bin/bash
for s in xauusd xagusd eurusd gbpusd usdjpy audusd; do
  for y in 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026; do
    end="$((y+1))-01-01"; [ "$y" = "2026" ] && end="2026-06-09"
    out="/home/research-data/chunks/${s}-${y}.csv"
    if [ -s "$out" ]; then continue; fi
    npx -y dukascopy-node -i $s -from ${y}-01-01 -to $end -t h1 -f csv -dir /tmp/dukachunks -r 8 -rp 1500 -re -bs 5 -bp 1500 --cache -chpath /home/research-data/.dukascopy-cache >> /tmp/duka_chunks.log 2>&1
    f=$(ls -t /tmp/dukachunks/${s}-h1-bid-${y}* 2>/dev/null | head -1)
    [ -n "$f" ] && mv "$f" "$out"
    echo "done $s $y $(wc -l < $out 2>/dev/null)" >> /tmp/duka_progress.log
  done
done
echo ALLDONE >> /tmp/duka_progress.log
