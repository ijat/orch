# Policy: <repo-name>

- merge: auto | ask
- security: rule | always | ask
- docs-only: default | none | <comma-separated globs>

<!-- Keep one value per line. An absent file means `merge: auto` and `security: rule`; any other value for `merge` stops `orch merge`. `docs-only` lists the paths whose change alone (after a passed review) needs a quick accuracy check, not a review round, and no new gate or security pass: `default` is `docs/**/*.md`, `STATE.md`, `DECISIONS.md`, `CHANGELOG.md` (root only); `none` turns it off; an absent line means `default`; an unreadable value means off. -->