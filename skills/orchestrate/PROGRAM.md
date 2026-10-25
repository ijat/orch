# Program mode — when the planner returns a roadmap

Read this once a planner returns `shape: roadmap` (or `need: shape_roadmap`). Everything in
`SKILL.md` still applies — dispatch protocol, pacing, escalation ladder, journal, lessons, gates —
except where this file says otherwise.

## Layout

The task directory becomes the **program directory**. Same files as a task (`status.md`, `log.md`,
`journal.jsonl`, `notes.md`, `retro.md`), plus `roadmap.md`, `plan-review.md`, and one subdirectory
per milestone, `m<NN>-<slug>/`, holding that milestone's `plan.md`, `review.md`,
`security-review.md` and `notes.md`; `questions.md` and `briefs/` stay at program level.
`status.md` carries `shape: roadmap`, `milestone:`, `reviewed_through:` and `merged_through:` (see the
template). Programs made by the old text work too: `orch` adds `milestone` to journal lines itself
and `orch merge` fails closed on anything it can't prove (then ask the user).

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
4. **Security decision** (SKILL.md step 5 rule) for every milestone, once, at this gate: one line each,
   `orch journal auto_security milestone:=<N> areas:='[…]' ran:=true rule=step5` (`ran:=false` for a skip).
5. **Budget.** Run `orch usage` (`7d_proj` for the 7d budget). Estimate the program from the milestone sizes and past runs per tier
   (`~/.claude/bin/role-cost.py --by tier`; it has no per-size grouping,
   so give a range); say whether it fits the 5h/7d headroom.
6. **Gate.** Show the user: the shape line, Summary, the Milestones table as is, every checker WARN
   with the planner's justification, plan-review findings and how they were handled, the security
   decision per milestone, the budget line. Approving the roadmap approves its `you` column: you stop for the
   user only where it says so (plus the stops in §4). Journal `gate` (`gate: roadmap`) and
   `milestone_plan` (the whole table as JSON rows).

## 2. Each milestone

Journal `milestone_start` and set `milestone: M<n> — <phase>` in `status.md`:
`orch journal milestone_start milestone:=<N> --set "milestone=M<N> — detail-plan"`. Set the
security-reviewer row from that milestone's `auto_security` line — `pending`, or `not_offered` with the
reason — every time (`orch merge` reads the row, so last milestone's value must not carry over).

1. **Detail plan**, written now against what's actually built:
   - `standard`: an `orch-planner` at the row's planner tier, briefed `shape: single (milestone)`,
     writes `m<NN>-<slug>/plan.md` from the plan template, scoped to the milestone, citing the roadmap's contracts. A milestone too small
     to deserve its own planner should have been `lite` — that's a roadmap revision, not a shortcut.
   - `lite`: the lite agent's phase 1 (SKILL.md "Lite mode"), scoped to the milestone.
   - **You approve it** against the roadmap row and contracts. It goes to the user only when the row's
     `you` column says so, or when it changes a contract, the scope, or a user decision — that's a
     roadmap revision (§3), not a detail-plan approval.
2. **Implement** as SKILL.md step 7, on the program worktree. That includes the continue gate: you approve a detail
   plan yourself, so the planner is still warm and the gate wait is short.
3. **Tests after every milestone**, review point or not: `orch-executor` runs the plan's verification
   command(s); a failure is fixed before the next milestone starts.
4. **If the row is a review point:** (a `skip` row is not one — no reviewer runs) code review (plus security review where the step 5 rule scheduled it)
   over `git diff <reviewed_through>..HEAD` — every milestone since the previous review point.
   Brief the reviewers with that range (`orch brief review` — it defaults to `<reviewed_through>..<tip>`
   once `reviewed_through` is a sha; `--range A..B` overrides) and the milestone plans it spans; `review.md` goes in the review-point milestone's dir. Fix rounds and the
   round-3 stop are SKILL.md step 9, per review point, under the standard rules even when a spanned
   milestone ran lite. Journal `review_point`; on pass set `reviewed_through` to HEAD
   (`orch set reviewed_through=<sha>`).
5. **Re-plan check** at each review point (§3).
6. **If the row is a merge point:** SKILL.md step 10 — the sensitivity re-check, the final gate, then
   `orch merge --recheck-ok`, which merges automatically when clean (`merge: ask` repos and a refused
   merge ask the user). After a merge point `orch merge` back-merges base into the task branch
   (`--no-ff`), journals `merge_point` and sets `merged_through`; when it reports `backmerge: null`,
   ask the user. Never fast-forward. The branch continues.
7. **If the row has a `you` entry**, park it as a question (§4) and wait for it — including `merge`; `orch merge` refuses
   where the `you` cell names merge.
8. Journal `milestone_end` (outcome, tiers actually used, rounds, tokens if reported) and
   `orch checkpoint`.

A lite milestone that returns `scope_exceeded` switches to standard as a lite task does.
Sequential only: no two milestones run at once.

## 3. Re-plan check and roadmap revisions

At each review point, compare the remaining rows with what was built and learned (blockers,
deviations, review findings, probe results). Nothing broken → journal `replan` with `changed: []`
and continue. A premise or contract broke, a milestone grew past its size, or the user changed
direction → send the roadmap planner (same tier) to rewrite the affected rows of the Milestones table in place,
bump `revision:`, and append `## Revision N` with each changed row before and after (the checker reads
only the Milestones table); re-run the checker; show the user the changed rows (before/after) and get approval.
Journal `replan` with the changed rows and why.

## 4. When to stop for the user

Stop at once only at SKILL.md's hard stops ("Checkpoints and questions"): sudo, an exclusive VM or device, a paid
action; a destructive operation outside the task's worktree; push; an upstream PR; a merge the guard refuses
(`orch merge` exit 2); a usage `STOP`; three strikes after rungs 1–2. On a `STOP`, prefer pausing between
milestones. The roadmap gate is asked at once: nothing after it is independent.

Every other question parks as in SKILL.md: rows marked in `you`, a roadmap revision, a director escalation about
a recorded decision, disputed findings after round 3, an irreversible or sensitive-area blocker, a checker FAIL
you can't route back to the planner. Only the milestones that depend on it wait; the program stops only when
every remaining milestone depends on open questions.

## 5. Program end

The last milestone is always a merge point, and a review point unless the user approved `skip` (the
checker enforces it). Then SKILL.md step
11 cleanup, `task_end`, and `retro.md` with its **Milestones: planned vs actual** table filled.

## Resume

`orch resume <repo>/<slug>` first. `status.md`'s `milestone:` line says which milestone and phase;
`reviewed_through` / `merged_through` say what's safe. Resume at that milestone's phase — never
re-run a merged milestone.
