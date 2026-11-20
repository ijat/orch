"""The rules of `orch hook git-guard`: which Bash command lines are refused (see gitguard.py for the list)."""
import re
import shlex

ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC_RE = re.compile(r"<<-?\s*([\x27\"]?)(\w+)\1[^\n]*\n.*?\n\2(\n|$)", re.S)
WRAPPERS = ("env", "command", "exec", "sudo")
ASSIGN_BUILTINS = ("export", "declare", "typeset", "readonly")
PYTHONS = re.compile(r"^python[0-9.]*$")
GLOBAL_VALUE_OPTS = ("-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env")
# Environment names that move git's configuration (the hooks directory and the hook settings live there), or the
# programs and data the hooks run from. Refused as an assignment on a git command (and after export/declare).
GUARDED_ENV = re.compile(r"^(HOME|XDG_CONFIG_HOME|GIT_CONFIG|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_NOSYSTEM|"
                         r"GIT_CONFIG_PARAMETERS|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_.*|GIT_CONFIG_VALUE_.*|GIT_EXEC_PATH|"
                         r"ORCH_BIN|ORCH_DATA|ORCH_DIR|ORCH_DOCS_PRIVATE|PATH|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP)$")
NO_VERIFY = "--no-verify"


def split_segments(cmd):
    """The simple commands of a command line: it is cut at `;`, `|`, `&`, `&&`, `||`, newlines, parentheses and
    backticks that are outside quotes. `>&`, `<&` and `&>` are redirections, not separators; a comment runs to the end
    of its line. Quoted text is never cut, so a message such as "a; b" stays inside its command."""
    cmd = cmd.replace("\\\n", " ")
    segs, cur, quote, i, n = [], [], None, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote == "'":
            cur.append(c)
            if c == "'":
                quote = None
        elif quote == '"':
            cur.append(c)
            if c == "\\" and i + 1 < n:
                i += 1
                cur.append(cmd[i])
            elif c == '"':
                quote = None
        elif c == "\\" and i + 1 < n:
            cur.append(c)
            i += 1
            cur.append(cmd[i])
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "#" and (not cur or cur[-1] in " \t"):
            while i + 1 < n and cmd[i + 1] != "\n":
                i += 1
        elif c == "&" and ((i > 0 and cmd[i - 1] in "<>") or (i + 1 < n and cmd[i + 1] == ">")):
            cur.append(c)
        elif c in ";|&\n()`":
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    segs.append("".join(cur))
    return segs


def _split_words(seg):
    try:
        return shlex.split(seg, comments=True)
    except ValueError:      # an unbalanced quote: read the quote marks as spaces (stricter than leaving them in words)
        return seg.replace('"', " ").replace("'", " ").split()


def _base(word):
    return word.split("/")[-1]


def _is_no_verify(arg):
    """`--no-verify` or any prefix of it git accepts as an abbreviation (`--no-verif`, `--no-ve`, ...)."""
    name = arg.split("=", 1)[0]
    return len(name) >= 6 and name.startswith("--no-v") and NO_VERIFY.startswith(name)


def _git_reasons(args):
    bad = []
    if any("core.hookspath" in a.lower() for a in args):
        bad.append("core.hooksPath")
    sub, i = "", 0
    while i < len(args):
        a = args[i]
        if "agentdocs." in a.lower():           # -c agentdocs.allow=true, --config-env=agentdocs.mode=VAR, ...
            bad.append("git -c agentdocs.*")
        if a in GLOBAL_VALUE_OPTS:
            i += 2
            if i - 1 < len(args) and "agentdocs." in args[i - 1].lower():
                bad.append("git -c agentdocs.*")
            continue
        if a.startswith("-"):
            i += 1
            continue
        sub = a
        break
    rest = args[i + 1:] if sub else []
    if any(_is_no_verify(a) for a in rest):
        bad.append(NO_VERIFY)
    if sub == "commit" and any(re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", a) for a in rest):
        bad.append("commit -n")
    if sub == "config":
        # reads and unsets only tighten; setting a value (or --add/--replace-all/`config set`) may loosen
        pos = [a for a in rest if not a.startswith("-")]
        if pos and pos[0] in ("set", "get", "unset", "list"):
            verb, pos = pos[0], pos[1:]
        else:
            verb = "set" if len(pos) >= 2 else "get"
        if any(a in ("--add", "--replace-all") for a in rest):
            verb = "set"
        if any(a.startswith("--unset") or a in ("--get", "--get-all", "--get-regexp", "--list", "-l") for a in rest):
            verb = "read-or-unset"
        if verb == "set" and any(p.lower().startswith("agentdocs.") for p in pos):
            bad.append("git config agentdocs.*")
        if verb == "set" and any(NO_VERIFY in p.lower() for p in pos):     # an alias that skips the hooks
            bad.append("git config " + NO_VERIFY)
    return bad


def _orch_reasons(args):
    """The switch-off commands: `docs ... disable` and `setting set agent_docs off`."""
    sub, i = "", 0
    while i < len(args):
        a = args[i]
        if a in ("-t", "--task"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        sub = a
        break
    pos = [a for a in args[i + 1:] if not a.startswith("-")] if sub else []
    if sub == "docs" and "disable" in pos:
        return ["orch docs disable"]
    if sub == "setting" and pos[:3] == ["set", "agent_docs", "off"]:
        return ["orch setting set agent_docs off"]
    return []


def _env_reasons(assigns, runs_git, export_like):
    bad = []
    for a in assigns:
        name = a.split("=", 1)[0]
        if name == "AGENTDOCS_ALLOW":
            bad.append("AGENTDOCS_ALLOW")
        elif GUARDED_ENV.match(name) and (runs_git or export_like):
            bad.append("env " + name)
    return bad


def reasons(cmd):
    """The sorted, de-duplicated reasons a Bash command line is refused (empty = allowed)."""
    cmd = HEREDOC_RE.sub("\n", cmd)     # heredoc bodies are data
    bad = []
    for seg in split_segments(cmd):
        w = _split_words(seg)
        assigns = []
        while w and ASSIGN_RE.match(w[0]):
            assigns.append(w.pop(0))
        export_like = bool(w) and w[0] in ASSIGN_BUILTINS
        if export_like:
            assigns += [x for x in w[1:] if ASSIGN_RE.match(x)]
        wrapped = bool(w) and w[0] in WRAPPERS
        if wrapped:     # `env -i X=1 git ...`, `sudo -u u X=1 git ...`: assignments anywhere in the wrapper's words
            w = w[1:]
            assigns += [x for x in w if ASSIGN_RE.match(x)]
        runs_git = bool(w) and (any(_base(x) == "git" for x in w) if wrapped else _base(w[0]) == "git")
        bad += _env_reasons(assigns, runs_git, export_like)
        if not w:
            continue
        if wrapped:     # skip the wrapper's options and assignments (`sudo -u root`) to the command it runs
            while w and not (_base(w[0]) in ("git", "orch") or PYTHONS.match(_base(w[0]))):
                w = w[1:]
            if not w:
                continue
        if _base(w[0]) == "git":
            bad += _git_reasons(w[1:])
        elif _base(w[0]) == "orch":
            bad += _orch_reasons(w[1:])
        elif PYTHONS.match(_base(w[0])) and len(w) > 1 and _base(w[1]) == "orch":
            bad += _orch_reasons(w[2:])
    return sorted(set(bad))


def message(bad):
    switch = [b for b in bad if b.startswith("orch ")]
    hooks = [b for b in bad if not b.startswith("orch ")]
    parts = []
    if hooks:
        parts.append("Blocked (%s): this bypasses or loosens the repo hooks (orch docs / gitleaks). Fix what the hook "
                     "reports; agent docs belong in the private store (orch docs adopt <path>)." % ", ".join(hooks))
    if switch:
        parts.append("Blocked (%s): turning the agent-docs switch off is the user's decision." % ", ".join(switch))
    return " ".join(parts) + " If the user wants an exception, they run it themselves."
