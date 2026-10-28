# Plan: <task-slug>

- tier: <model>/<effort> (e.g. opus/high, opus/max, fable/max)
- tradeoff: <the orchestrator's one-line tier tradeoff, verbatim; for fable say proactive or post-failure>
- repo: /abs/path
- base branch: <branch>
- planned at: <UTC>
- planned against: <sha of the base branch tip the plan was written against>
- status: draft | approved | revised (rev N)

<!-- Guidance comments like this one are for you; delete them as you fill each section.
     Every section below is required. If one genuinely doesn't apply, write "None." and why. -->

## Summary

<!-- At most ~40 lines plus one line per step. This is what the user reads at the approval gate and
     what every later role reads first, so it must stand on its own. Detail belongs in the sections
     below. -->

- shape: single — <why not a roadmap, one line>
- goal: <one sentence>
- approach: <2–4 bullets>
- steps: <one line each — `N. <title> — <what it achieves> (S|M|L)`>
- decisions needing the user: <D-ids from ## Decisions, or "none">
- needs from user: <sudo, exclusive VM/device, credentials, commands auto mode may deny — or "none">
- files: <count>; sensitive areas: <list from SKILL.md step 5, or "none">
- implementer tier: <model>/<effort> (default sonnet/high) — <why>
- continue: steps <ids> | no — <why>   <!-- steps you would implement yourself: TIERS.md § Planner continuation; the orchestrator decides -->
- parallelization: sequential | <N> disjoint tracks
- sketched steps: <step numbers, or "none">
- guided steps: <step numbers with a guide (routed haiku/high), or "none">
- visual steps: <step numbers run with a director, or "none">
- top risks: <at most 3>

## Goal

<!-- What the user asked for, one short paragraph, in their terms. -->

## Findings

<!-- What you verified by reading code, docs, or running things. Cite absolute paths with :line
     where a location matters. Cite, don't paste: quote only the lines a step depends on. -->

### Verified

### Assumptions (not verified)

<!-- Each with why it couldn't be checked. Never assert behaviour you did not read. -->

## Decisions

<!-- One entry per design choice with more than one defensible answer. -->

- **D1 <title>** — <choice>. Rejected: <alternative> (<why>). Status: decided | needs user (reversible: yes|no)

## Approach

<!-- The design as a whole and why it holds together. Close calls: say so. -->

## Steps

### Step 1 — <title>

- size: S | M | L  <!-- L = expect well over ~40 tool calls -->
- depends: <step ids this step needs finished first, e.g. 1, 2 — or none>
- files: <absolute paths>
- context: <optional; file — purpose; symbols@line, so the brief can say what to read first>
- do: <what changes>
- contract: <BINDING — the interfaces/signatures, invariants, edge cases, and specific test cases
  this step must satisfy. Breaking one means the implementer stops and reports.>
- sketch: sketches/step-1.md | none
- guide: guides/step-1.md | none  <!-- the Haiku route: TIERS.md § Implementer routing, template guide.md -->
- pilot: <first ~5 items run alone to measure cost before the rest> | none  <!-- steps with ~20+ similar items -->
- visual: <what the director judges by eye, and the reference> | none  <!-- only where acceptance needs eyes, not a number -->
- done when: <how the implementer knows it worked>

<!-- Write a sketch (template: sketch.md) only for a step where a first attempt at the implementer's
     tier is likely to go wrong: an algorithm core, a concurrency protocol, a delicate state change.
     Most steps get none. A sketch is non-binding; the contract is what gets enforced. -->

## Files and areas touched

<!-- Flat list of every path and subsystem reached, including ones touched lightly. The
     orchestrator matches this against the sensitive-area list; an omission here is how a
     security review gets silently skipped. -->

## Parallelization

<!-- Exactly one of: "Sequential." (the default) or "Parallelizable:" followed by named, disjoint
     file sets, one per track — only if no two tracks share a file and none needs another's output. -->

## Verification

### Per step (targeted)

<!-- Per step: the exact command(s) running only the tests that cover the files that step touches
     or adds. Never the full suite here. Flaky-prone (async/timer/browser) tests added or touched:
     ≥ 5 consecutive clean runs. -->

### Final gate (once, before review)

<!-- Exact full-suite, lint and build commands, run once after the last step via `orch-executor` (one
     clean run), plus any manual check automation can't cover. -->

## Risks and unknowns

<!-- What could invalidate this plan, and what the implementer must stop and report on rather
     than improvise around. -->

<!-- Revisions: never edit the sections above after approval. Append below, one per revision:

## Revision N — <UTC date>
- trigger: implementer blocker | user feedback at the gate | review
- what broke or changed: ...
- changes: which steps/decisions are superseded, and their replacements
-->
