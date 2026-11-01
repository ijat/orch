# Snippets for orch-init outputs

Default snippets are applied without a question; everything under "Opt-in" only when the user chose
that extra in the question batch. Replace `{{...}}`. No real values, tokens or hostnames in any snippet.
Tool versions and action majors below were checked on 2026-11-01; the agent re-checks anything it
writes into a file (`latest release` on the project page or `gh api`), and records what it checked.

## Default: `.gitignore` lines (append; skip lines already present)

```gitignore
# local agent/config state - never commit
CLAUDE.local.md
.claude/settings.local.json
# secrets - keep the example, ignore the rest
.env*
!.env.example
```

Check with `git ls-files`, not only ignore rules: a file already tracked stays tracked
(`git ls-files CLAUDE.local.md .claude/settings.local.json '.env*'` must list nothing but `.env.example`).
`.claude/settings.local.json` is also kept out by Claude Code's global excludes when it creates the file
(<https://code.claude.com/docs/en/settings>), but a hand-made one is not covered: keep the line.

## Default: `.env.example` rule (when the project reads config from the environment)

- One line per key the code reads, with an empty or obviously fake value and a comment on what it is.
- Never a real value. Rule for CLAUDE.md: "Config comes from env through the typed loader; `.env.example`
  lists every key." Litmus: the repo could be public at any moment without exposing a credential
  (<https://12factor.net/config>).

```dotenv
# {{KEY}}: {{what it is, who issues it}}
{{KEY}}=
```

## Opt-in: `.claude/settings.json` (committed; shared by everyone who clones)

Keys used: `permissions.allow`, `permissions.deny` (and `ask`), `hooks.PostToolUse`; the file is strict
JSON (no comments, no trailing commas). Sources:
<https://code.claude.com/docs/en/settings> (files and scopes: commit `.claude/settings.json`, keep
`.claude/settings.local.json` out of git; the `permissions` rule form), <https://code.claude.com/docs/en/hooks>
(hook nesting). `allow` rules in the committed file apply only after the user trusts the folder; `deny` and
`ask` apply immediately. Deny rules are the control; prose in CLAUDE.md is not.

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash({{gate command}})",
      "Bash({{test command}} *)"
    ],
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)"
    ]
  }
}
```

Adjust the rule strings to the project's real commands. Keep the enforced set small (formatter, gate,
secret scan); every extra rule is something to maintain.

## Opt-in: format hook (PostToolUse, in the same settings.json)

Merge into the file above. `{{formatter}}` is the ecosystem's per-file formatter from the "Per-file
formatter for the hook" line of `references/<eco>.md`; `{{ext}}` is its source extension (for example `rs`).

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/format.sh" }
        ]
      }
    ]
  }
}
```

`.claude/hooks/format.sh` (commit it, `chmod +x`). The hook receives JSON on stdin; the edited path is
`tool_input.file_path` (<https://code.claude.com/docs/en/hooks>). PostToolUse cannot block the tool (it
already ran); exit 2 shows stderr to Claude, other non-zero exits are only a notice. The extension
filter keeps formatters that reject non-source files from failing on CLAUDE.md or STATE.md edits:

```bash
#!/bin/bash
file=$(jq -r '.tool_input.file_path // empty')
[ -n "$file" ] || exit 0
case "$file" in *.{{ext}}) ;; *) exit 0 ;; esac   # only source files; skip .md, .json, ...
if ! out=$({{formatter}} "$file" 2>&1); then
  echo "$out" >&2
  exit 2
fi
```

## Opt-in: secret scan

A local check run by the gate and by CI, not by emphasis in CLAUDE.md. Example with gitleaks
(<https://github.com/gitleaks/gitleaks>; `git --staged --redact` verified against 8.30.1 `--help` on 2026-11-01; run `gitleaks --version` first):

```bash
#!/bin/bash
# scripts/check-secrets.sh - fails if staged changes contain a secret
gitleaks git --staged --redact
```

Add `./scripts/check-secrets.sh` to the gate command. `--redact` keeps values out of output and logs.

## Opt-in: GitHub Actions gate workflow (`.github/workflows/ci.yml`)

Runs the same gate command as local, nothing else. All CI in the user's repos so far is GitHub Actions.
Pin the checkout major to the latest release found at write time (v7 on 2026-11-01); add the ecosystem's
setup step from `references/<eco>.md` before the gate.

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      # {{ecosystem setup step from references/<eco>.md}}
      - run: {{gate command}}
```

Rule for CLAUDE.md when CI is chosen: "CI runs the gate command; keep local and CI identical."

## Opt-in: README and LICENSE

- README: name, one-line purpose, how to build/test (the same commands as CLAUDE.md `Commands`), license
  line. Do not duplicate CLAUDE.md prose.
- LICENSE: the type is **asked** (MIT / Apache-2.0 / GPL / BSL / other); never chosen by default. Take the
  text from <https://spdx.org/licenses/> and fill the year and the holder the user names.

## Opt-in: `CLAUDE.local.md`

Personal notes for this checkout only; gitignored (default `.gitignore` lines). Note for the user: it
exists only in the worktree it was made in, and adding it stops Claude reading `AGENTS.md` natively
(<https://code.claude.com/docs/en/memory>); harmless here because CLAUDE.md is canonical. Start it empty
with a one-line header.
