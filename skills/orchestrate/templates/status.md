# Status: <task-slug>

<!-- Keep every field on one line in exactly this form; notes go in the table or log.md.
     task_state holds EXACTLY one enum value, nothing after it (orch-index.sh parses it). -->

- task_state: planning | awaiting_approval | implementing | reviewing | awaiting_merge | paused_usage_limit | done | abandoned
- mode: standard | lite
- shape: single | roadmap
- milestone: n/a | M<n> — <detail-plan | implementing | testing | reviewing | awaiting_merge>   (roadmap only)
- reviewed_through: none | <sha>   (roadmap only)
- merged_through: none | <sha>   (roadmap only)
- task_dir: ~/.claude/orchestrations/<repo>/<slug>
- repo: /abs/path/to/repo
- base_branch: <branch>
- task_branch: orch/<task-slug>
- worktree: /abs/path/to/repo/.claude/worktrees/orch-<task-slug> | not yet created | none (user: no worktree)
- planner_tier: <model>/<effort> — default opus/high; ladder opus/max → fable/max
- implementer_tier: <model>/<effort> — default sonnet/high; ladder opus/medium → fable/high
- review_round: 0/3
- usage_override: none | <what the user allowed, and when>

| role | state | timestamp (UTC) | last completed step | notes |
|---|---|---|---|---|
| orchestrator | in_progress | | task dir created | |
| planner | pending | | | |
| implementer | pending | | | |
| director | n/a | | | <pending if the plan has visual steps> |
| code-reviewer | pending | | | |
| security-reviewer | not_offered | | | <one-line reason, or pending / declined> |

<!-- Row states: pending | in_progress | blocked | failed | done. Security reviewer also:
     not_offered | declined. Lite mode: planner is n/a (the implementer plans). Director is n/a unless the plan has visual steps. Parallel parts get their own row: implementer:<part>. -->
