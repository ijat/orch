# orch

orch is a multi-agent orchestration skill for [Claude Code](https://code.claude.com). The work
moves through these steps:

1. A **planner** writes a plan, and you approve it.
2. An **implementer** carries out the plan in its own git worktree.
3. A **code reviewer** (and a **security reviewer**, if you accept one) checks the diff, for up to
   3 fix rounds.
4. You approve the merge.

All state lives in Markdown files on disk, so a task survives a crashed session. The orchestrator
picks each role's model and effort level per spawn, records every choice in a journal, and paces
itself against your 5h/7d usage limits. It slows down at 90%, pauses at 95%, and resumes on its own
when the window resets.

Status: in active use, still changing.

## Why use it

You make two decisions: approve the plan, and approve the merge. The agents (blue) do the rest,
including the build-and-review loop in between. Yellow is you.

```mermaid
flowchart LR
    U([You]) -->|describe the task| PL[Plan]
    PL -->|plan.md| G1{{You approve?}}
    G1 -->|change it| PL
    G1 -->|yes| B[Build]
    B --> R[Review]
    R -->|issues found| B
    R -->|clean| G2{{You approve the merge?}}
    G2 -->|yes| M([Merged into your branch])
    G2 -->|no| K([Kept on its own branch])

    classDef you fill:#fff3c4,stroke:#c9a400,color:#000
    classDef agent fill:#e3ecff,stroke:#5b7fd6,color:#000
    class U,G1,G2 you
    class PL,B,R agent
```

- **You stay in charge at the two points that matter.** You approve the plan before any code is
  written and the merge before anything lands on your branch. Between those gates it runs on its own.
- **Work survives crashes and usage limits.** All state lives in Markdown files: plan, status, log,
  review and journal. A dead session, a closed terminal or a usage pause resumes from the files, not
  from scratch.
- **Separate reviewers check the work.** A code reviewer with a fresh context, plus a security reviewer
  on sensitive changes, which it offers and never runs silently. They review against the approved plan,
  for up to 3 fix rounds.
- **It spends usage deliberately.** A cheap Sonnet implementer does most of the work. Opus plans and
  reviews, and a step escalates to a stronger tier only for a stated reason, which goes in the journal.
  It paces itself against your 5h/7d limits, so a long task pauses instead of hitting the wall.
- **Runs are bounded.** Pilot batches measure cost before a big step. Check-ins catch a silent agent
  that's looping, and three strikes stop retry spirals.
- **It learns.** Mistakes go into a `LESSONS.md`. Lessons that change behaviour get promoted into the
  skill itself, and every decision is journaled for later analysis.

## How it differs from other ways to run agents

| | Plain subagents / one long session | `/goal` (Claude Code) | Dynamic workflows (Claude Code) | **orch** |
|---|---|---|---|---|
| Who decides "done" | the agent itself | a small model reads the conversation after each turn | the script | reviewers against the approved plan, then you at merge |
| Human gates | ad hoc | none until the condition is met | none mid-run | plan approval and merge approval, plus cost and decision gates |
| Survives a crash | no | goal restored on resume; counters reset | no | yes: every role checkpoints to files |
| Isolation | your branch | your branch | your branch | its own git worktree until you merge |
| Model per role | one | one, plus an evaluator | per agent | per role and per spawn, with a written tradeoff |
| Loop limits | none | a turn cap you write yourself | per script | 3 review rounds, 2 visual redo rounds, three strikes, check-ins |
| Usage pacing | none | pauses on limits | none | slows at 90%, pauses at 95%, resumes on its own |

Use `/goal` when one agent can reach a checkable end state, like "tests pass". Use dynamic workflows
for wide fan-out with no human in the loop. Use orch for multi-step engineering work where you want
a plan you approve, independent review and a merge you control.

## Track record

The workflow goes back to late December 2025. It began as a project-specific orchestration skill
inside one repository and evolved into this standalone skill. Records were only kept from
2026-08-27, so the numbers below cover about the last seven weeks, not the whole history. They come
from the skill's own records. Journaled outcomes start on 2026-09-26, when the journal was added. Review findings
are counted from the review files themselves.

| | |
|---|---|
| Tasks run | 84 across 13 repositories |
| Journaled task ends since 2026-09-26 | 41: 36 merged, 1 abandoned, 4 with no outcome recorded |
| Plans | 46 approvals at the gate, 5 sent back for revision there; 13 mid-task revisions (5 after a blocker, 8 asked for by the user) |
| Caught by code review before merge | 18 blockers, 205 should-fix and 287 nits, over 66 reviews |
| Caught by security review | 3 high, 15 medium and 36 low, over 23 reviews |
| Blockers raised by roles | 31; 13 resolved by the orchestrator itself, 4 by a side investigation, 14 by asking the user |
| Usage-limit pauses | 5 pauses, 7 automatic resumes |
| Lessons | 15 promoted from `LESSONS.md` into standing rules |

### What changed because of the data

- **Tiers chosen per spawn** (2026-10-08). Mean review rounds on merged standard tasks fell from 2.7
  (n=11) to 2.1 (n=9).
  Escalations went from barely logged to logged every time.
- **Batching implementers into ≤ 2 steps** was tried and reverted (2026-10-03). Measured volume went
  up, not down.
- **Planner sketches** are kept under watch. 10 of 48 were judged wrong, so they stay an experiment
  rather than a rule.
- **A full audit on 2026-10-15** found duplicated and drifted role rules and fixed them. The same
  scenarios were run against the old and new role files: existing behaviour scored the same (20/21
  each), and the fixes passed 42/42.

### Not proven yet

- The visual director loop, pilot batches, check-ins and three strikes were added on 2026-10-15.
  None has run in a real task yet.
- Program mode (roadmaps of milestones) has been used once.
- The numbers above come from one person's projects. Your mileage will vary with codebase and model
  versions.

## How it works

### The main flow

You approve at two gates, the plan and the merge. Everything between them runs on its own.

```mermaid
flowchart TD
    A([You: orchestrate this]) --> B[Resume check:<br/>unfinished task for this repo?]
    B -->|yes| R[Resume from status.md + log.md]
    B -->|no| C[Create task dir<br/>~/.claude/orchestrations/repo/task/]
    C --> P[Planner writes plan.md<br/>shape: single plan or roadmap]
    P --> PR{Roadmap or<br/>opus/max plan?}
    PR -->|yes| PRV[Plan reviewer] --> G1
    PR -->|no| G1{{Gate: you approve the plan}}
    G1 -->|revise| P
    G1 -->|approve| S{Touches a<br/>sensitive area?}
    S -->|yes| SO{{Offer a security review}}
    S -->|no| W
    SO --> W[Create git worktree<br/>only now]
    W --> I[Implement<br/>see the next chart]
    I --> CR[Code review<br/>+ security review if accepted]
    CR --> F{Findings?}
    F -->|none| G2
    F -->|yes, round ≤ 3| FX[Implementer fixes] --> CR
    F -->|still open after round 3| ESC[Escalate tier once,<br/>or hand dispute to you]
    ESC --> G2{{Gate: you approve the merge}}
    G2 -->|approve| M[git merge --no-ff] --> CL[Remove worktree]
    G2 -->|decline| K[Keep the branch]
```

### Inside "Implement": the loops that keep a step bounded

```mermaid
flowchart TD
    ST[Step from plan.md] --> PL{Step has<br/>~20+ similar items?}
    PL -->|yes| PI[Pilot: first ~5 items alone]
    PI --> PG{{Gate: measured cost<br/>→ estimate for the rest}}
    PG -->|continue / change tier| RUN
    PG -->|hold| H([Stop])
    PL -->|no| RUN[Implementer works the step<br/>commit + log line per step]
    RUN --> CHK{Same check failed<br/>3× on one item?}
    CHK -->|yes| STK[Three strikes: stop,<br/>write attempts to notes.md,<br/>return blocked: stuck]
    CHK -->|no| DONE[Step done]
    RUN -.->|every 10–15 min tick| TICK{No log line for<br/>30 → 60 → 120 min?}
    TICK -->|progressing| RUN
    TICK -->|looping| STOP[Check-in: message it to<br/>checkpoint and return]
    STK --> LAD[Escalation ladder:<br/>answer it / sharper brief /<br/>higher tier / ask you]
    STOP --> LAD
```

### Visual steps: a director judges, a cheaper implementer draws

For steps whose acceptance needs eyes (glyphs, UI, images). The two agents talk directly through
one-line messages that point at a file. The file, `visual-s<step>-<N>.md`, is the record.

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant D as Director (opus, sees images)
    participant I as Implementer (sonnet)
    O->>D: spawn (gets implementer ID)
    O->>I: spawn (gets director ID)
    D->>I: spec N: visual-s3-N.md
    Note over D: row = waiting
    I->>I: draw → build → numeric gates pass
    I->>D: batch N ready (crops of changed items)
    Note over I: moves on to batch N+1
    D->>D: view crops beside reference
    D->>I: verdicts N: accept / redo + reason
    Note over D,I: ≤ 2 redo rounds per item, then pick a variant or escalate
    I-->>D: stuck: gate failed 3× → new spec, or director blocks
    D-->>O: blocked: conflicts with a recorded decision → you decide
    I->>O: done (all items accepted)
```

### Usage pacing

It reads your 5h usage window from a local cache. No network calls.

```mermaid
stateDiagram-v2
    [*] --> Normal
    Normal --> Warn: 5h ≥ 90%
    Warn --> Normal: drops below 90%
    Warn --> Paused: 5h ≥ 95%
    Normal --> Paused: 5h ≥ 95%
    Paused --> Normal: window resets (drop > 5 points)
    Normal: Normal pace
    Warn: Slow down (sequential, lower planner tier)
    Paused: Pause (checkpoint, wake at reset, re-spawn)
```

### Other modes

```mermaid
flowchart TB
    subgraph Lite["Lite mode: small, low-risk tasks"]
        direction LR
        L1[One agent writes the plan] --> L2{{You approve}} --> L3[Same agent implements] --> L4[Review, ≤ 2 fix rounds,<br/>then standard rules]
    end
    subgraph Program["Program mode: big features"]
        direction LR
        P1[Roadmap of milestones<br/>+ checker + plan reviewer] --> P2{{You approve}} --> P3[Per milestone:<br/>detail plan → implement → tests]
        P3 --> P4[Review points and merge points<br/>where the roadmap puts them]
        P4 --> P3
    end
    Lite ~~~ Program
```

Every decision (tier choice, escalation, gate, pilot, check-in) is appended to `journal.jsonl`,
and `bin/orch-stats.sh` aggregates the journals across tasks.

## What's in it

| Path | What |
|---|---|
| `skills/orchestrate/SKILL.md` | The procedure the orchestrator follows |
| `skills/orchestrate/PROGRAM.md` | Program mode: a roadmap of milestones for big features |
| `skills/orchestrate/TIERS.md` | Guide for choosing each role's model and effort |
| `skills/orchestrate/templates/` | One template per task file: plan, status, log, review, journal, … |
| `agents/orch-*.md` | Roles: planner, implementer, code reviewer, security reviewer, plan reviewer, director (visual judgement). **Generated**, do not edit |
| `skills/orchestrate/roles/` | Role sources; `_shared/` holds blocks every role includes |
| `bin/orch-build-agents.py` | Renders `roles/` into `~/.claude/agents/` (`orch-*.md`, `explorer.md`, `executor.md`). Agent files can't import other files at runtime, so shared rules are inlined at build time |
| `bin/orch-build-hook.sh` | PostToolUse hook: re-runs the build whenever a Write, Edit or Bash call touches `roles/`, and reports a failed build back to Claude |
| `agents/explorer.md`, `agents/executor.md` | Cheap helpers that any role can call: read-only lookups, and builds/tests that report a digest. **Generated** from `roles/`, do not edit |
| `bin/usage-check.sh`, `bin/usage-watch.sh` | Read your usage-limit state from a local cache. No network |
| `bin/orch-ratelimit-cache.sh` | Writes that cache from the statusline input |
| `bin/orch-index.sh`, `bin/orch-index-hook.sh` | Keep `~/.claude/orchestrations/ACTIVE.md`, the list of unfinished tasks |
| `bin/orch-stats.sh`, `bin/role-cost.py` | Analytics over the journals and transcripts |
| `bin/orch-program-check.py` | Checks a program roadmap against its rules |

## Install

Requires Claude Code, `git`, `jq` and `python3`.

```bash
git clone <this repo> orch && cd orch
./install.sh --dry-run   # list what would change
./install.sh             # copy into ~/.claude; any replaced file is backed up first
```

Then add the following to `~/.claude/settings.json`:

```jsonc
{
  "hooks": {
    "PostToolUse": [{ "matcher": "Write|Edit|Bash",
      "hooks": [{ "type": "command", "command": "$HOME/.claude/bin/orch-index-hook.sh" },
                { "type": "command", "command": "$HOME/.claude/bin/orch-build-hook.sh" }] }],
    "SessionStart": [{ "hooks": [{ "type": "command",
      "command": "$HOME/.claude/bin/orch-index.sh >/dev/null 2>&1 || true" }] }]
  },
  // Usage pacing needs the rate-limit cache. Use this as your statusline...
  "statusLine": { "type": "command", "command": "$HOME/.claude/bin/orch-ratelimit-cache.sh --print" }
  // ...or, if you already have a statusline script, pipe its stdin through:
  //   input=$(cat); printf '%s' "$input" | ~/.claude/bin/orch-ratelimit-cache.sh
}
```

Without the cache, `usage-check.sh` reports `UNKNOWN`. The skill treats `UNKNOWN` as a warning, so
it runs slowed down the whole time and can't auto-pause or resume. Rate limits appear in the
statusline input only on Pro/Max plans.

To have Claude offer the mode on its own, add a line like this to `~/.claude/CLAUDE.md`:

> Before a new feature, or a bugfix or refactor that spans several files, offer the orchestration
> mode (standard, or lite for small low-risk tasks) alongside normal single-session mode.

## Use

Say "orchestrate this" (or "lite mode" for a small task) and describe the work. Task files go to
`~/.claude/orchestrations/<repo>/<task>/`.

## Maintaining (for the repo owner)

This repo is a sanitised **export** of a live `~/.claude` install. The live files are the source
of truth.

1. Edit the skill in `~/.claude`. For a role, edit `skills/orchestrate/roles/`; never edit
   `agents/orch-*.md`, `explorer.md` or `executor.md` directly. The build hook regenerates them after each edit made inside Claude
   Code. After editing outside Claude Code, run `~/.claude/bin/orch-build-agents.py` yourself.
   Running sessions keep the agent definitions they started with; new sessions pick up the change.
2. Run `scripts/export.py`. It copies the files here, applies its listed edits, and fails if a
   local denylist (kept outside the repo) matches anything.
3. Commit and push.

Don't run `install.sh` over the live install. It would replace live files with the sanitised copies.

## License

MIT. See [LICENSE](LICENSE).
