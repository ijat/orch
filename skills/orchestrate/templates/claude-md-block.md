<!-- orch:begin (managed by `orch install-instructions`; a re-run replaces this block) -->
## Orchestration mode

- **Offer it before non-trivial work.** Before a new feature, a bugfix or refactor spanning several files, or anything that would warrant a written plan: **always ask me which mode** (via `AskUserQuestion`): interactive to plan it with me in this session, orchestration standard, lite for small, low-risk tasks, or normal single-session. **Recommend interactive by default** for non-trivial work (first option); recommend another only when the task clearly fits it, and say why. Never pick for me, even if a memory says to skip the question. Ask once, then get on with it. `AskUserQuestion` allows 4 options, so list only these 4 modes; a rule of mine that adds an option to every question does not apply to this one.
- **Don't** offer it for questions, read-only research, trivial edits, or when I already dictated the approach.
- If I pick it, load the **`orchestrate`** skill and follow it. Orchestration tasks run in a git worktree by default, even if I otherwise avoid worktrees: choosing the mode *is* the request.
- **Pending work.** When this session has nothing in progress and my prompt only asks to pick up pending work ("resume", "continue", "start", "carry on", "where were we", or the same thing in other words): run `~/.claude/bin/orch current` before anything else and follow its `action:` line.
  - `resume <repo>/<slug>`: load the `orchestrate` skill and resume that task (its step 2), without asking.
  - `ask`: ask me which of the listed tasks to resume, via `AskUserQuestion`.
  - `none`: say no task is active here and name the other repos' tasks it lists. If the project has its own hand-off file (`docs/STATE.md`, `STATE.md`), offer its next step.
  - In a session already working on something, "continue" means carry on with that work. A prompt that names other work is that work, not a resume.
- **`orch-explorer`** (Haiku, read-only) and **`orch-executor`** (Haiku, runs builds/tests/lint, reports a digest) are general-purpose and available in **every** session, not only in orchestration. Reach for them whenever a lookup or a noisy command would otherwise burn the caller's own context. Neither can spawn further agents.
- To see every repo's unfinished tasks: `~/.claude/bin/orch show`.
<!-- orch:end -->
