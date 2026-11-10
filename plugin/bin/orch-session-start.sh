#!/usr/bin/env bash
# orch-session-start.sh ROOT — the plugin's SessionStart hook (ROOT = ${CLAUDE_PLUGIN_ROOT}).
# Runs `orch session-start ROOT`, so the live/dev rule has one implementation (orchlib/paths.py):
#   1. regenerates ACTIVE.md (orch-index.sh), as the SessionStart hook always did, when this copy may write;
#   2. points ~/.claude/orch-data/plugin at ROOT, only from the copy Claude Code installed; a dev copy never does.
here=$(cd "$(dirname "$0")" && pwd -P)
py=$(command -v python3 || command -v python || echo python3)
[ -n "${1:-}" ] || { echo "usage: orch-session-start.sh PLUGIN_ROOT" >&2; exit 1; }
exec "$py" "$here/orch" session-start "$1"
