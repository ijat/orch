# `journal.jsonl` — schema

One JSON object per line, append-only, **written only by the orchestrator**. It records what the
workflow decided and why, in a form `~/.claude/bin/orch-stats.sh` can aggregate across tasks.
`log.md` stays the human-readable narrative; this is for analysis. The append method is
`orch journal <kind> k=v k:=json [--set F=V] [--log TEXT]` (it validates, fills `ts`, `repo`, `task`,
and for programs `milestone`; `review_round` and `final_gate` take `sha` as below). Hand append with the
heredoc at the end is the fallback.

Every line has `ts` (UTC, `date -u +%Y-%m-%dT%H:%MZ`), `repo`, `task`, `kind`. Kinds marked
**choice** also require `reason` (one sentence, the real one) and `alternatives` (array of what
else was considered; may be `[]`). Unknown values are `null` — never omitted, never guessed.

| kind | when | extra fields |
|---|---|---|
| `task_start` | task dir created | `mode` (standard/lite/interactive), `request` (≤ 1 line) |
| `tier_choice` **choice** | before each planner/implementer/director spawn, and any non-default reviewer | `role`, `tier` (`<model>/<effort>`), `tradeoff` (≤ 1 line, see `TIERS.md`) |
| `spawn` | each subagent spawn | `role`, `agent`, `tier`, `steps` (array or null), `usage_5h`, `usage_7d` |
| `return` | each subagent returns | `role`, `agent`, `outcome` (done/blocked/failed/stopped), `tokens`, `tool_uses`, `duration_ms` (from the completion notice if it reports them, else null) |
| `escalation` **choice** | a tier is raised | `role`, `from`, `to`, `trigger` (plan_flag/plan_shape/review_pattern/prior_tier_failed/blocker/user), `tradeoff` |
| `blocker` | a role returns blocked | `role`, `need` (≤ 1 line), `rung` (1/2/3 — where it got resolved), `resolved_by` |
| `security_offer` | old lines; new tasks only when `policy.md` says `security: ask` and the user was asked | `areas` (array), `answer` (accepted/declined/not_offered), `late` (bool) |
| `review_round` | each reviewer return | `round`, `reviewer`, `blockers`, `should_fix`, `nits`, `verdict`, `sha` |
| `gate` | plan or merge approval | `gate` (plan/roadmap/merge/pilot), `outcome` (approved/revised/declined) |
| `plan_revision` | a revision appended | `n`, `trigger` (blocker/user/review) |
| `sketch` | a sketched step finishes | `step`, `followed` (full/partial/no), `sketch_wrong` (bool), `escalated` (bool) |
| `pause` / `resume` | usage pause and resume | `usage_5h` |
| `checkin` | each check-in on a silent role (dispatch step 5) | `role`, `silent_min`, `verdict` (ok/looping), `action` (continue/messaged/stopped) |
| `pilot` | a pilot batch returns | `step`, `items`, `tokens`, `rounds`, `est_rest_tokens`, `answer` (continue/retier/hold) |
| `override` | the user overrides a default | `what` |
| `direct_edit` | direct-edit allowance used | `what` |
| `lesson` | LESSONS entry written or promoted | `file`, `title`, `promoted_to` (or null) |
| `mode_switch` | lite or interactive task moved to standard | `from`, `to`, `reason` (scope_exceeded/ineligible/review_rounds/user), `phase` (plan/implement/review) |
| `judgement` **choice** | any other non-obvious decision you made | `decision`, `confidence` (low/medium/high) |
| `task_end` | task ends, any outcome | `outcome` (merged/declined/failed_merge/abandoned/handed_to_user), `rounds` |
| `plan_shape` **choice** | every planner return | `shape` (single/roadmap), `criteria` (array of the planner's criterion numbers hit), `tier`, `overridden` (bool) |
| `checker` | each `orch-program-check.py` run | `fails`, `warns`, `verdict` (OK/FAIL) |
| `plan_review` | each plan-reviewer return | `round`, `blockers`, `should`, `notes`, `verdict` |
| `milestone_plan` | roadmap approved, and after each approved revision | `revision`, `rows` (array of objects, one per table row, keys as the table header) |
| `milestone_start` / `milestone_end` | each milestone | `milestone`; end adds `outcome` (done/blocked/abandoned), `tiers` (array actually used), `rounds` |
| `review_point` | each review point passes or stops | `milestone`, `span` (array of milestones), `rounds`, `blockers`, `should_fix`, `nits`, `late` (findings caused in a milestone before the span's first) |
| `merge_point` | each merge to base | `milestone`, `sha`, `backmerge` (the back-merge commit on the task branch, or null), `tree_equal` (bool; null when there was no back-merge) |
| `replan` | each re-plan check | `milestone`, `changed` (array of row numbers, `[]` if none), `why` |
| `checkpoint` | a parked-question checkpoint | `questions` (open count), `asked` (bool) |
| `auto_merge` | `orch merge` merged the task branch | `sha`, `base`, `branch`, `revert` (the command that undoes it) |
| `auto_security` | every other security run or skip (the step 5 rule, a late finding, a user request, policy `always`) | `areas` (array), `ran` (bool), `rule` (step5/late/request/policy) |
| `assumed_decision` | a reversible question was assumed instead of asked | `decision`, `reversible` (bool), `role`, `question` |
| `final_gate` | the final-gate commands ran | `verdict`, `sha` (commit the gate ran on), `command` |
| `continue_gate` | the first implementer spawn after plan approval (`orch continue-gate`) | `decision` (continue/split), `agent_id`, `ctx`, `ctx_src` (transcript/notice/none), `planner_tier`, `routed` (array of `"<step>=<tier>"`), `steps`, `reasons` (array of failed condition codes), `plan_says` |

`review_round` counts are the findings still open **after** the round; `verdict` is `pass` only at 0 blockers and 0 should-fix (security: and 0 nits).

Types — `orch journal` coerces and checks these (`k=v` is coerced; `k:=json` must match or be `null`).
A field not listed is a string, or free JSON when written with `:=`:

- integer: `review_round.{round,blockers,should_fix,nits}`, `plan_review.{round,blockers,should,notes}`, `return.{tokens,tool_uses,duration_ms}`, `spawn.{usage_5h,usage_7d}`, `pause.usage_5h`, `resume.usage_5h`, `blocker.rung`, `task_end.rounds`, `checker.{fails,warns}`, `checkin.silent_min`, `pilot.{items,tokens,rounds,est_rest_tokens}`, `plan_revision.n`, `milestone_plan.revision`, `milestone_end.rounds`, `review_point.{rounds,blockers,should_fix,nits,late}`, `checkpoint.questions`, `continue_gate.ctx`, `*.milestone`
- boolean: `security_offer.late`, `sketch.{sketch_wrong,escalated}`, `plan_shape.overridden`, `checkpoint.asked`, `auto_security.ran`, `assumed_decision.reversible`, `merge_point.tree_equal`
- array: `*.alternatives`, `spawn.steps`, `security_offer.areas`, `auto_security.areas`, `plan_shape.criteria`, `replan.changed`, `review_point.span`, `milestone_end.tiers`, `milestone_plan.rows`, `continue_gate.{steps,routed,reasons}`

In a program, every other line also carries `milestone` (number, or null before the first one).

Example:

```json
{"ts":"2026-09-26T10:02Z","repo":"myapp","task":"add-login","kind":"tier_choice","role":"implementer","agent":"orch-implementer","tier":"sonnet/high","reason":"plan flags no concurrency or algorithmic risk; steps are UI wiring","alternatives":["opus/medium"],"tradeoff":"default: no factor pushes up or down"}
```

Fallback — when `orch` is unavailable, append through a quoted heredoc (so the shell can't expand
anything inside the JSON), then validate the whole file (a hand-appended `review_round` line only passes
`orch merge` if the round's review brief exists with the same sha):

```bash
cat >> "$d/journal.jsonl" <<'JSON'
{"ts":"…","repo":"…","task":"…","kind":"…"}
JSON
jq -c . "$d/journal.jsonl" >/dev/null || echo "journal.jsonl invalid — fix it before continuing"
```
