# Choosing model × effort

Read before every planner/implementer tier choice and before overriding any other role's default.
The ladders in `SKILL.md` are the normal path; this file is how to leave them, or decide where on
them to start, with the trade-offs said out loud.

## The models (facts from the bundled `claude-api` reference, cached 2026-10-06)

Which model each alias resolves to is read from real transcripts (`~/.claude/bin/role-cost.py`), not assumed.
Keep the aliases in the agent files and spawn parameters (never pinned IDs) so each role
follows the newest model. Aliases depend on the Claude Code version: `haiku` → Haiku 5.5 needs
v2.1.293+ (confirmed on 2.1.294, 2026-10-08), and sessions on older versions got Haiku 4.5.

| alias → model | `haiku` → Haiku 5.5 | `sonnet` → Sonnet 5.5 | `opus` → Opus 5.5 | `fable` → Fable 5.1 |
|---|---|---|---|---|
| API price in / out per 1M | $0.10 / $0.50 (≤100K prompt; $0.50 / $2.50 beyond) | $2 / $10 | $4 / $20 | $10 / $50 |
| Output cost vs Sonnet | 0.05× | 1× | 2× | 5× |
| Context / max output | 1M / 128K | 1M / 128K | 1M / 128K | 1M / 128K |
| Effort levels | low–max | low–max | low–max | low–max |
| API default effort | medium | high | **medium** | — |
| Notes | refusals have no server-side fallback | everyday coding/agent work; refuses in 5 categories (cyber, bio, frontier_llm, reasoning_extraction, general_harms) | the default model; medium ≈ Opus 5 at high on multistep coding | most capable; single turns on hard tasks can run **many minutes** |

**Cost here is usage-limit headroom, not dollars:** this account runs on the 5h/7d limits
(`usage-check.sh`). The API prices are the best available proxy for how hard each model draws on
them. Not measured locally: wall-clock speed per model. Treat "Haiku/Sonnet are faster" as
plausible, not verified, until measured.

## What effort trades

Effort sets thinking depth and total token spend within one model. Lower effort means fewer and
more consolidated tool calls, less preamble, terser output. The reference's guidance:
- `low`: simple, well-specified, mechanical work.
- `medium`: the cost-saving step down where quality holds.
- `high`: the floor for anything intelligence-sensitive.
- `xhigh`: the usual best for coding and agentic work on recent models.
- `max`: only when correctness matters more than cost.

Two consequences:
- **Try a stronger model at lower effort before a weaker model at higher effort.** On the newest
  models, low effort often matches the previous generation at high.
- **Judge cost per finished task, not per spawn.** A cheap pass that fails review and needs a second
  round has already cost more than one stronger pass.

## The factors

Weigh these for every non-default choice:

- **User caps come first.** If the user limits tiers ("no Opus", "Sonnet only", "no Fable this
  week"), that is a hard ceiling above every factor below. Log it once as an `override` journal
  event. Then escalate *within* the cap by raising effort: e.g. with Opus banned, the implementer
  ladder becomes `sonnet/high` → `sonnet/xhigh` → `sonnet/max`, and planners and reviewers run at
  `sonnet/xhigh` or above. If the cap stops a needed escalation (the work keeps failing at the
  capped ceiling), tell the user what the cap is costing; don't quietly break it.

- **Quality:** how bad is a subtle mistake here, and would review catch it? Sensitive areas
  (auth, crypto, secrets, untrusted input, PII, deserialization, sandboxing, infra credentials),
  concurrency and irreversible steps push up. Well-specified steps backed by tests push down.
- **Ability:** can this model do the task at all? Haiku is for lookups, running commands and
  purely mechanical edits, not design. Sonnet handles most implementation. Opus handles ambiguous
  design and hard debugging. Fable is for the cases Opus already failed or is likely to. Sonnet handles most implementation. Opus handles ambiguous design and hard debugging.
  Fable is for the cases Opus already failed or is likely to.
- **Performance / urgency:** is the user waiting? Fable turns can run for minutes, and `max` effort
  is slower than `high`. If the user said it's urgent, prefer a faster tier and say what that risks.
- **Cost:** check `usage-check.sh` first. At `WARN` (≥90%) stay on or below the default tier
  unless a higher one is already earned. Fable always needs the user told first (`SKILL.md`).
- **Limitations:** safety classifiers can refuse security-adjacent work (Sonnet 5.5 has the widest
  set of categories), and Haiku has no fallback when it refuses. Opus 5.5 refuses in fewer
  categories than Sonnet 5.5, so it is the safer choice for security tooling. Context is 1M on every model, so it never
  decides the choice.
- **Escalation history:** a `post-failure` escalation moves up a rung, never sideways. Never jump
  from the default straight to Fable (`SKILL.md`).

## Typical choices

| Situation | Tier | Gives up |
|---|---|---|
| Purely mechanical steps (renames, wiring, boilerplate) with tests | implementer `haiku/high` or `sonnet/medium` | depth; escalate on the first review failure |
| Ordinary implementation | implementer `sonnet/high` (default) | — |
| Concurrency, tricky algorithm, sensitive area | implementer `opus/medium`, or `opus/high` if sensitive | 2× Sonnet price; measured ≈ 3× `sonnet/high` per run (2026-10-08…15, 62 vs 43 runs; harder steps inflate it) |
| Clear task, well-trodden design | planner `opus/high` (default) | — |
| Small, unambiguous task | planner `opus/medium` or `sonnet/high` | fewer alternatives explored; not for anything sensitive |
| Ambiguous, cross-cutting, high blast radius | planner `opus/max` | slower, more tokens |
| Critical / irreversible / failed at `opus/max` | planner `fable/max` | 2.5× Opus cost, minutes-long turns; tell the user |
| Plan review (roadmap or `opus/max` plan) | plan reviewer `sonnet/high`; `opus/medium` if any milestone is sensitive | depth — it checks four questions, it doesn't re-plan |
| Review of a large or sensitive diff | reviewer `opus/xhigh` or `fable/high` | cost; tell the user if Fable |

## Record it

Every `tier_choice` and `escalation` journal event, and the spawn brief, carries a **`tradeoff`**
line: what this tier buys and what it gives up, naming the factors that decided it. For example:
`"sonnet/medium: steps are mechanical with tests; gives up depth — escalate on first failed review"`.
A default-tier choice can use `"default: no factor pushes up or down"`.
