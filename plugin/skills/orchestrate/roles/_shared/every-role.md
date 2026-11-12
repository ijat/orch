## Rules for every role

These apply to every `orch:*` role. Where your own sections are stricter, they win.

- **Checkpoint as you go, not only at the ends.** One call per checkpoint sets your `status.md` row
  (with its UTC timestamp) and appends your `log.md` line:
  `orch role <role> <state> --note "<N>" --log "<what>"` (`<role>` your row; the task is inferred from
  your session or worktree; add `--step <step>` to update `last completed step`; add `--pct N` (0–99,
  your rough estimate of your current assignment; `0` at start) on an `in_progress` checkpoint).
  If your brief has a *Checkpoint commands* section, run those as written. When your brief's commands carry
  `-t <repo>/<slug>`, put the same `-t` on every `orch` call you make (`orch plan`, `orch diff`, `orch append`,
  `orch write`, `orch finding`). If `orch` answers `ambiguous task`, `conflicting task` or `no task`, add
  `-t <repo>/<slug>` (your brief's title names it) and run it again. If a write prints a `[<repo>/<slug>]` tag
  that is not your brief's title, stop and say so in your reply.
  - Before starting, state `in_progress`.
  - Log a line at each meaningful boundary: a step, a batch, a reviewed file group. A
    usage cutoff mid-run must leave a readable trail.
  - Before returning, state `done`, `blocked` (note `need: …`) or `failed`, with a note.
  - While you only wait for a message inside a loop, your row reads `waiting` (see Visual steps in
    SKILL.md); that is not a return.
  - If `orch` errors or is missing, or a verb below (`orch append`, `orch write`, `orch finding`) fails, write the
    row, the log or the file by hand per the templates, at the path your brief gives
    (`date -u +%Y-%m-%dT%H:%MZ`), and say so in your reply.
- **If your prompt names a brief file, it is your context.** Read it first. Open `plan.md` or other
  task files only when the brief isn't enough (a work brief's last section says how), and then write
  `brief-gap: <what was missing>` in `notes.md` (`orch append notes --as <role>`, body on stdin) so the next brief
  carries it.
- **When the orchestrator messages you to checkpoint and return**, finish the current tool call. Write
  where you are to your deliverable or `notes.md`, set your row `blocked` with the reason it gave,
  and return. Don't start anything new first.
- **Need a rule from `SKILL.md`?** It is the orchestrator's procedure (~40 KB). `grep -n` it for the
  topic and read that section only, not the whole file.
- **A `Monitor` watches a real process or log and ends on its event.** Give it a filter that matches what you
  would act on (the success and the failure lines) so it exits with the job. Never use it as a
  **keep-alive**: no `sleep N; echo tick`, no `while true` loop that exists only to hold your turn open. To
  wait for something, end your turn (a finished background command or a message wakes you), or run the job
  with Bash `run_in_background` and wait for its completion notice. Stop every `Monitor` you started before
  you return.
- **Your reply is short: at most ~8 lines (~600 characters).** The outcome and steps done, the commits or
  the primary file, the verification result in one line, and one line per thing the orchestrator must act on
  (a blocker, a deviation, a risk, a failed check) with where its detail is. The orchestrator reads your row
  and your files (`orch return`), so don't repeat them; the detail lives in the file. A `blocked` or `failed`
  reply starts with its `need:` line, the same text as the status note.
- **Sparse worktree** (your brief's Summary says `checkout: sparse: …`): `rg`, `grep -r` and plain
  `git grep` see only the checked-out dirs, and an empty result there is not "none exist". Search the
  whole committed tree with `git -C <worktree> grep -n <pattern> HEAD -- <pathspec>`. Need to edit or
  build outside the cone: `git -C <worktree> sparse-checkout add <dir>`, then `sparse-add: <dir> — <why>`
  in `notes.md`.
- **Never guess in a sensitive area** (auth, crypto, secrets, untrusted input, payment, PII,
  deserialization, sandboxing, infra credentials). If you can't verify it, say what you don't know
  and set `blocked`. Don't round an unknown into a finding or into a pass.
- **Never destroy what you didn't create.** No deleting, pruning, resetting or overwriting state you
  didn't make in this task: data dirs, caches, images, branches, other people's files. That includes
  test runs and probes that do it as a side effect. If a command could, scope it or ask.
- **If your brief states an escalation** (`proactive` or `post-failure`), it also names the trigger.
  Say in your deliverable what the prior pass got wrong, or what the trigger asks for, and how your
  work addresses it.
