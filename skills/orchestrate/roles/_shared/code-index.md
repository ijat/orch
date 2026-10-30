## Code index (`orch-index`)

A repository may carry a prebuilt code index (C/C++, Rust, JS/TS, C#) that answers structural
questions (definitions, callers, references, includers, importers, overrides) in one query. It is
optional: when it is missing, you fall back to search and lose nothing.

**First command of every request that is a structural question:** run

    "${ORCH_INDEX_BIN:-orch-index}" stats [--repo DIR]   </dev/null

(`--repo DIR` when the caller names a repository; the binary comes from `$ORCH_INDEX_BIN` or `PATH`.)
It prints `commit <sha>` and row counts. Then, exactly one of:

- **Usable** — the command ran, `commit` is a sha (not `(none)`), and it equals
  `git -C <dir> rev-parse HEAD` (or the caller asked about that commit). Answer from the index.
- **Unavailable** — the binary is not found, the command fails, `commit (none)` (no index), or the
  commit differs from HEAD (stale). Start your reply with `index unavailable: <reason>` (one short
  line), then answer by scoped search (`git grep` / `rg`, always with a path argument and
  `</dev/null`) as you would without an index. If the caller did not ask about the index, keep that
  line to one clause.

**Never run `orch-index index`.** Building or updating an index is the user's or the orchestrator's
job; you only run `stats` and the queries below. A stale or missing index is reported, never repaired.

Queries (after a usable `stats`; use the same binary you ran `stats` with):

    orch-index def NAME [--kind class,struct,function,method,macro,...]   # NAME may be Class::Method
    orch-index callers NAME          # call sites, with the calling function
    orch-index refs NAME [--kind call|type|use]
    orch-index includers path/to/file.h   # C/C++ includes, relative JS/TS imports
    orch-index importers MODULE           # Rust `use`, C# `using`, JS/TS packages
    orch-index overrides METHOD [--sig "(nsIRequest*)"] [--base CLASS]   # one row per class

All take `--path PREFIX` (scope), `--lang cpp|rust|javascript|typescript|csharp`, `--repo DIR`, and
`--limit N` (default 30; `--limit 0` = all). If the question asks *which files*, add
`--files --limit 0`. Output: a count line (it says so if the index is stale), then
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
not cover, an index that `stats` or a count line shows as stale, and the rows marked above.
Unlabelled indexes (ctags and the like) are leads only: they miss macro-wrapped and inline
definitions and conflate overloads, so verify against source.
