#!/usr/bin/env bash
# install.sh: copy orch into ~/.claude.
#   ./install.sh            install; before overwriting a file that differs, back it up to ~/.claude/.orch-backup-<UTC stamp>/
#   ./install.sh --dry-run  only list what would change
# It never edits settings.json. The README shows the hook and statusline entries to add yourself.
set -euo pipefail
dry=0; [ "${1:-}" = "--dry-run" ] && dry=1
repo="$(cd "$(dirname "$0")" && pwd)"
dest="$HOME/.claude"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$dest/.orch-backup-$stamp"
new=0; same=0; changed=0

while IFS= read -r rel; do
  src="$repo/$rel"; dst="$dest/$rel"
  if [ ! -e "$dst" ]; then
    new=$((new+1)); echo "new      $rel"
    [ $dry = 1 ] || { mkdir -p "$(dirname "$dst")"; cp -p "$src" "$dst"; }
  elif cmp -s "$src" "$dst"; then
    same=$((same+1))
  else
    changed=$((changed+1)); echo "update   $rel"
    [ $dry = 1 ] || { mkdir -p "$(dirname "$backup/$rel")"; cp -p "$dst" "$backup/$rel"; cp -p "$src" "$dst"; }
  fi
done < <(cd "$repo" && find skills agents bin -type f | sort)

[ $dry = 1 ] || chmod +x "$dest"/bin/usage-*.sh "$dest"/bin/orch-*.sh "$dest"/bin/*.py 2>/dev/null || true
mkdir -p "$dest/orchestrations" 2>/dev/null || true
echo "new $new, updated $changed, unchanged $same$([ $dry = 1 ] && echo ' (dry run)')"
[ $changed -gt 0 ] && [ $dry = 0 ] && echo "backups of replaced files: $backup"
exit 0
