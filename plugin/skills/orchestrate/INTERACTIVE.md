# Interactive mode

Read this when the task's mode is interactive. Everything in `SKILL.md` applies except where this file says otherwise.

**You** are the planner as well as the orchestrator: the user talks to you while the plan takes shape,
and implementers, reviewers and the merge run exactly as in standard mode. Use it when the user picks
"interactive mode" / "orchestrate interactive" / "plan it with me". Any size of task, including a roadmap.

**The boundary is the point of the mode: you never write product code.** Not while planning, not after
the gate, not in fix rounds. Your writes are the task directory (the planner's files plus your
bookkeeping); every code change goes to an `orch:implementer` (steps 7 and 9). *Direct edits* still
applies after the gate and nowhere else.

**Tier.** You plan at the session's own model and effort (write `unknown` for an effort you can't
see); never switch your own. Run **Tier selection** first: if it calls for a planner above the
session's tier, say so and let the user pick — restart the session at that tier, switch to standard
mode (a spawned planner at that tier), or go on here (journal `override`). Record
`planner_tier=session <model>/<effort>` and a `tier_choice` (`role=planner`, `tradeoff`
`interactive: session plans`).

**Flow** — the standard procedure with these differences:

- **Step 3:** `orch init … --mode interactive`.
- **Step 4:** after the pre-plan `orch:graph` call, you do the planner's job. Read `../../agents/planner.md` once and follow it as
  the planner, except: the user's conversation is your brief; a question it
  would return `blocked` with goes straight to the user after rungs 1–2 of the escalation ladder;
  `need: shape_roadmap` below an escalated planner tier → tell the user and continue only if they agree (else
  standard mode at `opus/max` or `fable/high`, your pick). Its **Your context is the cost** rules bind you harder than a
  planner: your context also carries the implement and review loop. Talk the design through with the
  user before writing; until the first gate, edit the draft freely; after it, revisions are
  `## Revision N` sections you append yourself. The gate itself, `orch plan-check`, the plan
  reviewer (roadmaps, plans written at an escalated planner tier) and the approval are unchanged.
- **At the gate**, add one line: the implement/review loop is cheaper in a fresh session (`/clear`,
  then "resume <repo>/<slug>") since this one carries the whole planning conversation; staying keeps
  it at hand. Their choice. A resumed session stays in interactive mode and under the same boundary.
- **Program mode:** you write `roadmap.md`; milestone `plan.md`s are spawned per `PROGRAM.md` unless
  the user asks you to write one.
- **Later plan revisions** (an implementer's `plan_flag`, a blocker, user feedback): you append them.
  A plan whose own design kept failing escalates as in standard: spawn an `orch:planner` at the ladder
  tier (`escalation: post-failure`).

**Switching to standard:** the user asks for it, or the tier check above sends it there. Journal a
`mode_switch` (`from interactive`), set `mode: standard`, and spawn the planner with your `notes.md`
and any draft `plan.md` as starting findings.
