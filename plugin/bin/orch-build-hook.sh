#!/usr/bin/env bash
# orch-build-hook.sh: PostToolUse hook that rebuilds the orch-* agent files when a tool call touched a
# role source under skills/orchestrate/roles/ (including _shared/).
# It reads the hook JSON on stdin: Write/Edit carry tool_input.file_path, Bash carries tool_input.command.
# No match: exits 0, silent. On a match it runs orch-build-agents.py. If the build succeeds it exits 0
# silently. If the build fails it prints the error to stderr and exits 2, so Claude sees it.
in=$(cat)
target=$(printf '%s' "$in" | jq -r '(.tool_input.file_path // .tool_input.command // "")' 2>/dev/null)
case "$target" in
  *skills/orchestrate/roles/*) ;;
  *) exit 0 ;;
esac
py=$(command -v python3 || command -v python || echo python3)  # Windows installs often name it python
if ! out=$("$py" "$HOME/.claude/bin/orch-build-agents.py" 2>&1); then
  printf 'orch-build-hook: rebuilding agents from roles/ failed:\n%s\n' "$out" >&2
  exit 2
fi
exit 0
