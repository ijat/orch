---
name: orch-init
description: Initialize or audit a project's agent setup — a short canonical CLAUDE.md, an AGENTS.md pointer, docs/STATE.md and docs/DECISIONS.md, plus offered extras (gate command and CI, Claude settings and hooks, README/LICENSE, CLAUDE.local.md) and a language-adaptive architecture built on swappable interfaces. Use when the orchestrate skill offers it on a new or empty repo (step 3a), when the user says "init/initialize/set up this project", "start a new project", "/orch-init", or "audit CLAUDE.md" / "audit the project setup", and for the one refresh after the first merge on an initialized repo.
---

# orch-init

You are the **session** that runs project initialization. A Sonnet `orch-init` agent researches
current tooling, writes the files and commits; **you** detect, ask the user, show the proposal and
hand off. The agent never asks the user anything. Orchestration rules (modes, dispatch, usage pacing,
the task dir, the planner) live in `~/.claude/skills/orchestrate/SKILL.md`: cite them, do not redo them.

Evidence base: the generated files are minimal on purpose. Context files that restate what the code
shows cost tokens and were not shown to help (arXiv 2602.11988, seen via a secondary summary); the useful content is what an agent cannot infer (exact commands,
invariants, decisions, the swap points). Stay under the 200-line guidance
(<https://code.claude.com/docs/en/memory>).

## When it runs

- **Offered automatically** only from orchestrate step 3a: an orchestrated task starts on a new or
  empty repo (below). Outside orchestration it runs only on request (`/orch-init`, "initialize this
  project", "audit CLAUDE.md").
- **New or empty repo**: either (a) a directory that is not a git repo, or (b) a git repo with no
  commits, or (c) tracked files only from {`README*`, `LICENSE*`, `.gitignore`, `.gitattributes`,
  `docs/**`, `*.md` planning docs} AND no build manifest (`package.json`, `Cargo.toml`, `go.mod`,
  `*.csproj`, `*.sln`, `pyproject.toml`, `Package.swift`, `project.godot`, `*.slnx`) AND no `CLAUDE.md`, `AGENTS.md` or `.claude/CLAUDE.md`. Detect with
  `git rev-list -n1 --all`, `git ls-files`, and `ls`; exclude `.claude/worktrees/` from every scan.
  "No files" is not the test: most repos start with spec or notes docs.
- Anything else is **not new**. Say so and offer **audit** instead.

## Modes

| Mode | Trigger | Writes the repo? |
|---|---|---|
| `new` | orchestrate step 3a, or request on a new/empty repo | yes, once, after approval |
| `refresh` | once, after the first merge on an initialized repo (orchestrate step 11 / PROGRAM.md §2) | yes, once, after approval |
| `audit` | user request, any repo | never; writes a findings file |

`new` refuses a repo with a build manifest or an instruction file: tell the user and run `audit`
instead. Never overwrite an existing `CLAUDE.md`.

## Mode `new`

1. **Detect** (above). Read `~/.claude/CLAUDE.md` yourself or pass its path to the agent: generated
   files never copy its rules (global-rule dedup, below).
2. **Restate and offer.** First say in 3–5 lines what you understand the project to be, and invite
   corrections. Then offer, with `AskUserQuestion` (recommended first, plus **Hold on**), this text
   verbatim in the question:

   > This looks like a new project. I can set it up before planning: a short CLAUDE.md, an AGENTS.md
   > pointer, docs/STATE.md and docs/DECISIONS.md, plus optional extras (gate command and CI, Claude
   > settings and hooks, README and LICENSE). Setting it up first is important — not recommended to
   > skip: the planner and every later agent work against these files, and without them structure and
   > rules form by accident. Set it up now?

   Options: **Set it up (recommended)** / **Skip** / **Hold on**. Skip is allowed: record
   `init: skipped` in the task's `log.md` and return to orchestrate step 4.
3. **Question batch** (one `AskUserQuestion` call, up to 4 questions, recommended first, **Hold on**
   reachable via Other):
   1. *Ecosystem*: the stack, or non-code (docs, design, data). If it has no
      `references/<ecosystem>.md`, say the agent will run at `opus/medium` (D7) and research it from
      primary docs.
   2. *Extras* (multi-select, all opt-in): gate command and CI; `.claude/settings.json` (deny rules,
      allow the gate) with format hook and secret scan; README and LICENSE; `CLAUDE.local.md`.
   3. *Workflow rules that differ per project*: worktree per task (always / never / ask), commit per
      change vs per task, deploy or mutate-confirmation gates. Never default these: past projects
      disagree.
   4. *Project facts*: name, one-line purpose, license type (only if LICENSE was picked), budget or
      scope hard limits (free-tier cap, offline only, ...).
   Ask the git remote and any push **separately and never as part of this batch** (hard stops, below).
4. **Phase 1 — proposal.** Spawn the `orch-init` agent (model `sonnet`, effort `high`; `opus`/`medium`
   when no reference file) per the Dispatch protocol, with the brief: repo path, mode `new`, the
   answers, the skill dir, the global `CLAUDE.md` path, and "phase 1: write the proposal only, no repo
   writes". It researches current tooling (web, dated), reads `references/architecture.md` and
   `references/<ecosystem>.md`, and writes `~/.claude/orchestrations/<repo-name>/init-proposal.md`
   from `templates/proposal.md`: the file list with sizes, the filled CLAUDE.md, the commands it ran
   and their results, the architecture (interfaces and adapters named for this project), what it
   dropped as a global duplicate, and open questions.
5. **Proposal gate.** Show the user the proposal (summary plus the path; the filled CLAUDE.md inline).
   `AskUserQuestion`: **Approve (recommended)** / **Change** (user says what; re-run phase 1) /
   **Skip** / **Hold on**. Nothing is written to the repo before Approve.
6. **Phase 2 — write.** Resume the agent (`SendMessage`) or spawn it again with the approved proposal:
   it writes the files in the repo's main checkout on the base branch (D9: no worktree, no review —
   an empty repo has no work to protect, and the planner needs the files on the base it plans
   against), runs `git init` only if the directory is not a repo, checks the files (below), and makes
   **one** commit `chore: initialize project (orch-init)`. It never adds a remote and never pushes.
   It then writes `~/.claude/orchestrations/<repo-name>/init.md` from `templates/init-record.md`
   (`refresh: pending`).
7. **Verify** (agent, before it reports): every command in CLAUDE.md `Commands` was run and the
   result is in the report; generated `CLAUDE.md` ≤ 120 lines for a typical project (hard cap 200);
   `AGENTS.md` ≤ 5 lines; no secret or real value anywhere; `git ls-files` shows no
   `CLAUDE.local.md`, `.env*` (except `.env.example`) or `.claude/settings.local.json`; every path
   CLAUDE.md names exists.
8. **Hand off.** Return to orchestrate step 4. The planner's brief names `CLAUDE.md`,
   `docs/STATE.md` and `docs/DECISIONS.md` as required reading, and the init commit is the base the
   plan is made against. The planner treats DECISIONS.md rows as settled ("don't re-open unless the
   user asks"). The refresh runs once after the first merge (below).

## Mode `refresh`

Runs once, automatically, when `init.md` says `refresh: pending` and the first task (or, in program
mode, the first milestone) has merged. Same two phases: the agent diffs CLAUDE.md, docs/STATE.md and
the architecture notes against the repo as built (real commands re-run, real layout), writes a
proposal of diffs only, the user approves, one commit `chore: refresh project docs (orch-init)`, and
`init.md` becomes `refresh: done`. Later refreshes happen only through `audit` on request. Never
refresh while the tree has uncommitted work you did not start.

## Mode `audit`

On request, any repo. Spawn the agent with mode `audit`; it writes
`~/.claude/orchestrations/<repo-name>/audit.md` from `templates/audit.md`: findings with evidence and
a **proposed diff** per finding. It **never edits** the repo. Checks:

- Size: CLAUDE.md > 200 lines, or sections that restate the layout or dependency lists.
- Duplicates of `~/.claude/CLAUDE.md` rules; contradictions with them (marked override vs accidental).
- Stale commands: run each documented command; list the ones that fail or no longer exist.
- Shared files: AGENTS.md diverged from CLAUDE.md (copy, not pointer); `CLAUDE.local.md` or
  `.claude/settings.local.json` **tracked** (`git ls-files`, not just ignore rules); `.env*` tracked.
- Missing: STATE.md, DECISIONS.md, a gate command, seams at boundaries (`references/architecture.md`),
  enforced boundary checks.
- Rules that should be hooks or deny rules instead of emphasis.
Scans exclude `.claude/worktrees/`. The user applies a finding by asking; you then edit in the normal
flow, not through the agent.

## Outputs

Defaults (D6, no question): `CLAUDE.md` (canonical, from `templates/CLAUDE.md`), `AGENTS.md`
(≤ 5-line pointer: "read CLAUDE.md, it is the source of truth"), `docs/STATE.md`,
`docs/DECISIONS.md`, and `.gitignore` lines for `CLAUDE.local.md`, `.env*` (with `!.env.example`) and
`.claude/settings.local.json` (`templates/snippets.md`). Offered extras come from
`templates/snippets.md`. Budget: CLAUDE.md ≤ 120 lines typical, 200 hard; everything else short.
Content rules:

- CLAUDE.md holds only what an agent cannot infer: purpose (≤ 3 lines), start-here, exact commands,
  architecture rules (≤ 10 lines), numbered invariants, project-specific git rules, a docs index.
- Never a derivable layout listing, dependency list or tutorial.
- A rule that must always hold goes in a check (test, script, hook, deny rule), with CLAUDE.md naming
  the check; emphasis is not enforcement.
- **Global-rule dedup (D13)**: drop anything `~/.claude/CLAUDE.md` already says (package manager,
  fonts, git merge and push rules, question tool, verification). A project rule that *differs* from
  global stays and is marked `Override:` so it is not mistaken for drift.
- Architecture: `references/architecture.md` (swappable interfaces at every boundary, composition
  root, contract test per interface, enforced boundaries). Ecosystem commands, layout and conventions:
  `references/<ecosystem>.md` (`typescript`, `python`, `rust`, `go`, `dotnet`, `swift`, `godot`,
  `non-code`), each stamped `verified: <date>`. A reference older than 6 months, or one for a tool
  version the agent finds newer, is re-verified against primary docs before use; say so in the proposal.

## What it never does

- Add a remote, push, or choose a LICENSE type without asking (each is asked separately).
- Overwrite an existing `CLAUDE.md` or `AGENTS.md`; in `new` mode that switches to `audit`.
- Copy global rules into the project (D13), or duplicate CLAUDE.md into AGENTS.md.
- Write the repo in `audit` mode, or before the user approved the proposal.
- Run `install.sh`, deploy, or touch anything outside the repo and `~/.claude/orchestrations/<repo-name>/`.
- Commit a secret, a real credential, `.env`, `CLAUDE.local.md` or `settings.local.json`.
- Install tools or global config on the user's machine: it proposes the commands, the user decides.
- Treat the permission/hook snippets as security: they are config text; deny rules are the control.

## The init record

`~/.claude/orchestrations/<repo-name>/init.md`, format in `templates/init-record.md`: mode, date,
ecosystem, answers, extras chosen, files written, commit sha, `refresh: pending|done|n/a`. Orchestrate
reads it to decide the refresh; `audit` reads it to know what was generated and what the user chose.

## Paths

`~/.claude/skills/orch-init/` (live) — `templates/{CLAUDE.md, AGENTS.md, STATE.md, DECISIONS.md,
init-record.md, proposal.md, audit.md, snippets.md}`; `references/architecture.md`;
`references/{typescript, python, rust, go, dotnet, swift, godot, non-code}.md`. The role is
`agents/orch-init.md` (`skills/orchestrate/roles/orch-init.md`).
