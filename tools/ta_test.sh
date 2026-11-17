#!/bin/bash
# Prüft, ob TrueAchievements vom LXC aus lesbar ist oder eine JavaScript-Sperre zeigt
B="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
for u in https://www.trueachievements.com/news/xbox-game-pass-leaving-soon-november-2026-wave-2 https://www.trueachievements.com/game/Moving-Out-2/completiontime https://www.trueachievements.com/rss/news https://www.trueachievements.com/xbox-game-pass/games; do
 for ua in "Morgenzeitung/1.0" "$B"; do
  r=$(curl -s -L -A "$ua" -H "Accept-Language: en-US,en;q=0.9" -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" -w " CODE%{http_code}" "$u")
  echo "${u#https://www.trueachievements.com} | ${ua:0:12} | ${r##* CODE} | $(echo "$r" | wc -c) Bytes | $(echo "$r" | grep -c 'Enable JavaScript') Sperre | $(echo "$r" | grep -o 'complete all[^.]*hours' | head -1)"
 done
done
