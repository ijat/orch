<!-- Output of the orch-init agent in audit mode: ~/.claude/orchestrations/<repo-name>/audit.md.
The agent never edits the repo. Every finding has evidence and a proposed diff. -->
# Audit: {{repo-name}}

- date: {{YYYY-MM-DD}}   commit audited: {{sha}}   scans exclude: `.claude/worktrees/`
- instruments: {{git ls-files / wc -l / commands run}}

## Summary

{{N findings: blocker x, should y, nit z}}

## Findings

### F1 {{title}} - {{blocker|should|nit}}
- evidence: `{{file:line}}` or `{{command -> output}}`
- why it matters: {{one line}}
- proposed diff:
```diff
{{unified diff}}
```

## Checked, no finding

- {{size, global duplicates, stale commands, tracked local files, shared-file drift, missing STATE/DECISIONS, missing seams, rules that should be hooks}}

## Not run (mutating)

- {{documented commands that were only checked for existence: deploy, migrate, install, push, in-place format}}
- `git status --porcelain` before/after: {{empty / empty, or unchanged}}

## Not checked

- {{anything that could not be verified, and why}}
