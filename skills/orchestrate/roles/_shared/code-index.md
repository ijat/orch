## Code index (`orch-index`)

A repository may carry a prebuilt code index (C/C++, Rust, JS/TS, C#) that answers structural
questions (definitions, callers, references, includers, importers, overrides) in one query. It is
optional: when it is missing, you fall back to search and lose nothing.

**Before the first `stats`, check for a rebuild in progress.** A caller may be (re)building the
index; the marker is `<git-common-dir>/orch-index/indexing`:

    M="$(git -C DIR rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/orch-index/indexing"
    cat "$M" 2>/dev/null   # one line: pid=<pid> started=<UTC time>
    P=$(sed -n 's/^pid=\([0-9]*\).*/\1/p' "$M" 2>/dev/null)
    [ -n "$P" ] && kill -0 "$P" 2>/dev/null && find "$M" -mmin -30 | grep -q . && echo live

The marker is live when that last line prints `live` (the pid is alive and the file is under 30
minutes old; it is written once, so its age is the build's age, and `started` is for humans). A live marker
means a rebuild is running: wait with `sleep 15` loops (re-checking the marker each time) for at most
5 minutes, then continue. If it is still live after 5 minutes, reply `index busy` and answer by
scoped search. A marker whose pid is dead, or that is older than 30 minutes, is stale: ignore it and
say so in one clause. No marker: go on.

**First command of every request that is a structural question:** run

    "${ORCH_INDEX_BIN:-orch-index}" stats [--repo DIR]   </dev/null

(`--repo DIR` when the caller names a repository; the binary comes from `$ORCH_INDEX_BIN` or `PATH`.)
It prints `commit <sha>` and row counts. The state is decided by `commit`, HEAD and `merge-base`,
not by the note on a query's count line: whenever `commit` differs from HEAD, every query's count
line carries ``(index at <sha>, HEAD is <sha>: results may be stale; run `orch-index index`)``,
ancestor or not, so that note alone is never a stale signal. Then, exactly one of:

- **Current** — `commit` is a sha equal to `git -C <dir> rev-parse HEAD` (or the caller asked about
  that commit). Answer from the index, unless the count line of your first query (read it before
  using its rows) contains the text `index built by an older orch-index`: then it is **Stale**.
- **Behind** — `commit` is a sha that differs from HEAD, and
  `git -C <dir> merge-base --is-ancestor <sha> HEAD` succeeds (HEAD is the indexed commit plus later
  commits, as in a task worktree). The `index at …, HEAD is …` note is expected here. The index is
  still usable, with a **mandatory delta search**: list the changed files with
  `git -C <dir> diff --name-only --no-renames <sha>` (the working tree against the indexed commit, so
  committed and uncommitted changes; `--no-renames` keeps the old path of a renamed file) and
  `git -C <dir> ls-files --others --exclude-standard`; search those files for the same name or path
  with the scoped search below; and for those files use the search result instead of any index row
  (the index's line numbers and edges there are old), dropping index rows for a listed file that no
  longer exists. Start the reply with `index at <sha> + delta search`. Do not suggest a refresh.
  Limit: in this state `orch-index` prints only the HEAD note, never the older-extractor note, so an
  index built by an older `orch-index` cannot be seen here; it shows once the index is rebuilt at a
  commit equal to HEAD.
- **Stale** — `commit` is a sha that is not an ancestor of HEAD (reason: `index from another
  branch`), or the Current case above found `index built by an older orch-index` (reason: `built by
  an older orch-index`). If `<dir>` is the main checkout, start the reply with
  `index unavailable: stale (<reason>) (refresh: orch-graph)` (one short line; the caller decides
  whether to run `orch-graph`). If `<dir>` is a linked worktree, start it with
  `index unavailable: stale (<reason>)` and no refresh hint: the index is shared with the main
  checkout, and rebuilding it at the worktree's HEAD would make it stale there. Tell them apart with

      [ "$(git -C <dir> rev-parse --path-format=absolute --git-dir)" = \
        "$(git -C <dir> rev-parse --path-format=absolute --git-common-dir)" ] && echo main-checkout

  (prints `main-checkout` in the main checkout, nothing in a linked worktree). Either way, then answer
  by scoped search (`git grep` / `rg`, always with a path argument and `</dev/null`) as you would
  without an index.
- **Absent** — the binary is not found, the command fails, or `commit (none)` (no index). Start the
  reply with a bare `index unavailable: <reason>` (no refresh hint: nobody asked for an index), then
  answer by scoped search as you would without one. If the caller did not ask about the index, keep
  that line to one clause.

**Never run `orch-index index`, and never run `orch-graph` (you cannot).** Building or updating an
index is the user's, the orchestrator's or the planner's job, through `orch-graph`; you only run
`stats` and the queries below. A stale or missing index is reported, never repaired.

Queries (after a usable `stats`; use the same binary you ran `stats` with):

    orch-index def NAME [--kind class,struct,function,method,macro,...]   # NAME may be Class::Method
    orch-index callers NAME          # call sites, with the calling function
    orch-index refs NAME [--kind call|type|use]
    orch-index includers path/to/file.h   # C/C++ includes, relative JS/TS imports
    orch-index importers MODULE           # Rust `use`, C# `using`, JS/TS packages
    orch-index overrides METHOD [--sig "(nsIRequest*)"] [--base CLASS]   # one row per class

All take `--path PREFIX` (scope), `--lang cpp|rust|javascript|typescript|csharp`, `--repo DIR`, and
`--limit N` (default 30; `--limit 0` = all). If the question asks *which files*, add
`--files --limit 0`. Output: a count line (with a note when the index commit differs from HEAD,
or, only when it equals HEAD, when an older `orch-index` built it), then
`path:line [label] detail | source line`. Sources come from the git object store, so a sparse
checkout is fully covered.

Trust labels:

| Label | Meaning | What you do |
|---|---|---|
| `resolved` | exact | use as is |
| `syntactic` | parsed from source, names matched textually, complete for indexed languages at the indexed commit | use; for a generic name read the `detail` / source line to pick the right row |
| `syntactic:ambiguous:N`, `syntactic:basename` | the include/import target is uncertain | verify only those rows (read the line) |
| `…:parse-errors` | the file has syntax errors; facts from it may be incomplete | verify those rows |

**Rule:** answer from the index output. Do not run grep to double-check `resolved` or `syntactic`
rows; that repeats work the index already did. Search only for: files in languages the index does
not cover, the delta files of a **Behind** index, an index that is **Stale** or **Absent**, and the
rows marked above.
Unlabelled indexes (ctags and the like) are leads only: they miss macro-wrapped and inline
definitions and conflate overloads, so verify against source.
