# Lite mode

Read this when the task's mode is lite. Everything in `SKILL.md` applies except where this file says otherwise.

One `orch:implementer` plans **and** implements, so the repo gets explored once instead of twice.
Everything else stays: task dir, journal, plan gate, worktree, code review, retro — and the standard
stop policy (step 5 rule, assumed decisions, parked questions, step 10's automatic merge).

**Eligible only if all hold** (judge from the request plus a quick look; when unsure, standard):
about ≤ 5 files and ≤ 3 steps; no sensitive area (step 5 list); no concurrency, performance-
sensitive algorithm, migration or other hard-to-undo change; one obvious design (nothing that would
earn `fable/high` as planner). The user may force lite anyway — journal it as `override`.

**Tier:** `opus/medium` by default (it must plan as well as code; `sonnet/high` plans worse).
`opus/high` if the task is at the edge of eligibility. Never Fable — that need means standard mode.

**Flow** — the standard procedure with these differences:

- **Step 3:** `orch init … --mode lite` (`mode: lite`, planner row `n/a (lite)`, `task_start` mode `"lite"`).
- **Step 4:** after the pre-plan `orch:graph` call, spawn the lite agent (`description`: `orch-implementer-<model>[<effort>] lite: <task>`)
  in **phase 1**: read-only on the repo, writes a lite `plan.md` (see the role file) and returns; you
  `orch plan-check --lite` it and set `task_state: awaiting_approval`. Gate it exactly as a standard
  plan. Revisions go back to the same agent via `SendMessage`.
- **Step 5:** if the lite plan names a sensitive area after all, switch to standard (below).
- **Step 7:** after creating the worktree, `SendMessage` the **same** agent — its context is the
  saving — with the worktree path and "phase 2: implement" (no brief). If that agent is gone (crash,
  resumed session), spawn a fresh one at the same tier on phase 2; `plan.md` carries everything.
- **Step 9:** cycle 1 fixes go to the same agent the same way, under SKILL.md § Review loops (blockers and should-fix in
  one batch; open findings get fix-and-check cycles, and a blocker chain then a fresh round). Cycles 2 and 3 of a round
  are fresh spawns at higher tiers (`TIERS.md` § Fix rounds), as in standard. **Max 2 rounds**; a blocker still open after
  round 2, a commit awaiting review after round 2, or a fresh round due after round 2 → switch to standard and continue under the standard rules. A blocker
  still open after cycle 3 of a round is the cycle stop in § Review loops (a parked question), in either mode.

**Switching to standard** — when the agent returns `blocked` with `need: scope_exceeded`, the plan
fails eligibility, or round 2 ends with a blocker open, a commit awaiting review or a fresh round due: journal a `mode_switch` (`from`, `to`,
`reason`), set `mode: standard` in `status.md`, and continue at the matching standard step. A
switch at planning spawns a real planner that is given the lite `plan.md` as its starting findings.
Don't argue an agent out of `scope_exceeded`.
