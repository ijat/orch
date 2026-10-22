## Rules for every role

These apply to every `orch-*` role. Where your own sections are stricter, they win.

- **Checkpoint as you go, not only at the ends.** One call per checkpoint sets your `status.md` row
  (with its UTC timestamp) and appends your `log.md` line:
  `~/.claude/bin/orch -t <task> role <role> <state> --note "<N>" --log "<what>"` (`<task>` is the task
  dir, `<role>` your row; add `--step <step>` to update `last completed step`).
  If your brief has a *Checkpoint commands* section, run those as written.
  - Before starting, state `in_progress`.
  - Log a line at each meaningful boundary: a step, a batch, a reviewed file group. A
    usage cutoff mid-run must leave a readable trail.
  - Before returning, state `done`, `blocked` (note `need: …`) or `failed`, with a note.
  - While you only wait for a message inside a loop, your row reads `waiting` (see Visual steps in
    SKILL.md); that is not a return.
  - If `orch` errors or is missing, edit the row and the log by hand per the templates
    (`date -u +%Y-%m-%dT%H:%MZ`) and say so in your reply.
- **If your prompt names a brief file, it is your context.** Read it first. Open `plan.md` or other
  task files only when the brief isn't enough (a work brief's last section says how), and then write
  `brief-gap: <what was missing>` in `notes.md` so the next brief carries it.
- **When the orchestrator messages you to checkpoint and return**, finish the current tool call. Write
  where you are to your deliverable or `notes.md`, set your row `blocked` with the reason it gave,
  and return. Don't start anything new first.
- **Need a rule from `SKILL.md`?** It is the orchestrator's procedure (~40 KB). `grep -n` it for the
  topic and read that section only, not the whole file.
- **Your reply is decision-grade:** a short summary, the primary file path, blockers and risks,
  verification results, deviations. The detail lives in the file.
- **Never guess in a sensitive area** (auth, crypto, secrets, untrusted input, payment, PII,
  deserialization, sandboxing, infra credentials). If you can't verify it, say what you don't know
  and set `blocked`. Don't round an unknown into a finding or into a pass.
- **Never destroy what you didn't create.** No deleting, pruning, resetting or overwriting state you
  didn't make in this task: data dirs, caches, images, branches, other people's files. That includes
  test runs and probes that do it as a side effect. If a command could, scope it or ask.
- **If your brief states an escalation** (`proactive` or `post-failure`), it also names the trigger.
  Say in your deliverable what the prior pass got wrong, or what the trigger asks for, and how your
  work addresses it.
