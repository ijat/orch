# Choosing model × effort

Read before every planner/implementer tier choice and before overriding any other role's default.
The ladders in `SKILL.md` are the normal path; this file is how to leave them, or decide where on
them to start, with the trade-offs said out loud. Every implementer spawn (plan step, fix round,
pilot, lite task) is routed with **§ Implementer routing** below.

## The models (facts from the Claude API model pages, cached 2026-11-04; Fable row from the pricing and model docs, 2026-11-12)

Which model each alias resolves to is read from real transcripts (`role-cost.py`), not assumed.
Keep the aliases in the agent files and spawn parameters (never pinned IDs) so each role
follows the newest model. Aliases depend on the Claude Code version: `haiku` → Haiku 5.5 needs
v2.1.293+ (confirmed on 2.1.294, 2026-10-08), and sessions on older versions got Haiku 4.5.

| alias → model | `haiku` → Haiku 5.5 | `sonnet` → Sonnet 5.5 | `opus` → Opus 5.5 | `fable` → Fable 5.2 |
|---|---|---|---|---|
| API price in / out per 1M | $0.10 / $0.50 (≤100K prompt; $0.50 / $2.50 beyond) | $2 / $10 | $4 / $20 | $8 / $40 |
| Cache read per 1M | $0.01 (≤100K prompt; $0.05 beyond) | $0.10 | $0.20 | $0.20 |
| Output cost vs Sonnet | 0.05× | 1× | 2× | 4× |
| Context / max output | 1M / 128K | 1M / 128K | 1M / 128K | 1M / 128K |
| Effort levels | low–max | low–max | low–max | low–max |
| API default effort | medium | high | **medium** | high |
| Notes | refuses in cyber, bio, frontier_llm, general_harms, with no server-side fallback | everyday coding/agent work; refuses in 5 categories (cyber, bio, frontier_llm, reasoning_extraction, general_harms) | the default model; medium ≈ Opus 5 at high on multistep coding; refuses in cyber, bio, reasoning_extraction | most capable; single turns on hard tasks can run **many minutes** (output up to 30% faster than 5.1 per the claude.dev model-choice post of 2026-11-09; not measured here) |

**Cost here is usage-limit headroom, not dollars:** this account runs on the 5h/7d limits
(`orch usage`). The API prices are the best available proxy for how hard each model draws on
them (not verified for the subscription; a pilot's `orch usage` delta is the real measure).
Long agentic runs are mostly cache reads. On recorded Sonnet implementer tokens (2026-11-04), the same
tokens cost 2.0× Sonnet at Opus prices, ~3.1× at Fable 5.2 prices (3.9× at 5.1's, where it was measured), and 0.07× at Haiku prices below 100K context
(0.36× above it). Transcripts under-record output, so the Fable ratio sits somewhat higher. Speed, measured for the implementer only (2026-10-21): Haiku is
*not* faster — `haiku/medium` took 1.3× `sonnet/high`'s wall time on small steps and about the same
on real-repo replays, `haiku/high` 1.7×; `sonnet/medium` was the fastest. Opus and Fable speed are
not measured locally.

## What effort trades

Effort sets thinking depth and total token spend within one model. Lower effort means fewer and
more consolidated tool calls, less preamble, terser output. The reference's guidance:
- `low`: simple, well-specified, mechanical work.
- `medium`: the cost-saving step down where quality holds.
- `high`: the floor for anything intelligence-sensitive.
- `xhigh`: the usual best for coding and agentic work on recent models. (For implementer spawns on
  the 5.5 models, § Implementer routing decides when it is used.)
- `max`: only when correctness matters more than cost.

The current generation is recalibrated: Opus 5.5 at `medium` matches or beats Opus 5 at `high` on
multistep coding; Sonnet 5.5's starting point for agentic coding is `medium`, Haiku 5.5's is
`medium`, Fable 5.2's is `high` (platform docs: most of its gain is already there at `high`; `max` is "often for only a small gain"). At `low`, Haiku and Sonnet 5.5 may stop early or skip verifying a
change on long agentic tasks. These starting points inform § Implementer routing only; the other
roles keep the tiers in § Tier table.

Two consequences:
- **Try a stronger model at lower effort before a weaker model at higher effort.** On the newest
  models, low effort often matches the previous generation at high.
- **Judge cost per finished task, not per spawn.** A cheap pass that fails review and needs a second
  round has already cost more than one stronger pass.

## The factors

Weigh these for every non-default choice:

- **User caps come first.** If the user limits tiers ("no Opus", "Sonnet only", "no Fable this
  week"), that is a hard ceiling above every factor below. Log it once as an `override` journal
  event. Then escalate *within* the cap by raising effort: e.g. with Opus banned, planners and
  reviewers run at `sonnet/xhigh` or above, and the implementer follows § Implementer routing →
  Caps and pressure. The tier table's Never and default-only cells yield to a cap (§ Tier table). If the cap stops a needed escalation (the work keeps failing at the capped
  ceiling), tell the user what the cap is costing; don't quietly break it.

- **Quality:** how bad is a subtle mistake here, and would review catch it? Sensitive areas
  (auth, crypto, secrets, untrusted input, PII, deserialization, sandboxing, infra credentials),
  concurrency and irreversible steps push up. Well-specified steps backed by tests push down.
- **Ability:** can this model do the task at all? Haiku is for lookups, running commands, and small
  implementation steps whose every contract item a command checks (measured 2026-10-21: equal to
  Sonnet there; in real repos it skips the items no test checks); not design. Sonnet handles most
  implementation. Opus handles ambiguous design and hard debugging. Fable is the planner's escalated pick
  (§ Tier table); for every other role it needs one of the four reasons in § Fable outside planning.
- **Performance / urgency:** is the user waiting? Fable turns can run for minutes, and `max` effort
  is slower than `high`. If the user said it's urgent, prefer a faster tier and say what that risks.
  Fable counts as the faster tier only where the Opus tier it replaces is `opus/max` (planner bench: `fable/high`
  ~10 min faster per plan); against `opus/medium` or `opus/high` it is slower and costlier, so urgency never raises
  to it from those.
- **Cost:** check `orch usage` first. At `WARN` stay on or below the default tier
  unless a higher one is already earned. A Fable spawn needs no user notice or approval; its `tier_choice`
  journal line, with the reason, is the record.
- **Limitations:** safety classifiers can refuse security-adjacent work (Sonnet 5.5 has the widest
  set of categories), and Haiku has no fallback when it refuses. Opus 5.5 refuses in fewer
  categories than Sonnet 5.5, so it is the safer choice for security tooling. Context is 1M on every model, so it never
  decides the choice.
- **Escalation history:** a `post-failure` escalation moves up a rung, never sideways (implementer exceptions: an exact-edit fix list, and fix rounds, which are routed fresh — § Fix rounds). Never jump
  from the default straight to Fable on the failure ladder (implementer ladder below); a non-planner Fable spawn needs one of the four reasons in § Fable outside planning, and "complex", "important" or "to be safe" is none of them.

## Tier table

This table lists every tier each role may run at. A tier it does not list for a role runs only on the user's
request, journaled as an `override` event. The caps in § Caps and pressure are part of it. A Fable spawn needs no
user notice or approval; the `tier_choice` or `escalation` journal line with its reason is the record. Tags as in
§ Implementer routing; the planner bench is in § Planner bench below.

A user cap (§ The factors) overrides a Never or default-only cell, so a capped role always has a tier: run it at the
nearest tier the cap allows, raising effort inside the cap. With Opus banned, planners, reviewers and the director
run at `sonnet/xhigh` or above; with Haiku banned, the explorer, executor and graph run at `sonnet/low`. The
implementer follows § Caps and pressure. A cap that leaves a role no tier at all is for the user (a parked
question), not for a quiet substitute.

| Role | Default | Allowed, and when | Never | Basis |
|---|---|---|---|---|
| Planner | `opus/high` | `opus/medium` or `sonnet/high`: small, unambiguous task, nothing sensitive (fewer alternatives explored). `opus/max` or `fable/high`: escalated, for an architecturally ambiguous, cross-cutting or high-blast-radius task, or one the user called large, complex or risky; the orchestrator picks either per task and journals why (see § Planner bench). After a failure: a plan whose design failed moves to whichever of the two has not planned this work yet. `fable/max`: only after both an `opus/max` and a `fable/high` plan of this work failed on design | `fable/medium` and `fable/low` (open benchmark: `fable/medium` vs `opus/high`); `fable/max` as a first plan; `haiku/*` | [measured 2026-11-12, § Planner bench; judgment for the small-task rows] |
| Implementer | `sonnet/high` | § Implementer routing and § Escalation ladder: `haiku/medium` through its gate, `haiku/high` guided, Opus by signal or ladder rung. `fable/high` only by § Fable outside planning | the cells marked never or dominated in § Cells; `fable/medium`, `fable/low`, `fable/max` | [measured 2026-10-21, 2026-10-28; judgment for Fable] |
| Code reviewer | `opus/high` | `opus/xhigh` for a large or sensitive diff. `fable/high` only by § Fable outside planning | `sonnet/*` and `haiku/*` as the reviewer; `fable/medium`, `fable/low`, `fable/max` | [judgment, observed] |
| Security reviewer | `opus/high` | `opus/xhigh` for a large or sensitive diff. `fable/high` only by § Fable outside planning | `sonnet/*` and `haiku/*` (widest refusal sets, no fallback on Haiku; § The factors, Limitations); `fable/medium`, `fable/low`, `fable/max` | [judgment; model facts above] |
| Plan reviewer | `sonnet/high` | `opus/medium` if any milestone is sensitive. Runs only on a roadmap or a plan written at an escalated planner tier. `fable/high` only by § Fable outside planning | `haiku/*`; `fable/medium`, `fable/low`, `fable/max` (it checks four questions, it does not re-plan) | [judgment] |
| Director | `opus/high` | `opus/max` after the user reverses an accepted verdict; `visual:` plan steps only. `fable/high` only by § Fable outside planning | `fable/medium`, `fable/low`, `fable/max` | [judgment] |
| Explorer | `haiku/low` | the default only (read-only lookups and claim checks) | anything else, `fable/*` included | [judgment] |
| Executor | `haiku/low` | the default only (it runs commands and returns a digest) | anything else, `fable/*` included | [judgment] |
| Graph | `haiku/low` | the default only (it keeps the code index fresh) | anything else, `fable/*` included | [judgment] |
| Init | `sonnet/high` | `opus/medium` for an ecosystem with no `skills/init/references/<ecosystem>.md` | `fable/*` | [judgment] |

### Planner bench

2026-11-12 (notes of the `planner-tier-bench` task; the harness is `~/.claude/benchmarks/planner-tiers/`).
Four past tasks, each planned at `opus/max` when it really ran, were replayed from their base commit with the
request verbatim and the installed `orch:planner` at three tiers; blinded Opus judges scored each plan against an
answer key built from what really happened (plan review, revisions, review blockers, retro).

| | `opus/max` | `fable/high` | `fable/medium` |
|---|---|---|---|
| A atomic repo switch | **7** | 6 | 5 |
| B codec | 6 (chose a roadmap) | **8** | 7 |
| C frame pacing | **7** | 5 | 6 |
| D agent join | **7.5** | 7 | 5 |
| mean score | **6.88** | 6.50 | 5.75 |
| mean cost at list prices | $8.67 | $9.14 | $3.99 |
| mean wall time | 36 min | 26 min | 14 min |
| wrong turns | 3 | 3 | 8 |

- **What it shows:** `opus/max` and `fable/high` are close. `opus/max` won 3 of 4 and led the mean by 0.4;
  `fable/high` cost the same and was ~10 min faster per plan, and won B (8 vs 6, where `opus/max` chose a
  roadmap). So the orchestrator picks per task: `opus/max` when plan quality matters most, `fable/high` when the
  user is waiting. `fable/medium` is cheapest and fastest but scored lowest
  with 8 wrong turns, so it is not listed.
- **Caveats:** 1 rep per cell; hindsight scoring against keys that lean on the design actually shipped (the
  judges accepted sound alternatives); judge confidence medium-high on A and medium on C and D (B's top rank high, the rest medium); a score gap under
  one point is not a ranking. A first, leaked blind pass ranked B, C and D the same way, scores within 1 point.
- **Not tested:** `opus/high` (the default), `opus/medium`, `fable/max`, and any non-planner role. Vendor
  claim, not ours: `fable/high` ≈ `opus/max` on FrontierCode 2.0 (45.3% vs 45.5%) at about half the cost per task
  (claude.dev post, 2026-11-09). The next benchmark is `opus/high` on the same four tasks against the existing
  `fable/medium` cells.

### Fable outside planning

For every role except the planner, `fable/high` needs one of these four, and the orchestrator decides and
journals which:

1. An `opus/max` pass on the same work failed (the ladder's last rung).
2. A named capability Opus lacks, stated as a claim the orchestrator checks before spawning.
3. A measured result in this file where Fable beats the Opus tier for that role. None exists yet: the bench
   above is planner-only.
4. Urgency: the user is waiting and the Opus tier it would replace is `opus/max`. The bench measured Fable
   ~10 min faster than `opus/max` per plan; that speed gain is unmeasured for other roles. Urgency never raises
   to Fable from `opus/medium` or `opus/high`.

"Complex", "important" and "to be safe" are not reasons. A role that wants Fable returns `blocked` with
`need: tier — <reason>`; the orchestrator grants it, picks an Opus rung instead, or re-briefs, and journals the
decision.

## Implementer routing

Tags: [measured] = the 2026-10-21 implementer benchmark (12 tasks × 3 reps per setting, 216 runs,
hidden tests plus blinded Opus review); [observed] = orchestration journals and transcripts;
[judgment] = reasoned, not measured.

### Default: `sonnet/high`

Equal to `haiku/medium` on small, fully checkable steps; better on real-repo replays (review score
8.56 vs 7.89; 0 vs 4 medium issues, all of them contract items no test checked — Sonnet's one issue
was help text contradicting the code); the fastest cell at that quality [measured]. Real spawns
make 7–10× the benchmark's requests at 3–5× its peak context [observed], where Haiku's price edge
shrinks (the 100K price step) and a skipped item costs a review round (~$2 per `opus/high` review
run, plus a fix pass). So Haiku gets only the steps its gate admits.

### Signals — read at spawn time

| Signal | Holds when | Source |
|---|---|---|
| `lite` | the brief says `mode: lite` | `status.md` |
| `history` | this work (the step, or the code a fix touches) already ran and failed: a routed review finding, `blocked: stuck`, a looping check-in, a `[ ]` in its `contract-check` | `review.md`, `status.md`, `notes.md` |
| `flag` | the planner flags the step as a concurrency protocol, a complex or performance-sensitive algorithm, or a delicate state change (persisted format, migration, irreversible op), or names `opus/*` for it **with one of those reasons**; a roadmap row's tier is the planner's budgeting estimate and never a flag (each step is routed from the milestone's `plan.md`), and "sensitive" or "security" alone is not a flag reason | plan Summary, the step, Risks |
| `boundary` | the step implements security-bound logic: an authn/authz decision, crypto/key/token handling, parsing or deserializing untrusted input, a sandbox or permission check, credential-bearing deploy config | step 5 list + the step's `do:` |
| `sensitive` | the step's files are in a sensitive area, boundary or not | plan `sensitive areas:`, Files touched |
| `unknown_cause` | the step has to find a cause (investigate, profile, bisect, "why does X"), not apply a stated fix | the step's `do:` |
| `refusal` | the work is in a classifier domain: exploit, malware or offensive tooling; training or distilling AI models; dual-use bio | the step |
| `guide` | the step's `guide:` names a file and `orch plan-check` reported no guide failure (it exists, has its four sections, and every path it cites exists) | the step, plan-check output |
| `size`, `mechanical` | the step's `S/M/L`; mechanical = rename, move, wire or boilerplate after a pattern the plan names, no new behaviour | the step |
| `files` | paths in the step's `files:` | the step |
| `unchecked` | contract items and `done when` lines that map to no command in the step's targeted verification (tests, goldens, validators, typecheck, a grep) and to no test case the contract lists with inputs and expected output | contract + Verification |
| `prose` | the step must write text that states behaviour: docs, README/FORMAT, help or usage text, explanatory comments, changelog, user-facing copy (renaming inside prose is not prose) | the step |
| `words` | words in the step's `do` + `contract` + `done when` + its verification lines | the step |
| `extras` | the step has `sketch:` or `visual:`, or adds flaky-prone tests (async, timer, concurrency, browser) | the step |
| `urgent` | the user is waiting on this step or said it is urgent | conversation |
| `caps` | user caps (no Haiku / no Opus / Sonnet only / no Fable / an effort ceiling), and repo-lesson caps such as "no cheaper tiers" | conversation, `LESSONS.md` |
| `usage` | `orch usage` state and the 7d % | pre-flight |

Not used: repo size (Haiku was clean on the 1,260-file R3 repo and skipped items on 187–284-file
ones), or brief length on its own (clean up to 1,104 words when every item was checked; skips on
1,017–1,236-word replays) [measured].

### Procedure — first match wins

1. `lite` → `opus/medium` (it must plan as well as code); `opus/high` at the edge of lite eligibility (the eligibility check passed but you recorded a caveat: an open design choice, a borderline file count, or a near-sensitive area). Never Haiku (unmeasured at planning) or Fable (that need means standard mode). [observed: 12 lite tasks, 0.5 review rounds on average]
2. `history` → § Escalation ladder.
3. *(retired 2026-11-06: a planner can no longer route a step to Fable. Fable comes only from the § Escalation ladder or § Fable outside planning.)*
4. `flag`, `unknown_cause` or `refusal` → `opus/medium` (`proactive`). `boundary` alone → `sonnet/high`: the security review is due on it (SKILL.md step 5), and that review, not the writer's tier, is the security check. `boundary` when the user declined a due security review → `opus/high`. [judgment since 2026-10-28; was `opus/medium`]
5. `guide`, with G1 and G6 holding → `haiku/high`. The guide replaces G2–G5: size, unchecked items, prose and brief length don't bar a guided step. [measured 2026-10-28, see § Guided Haiku]
6. The Haiku gate passes → `haiku/medium`. [measured]
7. Otherwise → `sonnet/high`. [measured]
8. Apply § Caps and pressure, then journal the `tier_choice` with the deciding signals in `tradeoff`.

At the plan gate, show the tier this gives each step, one line per step (step → tier → deciding
signal). Spawn-time facts (history, caps, usage) can still change it.

### Haiku gate — every line must hold

- G1. Procedure steps 1, 3 and 4 didn't fire (on a fix round, judge the step's own signals; `history` alone doesn't fail G1); not `boundary`; not `sensitive`; no `extras`.
- G2. One plan step per spawn; `size` S, or M when `mechanical`; `files` ≤ 5, or ≤ 15 when `mechanical`.
- G3. `unchecked` = 0.
- G4. No `prose`.
- G5. `words` ≤ ~1,100.
- G6. Not `urgent`; no cap excludes Haiku.

[measured, fitted after the results] On the 8 benchmark tasks this gate admits (M1–M3, O1–O3, T2,
T3), `haiku/medium` scored 9.58 (24/24 hidden-test passes, 1 medium issue) against `sonnet/high`'s
9.54 (24/24, none), at 0.10× the cost and 1.3× the wall time. The 4 it rejects (T1 for its
concurrency tests, R1–R3 for unchecked items and prose) include both replays where Haiku skipped
items; across the 4, 8.25 vs 8.67.
G2's and G5's bounds are the largest admitted tasks (15 files, 1,104 words), not known failure points.

### Guided Haiku

A planner-written guide (`templates/guide.md`: where each part goes and the existing pattern to model it
on, a checklist of every brief item, the verify commands, repo pitfalls) is what Haiku lacked on long and
real-repo steps. Benchmark 2026-10-28 (`~/.claude/benchmarks/implementer-tiers/baselines/2026-10-28-guided-full`,
12 tasks × 2 reps, all on the same role, one blinded Opus review round): `haiku/high` + guide 9.46 (1 medium,
0 high, 24/24 hidden tests), unguided `haiku/high` 9.12 (5 medium, 1 high, 23/24), `sonnet/high` 9.29
(1 medium) and `sonnet/high` + guide 9.25 — the guide doesn't help Sonnet. Guided Haiku was weakest on the
real-replay band (8.50 vs Sonnet's 8.67–8.83; its one issue a skipped brief item), ran 0.22× Sonnet's bench
cost and 1.75× its wall time. Real spawns are 7–10× the bench's requests, so the real price edge is smaller.
No pattern (a new project's first steps, code with no precedent) → no guide → Sonnet; this split is
judgment, not measured (every benchmark task was an existing repo). [measured; judgment for the split]

When a Haiku spawn returns `done`, read its `contract-check` in `notes.md` before anything else: a
`[ ]`, or fewer lines than the step has contract items, re-routes the step to `sonnet/high`
(`post-failure`) before review. A `haiku/*` return of `blocked` with `need: retier` re-routes it to
`sonnet/high` as `proactive`, not `post-failure`: it is not a failure, so there is no prior pass to name; journal it as a `blocker` with `need: retier`.

### Fix rounds, pilots, visual and parallel steps

- **Fix rounds** (one batch per review, SKILL.md § Review loops) start at `sonnet/high` whatever tier wrote the code — no writer floor. They go to
  `opus/medium` only when the fix itself carries a `flag` (it changes the concurrency protocol, the
  algorithm or the state change the step was flagged for, not a rename or a log line beside it),
  `unknown_cause` (the finding's cause must be found), or `refusal`, or by the ladder: a class already
  raised in an earlier round of this task (`post-failure`, `review_pattern`). A step that already climbed
  the ladder after a failure in this task (a `post-failure` escalation, not a proactive signal) keeps the rung it
  reached for its later fix rounds and climbs from there (Sonnet already had trouble with it). Security-reviewer findings
  follow the same rule; the security reviewer re-reviews the fix. One exception downward: an **exact-edit list** — at most 5 routed findings, each with `file:line` and
  a literal suggested change, none naming a class or invariant, none from the security reviewer,
  on code Haiku didn't write, with G1 and G6 holding — may go to `haiku/medium`. [judgment]
- **Fix-and-check cycles** (up to 3 per code-review round, SKILL.md § Review loops) climb the implementer only; the
  reviewer keeps its tier so it can be resumed. Cycle 1 is routed as above. Cycle 2 goes one rung up the ladder from
  cycle 1's tier. Cycle 3 goes to `opus/max`, or stays there if the ladder already reached it. Cycles 2 and 3 are
  `post-failure` fresh spawns (never resumed: SKILL.md step 7), each given the prior diff, notes and the check's
  findings. This rule never reaches `fable/high` by itself; the ladder reaches Fable only after a failed `opus/max` pass. The cycle cap is never raised
  (SKILL.md § Review loops): a blocker open after cycle 3 is a parked question, and Fable follows only if the user
  picks it there. [judgment]
- Code Haiku wrote that gets any routed finding → `sonnet/high` (`post-failure`).
- A round-2 finding in a class already raised in round 1 → the next rung (`post-failure`,
  `review_pattern`). At the cap, SKILL.md step 9 and § Review loops.
- **Pilots:** a mechanical step that passes the Haiku gate and whose items each have their own check pilots at `haiku/medium`. It
  continues at `haiku/medium`, in spawns of ≤ ~10 items, only if the pilot passed its gates with
  every `contract-check` line `[x]`; otherwise offer the retier to `sonnet/high` at the pilot gate. Other pilots run
  at the routed tier. [judgment]
- **Visual steps:** route by the mechanical difficulty, never Haiku. Don't raise for taste; the
  director owns it. [current rule]
- **Parallel tracks:** route each track on its own.

### Escalation ladder

`haiku/medium | haiku/high (guided) → sonnet/high → opus/medium → opus/high → opus/max → fable/high` — at most one rung per failure,
never sideways or down for the same work (one exception: an exact-edit fix list, above).

| Failing tier | Failure | Next | Why |
|---|---|---|---|
| `haiku/medium`, `haiku/high` (guided) | any routed finding; a check still failing after its three strikes; a `[ ]` in `contract-check` | `sonnet/high` | Don't raise Haiku's effort: at `high` it skipped the same items on replays (4 medium issues, as at medium); `max` fixed that at 1.9× `sonnet/high`'s cost and 3.8× its wall time [measured] |
| `sonnet/medium` (caps, pressure, saver) | any routed finding | `sonnet/high` | escalate on the first review failure [current rule] |
| `sonnet/high` | round-1 findings | fix at `sonnet/high` | [current rule] |
| `sonnet/high` | the same class again in round 2; `stuck` where the check is right and the approach keeps failing | `opus/medium` | change model, not effort: Opus costs 2× Sonnet per token [list prices]; `sonnet/xhigh` is for the no-Opus cap [judgment] |
| `opus/medium` | round-1 findings that are neither diligence nor ability failures (routine review findings) | fix at `opus/medium` | same as Sonnet's round 1 [current rule] |
| `opus/medium` | diligence: an omission, an edge case the contract named, thin tests, an unverified claim — the design is right | `opus/high` | raise effort within the model [judgment] |
| `opus/medium` | ability: the same class after the Opus pass, the wrong mechanism twice, `stuck` on a hard item | `opus/high` | Fable on the failure ladder only follows a failed `opus/max` pass (2026-11-06; was a jump to `fable/high`) [current rule] |
| `opus/high` | any | `opus/max` | raise effort within the model before changing model [current rule 2026-11-06] |
| `opus/max` | any | `fable/high` | no user notice; journal the `escalation` event with the reason (reason 1 of § Fable outside planning) [current rule] |
| `fable/high` | any | planner revision if the plan is the problem, else the user | no `fable/xhigh` unless the user asks [judgment] |

- **A plan defect at any rung** (the contract can't hold as written) goes to a planner revision, not
  a tier raise; an environment need (sudo, a device, a denial) goes to SKILL.md's rung ladder. [current rule]
- **Skip a rung** only when the failure reveals a signal the first routing missed: run the
  procedure again with it (a Haiku step that turned out to touch concurrency → `opus/medium`). [judgment]
- **Fable** (implementer and every other non-planner role) needs one of the four reasons in § Fable outside planning. The failure ladder alone reaches it only after a failed `opus/max` pass on this work, never from Haiku, Sonnet or a lower Opus rung, and no planner flag starts it. [current rule]
- Every `post-failure` pass gets the prior diff, notes and findings (SKILL.md).

### Cells

| Cell | Verdict | Use when | Never when | Basis |
|---|---|---|---|---|
| `haiku/low` | dominated by `haiku/medium` | — | always | [measured] the only hidden-test failure in 216 runs, a README cut short mid-rename, a self-added deadlocking test |
| `haiku/medium` | gated | the Haiku gate holds; exact-edit fix lists; mechanical pilots | the gate fails; as an escalation target | [measured] |
| `haiku/high` | guided | steps with a `guide` (procedure step 5) | unguided steps: 9.42 vs medium's 9.58 on admitted tasks at 1.2× the cost; on replays the same skips at 3× | [measured: 2026-10-21 unguided, 2026-10-28 guided] |
| `haiku/xhigh` | dominated (expected) by `sonnet/high` | — | always | [judgment] lies between measured high and max |
| `haiku/max` | dominated by `sonnet/high` | — | always: no skips, but 1.2–1.9× `sonnet/high`'s cost and 3.8–5.8× its wall time | [measured] |
| `sonnet/low` | excluded | — | always: early stops and skipped verification on long agentic tasks | [judgment, reference] |
| `sonnet/medium` | caps and pressure only | a cap excludes Haiku and the step passes the gate, at 5h WARN; a user effort ceiling; 7d saver routing | otherwise: 9.00 vs 9.54 on admitted tasks, about equal on replays, for 0.74–0.83× the cost | [measured] |
| `sonnet/high` | **default** | everything the procedure doesn't route elsewhere, `boundary` steps (with their security review) and fix rounds included | `flag`, `unknown_cause`, `refusal`; `boundary` with the security review declined; lite | [measured, observed; judgment for `boundary`] |
| `sonnet/xhigh` | cap only | no-Opus cap: Opus-signal steps, first escalation | Opus is allowed (`opus/medium` is the better buy) | [judgment] |
| `sonnet/max` | cap only | no-Opus cap: second escalation | Opus is allowed | [judgment] |
| `opus/low` | excluded until benchmarked | — | — | [judgment] first candidate for the next benchmark |
| `opus/medium` | signal tier, rung 3 of the ladder | Opus signals (`flag`, `unknown_cause`, `refusal`); lite; post-failure from `sonnet/high` | gate-passing or plain steps; `boundary` alone; routine fix rounds | [observed, reference] |
| `opus/high` | rung 4 | diligence failure at `opus/medium`; `boundary` when the user declined a due security review; lite at the edge | before `opus/medium` has tried non-boundary work | [judgment, observed] |
| `opus/xhigh` | unused on the ladder | a user request; a reviewer tier for a large diff | as an implementer rung: the ladder goes `opus/high → opus/max` | [judgment] |
| `opus/max` | rung 5 | a failure at `opus/high`; the no-Fable cap's last pass | before `opus/high` has tried the work; as a first route | [judgment, 2026-11-06] |
| `fable/low`, `fable/medium` | never (benchmark candidates) | — | — | [measured as planner, 2026-11-12] `fable/medium` scored lowest of three planner tiers (5.75 vs 6.88 and 6.50, 8 wrong turns vs 3); no measured gain over Opus for implementers [judgment] |
| `fable/high` | last rung | after a failed `opus/max` pass on this work; the other three reasons of § Fable outside planning; the user's request | straight from Haiku, Sonnet or a lower Opus rung without a listed reason; a planner flag; "complex", "important", "to be safe" | [current rule]; [measured] only for planners |
| `fable/xhigh` | on the user's request | the user asks after `fable/high` failed and the plan holds | otherwise | [judgment] |
| `fable/max` | never for implementers | — | — | [judgment] planner tier |

### Caps and pressure

| Situation | Routing | Basis |
|---|---|---|
| No Haiku, or a repo lesson such as "no cheaper tiers" | gate-passing steps, pilots and exact-edit fixes → `sonnet/high` (`sonnet/medium` is allowed at 5h WARN, not required) | [observed] |
| No Opus (and so no Fable unless the user says otherwise) | Opus-signal steps → `sonnet/xhigh`; ladder `haiku/medium → sonnet/high → sonnet/xhigh → sonnet/max` → the user | [judgment] |
| Sonnet only | as no Opus; gate-passing steps → `sonnet/high` (`sonnet/medium` at 5h WARN) | [measured] |
| No Fable | the ladder ends `opus/high → opus/max` → the user | [judgment] |
| Effort ceiling, e.g. ≤ medium | `haiku/medium` gated, `sonnet/medium` otherwise, `opus/medium` for signals; escalate by model only | [observed] |
| 5h WARN, STALE or UNKNOWN | no proactive `opus/high`, except a `boundary` step whose due security review the user declined (it keeps `opus/high`; if that can't run, pause); signal steps keep `opus/medium` (the plan earned it); no proactive Fable (an earned post-failure Fable pass still runs, as with `opus/max`); batch fix findings; never send a step that fails the gate to Haiku for budget — pause instead | [judgment] |
| 5h STOP | pause (SKILL.md) | [current rule] |
| 7d ≥ 90% | ask as a parked question (SKILL.md § Checkpoints and questions: recommended option first; recommend continue-as-routed when the remaining work is flagged or sensitive, otherwise saver routing): continue as routed; **saver routing** — unflagged, non-`sensitive` steps that fail the gate → `sonnet/medium`, the gate admits any M step, `algorithm`/`state` flags → `sonnet/high` with the sketch and escalation on the first failure, Opus kept for concurrency, `unknown_cause`, `refusal`, `boundary` stays `sonnet/high`; or hold until the reset | [measured for `sonnet/medium`; judgment for the rest] |
| User waiting / urgent | no Haiku; gate-passing steps → `sonnet/high`; Opus signal steps keep Opus, and say it is slower; no `opus/high` unless earned; Fable only under reason 4 of § Fable outside planning (it replaces an `opus/max` tier) | [measured wall time; judgment] |

When a cap blocks a needed escalation, tell the user what it is costing; don't quietly break it.
Never lower a `boundary` or `sensitive` step's tier for budget or usage pressure; pause instead. When a cap removes a tier a rule requires (lite mode with no Opus, the end of an effort-ceiling ladder), ask the user (a parked question) instead of picking a substitute.

### Cost and latency

Dollar medians below were recorded before the Sonnet cache-read correction of 2026-11-04, so Sonnet's are overstated.

| Tier | Benchmark $/run · wall (admitted tasks; replays) | Real spawns, recorded medians | Same tokens vs Sonnet |
|---|---|---|---|
| `haiku/medium` | $0.022 · 94 s; $0.079 · 222 s | none yet | 0.07–0.36× by context (+ its extra turns) |
| `sonnet/medium` | $0.161 · 47 s; $0.444 · 191 s | 9 runs: 34 requests, $0.91, 5.6 min | 1× (fewer tokens) |
| `sonnet/high` | $0.219 · 71 s; $0.536 · 235 s | 193 runs: 66 requests, 193K peak, $2.35, 13.6 min (p75 41) | 1× |
| `opus/medium` | — | 133 runs: 84 requests, 245K peak, $4.74, 27 min (p75 50) | 2× |
| `opus/high` | — | 33 runs: 122 requests, 287K peak, $10.06 | 2×; ~2× `opus/medium` per spawn |
| `fable/high` | — | 2 runs | ~3.1× at Fable 5.2 prices (3.9× at 5.1's); turns can run many minutes |

"Recorded" is transcript usage at list prices (`role-cost.py`'s method). 47% of implementer requests
are logged with a placeholder output count, so the dollars are lower bounds, and Opus's per-spawn
figures carry its harder steps. Minutes come from journal `return.duration_ms`.

### Tell the user

(Not a Fable spawn: it needs no notice; its journal line is the record.)

- At the plan gate: the per-step tier map.
- Once, when a cap blocks an escalation or holds an Opus-signal step below Opus, with what it risks.
- At 7d ≥ 90%: the saver question above. At 5h WARN: SKILL.md's one line.
- When an Opus-signal step keeps Opus while the user is waiting: that it is slower, and why.

## Record it

Every `tier_choice` and `escalation` journal event, and the spawn brief, carries a **`tradeoff`**
line: what this tier buys and what it gives up, naming the factors that decided it. For example:
`"haiku/medium: gate holds (S, 3 files, 7/7 contract items test-checked, no prose); gives up depth on anything unlisted — any finding moves it to sonnet/high"`.
A default-tier choice can use `"default: no factor pushes up or down"`.
