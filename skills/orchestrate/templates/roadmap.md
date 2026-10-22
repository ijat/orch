# Roadmap: <program-slug>

- shape: roadmap — <which criteria from the planner's "Choose the shape" hit, one line>
- tier: <model>/<effort>
- revision: 0

## Summary

<!-- What the user reads at the roadmap gate; must stand alone in about 40 lines plus the table.
     Detail goes in the sections below or in each milestone's own plan.md later. -->

- goal: <one sentence>
- approach: <2–4 bullets>
- decisions needing the user: <D-ids, or "none">
- needs from user: <sudo, exclusive VM/device, credentials, commands auto mode may deny — or "none">
- milestones: <N>; review points: <M-ids>; merge points: <M-ids>; user stops: <M-ids>
- budget: <sum of sizes>; fits current 5h/7d headroom: yes | no — <why> (orchestrator fills at the gate)
- top risks: <at most 3>

## Milestones

<!-- One row per milestone, in run order. Parsed by ~/.claude/bin/orch-program-check.py, so keep
     the header and these value forms exactly:
     M          1, 2, 3 … (run order)
     depends on - | 1 | 1,3
     mode       standard | lite      (lite only for milestones that meet SKILL.md's lite rules)
     tiers      free text, e.g. "opus/high → sonnet/high" or "lite opus/medium"
     sensitive  - | <areas from SKILL.md step 5>
     review     - | skip | code | code+sec  (a review point covers every milestone since the previous one;
                skip = merge without review on purpose — only where a review would find nothing that
                matters (throwaway mockup/prototype, docs-only, output already gated by checks); give
                the reason in the goal cell; never on a sensitive row; the user approves it at the gate)
     merge      - | yes              (a merge point must be a review point or `skip`)
     you        - | <why the user is needed: mock, eye-check, sudo, decision D-n; name `merge` only when
                the user wants to approve that merge — `orch merge` refuses where this cell names it>
     size       S | M | L            (L = several days of agent work or ≥ ~10 steps)
     confidence high | med | low      (low needs a probe in "Premises and probes") -->

| M | goal | depends on | mode | tiers | sensitive | review | merge | you | size | confidence |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | <goal> | - | standard | opus/high → sonnet/high | - | - | - | - | M | high |

## Contracts between milestones

<!-- Interfaces and invariants a later milestone builds on. One line each, in this form (parsed):
     - C1 (M2 → M4, M5): <the contract>
     The producer's code must be reviewed before the first consumer starts. -->

## Premises and probes

<!-- What each milestone assumes about the platform, tools or data. For each: probed now (result),
     probed as that milestone's step 0 (hard stop), or verified by reading (path:line). -->

## Decisions

<!-- As plan.md: - **D1 <title>** — <choice>. Rejected: <alternative> (<why>). Status: decided | needs user (reversible: yes|no) -->

## Risks and unknowns

<!-- Include the justification for any checker WARN you chose to keep. -->
