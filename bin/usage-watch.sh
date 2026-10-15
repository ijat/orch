#!/usr/bin/env bash
# usage-watch.sh — background watchdog for the account's 5h usage window.
#
# Designed to be run as the `command` of a Monitor tool call: it prints ONE line
# per state transition and stays silent otherwise, so it costs nothing in tokens
# until something actually changes.
#
# Like usage-check.sh, it reads only the local statusline cache. No network, no
# API calls — polling it at any interval is free.
#
# Emits, on transition only:
#   WARN  5h=91% age=12s  — entered the slow-down zone
#   STOP  5h=96% age=8s   — halt: checkpoint and stop dispatching
#   STALE last sample 14m old (5h=88%) — the numbers went blind; treat as WARN
#   FRESH 5h=88% age=6s   — samples resumed after a STALE
#   CLEAR 5h=42% age=5s   — dropped back below WARN
#   RESET 5h 93%->4%      — window rolled over (big drop); safe to resume
#
# Usage:
#   usage-watch.sh [--warn 90] [--stop 95] [--stale-sec 600] [--interval 20]
#                  [--exit-on-stop]
#
# --exit-on-stop makes the script exit after emitting STOP, which turns it into
# a single-shot notifier suitable for Bash(run_in_background). Without it the
# watch is continuous and must be ended with TaskStop.

set -uo pipefail

WARN_PCT=90; STOP_PCT=95; STALE_SEC=600; INTERVAL=20; EXIT_ON_STOP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --warn)        WARN_PCT="$2"; shift 2 ;;
    --stop)        STOP_PCT="$2"; shift 2 ;;
    --stale-sec)   STALE_SEC="$2"; shift 2 ;;
    --interval)    INTERVAL="$2"; shift 2 ;;
    --exit-on-stop) EXIT_ON_STOP=1; shift ;;
    *) echo "usage-watch: unknown arg $1" >&2; exit 64 ;;
  esac
done

CHECK="$HOME/.claude/bin/usage-check.sh"
[ -x "$CHECK" ] || { echo "usage-watch: missing $CHECK" >&2; exit 66; }

prev_state=""     # last state we announced
prev_pct=""       # last known good percentage, for reset detection

while true; do
  # A failing read must never kill the watch.
  out=$(WARN_PCT="$WARN_PCT" STOP_PCT="$STOP_PCT" STALE_SEC="$STALE_SEC" "$CHECK" 2>/dev/null) || true

  # Parse key=value fields exactly. A loose regex here matches `reset_5h=` as if
  # it were `5h=` and reports the reset epoch as a percentage — don't loosen it.
  kv() { awk -v k="$1" '{for(i=1;i<=NF;i++){split($i,a,"="); if(a[1]==k){print a[2]; exit}}}' <<<"$2"; }
  state=$(kv state "$out")
  pct=$(kv 5h "$out")
  age=$(kv 5h_age "$out")
  [ -z "$state" ] && state=UNKNOWN

  # Window rollover: a drop of more than 5 points is how the statusline itself
  # decides a fresh window started. Announce it so a paused caller can resume.
  if [ -n "$prev_pct" ] && [[ "$pct" =~ ^[0-9]+$ ]] && [ "$((prev_pct - pct))" -gt 5 ]; then
    echo "RESET 5h ${prev_pct}%->${pct}% — window rolled over, now ${state}"
    prev_state="$state"   # the RESET line already announced the new zone
    prev_pct="$pct"
    sleep "$INTERVAL"
    continue
  fi
  [[ "$pct" =~ ^[0-9]+$ ]] && prev_pct="$pct"

  if [ "$state" != "$prev_state" ]; then
    case "$state" in
      STOP)  echo "STOP 5h=${pct}% age=${age}s" ;;
      WARN)  echo "WARN 5h=${pct}% age=${age}s" ;;
      STALE) echo "STALE last sample ${age}s old (5h=${pct}%) — treat as WARN, numbers are blind" ;;
      UNKNOWN) echo "STALE no readable usage sample — treat as WARN" ;;
      OK)
        case "$prev_state" in
          STALE|UNKNOWN) echo "FRESH 5h=${pct}% age=${age}s" ;;
          WARN|STOP)     echo "CLEAR 5h=${pct}% age=${age}s" ;;
        esac
        ;;
    esac
    prev_state="$state"
    if [ "$state" = "STOP" ] && [ "$EXIT_ON_STOP" = "1" ]; then
      exit 0
    fi
  fi

  sleep "$INTERVAL"
done
