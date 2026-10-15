# Visual batch <N> — step <plan step>

<!-- One file per batch: the director writes Spec, the implementer fills Delivered, the director
     appends one Round section per review. Max 2 redo rounds per item. -->

## Spec (director)

- items: <ids>
- references: <file + glyph/screen per item, or one line if shared>
- form decisions: <per item, citing plan D-ids / design docs>
- acceptance: <checks the implementer can run without the director, then what the director judges by eye>

## Delivered (implementer, per round)

- round <R>: crops <artifacts/visual/batch-N/rR/>, gates digest <path> — PASS | FAIL

## Round <R> verdicts (director)

<!-- One line per item: accept | redo | variant (both kept, pick named) | escalate. Reason always —
     it is the audit trail for decisions the user does not gate. -->

| item | verdict | reason | crop |
|---|---|---|---|
| <id> | accept | <one line> | <path> |
| <id> | redo | <what is wrong + what to change> | <path> |

- shared parameters touched by redos: <name + batches to re-check, or "none">
- escalations: <recorded-decision conflict or uncovered form: options with crops, recommended first — or "none">
