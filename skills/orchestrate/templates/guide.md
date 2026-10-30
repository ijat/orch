# Guide: <task> — step <N>

<!-- Written by the planner for a step it routes to Haiku because the repo already has a pattern the
     step can follow. Advice, not contract: the plan's contract is binding. ≤ ~500 words. Pointers
     and names only — no code block longer than one line. `orch plan-check` checks that every path
     cited in backticks exists in the repo (mark a file the step creates with "(new" on that line). -->

## Approach
- <part of the change> → `<path>`: model it on `<existing path>` `<symbol>` (<what to copy from it>)

## Checklist
- [ ] <every contract item and brief requirement, including the easy-to-miss ones: help/usage text,
      docs, goldens, re-exports, tests the step asks for>

## Verify
- `<exact command>` → <what passing looks like>

## Do / Don't
- Do <repo-specific pitfall to avoid, e.g. a lint the repo enforces, an error variant to use>
- Don't <scope the step must not touch>
