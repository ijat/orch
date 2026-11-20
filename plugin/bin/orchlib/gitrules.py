"""The rules of `orch hook git-guard`: which Bash command lines are refused (see gitguard.py for the list).

The decision is per command line, not per spelling of a git call:
  - a line with no git word (outside `echo`/`grep` arguments, heredoc bodies and comments) is allowed, except for an
    `AGENTDOCS_ALLOW=` assignment and the agent-docs switch-off commands, which are refused on any line;
  - a line with a git word anywhere (behind `timeout`, `time`, `!`, `xargs`, `then`, `{`, a variable holding git, ...)
    is refused when any of its commands holds a risky word: a `--no-verify` prefix, `hookspath`, an `agentdocs.` or
    `alias.` key, `-n` beside `commit`, or a name that moves git's config or the hook's programs (assigned, exported,
    unset, `env -i`/`-u`). The `--no-verify` prefix, `hookspath` and the keys are matched as text anywhere in a
    command (quoted words, assignment values, substitutions), so a commit message that mentions them is refused too:
    a heredoc message (`git commit -F - <<EOF`) is the way to write one;
  - a line that cannot be read (an unclosed quote, an unterminated heredoc) is judged on its raw words, fail closed.
Over-blocking a rare harmless command is accepted: the git hooks are the real check, and the user can run it."""
import re
import shlex

ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
# Names that move git's configuration (the hooks directory and the hook settings live there), or the programs and data
# the hooks run from; AGENTDOCS_ALLOW is the hooks' own override. A word that assigns, names (unset, export -n, env -u)
# or `-u`/`--unset=`-names one of them is risky on a git line.
GUARDED_WORD = re.compile(r"^(?:-u|--unset=)?(HOME|XDG_CONFIG_HOME|GIT_CONFIG\w*|GIT_EXEC_PATH|PATH|PYTHON\w*|ORCH_BIN|"
                          r"ORCH_DATA|ORCH_DIR|ORCH_DOCS_PRIVATE|AGENTDOCS_ALLOW)(?:\+?=.*)?$", re.S)
ALLOW_ASSIGN = re.compile(r"^AGENTDOCS_ALLOW\+?=")
SHORT_N = re.compile(r"^-[a-zA-Z]*n[a-zA-Z]*$")
NO_VERIFY_SUB = re.compile(r"--no-v(?:e(?:r(?:i(?:fy?)?)?)?)?(?![\w-])")
KEY_SUB = re.compile(r"(?:^|[\s=;'\"(`])(agentdocs|alias)\.", re.I)
SUBST_RE = re.compile(r"\$\(([^\n]*)|`([^`\n]*)`")
KEY_RE = re.compile(r"(?:^|=)(agentdocs|alias)\.", re.I)
DATA_CMDS = ("echo", "grep")        # commands whose arguments are never run: their words are data
EVALUATORS = ("bash", "sh", "zsh", "dash", "ksh", "eval")
GLOBAL_VALUE_OPTS = ("-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env")
NO_VERIFY = "--no-verify"
UNPARSED = "unreadable command line"
MAX_DEPTH = 3


class Unreadable(Exception):
    pass


def _heredoc_delim(cmd, i):
    """Read the heredoc word that starts at cmd[i]: (delimiter with its quotes removed, whether it was quoted, index
    after it). A quoted delimiter means the body is not expanded."""
    n = len(cmd)
    while i < n and cmd[i] in " \t":
        i += 1
    out, quoted = [], False
    while i < n and cmd[i] not in " \t\n;|&<>()":
        c = cmd[i]
        quoted = quoted or c in "'\"\\"
        if c in "'\"":
            j = cmd.find(c, i + 1)
            if j < 0:
                raise Unreadable()
            out.append(cmd[i + 1:j])
            i = j + 1
            continue
        if c == "\\" and i + 1 < n:
            i += 1
        out.append(cmd[i])
        i += 1
    if not out:
        raise Unreadable()
    return "".join(out), quoted, i


def _skip_heredoc_bodies(cmd, i, pending, runs):
    """cmd[i] is the first character after a newline; skip the bodies of the pending heredocs, return the index after
    the last terminator line. `<<-` strips leading tabs before comparing. No terminator: Unreadable. The command
    substitutions of a body with an unquoted delimiter run, so their text is added to `runs`."""
    n = len(cmd)
    for delim, quoted, dash in pending:
        while True:
            if i >= n:
                raise Unreadable()
            j = cmd.find("\n", i)
            end = n if j < 0 else j
            line = cmd[i:end]
            i = end + 1
            if (line.lstrip("\t") if dash else line) == delim:
                break
            if not quoted:
                runs += ["".join(m) for m in SUBST_RE.findall(line)]
    return i


def split_segments(cmd):
    """The simple commands of a command line: it is cut at `;`, `|`, `&`, `&&`, `||`, newlines, parentheses and
    backticks that are outside quotes. `>&`, `<&` and `&>` are redirections, not separators; a comment runs to the end
    of its line; heredoc bodies (`<<`, `<<-`, quoted delimiter or not) are dropped, except the command substitutions
    of an unquoted one, which are added as commands. Quoted text is never cut. Raises Unreadable on an unclosed quote
    or an unterminated heredoc. Returns (text, spliced) pairs: spliced is true for a command that starts after a `(`
    or a backtick, or comes from a heredoc body, whose output may become words of another command."""
    segs, cur, quote, i, n, pending, runs, spliced = [], [], None, 0, len(cmd), [], [], False
    while i < n:
        c = cmd[i]
        if quote == "'":
            cur.append(c)
            if c == "'":
                quote = None
        elif quote == '"':
            if c == "\\" and i + 1 < n:
                if cmd[i + 1] != "\n":
                    cur.append(c)
                    cur.append(cmd[i + 1])
                i += 1
            else:
                cur.append(c)
                if c == '"':
                    quote = None
        elif c == "\\" and i + 1 < n:
            if cmd[i + 1] != "\n":     # backslash-newline is a line continuation
                cur.append(c)
                cur.append(cmd[i + 1])
            i += 1
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "#" and (not cur or cur[-1] in " \t"):
            while i + 1 < n and cmd[i + 1] != "\n":
                i += 1
        elif c == "<" and cmd.startswith("<<<", i):
            cur.append("<<<")
            i += 2
        elif c == "<" and cmd.startswith("<<", i):
            dash = cmd.startswith("<<-", i)
            delim, quoted, i = _heredoc_delim(cmd, i + (3 if dash else 2))
            pending.append((delim, quoted, dash))
            cur.append(" ")
            continue
        elif c == "&" and ((i > 0 and cmd[i - 1] in "<>") or (i + 1 < n and cmd[i + 1] == ">")):
            cur.append(c)
        elif c in ";|&\n()`":
            segs.append(("".join(cur), spliced))
            cur, spliced = [], c in "(`"
            if c == "\n" and pending:
                i = _skip_heredoc_bodies(cmd, i + 1, pending, runs)
                pending = []
                continue
        else:
            cur.append(c)
        i += 1
    if quote or pending:
        raise Unreadable()
    segs.append(("".join(cur), spliced))
    for text in runs:
        segs += [(t, True) for t, _ in split_segments(text)]
    return segs


def _base(word):
    return word.split("/")[-1]


def _is_git(word):
    """`git`, a path to it, or an assignment whose value runs it (`G=git`, `G="/usr/bin/git -C x"`)."""
    if _base(word) == "git":
        return True
    if ASSIGN_RE.match(word):
        value = word.split("=", 1)[1].split()
        return bool(value) and _base(value[0]) == "git"
    return False


def _is_no_verify(arg):
    """`--no-verify` or any prefix of it git accepts as an abbreviation (`--no-verif`, `--no-ve`, ...)."""
    name = arg.split("=", 1)[0]
    return len(name) >= 6 and name.startswith("--no-v") and NO_VERIFY.startswith(name)


def _config_read_keys(w):
    """Indexes of the key words of `git config` reads and unsets in this command (they only tighten, so a key such as
    agentdocs.mode is fine there). Empty when the command is not git config or sets a value."""
    start = next((k for k, a in enumerate(w) if _base(a) == "git"), None)
    if start is None:
        return set()
    i = start + 1
    while i < len(w) and w[i].startswith("-"):
        i += 2 if w[i] in GLOBAL_VALUE_OPTS else 1
    if i >= len(w) or w[i] != "config":
        return set()
    rest = list(range(i + 1, len(w)))
    pos = [k for k in rest if not w[k].startswith("-")]
    if pos and w[pos[0]] in ("set", "get", "unset", "list"):
        verb, pos = w[pos[0]], pos[1:]
    else:
        verb = "set" if len(pos) >= 2 else "get"
    if any(w[k] in ("--add", "--replace-all") for k in rest):
        verb = "set"
    if any(w[k].startswith("--unset") or w[k] in ("--get", "--get-all", "--get-regexp", "--list", "-l") for k in rest):
        verb = "unset"
    return set(pos) if verb in ("get", "unset", "list") else set()


def _env_reasons(w):
    """`env -i` / `env -` (a clean environment drops HOME and PATH) and `env -S` (a string the guard does not read)."""
    bad = []
    for k, a in enumerate(w):
        if _base(a) != "env":
            continue
        j = k + 1
        while j < len(w):
            o = w[j]
            if o in ("-", "--ignore-environment") or (re.match(r"^-[a-zA-Z]+$", o) and o[1] not in "uCS" and "i" in o):
                bad.append("env -i")
            if o.startswith(("-S", "--split-string")):
                bad.append("env -S")
            if o in ("-u", "-C", "-S", "--unset", "--chdir", "--split-string"):
                j += 2
            elif o.startswith("-") or ASSIGN_RE.match(o):
                j += 1
            else:
                break
    return bad


def _risky(w, exempt=()):
    """The risky words of one command on a git line."""
    bad = []
    for k, a in enumerate(w):
        if _is_no_verify(a):
            bad.append(NO_VERIFY)
        if "hookspath" in a.lower():
            bad.append("core.hooksPath")
        m = GUARDED_WORD.match(a)
        if m:
            bad.append("AGENTDOCS_ALLOW" if m.group(1) == "AGENTDOCS_ALLOW" else "env " + m.group(1))
        if k not in exempt:
            for key in KEY_RE.findall(a):
                bad.append("git " + key.lower() + ".*")
    if "commit" in w and any(SHORT_N.match(a) for a in w):
        bad.append("commit -n")
    return bad + _env_reasons(w)


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


def _commands(cmd):
    """(words, is_data, spliced, text) per simple command; Unreadable when the line cannot be split. A command whose
    output is spliced into another (`$(echo ...)`) is never data."""
    out = []
    for seg, spliced in split_segments(cmd):
        try:
            w = shlex.split(seg, comments=True)
        except ValueError:
            raise Unreadable()
        k = 0
        while k < len(w) and ASSIGN_RE.match(w[k]):
            k += 1
        out.append((w, not spliced and k < len(w) and _base(w[k]) in DATA_CMDS, spliced, seg))
    return out


def _leading_assigns(w):
    k = 0
    while k < len(w) and ASSIGN_RE.match(w[k]):
        k += 1
    return w[:k]


def _raw_reasons(cmd):
    """Fail closed for a line that cannot be read: its raw words, with every quote and separator as a space."""
    words = [x for x in re.split(r"[\s;&|()`'\"<>{}]+", cmd) if x]
    bad = []
    if "orch" in [_base(x) for x in words] and (
            ("docs" in words and "disable" in words) or ("agent_docs" in words and "off" in words)):
        bad.append("orch docs disable")
    if any(_is_git(x) for x in words):
        bad += _risky(words)
        if NO_VERIFY_SUB.search(cmd):
            bad.append(NO_VERIFY)
    elif any(ALLOW_ASSIGN.match(x) for x in words):
        bad.append("AGENTDOCS_ALLOW")
    return bad + [UNPARSED] if bad else []


def _reasons(cmd, depth):
    try:
        cmds = _commands(cmd)
    except Unreadable:
        return _raw_reasons(cmd)
    bad = []
    git_line = False
    for w, data, _, _ in cmds:
        for k, a in enumerate(w):
            if "$(" in a or "`" in a:       # a command substitution left inside a quoted word still runs
                bad += _nested(a, depth)
        if data:    # only its leading assignments act
            w = _leading_assigns(w)
        if any(_is_git(a) for a in w):
            git_line = True
        for k, a in enumerate(w):
            if ALLOW_ASSIGN.match(a):
                bad.append("AGENTDOCS_ALLOW")
            if _base(a) == "orch":
                bad += _orch_reasons(w[k + 1:])
            elif _base(a) in EVALUATORS:        # bash -c '...', eval '...': read each later word as a command line
                for s in w[k + 1:]:
                    bad += _nested(s, depth)
            elif _base(a) == "env":          # env -S 'A=1 git' ...: the string is split into words and run
                for j in range(k + 1, len(w)):
                    o = w[j]
                    if o in ("-S", "--split-string"):
                        bad += _nested(" ".join(w[j + 1:j + 2]) + " " + shlex.join(w[j + 2:]), depth)
                        break
                    if o.startswith(("-S", "--split-string=")):
                        head = o.split("=", 1)[1] if o.startswith("--") else o[2:]
                        bad += _nested(head + " " + shlex.join(w[j + 1:]), depth)
                        break
    if git_line:
        bad += _indirect(cmds)
        for w, data, _, _ in cmds:
            if data:
                w = _leading_assigns(w)
            ev = next((k for k, a in enumerate(w) if _base(a) in EVALUATORS), None)
            if ev is not None:      # `eval 'unset HOME'; git ...` acts on this shell: read its strings' words too
                w = w + [x for s in w[ev + 1:] for x in re.split(r"[\s;&|()`'\"]+", s) if x]
            bad += _risky(w, _config_read_keys(w))
    return bad


def _indirect(cmds):
    """On a git line, risky text that reaches git through another word: matched in the raw text of every command
    that is not data (quotes, assignment values and substitutions included; heredoc bodies and comments are already
    gone), plus `-n` from an assignment value or a substitution when the line has a `commit` word."""
    bad, short_n, commit = [], False, False
    for w, data, spliced, text in cmds:
        if data:
            w = _leading_assigns(w)
            text = " ".join(w)
        commit = commit or "commit" in w
        if NO_VERIFY_SUB.search(text):
            bad.append(NO_VERIFY)
        if "hookspath" in text.lower():
            bad.append("core.hooksPath")
        if not _config_read_keys(w):
            bad += ["git " + key.lower() + ".*" for key in KEY_SUB.findall(text)]
        vals = [x for a in w if ASSIGN_RE.match(a) for x in a.split("=", 1)[1].split()]
        short_n = short_n or any(SHORT_N.match(x) for x in vals + (w if spliced else []))
    if commit and short_n:
        bad.append("commit -n")
    return bad


def _nested(text, depth):
    if depth >= MAX_DEPTH:
        return [UNPARSED] if ("git" in text or "orch" in text) else []
    return _reasons(text, depth + 1)


def reasons(cmd):
    """The sorted, de-duplicated reasons a Bash command line is refused (empty = allowed)."""
    return sorted(set(_reasons(cmd, 0)))


def message(bad):
    switch = [b for b in bad if b.startswith("orch ")]
    hooks = [b for b in bad if not b.startswith("orch ")]
    parts = []
    if hooks:
        parts.append("Blocked (%s): on a line that runs git, this can bypass or loosen the repo hooks (orch docs / "
                     "gitleaks). Fix what the hook reports; agent docs belong in the private store (orch docs adopt "
                     "<path>)." % ", ".join(hooks))
    if switch:
        parts.append("Blocked (%s): turning the agent-docs switch off is the user's decision." % ", ".join(switch))
    return " ".join(parts) + (" The guard reads command lines coarsely and may refuse a harmless one; if so, or if the "
                              "user wants an exception, they run it themselves.")
