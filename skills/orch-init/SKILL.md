---
name: orch-init
description: Initialize or audit a project's agent setup — a short canonical CLAUDE.md, an AGENTS.md pointer, docs/STATE.md and docs/DECISIONS.md, plus offered extras (gate command and CI, Claude settings and hooks, README/LICENSE, CLAUDE.local.md) and a language-adaptive architecture built on swappable interfaces. Use when the orchestrate skill offers it on a new or empty repo (step 3a), when the user says "init/initialize/set up this project", "/orch-init", or "audit CLAUDE.md" / "audit the project setup", and for the one refresh after the first merge on an initialized repo.
---

# orch-init

You are the **session** that runs project initialization. A Sonnet `orch-init` agent researches
current tooling, writes the files and commits; **you** detect, ask the user, show the proposal and
hand off. The agent never asks the user anything. Orchestration rules (modes, dispatch, usage pacing,
the task dir, the planner) live in `~/.claude/skills/orchestrate/SKILL.md`: cite them, do not redo them.
**Outside orchestration** (a request on a new repo, or any audit): there is no task dir, so no `orch spawn`,
no `orch -t` and no `log.md`; spawn the agent directly, still writing to `~/.claude/orchestrations/<repo-name>/`,
and say any skip or refusal in your reply instead of a log.

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
- Anything else is **not new**. Called from orchestrate step 3a: return to step 4 and say or log nothing. Asked directly: say so and offer **audit** instead.

## Modes

| Mode | Trigger | Writes the repo? |
|---|---|---|
| `new` | orchestrate step 3a, or request on a new/empty repo | yes, once, after approval |
| `refresh` | once, after the first merge on an initialized repo (orchestrate step 11 / PROGRAM.md §2) | yes, once, after approval |
| `audit` | user request, any repo | never; writes a findings file |

`new` refuses a repo with a build manifest or an instruction file: from step 3a, return silently; on a direct request, tell the user and offer `audit`
instead. Never overwrite an existing `CLAUDE.md`.

## Mode `new`

1. **Detect** (above). Read `~/.claude/CLAUDE.md` yourself or pass its path to the agent: generated
   files never copy its rules (global-rule dedup, below).
2. **Restate and offer.** First say in 3–5 lines what you understand the project to be, and invite
   corrections. Then offer, in text in the format of `~/.claude/skills/orchestrate/SKILL.md` § Checkpoints and questions
   (lettered options, recommended first, each explained, a reply hint, **Hold on**), this text verbatim in the question:

   > This looks like a new project. I can set it up before planning: a short CLAUDE.md, an AGENTS.md
   > pointer, docs/STATE.md and docs/DECISIONS.md, plus optional extras (gate command and CI, Claude
   > settings and hooks, README and LICENSE). Setting it up first is important — not recommended to
   > skip: the planner and every later agent work against these files, and without them structure and
   > rules form by accident. Set it up now?

   Options: **Set it up (recommended)** (writes the files above before planning) / **Skip** (plan without them; nothing is
   written) / **Hold on**. Skip is allowed: record
   `init: skipped` in the task's `log.md` (outside orchestration: say it in your reply) and return to
   orchestrate step 4 (or stop).
3. **Question batch** (one text message, the 4 questions below numbered, each with lettered options, the
   recommended one first and every option explained in plain words for someone who has not read the code; one
   **Hold on** line and a reply hint such as `1a 2bd 3a 4: <text>` at the end; the extras question takes several
   letters):
   1. *Ecosystem*: the stack, or non-code (docs, design, data). If it has no
      `references/<ecosystem>.md`, say the agent will run at `opus/medium` and research it from
      primary docs.
   2. *Extras* (multi-select, all opt-in): gate command and CI (the CI workflow is written only when the
      gate command exists after init; with no code yet, "add CI once the gate exists" goes into
      `docs/STATE.md` next steps instead, so there is no red CI from day 0); `.claude/settings.json` (deny rules,
      allow the gate) with format hook and secret scan; README and LICENSE; `CLAUDE.local.md`.
   3. *Workflow rules that differ per project*: worktree per task (always / never / ask), commit per
      change vs per task, deploy or mutate-confirmation gates. Never default these: past projects
      disagree.
   4. *Project facts*: name, one-line purpose, license type (only if LICENSE was picked), budget or
      scope hard limits (free-tier cap, offline only, ...).
   Ask the git remote and any push **separately and never as part of this batch** (hard stops, below).
   "No remote" or "don't push" means *not now*: init writes no `git push`/`git remote` deny rule and no
   "nothing is pushed" CLAUDE.md rule for it. Only if the user explicitly asks for a permanent ban, write
   that rule and mark it `Override:` (the global file allows push after asking).
4. **Phase 1 — proposal.** Spawn the `orch-init` agent (model `sonnet`, effort `high`; `opus`/`medium`
   when no reference file) per the Dispatch protocol, with the brief: repo path, mode `new`, the base
   branch (the one `orch init --base` recorded; outside orchestration, the repo's current or intended
   default branch, asked if unknown), the answers, the skill dir, the global `CLAUDE.md` path, and "phase 1: write the proposal only, no repo
   writes". It researches current tooling (web, dated), reads `references/architecture.md` and
   `references/<ecosystem>.md`, and writes `~/.claude/orchestrations/<repo-name>/init-proposal.md`
   from `templates/proposal.md`: the file list with sizes (including untracked docs already in the
   repo, which the init commit will include), the filled CLAUDE.md, the commands it ran and their
   results, the planned architecture (interfaces and adapters named for this project; marked "to be
   created by the first plan"), what it dropped as a global duplicate, and open questions.
5. **Proposal gate.** Show the user the proposal (summary plus the path; the filled CLAUDE.md inline).
   Ask in text, options explained: **Approve (recommended)** / **Change** (user says what; re-run phase 1) /
   **Skip** / **Hold on**. Nothing is written to the repo before Approve.
6. **Phase 2 — write.** Resume the agent (`SendMessage`) or spawn it again with the approved proposal:
   it writes the files in the repo's main checkout on the base branch (no worktree, no review: an
   empty repo has no work to protect, and the planner needs the files on the base it plans against).
   It writes **docs and config only, no code skeleton**: no `cargo new`, no manifest, no `src/`, no
   scripts; the first plan creates the code. If the directory is not a repo it runs `git init -b <base>`;
   an existing repo with no commits must already point `HEAD` at `<base>`
   (`git symbolic-ref --short HEAD`), otherwise it blocks with `need: base mismatch` and never renames
   a branch. It checks the files (below) and makes **one** commit `chore: initialize project (orch-init)`
   that includes the files it wrote plus the untracked docs the proposal listed (so the worktree built
   from the base has the user's specs). It never adds a remote and never pushes.
   It then writes `~/.claude/orchestrations/<repo-name>/init.md` from `templates/init-record.md`
   (`refresh: pending`).
7. **Verify** (agent, before it reports), limited to what init wrote: every command in CLAUDE.md
   `Commands` that can run on the repo as it is was run and the result is in the report; a command that
   needs code that does not exist yet is marked `not yet runnable (no code)` in CLAUDE.md and is not
   run (no gate requirement while there is no code); generated `CLAUDE.md` ≤ 120 lines for a typical
   project (hard cap 200); `AGENTS.md` ≤ 5 lines; no secret or real value anywhere; `git ls-files`
   shows no `CLAUDE.local.md`, `.env*` (except `.env.example`) or `.claude/settings.local.json`; every
   file CLAUDE.md names as existing exists (paths marked "to be created by the first plan" are exempt);
   after the commit `git status --porcelain` is empty for the paths init wrote or the proposal listed.
8. **Hand off.** Return to orchestrate step 4. The planner's brief names `CLAUDE.md`,
   `docs/STATE.md`, `docs/DECISIONS.md` and `~/.claude/orchestrations/<repo-name>/init.md` as required
   reading (each "if set up": after a skip they do not exist and the brief omits them), and the init
   commit is the base the plan is made against. The planner treats DECISIONS.md rows as settled ("don't re-open unless the
   user asks"). The refresh runs once after the first merge (below).

## Mode `refresh`

Runs once, automatically, when `init.md` says `refresh: pending` and the first task (or, in program
mode, the first milestone) has merged. Same two phases: the agent diffs CLAUDE.md, docs/STATE.md and
the architecture notes against the repo as built (real commands re-run, real layout), writes a
proposal of diffs only; the user is asked in text, options explained: **Approve (recommended)** / **Skip** /
**Hold on**. On Approve: one commit `chore: refresh project docs (orch-init)`, and `init.md` becomes
`refresh: done`. On Skip: you set `refresh: skipped` in `init.md` yourself (the agent is not running)
and log it, so the refresh is not offered again. Orchestrate fires the refresh only on `pending`.
Later refreshes happen only through `audit` on request. Never refresh while the tree has uncommitted
work you did not start. Re-running commands in phase 1 follows the audit rule below (non-mutating only).

## Mode `audit`

On request, any repo. Spawn the agent with mode `audit`; it writes
`~/.claude/orchestrations/<repo-name>/audit.md` from `templates/audit.md`: findings with evidence and
a **proposed diff** per finding. It **never edits** the repo. Checks:

- Size: CLAUDE.md > 200 lines, or sections that restate the layout or dependency lists.
- Duplicates of `~/.claude/CLAUDE.md` rules; contradictions with them (marked override vs accidental).
- Stale commands: run only non-mutating forms (build, test, lint/format in check mode, `--help`,
  `command -v`), and only after `git status --porcelain` is empty (if it is not, run none and say
  so). For anything else (deploy, migrate, install, push, an in-place format), check only that the
  command exists and list it as "not run (mutating)". Afterwards confirm `git status --porcelain` is
  unchanged and report it. List the commands that fail or no longer exist.
- Shared files: AGENTS.md diverged from CLAUDE.md (copy, not pointer); `CLAUDE.local.md` or
  `.claude/settings.local.json` **tracked** (`git ls-files`, not just ignore rules); `.env*` tracked.
- Missing: STATE.md, DECISIONS.md, a gate command, seams at boundaries (`references/architecture.md`),
  enforced boundary checks.
- Rules that should be hooks or deny rules instead of emphasis.
- A CLAUDE.md that requires the full gate before every commit (instead of a fast per-commit check and the
  full gate once per tip): it makes every agent re-run the whole suite on each commit. Propose the split
  `Check` / `Gate` lines of `templates/CLAUDE.md`.
Scans exclude `.claude/worktrees/`. The user applies a finding by asking; you then edit in the normal
flow, not through the agent.

## Outputs

Defaults (the default set, no question): `CLAUDE.md` (canonical, from `templates/CLAUDE.md`), `AGENTS.md`
(≤ 5-line pointer: "read CLAUDE.md, it is the source of truth"), `docs/STATE.md`,
`docs/DECISIONS.md`, and `.gitignore` lines for `CLAUDE.local.md`, `.env*` (with `!.env.example`) and
`.claude/settings.local.json` (`templates/snippets.md`). Offered extras come from
`templates/snippets.md`. Docs and config only: the ecosystem's init commands (`cargo new`, `pnpm init`, ...)
belong to the first plan, not to init. Budget: CLAUDE.md ≤ 120 lines typical, 200 hard; everything else short.
Content rules:

- CLAUDE.md holds only what an agent cannot infer: purpose (≤ 3 lines), start-here, exact commands,
  architecture rules (≤ 10 lines), numbered invariants, project-specific git rules, a docs index.
- Never a derivable layout listing, dependency list or tutorial.
- A rule that must always hold goes in a check (test, script, hook, deny rule), with CLAUDE.md naming
  the check; emphasis is not enforcement.
- **Global-rule dedup**: drop anything `~/.claude/CLAUDE.md` already says (package manager,
  fonts, git merge and push rules, question tool, verification). A project rule that *differs* from
  global stays and is marked `Override:` so it is not mistaken for drift.
- Architecture: `references/architecture.md` (swappable interfaces at every boundary, composition
  root, contract test per interface, enforced boundaries). Ecosystem commands, layout and conventions:
  `references/<ecosystem>.md` (`typescript`, `python`, `rust`, `go`, `dotnet`, `swift`, `godot`,
  `non-code`), each stamped `verified: <date>`. A reference older than 90 days, or one for a tool
  version the agent finds newer, is re-verified against primary docs before use; say so in the proposal.

## What it never does

- Add a remote, push, or choose a LICENSE type without asking (each is asked separately).
- Turn "no remote / don't push now" into a deny rule or a standing project rule unless the user asked for a permanent ban (then it is marked `Override:`).
- Write a CI workflow that would fail on day 0 because its gate command does not exist yet.
- Overwrite an existing `CLAUDE.md` or `AGENTS.md`; in `new` mode that switches to `audit`.
- Copy global rules into the project (global-rule dedup, above), or duplicate CLAUDE.md into AGENTS.md.
- Write the repo in `audit` mode, or before the user approved the proposal.
- Run `install.sh`, deploy, or touch anything outside the repo and `~/.claude/orchestrations/<repo-name>/`.
- Commit a secret, a real credential, `.env`, `CLAUDE.local.md` or `settings.local.json`.
- Install tools or global config on the user's machine: it proposes the commands, the user decides.
- Treat the permission/hook snippets as security: they are config text; deny rules are the control.

## The init record

`~/.claude/orchestrations/<repo-name>/init.md`, format in `templates/init-record.md`: mode, date,
ecosystem, answers, extras chosen, files written, commit sha, `refresh: pending|done|skipped|n/a`.
Orchestrate reads it to decide the refresh (only `pending` fires); `audit` reads it to know what was generated and what the user chose.

## Paths

`~/.claude/skills/orch-init/` (live) — `templates/{CLAUDE.md, AGENTS.md, STATE.md, DECISIONS.md,
init-record.md, proposal.md, audit.md, snippets.md}`; `references/architecture.md`;
`references/{typescript, python, rust, go, dotnet, swift, godot, non-code}.md`. The role is
`agents/orch-init.md` (`skills/orchestrate/roles/orch-init.md`).
