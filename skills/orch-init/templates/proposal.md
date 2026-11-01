<!-- Phase-1 output of the orch-init agent: ~/.claude/orchestrations/<repo-name>/init-proposal.md.
Nothing is written to the repo until the user approves this. Keep it scannable. -->
# Init proposal: {{repo-name}}

- mode: {{new|refresh}}   ecosystem: {{...}}   tier: {{model/effort}}
- researched: {{date}}; reference `{{references/<eco>.md}}` verified {{date}}{{; re-verified against: <urls>}}

## Files

| Path | Lines | Purpose |
|---|---|---|
| CLAUDE.md | {{n}} | canonical rules |
| AGENTS.md | {{n}} | pointer |
| docs/STATE.md | {{n}} | |
| docs/DECISIONS.md | {{n}} | |
| {{extras chosen}} | | |

## Commands run

| Command | Result |
|---|---|
| {{cmd}} | {{exit code and one-line outcome}} |

## Architecture (named for this project)

- Boundaries and interfaces: {{interface -> adapters}}
- Composition root: {{file}}
- Contract tests / boundary check: {{paths}}

## Dropped as duplicates of ~/.claude/CLAUDE.md

- {{rule}} ({{why dropped}}); overrides kept: {{rule}}

## Filled CLAUDE.md

{{the full text}}

## Open questions / risks

- {{or "none"}}

## Diffs (refresh mode only)

{{per file: unified diff}}
