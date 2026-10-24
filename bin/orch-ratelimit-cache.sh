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
# and one line per change (60 s heartbeat) to the shared sample log that `orch usage` reads:
#   ~/.claude/.statusline-usage-log      "<epoch> <p5> <reset5> <p7> <reset7> <sid>" ('-' = unknown;
#                                         pruned to 26 h above 256 KB)
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

# Shared sample log for `orch usage`: one line per change per session, plus a 60 s heartbeat.
#   <epoch> <p5> <reset5_epoch|-> <p7|-> <reset7_epoch|-> <sid|->
# Appended only when the fields differ from the last line of the same sid (last ~20 lines) or
# >= 60 s passed; pruned to 26 h (atomic tmp + mv) above 256 KB. The legacy caches above keep
# their own prune/wipe rules. Never fails the caller: the call site swallows errors.
orch_usage_log() {  # <epoch> <p5> <reset5_epoch> <p7> <reset7_epoch> <sid>
  local f="$HOME/.claude/.statusline-usage-log" now="$1" p5="${2:-}" r5="${3:-}" p7="${4:-}" r7="${5:-}" sid="${6:-}"
  local key skip sz tmp
  [[ "$now" =~ ^[0-9]+$ ]] || return 0
  [[ "$p5" =~ ^[0-9]+(\.[0-9]+)?$ ]] || p5="-"
  [[ "$p7" =~ ^[0-9]+(\.[0-9]+)?$ ]] || p7="-"
  [[ "$r5" =~ ^[0-9]+$ ]] || r5="-"
  [[ "$r7" =~ ^[0-9]+$ ]] || r7="-"
  [[ "$sid" =~ ^[A-Za-z0-9._-]+$ ]] || sid="-"
  [ "$p5" = "-" ] && [ "$p7" = "-" ] && return 0
  key="$p5 $r5 $p7 $r7"
  if [ -f "$f" ]; then
    skip=$(tail -n 20 "$f" 2>/dev/null | awk -v s="$sid" -v k="$key" -v n="$now" '
      $6 == s { lt = $1; lk = $2 " " $3 " " $4 " " $5; seen = 1 }
      END { if (seen && lk == k && n - lt < 60) print 1 }')
    [ "$skip" = "1" ] && return 0
  fi
  echo "$now $key $sid" >> "$f"
  sz=$(wc -c < "$f" 2>/dev/null | tr -d ' ')
  if [ "${sz:-0}" -gt 262144 ]; then
    tmp="$f.tmp.$$"
    if awk -v c=$((now - 93600)) '$1 >= c' "$f" > "$tmp" 2>/dev/null; then mv "$tmp" "$f"; else rm -f "$tmp"; fi
  fi
  return 0
}
r7=$(j '.rate_limits.seven_day.resets_at // .rate_limits.seven_day.reset_at // empty')
if [ -n "$r7" ] && ! [[ "$r7" =~ ^[0-9]+$ ]]; then
  c="${r7%%.*}"; c="${c%Z}"; c="${c/T/ }"
  r7=$(date -j -u -f "%Y-%m-%d %H:%M:%S" "$c" +%s 2>/dev/null || date -u -d "$r7" +%s 2>/dev/null)
fi
orch_usage_log "$now" "$p5" "${e:-}" "$p7" "${r7:-}" "$(j '.session_id // empty')" >/dev/null 2>&1 || true

if [ "${1:-}" = "--print" ]; then
  out=""
  [ -n "$p5" ] && out="5h: $(printf '%.0f' "$p5")%"
  [ -n "$p7" ] && out="$out${out:+ | }7d: $(printf '%.0f' "$p7")%"
  printf '%s\n' "${out:-no rate limits in statusline input}"
fi
exit 0
