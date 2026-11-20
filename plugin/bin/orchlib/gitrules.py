"""The rules of `orch hook git-guard`: which Bash command lines are refused (see gitguard.py for the list)."""
import re
import shlex

ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC_RE = re.compile(r"<<-?\s*([\x27\"]?)(\w+)\1[^\n]*\n.*?\n\2(\n|$)", re.S)
SPLIT_RE = re.compile(r"\n|&&|\|\||;|\||&")
WRAPPERS = ("env", "command", "exec", "sudo")
ASSIGN_BUILTINS = ("export", "declare", "typeset", "readonly")
PYTHONS = re.compile(r"^python[0-9.]*$")
GLOBAL_VALUE_OPTS = ("-c", "-C", "--git-dir", "--work-tree", "--namespace")


def _split_words(seg):
    try:
        return shlex.split(seg, comments=True)
    except ValueError:
        return seg.split()


def _base(word):
    return word.split("/")[-1]


def _git_reasons(args):
    bad = []
    if any("core.hookspath" in a.lower() for a in args):
        bad.append("core.hooksPath")
    sub, i = "", 0
    while i < len(args):
        a = args[i]
        if a in GLOBAL_VALUE_OPTS:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        sub = a
        break
    rest = args[i + 1:] if sub else []
    if "--no-verify" in rest:
        bad.append("--no-verify")
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


def reasons(cmd):
    """The sorted, de-duplicated reasons a Bash command line is refused (empty = allowed)."""
    cmd = HEREDOC_RE.sub("\n", cmd)     # heredoc bodies are data
    bad = []
    for seg in SPLIT_RE.split(cmd):
        w = _split_words(seg)
        env = []
        while w and ASSIGN_RE.match(w[0]):
            env.append(w.pop(0))
        if w and w[0] in ASSIGN_BUILTINS:
            env += [x for x in w[1:] if ASSIGN_RE.match(x)]
        if w and w[0] in WRAPPERS:
            env += [x for x in w[1:] if x.startswith("AGENTDOCS_ALLOW=")]   # `env -i X=1 git ...`, `sudo -u u X=1 git ...`
            w = w[1:]
            while w and ASSIGN_RE.match(w[0]):
                env.append(w.pop(0))
        if any(e.startswith("AGENTDOCS_ALLOW=") for e in env):
            bad.append("AGENTDOCS_ALLOW")
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
