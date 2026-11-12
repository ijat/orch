### When to delegate (quality first)

Delegating protects *your* context from bulk you only need a conclusion from. It never replaces your
judgement, and it is never worth a worse result — when in doubt about whether a digest is enough,
read the source yourself.

- **`orch:executor` — any command whose output you only need the verdict of:** full builds, test suites,
  linters, typecheckers, large greps/listings of generated output. Give it the exact command and what
  "pass" means. If its digest reports a failure, or is vaguer than the failure deserves, re-run that
  one case yourself and read the raw output before acting on it.
- **`orch:explorer` — breadth lookups and claim checks, and your default for them:** "where is X used",
  "who calls/includes/overrides X", "does this repo already have a helper for Y", "what does the config
  actually say", and confirming a `plan.md` assumption before you rely on it. **Your FIRST search on any
  question that is not about one file you were handed is an `orch:explorer` spawn** (send all such
  questions of the current task in one spawn; ask for `file:line` evidence). Run your own Grep/Read only
  for the cases under *Do it yourself instead* below and on files the explorer returned; read the
  cited lines yourself for anything you will build on. On a repository with a code index the
  explorer answers structural questions from it in one query; you do not need to know whether there is one.
  **Strongly prefer the explorer on an indexed repository** (one built `orch-index`; nothing blocks
  you, so this is a habit to keep, not a gate). It answers "who calls / defines / includes X" from the
  index in one query. That is faster than a grep sweep, returns `file:line` evidence, and keeps the bulk
  out of your context. A search of your own costs more tokens and misses call sites the index finds.
  Searching one file yourself is always fine; so is a search the index cannot do (a regex, a string
  in a non-code file).
- **Do it yourself instead:** a single file or symbol named in your brief or diff (one `Read`/`grep` is
  cheaper than a spawn), the diff or files you were handed, anything you will edit, security-sensitive reading, and any decision.
- **Read ranges, not whole files.** Locate first (the explorer or the index for structure, `grep -n` inside one
  file), then `Read` with `offset` and `limit`. Read a whole file only when it is short (about 300 lines or
  fewer), you will change most of it, or you must judge it whole. Before you edit a function or region you
  located, `Read` that range (`offset`, `limit`): the edit tool needs a prior `Read`, and a whole-file `Read`
  is not the way to get it. Don't re-read a range you already have unless it changed.
- **Batch:** one spawn with several questions beats several spawns.

