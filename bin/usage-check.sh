#!/usr/bin/env bash
# usage-check.sh — one-shot read of the account's rolling usage windows.
#
# Reads ONLY local files written by orch-ratelimit-cache.sh (from the statusline). Makes no
# network calls and no API calls: the harness pushes rate_limits into the
# statusline on each render, the statusline caches them, this reads the cache.
#
# Output (one line, both human- and machine-readable):
#   state=OK 5h=31 5h_age=4 7d=60 7d_age=4 reset_5h=1787919000 reset_in=4h21m
#
# state is one of OK | WARN | STOP | STALE | UNKNOWN
# Exit code: 0=OK 1=WARN 2=STOP 3=STALE 4=UNKNOWN  (so callers can branch on $?)
#
# Env overrides: WARN_PCT (90) STOP_PCT (95) STALE_SEC (600)
#
# Delegates to `orch usage` (the estimator: dynamic stop line, all sessions' spend) and prints its
# line and exit code unchanged. WARN_PCT / STOP_PCT, when set by the caller, become fixed
# thresholds (`--warn` / `--line`). If `orch` is missing or crashes, the legacy logic below runs
# on the cache files, with its fixed 90 / 95 thresholds. STALE_SEC only applies to that fallback.

set -uo pipefail

# Delegate. Test the caller's own values (non-empty) before the defaults below fill them in.
_orch="$(dirname "$0")/orch"
if [ -x "$_orch" ]; then
  _args=()
  [ -n "${STOP_PCT:+x}" ] && _args+=(--line "$STOP_PCT")
  [ -n "${WARN_PCT:+x}" ] && _args+=(--warn "$WARN_PCT")
  _out=$("$_orch" usage ${_args[@]+"${_args[@]}"} 2>/dev/null)
  _code=$?
  _last=$(printf '%s\n' "$_out" | tail -n 1)
  if [[ "$_last" == state=* ]]; then
    printf '%s\n' "$_last"
    exit "$_code"
  fi
fi

WARN_PCT="${WARN_PCT:-90}"
STOP_PCT="${STOP_PCT:-95}"
STALE_SEC="${STALE_SEC:-600}"

CACHE_5H="$HOME/.claude/.statusline-ratelimit-5h"
CACHE_7D="$HOME/.claude/.statusline-ratelimit-7d"
RESET_5H="$HOME/.claude/.statusline-reset-5h"

now=$(date +%s)

# Read the last <epoch> <pct> sample from a cache file.
# The statusline truncates and re-appends non-atomically on window reset, so an
# empty read is a race, not a zero. Retry briefly, then report unknown.
read_sample() {
  local f="$1" line="" i
  for i in 1 2 3; do
    line=$(tail -1 "$f" 2>/dev/null)
    [ -n "$line" ] && break
    sleep 0.2
  done
  [ -z "$line" ] && { echo ""; return 1; }
  # Guard against a partially-written line: need "<int> <number>".
  awk 'NF>=2 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9.]+$/ {printf "%d %d\n", $1, ($2+0.5)}' <<<"$line"
}

s5=$(read_sample "$CACHE_5H") || s5=""
s7=$(read_sample "$CACHE_7D") || s7=""

if [ -z "$s5" ]; then
  echo "state=UNKNOWN 5h=? 5h_age=? reason=no-cache-sample"
  exit 4
fi

t5=${s5% *}; p5=${s5#* }
age5=$((now - t5))

if [ -n "$s7" ]; then
  t7=${s7% *}; p7=${s7#* }
  age7=$((now - t7))
else
  p7="?"; age7="?"
fi

reset_epoch=$(cat "$RESET_5H" 2>/dev/null)
reset_in="?"
if [[ "${reset_epoch:-}" =~ ^[0-9]+$ ]] && [ "$reset_epoch" -gt "$now" ]; then
  secs=$((reset_epoch - now))
  reset_in="$((secs / 3600))h$(( (secs % 3600) / 60 ))m"
fi

# Staleness outranks the thresholds: a fresh-looking low number that is actually
# an hour old is the dangerous case, so never report OK on a stale sample.
if [ "$age5" -gt "$STALE_SEC" ]; then
  state=STALE; code=3
elif [ "$p5" -ge "$STOP_PCT" ]; then
  state=STOP; code=2
elif [ "$p5" -ge "$WARN_PCT" ]; then
  state=WARN; code=1
else
  state=OK; code=0
fi

echo "state=$state 5h=$p5 5h_age=$age5 7d=$p7 7d_age=$age7 reset_5h=${reset_epoch:-?} reset_in=$reset_in"
exit $code
