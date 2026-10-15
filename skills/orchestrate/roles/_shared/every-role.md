## Rules for every role

These apply to every `orch-*` role. Where your own sections are stricter, they win.

- **Checkpoint as you go, not only at the ends.**
  - Before starting, set your `status.md` row to `in_progress` with a UTC timestamp.
  - Append a `log.md` line at each meaningful boundary: a step, a batch, a reviewed file group. A
    usage cutoff mid-run must leave a readable trail.
  - Before returning, set `done`, `blocked` (with `need: …`) or `failed`, with a note.
  - While you only wait for a message inside a loop, your row reads `waiting` (see Visual steps in
    SKILL.md); that is not a return.
- **When the orchestrator messages you to checkpoint and return**, finish the current tool call. Write
  where you are to your deliverable or `notes.md`, set your row `blocked` with the reason it gave,
  and return. Don't start anything new first.
- **Need a rule from `SKILL.md`?** It is the orchestrator's procedure (~30 KB). `grep -n` it for the
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
