# Program mode — when the planner returns a roadmap

Read this once a planner returns `shape: roadmap` (or `need: shape_roadmap`). Everything in
`SKILL.md` still applies — dispatch protocol, pacing, escalation ladder, journal, lessons, gates —
except where this file says otherwise.

## Layout

The task directory becomes the **program directory**. Same files as a task (`status.md`, `log.md`,
`journal.jsonl`, `notes.md`, `retro.md`), plus `roadmap.md`, `plan-review.md`, and one subdirectory
per milestone, `m<NN>-<slug>/`, holding that milestone's `plan.md`, `review.md`,
`security-review.md` and `notes.md`. `status.md` carries `shape: roadmap`, `milestone:`,
`reviewed_through:` and `merged_through:` (see the template).

One worktree and branch for the whole program, `orch/<program-slug>`, created after the roadmap gate.
It survives merge points and is removed only at program end.

## 1. Roadmap → gate

1. **Tier follows shape.** A planner below `opus/max` returning `need: shape_roadmap`: journal
   `plan_shape` and an `escalation` (`trigger: plan_shape`), re-spawn at `opus/max` with its
   `notes.md` as starting findings. Under a user cap that forbids it, send the same planner back to
   write the roadmap at its tier, and say at the gate what the cap costs.
2. **Checker.** Run `~/.claude/bin/orch-program-check.py <roadmap.md>` yourself — don't trust the
   planner's report of it. Any FAIL → back to the planner (a revision), then re-run. Journal
   `checker`.
3. **Plan reviewer.** Spawn `orch-plan-reviewer` (default `sonnet/high`; `opus/medium` if any
   milestone is sensitive) with the roadmap path, repo path, and `plan-review.md` path. `blocker`
   findings → one planner revision → re-run the checker and re-review only what changed. Journal
   `plan_review`.
4. **Security offer** (SKILL.md step 5) for every sensitive milestone, once, at this gate.
5. **Budget.** Run `usage-check.sh`. Estimate the program from the milestone sizes and the
   per-size medians of past tasks (`~/.claude/bin/role-cost.py`); say whether it fits the 5h/7d headroom.
6. **Gate.** Show the user: the shape line, Summary, the Milestones table as is, every checker WARN
   with the planner's justification, plan-review findings and how they were handled, the security
   answers needed, the budget line. Approving the roadmap approves its `you` column: you stop for the
   user only where it says so (plus the stops in §4). Journal `gate` (`gate: roadmap`) and
   `milestone_plan` (the whole table as JSON rows).

## 2. Each milestone

Journal `milestone_start`; set `milestone: M<n> — <phase>` in `status.md`.

1. **Detail plan**, written now against what's actually built:
   - `standard`: an `orch-planner` at the row's planner tier writes `m<NN>-<slug>/plan.md` from the
     plan template, scoped to the milestone, citing the roadmap's contracts. A milestone too small
     to deserve its own planner should have been `lite` — that's a roadmap revision, not a shortcut.
   - `lite`: the lite agent's phase 1 (SKILL.md "Lite mode"), scoped to the milestone.
   - **You approve it** against the roadmap row and contracts. It goes to the user only when the row's
     `you` column says so, or when it changes a contract, the scope, or a user decision — that's a
     roadmap revision (§3), not a detail-plan approval.
2. **Implement** as SKILL.md step 7, on the program worktree.
3. **Tests after every milestone**, review point or not: `executor` runs the plan's verification
   command(s); a failure is fixed before the next milestone starts.
4. **If the row is a review point:** code review (plus security review where scheduled and accepted)
   over `git diff <reviewed_through>..HEAD` — every milestone since the previous review point.
   Fix rounds and the round-3 stop are SKILL.md step 9, per review point. Journal `review_point`;
   on pass set `reviewed_through` to HEAD.
5. **Re-plan check** at each review point (§3).
6. **If the row is a merge point:** SKILL.md step 10 — sensitivity re-check, summary, **ask the
   user**, `git merge --no-ff`. Journal `merge_point`; set `merged_through`. The branch continues.
7. **If the row has a `you` entry** other than merge, stop and ask for it.
8. Journal `milestone_end` (outcome, tiers actually used, rounds, tokens if reported).

A lite milestone that returns `scope_exceeded` switches to standard as a lite task does.
Sequential only: no two milestones run at once.

## 3. Re-plan check and roadmap revisions

At each review point, compare the remaining rows with what was built and learned (blockers,
deviations, review findings, probe results). Nothing broken → journal `replan` with `changed: []`
and continue. A premise or contract broke, a milestone grew past its size, or the user changed
direction → send the roadmap planner (same tier) to append `## Revision N` touching only the
affected rows; re-run the checker; show the user the changed rows (before/after) and get approval.
Journal `replan` with the changed rows and why.

## 4. When to stop for the user

Only at: the roadmap gate; rows marked in `you`; every merge; a roadmap revision; an escalation-ladder
rung 3; a usage STOP (prefer pausing between milestones); a checker FAIL you can't route back to
the planner.

## 5. Program end

The last milestone is always a review and merge point (the checker enforces it). Then SKILL.md step
11 cleanup, `task_end`, and `retro.md` with its **Milestones: planned vs actual** table filled.

## Resume

`status.md`'s `milestone:` line says which milestone and phase; `reviewed_through` / `merged_through`
say what's safe. Resume at that milestone's phase — never re-run a merged milestone.
