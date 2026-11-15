---
name: memory
description: Go through Claude's auto-memory for the current project with the user, move what orch should keep into lessons, offer to clear the rest and to switch auto-memory off. Use when the user says "/orch:memory", "audit my memory", "move my memory to orch", "clean up auto-memory", "turn off auto-memory", or presses "Move memory to orch lessons" in the pane. Works in any session, orchestration or not.
---

# orch:memory

You are the **session** that audits Claude's auto-memory (`~/.claude/projects/<project>/memory/`) with the user.
The CLI lists and flags what a program can check; **you** judge, ask, and run the changes the user says yes to.
Nothing is changed, moved, cleared or switched off without the user's yes, also in "auto" mode (auto only
pre-sorts the table). You never edit `~/.claude/settings.json` yourself.

Why: auto-memory decides nothing in an orch task (SKILL.md, "Memory has no say"). What still matters is
moved into a lesson, which has a quote, a scope and a review date, is shown to every brief, and is audited.

## Steps

1. **Inventory.** Run `orch memory audit --json` (default project: the current directory). If it says "no Claude
   memory for this project", say so and go to step 6. Each entry has `name`, `type`, `description`, `size`, dates,
   `origin_transcript` (`exists` or not), `missing` (required fields) and `flags` (`stale-name`, `orch-policy`).

2. **Sort every file** into exactly one row of a table:
   - **flagged**: the program flagged it (stale names, missing fields);
   - **conflict**: contradicts a newer rule in a CLAUDE.md or in a lesson (read the project's and the global
     `LESSONS.md`); the newer one wins;
   - **superseded**: a later memory or lesson says the same or the opposite;
   - **orch policy**: names a model, tier, escalation or orch role (the `orch-policy` flag; confirm by reading);
   - **preference**: the user's own taste or habit, not orch policy;
   - **other**: facts about the project that Claude may keep loading.
   `MEMORY.md` is the index: list it, do not sort it.

3. **Show the table and ask** before changing anything: one row per file with its sort, the description, the date
   and what you would do. A **preference** is always asked, with its quote (the user's recorded words), date and
   project: *keep*, *limit to this project*, or *retire*. Ask in one `AskUserQuestion` per batch of rows, not
   one question per file when the answer is likely the same.

4. **Move what orch should keep.** Orch policy and anything else worth keeping for orch goes to a lesson:
   `orch lessons add --scope project|global [--repo <repo folder>] --role <role> --title "<title>" --summary "<the instruction, one
   line, at most 200 characters>" --lesson "<full instruction>" --quote "<quote>" --review-by <YYYY-MM-DD>
   [--exempt "<why it never expires>"] [--task <task>] [--what-happened "<...>"] [--actually-true "<...>"]`
   (`--repo` is the repo folder under the orch root; outside a task, project scope needs it).
   The quote is the user's recorded words from the memory (or asked now); if the lesson did not come from the
   user, `none (observed: <where seen>)`. A memory you keep for Claude gets the four required fields added to its
   frontmatter (`quote`, `scope`, `review-by`, `exempt`) so the next audit does not flag it. Never put a model, a
   tier or an escalation into a kept memory: that is a lesson or nothing.

5. **Offer to clear** this project's memory: `orch memory clear --project <path> --reason "<why>"`. It copies
   the folder to a backup, checks the copy, then empties the folder, and prints the one command that restores it
   (`orch memory restore <name>`); tell the user that line. Per project, on the user's yes only.

6. **Offer auto-memory off for this project**, every time this skill runs and the project's memory still loads
   (step 7 shows it): `orch memory auto-off --project <path>`, after their yes. **Global**: offer it once, after
   their memories are moved, as a command for the user to run (`orch memory auto-off --global`); the pane's
   "Auto-memory off for this project" row is not that switch (it changes this project only); never run it yourself. Record that it was offered:
   `orch setting set memory_global_offered yes` (read it first with `orch setting get memory_global_offered`;
   when it is `yes`, do not offer again unless the user asks).

7. **Status.** Run `orch memory status` and say what it shows: the value in each settings file, the environment
   variable, and the live check (`verified off`, `still loading`, `not yet verified`). A change takes effect for
   a session that starts after it; `not yet verified` after a switch is normal until then.

8. **`--all`** (on request only): `orch memory audit --all` lists the projects with a count first. Work one
   project at a time, each with its own table and its own confirmations; never clear or switch off a project
   the user did not name.

## Rules

- A memory is a claim, not a record: check a name, a path or a rule it cites before you move it.
- Backups: `clear` and `restore` print what they did; quote it. Nothing is deleted without a backup.
- Settings files may hold secrets: use only `orch memory ...` to change them and never print a settings file.
- Report at the end: files sorted, lessons added (ids), cleared or not (backup name), switch state.
