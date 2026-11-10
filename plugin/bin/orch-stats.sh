#!/usr/bin/env bash
# orch-stats.sh — aggregate orchestration journals (schema: ~/.claude/skills/orchestrate/templates/journal.md)
# Usage: orch-stats.sh [--repo NAME] [--since YYYY-MM-DD]
# Reads ~/.claude/orchestrations/*/*/journal.jsonl (override the root with ORCH_DIR). Local files only.
set -euo pipefail

root="${ORCH_DIR:-$HOME/.claude/orchestrations}"
repo=""; since=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --since) since="$2"; shift 2 ;;
    -h|--help) sed -n 2,4p "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

files=(); bad=()
while IFS= read -r -d '' f; do
  if jq -e . "$f" >/dev/null 2>&1; then files+=("$f"); else bad+=("$f"); fi
done < <(find "$root" -mindepth 3 -maxdepth 3 -name journal.jsonl -print0 2>/dev/null)

for f in ${bad[@]+"${bad[@]}"}; do echo "WARN invalid JSON, skipped: $f" >&2; done
if [ ${#files[@]} -eq 0 ]; then echo "no valid journal.jsonl files under $root"; exit 0; fi

# repo/task keys that have a retro.md (path layout: <root>/<repo>/<task>/)
retros=$(for f in "${files[@]}"; do d=$(dirname "$f"); if [ -f "$d/retro.md" ]; then printf '%s/%s\n' "$(basename "$(dirname "$d")")" "$(basename "$d")"; fi; done | jq -Rsc 'split("\n") | map(select(length > 0))')

# Normalise legacy lines: older journals use "event" instead of "kind" and omit repo/task (derive them from the path).
# Tiers: before 2026-10-08 a tier was named by its agent file; map those names to <model>/<effort> so
# tier counts stay comparable across the change. A spawn-time model override in old lines is not
# visible here (e.g. an "orch-planner" spawn that ran on sonnet), so mapped values are the file defaults.
for f in "${files[@]}"; do
  d=$(dirname "$f")
  jq -c --arg r "$(basename "$(dirname "$d")")" --arg t "$(basename "$d")" '
    def tm: {"orch-planner":"opus/high","orch-planner-opus-high":"opus/high",
             "orch-planner-max":"opus/max","orch-planner-opus-max":"opus/max",
             "orch-planner-fable":"fable/max","orch-planner-fable-max":"fable/max",
             "orch-implementer":"sonnet/high","orch-implementer-sonnet":"sonnet/high",
             "orch-implementer-expert":"opus/medium","orch-implementer-opus":"opus/medium",
             "orch-implementer-fable":"fable/high"};
    def nt: if type == "string" then (tm[.] // .) else . end;
    . + {repo: (.repo // $r), task: (.task // $t), kind: (.kind // .event)}
    | if has("tier") then .tier |= nt else . end
    | if has("from") then .from |= nt else . end
    | if has("to") then .to |= nt else . end' "$f"
done | jq -rs --arg repo "$repo" --arg since "$since" --argjson retros "$retros" '
  map(select(($repo == "" or .repo == $repo) and ($since == "" or .ts >= $since)))
  | . as $e
  | def by(k): if length == 0 then "  none" else group_by(k) | map("  \(.[0] | k // "null"): \(length)") | join("\n") end;
    def kind(k): $e | map(select(.kind == k));
    def tasks: $e | map("\(.repo)/\(.task)") | unique;
    # tasks (repo/task) whose implementer got escalated
    def escalated_tasks: kind("escalation") | map(select(.role == "implementer")) | map("\(.repo)/\(.task)") | unique;
    # only escalations forced by a failure; plan_flag/proactive ones are chosen up front, mostly on the same hard tasks that get sketches
    def reactive_tasks: kind("escalation") | map(select(.role == "implementer" and ((.trigger // "") | test("^(plan_flag|proactive)") | not))) | map("\(.repo)/\(.task)") | unique;
    def sketched_tasks: kind("sketch") | map("\(.repo)/\(.task)") | unique;
    # final mode per task: last mode_switch.to, else task_start.mode
    ($e | map(select(.kind == "task_start" or .kind == "mode_switch")) | sort_by(.ts)
      | reduce .[] as $x ({}; .["\($x.repo)/\($x.task)"] = ($x.mode // $x.to))) as $modes
    # total reported subagent tokens per task
    | ($e | map(select(.kind == "return" and .tokens != null)) | group_by("\(.repo)/\(.task)")
      | map({key: "\(.[0].repo)/\(.[0].task)", value: (map(.tokens) | add)}) | from_entries) as $tok
    | def rate(xs; ys): if (xs | length) == 0 then "n/a" else "\([xs[] | select(. as $x | ys | index($x))] | length)/\(xs | length)" end;
    def per(xs): "\(xs | length) (\(if (tasks | length) == 0 then "n/a" else ((xs | length) / (tasks | length) * 100 | round / 100) end) per task)";
  "tasks: \(tasks | length)  events: \($e | length)  ended without retro.md: \(kind("task_end") | map("\(.repo)/\(.task)") | unique | map(select(. as $t | $retros | index($t) | not)) | length)",
  "",
  "outcome (task_end):", (kind("task_end") | by(.outcome)),
  "",
  "mode (task_start):", (kind("task_start") | by(.mode)),
  "mode switches:", (kind("mode_switch") | map(.k = "\(.from)->\(.to) [\(.reason) @ \(.phase)]") | by(.k)),
  "by mode (task_end; a switched task counts as its final mode):",
  (kind("task_end") | map(. as $t | .m = ($modes["\($t.repo)/\($t.task)"] // "null")) | if length == 0 then "  none" else group_by(.m)
     | map(.[0].m as $m | "  \($m): tasks=\(length) avg_rounds=\(map(.rounds | numbers) | if length == 0 then "n/a" else (add / length * 10 | round / 10) end) avg_tokens=\(map("\(.repo)/\(.task)") as $ts | [$ts[] | $tok[.] // empty] | if length == 0 then "n/a" else (add / length | floor) end)")
     | join("\n") end),
  "",
  "tier choices (<model>/<effort>; pre-2026-10-08 names mapped):", (kind("tier_choice") | map(.k = "\(.role) -> \(.tier)") | by(.k)),
  "",
  "tier choices with a tradeoff line: \(kind("tier_choice") | map(select(.tradeoff)) | length)/\(kind("tier_choice") | length)",
  "",
  "escalations by trigger:", (kind("escalation") | map(.k = "\(.role) \(.from)->\(.to) [\(.trigger)]") | by(.k)),
  "",
  "review rounds per task:", (kind("task_end") | by(.rounds)),
  "",
  "blockers resolved at rung:", (kind("blocker") | by(.rung)),
  "",
  "gates:", (kind("gate") | map(.k = "\(.gate) \(.outcome)") | by(.k)),
  "",
  "sketches: \(kind("sketch") | length)",
  (kind("sketch") | map(.k = "followed=\(.followed) wrong=\(.sketch_wrong) escalated=\(.escalated)") | by(.k)),
  "implementer escalated — tasks with sketches: \(rate(sketched_tasks; escalated_tasks))  without: \(rate(tasks - sketched_tasks; escalated_tasks))",
  "  after a failure only — with sketches: \(rate(sketched_tasks; reactive_tasks))  without: \(rate(tasks - sketched_tasks; reactive_tasks))",
  "sketch wrong: \(kind("sketch") | map(select(.sketch_wrong == true)) | length)/\(kind("sketch") | map(select(.sketch_wrong != null)) | length) of sketches with a verdict",
  "",
  "tokens by agent (returns reporting tokens):",
  (kind("return") | map(select(.tokens != null)) | if length == 0 then "  none" else group_by(.agent)
     | map("  \(.[0].agent): n=\(length) total=\(map(.tokens) | add) avg=\((map(.tokens) | add) / length | floor)") | join("\n") end),
  "",
  "planner continuation:",
  (kind("continue_gate") as $g
    | ($e | map(select(.kind == "spawn" and .role == "implementer" and .agent == "orch-planner"))) as $cs
    | def med: sort | if length == 0 then "n/a" elif length % 2 == 1 then .[(length - 1) / 2] else ((.[length / 2 - 1] + .[length / 2]) / 2 | floor) end;
      if ($g | length) == 0 and ($cs | length) == 0 then "  none" else
        "  gates: \($g | length) (continue \($g | map(select(.decision == "continue")) | length), split \($g | map(select(.decision == "split")) | length))",
        "  reasons:",
        ($g | map(.reasons // [] | .[]) | if length == 0 then "    none" else group_by(.) | sort_by(-length) | map("    \(.[0]): \(length)") | join("\n") end),
        "  median ctx: continue \($g | map(select(.decision == "continue") | .ctx | numbers) | med)  split \($g | map(select(.decision == "split") | .ctx | numbers) | med)",
        "  continued spawns: \($cs | length)"
      end),
  "",
  "low-confidence judgements: \(kind("judgement") | map(select(.confidence == "low")) | length) of \(kind("judgement") | length)",
  "",
  "plan shape (plan_shape):", (kind("plan_shape") | map(.k = "\(.shape) overridden=\(.overridden)") | by(.k)),
  "programs (tasks with a milestone_plan): \(kind("milestone_plan") | map("\(.repo)/\(.task)") | unique | length)",
  (kind("milestone_end") | if length == 0 then empty else "  milestones ended: \(length)  by outcome:\n\(by(.outcome))" end),
  (kind("review_point") | if length == 0 then empty else "  review points: \(length)  avg span: \(map(.span | length) | add / length * 10 | round / 10)  avg rounds: \(map(.rounds | numbers) | if length == 0 then "n/a" else (add / length * 10 | round / 10) end)  late findings: \(map(.late | numbers) | add // 0)" end),
  (kind("replan") | if length == 0 then empty else "  re-plan checks: \(length)  with changes: \(map(select((.changed | length) > 0)) | length)" end),
  (kind("checker") | if length == 0 then empty else "  checker runs: \(length)  FAIL: \(map(select(.verdict == "FAIL")) | length)" end),
  (kind("plan_review") | if length == 0 then empty else "  plan reviews: \(length)  blockers: \(map(.blockers | numbers) | add // 0)  should: \(map(.should | numbers) | add // 0)" end),
  "",
  "stops and automation (counts, and per task over \(tasks | length) tasks):",
  "  plan gates: \(per(kind("gate") | map(select(.gate == "plan"))))",
  "  merge gates asked: \(per(kind("gate") | map(select(.gate == "merge"))))",
  "  checkpoints: \(kind("checkpoint") | length)  asked the user: \(per(kind("checkpoint") | map(select(.asked == true))))",
  "  blockers resolved at rung 3: \(per(kind("blocker") | map(select((.rung | tostring) == "3"))))",
  "  auto-merges: \(per(kind("auto_merge")))",
  "  security by rule: ran \(per(kind("auto_security") | map(select(.ran == true))))  skipped \(per(kind("auto_security") | map(select(.ran == false))))",
  "  assumed decisions: \(per(kind("assumed_decision")))"
'
