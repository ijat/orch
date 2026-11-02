<!-- Phase-1 output of the orch-init agent: ~/.claude/orchestrations/<repo-name>/init-proposal.md.
Nothing is written to the repo until the user approves this. Keep it scannable. -->
# Init proposal: {{repo-name}}

- mode: {{new|refresh}}   ecosystem: {{...}}   tier: {{model/effort}}   base branch: {{base}}
- researched: {{date}}; reference `{{references/<eco>.md}}` verified {{date}}{{; re-verified against: <urls>}}

## Files

| Path | Lines | Purpose |
|---|---|---|
| CLAUDE.md | {{n}} | canonical rules |
| AGENTS.md | {{n}} | pointer |
| docs/STATE.md | {{n}} | |
| docs/DECISIONS.md | {{n}} | |
| {{extras chosen}} | | |
| {{untracked docs already in the repo (spec, design, planning .md): each path, "included in the init commit"}} | | existing |

## Commands run

| Command | Result |
|---|---|
| {{cmd}} | {{exit code and one-line outcome, or "not yet runnable (no code)"}} |

## Architecture (planned, named for this project; none of it is created by init)

- Boundaries and interfaces: {{interface -> adapters}}
- Composition root: {{file}}
- Contract tests / boundary check: {{paths, each "to be created by the first plan"}}

## Dropped as duplicates of ~/.claude/CLAUDE.md

- {{rule}} ({{why dropped}}); overrides kept: {{rule}}

## Filled CLAUDE.md

{{the full text}}

## Open questions / risks

- {{or "none"}}

## Diffs (refresh mode only)

{{per file: unified diff}}
