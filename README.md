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
| `bin/orch-build-agents.py` | Renders `roles/` into `~/.claude/agents/orch-*.md`. Agent files can't import other files at runtime, so shared rules are inlined at build time |
| `agents/explorer.md`, `agents/executor.md` | Cheap helpers that any role can call: read-only lookups, and builds/tests that report a digest |
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
      "hooks": [{ "type": "command", "command": "$HOME/.claude/bin/orch-index-hook.sh" }] }],
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

1. Edit the skill in `~/.claude`. For a role, edit `skills/orchestrate/roles/`, then run
   `~/.claude/bin/orch-build-agents.py`; never edit `agents/orch-*.md` directly.
2. Run `scripts/export.py`. It copies the files here, applies its listed edits, and fails if a
   local denylist (kept outside the repo) matches anything.
3. Commit and push.

Don't run `install.sh` over the live install. It would replace live files with the sanitised copies.

## License

MIT. See [LICENSE](LICENSE).
