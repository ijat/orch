# Security review: <task-slug>

- round: 1
- scope: full diff | files: <paths>
- reviewed at: <UTC>
- verdict: pass | findings
- areas checked: <the sensitive areas that applied>
- ledger: read <path> | none given

## Findings

### S1 — <one-line summary>
- severity: critical | high | medium | low
- file: /abs/path:LINE
- fingerprint: `severity | path | short-slug`
- attack path: who the attacker is, what they control, what they achieve.
- impact: what an attacker gets.
- suggested fix: the smallest change that closes it.
- resolution: <left empty — the implementer fills this in>

<!-- Written with `orch write review --security` (text on stdin; ids continue from the brief's next id), by hand only when
     that fails; resolutions go in with `orch finding resolve <id>`. Numbering (S1, S2, …) stays stable across rounds. On a re-review, append "## Round N" below
     with each earlier finding marked resolved | still open | disputed — <reasoning>. -->
