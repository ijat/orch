# Questions: <task-slug>

<!-- Written by `orch ask`, resolved by `orch answer`; read by `orch checkpoint`, `orch show`, `orch resume`
     and every brief. One block per question, ids are never reused:

     ## Q<n> — <question text, one line>
     - status: open | answered | withdrawn | assumed
     - asked by: <role> at <UTC>
     - blocks: <step ids, comma separated> | all | none
     - reversible: yes | no | unknown
     - topic: <short title; default: the first 60 characters of the text>
     - group: <optional name that batches related questions>
     - options: A (recommended) :: what it does | B :: what it does | C !! why it cannot be explained
     - answer: <text> (<UTC>)

     Each option is `Label :: explanation` or `Label !! reason` (3+ words, never filler); a recommended option
     and every option of a `reversible: no` question are explained (`::`), never given a reason. Old files with
     bare options still read.

     `assumed` = a reversible decision taken without asking (`--assume` on `orch ask`); it is journaled
     (`assumed_decision`) and listed in the final digest. Edit through the CLI; fallback by hand. -->
