# Non-code projects reference (research, planning, docs, creative, data)

verified: 2026-11-02
sources: patterns from three earlier non-code projects (a research/strategy workspace, a creative production
project, a scope/planning workspace), read on 2026-11-02; <https://code.claude.com/docs/en/memory>
(size, `@` imports, `.claude/rules/`), <https://code.claude.com/docs/en/best-practices> (include only what cannot be inferred).
This file has no tool versions; nothing here is `unverified` except the lines marked so. There is no interfaces-and-adapters
section, but the same idea holds for content: one source of truth, derived files that never contradict it.
Architecture reference for code (`architecture.md`) does not apply unless the project later gains code.

## Init command

`git init -b <base>` if missing (never a remote). No package manager, no build. Create folders only for content that exists:
`docs/` (STATE.md, DECISIONS.md), plus the project's own folders (below). Do not scaffold empty `src/` or `tests/`.

## Layout (the content equivalent of interfaces and adapters)

Pick folders by the kind of work, name them in CLAUDE.md in one line each:

- **Research / strategy:** `README.md` as the index, `strategy/` (positioning, formats), `research/`
  (dated findings, evidence, tool comparisons, limits, sources), `plans/` (proposals, not commitments), `workflows/`
  (repeatable steps), `templates/` (reusable briefs), `admin/` (private notes, never in shareable docs).
- **Creative production:** a shared source of truth (`playbooks/base.md`), a per-item template
  (`playbooks/item-template.md`), one file per item (`items/NN-slug.md`), an index (`index.md`). Rule: a compiled item =
  shared base + item prompt; any departure from the base is written down under "Deviations from base" in the item file.
- **Scope / planning workspace:** one source document (`*.xlsx`), derived docs
  numbered `docs/00-overview.md ...`, a `glossary.md`, an `analysis/` folder for contradictions and gaps. The CLAUDE.md
  states "There is no code here yet. Do not assume a framework, package manager, test runner or build step exists."
- Swappable pieces here are the **inputs and tools**: keep the vendor or tool choice (a generator, a transcription
  tool, a spreadsheet) in one `workflows/` file so it can be replaced without rewriting the content.

## Tooling

No gate by default. Optional, ask once: Markdown link check and spell check (tool choice: unverified, research it at write
time), a script that rebuilds derived docs from the source file (only if the project has one), a secret scan if any
private notes or contact details exist (`templates/snippets.md`). Preserve originals: never overwrite source media or
raw data; generated outputs go to a separate folder (for example `source/` vs `exports/`).

## Strictness from day 0

- **Sources rule** (write it into CLAUDE.md): every changeable claim (price, quota, feature, date) is checked against a
  current primary source; record the check date, URL and the uncertainty; separate vendor claims, tested behaviour and our
  recommendation. Never present an advertised feature, a beta or a roadmap as a tested result.
- Mark unverified items as `unverified` instead of rounding them up.
- A "single source of truth" file is named in CLAUDE.md; derived files say which source they derive from and are updated
  in the same commit as the source.
- Scope boundary: if the project is for a third party or must not share accounts, hosting, secrets or infrastructure with
  the user's other work, say so in the first 10 lines (a "Scope boundary" section).
- Language and tone rules the user stated (for example a spoken language, subtitle languages, audience) go in CLAUDE.md
  as hard rules, not preferences.

## Logging

None. The equivalent is `docs/STATE.md` (now / next / blockers / last updated) updated in the same commit as the work, and
dated entries in `research/` or `docs/DECISIONS.md`.

## Errors

Not applicable. Contradictions between sources are findings: record them in an `analysis/` or `research/` file with both
sources and the date (for example `analysis/gaps.md`), do not silently pick one.

## Config and secrets

Private details (emails, account names, budgets, client data) live in one `admin/` or `private/` folder that is listed in
CLAUDE.md as "never copy into shareable docs"; add it to `.gitignore` if the repo might be shared (gitignore choice is the user's). `.env*` and secrets ignored and denied as in
`templates/snippets.md`. Cloud versus local processing for any private material is stated per workflow (for example local editing and transcription).

## Gate command

None. If the user wants one, the minimum is: link check + no tracked secrets (`git ls-files` against the ignore list) +
STATE.md updated. Put it in `scripts/check.sh` and list it under Commands. Otherwise write "No build, test or lint
step exists" under Commands.

## CI snippet notes

No CI by default (and no workflow until a gate command exists; until then "add CI once the gate exists" goes into `docs/STATE.md` next steps). A docs-only gate would run the script above in the GitHub Actions workflow from `templates/snippets.md`.
Publishing a site from the docs (Pages, static generator): unverified, only on request.

## Gotchas

- Do not invent a build or test command; an agent told "run the tests" in a docs repo should say there is nothing to run.
- Keep CLAUDE.md short (well under 200 lines, code.claude.com/docs/en/memory); the long material is the content itself.
  The three earlier projects were 167, 56 and 41 lines (`wc -l`, 2026-11-02): the shortest were the most usable.
- Generated or derived documents drift from the source; name the source in each and update both together.
- Do not put per-item data (a track list, a feature list) in CLAUDE.md; point to the index file.
- Private and public material in one tree: decide the sharing boundary on day 0 (see Config and secrets).
