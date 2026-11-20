"""`orch hook git-guard`: a PreToolUse hook on the Bash tool that refuses git commands which bypass or loosen the
agent-docs hooks, and the commands that would turn the agent-docs switch off. Agents never skip hooks; a human who
must can run the command themselves.

Reads the PreToolUse JSON on stdin. Exit 2 and one stderr message when blocked, exit 0 otherwise. It acts only when the
`agent_docs` setting is `on`: with the switch off it exits 0 before reading stdin (the cost is one settings read).
Fails open: unreadable JSON, or any surprise, allows the call (the git hooks still check content at commit and push).

The decision is per command line (gitrules.py): a line that runs no git is allowed, except an `AGENTDOCS_ALLOW=`
assignment and the switch-off commands `orch docs disable` / `orch setting set agent_docs off`, refused anywhere. A line
with a git word in any position (behind timeout, time, !, xargs, if/then, { }, for/do, a subshell, `bash -c`/`eval`,
a `$(...)` or backticks) is refused when any of its commands holds a risky word:
  - `--no-verify` or an abbreviation of it, as text anywhere in a command (a quoted message, `x=--no-verify`, a
    substitution's output source); `-n` (or a short-flag group holding n) beside `commit` in one command, or from an
    assignment value or a substitution on a line with a `commit` word
  - `hookspath` (any case) as text; an `agentdocs.` or `alias.` key at the start of a word or after `=` (reads and
    --unset of `git config` excepted)
  - an assignment, export, unset, `env -u` of HOME, XDG_CONFIG_HOME, GIT_CONFIG*, GIT_EXEC_PATH, PATH, PYTHON*, ORCH_BIN,
    ORCH_DATA, ORCH_DIR, ORCH_DOCS_PRIVATE or AGENTDOCS_ALLOW; `env -i`, `env -`
Data, never checked: heredoc bodies (`<<`, `<<-`; except the `$(...)`/backticks of an unquoted one, which run),
comments, and the arguments of `echo`/`grep` unless that command sits inside a `$(...)`, backticks or a subshell. The
strings of `bash -c`/`sh -c`/`eval`/`env -S` and a `$(...)` inside a quoted word are also read as command lines. A line
that cannot be read (an unclosed quote, an unterminated heredoc) is judged on its raw words, fail closed. Over-blocking is accepted; the message
says the user can run the command. Known gap: a script or a sourced file that runs git is not read."""
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
