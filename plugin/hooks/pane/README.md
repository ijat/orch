# orch-pane

A Claude Code pane beside the chat that shows the orch orchestration tasks of the current repo: what each task does, its state, what is running now, step progress and overall progress. Click a milestone or step to see its summary.

Needs the `orch` CLI at `~/.claude/bin/orch` (the pane runs `orch status --json`). For the Index section it also runs `~/.claude/bin/orch-index status --json` (`orch-index.exe` on Windows) from [ijat/orch-index](https://github.com/ijat/orch-index), 0.2.0 or later; without it the section says so and nothing else changes.

Works on macOS, Linux and Windows. On Windows the pane runs `orch` as `python <path>` and finds `~` from `USERPROFILE` instead of `HOME`.

## Use

`/orch-pane` opens the pane. It opens by itself at session start when the folder belongs to an active task's repo or worktree. The `[this repo]` / `[all tasks]` button switches scope. The task the session itself is driving comes first under a `this session` heading, whatever repo it lives in: the pane passes its session id to `orch status --json --session`, and `orch` (a version that records the driving session) flags that task `this_session`. Its questions show like a task of this folder's, and an answer to it wakes this session. With an older `orch` (one that rejects `--session`: the pane repeats the call without it and leaves the flag out until the next session start or `/orch-pane`), or when the id cannot be read, only the folder's tasks show, as before.

Each task is a status card: the goal (click to open), facts, an Overall and a Steps bar with counts (`1/3 · 33%`), and the running Agents (three shown, then `· N more`). Below it a Milestones/Steps panel folds done and later items around the current one; click a fold row to see them all. History, Ask and More are folded tabs (click again to fold); Advanced options holds the AI summaries switch and Auto-answer and Notify.

**Needs you** lists the questions agents parked in `questions.md`, grouped, each option with a plain explanation. Pick an option or type an answer (a multi-select question shows checkboxes: tick one or more, no preselection, no typed answer, left out of Accept and Auto-answer), then Next / Skip for now / Send; drafts are kept, and Hold on pauses a question without changing anything. Accept takes the recommended option and Auto-answer does so for reversible questions (recorded as assumed). History pages and searches the answered ones. Answers go through `orch answers`. Compact, then send asks the session to compact between turns (the `compact_when_idle` tool) before it wakes on your answer.

An About section under Ask lists the version and commit of the orch skill, orch-index and this plugin, or `not installed`. The index records no commit, so it shows its version only. It is read once per session and again on `/orch-pane`.

When a running agent reports its own progress (`pct`, 0 to 99, in `orch status --json`), a bar under its name shows it: the same width as the Steps bar, in the agent's colour, animated while it runs. An agent that has not reported gets no bar.

Opening a goal, milestone or step also asks Haiku (lowest effort) for a plain-language summary of it, once per text: summaries are kept in the mod's own store across sessions. The `raw` button shows the original text.

Under Goal, a task of the session's folder has an Index line: the code index's state (`latest`, `stale · N behind`, `outdated`, `not built`, or `indexing…`), its size, and when it was last revised. It is folded; click it for the indexed commit and how far HEAD is ahead, file/symbol/reference counts, languages, parse errors, the last run (kind, time, files per second, extracted vs reused) with a run-time sparkline, and the last 7 days of queries (p50/p95 per command, empty-answer %, stale-answer %, trust-label mix). A `⚠ stale answers` badge shows when over 10% of answers came from a stale index.

The Ask box at the bottom answers questions about the shown tasks with Haiku, from their `orch status` data. It only reads; it changes nothing.
