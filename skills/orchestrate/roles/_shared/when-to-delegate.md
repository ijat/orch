### When to delegate (quality first)

Delegating protects *your* context from bulk you only need a conclusion from. It never replaces your
judgement, and it is never worth a worse result — when in doubt about whether a digest is enough,
read the source yourself.

- **`orch-executor` — any command whose output you only need the verdict of:** full builds, test suites,
  linters, typecheckers, large greps/listings of generated output. Give it the exact command and what
  "pass" means. If its digest reports a failure, or is vaguer than the failure deserves, re-run that
  one case yourself and read the raw output before acting on it.
- **`orch-explorer` — breadth lookups and claim checks:** "where is X used", "does this repo already have a
  helper for Y", "what does the config actually say", and confirming a `plan.md` assumption before you
  rely on it. Ask for `file:line` evidence, and read the cited lines yourself for anything you will
  build on.
- **`orch-graph` — structural questions:** who defines, calls, references, includes, imports or overrides
  X. It answers from a prebuilt `orch-index` when the repository has one and falls back to scoped search
  itself (its reply starts `index unavailable: <reason>`), so you never need to check for an index first.
  Send **all** the structural questions for a step in **one** spawn. Before asking, look in `notes.md`
  for an earlier answer, and record the answer you rely on there (`path:line`, with its trust label) so
  later roles reuse it instead of asking again. Anything that is not structural goes to `orch-explorer`.
- **Do it yourself instead:** a single known file or symbol (one `Read`/`grep` is cheaper than a
  spawn), anything you will edit, security-sensitive reading, and any decision.
- **Batch:** one spawn with several questions beats several spawns.

