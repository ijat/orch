<!-- orch:begin (managed by `orch install-instructions`; a re-run replaces this block) -->
## Orchestration mode

- Before non-trivial work (a feature, a multi-file fix or refactor, anything needing a written plan), ask which mode with `AskUserQuestion`: interactive (recommended), standard, lite or single-session. Then load the `orchestrate` skill.
- With nothing in progress and a prompt that only says "resume" or "continue": run `orch current` and follow its `action:` line; for `ask`, use `AskUserQuestion`.
- To create or schedule an orch task, run `orch init <repo> <slug> ...` (see the `orchestrate` skill); never write a plain markdown file for it.
<!-- orch:if agent_docs -->

## Agent docs stay out of repos

- Agent docs (CLAUDE.md, AGENTS.md, STATE/DECISIONS, plans, task notes, handoffs, `.claude/`) never enter a repo. They live in the private store (`orch docs status` prints its path) and are linked into each checkout as `.agent/`.
- A new agent doc: `orch docs adopt <path>` moves it into the store and links it back.
- Edit one through `.agent/<path>` (Write refuses a symlinked file), then run `orch docs commit`.
- Never bypass a hook that reports a problem (`--no-verify`, `core.hooksPath`, `AGENTDOCS_ALLOW`): fix what it reports.
<!-- orch:endif -->
<!-- orch:end -->
