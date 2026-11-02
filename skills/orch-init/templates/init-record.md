<!-- Written by the orch-init agent at ~/.claude/orchestrations/<repo-name>/init.md. Orchestrate reads
`refresh:` to decide the one refresh after the first merge. -->
# Init record: {{repo-name}}

- mode: new
- date: {{YYYY-MM-DD}}
- repo: {{absolute path}}
- base branch: {{branch}}
- ecosystem: {{typescript|python|rust|go|dotnet|swift|godot|non-code|other: ...}}
- reference used: {{references/<eco>.md, verified: <date>, re-verified: yes|no}}
- agent tier: {{model/effort}}
- extras chosen: {{gate+CI | settings+hooks+secret-scan | README+LICENSE({{type}}) | CLAUDE.local.md | none}}
- worktree policy / commit cadence: {{answers}}
- files written: {{list}}
- commit: {{sha}} chore: initialize project (orch-init)
- refresh: pending   <!-- pending | done | skipped | n/a ; done after the refresh commit; skipped when the user declines the refresh (set by the session). Only pending fires -->
- refresh commit: {{sha or -}}
