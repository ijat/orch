## First command: every request, structural or not, before anything else

A repository may carry an `orch-index` code index (C/C++, Rust, JS/TS, C#). Run this first, whatever
the question (`D` = the repository the caller names, else `.`):

    D=.; M="$(git -C $D rev-parse --path-format=absolute --git-common-dir)/orch-index/indexing"
    P=$(sed -n 's/^pid=\([0-9]*\).*/\1/p' "$M" 2>/dev/null)
    if [ ! -f "$M" ]; then echo marker: none; elif [ -n "$P" ] && kill -0 "$P" 2>/dev/null && find "$M" -mmin -30 | grep -q .; then echo marker: live; else echo marker: stale; fi
    "${ORCH_INDEX_BIN:-orch-index}" stats --repo $D </dev/null 2>&1 | head -4; git -C $D rev-parse HEAD

`marker: live` = a rebuild is running: re-run the marker lines every `sleep 15`, at most 5 minutes; still
live → reply `index busy`, use scoped search. `marker: stale` (dead pid or over 30 min): ignore it,
say so in one clause. Then decide by `commit` against HEAD, never by a query's count-line note (it
prints `index at <sha>, HEAD is <sha>` whenever they differ):

| State | When | Do |
|---|---|---|
| Current | `commit` = HEAD | answer from the index; if your first query's count line says `index built by an older orch-index` it is Stale (reason `built by an older orch-index`) |
| Behind | `commit` ≠ HEAD and `git -C $D merge-base --is-ancestor <sha> HEAD` succeeds | answer from the index plus a **mandatory delta search**: files from `git -C $D diff --name-only --no-renames <sha>` and `git -C $D ls-files --others --exclude-standard`; for those files use scoped-search results, not index rows, and drop rows of files that no longer exist. Start the reply `index at <sha> + delta search`; no refresh hint. (An older extractor is not visible here; it shows once `commit` = HEAD.) |
| Stale | `commit` not an ancestor of HEAD (reason `index from another branch`), or older extractor | first reply line `index unavailable: stale (<reason>) (refresh: orch-graph)`; omit the hint in a linked worktree (`--git-dir` ≠ `--git-common-dir`; the index is shared and a rebuild there would stale the main checkout); then scoped search |
| Unavailable | binary not found, command fails, or `commit (none)` | first reply line `index unavailable: <reason>` (no hint; one clause if the caller did not ask about the index); then scoped search |

Scoped search = `git grep` / `rg`, always with a path and `</dev/null`. Never run `orch-index index` or
`orch-graph`: a stale or missing index is reported, not repaired.

Queries (Current or Behind). Run each as `"${ORCH_INDEX_BIN:-orch-index}" <query>`, the same binary as
`stats`; a bare `orch-index` is not on `PATH` when only `$ORCH_INDEX_BIN` is set. Below, `orch-index`
stands for that:

    orch-index def NAME [--kind class,struct,function,method,macro,...]  # NAME may be Class::Method
    orch-index callers NAME | refs NAME [--kind call|type|use] | includers FILE | importers MODULE
    orch-index overrides METHOD [--sig "(T*)"] [--base CLASS]
    common: --path PREFIX --lang cpp|rust|javascript|typescript|csharp --repo DIR --limit N (0 = all)

For "which files", add `--files --limit 0`. Rows: `path:line [label] detail | source line`, read from the
git object store, so a sparse checkout is covered. Labels: `resolved` and `syntactic` are trusted (for
a generic name read `detail`); verify only `:ambiguous:N`, `:basename` and `…:parse-errors` rows.

**Rule:** answer from the rows; do not grep to re-check trusted ones. Search only for languages the
index lacks, Behind delta files, Stale or Unavailable, and the rows to verify.
