---
name: orchestrate
description: Run a multi-agent orchestration workflow (planner → implementer → code review → security review by rule → merge) for non-trivial, multi-step engineering work. Use when the user picks "orchestration mode", says "orchestrate this", "run this with subagents", "plan then implement then review", or accepts the orchestration offer for a feature, refactor, or multi-file bugfix. State lives in per-task Markdown files (via the `orch` CLI); usage-paced; merges automatically when clean; works in a git worktree. Also has a **lite mode** (one agent plans and implements) for small, low-risk tasks — use when the user picks "lite mode" / "orchestrate lite" — and **program mode** (a roadmap of milestones, `PROGRAM.md`) — use when the user asks to "plan this in milestones", "roadmap this", or resumes a multi-milestone program — and **interactive mode** (this session plans with the user, subagents implement and review) — use when the user picks "interactive mode", "orchestrate interactive" or "plan it with me".
---

# Orchestration mode

You are the **orchestrator**: you coordinate specialist roles, keep all shared state in Markdown
files on disk, run the work in a dedicated git worktree so nothing lands on the user's branch until
review and the final gate pass (step 10), and pace the work against the account's usage limits. Keep the model the user already
has — never switch your own.

## The one rule

**Files on disk are the system of record.** A session can die mid-task; nothing in memory or in an
agent's transcript survives. Every role writes progress to the task directory before and after
each meaningful step, and a restart resumes by reading those files — never by re-running from
scratch. Anything that matters goes in the files first; `SendMessage` to a live agent is optional
extra, never the medium.

## The `orch` CLI

Bookkeeping is one call per state transition: `~/.claude/bin/orch [-t <task>] <command>` (`orch --help`;
`<task>` is `<repo>/<slug>` or an absolute task dir). Exit 0 ok; 1 usage or validation error and 2 refused
by a guard or policy (usage STOP, a merge condition) write nothing; 3 internal error (for `merge`: the merge
commit exists): read the message.

**Fallback:** `orch` missing, or failing in a way a corrected call won't fix → edit the files by hand
per their templates (then `~/.claude/bin/orch-index.sh` refreshes `ACTIVE.md`) and say so in the log line.

**Old-format tasks** (no `briefs/` or `questions.md`, journal lines keyed `event`, status.md missing
newer fields) work as they are. A row `orch` can't parse (exit 1) is fixed by hand per the template; if
`orch brief` fails, give the role the worktree path (step 6), the plan path, its step text, each step's sketch
and guide path, the checkpointing contract and the lessons (step 4); a reviewer the base (`git diff <base>...orch/<task-slug>`),
the plan path and the review-file path. Reviews or a gate recorded
without a sha can't satisfy `orch merge`: it refuses and the merge goes to the user (step 10). Never
`orch migrate` a task another session may be working on (`--dry-run` is safe).

## Roles and models

One agent file per role; the **tier** is chosen per spawn by passing `model` and `effort` to the
`Agent` tool (both override the file's defaults). Write a tier
as `<model>/<effort>`, e.g. `opus/max`. Models: `haiku | sonnet | opus | fable` (aliases, never pinned IDs, so roles track the newest model — see `TIERS.md`). Efforts:
`low | medium | high | xhigh | max` (a level a model doesn't support falls back to the highest one
below it).

| Role | `subagent_type` | Default tier | Escalation ladder |
|---|---|---|---|
| Planner | `orch-planner` | `opus/high` | → `opus/max`; `fable/max` only after an `opus/max` plan failed (or the user asks) |
| Implementer | `orch-implementer` | `sonnet/high` | → `opus/medium` → `opus/high` → `opus/max` → `fable/high` (Fable only after a failed `opus/max` pass, or the user asks); `haiku/medium` for gated steps (`TIERS.md`) |
| Code reviewer | `orch-code-reviewer` | `opus/high` | — |
| Security reviewer | `orch-security-reviewer` | `opus/high` | — **by rule** (step 5) or on request; every run or skip journaled |
| Plan reviewer | `orch-plan-reviewer` | `sonnet/high` | — roadmaps and `opus/max` plans only (`PROGRAM.md` §1) |
| Director | `orch-director` | `opus/high` | → `opus/max` after the user reverses an accepted verdict — plan steps marked `visual:` only (step 7) |
| Explorer | `orch-explorer` | `haiku/low` | read-only lookups and claim checks |
| Graph | `orch-graph` | `haiku/low` | keeps the code index (orch-index) fresh (check; rebuild when stale or missing; install a missing binary once, only under its install conditions); you, the planner or the user call it, never the explorer. The explorer is the only role that queries the index |
| Executor | `orch-executor` | `haiku/low` | runs builds/tests/lint, returns a digest |
| Init | `orch-init` | `sonnet/high` | → `opus/medium` for an ecosystem with no `skills/orch-init/references/<ecosystem>.md`; sets up or audits a project's agent files for the `orch-init` skill (step 3a) |

Always pass both `model` and `effort` for planners and implementers, even at the default tier, so the
spawn record states it. The ladders are the normal path; any other combination, including raising a
reviewer, is allowed after weighing **`TIERS.md`** (quality, ability, speed, cost, limitations, escalation
history). Read it before every tier choice; each records a one-line `tradeoff`.

Set the spawn's `description` to `<agent>-<model>[<effort>]: <task>`, e.g.
`orch-planner-opus[max]: plan auth flow`, so `/tasks` shows the tier (the agent *type* stays `orch-planner`).

Every spawn brief states `tier: <model>/<effort>` and `escalation: none | proactive | post-failure`;
anything but `none` switches on the role file's **If escalated** section, and **requires an
`escalation` journal event** (`from`, `to`, `trigger`, `tradeoff`) before the spawn (`orch spawn`
writes it). A proactive escalation's `from` is the role's default tier. Every `spawn` event carries the `tier`.
Records before 2026-10-08 name tiers by agent file; map them when resuming (`orch-planner` → `opus/high`,
`…-max` / `…-opus-max` → `opus/max`, `…-fable[-max]` → `fable/max`; `orch-implementer` / `…-sonnet` →
`sonnet/high`, `…-expert` / `…-opus` → `opus/medium`, `…-fable` → `fable/high`).

Every role (and you) may nest-call `orch-explorer` and `orch-executor`, and nothing else; you and `orch-planner`
may also call `orch-graph` (instruction-enforced, not tool-gated: a real boundary; each role's file says so).
A role needing other help stops and reports to you.

**Verify, don't assume — every role, you included.** State a claim only once you or `orch-explorer`
read the thing it's about. What genuinely can't be checked gets labelled an assumption.

**Your own lookups go to `orch-explorer`** ("where is X", "does Y exist", "who uses Z"): your first search
on any question that is not about one file you were handed is an explorer spawn (batch several per spawn). You read plan, status, brief and task files yourself.

### Tier selection

**Planner** — choose by complexity, not failure count: `opus/max` (`escalation: proactive`) if the task
is architecturally ambiguous (several defensible designs, expensive to undo), cross-cutting, high
blast-radius (hard to reverse; shared/prod systems, migrations, auth, infra), or the user called it
large/complex/risky; otherwise `opus/high`. `fable/max` only reactively (`escalation: post-failure`), when
a plan written at `opus/max` failed because its own *design* is why work kept failing, or when the user asks
for it directly. No first plan is ever Fable-tier.

**Implementer** — route every spawn (plan step, fix round, pilot, lite task) with `TIERS.md`
§ Implementer routing (first match wins; `sonnet/high` unless it routes elsewhere). On a `post-failure`
escalation, give the new tier the prior pass's diff and notes (their paths in the `--extra` file of its `orch brief`); its contract
requires naming what the prior pass got wrong.

Record the tier, the escalation kind and the **`tradeoff` line** (`TIERS.md`) in the spawn prompt (the role copies it
into its file's header; the planner's goes in the `plan.md` header). For every role that has a brief
(implementer, director, code and security reviewer, fix rounds) pass the three to `orch brief` (`--tier`,
`--escalation`, `--tradeoff`) and send the spawn prompt it prints, unchanged: never a hand-written one. `--extra`
carries facts, never test scope or gate instructions; only a step's `full-gate:` field lets the implementer run
the gate. `--extra` takes the path of a file, not inline text. Any other value a spawn needs goes in that file, and
so into the brief: the prior pass's diff and notes paths on a `post-failure` spawn, the security-ledger path for the
security reviewer, a peer's agent ID on a visual step. **Fable starts only after an `opus/max` pass on this
work failed, or when the user asks for it directly; before spawning any Fable-tier agent, tell the user and say
why** — it's the most expensive rung.

## Task directory

```
~/.claude/orchestrations/<repo-name>/<task-slug>/
  plan.md             # planner's output; Summary first
  status.md           # fixed-form fields + one row per role, rewritten in place
  log.md              # append-only, one line per event
  review.md           # code reviewer's findings + resolutions
  security-review.md  # only if the security reviewer ran (step 5)
  notes.md            # any role's working notes (diagnoses, measurements, reports)
  questions.md        # parked and assumed questions (`orch ask`)
  briefs/             # one role's context per spawn (`orch brief`)
  visual-s<S>-<N>.md  # director/implementer batch file, step S batch N; visual steps only
  artifacts/          # images, builds, patches, scripts too big for prose
  sketches/           # planner's non-binding step-<N>.md sketches, hard steps only
  guides/             # planner's step-<N>.md guides for Haiku-routed steps (a pattern to copy; TIERS.md)
  journal.jsonl       # orchestrator's structured decision record
  retro.md            # orchestrator's end-of-task retrospective
```

`<repo-name>` is the repo directory name; `<task-slug>` is short, lowercase, hyphenated. Beside the
task dirs: `~/.claude/orchestrations/LESSONS.md` (global),
`~/.claude/orchestrations/<repo-name>/LESSONS.md` (repo),
`~/.claude/orchestrations/<repo-name>/security-ledger.md`, and optionally `<repo-name>/policy.md`
(`- merge: auto | ask`, `- security: rule | always | ask`; absent = `auto`, `rule`). Never use
`~/.claude/tasks/` or `~/.claude/plans/` — Claude Code owns those.

The `orch-init` skill (step 3a) also keeps `<repo-name>/init.md` (its record), `<repo-name>/init-proposal.md` (the proposal) and `<repo-name>/audit.md` (an audit's findings) beside the task dirs.

**Every file above has a template in `~/.claude/skills/orchestrate/templates/`** — the single definition of its format, including
state vocabularies and round rules. Nobody invents another layout or
another file. Anything that doesn't fit a template's sections goes in `notes.md`.

## Checkpointing contract (give this to every subagent)

Every `orch-*` role must, one call each (the brief's *Checkpoint commands* have them filled in):
`~/.claude/bin/orch -t <task> role <role> <state> --step <step> --note "<note>" --log "<what>"`.

- set its `status.md` row to `in_progress` with a UTC timestamp **before** starting;
- append a `log.md` line at each meaningful step boundary, and update `last completed step` as it
  goes, not only at the end;
- add `--pct N` (0–99, your rough estimate of your current assignment; `0` at start) to its
  `in_progress` checkpoints, so `orch status --json` shows how far along each running role is;
- set `done`, `blocked`, or `failed` with a note before returning;
- write its output to its designated file — a return message that never reached disk didn't happen;
- keep the chat reply decision-grade: short summary, primary file path, blockers/risks, verification
  results, deviations. Detail lives in the file;
- be brief everywhere — files and replies. Required sections stay; padding goes;
- if `orch` errors or is missing, edit the files by hand per the templates and say so in the reply.

After any subagent returns, `orch return <role>`: it journals the return and prints the row, the task-dir
files changed since its spawn and, for `haiku/*`, its `contract-check`. Confirm its files exist (the
worktree too: `git -C <worktree> log -3 --stat`) before briefing the next role. Don't infer it from the reply.

## Usage pacing (5h / 7d windows)

Pacing is yours (local files, no tokens):

- `orch usage` — one line (`state=… 5h=… est=… line=… 7d_proj=…`), exit `0=OK 1=WARN 2=STOP 3=STALE
  4=UNKNOWN`; `usage-check.sh` prints the same. `est` = last sample + every session's transcript cost
  since; `line` is the dynamic stop line (≈ 95–99).
- `orch -t <task> usage wait` — background, silent; ends only on an event (dispatch step 3).

Never hand-roll cache parsing or compare a raw % to a fixed number: the line moves.

| State | Action |
|---|---|
| `OK` | Normal pace. |
| `WARN` (`est` within 5 of the line, or < 30 min to it) | Slow down: sequential not parallel implementers (a director + implementer pair counts as one), planner at `opus/high` not `opus/max` unless already earned, batch findings instead of round-tripping small ones. Tell the user, log it. |
| `STOP` (`est` ≥ line) | Pause — procedure below. |
| `STALE` / `UNKNOWN` | Treat as `WARN`. A stale low number misleads. |

7d is advisory: at 7d ≥ 90% or `7d_proj` ≥ 100, tell the user; never pause on it alone. A user override
(e.g. "spend the window fully"): record `usage_override` in `status.md`, pass `--line N` to the wait, log
the reading before each spawn (`orch spawn` has no override flag: for a `STOP` the user allowed, use
`orch journal`).

### Dispatch protocol — every spawn

1. **Pre-flight and journal, one call:** `orch spawn <role> --agent <agent> --tier <tier> --steps <steps>
   --reason "<why>" --alt "<alternative>" --tradeoff "<one line>"` runs the estimator, then writes the
   `tier_choice`, `escalation` (when raised: add `--escalation proactive|post-failure --from <tier>
   --trigger <trigger>`) and `spawn` lines. `STOP` → exit 2, nothing written: don't spawn, pause.
   `WARN`/`STALE`/`UNKNOWN` warn once: act on it. It also prints `usage wait: armed (pid N)` or `not armed`.
2. **Spawn** the subagent in the background.
3. **Notify, don't poll.** If `not armed`, start `orch -t <task> usage wait` with Bash `run_in_background`
   in the same turn, then wait; no `ScheduleWakeup` ticks while armed. "failed with exit code N" is its
   event, not an error (output file: only to name the CHECKIN role): `2` STOP → pause · `10` RESET →
   resume · `11` CHECKIN → step 5, re-arm · `12` REFRESH → re-arm only (`noop`) · `13` ENDED → re-arm unless
   the task ended · `0` already armed → nothing · `4`, other codes, `killed` → no monitor: one `ScheduleWakeup`
   tick ≤ 8 min running `orch usage` with check-in on silence, re-arming the wait once. Two exits < 60 s
   apart (not 2/10), or the re-armed wait failing again → ticks until the next resume; tell the user once.
   Never `TaskStop` the wait when a subagent returns; at task end only if `orch end` didn't end it.
4. On `WARN`/`STALE`: log, tell the user, apply the slow-down rules; don't stop the subagent. `STOP`: pause.
5. **Check-in on silence** (a CHECKIN exit, or a fallback tick): if the running role's last `log.md` line
   is older than the check-in interval — 30 min, then 60, then 120 after each check-in on that
   spawn — read its last log lines, the tail of `notes.md`, and `git -C <worktree> log -3 --stat`.
   Progressing (new commits or differing results): log `check-in: ok`, back off. Looping (the
   same failure or sweep repeating, no commit since the last check-in): `SendMessage` it to checkpoint
   in `notes.md` and return `blocked` with `need: stuck — …` (it reads it at its next tool call); if
   nothing lands within one tick, `TaskStop` it and checkpoint from its files yourself, then work the
   rung ladder with a sharper brief (a tier raise: `post-failure`, trigger `blocker`).
   Journal a `checkin` line either way. Check-ins read files, never the transcript.
   **`final=<state>` in the CHECKIN line** (`CHECKIN role=<r> silent_min=5 interval=5 final=done ...`): the
   role's row is `done`, `blocked` or `failed` but its spawn has no `orch return` yet, so you never saw it
   finish. Don't judge progress: run `orch return <role>` now (Checkpointing contract), then act on its outcome. It fires once per
   spawn, 5 minutes after the row went final, whatever check-ins came before; it is not one of the 4 silence check-ins;
   a `waiting` row never fires it.

**Index rebuild pause.** Two triggers call `orch-graph`.

- **Pre-plan call (yours):** once before each plan, in every mode: step 4's planning (standard, lite,
  interactive) and each program milestone's detail plan. Ask it for a `pre-plan` run on the
  repository's main checkout (`--repo <path>`). It counts tracked files with an indexed extension and
  stops with `no indexable files` when there are none; otherwise it builds when there is no index and
  rebuilds when the index is stale or behind HEAD (`commit` ≠ HEAD), and leaves a current one alone.
  It is also the call that may install a missing `orch-index` binary. Log the result:
  `orch set --log "pre-plan index: <state>"`.
- **Stale hint:** when the explorer's reply says the index is stale
  (`index unavailable: stale … (refresh: orch-graph)`) on a repository that has an index. Never for
  `no index`, `binary not found`, an index that is only behind HEAD (`index at <sha> + delta search`)
  or a stale line without the refresh hint (the explorer omits it in a linked worktree, where a rebuild
  would make the main checkout's index stale). Only the pre-plan call handles those states.

Never at any other time or automatically. Never two at once, and never while another index run is in progress (a live
`<git-common-dir>/orch-index/indexing` marker, or a running `orch-graph` spawn).
`orch-graph` installs a missing binary only when the caller did not ask for check only,
`ORCH_INDEX_AUTOINSTALL` is not `0`, `ORCH_INDEX_BIN` is unset or empty, no `orch-index` is on `PATH`,
and `~/.claude/bin/orch-index` does not exist; it then runs `~/.claude/bin/orch-install-code-index.sh`
once (Bash timeout 300 s) and, if `stats` still fails after that, reports the script's last line and
stops. Point it at the repository's main checkout, never a task worktree (from a linked worktree it
does not rebuild). Spawn it in the **foreground** (it writes
`<git-common-dir>/orch-index/indexing` for the rebuild, removes it after, and the explorer's first
command waits on that marker). While an `orch-graph` spawn runs, spawn nothing else (no explorer, no
implementer, no reviewer) and do no exploring of your own. The same binds `orch-planner` when it calls
`orch-graph`. This is instruction-level: another person's session, or a role that never reads this
rule, is not blocked.

### Pause on STOP

1. `TaskStop` the running subagent (on a visual step, both the director and the implementer).
2. Checkpoint its progress in `status.md` (`blocked`, why); set `task_state: paused_usage_limit`; log the
   estimate: `orch journal pause usage_5h:=<N> --set task_state=paused_usage_limit --log "paused at <N>%"`.
3. Tell the user in one line: what paused, at what %, that it resumes automatically.
4. Arm `orch -t <task> usage wait` in the background: seeing `paused_usage_limit` it stays silent and exits
   `10` once the window has rolled over. Then re-spawn from the `status.md` checkpoint, journal `resume`
   (restoring `task_state`), re-arm. Other exit: `ScheduleWakeup` at `reset_5h` + a few minutes (clamps to
   60–3600s — chain; no epoch: every 15–20 min) running `orch usage`; a drop of > 5 points since the pause
   = rolled over, else `noop: true`.

This self-initiated pause is the **only** automatic resume. A hard crash (API error, closed
terminal, cutoff before you paused) is not: the user re-invokes, and you resume from the files.
Never tell the user otherwise.

## Escalation ladder

A role that can't resolve something — missing context, ambiguous requirement, unauthorized design
choice, unconfirmable fact — stops, sets `blocked`, writes what it needs into `status.md` notes and
`log.md`, and returns. You then work down, in order, without skipping:

1. **Answer it yourself** if you know or can check it in a moment.
2. **Spawn for it** — usually `orch-explorer`; or a planner revision, a targeted reviewer question, a
   fresh side-investigation.
3. **Ask the user** only when 1 and 2 produced no real answer. A blocker that is a **reversible**
   choice, **outside every sensitive area** (step 5 list) and contradicts **no recorded user
   decision** isn't asked: take the recommended option and continue, recording it —
   `orch ask "<question>" --options "A|B" --recommended A --reversible yes --assume A` (status `assumed`,
   journaled, in every later brief, listed in the digest). Any other question is parked with a
   notification (next section); only the steps that depend on it wait.

Log every rung tried, including failures. Inside a sensitive area (list in step 5) the bar is
stricter: no role guesses, under any time pressure — and nothing is assumed there.

## Review loops

One rule for every loop that goes round and round until something is clean. A **round** is one reviewer pass over
work that must be fixed before the task goes on. Each loop keeps its own cap; **this table is the only place a cap
is written**, and the other files point here.

| Loop | Cap | What starts a round | At the cap |
|---|---|---|---|
| Code review (step 9) | 3 rounds | a review whose blocker chain ended on a passed check: one **fresh** round N+1 (round 1 is the first review). Should-fix alone never starts one, nor does a nit. A commit landing after a review or check passed also starts one (below) | stop: no fourth round at the same tier (step 9) |
| Code review fix-and-check cycles (step 9) | 3 per round | any finding left open (blocker or should-fix, routed or new) at the review or at a check: one more fix batch, then a check by the same reviewer. Checks are not rounds | a blocker still open: step 9's stop; only should-fix open: parked with `orch follow-up`, and the review passes |
| Security review (step 9) | 3 rounds | any routed security finding; all are routed, nits too (`orch merge` needs 0 nits) | same stop |
| Lite mode (`LITE.md`) | 2 rounds | as code review | switch to standard and go on from round 3 |
| Director, per item (`orch-director`) | 2 redo rounds | a redo verdict, or a `stuck` | record both variants; pick or escalate (the director's file); never a third |
| Plan reviewer (`PROGRAM.md` §1.3) | 3 passes | a `blocker` finding | a parked question: the open blockers go to the user |

- **Raising a cap** (a loop that is converging, one more round is cheaper than a restart): an orchestrator running
  at Opus level or higher decides it alone, with no alert, and journals it (`orch journal override what="<loop> cap
  raised to N: <why>"`). Any other orchestrator parks a question (`orch ask`). Never raise a cap to repeat the same
  failing fix at the same tier.
- **Code review is fix, check, then a fresh round after a blocker.** One review's blockers and should-fix always go
  to the implementer in **one batch**; a batch plus its check is one **cycle** (3 per round). What follows:
  - **The check.** After any review with something open, `orch brief review --check` (same round number,
    `check:=true cycle:=K`, its own brief) has the **same reviewer** read only the fix commits; step 7 says how it is
    resumed. A check is not a round and does not touch `review_round`. `--check` refuses (exit 2) after a review that
    left nothing open, after a check that left nothing open (that commit takes round N+1), and as a 4th check of one
    round. The first check keeps the range from the review's sha; check K starts at check K-1's sha.
  - **Anything open at a check** starts the next cycle: a routed finding the check reports **not fixed**, a new
    **blocker** or a new **should-fix**. Route all of it as one batch (the implementer climbs a tier each cycle,
    `TIERS.md` § Fix rounds), then check K+1. A new **nit**: the digest. `orch brief review` for round N+1 refuses
    while the latest check left something open.
  - **Nothing open at a check** ends the chain. If round N or any of its checks held a **blocker**, brief one
    **fresh round** N+1 (`orch brief review`): its range runs from round N's reviewed sha, the reviewer reads the code
    around the fixes and does not re-raise closed findings. `orch merge` refuses a blocker chain that ends on a
    check, so the fresh round cannot be skipped. A chain that only ever held should-fix passes at the clean check.
  - **At the cap** (check 3 still open): a **blocker** open is step 9's stop. Only **should-fix** open: park each with
    `orch follow-up "<text>" --finding <id> --severity should-fix --file <path>` (in `<repo-name>/follow-ups.md`, plus a
    `follow_up` journal line); parked findings are not counted open, so the review passes.
  - **The tip moved after a review or check passed** (a gate failure fixed at the tip, a security fix commit): the
    passed line no longer covers the tip, and `orch merge` needs a code line at the tip. Brief a real round N+1
    (`orch brief review`, narrow range from the last reviewed sha), not a check. It is counted in `review_round`
    and against the cap, so at the cap it is the raising rule above (Opus level or higher: raise and journal
    `override`; any other orchestrator: `orch ask`), never a silent extra round (step 9's stop).
  - `verdict` is `pass` only with zero findings open. Pre-existing findings go to the digest, as before.
- **Security stays on rounds.** Every routed security finding is fixed and re-reviewed as the next round of the
  security loop; there is no check and no follow-up lane for it. Round 2 and up resume the same security reviewer
  (step 7), nothing else changes.
- **Severity does not depend on the loop.** A reviewer rates a finding by what it does, never by whether the
  rating would start a round. The definitions of blocker, should-fix and nit are in `templates/review.md`.
- **Commits.** Every fix is committed before the implementer returns, one commit per finding or per batch of one
  class. The review range sees only commits, so `orch brief review` refuses a dirty worktree.
- **The gate repeats at every new tip** (step 8: beside the check or re-review).
- A task started before this rule keeps working: a journal without `check` lines just has no checks yet, and a check
  line without `cycle` reads as cycle 1. Apply the rule from the next review on.

## Checkpoints and questions

Ask the user only what they must decide. Park a question: `orch ask "<question>" --options "A|B"
--recommended A --blocks <steps> --reversible no` (`--blocks`: step ids, `all` (default) or `none`) and keep
working on everything it doesn't block; `orch answer <qid> "<answer>"` resolves it.

**Checkpoint** (`orch checkpoint`) after plan approval, after each milestone or review point, and before
the merge. None open → continue, no notification. Some open → keep working on what they don't block;
only when nothing independent is left, `orch checkpoint --ask` prints one `AskUserQuestion` batch (≤ 4
questions, recommended option first, **Hold on**): ask it, `orch answer` each reply.

**Hard stops** — the only things never parked or assumed; ask or report at once: sudo, an exclusive VM
or device, a paid action; a destructive operation outside the task's worktree; **push**; an upstream PR;
a merge the guard refuses (`orch merge` exit 2); a usage `STOP`; three strikes after rungs 1–2. Every other
question parks (an irreversible or sensitive-area blocker, a roadmap revision, a `you` stop,
disputed findings after round 3, a director escalation, step 7's pilot gate): only the steps that depend on
it wait; the loop stops only when every remaining step does. The plan gate (step 4) is asked at once:
nothing after it is independent.

**Notifications:** `PushNotification` (≤ 200 characters; load it with ToolSearch if deferred, skip
silently if unavailable) only when the user has something to act on or should know: a question just
parked, an auto-merge (sha and revert command), a hard stop, task end — never routine progress.

## Decision journal and retro — for later analysis

Two files record what you decided and why; both are written only by you:

- **`journal.jsonl`** — one JSON line per event (kinds, types, when to write each, hand-append
  fallback: `templates/journal.md`). Choices carry the **real** `reason` and the `alternatives` you
  weighed — written at the moment you decide, not reconstructed at the end. `orch journal <kind> k=v
  k:=json` appends and validates (`k:=` is raw JSON; `--set F=V` / `--log TEXT` also update status.md /
  log.md); `orch spawn`, `return`, `ask`, `checkpoint`, `merge`, `end` write their own lines.
- **`retro.md`** — when the task ends, however it ends (merged, declined, abandoned, handed to the
  user): predictions vs outcome, what cost the most, how sketches fared, what you'd do differently
  (`templates/retro.md`).

`log.md` stays the human narrative. Don't let journalling delay the work: one line per event, no prose. `orch-stats.sh` aggregates every journal.

## Direct edits

You may fix the genuinely trivial yourself (typo, stale comment, one-line doc fix, behaviour-free
rename) and log it. Never behavioural changes, sensitive areas, or anything outside the plan; when
in doubt, route it through the pipeline.

## Security ledger

`<repo-name>/security-ledger.md` holds the repo's security findings across all tasks; the reviewer owns it
(`templates/security-ledger.md`). Give its path to every `orch-security-reviewer` spawn (it may not exist yet), in the `--extra` file of `orch brief security`.

## Lessons learned

When a role is confidently wrong in a way a future agent repeating would be **a real problem** — a
confirmed bug with a pattern behind it, a false plan assumption, a user correction, a guess that
should have been verified — record it. The bar is critical-only (most fix-round findings don't
qualify); when in doubt, don't write it.

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
role's source, `~/.claude/skills/orchestrate/roles/<role>.md` (or this skill, for orchestrator behaviour), and run
`~/.claude/bin/orch-build-agents.py` in the same change — never `agents/orch-*.md`, which are generated and
overwritten by the next build; and in
`LESSONS.md` replace the entry with a one-line row in the *Promoted* table pointing at where it now
lives. `LESSONS.md` should only hold lessons not yet promoted anywhere; if it grows past about a
screen, consolidate.

## Procedure

Every spawn below follows the **Dispatch protocol**. In lite mode, read
`~/.claude/skills/orchestrate/LITE.md` first and apply its differences at steps 3, 4, 5, 7 and 9; in interactive
mode, read `~/.claude/skills/orchestrate/INTERACTIVE.md` first and apply its differences.

### 1. Confirm the mode

If the user hasn't chosen orchestration mode, confirm it's warranted (`~/.claude/CLAUDE.md`,
"Orchestration mode"). Trivial or read-only work: say so and just do it. If the user didn't say
standard, lite or interactive, recommend one against the lite eligibility rules and let them pick.

### 2. Resume check — always first

`orch show` lists the active tasks of every repo (`ACTIVE.md` is the same list: generated, never edit).
If a task matches this work, `orch resume <repo>/<slug>` prints a read-only packet; read it (an old
layout it can't read: `status.md` and `log.md`) before anything else, tell the user where things
stand, and resume from the last incomplete step. Never restart a half-done task or make a second dir for it.

### 3. Create the task directory

Before spawning the planner, create it with what's known (slug, repo, base branch; no worktree yet):
`orch init <repo-name> <task-slug> --repo-path <repo-path> --base <base> --mode standard --request "<request>"`
seeds `status.md` (`task_state: planning`) and `log.md`, journals `task_start`, refreshes `ACTIVE.md`.

### 3a. New repository — offer orch-init

Applies only when the repo is new or empty, by the `orch-init` skill's *When it runs* test (do not
restate it here). For any other repo skip this step: nothing below changes its flow, and nothing about
init is logged or journaled (only an offered-and-answered init gets `init: set up` / `init: skipped`). Load the
`orch-init` skill (`~/.claude/skills/orch-init/SKILL.md`) and run its mode `new` through its hand-off:
it restates the project, offers the setup with a strong "don't skip" line, asks one question batch,
shows the proposal, and has the `orch-init` agent write the files in the repo's main checkout on the
base branch with one commit. The user may skip; journal the choice (`override` if they skipped against
the recommendation, else `judgement`) and log `init: set up` or `init: skipped`. Then go on to step 4:
if set up, the planner's brief names `CLAUDE.md`, `docs/STATE.md`, `docs/DECISIONS.md` and
`~/.claude/orchestrations/<repo-name>/init.md` as required reading, and the init commit is the base the
plan is made against; after a skip those files do not exist and the brief omits them.

### 4. Plan

Read both lessons files (`~/.claude/orchestrations/LESSONS.md`, `~/.claude/orchestrations/<repo-name>/LESSONS.md`)
— each independently, so a missing one can't skip the other. Also read `~/.claude/orchestrations/<repo-name>/follow-ups.md`
if it exists: should-fix and nit findings earlier tasks parked (`orch follow-up`, § Review loops). They are planning
input, not work: offer one to the user only when it touches the request, and give the planner the rows that do.

Then the pre-plan `orch-graph` call (Index rebuild pause), before any planner spawn or planning.

Spawn the chosen planner tier with: the user's request verbatim; absolute repo path and branch;
absolute task dir and `plan.md` path; the tier and one-line reason for the header; anything relevant
from the lessons files; and that it's read-only on the codebase (writes only `plan.md` or `roadmap.md`, `sketches/`,
`guides/`, `notes.md`, `status.md`, `log.md`).

**Shape.** Every plan states `shape: single | roadmap` (first Summary line of `plan.md`, header of
`roadmap.md`). A `roadmap` (or a planner
returning `need: shape_roadmap`) → read `~/.claude/skills/orchestrate/PROGRAM.md` and follow it from
here. A single plan written at `opus/max` gets the plan reviewer (`PROGRAM.md` §1.3) before the gate.
Journal `plan_shape`; show the shape line at the gate, and if the user overrules it, journal
`override` and send it back to the planner.

Read `plan.md` yourself and `orch plan-check <plan.md>` it (a FAIL goes back to the planner), then **present it to the user and get approval before any code is
written.** Show its Summary, every decision marked `needs user`, and the implementer tier
`TIERS.md` gives each step (one line each), and give the file path for the rest; if the Summary can't stand alone, send it back to the planner before the gate. If they want changes, send their feedback to the same planner tier to append a
`## Revision N` section, and return to this gate — don't rewrite the plan yourself.

Before asking: `orch set task_state=awaiting_approval --log "plan ready"`. Add one line to the question:
if they answer more than about an hour later, a fresh session (`/clear`, then "resume <repo>/<slug>") is
cheaper than replying here (the prompt cache has expired).

### 5. Security check — by rule, no question

Sensitive areas: authentication/session handling; cryptography/key management; secrets/credentials;
parsing network-exposed or untrusted input; payment or PII; deserialization of untrusted data;
sandboxing/permission boundaries; dependency/supply-chain changes, or infra/deploy config carrying
credentials.

Decide it yourself once the plan is approved. The security reviewer **runs** when a plan step implements
security-bound logic (`TIERS.md`'s `boundary` signal: an authn/authz decision, crypto/key/token handling,
parsing or deserializing untrusted input, a sandbox or permission check, credential-bearing deploy
config), when the plan or its diff adds network exposure (a new listener, port, public endpoint,
CORS/origin change), when the user asks, or when `<repo-name>/policy.md` says `security: always`.
Otherwise it is **skipped** — merely touching a sensitive area isn't enough (`security: ask` asks as
before). Journal every run or skip: `orch journal auto_security areas:='["parsing untrusted input"]'
ran:=true rule=step5` (`rule`: `step5`, `late`, `request`, `policy`; a skip is `ran:=false`), and set the
row (`orch role security-reviewer pending --note "<why>"`, or `not_offered` with the reason). If the user
declines a due review, the merge goes to the user.

### 6. Create the worktree — only now

```bash
git -C <repo> worktree add .claude/worktrees/orch-<task-slug> -b orch/<task-slug> <base-branch>
```

Base = the user's current branch unless they say otherwise. Record worktree, branch, and base in
`status.md` (`orch set`). If the user says "no worktree", work on a branch in the main tree and record that.

**Sparse checkout** when the plan's Summary says `checkout: sparse: <dirs>` (the planner's rule: a repo
of ≥ 100,000 tracked files). Cone mode; files at the repo root always come along:

```bash
git -C <repo> worktree add --no-checkout .claude/worktrees/orch-<task-slug> -b orch/<task-slug> <base-branch>
git -C <worktree> sparse-checkout set --cone <dirs>
git -C <worktree> checkout
```

Record it as `worktree=<path> (sparse: <dirs>)`. A role that needs a path outside the cone runs
`git -C <worktree> sparse-checkout add <dir>` and writes `sparse-add: <dir> — <why>` in `notes.md`; pass it
on in later briefs (`--extra`). If the final gate needs the whole tree, run `git -C <worktree>
sparse-checkout disable` first and log it. Measured on Firefox (486K files): full worktree 49 s / 4.7 GB,
sparse 1.6 s / 29 MB.

Give every subagent the **absolute worktree path** and state that it is the worktree, not the main
repo. Never pass `isolation: "worktree"` to the `Agent` tool.

### 7. Implement

**Batch adjacent small steps.** One spawn takes consecutive steps when all hold: same routed tier, at most 3
steps, sizes `S` or `M` with at most one `M`, each depending only on steps before it, and none `L`, `visual:`,
`pilot:`, `boundary`, `sensitive` or in a parallel track. Pass them together: `orch brief implement 3,4`,
`orch spawn … --steps 3,4`. A step that fails any line runs alone.

**Sequential by default:** `orch brief implement <steps>` (director: `orch brief director <step>`) writes
`briefs/…md`, that role's whole context: worktree, steps, the Decisions and revisions touching them,
findings, sketch path, checkpoint commands, matching repo lessons. `orch spawn`, then spawn one
implementer at the chosen tier with the **tier, escalation and tradeoff lines** (`TIERS.md` § Record
it) and the brief's absolute path — not plan sections; there is no second lessons read. Send the spawn prompt
`orch brief` printed, unchanged (§ Tier selection says how); `--extra` carries facts only. When a sketched
step completes, read the implementer's `sketch-followed` / `sketch-deviation` note in `notes.md` and
journal a `sketch` line. When a `haiku/*` spawn returns, read its `contract-check` (`orch return` prints
it) before anything else; a gap re-routes the step (`TIERS.md` § Haiku gate). A `brief-gap:` note in
`notes.md` goes into the next brief (`--extra`).

**Reuse while the cache is warm.** For the next implementer work in a milestone (a step, a batch or a fix round),
if you hold the last implementer's agent ID, first try `orch spawn implementer --reused …` at the routed tier. It
refuses, writing nothing, unless that implementer returned `done` within 55 minutes, in this milestone, at the same
tier, and the tier isn't `haiku/*`. Accepted → `SendMessage` it `orch: next work — brief <absolute path>` (the line `orch brief` printed, unchanged) instead of
spawning. Refused → spawn fresh without `--reused`. A failed send → `orch return implementer --outcome failed`,
then spawn fresh. Never reuse for a `visual:` step, a pilot, a parallel track or an escalation. Context growth is
left to autocompaction.

**Reviewers resume the same way, for a check and for security round 2 and up.** For a code-review fix-batch check, if you hold
the code reviewer's agent ID, `orch spawn code-reviewer --reused …` at the review's tier; for security round 2 or
later, `orch spawn security-reviewer --reused …`. The conditions are the implementer's (`done` within 55
minutes, this milestone, the same tier, not `haiku/*`). Accepted → `SendMessage` the brief line unchanged, as above.
Refused → spawn fresh without `--reused`: for a check, a fresh reviewer on the same `--check` brief at the review's
tier. A failed send → `orch return <role> --outcome failed`, then spawn fresh. Never for a real code round (its
brief prints no resume line) and never for security round 1. The implementer climbs between cycles, so it is a
fresh spawn each time; only the reviewer is resumed.

**Parallel only when `plan.md` explicitly declares disjoint file sets.** Then each part gets its own
branch and worktree off the task branch (own brief and row, `implementer:<part>`):
`git -C <repo> worktree add .claude/worktrees/orch-<slug>-<part> -b orch/<slug>--<part> orch/<slug>`
(sparse task: the same three commands as step 6, same cone).
You merge the part-branches into `orch/<task-slug>` yourself **before** review, then remove each
part worktree and delete its branch. Log each part-branch, merge, and removal.

**Visual steps and pilots:** a step with `visual:` or `pilot:` → read `~/.claude/skills/orchestrate/VISUAL.md` first and follow it.

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
in `status.md` (row `skipped`) and journal an `override` whose `what` says "review skipped: <reason>"
(`orch merge` needs both); then go to step 10.

`orch brief review` writes the code reviewer's brief (worktree, the range pinned to the tip's sha — round 1
`<base>...orch/<task-slug>`, from round 2 `<last reviewed sha>..<tip>` — and the exact `git diff <range>` command to
read it with, the plan's steps, decisions and revisions, the absolute `review.md` path). It refuses (exit 2) while the
worktree has uncommitted changes: the range sees only commits. Spawn
`orch-code-reviewer` with the spawn prompt `orch brief` printed, unchanged. If step 5 said it runs, `orch brief security` and spawn
`orch-security-reviewer` the same way, with `security-review.md` and the security-ledger path (in its `--extra` file). The two
may run concurrently.

**Gate alongside review.** In the same turn, start `orch-executor` in the background on the plan's Verification
commands at the same tip; it reads the tree and changes nothing. A failure is a finding: route it with the
review's findings (step 9). Every fix batch repeats this at its new tip, beside its check or re-review. The pass that
stands at the final tip is step 10's gate, so it doesn't run again. With review skipped, the gate runs at step 10.

After each reviewer returns, journal the round — the sha comes from that round's brief, the
counts are findings still **open after** the round, `verdict` is `pass` only at 0 blockers and 0
should-fix (security: and 0 nits):
`orch journal review_round reviewer=code round:=1 blockers:=0 should_fix:=0 nits:=0 verdict=pass`.
A fix-batch check (step 9) is journaled the same way with `check:=true`, `cycle:=K` and the round it belongs to.

### 9. Fix, check, re-review — caps in § Review loops

1. Route findings to an implementer as concrete fixes (`orch brief fix <round> --findings F1,F2`, with `--tier`, `--escalation` and `--tradeoff`, and send the
   spawn prompt it printed, unchanged; `S<n>` for security findings), at the tier `TIERS.md` § Fix rounds gives
   (`sonnet/high` whoever wrote the code, unless the fix itself carries an Opus signal; an exact-edit list may go to Haiku; a
   class raised again in round 2 moves up a rung; cycles 2 and 3 of one round climb, `TIERS.md` § Fix rounds). **One
   review's blockers and should-fix go in one batch** — one brief, one spawn. When a finding is one instance of a class
   (state reset, validation, cleanup), name the whole class and the invariant the fix must keep, in
   the implementer's brief (`--extra`) and the reviewer's check or re-review scope. Code-review findings that are `nit`
   or `origin: pre-existing` are not routed and never start or extend a round or a cycle; they go to the digest
   (step 10). Security findings are always routed. The implementer commits every fix before it returns (one commit per
   finding, or per batch of one class).
2. **Code review: check the fix batch, cycle by cycle.** After a review (or a check) with something open and its batch
   committed, `orch brief review --check` (with `--tier`, `--escalation` and `--tradeoff`, spawn prompt unchanged)
   briefs the same `orch-code-reviewer` (step 7) on the fix commits only, the range from the previous line's sha
   to the tip, and names the closed findings (resolved, or disputed and ruled on) so it doesn't re-raise them.
   The reviewer appends a `## Check of round N` section to `review.md` (`## Check K of round N` from cycle 2).
   Journal it with `check:=true` and `cycle:=K`, the counts open after the check
   (`orch journal review_round reviewer=code round:=N check:=true cycle:=1 blockers:=0 should_fix:=0 nits:=0 verdict=pass`)
   and handle its outcomes as § Review loops says: another cycle, the fresh round after a blocker chain, or the cap.
   A reviewer who names a broader concern gets the wider scope from you (`--range`). The gate repeats at the new tip
   beside the check (step 8).
3. **Security review: re-review.** Re-review only the touched files, unless the reviewer named a broader concern.
   Name the closed findings in the brief; the reviewer doesn't re-raise them. After commits, brief round N+1
   (`orch brief security`) and resume the same reviewer for it (step 7); a refused or failed resume is a fresh
   spawn on that brief. There is no tier climb and no check chain for security.
4. **A new round** (the fresh round after a blocker chain's passed check, any routed security finding, or a commit
   landing after a review or check passed, § Review loops): after
   the batch is committed, brief round N+1 (`orch brief review`; `orch brief security` for security); never re-brief round
   N — it refuses. A check never takes a round number of its own, so the next real round is N+1 whether or not checks
   came between.
5. Increment `review_round` in `status.md` for each real round, never for a check; log it
   (`orch set review_round=<n>/3 --log "<round n>"`).

After round 3 with a blocker (or a routed security finding) still open, or after cycle 3 of a round with a blocker still open, **stop** — no fourth round or cycle at the same tier. The same
stop applies when a commit awaits review after round 3 (§ Review loops, the tip-moved case): no round 4 without the
raising rule, so raise the cap (Opus level or higher, journal `override`) or park a question (`orch ask`). If the open
findings share a category (concurrency, one algorithm, one bug class), escalate the implementer tier
for one fresh pass with the accumulated findings as its brief. If they're genuinely disputed,
summarize the disagreement and hand it to the user. A cap raised under § Review loops moves this stop to the new
number. Consider each resolved finding against the critical-only lessons bar.

### 10. Merge — automatic when clean

First re-check sensitivity against what was **actually** built: diff the real touched-file list
against the step 5 list. If it reaches a sensitive area the plan didn't declare, with no question to the
user: journal `auto_security ran:=true rule=late areas:='[…]'`, set the security-reviewer row to `pending`,
and spawn the reviewer (`orch brief security`); say in the digest why it came up late. The merge waits for
its pass. (A user request is `rule=request`, policy `always` is `rule=policy`.)

If the repo has rules about user-facing output, grep the **built artefact** for the forbidden
strings yourself before this gate — a green report checked the source. When you brief anyone to
embed a whole file, say where it must and must not land.

When review passes (no routed finding still open):

1. Journal the final gate — the plan's Verification commands, once, via `orch-executor`, passed at the
   task-branch tip. Step 8's run counts when no commit followed it; any later commit means running it again:
   `orch journal final_gate verdict=pass sha=<tip> command="<gate command>"`.
2. `orch merge --recheck-ok` checks its conditions in code and fails closed (`--check` lists them: the
   repo's `merge` policy, the gate and reviews pinned to the tip's sha, the security rule, a roadmap `you`
   cell, a clean main checkout on the base branch, base contained in the tip), then runs `git merge
   --no-ff`, cleans up (step 11) and journals `auto_merge` with a revert command. **It never pushes.**
   - exit 0: merged. Notify (sha and revert command); nits and `origin: pre-existing` findings go in the digest as follow-ups, beside the should-fix findings parked in `follow-ups.md` (`orch end` lists them).
   - exit 2: a condition failed (all listed), nothing changed. Summarize for the user: files touched, what
     changed, what reviewers found and how it was resolved, nits and pre-existing findings
     separately. **Ask explicitly before merging**; nits the user wants fixed go to step 9. On confirmation: `git merge --no-ff orch/<task-slug>` —
     never fast-forward, even on "just merge" — verify it landed, then step 11 by hand.
   - exit 3: a merge commit exists — read the message (sha, revert command); don't merge again.
   - exit 1: nothing was written (repo or status problem): fix it and rerun.

### 11. Cleanup — only after a confirmed, successful merge

`orch merge` does this on exit 0 (`worktree remove` without `--force`, `branch -d`). After a manual merge:

```bash
git -C <repo> worktree remove .claude/worktrees/orch-<task-slug>
git -C <repo> branch -d orch/<task-slug>     # -d, never -D: a refusal means the merge didn't land
git -C <repo> worktree list                  # confirm no orch-<task-slug>* remain
```

Keep the task directory — it's the record.

**Init refresh.** After this merge, if `~/.claude/orchestrations/<repo-name>/init.md` says `refresh: pending`, run the `orch-init` skill's mode `refresh` once (a proposal the user approves, one commit), then it reads `refresh: done`; if the user skips it, the session sets `refresh: skipped` and it is not offered again (only `pending` fires). In program mode this runs after the first milestone's merge (`PROGRAM.md` §2).

**Every task end — merged, declined, failed merge, abandoned, or handed to the user — gets a
`task_end` journal line and a `retro.md`**, before you report back: write `retro.md`, then
`orch end <outcome>` (`merged | declined | failed_merge | abandoned | handed_to_user`) journals it, sets the
final `task_state` and prints the digest (merges, assumed decisions, open questions, security runs and
skips, tokens). Paste it into retro.md's Digest section, show it to the user, and notify.

If the user declines the merge or it fails: change nothing, and tell them the absolute paths of the
worktree and task directory plus the branch name.

## What not to do

- Create the worktree before plan approval; merge outside `orch merge` without the user's explicit
  confirmation, fast-forward, or push; delete a worktree or branch before a confirmed merge.
- Run or skip the security reviewer outside the step 5 rule, or without journaling it.
- Hand-edit `status.md` or `journal.jsonl` while `orch` works; give a role the whole plan when a brief exists.
- Run implementers in parallel without `plan.md` declaring disjoint file sets; exceed a review-loop cap (§ Review loops: 3 code
  or security rounds at the same tier) without its raising rule; rely on live messaging for anything that must survive a crash.
- Have a planner implement steps: every plan goes to fresh implementers.
- Spawn without the pre-flight (`orch spawn`); treat `STALE`/`UNKNOWN` as safe; let a subagent run
  past `STOP`; poll usage on a timer while a wait is armed; arm `usage-watch.sh`; leave a `Monitor` armed past
  its subagent, or run one as a keep-alive (`sleep N; echo tick`) instead of a filter on a real process or log; claim a crash will auto-resume.
- Let a role spawn anything but `orch-explorer`/`orch-executor` (the planner and you also `orch-graph`), or guess inside a sensitive area.
- Assert what could have been verified; go to the user before trying rungs 1 and 2.
- Let a subagent write to `LESSONS.md`; log non-critical findings as lessons; leave a
  behaviour-changing lesson unpromoted.
- Spawn a Fable-tier agent without telling the user why; start any role at Fable, or move an implementer or
  planner to it, without a failed `opus/max` pass on that work or the user's own request; give Haiku a step
  that fails its gate.
- Make a choice without journalling its real reason at the time; end a task without `retro.md`;
  treat a sketch as binding, or a sketch deviation as a review finding.
- Run lite mode on an ineligible task without the user forcing it; skip its plan gate or review;
  run a lite task past 2 fix rounds without switching to standard.
- Write product code yourself in interactive mode beyond *Direct edits*, or continue yourself as the implementer.
