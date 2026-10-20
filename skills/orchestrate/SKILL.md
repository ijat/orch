---
name: orchestrate
description: Run a multi-agent orchestration workflow (planner → implementer → code review → optional security review → merge) for non-trivial, multi-step engineering work. Use when the user picks "orchestration mode", says "orchestrate this", "run this with subagents", "plan then implement then review", or accepts the orchestration offer for a feature, refactor, or multi-file bugfix. Coordinates `orch-planner` and `orch-implementer` (tier picked per spawn as model × effort, e.g. opus/max) plus `orch-code-reviewer` and `orch-security-reviewer` — each able to nest-call `explorer` (read-only lookups) and `executor` (runs builds/tests/lint, reports a condensed digest) — keeps durable state in per-task Markdown files plus a persistent per-repo security ledger and lessons-learned log, paces itself against the account's 5h/7d usage limits (slowing down at 90%, pausing and auto-resuming at 95%), and runs the work inside a dedicated git worktree. Also has a **lite mode** (one agent plans and implements, review kept) for small, low-risk tasks — use when the user picks "lite mode" / "orchestrate lite". For big features the planner may return a **roadmap** of milestones instead of a single plan (program mode, `PROGRAM.md`) — also use when the user asks to "plan this in milestones", "roadmap this", or resumes a multi-milestone program.
---

# Orchestration mode

You are the **orchestrator**: you coordinate specialist roles, keep all shared state in Markdown
files on disk, run the work in a dedicated git worktree so nothing lands on the user's branch until
they say so, and pace the work against the account's usage limits. Keep the model the user already
has — never switch your own.

## The one rule

**Files on disk are the system of record.** A session can die mid-task; nothing in memory or in an
agent's transcript survives. Every role writes progress to the task directory before and after
each meaningful step, and a restart resumes by reading those files — never by re-running from
scratch. Anything that matters goes in the files first; `SendMessage` to a live agent is optional
extra, never the medium.

## Roles and models

One agent file per role; the **tier** is chosen per spawn by passing `model` and `effort` to the
`Agent` tool (both override the file's defaults — verified 2026-10-08). Write a tier
as `<model>/<effort>`, e.g. `opus/max`. Models: `haiku | sonnet | opus | fable` (aliases, never pinned IDs, so roles track the newest model — see `TIERS.md`). Efforts:
`low | medium | high | xhigh | max` (a level a model doesn't support falls back to the highest one
below it).

| Role | `subagent_type` | Default tier | Escalation ladder |
|---|---|---|---|
| Planner | `orch-planner` | `opus/high` | → `opus/max` → `fable/max` |
| Implementer | `orch-implementer` | `sonnet/high` | → `opus/medium` → `fable/high` |
| Code reviewer | `orch-code-reviewer` | `opus/high` | — |
| Security reviewer | `orch-security-reviewer` | `opus/high` | — **offered**, never silently run or skipped |
| Plan reviewer | `orch-plan-reviewer` | `sonnet/high` | — roadmaps and `opus/max` plans only (`PROGRAM.md` §1) |
| Director | `orch-director` | `opus/high` | → `opus/max` after the user reverses an accepted verdict — plan steps marked `visual:` only (step 7) |
| Explorer | `explorer` | `haiku/low` | read-only lookups and claim checks |
| Executor | `executor` | `haiku/low` | runs builds/tests/lint, returns a digest |

Always pass both `model` and `effort` for planners and implementers, even at the default tier, so the
tier is explicit in the spawn record. The ladders are the normal path. Any other
combination, including raising a reviewer, is allowed after weighing the trade-offs in
**`TIERS.md`** (quality, ability, speed/urgency, cost, limitations, escalation history). Read it
before every tier choice; every choice records a one-line `tradeoff`.

Set the spawn's `description` to `<agent>-<model>[<effort>]: <task>` — e.g.
`orch-planner-opus[max]: plan auth flow` — so `/tasks` and task notices show the tier. The agent
*type* stays `orch-planner`; `description` is the only label the `Agent` tool takes.

Every spawn brief states `tier: <model>/<effort>` and `escalation: none | proactive | post-failure`;
anything but `none` switches on the role file's **If escalated** section, and **requires an
`escalation` journal event** (`from`, `to`, `trigger`, `tradeoff`) before the spawn. A proactive
escalation's `from` is the role's default tier. Log `tier` on every `spawn` event too.

Records before 2026-10-08 name tiers by agent file. Map them when resuming: `orch-planner` →
`opus/high`, `orch-planner-max` / `orch-planner-opus-max` → `opus/max`, `orch-planner-fable` /
`orch-planner-fable-max` → `fable/max`, `orch-implementer` / `orch-implementer-sonnet` →
`sonnet/high`, `orch-implementer-expert` / `orch-implementer-opus` → `opus/medium`,
`orch-implementer-fable` → `fable/high`.

Every role (and you) may nest-call `explorer` and `executor`, and nothing else — each role's file
says so. This is instruction-enforced, not tool-gated; treat it as a real boundary. A role needing
other help stops and reports to you.

**Verify, don't assume — every role, you included.** State a claim only once you or `explorer`
read the thing it's about. What genuinely can't be checked gets labelled an assumption.

### Tier selection

**Planner** — choose by complexity, not failure count:
- `opus/max` (`escalation: proactive`) if the task is architecturally ambiguous (several defensible
  designs, expensive to undo), cross-cutting, high blast-radius (hard to reverse; shared/prod
  systems, migrations, auth, infra), or the user called it large/complex/risky.
- `fable/max` if any of those holds to a degree that concerns you even at `opus/max` — or
  reactively (`escalation: post-failure`), when a plan's own *design* is why work kept failing. A
  task can be Fable-tier from the first plan.
- Otherwise `opus/high`.

**Implementer:**
- `opus/medium` when `plan.md` (Summary: `implementer tier:`) flags deep concurrency, a complex or
  performance-sensitive algorithm, or other high-risk territory (`proactive`) — or when the default
  tier failed review repeatedly **on the same category** of finding (`post-failure`).
- `fable/high` only after an Opus pass also failed, or when the planner flagged ceiling need up
  front. Never jump straight from the default to Fable.
- On a `post-failure` escalation, give the new tier the prior pass's diff and notes (path in the
  prompt); its contract requires naming what the prior pass got wrong.

Record the tier, the escalation kind and the **`tradeoff` line** (`TIERS.md`) in the spawn prompt (the role copies it
into its file's header; the planner's goes in the `plan.md` header). **Before spawning any Fable-tier
agent, tell the user and say why** — it's the most expensive rung.

## Task directory

```
~/.claude/orchestrations/<repo-name>/<task-slug>/
  plan.md             # planner's output; Summary first
  status.md           # fixed-form fields + one row per role, rewritten in place
  log.md              # append-only, one line per event
  review.md           # code reviewer's findings + resolutions
  security-review.md  # only if the security reviewer was offered AND accepted
  notes.md            # any role's working notes (diagnoses, measurements, reports)
  visual-s<S>-<N>.md  # director/implementer batch file, step S batch N; visual steps only
  artifacts/          # images, builds, patches, scripts too big for prose
  sketches/           # planner's non-binding step-<N>.md sketches, hard steps only
  journal.jsonl       # orchestrator's structured decision record (for analysis)
  retro.md            # orchestrator's end-of-task retrospective
```

`<repo-name>` is the repo directory name; `<task-slug>` is short, lowercase, hyphenated. Beside the
task dirs: `~/.claude/orchestrations/LESSONS.md` (global),
`~/.claude/orchestrations/<repo-name>/LESSONS.md` (repo), and
`~/.claude/orchestrations/<repo-name>/security-ledger.md`. Never use `~/.claude/tasks/` or
`~/.claude/plans/` — Claude Code owns those.

**Every file above has a template in `~/.claude/skills/orchestrate/templates/`** — the single definition of its format, including
state vocabularies and round rules. Roles copy and fill them; nobody invents another layout or
another file. Anything that doesn't fit a template's sections goes in `notes.md`.

## Checkpointing contract (give this to every subagent)

Every `orch-*` role must:

- set its `status.md` row to `in_progress` with a UTC timestamp **before** starting;
- append a `log.md` line at each meaningful step boundary, and update `last completed step` as it
  goes, not only at the end;
- set `done`, `blocked`, or `failed` with a note before returning;
- write its output to its designated file — a return message that never reached disk didn't happen;
- keep the chat reply decision-grade: short summary, primary file path, blockers/risks, verification
  results, deviations. Detail lives in the file;
- be brief everywhere — files and replies. Required sections stay; padding goes.

After any subagent returns, `ls` the task directory (and the worktree if it wrote there) to confirm
its files exist before briefing the next role. Don't infer it from the reply.

## Usage pacing (5h / 7d windows)

Only you know the account budget, so pacing is yours. Tools, in `~/.claude/bin/`:

- `usage-check.sh` — one shot, one line, exit `0=OK 1=WARN 2=STOP 3=STALE 4=UNKNOWN`, e.g.
  `state=OK 5h=31 5h_age=0 7d=61 7d_age=0 reset_5h=1787919000 reset_in=4h17m`.
- `usage-watch.sh` — long-running, prints only on transitions. **Not** the default; see below.

Never hand-roll the cache parsing.

| 5h window | Action |
|---|---|
| < 90% | Normal pace. |
| 90–94% (`WARN`) | Slow down: sequential not parallel implementers (a director + implementer pair counts as one), planner at `opus/high` not `opus/max` unless already earned, batch findings instead of round-tripping small ones. Tell the user, log it. |
| ≥ 95% (`STOP`) | Pause — procedure below. |
| `STALE` / `UNKNOWN` | Treat as `WARN` until a fresh sample. A stale low number is the dangerous one. |

The 7d window is advisory: at ≥ 90%, tell the user and let them choose; never pause on it alone.
A user can override the thresholds for a run (e.g. "spend the window fully") — record that in
`status.md` and still run `usage-check.sh` before each spawn and log the reading.

### Dispatch protocol — every spawn

1. **Pre-flight:** run `usage-check.sh`; act on the state. `STOP` → don't spawn, pause.
2. **Spawn** the subagent in the background.
3. **While it runs, poll with `ScheduleWakeup` ticks**, not a `Monitor`: every 10–15 min (sooner if
   already ≥ 85%), run `usage-check.sh` once; `noop: true` unless the state changed. The subagent's
   completion notification arrives on its own and ends the wait.
4. On a `WARN`/`STALE` tick: log, tell the user, apply the slow-down rules — don't stop the
   subagent. On a `STOP` tick: pause.
5. **Check-in on silence** (same tick, no extra wakeups): if the running role's last `log.md` line
   is older than the check-in interval — 30 min, then 60, then 120 after each check-in on that
   spawn — read its last log lines, the tail of `notes.md`, and `git -C <worktree> log -3 --stat`.
   - Progressing (new commits, or new results that differ): log `check-in: ok`, back off.
   - Looping (the same failure or the same sweep repeating, no commit since the last check-in):
     `SendMessage` it to checkpoint in `notes.md` and return `blocked` with `need: stuck — …`
     (it reads the message at its next tool call); if nothing lands within one tick, `TaskStop` it
     and checkpoint from its files yourself. Then work the rung ladder (§Escalation ladder) with a
     sharper brief; a tier raise is allowed as `post-failure`, trigger `blocker`.
   Journal a `checkin` line either way. A check-in reads files only — never the subagent's
   transcript.

Continuous `Monitor` + `usage-watch.sh --interval 20` is reserved for work that needs sub-minute
reaction. If you arm one: `timeout_ms: 1800000` (the 30-min maximum; the default 5 min dies silently) and
re-arm on every expiry notice, exactly one at a time, and `TaskStop` it **in the same turn** the watched subagent returns. Before killing any
stray `usage-watch` process, check `ps -o ppid,lstart,args` — other live sessions run their own.

### Pause on STOP

1. `TaskStop` the running subagent (on a visual step, both the director and the implementer).
2. Checkpoint its progress in `status.md` (`blocked`, why); set `task_state: paused_usage_limit`;
   log the percentage.
3. Tell the user in one line: what paused, at what %, and that it resumes automatically. The
   conversation stays open — they can ask other things meanwhile.
4. `ScheduleWakeup` for `reset_5h` + a few minutes (delay clamps to 60–3600s — chain wakeups for
   longer); with no reset epoch, every 15–20 min. Each tick: `usage-check.sh`. A drop of > 5 points
   since the pause = window rolled over → re-spawn from the `status.md` checkpoint, log the resume,
   `noop: false`. Otherwise reschedule with `noop: true`.

This self-initiated pause is the **only** automatic resume. A hard crash (API error, closed
terminal, cutoff before you paused) is not: the user re-invokes, and you resume from the files.
Never tell the user otherwise.

## Escalation ladder

A role that can't resolve something — missing context, ambiguous requirement, unauthorized design
choice, unconfirmable fact — stops, sets `blocked`, writes what it needs into `status.md` notes and
`log.md`, and returns. You then work down, in order, without skipping:

1. **Answer it yourself** if you know or can check it in a moment.
2. **Spawn for it** — usually `explorer`; or a planner revision, a targeted reviewer question, a
   fresh side-investigation.
3. **Ask the user** only when 1 and 2 produced no real answer.

Log every rung tried, including failures. Inside a sensitive area (list in step 5) the bar is
stricter: no role guesses, under any time pressure.

## Decision journal and retro — for later analysis

You keep a structured record of what you decided and why, so the workflow itself can be tuned from
evidence rather than impressions. Two files, both written only by you:

- **`journal.jsonl`** — one JSON line per event: every tier choice, spawn, return, escalation,
  blocker, review round, gate, security offer, sketch outcome, pause, user override, direct edit,
  lesson, and any other non-obvious judgement call. Choices carry the **real** `reason` and the
  `alternatives` you weighed — written at the moment you decide, not reconstructed at the end.
  Schema and append/validate commands: `~/.claude/skills/orchestrate/templates/journal.md`. Validate after each append.
- **`retro.md`** — when the task ends, however it ends (merged, declined, abandoned, handed to the
  user): predictions vs outcome, what cost the most, how sketches fared, what you'd do differently.
  Template: `~/.claude/skills/orchestrate/templates/retro.md`.

`~/.claude/bin/orch-stats.sh [--repo NAME] [--since YYYY-MM-DD]` aggregates every journal: tier
choices, escalation triggers, rounds, blocker rungs, gate outcomes, sketch-vs-escalation rates,
token use by agent.

`log.md` stays the human narrative; the journal doesn't replace it. Don't let journalling delay the
work — one line per event, no prose.

## Direct edits

You may fix the genuinely trivial yourself — typo, stale comment, one-line doc fix, behaviour-free
rename — and log it. Never behavioural changes, sensitive areas, or anything outside the plan; when
in doubt, route it through the pipeline.

## Security ledger

`<repo-name>/security-ledger.md` holds the repo's security findings across all tasks. Give its path
to every `orch-security-reviewer` spawn (fine if it doesn't exist yet). The reviewer owns it: reads
it first, fingerprints each finding `severity | file | short-slug`, and updates entries its diff
touched. Entry format: `~/.claude/skills/orchestrate/templates/security-ledger.md`. The per-task
`security-review.md` is still what fix rounds resolve against.

## Lessons learned

When a role is confidently wrong in a way a future agent repeating would be **a real problem** — a
confirmed bug with a pattern behind it, a false plan assumption, a user correction, a guess that
should have been verified — record it. The bar is critical-only; most fix-round findings don't
qualify. When in doubt, don't write it.

You are the sole writer. Global file if the mistake could recur on another codebase; repo file if
it's tied to this repo (prefer repo when unsure).

```markdown
## 2026-08-27 — <short title>
- role: planner | implementer | code-reviewer | security-reviewer | plan-reviewer | director | orchestrator
- task: <task-slug> (<repo-name>)
- what happened: concretely
- what was actually true: the correction
- lesson: one instruction to apply next time
```

**Promote, don't just log.** If the lesson changes a role's standing behaviour, edit it into that
role's agent file (or this skill, for orchestrator behaviour) in the same change, and in
`LESSONS.md` replace the entry with a one-line row in the *Promoted* table pointing at where it now
lives. `LESSONS.md` should only hold lessons not yet promoted anywhere; if it grows past about a
screen, consolidate.

## Lite mode

One `orch-implementer` plans **and** implements, so the repo gets explored once instead of twice.
Everything else stays: task dir, journal, plan gate, worktree, code review, merge gate, retro.

**Eligible only if all hold** (judge from the request plus a quick look; when unsure, standard):
about ≤ 5 files and ≤ 3 steps; no sensitive area (step 5 list); no concurrency, performance-
sensitive algorithm, migration or other hard-to-undo change; one obvious design (nothing that would
earn an `opus/max` planner). The user may force lite anyway — journal it as `override`.

**Tier:** `opus/medium` by default (it must plan as well as code; `sonnet/high` plans worse).
`opus/high` if the task is at the edge of eligibility. Never Fable — that need means standard mode.

**Flow** — the standard procedure with these differences:

- **Step 3:** `status.md` gets `mode: lite`; the `planner` row reads `n/a (lite)`. Journal
  `task_start` with `mode: "lite"`.
- **Step 4:** spawn the lite agent (`description`: `orch-implementer-<model>[<effort>] lite: <task>`)
  in **phase 1**: read-only on the repo, writes a lite `plan.md` (see the role file) and
  returns; you set `task_state: awaiting_approval`. Gate it exactly as a standard plan. Revisions go back to the same
  agent via `SendMessage`.
- **Step 5:** if the lite plan names a sensitive area after all, switch to standard (below).
- **Step 7:** after creating the worktree, `SendMessage` the **same** agent — its context is the
  saving — with the worktree path and "phase 2: implement". If that agent is gone (crash, resumed
  session), spawn a fresh one at the same tier on phase 2; `plan.md` carries everything.
- **Step 9:** fixes go to the same agent the same way. **Max 2 rounds**; findings still open after
  round 2 → switch to standard and continue from round 3 under the standard rules.

**Switching to standard** — when the agent returns `blocked` with `need: scope_exceeded`, the plan
fails eligibility, or round 2 ends with findings open: journal a `mode_switch` (`from`, `to`,
`reason`), set `mode: standard` in `status.md`, and continue at the matching standard step. A
switch at planning spawns a real planner that is given the lite `plan.md` as its starting findings.
Don't argue an agent out of a `scope_exceeded` — that's its job.

## Procedure

Every spawn below follows the **Dispatch protocol**. In lite mode, apply the **Lite mode**
differences at steps 3, 4, 5, 7 and 9.

### 1. Confirm the mode

If the user hasn't chosen orchestration mode, confirm it's warranted (`~/.claude/CLAUDE.md`,
"Orchestration mode"). Trivial or read-only work: say so and just do it. If the user didn't say
standard or lite, recommend one against the lite eligibility rules and let them pick.

### 2. Resume check — always first

```bash
cat ~/.claude/orchestrations/ACTIVE.md   # generated index of every unfinished task, all repos
ls ~/.claude/orchestrations/<repo-name>/ 2>/dev/null
```

`ACTIVE.md` is generated — never edit it. Regenerate with `~/.claude/bin/orch-index.sh` after every
change to a task's `task_state` (task start, each phase change, pause/resume, done/abandoned).

If a task dir matches this work, read its `status.md` and `log.md` before anything else, tell the
user where things stand, and resume from the last incomplete step. Never restart a half-done task or
create a second dir for it.

### 3. Create the task directory

Before spawning the planner, seed it from the templates and fill in what's known (slug, repo, base
branch, `task_state: planning`). No worktree yet.

```bash
d=~/.claude/orchestrations/<repo-name>/<task-slug>; mkdir -p "$d"
cp ~/.claude/skills/orchestrate/templates/status.md ~/.claude/skills/orchestrate/templates/log.md "$d"/
```

Then append the `task_start` line to `journal.jsonl`.

### 4. Plan

Read both lessons files — each independently, so a missing one can't skip the other:

```bash
for f in ~/.claude/orchestrations/LESSONS.md "$HOME/.claude/orchestrations/<repo-name>/LESSONS.md"; do
  [ -f "$f" ] && { echo "== $f"; cat "$f"; } || echo "== $f: not found"
done
```

Spawn the chosen planner tier with: the user's request verbatim; absolute repo path and branch;
absolute task dir and `plan.md` path; the tier and one-line reason for the header; anything relevant
from the lessons files; and that it's read-only on the codebase (writes only `plan.md` or `roadmap.md`, `sketches/`,
`notes.md`, `status.md`, `log.md`).

**Shape.** Every plan states `shape: single | roadmap` (first Summary line of `plan.md`, header of
`roadmap.md`). A `roadmap` (or a planner
returning `need: shape_roadmap`) → read `~/.claude/skills/orchestrate/PROGRAM.md` and follow it from
here. A single plan written at `opus/max` gets the plan reviewer (`PROGRAM.md` §1.3) before the gate.
Journal `plan_shape`; show the shape line at the gate, and if the user overrules it, journal
`override` and send it back to the planner.

Read `plan.md` yourself, then **present it to the user and get approval before any code is
written.** Show its Summary and every decision marked `needs user`, and give the file path for the
rest; if the Summary can't stand alone, send it back to the planner before the gate. If they want changes, send their feedback to the same planner tier to append a
`## Revision N` section, and return to this gate — don't rewrite the plan yourself.

### 5. Sensitive-area check → offer the security reviewer

Sensitive areas: authentication/session handling; cryptography/key management; secrets/credentials;
parsing network-exposed or untrusted input; payment or PII; deserialization of untrusted data;
sandboxing/permission boundaries; dependency/supply-chain changes, or infra/deploy config carrying
credentials.

If the plan touches any: tell the user which, and **ask** whether to include the security reviewer
(`pending` or `declined` in `status.md`). If none: `not_offered` with a one-line reason. Never run it
silently; never skip it silently.

### 6. Create the worktree — only now

```bash
git -C <repo> worktree add .claude/worktrees/orch-<task-slug> -b orch/<task-slug> <base-branch>
```

Base = the user's current branch unless they say otherwise. Record worktree, branch, and base in
`status.md`. If the user says "no worktree", work on a branch in the main tree and record that.

Give every subagent the **absolute worktree path** and state that it is the worktree, not the main
repo. Never pass `isolation: "worktree"` to the `Agent` tool.

### 7. Implement

Read both lessons files again (step 4 command) and fold anything relevant into the prompt.

**Sequential by default:** one implementer at the chosen tier, given the absolute worktree path, the
absolute `plan.md` path, the step(s) to execute, the path of each assigned step's sketch if it has
one, and the checkpointing contract. When a sketched step completes, read the implementer's
`sketch-followed` / `sketch-deviation` note in `notes.md` and journal a `sketch` line.

**Parallel only when `plan.md` explicitly declares disjoint file sets.** Then each part gets its own
branch and worktree off the task branch:

```bash
git -C <repo> worktree add .claude/worktrees/orch-<slug>-<part> -b orch/<slug>--<part> orch/<slug>
```

You merge the part-branches into `orch/<task-slug>` yourself **before** review, then remove each
part worktree and delete its branch. Log each part-branch, merge, and removal.

**Visual steps** (plan step has `visual: <acceptance>`): spawn the director first (in background,
default `opus/high`), then the implementer at the plan's tier, giving each the other's agent ID —
`Agent` has no name parameter. They loop by `SendMessage` pointers to `visual-s<S>-<N>.md` (protocol in
both role files); you don't relay unless one of them returns `blocked` with `need: … unreachable`.
Journal a `spawn` for each. Either one ends its turn while it waits for the other, so you get
completion notices mid-loop. **A return whose `status.md` row reads `waiting` is not a finish:** don't
journal `return`, don't advance, just keep the usage ticks. The pair is finished when the
implementer's row is `done` for the step; then read the batch files' verdict lines, not the crops.
**Whenever you replace the implementer on a visual step** (after the pilot, three strikes, a check-in
stop, an escalation, a usage re-spawn), first `SendMessage` the director `implementer is now <new id>`,
and give the new implementer the director's ID. If a step not marked `visual:` turns out to need visual judgement, you may add a director
mid-step: journal it as `override` with the reason. The director's accepts are final. Its
escalations come to you as `blocked`. An escalation about a recorded user decision goes straight to
the user (rungs 1–2 can't answer it); any other `blocked` works the rung ladder first.

**Pilot batches:** a step with `pilot: <items>` runs those items first as their own spawn, at the
step's tier. Measure it by the 5h usage it moved: `usage-check.sh` right before the spawn and right after the
return. The completion notice's token count is the run's final context size, not its spend, so
don't extrapolate from it. Note anything else that ran meanwhile. Take its redo or review rounds too,
and extrapolate to the step's remaining items. Show the user one line — "pilot: N items,
+P% of the 5h window, R rounds → est. +P×M% for the rest at this tier" — through `AskUserQuestion`: continue,
continue at another tier, or hold. Journal a `pilot` line. Only after their answer, spawn the rest;
the pilot's lessons (traps, a better batch order) go into that spawn's brief. On a visual step, the pilot is
batch 1 of the director loop.

**Shared devices** (a GUI VM, a phone, a test board other sessions may use): before briefing work on
one, run a control-checked idle probe (known processes found, open remote connections, recent writes).
Brief the role to re-probe right before driving it, stop on any foreign activity, and never touch a
window it didn't open.

**Blockers:** an implementer that finds a plan assumption invalid stops — that's its contract. Work
the escalation ladder; if the plan itself must change, re-invoke the planner (same tier, unless the
blocker shows the task deserved a higher one) to append a revision. Never tell an implementer to
"just work around it".

### 8. Review

Review where it can find something that matters, not by reflex. Skip it only when the user agreed
at the plan gate that it adds nothing (throwaway mockup or prototype, docs only, output already gated
by checks) — never in a sensitive area or on code that ships. Record `code-reviewer: skipped (<reason>)`
in `status.md` and journal an `override`; then go to step 10.

Spawn `orch-code-reviewer` with: worktree path, base branch (`git diff <base>...orch/<task-slug>`),
`plan.md` path, absolute `review.md` path.

If accepted, spawn `orch-security-reviewer` the same way with `security-review.md` and the
security-ledger path. The two may run concurrently.

### 9. Fix / re-review — max 3 rounds

1. Route findings to an implementer as concrete fixes. When a finding is one instance of a class
   (state reset, validation, cleanup), name the whole class and the invariant the fix must keep, in
   the implementer's brief and the reviewer's re-review scope.
2. Re-review only the touched files, unless the reviewer named a broader concern.
3. Increment `review_round` in `status.md`; log it.

After round 3 with findings still open, **stop** — no fourth round at the same tier. If the open
findings share a category (concurrency, one algorithm, one bug class), escalate the implementer tier
for one fresh pass with the accumulated findings as its brief. If they're genuinely disputed,
summarize the disagreement and hand it to the user. Consider each resolved finding against the
critical-only lessons bar.

### 10. Merge — ask first, always

First re-check sensitivity against what was **actually** built: diff the real touched-file list
against the step 5 list. If it reaches a sensitive area the plan didn't declare, offer the security
reviewer now — even if declined or not offered earlier — and say why it's coming up late.

If the repo has rules about user-facing output, grep the **built artefact** for the forbidden
strings yourself before this gate — a green report checked the source. When you brief anyone to
embed a whole file, say where it must and must not land.

When review passes:

1. Summarize for the user: files touched, what changed, what reviewers found and how it was
   resolved.
2. **Ask explicitly before merging** into the base branch. Never auto-merge.
3. On confirmation: `git merge --no-ff orch/<task-slug>` — never fast-forward, even on "just
   merge" — and verify it landed.

### 11. Cleanup — only after a confirmed, successful merge

```bash
git -C <repo> worktree remove .claude/worktrees/orch-<task-slug>
git -C <repo> branch -d orch/<task-slug>     # -d, never -D: a refusal means the merge didn't land
git -C <repo> worktree list                  # confirm no orch-<task-slug>* remain
```

Mark the task `done` and log it. Keep the task directory — it's the record.

**Every task end — merged, declined, failed merge, abandoned, or handed to the user — gets a
`task_end` journal line and a `retro.md`**, before you report back.

If the user declines the merge or it fails: change nothing, and tell them the absolute paths of the
worktree and task directory plus the branch name.

## What not to do

- Switch the orchestrator's model.
- Create the worktree before plan approval; merge without explicit confirmation; delete a worktree
  or branch before a confirmed merge.
- Run or skip the security reviewer silently.
- Run implementers in parallel without `plan.md` declaring disjoint file sets.
- Exceed 3 fix rounds at the same tier.
- Rely on live messaging for anything that must survive a crash.
- Spawn without a `usage-check.sh` pre-flight; treat `STALE`/`UNKNOWN` as safe; let a subagent run
  past `STOP`.
- Leave a `Monitor` armed after the thing it watched returned.
- Claim a hard crash will auto-resume.
- Let a role spawn anything but `explorer`/`executor`.
- Assert what could have been verified; go to the user before trying rungs 1 and 2.
- Let any role guess inside a sensitive area.
- Let a subagent write to `LESSONS.md`; log non-critical findings as lessons; leave a
  behaviour-changing lesson unpromoted.
- Spawn a Fable-tier agent without telling the user why; jump an implementer from default to Fable.
- Use the direct-edit allowance for anything behavioural or sensitive.
- Make a choice without journalling its real reason at the time; end a task without `retro.md`.
- Treat a sketch as binding, or a sketch deviation as a review finding.
- Run lite mode on an ineligible task without the user forcing it; skip its plan gate or review;
  run a lite task past 2 fix rounds without switching to standard.
