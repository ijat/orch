# Code review: <task-slug>

- round: 1
- scope: full diff | files: <paths>
- reviewed at: <UTC>
- verdict: pass | findings
- checked: <what you actually ran and read — required even on a pass>

## Findings

<!-- Severity is what the defect is, never whether rating it so would start a round:
- blocker: must not ship. A wrong result, data loss, a security hole, or a broken contract on a normal path.
- should-fix: a real defect that can ship for now: an edge case, a missing test for new behaviour, text that misleads.
- nit: no behaviour change: style, naming, a stale comment.
-->

### F1 — <one-line summary>
- severity: blocker | should-fix | nit
- origin: introduced | pre-existing — <blame evidence for pre-existing: commit or "on base">
- file: /abs/path:LINE
- what: what is wrong, concretely.
- why it matters: the consequence, not the principle.
- suggested fix: the smallest change that resolves it.
- resolution: <left empty — the implementer fills this in>

<!-- Numbering (F1, F2, …) stays stable across rounds. On a re-review, append a section below;
     never overwrite an earlier round:

## Round N
- reviewed at / scope / verdict: ...
- F1: resolved | still open | disputed — <implementer's reasoning and your response>
- new findings continue the numbering (F7, F8, …) in the same shape as above.
- a finding marked resolved, or disputed and then ruled on, is closed: never re-raise it or a
  restatement of it under a new number.

A fix-batch check (a brief headed "fix-batch check") is not a round. Append instead, headed
"## Check of round N" for the first check and "## Check K of round N" for check K (up to 3 per round):

## Check of round N
- reviewed at / range / verdict: ... (only the fix commits)
- F1: fixed | not fixed — <evidence>
- new findings continue the numbering in the same shape as above. Severity is what the defect is:
  never rate by whether it would start a cycle or a round (a blocker or should-fix left open starts the
  next fix-and-check cycle; after a blocker, a fresh round follows the passed check; a nit goes to the
  digest).
-->
