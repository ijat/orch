<!-- orch:begin (managed by `orch install-instructions`; a re-run replaces this block) -->
## Orchestration mode

- Before non-trivial work (a feature, a multi-file fix or refactor, anything needing a written plan), ask which mode with `AskUserQuestion`: interactive (recommended), standard, lite or single-session. Then load the `orchestrate` skill.
- With nothing in progress and a prompt that only says "resume" or "continue": run `~/.claude/bin/orch current` and follow its `action:` line; for `ask`, use `AskUserQuestion`.
<!-- orch:end -->
