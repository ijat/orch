<!-- A lesson file: lessons/<YYYY-MM-DD>-<slug>.md under the tasks root (global) or <repo-name>/ (repo). Write it with
     `orch lessons add`, never by hand; `orch lessons check` validates it, and LESSONS.md is generated from these files.
     Critical-only: a confident, repeatable mistake with a pattern behind it. A directive for one task goes in its plan.

     status      open | promoted | archived (promote with `orch lessons promote`)
     role        who it is for: planner | implementer | code-reviewer | security-reviewer | plan-reviewer | director | orchestrator
     scope       project | global (must match where the file sits)
     summary     the instruction itself, one line, at most 200 characters; what a brief shows
     quote       "<the user's own words>" for a user correction, else none (observed: <where you saw it>)
     review-by   YYYY-MM-DD: when `orch lessons audit` asks whether it still earns its place
     exempt      no | yes — <why it must outlive disuse> (a rare-event rule)
     lesson      the full instruction
     Other fields (task, what happened, what was actually true) are kept; one line per field. -->

# <short title>

- status: open
- role: implementer
- scope: project
- task: <task-slug> (<repo-name>)
- summary: <the instruction, in one line>
- quote: none (observed: <where>)
- review-by: <YYYY-MM-DD>
- exempt: no
- what happened: concretely
- what was actually true: the correction
- lesson: one instruction to apply next time, with the detail the summary cannot hold
