### When to delegate (quality first)

Delegating protects *your* context from bulk you only need a conclusion from. It never replaces your
judgement, and it is never worth a worse result — when in doubt about whether a digest is enough,
read the source yourself.

- **`orch-executor` — any command whose output you only need the verdict of:** full builds, test suites,
  linters, typecheckers, large greps/listings of generated output. Give it the exact command and what
  "pass" means. If its digest reports a failure, or is vaguer than the failure deserves, re-run that
  one case yourself and read the raw output before acting on it.
- **`orch-explorer` — breadth lookups and claim checks, and your default for them:** "where is X used",
  "who calls/includes/overrides X", "does this repo already have a helper for Y", "what does the config
  actually say", and confirming a `plan.md` assumption before you rely on it. **Threshold:** before you
  run a second search, or read a second unfamiliar file, for the same question, send that question to
  `orch-explorer` instead (several questions in one spawn; ask for `file:line` evidence), and read the
  cited lines yourself for anything you will build on. On a repository with a code index the explorer
  answers structural questions from it in one query; you do not need to know whether there is one.
- **Do it yourself instead:** a single known file or symbol (one `Read`/`grep` is cheaper than a
  spawn), anything you will edit, security-sensitive reading, and any decision.
- **Batch:** one spawn with several questions beats several spawns.

