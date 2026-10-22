# Choosing model × effort

Read before every planner/implementer tier choice and before overriding any other role's default.
The ladders in `SKILL.md` are the normal path; this file is how to leave them, or decide where on
them to start, with the trade-offs said out loud. Every implementer spawn (plan step, fix round,
pilot, lite task) is routed with **§ Implementer routing** below.

## The models (facts from the bundled `claude-api` reference, cached 2026-10-06)

Which model each alias resolves to is read from real transcripts (`~/.claude/bin/role-cost.py`), not assumed.
Keep the aliases in the agent files and spawn parameters (never pinned IDs) so each role
follows the newest model. Aliases depend on the Claude Code version: `haiku` → Haiku 5.5 needs
v2.1.293+ (confirmed on 2.1.294, 2026-10-08), and sessions on older versions got Haiku 4.5.

| alias → model | `haiku` → Haiku 5.5 | `sonnet` → Sonnet 5.5 | `opus` → Opus 5.5 | `fable` → Fable 5.1 |
|---|---|---|---|---|
| API price in / out per 1M | $0.10 / $0.50 (≤100K prompt; $0.50 / $2.50 beyond) | $2 / $10 | $4 / $20 | $10 / $50 |
| Cache read per 1M | not stated (0.1× input assumed) | $0.20 | $0.20 | $0.25 |
| Output cost vs Sonnet | 0.05× | 1× | 2× | 5× |
| Context / max output | 1M / 128K | 1M / 128K | 1M / 128K | 1M / 128K |
| Effort levels | low–max | low–max | low–max | low–max |
| API default effort | medium | high | **medium** | high |
| Notes | refuses in cyber, bio, frontier_llm, general_harms, with no server-side fallback | everyday coding/agent work; refuses in 5 categories (cyber, bio, frontier_llm, reasoning_extraction, general_harms) | the default model; medium ≈ Opus 5 at high on multistep coding; refuses in cyber, bio, reasoning_extraction | most capable; single turns on hard tasks can run **many minutes** |

**Cost here is usage-limit headroom, not dollars:** this account runs on the 5h/7d limits
(`usage-check.sh`). The API prices are the best available proxy for how hard each model draws on
them (not verified for the subscription; a pilot's `usage-check.sh` delta is the real measure).
Long agentic runs are mostly cache reads, which Opus prices like Sonnet: on recorded implementer
runs, the same tokens cost ~1.25–1.3× Sonnet at Opus prices, ~2.1–2.3× at Fable prices and ~0.22×
at Haiku prices, not the 2× / 5× / 0.05× of output (transcripts under-record output, so the Opus and
Fable ratios sit somewhat higher). Speed, measured for the implementer only (2026-10-21): Haiku is
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
`medium`, Fable 5.1's is `high`. At `low`, Haiku and Sonnet 5.5 may stop early or skip verifying a
change on long agentic tasks. These starting points inform § Implementer routing only; the other
roles keep the tiers in § Typical choices.

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
  Caps and pressure. If the cap stops a needed escalation (the work keeps failing at the capped
  ceiling), tell the user what the cap is costing; don't quietly break it.

- **Quality:** how bad is a subtle mistake here, and would review catch it? Sensitive areas
  (auth, crypto, secrets, untrusted input, PII, deserialization, sandboxing, infra credentials),
  concurrency and irreversible steps push up. Well-specified steps backed by tests push down.
- **Ability:** can this model do the task at all? Haiku is for lookups, running commands, and small
  implementation steps whose every contract item a command checks (measured 2026-10-21: equal to
  Sonnet there; in real repos it skips the items no test checks); not design. Sonnet handles most
  implementation. Opus handles ambiguous design and hard debugging. Fable is for the cases Opus
  already failed or is likely to.
- **Performance / urgency:** is the user waiting? Fable turns can run for minutes, and `max` effort
  is slower than `high`. If the user said it's urgent, prefer a faster tier and say what that risks.
- **Cost:** check `usage-check.sh` first. At `WARN` (≥90%) stay on or below the default tier
  unless a higher one is already earned. Fable always needs the user told first (`SKILL.md`).
- **Limitations:** safety classifiers can refuse security-adjacent work (Sonnet 5.5 has the widest
  set of categories), and Haiku has no fallback when it refuses. Opus 5.5 refuses in fewer
  categories than Sonnet 5.5, so it is the safer choice for security tooling. Context is 1M on every model, so it never
  decides the choice.
- **Escalation history:** a `post-failure` escalation moves up a rung, never sideways (implementer exception: an exact-edit fix list, § Fix rounds). Never jump
  from the default straight to Fable (`SKILL.md`; implementer ladder below).

## Typical choices

| Situation | Tier | Gives up |
|---|---|---|
| Any implementer spawn (step, fix round, pilot, lite) | § Implementer routing: `sonnet/high` by default, `haiku/medium` through its gate, Opus by signal | — |
| Clear task, well-trodden design | planner `opus/high` (default) | — |
| Small, unambiguous task | planner `opus/medium` or `sonnet/high` | fewer alternatives explored; not for anything sensitive |
| Ambiguous, cross-cutting, high blast radius | planner `opus/max` | slower, more tokens |
| Critical / irreversible / failed at `opus/max` | planner `fable/max` | 2.5× Opus cost, minutes-long turns; tell the user |
| Plan review (roadmap or `opus/max` plan) | plan reviewer `sonnet/high`; `opus/medium` if any milestone is sensitive | depth — it checks four questions, it doesn't re-plan |
| Review of a large or sensitive diff | reviewer `opus/xhigh` or `fable/high` | cost; tell the user if Fable |

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
| `ceiling` | the planner names `fable/*` for the step, with a reason | plan Summary `implementer tier:`, the step |
| `flag` | the planner flags the step as a concurrency protocol, a complex or performance-sensitive algorithm, or a delicate state change (persisted format, migration, irreversible op), or names `opus/*` for it; a roadmap row naming Opus counts for its milestone's steps (a row's tier is the planner's budgeting estimate; each step is still routed from the milestone's `plan.md`) | plan Summary, the step, Risks |
| `boundary` | the step implements security-bound logic: an authn/authz decision, crypto/key/token handling, parsing or deserializing untrusted input, a sandbox or permission check, credential-bearing deploy config | step 5 list + the step's `do:` |
| `sensitive` | the step's files are in a sensitive area, boundary or not | plan `sensitive areas:`, Files touched |
| `unknown_cause` | the step has to find a cause (investigate, profile, bisect, "why does X"), not apply a stated fix | the step's `do:` |
| `refusal` | the work is in a classifier domain: exploit, malware or offensive tooling; training or distilling AI models; dual-use bio | the step |
| `size`, `mechanical` | the step's `S/M/L`; mechanical = rename, move, wire or boilerplate after a pattern the plan names, no new behaviour | the step |
| `files` | paths in the step's `files:` | the step |
| `unchecked` | contract items and `done when` lines that map to no command in the step's targeted verification (tests, goldens, validators, typecheck, a grep) and to no test case the contract lists with inputs and expected output | contract + Verification |
| `prose` | the step must write text that states behaviour: docs, README/FORMAT, help or usage text, explanatory comments, changelog, user-facing copy (renaming inside prose is not prose) | the step |
| `words` | words in the step's `do` + `contract` + `done when` + its verification lines | the step |
| `extras` | the step has `sketch:` or `visual:`, or adds flaky-prone tests (async, timer, concurrency, browser) | the step |
| `urgent` | the user is waiting on this step or said it is urgent | conversation |
| `caps` | user caps (no Haiku / no Opus / Sonnet only / no Fable / an effort ceiling), and repo-lesson caps such as "no cheaper tiers" | conversation, `LESSONS.md` |
| `usage` | `usage-check.sh` state and the 7d % | pre-flight |

Not used: repo size (Haiku was clean on the 1,260-file R3 repo and skipped items on 187–284-file
ones), or brief length on its own (clean up to 1,104 words when every item was checked; skips on
1,017–1,236-word replays) [measured].

### Procedure — first match wins

1. `lite` → `opus/medium` (it must plan as well as code); `opus/high` at the edge of lite eligibility (the eligibility check passed but you recorded a caveat: an open design choice, a borderline file count, or a near-sensitive area). Never Haiku (unmeasured at planning) or Fable (that need means standard mode). [observed: 12 lite tasks, 0.5 review rounds on average]
2. `history` → § Escalation ladder.
3. `ceiling` → `fable/high`; tell the user first. [current rule]
4. `flag`, `boundary`, `unknown_cause` or `refusal` → `opus/medium` (`proactive`); `boundary` with the security reviewer declined → `opus/high`. [observed, judgment]
5. The Haiku gate passes → `haiku/medium`. [measured]
6. Otherwise → `sonnet/high`. [measured]
7. Apply § Caps and pressure, then journal the `tier_choice` with the deciding signals in `tradeoff`.

At the plan gate, show the tier this gives each step, one line per step (step → tier → deciding
signal). Spawn-time facts (history, caps, usage) can still change it.

### Haiku gate — every line must hold

- G1. Procedure steps 1, 3 and 4 didn't fire (on a fix round, judge the step's own signals; `history` alone doesn't fail G1); not `sensitive`; no `extras`.
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

When a Haiku spawn returns `done`, read its `contract-check` in `notes.md` before anything else: a
`[ ]`, or fewer lines than the step has contract items, re-routes the step to `sonnet/high`
(`post-failure`) before review. A Haiku return of `blocked` with `need: retier` re-routes it to
`sonnet/high` as `proactive`, not `post-failure`: it is not a failure, so there is no prior pass to name; journal it as a `blocker` with `need: retier`.

### Fix rounds, pilots, visual and parallel steps

- **Fix rounds** are routed like steps, with a writer floor: never below the tier that wrote the
  code. One exception: an **exact-edit list** — at most 5 routed findings, each with `file:line` and
  a literal suggested change, none naming a class or invariant, none from the security reviewer,
  on code Haiku didn't write, with G1 and G6 holding — may go to `haiku/medium`. [judgment]
- Code Haiku wrote that gets any routed finding → `sonnet/high` (`post-failure`).
- A round-2 finding in a class already raised in round 1 → the next rung (`post-failure`,
  `review_pattern`). After round 3, SKILL.md step 9.
- **Pilots:** a mechanical step that passes the Haiku gate and whose items each have their own check pilots at `haiku/medium`. It
  continues at `haiku/medium`, in spawns of ≤ ~10 items, only if the pilot passed its gates with
  every `contract-check` line `[x]`; otherwise offer the retier to `sonnet/high` at the pilot gate. Other pilots run
  at the routed tier. [judgment]
- **Visual steps:** route by the mechanical difficulty, never Haiku. Don't raise for taste; the
  director owns it. [current rule]
- **Parallel tracks:** route each track on its own.

### Escalation ladder

`haiku/medium → sonnet/high → opus/medium → opus/high → fable/high` — at most one rung per failure
(except an Opus ability failure, which goes from `opus/medium` straight to `fable/high`; see the table),
never sideways or down for the same work (one exception: an exact-edit fix list, above).

| Failing tier | Failure | Next | Why |
|---|---|---|---|
| `haiku/medium` | any routed finding; a check still failing after its three strikes; a `[ ]` in `contract-check` | `sonnet/high` | Don't raise Haiku's effort: at `high` it skipped the same items on replays (4 medium issues, as at medium); `max` fixed that at 1.9× `sonnet/high`'s cost and 3.8× its wall time [measured] |
| `sonnet/medium` (caps, pressure, saver) | any routed finding | `sonnet/high` | escalate on the first review failure [current rule] |
| `sonnet/high` | round-1 findings | fix at `sonnet/high` | [current rule] |
| `sonnet/high` | the same class again in round 2; `stuck` where the check is right and the approach keeps failing | `opus/medium` | change model, not effort: Opus costs ~1.25–1.3× Sonnet per token here [observed]; `sonnet/xhigh` is for the no-Opus cap [judgment] |
| `opus/medium` | round-1 findings that are neither diligence nor ability failures (routine review findings) | fix at `opus/medium` | same as Sonnet's round 1 [current rule] |
| `opus/medium` | diligence: an omission, an edge case the contract named, thin tests, an unverified claim — the design is right | `opus/high` | raise effort within the model [judgment] |
| `opus/medium` | ability: the same class after the Opus pass, the wrong mechanism twice, `stuck` on a hard item | `fable/high` | tell the user first [current rule] |
| `opus/high` | any | `fable/high` | tell the user first [current rule] |
| `fable/high` | any | planner revision if the plan is the problem, else the user | no `fable/xhigh` unless the user asks [judgment] |

- **A plan defect at any rung** (the contract can't hold as written) goes to a planner revision, not
  a tier raise; an environment need (sudo, a device, a denial) goes to SKILL.md's rung ladder. [current rule]
- **Skip a rung** only when the failure reveals a signal the first routing missed: run the
  procedure again with it (a Haiku step that turned out to touch concurrency → `opus/medium`). [judgment]
- **Fable** needs a failed Opus pass on this work or a `ceiling` flag; never from Haiku or Sonnet. [current rule]
- Every `post-failure` pass gets the prior diff, notes and findings (SKILL.md).

### Cells

| Cell | Verdict | Use when | Never when | Basis |
|---|---|---|---|---|
| `haiku/low` | dominated by `haiku/medium` | — | always | [measured] the only hidden-test failure in 216 runs, a README cut short mid-rename, a self-added deadlocking test |
| `haiku/medium` | gated | the Haiku gate holds; exact-edit fix lists; mechanical pilots | the gate fails; as an escalation target | [measured] |
| `haiku/high` | dominated | — | always: 9.42 vs medium's 9.58 on admitted tasks at 1.2× the cost; on replays the same skips at 3× | [measured] |
| `haiku/xhigh` | dominated (expected) by `sonnet/high` | — | always | [judgment] lies between measured high and max |
| `haiku/max` | dominated by `sonnet/high` | — | always: no skips, but 1.2–1.9× `sonnet/high`'s cost and 3.8–5.8× its wall time | [measured] |
| `sonnet/low` | excluded | — | always: early stops and skipped verification on long agentic tasks | [judgment, reference] |
| `sonnet/medium` | caps and pressure only | a cap excludes Haiku and the step passes the gate, at 5h WARN; a user effort ceiling; 7d saver routing | otherwise: 9.00 vs 9.54 on admitted tasks, about equal on replays, for 0.74–0.83× the cost | [measured] |
| `sonnet/high` | **default** | everything the procedure doesn't route elsewhere | `flag`, `boundary`, `unknown_cause`, `refusal`; lite | [measured, observed] |
| `sonnet/xhigh` | cap only | no-Opus cap: Opus-signal steps, first escalation | Opus is allowed (`opus/medium` is the better buy) | [judgment] |
| `sonnet/max` | cap only | no-Opus cap: second escalation | Opus is allowed | [judgment] |
| `opus/low` | excluded until benchmarked | — | — | [judgment] first candidate for the next benchmark |
| `opus/medium` | signal tier, rung 3 of the ladder | Opus signals; lite; post-failure from `sonnet/high` | gate-passing or plain steps | [observed, reference] |
| `opus/high` | rung 4 | diligence failure at `opus/medium`; `boundary` with the security review declined; lite at the edge | before `opus/medium` has tried non-boundary work | [judgment, observed] |
| `opus/xhigh` | cap only | no-Fable cap: one fresh pass after `opus/high` | Fable is allowed | [judgment] |
| `opus/max` | never | — | — | [judgment] past `xhigh`, re-plan |
| `fable/low`, `fable/medium` | never (benchmark candidates) | — | — | [judgment] no measured gain over Opus, and every Fable spawn needs the user told |
| `fable/high` | ceiling rung | after a failed Opus pass on this work; a `ceiling` flag | straight from Haiku or Sonnet | [current rule] |
| `fable/xhigh` | on the user's request | the user asks after `fable/high` failed and the plan holds | otherwise | [judgment] |
| `fable/max` | never for implementers | — | — | [judgment] planner tier |

### Caps and pressure

| Situation | Routing | Basis |
|---|---|---|
| No Haiku, or a repo lesson such as "no cheaper tiers" | gate-passing steps, pilots and exact-edit fixes → `sonnet/high` (`sonnet/medium` is allowed at 5h WARN, not required) | [observed] |
| No Opus (and so no Fable unless the user says otherwise) | Opus-signal steps → `sonnet/xhigh`; ladder `haiku/medium → sonnet/high → sonnet/xhigh → sonnet/max` → the user | [judgment] |
| Sonnet only | as no Opus; gate-passing steps → `sonnet/high` (`sonnet/medium` at 5h WARN) | [measured] |
| No Fable | the ladder ends `opus/high → opus/xhigh` (one fresh pass) → the user | [judgment] |
| Effort ceiling, e.g. ≤ medium | `haiku/medium` gated, `sonnet/medium` otherwise, `opus/medium` for signals; escalate by model only | [observed] |
| 5h WARN, STALE or UNKNOWN | no proactive `opus/high`, except a `boundary` step whose security review the user declined (it keeps `opus/high`; if that can't run, pause); signal steps keep `opus/medium` (the plan earned it); Fable only with the user's OK; batch fix findings; never send a step that fails the gate to Haiku for budget — pause instead | [judgment] |
| 5h STOP | pause (SKILL.md) | [current rule] |
| 7d ≥ 90% | ask with `AskUserQuestion` (recommended option first, plus **Hold on**; recommend continue-as-routed when the remaining work is flagged or sensitive, otherwise saver routing): continue as routed; **saver routing** — unflagged, non-`sensitive` steps that fail the gate → `sonnet/medium`, the gate admits any M step, `algorithm`/`state` flags → `sonnet/high` with the sketch and escalation on the first failure, Opus kept for concurrency, `boundary`, `unknown_cause`, `refusal`; or hold until the reset | [measured for `sonnet/medium`; judgment for the rest] |
| User waiting / urgent | no Haiku; gate-passing steps → `sonnet/high`; Opus signal steps keep Opus, and say it is slower; no `opus/high` or Fable unless earned | [measured wall time; judgment] |

When a cap blocks a needed escalation, tell the user what it is costing; don't quietly break it.
Never lower a `boundary` or `sensitive` step's tier for budget or usage pressure; pause instead. When a cap removes a tier a rule requires (a `ceiling` flag with no Fable, lite mode with no Opus, the end of an effort-ceiling ladder), ask the user with `AskUserQuestion` instead of picking a substitute.

### Cost and latency

| Tier | Benchmark $/run · wall (admitted tasks; replays) | Real spawns, recorded medians | Same tokens vs Sonnet |
|---|---|---|---|
| `haiku/medium` | $0.022 · 94 s; $0.079 · 222 s | none yet | 0.22× (+ its extra turns: est. 0.3–0.6× per real-size spawn) |
| `sonnet/medium` | $0.161 · 47 s; $0.444 · 191 s | 9 runs: 34 requests, $0.91, 5.6 min | 1× (fewer tokens) |
| `sonnet/high` | $0.219 · 71 s; $0.536 · 235 s | 193 runs: 66 requests, 193K peak, $2.35, 13.6 min (p75 41) | 1× |
| `opus/medium` | — | 133 runs: 84 requests, 245K peak, $4.74, 27 min (p75 50) | ~1.3× |
| `opus/high` | — | 33 runs: 122 requests, 287K peak, $10.06 | ~1.25×; ~2× `opus/medium` per spawn |
| `fable/high` | — | 2 runs | ~2.1–2.3×; turns can run many minutes |

"Recorded" is transcript usage at list prices (`role-cost.py`'s method). 47% of implementer requests
are logged with a placeholder output count, so the dollars are lower bounds, and Opus's per-spawn
figures carry its harder steps. Minutes come from journal `return.duration_ms`.

### Tell the user

- Before any Fable spawn: why, which pass failed (or the planner's ceiling reason), and that per
  token it costs ~1.6–1.8× an Opus pass (more per spawn: its turns are longer), with turns that can run many minutes. [current rule]
- At the plan gate: the per-step tier map.
- Once, when a cap blocks an escalation or holds an Opus-signal step below Opus, with what it risks.
- At 7d ≥ 90%: the saver question above. At 5h WARN: SKILL.md's one line.
- When an Opus-signal step keeps Opus while the user is waiting: that it is slower, and why.

## Record it

Every `tier_choice` and `escalation` journal event, and the spawn brief, carries a **`tradeoff`**
line: what this tier buys and what it gives up, naming the factors that decided it. For example:
`"haiku/medium: gate holds (S, 3 files, 7/7 contract items test-checked, no prose); gives up depth on anything unlisted — any finding moves it to sonnet/high"`.
A default-tier choice can use `"default: no factor pushes up or down"`.
