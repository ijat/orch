"""`orch hook delegate`: a PreToolUse hook that sends repo-wide searches to orch-explorer.

Reads the PreToolUse JSON on stdin; prints the deny JSON or nothing (allow). Always exits 0 and fails open:
a parse error, a missing binary, a `stats` timeout or any other surprise allows the call.

Enforced only for the roles in ENFORCED. A call is denied when a search target is inside the agent's git work
tree, the search is broad (a directory, or no path at all), and the repo has a built index (`orch-index stats`
reports a commit and files > 0). A Bash command carrying `# orch-self-search: <reason>` is allowed and logged
as a bypass. Every enforced decision that matters is appended to `<orch data>/logs/delegate-hook.jsonl`, never
with the command text."""
import json
import os
import re
import subprocess
import sys
import zlib

from .paths import agent_key, data_bin, logs_dir

ENFORCED = ("orch-implementer", "orch-code-reviewer")
REASON = ("orch: this repo has a code index — send repo-wide searches to orch:explorer "
          "(batch your questions; ask for file:line). Searching one file is fine.")
HATCH_HINT = ('If a Bash search must run itself, add "# orch-self-search: <reason>" to the command '
              "(logged). Grep and Glob have no such marker: use Bash with it.")
MARKER_RE = re.compile(r"#\s*orch-self-search:\s*\S")
STATS_TIMEOUT = 2
GIT_TIMEOUT = 2
WILDCARD_RE = re.compile(r"[*?\[{]")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PUNCT = ";&|()<>\n"
SEPARATORS = (";", "&&", "||", "&", "\n", ";;", "(", ")")
PIPES = ("|", "|&")
WRAPPERS = ("env", "time", "command", "exec", "nohup", "builtin")

RG_SHORT = frozenset("efgtTABCmjdErM")
RG_LONG = frozenset("""--regexp --file --glob --iglob --type --type-not --type-add --type-clear --after-context
--before-context --context --max-count --max-depth --maxdepth --max-filesize --max-columns --threads --encoding
--replace --sort --sortr --pre --pre-glob --ignore-file --path-separator --engine --colors --color
--context-separator --field-context-separator --field-match-separator --dfa-size-limit --regex-size-limit
--hostname-bin --hyperlink-format""".split())
GREP_SHORT = frozenset("efmABCdD")
GREP_LONG = frozenset("""--regexp --file --max-count --after-context --before-context --context --directories
--devices --include --exclude --exclude-from --exclude-dir --label --binary-files""".split())
AG_SHORT = frozenset("ABCGmpW")
AG_LONG = frozenset("""--after --before --context --file-search-regex --max-count --path-to-ignore --width
--ignore --depth --workers --pager""".split())
GITGREP_SHORT = frozenset("efABCm")
GITGREP_LONG = frozenset("""--file --regexp --after-context --before-context --context --max-count --max-depth
--threads""".split())
FIND_LEADING = ("-H", "-L", "-P", "-E", "-X", "-x", "-s", "-d", "-D0", "-O0", "-O1", "-O2", "-O3")


# -- small helpers -------------------------------------------------------------------------------------

def _real(path):
    try:
        return os.path.realpath(path)
    except Exception:
        return path


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _resolve(raw, cwd):
    raw = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(raw):
        raw = os.path.join(cwd, raw)
    return _real(os.path.normpath(raw))


def _dirs_of(raw, cwd):
    """The existing directories a path argument names (a shell glob expands to its matches)."""
    full = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(full):
        full = os.path.join(cwd, full)
    if WILDCARD_RE.search(full):
        import glob  # lazy: this module is loaded on every tool call
        cands = glob.glob(full)
    else:
        cands = [full]
    return [_real(c) for c in cands if os.path.isdir(c)]


# -- Bash command parsing ------------------------------------------------------------------------------

def _tokens(cmd):
    import shlex
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=PUNCT)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return list(lex)


def _segments(tokens):
    """Commands of a shell line: split on ; && || & newline ( ); keep only what comes before a pipe; drop
    redirections with their target."""
    segs, cur, piped, skip = [], [], False, False
    for t in tokens:
        if t in SEPARATORS:
            if cur:
                segs.append(cur)
            cur, piped, skip = [], False, False
            continue
        if t in PIPES:
            piped = True
            continue
        if piped:
            continue
        if skip:
            skip = False
            continue
        if t and all(c in PUNCT for c in t):  # >, >>, <, >&, <<<
            skip = True
            if cur and cur[-1].isdigit():  # the fd of 2>&1
                cur.pop()
            continue
        cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _parse(args, short_val, long_val):
    """Split options from positionals. Returns (flags [(name, value|None)], positionals, index of `--` in the
    positionals or None)."""
    flags, pos, dd, i, after = [], [], None, 0, False
    while i < len(args):
        a = args[i]
        i += 1
        if after:
            pos.append(a)
        elif a == "--":
            after, dd = True, len(pos)
        elif a.startswith("--"):
            name, eq, val = a.partition("=")
            if not eq and name in long_val and i < len(args):
                val, i = args[i], i + 1
            flags.append((name, val if (eq or name in long_val) else None))
        elif a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                c = a[j]
                if c in short_val:
                    rest = a[j + 1:]
                    if rest:
                        val = rest
                    elif i < len(args):
                        val, i = args[i], i + 1
                    else:
                        val = ""
                    flags.append(("-" + c, val))
                    break
                flags.append(("-" + c, None))
                j += 1
        else:
            pos.append(a)
    return flags, pos, dd


def _has(flags, *names):
    return any(n in names for n, _ in flags)


def _broad_paths(paths, cwd, repo):
    """paths: raw path arguments. No path -> the cwd. Broad when a directory (or the cwd) lies in the repo."""
    if not paths:
        return _inside(_real(cwd), repo)
    for p in paths:
        for d in _dirs_of(p, cwd):
            if _inside(d, repo):
                return True
    return False


def _classify_rg(args, cwd, repo, short_val=RG_SHORT, long_val=RG_LONG):
    flags, pos, _ = _parse(args, short_val, long_val)
    if _has(flags, "--help", "-h", "--version", "-V", "--type-list"):
        return None
    if _has(flags, "-e", "--regexp", "-f", "--file", "--files"):
        paths = pos
    elif pos:
        paths = pos[1:]
    else:
        return None
    return _broad_paths(paths, cwd, repo)


def _grep_recursive(flags):
    for name, val in flags:
        if name in ("-r", "-R", "--recursive", "--dereference-recursive"):
            return True
        if name in ("-d", "--directories") and val == "recurse":
            return True
    return False


def _classify_grep(args, cwd, repo):
    flags, pos, _ = _parse(args, GREP_SHORT, GREP_LONG)
    if not _grep_recursive(flags):
        return None
    if _has(flags, "-e", "--regexp", "-f", "--file"):
        paths = pos
    elif pos:
        paths = pos[1:]
    else:
        return None
    return _broad_paths(paths, cwd, repo)


def _classify_git(args, cwd, repo):
    """args: everything after `git`. Returns (cwd', broad|None) via a tuple so `-C` is followed."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            nxt = os.path.expandvars(os.path.expanduser(args[i + 1]))
            cwd = _real(os.path.normpath(nxt if os.path.isabs(nxt) else os.path.join(cwd, nxt)))
            i += 2
        elif a in ("-c", "--namespace", "--git-dir", "--work-tree", "--super-prefix", "--config-env") and i + 1 < len(args):
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            break
    if i >= len(args) or args[i] != "grep":
        return None
    flags, pos, dd = _parse(args[i + 1:], GITGREP_SHORT, GITGREP_LONG)
    if _has(flags, "-e", "--regexp", "-f", "--file"):
        rest, rest_dd = pos, dd
    elif pos:
        rest, rest_dd = pos[1:], (None if dd is None else max(dd - 1, 0))
    else:
        return None
    explicit = []
    if any(WILDCARD_RE.search(p) for p in rest):  # a pathspec glob: git expands it, not the shell
        return _inside(_real(cwd), repo)
    for k, p in enumerate(rest):
        after_dd = rest_dd is not None and k >= rest_dd
        full = os.path.expandvars(os.path.expanduser(p))
        full = full if os.path.isabs(full) else os.path.join(cwd, full)
        if os.path.exists(full) or after_dd:
            explicit.append(p)
    if not explicit:
        return _inside(_real(cwd), repo)
    return _broad_paths(explicit, cwd, repo)


def _classify_find(args, cwd, repo):
    i = 0
    while i < len(args) and args[i] in FIND_LEADING:
        i += 1
    paths = []
    while i < len(args) and not args[i].startswith("-") and args[i] not in ("!", ","):
        paths.append(args[i])
        i += 1
    return _broad_paths(paths, cwd, repo)


def classify_command(cmd, cwd, repo):
    """Returns the search form of the first broad search in a Bash command ('rg', 'grep', 'git grep', 'find',
    'ag'), or None."""
    segs = _segments(_tokens(cmd))
    cur = cwd
    for seg in segs:
        k = 0
        while k < len(seg) and (ENV_ASSIGN_RE.match(seg[k]) or seg[k] in WRAPPERS):
            k += 1
        seg = seg[k:]
        if not seg:
            continue
        name = os.path.basename(seg[0])
        args = seg[1:]
        if name == "cd":
            if args and not args[0].startswith("-"):
                cur = _resolve(args[0], cur)
            continue
        if name == "rg":
            broad, form = _classify_rg(args, cur, repo), "rg"
        elif name in ("ag", "ack"):
            broad, form = _classify_rg(args, cur, repo, AG_SHORT, AG_LONG), "ag"
        elif name in ("grep", "egrep", "fgrep"):
            broad, form = _classify_grep(args, cur, repo), "grep"
        elif name == "git":
            broad, form = _classify_git(args, cur, repo), "git grep"
        elif name == "find":
            broad, form = _classify_find(args, cur, repo), "find"
        else:
            continue
        if broad:
            return form
    return None


def classify(tool, tool_input, cwd, repo):
    """Returns the form of a broad search inside `repo`, or None."""
    if tool == "Bash":
        cmd = tool_input.get("command")
        return classify_command(cmd, cwd, repo) if isinstance(cmd, str) else None
    if tool == "Grep":
        path = tool_input.get("path")
        if not path:
            return "Grep" if _inside(_real(cwd), repo) else None
        full = _resolve(path, cwd)
        return "Grep" if os.path.isdir(full) and _inside(full, repo) else None
    if tool == "Glob":
        pattern = tool_input.get("pattern")
        if not isinstance(pattern, str) or not WILDCARD_RE.search(pattern):
            return None
        base = tool_input.get("path") or cwd
        if os.path.isabs(os.path.expanduser(pattern)):
            full = os.path.expanduser(pattern)
        else:
            full = os.path.join(_resolve(base, cwd), pattern)
        keep = []
        for comp in full.split(os.sep):
            if WILDCARD_RE.search(comp):
                break
            keep.append(comp)
        prefix = _real(os.path.normpath(os.sep.join(keep) or os.sep))
        return "Glob" if _inside(prefix, repo) else None
    return None


# -- index check ---------------------------------------------------------------------------------------

def _stat_key(path):
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def _binary():
    env = os.environ.get("ORCH_INDEX_BIN")
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d, "orch-index")
        if d and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    home = os.path.join(data_bin(), "orch-index")
    return home if os.path.isfile(home) and os.access(home, os.X_OK) else None


def _parse_stats(text):
    commit, files = None, 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "commit":
            commit = parts[1]
        elif len(parts) >= 2 and parts[0] == "files":
            try:
                files = int(parts[1])
            except ValueError:
                files = 0
    return bool(commit) and commit != "(none)" and files > 0


def index_exists(common_dir, repo):
    """D5: `stats` reports a commit other than (none) and files > 0. The db file must exist first (`stats`
    would create an empty one). The answer is cached in $TMPDIR (or /tmp), keyed by the db and wal mtime and size."""
    db = os.path.join(common_dir, "orch-index", "index.db")
    key = [_stat_key(db), _stat_key(db + "-wal")]
    if key[0] is None:
        return False
    name = re.sub(r"[^A-Za-z0-9]+", "_", os.path.basename(os.path.dirname(common_dir)))[:40]
    cache = os.path.join(os.environ.get("TMPDIR") or "/tmp",
                         "orch-delegate-%08x-%s.json" % (zlib.crc32(common_dir.encode("utf-8", "replace")), name))
    try:
        with open(cache, "r") as f:
            data = json.load(f)
        if data.get("key") == key and data.get("common") == common_dir and isinstance(data.get("index"), bool):
            return data["index"]
    except Exception:
        pass
    binary = _binary()
    if not binary:
        return False
    try:
        res = subprocess.run([binary, "stats", "--repo", repo], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=STATS_TIMEOUT)
    except Exception:
        return False
    if res.returncode != 0:
        return False
    answer = _parse_stats(res.stdout)
    try:
        tmp = "%s.%d" % (cache, os.getpid())
        with open(tmp, "w") as f:
            json.dump({"key": key, "common": common_dir, "index": answer}, f)
        os.replace(tmp, cache)
    except Exception:
        pass
    return answer


# -- decision + log ------------------------------------------------------------------------------------

def _repo_of(cwd):
    try:
        res = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel", "--path-format=absolute",
                              "--git-common-dir"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=GIT_TIMEOUT)
    except Exception:
        return None
    lines = res.stdout.splitlines()
    if res.returncode != 0 or len(lines) < 2:
        return None
    return _real(lines[0]), lines[1]


def _log(decision, payload, tool, form, repo):
    """One JSON line, never the command text. Never raises."""
    try:
        import datetime
        root = logs_dir()
        os.makedirs(root, exist_ok=True)
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "decision": decision, "agent_type": payload.get("agent_type"), "tool": tool, "form": form,
               "repo": repo, "session_id": payload.get("session_id")}
        with open(os.path.join(root, "delegate-hook.jsonl"), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except Exception:
        pass


def decide(payload):
    """Returns the deny dict, or None to allow."""
    if not isinstance(payload, dict) or agent_key(payload.get("agent_type")) not in ENFORCED:
        return None
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool not in ("Bash", "Grep", "Glob") or not isinstance(tool_input, dict):
        return None
    cwd = payload.get("cwd") or os.getcwd()
    if not os.path.isdir(cwd):
        return None
    where = _repo_of(cwd)
    if not where:
        return None
    repo, common = where
    form = classify(tool, tool_input, cwd, repo)
    if not form:
        return None
    if not index_exists(common, repo):
        _log("allow-broad-no-index", payload, tool, form, repo)
        return None
    cmd = tool_input.get("command") if tool == "Bash" else None
    if isinstance(cmd, str) and MARKER_RE.search(cmd):
        _log("bypass", payload, tool, form, repo)
        return None
    _log("deny", payload, tool, form, repo)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": REASON + "\n" + HATCH_HINT}}


def run(stdin=None, stdout=None):
    """The hook: always returns 0."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        payload = json.loads(stdin.read())
        out = decide(payload)
        if out is not None:
            stdout.write(json.dumps(out) + "\n")
            stdout.flush()
    except BaseException:  # fail open
        pass
    return 0


def cmd_hook(ns, extra):
    return run()


def register(add):
    sp = add("hook", cmd_hook, "PreToolUse hooks (hook delegate: send repo-wide searches to orch:explorer)")
    sp.add_argument("which", choices=["delegate"])
