<!--
TEMPLATE for the canonical CLAUDE.md. Fill every section, delete every comment (including this one)
and any section that has nothing project-specific to say. Target <= 120 lines filled, 200 hard cap.
Include only what an agent cannot infer from the code: exact commands, invariants, decisions, swap
points. Do NOT list the file layout, dependencies or a tutorial. Do NOT repeat ~/.claude/CLAUDE.md
rules (package manager, fonts, git merge/push, question tool, verification); a rule that differs
from global stays and starts with "Override:". A rule that must always hold goes in a check (test,
script, hook, deny rule) and this file names the check.
-->
# {{PROJECT NAME}}

{{Purpose: what it is and for whom, <= 3 lines. Hard scope limits, e.g. "offline only", "free tier only".}}

## Start here

Read `docs/STATE.md` first. Before stopping, update it (now / next / blockers / last updated) in the
same commit as the work. Decisions live in `docs/DECISIONS.md`; do not re-open one unless the user asks.

## Commands

<!-- Exact, copy-pasteable, each one actually run when this file was written. Delete lines that do not apply. -->
- Build: `{{cmd}}`
- Test (all): `{{cmd}}`
- Test (one): `{{cmd with a placeholder for the test name}}`
- Format / lint: `{{cmd}}`
- Gate (run before every commit; must be green): `{{cmd that runs format check + lint + tests}}`

## Architecture rules

<!-- <= 10 lines, from references/architecture.md, named for THIS project's boundaries. -->
- Anything external or replaceable ({{storage, network clients, third-party libs, OS, clock, randomness, logging sink}}) sits behind an interface owned by the core; implementations live in `{{adapter dir}}`.
- One composition root (`{{file}}`) wires interfaces to implementations; nothing else constructs adapters.
- Dependencies point inward only: `{{adapter}} -> {{core}}`, never the reverse. Enforced by `{{test or script}}`.
- Each interface has a contract test every implementation passes (`{{path}}`) and a fake for tests.
- Add a new {{driver/provider/codec/...}} by adding an adapter and registering it in the composition root; the core does not change.
{{Link `docs/ARCHITECTURE.md` here only if that file was created.}}

## Hard rules

<!-- Numbered so they can be cited in reviews and decisions. Each is a rule a violation of which breaks something. -->
1. {{e.g. Never log secrets, tokens or user data; the logger redacts by field name.}}
2. {{e.g. Config comes from environment variables through the typed loader only; `.env.example` lists every key, never real values.}}
3. {{project-specific invariant}}

## Git

<!-- Only what differs from ~/.claude/CLAUDE.md. Ask the user these at init; do not default them. -->
- {{Commit cadence: per change / per task.}}
- {{Worktree per task: always / never / ask.}}
- {{Override: ...}}

## Read on demand

- `docs/STATE.md` - current work and next steps
- `docs/DECISIONS.md` - decisions and what was rejected
- {{other docs, each with a one-line reason to open it}}
