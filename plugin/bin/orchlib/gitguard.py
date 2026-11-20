"""`orch hook git-guard`: a PreToolUse hook on the Bash tool that refuses git commands which bypass or loosen the
agent-docs hooks, and the commands that would turn the agent-docs switch off. Agents never skip hooks; a human who
must can run the command themselves.

Reads the PreToolUse JSON on stdin. Exit 2 and one stderr message when blocked, exit 0 otherwise. It acts only when the
`agent_docs` setting is `on`: with the switch off it exits 0 before reading stdin (the cost is one settings read).
Fails open: unreadable JSON, or any surprise, allows the call (the git hooks still check content at commit and push).

Each simple command is parsed (heredoc bodies are dropped; quoted text is not a command), so prose that merely mentions
a flag is not blocked. Blocked:
  - `--no-verify` after a git subcommand, `commit -n` and combined short flags holding `n`
  - any git argument containing `core.hooksPath` (any case)
  - an `AGENTDOCS_ALLOW=` assignment: env prefix, after env/command/exec/sudo, or `export`/`declare`/`typeset`/`readonly`
  - `git config` that sets, adds or replaces an `agentdocs.*` key (reads and --unset only tighten)
  - `orch docs disable` and `orch setting set agent_docs off` (any spelling of the orch binary path); enabling is allowed
Known gap, shared with the shell guard this replaces: a command hidden inside `$(...)`, backticks, `bash -c '...'` or
`eval` is not parsed."""
import os
import sys


def settings_file():
    """Where settings.json is: the same rule as settings.settings_path(), repeated here so the switch-off path does not
    import the rest of orch (a test keeps the two equal)."""
    if os.environ.get("ORCH_DATA"):
        return os.path.join(os.environ["ORCH_DATA"], "settings.json")
    if os.environ.get("ORCH_DIR"):
        return os.path.join(os.environ["ORCH_DIR"], ".orch-settings.json")
    return os.path.join(os.path.expanduser("~"), ".claude", "orch-data", "settings.json")


def switch_on():
    """True only when settings.json holds agent_docs = "on"; a missing or malformed file is off."""
    try:
        with open(settings_file(), encoding="utf-8") as fh:
            text = fh.read()
        if '"agent_docs"' not in text:
            return False
        import json
        data = json.loads(text)
        return isinstance(data, dict) and data.get("agent_docs") == "on"
    except (OSError, ValueError):
        return False


def run():
    try:
        if not switch_on():
            return 0
        import json
        from . import gitrules
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
        bad = gitrules.reasons(cmd) if isinstance(cmd, str) else []
    except BaseException:   # fail open
        return 0
    if bad:
        sys.stderr.write(gitrules.message(bad) + "\n")
        return 2
    return 0
