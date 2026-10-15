#!/usr/bin/env bash
# orch-ratelimit-cache.sh: cache Claude Code's rate-limit percentages for usage-check.sh and
# usage-watch.sh.
#
# Claude Code sends the statusline command a JSON object on stdin at each render, and on Pro/Max
# plans that object includes `rate_limits`. This script reads the object and appends
# "<epoch> <pct>" lines to:
#   ~/.claude/.statusline-ratelimit-5h   (pruned to the last 30 min)
#   ~/.claude/.statusline-ratelimit-7d   (pruned to the last 6 h)
#   ~/.claude/.statusline-reset-5h       (one line: the epoch when the 5h window resets)
#
# Use it in either of two ways:
#   - as the whole statusline:  "statusLine": {"type": "command", "command": "~/.claude/bin/orch-ratelimit-cache.sh --print"}
#   - from your own statusline: input=$(cat); printf '%s' "$input" | ~/.claude/bin/orch-ratelimit-cache.sh
#
# Needs jq. Never fails the statusline: always exits 0.
set -u
input=$(cat)
dir="$HOME/.claude"
now=$(date +%s)

j() { printf '%s' "$input" | jq -r "$1" 2>/dev/null; }

append() {  # append <file> <pct> <prune_sec>
  local f="$1" pct="$2" prune="$3" last tmp
  [ -n "$pct" ] || return 0
  if [ -f "$f" ]; then
    last=$(tail -1 "$f" 2>/dev/null | awk '{print $2}')
    # A drop of more than 5 points means a new window: start the history over.
    if [ -n "$last" ] && awk "BEGIN{exit !($pct < $last - 5)}"; then : > "$f"; fi
  fi
  echo "$now $pct" >> "$f"
  tmp="$f.tmp.$$"
  if awk -v c=$((now - prune)) '$1 >= c' "$f" > "$tmp" 2>/dev/null; then mv "$tmp" "$f"; else rm -f "$tmp"; fi
}

p5=$(j '.rate_limits.five_hour.used_percentage // empty')
p7=$(j '.rate_limits.seven_day.used_percentage // empty')
r5=$(j '.rate_limits.five_hour.resets_at // .rate_limits.five_hour.reset_at // empty')

append "$dir/.statusline-ratelimit-5h" "$p5" 1800
append "$dir/.statusline-ratelimit-7d" "$p7" 21600

if [ -n "$r5" ]; then
  if [[ "$r5" =~ ^[0-9]+$ ]]; then e="$r5"; else
    c="${r5%%.*}"; c="${c%Z}"; c="${c/T/ }"
    e=$(date -j -u -f "%Y-%m-%d %H:%M:%S" "$c" +%s 2>/dev/null || date -u -d "$r5" +%s 2>/dev/null)
  fi
  [ -n "${e:-}" ] && echo "$e" > "$dir/.statusline-reset-5h"
fi

if [ "${1:-}" = "--print" ]; then
  out=""
  [ -n "$p5" ] && out="5h: $(printf '%.0f' "$p5")%"
  [ -n "$p7" ] && out="$out${out:+ | }7d: $(printf '%.0f' "$p7")%"
  printf '%s\n' "${out:-no rate limits in statusline input}"
fi
exit 0
