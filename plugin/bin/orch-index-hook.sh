#!/usr/bin/env bash
# orch-index-hook.sh — PostToolUse hook: regenerate ACTIVE.md when a tool call touched an orchestration status.md.
# Reads the hook JSON on stdin (Write/Edit: tool_input.file_path; Bash: tool_input.command). Always exits 0, silent.
in=$(cat)
hit=$(printf '%s' "$in" | jq -r '(.tool_input.file_path // .tool_input.command // "")' 2>/dev/null | grep -c 'orchestrations/.*status\.md' || true)
[ "${hit:-0}" -gt 0 ] && "$HOME/.claude/bin/orch-index.sh" >/dev/null 2>&1
exit 0
