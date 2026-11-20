"""orch docs <verb>: keep a repo's agent/internal docs out of git, in a private store on this machine.

The store (paths.docs_store()) is a local git repo with one directory per repo:
  <store>/<name>/            the repo's private files, at their repo-relative paths
  <store>/<name>/.links      paths symlinked into the checkout (one per line)
  <store>/<name>/.origin     the repo's origin URL when it was registered
In the checkout `.agent` points at <store>/<name> and each path in .links is a symlink into `.agent/`.
The layout and the agentdocs.* git-config keys are the ones the older shell tool used, so existing entries keep working.
Commands here work whatever the `agent_docs` setting says: it only gates orch's automatic behaviour, not these verbs.
"""
import ast
import contextlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import types

from .paths import OrchError, docs_store, private_dir

PREFIX = "orch docs"
STORE_README = "# Private agent docs store (managed by `orch docs`). Never push to a public remote.\n"
DENY_PATHS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs_deny_paths.txt")


def say(msg):
    print("%s: %s" % (PREFIX, msg))


# -- git plumbing --------------------------------------------------------------------------------

def git(cwd, *args, check=False):
    """Run git in cwd with an argument list (never a shell string); returns the CompletedProcess (text)."""
    # No -C when cwd is where we already are: inside a git hook GIT_DIR / GIT_INDEX_FILE can be relative to it.
    pre = ["-C", cwd] if cwd and os.path.abspath(cwd) != os.getcwd() else []
    try:
        res = subprocess.run(["git"] + pre + list(args), capture_output=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OrchError("cannot run git: %s" % exc, 1)
    if check and res.returncode != 0:
        raise OrchError("git %s failed: %s" % (" ".join(args[:2]), (res.stderr or res.stdout).strip()), 1)
    return res


def repo_root(cwd):
    res = git(cwd, "rev-parse", "--show-toplevel")
    if res.returncode != 0 or not res.stdout.strip():
        raise OrchError("not in a git repo", 1)
    return res.stdout.strip()


def common_dir(cwd):
    res = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if res.returncode != 0 or not res.stdout.strip():
        raise OrchError("not in a git repo", 1)
    return res.stdout.strip()


def main_checkout(cwd):
    return os.path.dirname(common_dir(cwd))


def origin_url(cwd):
    res = git(cwd, "remote", "get-url", "origin")
    return res.stdout.strip() if res.returncode == 0 else ""


def norm_url(url):
    """github.com:o/r.git, https://github.com/o/r(.git) -> github.com/o/r (lowercase)."""
    u = re.sub(r"^(https?://|ssh://)?(git@)?", "", url or "")
    u = u.replace(":", "/", 1)
    u = re.sub(r"\.git$", "", u)
    u = re.sub(r"/$", "", u)
    return u.lower()


def exclude_local(cwd, path):
    """Add /<path> to <git-common-dir>/info/exclude unless present."""
    f = os.path.join(common_dir(cwd), "info", "exclude")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    line = "/" + path
    text = read_text(f) or ""
    if line in text.split("\n"):
        return
    with open(f, "a", encoding="utf-8") as fh:
        if text and not text.endswith("\n"):
            fh.write("\n")
        fh.write(line + "\n")


def rel_to_agent(path):
    """The relative symlink target from <path> to .agent/<path>."""
    return "../" * (len(path.split("/")) - 1) + ".agent/" + path


# -- the store -----------------------------------------------------------------------------------

def store_init(store):
    os.makedirs(store, exist_ok=True)
    if not os.path.isdir(os.path.join(store, ".git")):
        git(store, "init", "-q", "-b", "main", check=True)
        with open(os.path.join(store, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(STORE_README)
        git(store, "config", "agentdocs.allow", "true", check=True)
        git(store, "add", "README.md", check=True)
        git(store, "commit", "-q", "-m", "store: init", check=True)


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (FileNotFoundError, NotADirectoryError):
        return None


def repo_name(cwd, store):
    """The repo's store name: from an existing .agent link (this checkout, then the main one), else the store entry
    whose .origin matches this repo's origin, else the main checkout's basename."""
    root = repo_root(cwd)
    for base in (root, main_checkout(cwd)):
        link = os.path.join(base, ".agent")
        if os.path.islink(link):
            return os.path.basename(os.readlink(link).rstrip("/"))
    origin = norm_url(origin_url(cwd))
    if origin and os.path.isdir(store):
        for entry in sorted(os.listdir(store)):
            text = read_text(os.path.join(store, entry, ".origin"))
            if text is not None and norm_url(text.rstrip("\n")) == origin:
                return entry
    return os.path.basename(main_checkout(cwd))


def valid_name(name):
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise OrchError("invalid store entry name %r" % name, 1)
    return name


def clean_path(root, p):
    """A repo-relative path from the user's argument: no trailing slash, no leading ./, never outside root."""
    if os.path.isabs(p):
        p = os.path.relpath(p, root)
    p = p.rstrip("/")
    while p.startswith("./"):
        p = p[2:]
    parts = p.split("/")
    if not p or p == "." or ".." in parts or "" in parts:
        raise OrchError("%r is not a path inside the repo" % p, 1)
    return p


def links_of(entry):
    return [ln for ln in (read_text(os.path.join(entry, ".links")) or "").split("\n") if ln]


def add_link_entry(entry, p):
    path = os.path.join(entry, ".links")
    text = read_text(path) or ""
    if p in text.split("\n"):
        return
    with open(path, "a", encoding="utf-8") as fh:
        if text and not text.endswith("\n"):
            fh.write("\n")
        fh.write(p + "\n")


def entry_of_root(root):
    """The store entry dir the checkout's .agent link points at (None when it is not a link)."""
    link = os.path.join(root, ".agent")
    return os.readlink(link) if os.path.islink(link) else None


def put_link(root, p):
    full = os.path.join(root, p)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    os.symlink(rel_to_agent(p), full)


def point_agent_link(root, entry):
    """ln -sfn: (re)point <root>/.agent at the entry; refuse a real file or directory in its place."""
    link = os.path.join(root, ".agent")
    if os.path.lexists(link) and not os.path.islink(link):
        raise OrchError("%s exists and is not a symlink" % link, 1)
    if os.path.islink(link):
        os.unlink(link)
    os.symlink(entry, link)


def ensure_entry(ctx, root):
    """The entry dir of this checkout, running a silent init when .agent is not there yet."""
    entry = entry_of_root(root)
    if entry is None:
        with open(os.devnull, "w") as null, contextlib.redirect_stdout(null):
            cmd_init(ctx)
        entry = entry_of_root(root)
    return entry


# -- verbs ---------------------------------------------------------------------------------------

def cmd_init(ctx):
    store = docs_store()
    store_init(store)
    root = repo_root(ctx.cwd)
    name = valid_name(ctx.name) if ctx.name else valid_name(repo_name(ctx.cwd, store))
    entry = os.path.join(store, name)
    origin = origin_url(ctx.cwd)
    known = read_text(os.path.join(entry, ".origin"))
    if known is not None and origin and known.rstrip("\n") != origin:
        raise OrchError("store entry '%s' belongs to %s; pick another: orch docs init --name <name>"
                        % (name, known.rstrip("\n")), 1)
    if os.path.lexists(os.path.join(root, ".agent")) and not os.path.islink(os.path.join(root, ".agent")):
        raise OrchError("%s exists and is not a symlink" % os.path.join(root, ".agent"), 1)
    os.makedirs(entry, exist_ok=True)
    open(os.path.join(entry, ".links"), "a").close()
    if origin:
        with open(os.path.join(entry, ".origin"), "w", encoding="utf-8") as fh:
            fh.write(origin + "\n")
    point_agent_link(root, entry)
    exclude_local(ctx.cwd, ".agent")
    say("%s -> %s" % (os.path.join(root, ".agent"), entry))
    return 0


def cmd_adopt(ctx):
    if not ctx.paths:
        raise OrchError("usage: orch docs adopt <path>...", 1)
    root = repo_root(ctx.cwd)
    entry = ensure_entry(ctx, root)
    for raw in ctx.paths:
        p = clean_path(root, raw)
        full = os.path.join(root, p)
        if p == ".agent" or p.startswith(".agent/"):
            say("%s is the store link itself, skipped" % p)
            continue
        if os.path.islink(full):
            say("%s is already a link, skipped" % p)
            continue
        if not os.path.exists(full):
            say("%s does not exist, skipped" % p)
            continue
        if os.path.lexists(os.path.join(entry, p)):
            raise OrchError("%s already exists in the store; merge it by hand first" % os.path.join(entry, p), 1)
        if git(root, "ls-files", "--", p).stdout.strip():
            git(root, "rm", "-r", "-q", "--cached", "--", p, check=True)
        os.makedirs(os.path.dirname(os.path.join(entry, p)), exist_ok=True)
        shutil.move(full, os.path.join(entry, p))
        os.symlink(rel_to_agent(p), full)
        add_link_entry(entry, p)
        exclude_local(ctx.cwd, p)
        say("adopted %s" % p)
    return 0


def cmd_add(ctx):
    """Link a file that already exists in the store into the checkout and record it in .links."""
    if not ctx.paths:
        raise OrchError("usage: orch docs add <path>...", 1)
    root = repo_root(ctx.cwd)
    entry = ensure_entry(ctx, root)
    rc = 0
    for raw in ctx.paths:
        p = clean_path(root, raw)
        full = os.path.join(root, p)
        if not os.path.lexists(os.path.join(entry, p)):
            say("%s is not in the store, skipped" % p)
            rc = 1
            continue
        if os.path.islink(full):
            say("%s is already a link" % p)
        elif os.path.exists(full):
            say("%s exists as a real file; not replaced (orch docs adopt it, or remove it)" % p)
            rc = 1
            continue
        else:
            put_link(root, p)
            say("linked %s" % p)
        add_link_entry(entry, p)
        exclude_local(ctx.cwd, p)
    return rc


def cmd_link(ctx):
    store = docs_store()
    root = repo_root(ctx.cwd)
    name = repo_name(ctx.cwd, store)
    entry = os.path.join(store, name)
    if not os.path.isdir(entry):
        say("no store entry '%s' (orch docs init to create one)" % name)
        return 0
    point_agent_link(root, entry)
    exclude_local(ctx.cwd, ".agent")
    # A worktree nested inside the main checkout (orch's .claude/worktrees/*) already gets the main checkout's
    # CLAUDE.md/AGENTS.md as ancestors; linking them again would load them twice.
    mc = os.path.realpath(main_checkout(ctx.cwd)).rstrip("/")
    nested = os.path.realpath(root).startswith(mc + "/")
    for p in links_of(entry):
        if nested and p in ("CLAUDE.md", "AGENTS.md"):
            continue
        parts = p.split("/")
        if os.path.isabs(p) or ".." in parts or "" in parts:
            say("%s in .links is not a repo-relative path, skipped" % p)
            continue
        full = os.path.join(root, p)
        if os.path.islink(full):
            continue
        if os.path.exists(full):
            say("%s exists as a real file; not replaced (orch docs adopt it, or remove it)" % p)
            continue
        put_link(root, p)
        exclude_local(ctx.cwd, p)
        say("linked %s" % p)
    return 0


def pattern_lines(text):
    """The pattern lines of a list file: everything but blank lines and # comments."""
    return [ln for ln in (text or "").split("\n") if ln.strip() and not ln.lstrip().startswith("#")]


_POSIX_CLASSES = {
    "alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z", "blank": " \\t",
    "space": " \\t\\n\\r\\f\\v", "xdigit": "0-9A-Fa-f", "punct": "!-/:-@\\[-`{-~", "word": "a-zA-Z0-9_",
}


def ere_to_re(pat):
    """Compile one POSIX extended regex line with Python's re: POSIX bracket classes and \\< \\> are translated.
    Raises re.error when it does not compile (callers fail closed)."""
    pat = re.sub(r"\[:(\w+):\]", lambda m: _POSIX_CLASSES.get(m.group(1), m.group(0)), pat)
    pat = pat.replace("\\<", "\\b").replace("\\>", "\\b")
    return re.compile(pat)


def compile_list(lines):
    """(regexes, number of lines that did not compile)."""
    out, bad = [], 0
    for ln in lines:
        try:
            out.append(ere_to_re(ln))
        except re.error:
            bad += 1
    return out, bad


def deny_paths_file():
    """The optional private list that adds path patterns (absent = just the shipped list)."""
    return os.path.join(private_dir(), "agent-docs-deny-paths.txt")


def deny_content_file():
    """The private list of strings that must not enter a repo (absent = the content check is off)."""
    return os.path.join(private_dir(), "agent-docs-deny-content.txt")


def deny_path_lines():
    return pattern_lines(read_text(DENY_PATHS_FILE)) + pattern_lines(read_text(deny_paths_file()))


def deny_path_regexes():
    return compile_list(deny_path_lines())[0]


def cmd_status(ctx):
    store = docs_store()
    root = repo_root(ctx.cwd)
    name = repo_name(ctx.cwd, store)
    entry = os.path.join(store, name)
    print("repo:  %s" % root)
    print("store: %s%s" % (entry, "" if os.path.isdir(entry) else " (none)"))
    link = os.path.join(root, ".agent")
    print(".agent -> %s" % os.readlink(link) if os.path.islink(link) else ".agent: missing")
    if os.path.isfile(os.path.join(entry, ".links")):
        for p in links_of(entry):
            full = os.path.join(root, p)
            if os.path.islink(full) and os.path.exists(full):
                print("  ok      %s" % p)
            elif os.path.islink(full):
                print("  BROKEN  %s" % p)
            else:
                print("  MISSING %s" % p)
    pats = deny_path_regexes()
    tracked = [f for f in git(root, "ls-files", "-z").stdout.split("\0") if f and any(r.search(f) for r in pats)]
    if tracked:
        print("private paths still tracked:")
        for f in tracked:
            print("  %s" % f)
    if git(root, "config", "--bool", "agentdocs.allow").stdout.strip() == "true":
        print("(exempt: agentdocs.allow=true)")
    if read_text(deny_content_file()) is None:
        sys.stderr.write("%s: no private content list at %s; the content check is off (the path check is on)\n"
                         % (PREFIX, deny_content_file()))
    return 0


def cmd_list(ctx):
    store = docs_store()
    store_init(store)
    for name in sorted(os.listdir(store)):
        d = os.path.join(store, name)
        if name.startswith(".") or not os.path.isdir(d):
            continue
        print("%-28s %s" % (name, (read_text(os.path.join(d, ".origin")) or "").rstrip("\n")))
    return 0


def cmd_commit(ctx):
    store = docs_store()
    store_init(store)
    git(store, "add", "-A", check=True)
    if git(store, "diff", "--cached", "--quiet").returncode == 0:
        say("store clean")
        return 0
    git(store, "commit", "-q", "-m", ctx.message or "update", check=True)
    print(git(store, "log", "-1", "--oneline").stdout.rstrip("\n"))
    return 0


def cmd_migrated(ctx):
    root = repo_root(ctx.cwd)
    git(root, "config", "--unset", "agentdocs.mode")   # an unset key is fine: the repo already blocks
    say("%s now blocks" % root)
    return 0


# -- check ---------------------------------------------------------------------------------------

CHECK_USAGE = "usage: orch docs check staged | range -- <git rev args>... | tree [<rev>]"
LIST_MAX = 30
LINE_MAX = 200


def config_all(cwd, key):
    res = git(cwd, "config", "--get-all", key)
    return [ln for ln in res.stdout.split("\n") if ln] if res.returncode == 0 else []


def diff_added_lines(text):
    """[(path, line text)] for every added line of a `git diff -U0` / `git log -p` text (b/ side, fixed prefixes)."""
    out, cur, in_hunk = [], None, False
    for line in text.split("\n"):
        if line.startswith("diff --git ") or line.startswith("diff --cc ") or line.startswith("diff --combined "):
            cur, in_hunk = None, False
        elif line.startswith("@@"):
            in_hunk = True
        elif not in_hunk:
            if line.startswith("+++ "):
                name = line[4:]
                if name.startswith('"'):
                    try:
                        name = ast.literal_eval(name)
                    except (ValueError, SyntaxError):
                        pass
                cur = name[2:] if name.startswith("b/") else None
        elif line.startswith("+") and cur is not None:
            out.append((cur, line[1:]))
    return out


DIFF_FLAGS = ["-U0", "--diff-filter=ACMR", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/"]


def _names(text):
    return sorted({n.strip("\n") for n in text.split("\0") if n.strip("\n")})


def run_check(mode, args, cwd=None):
    """The path and content check. Returns 0 clean, 1 findings, 2 usage or list errors. Writes only to stderr."""
    err = sys.stderr
    if os.environ.get("AGENTDOCS_ALLOW") == "1":
        err.write("%s check: AGENTDOCS_ALLOW=1, skipped\n" % PREFIX)
        return 0
    if git(cwd, "config", "--bool", "agentdocs.allow").stdout.strip() == "true":
        return 0
    if mode not in ("staged", "range", "tree"):
        err.write(CHECK_USAGE + "\n")
        return 2
    if mode == "range" and not args:
        err.write(CHECK_USAGE + "\n")
        return 2
    rev = args[0] if args else "HEAD"
    if mode == "tree" and (rev.startswith("-") or len(args) > 1):
        err.write(CHECK_USAGE + "\n")
        return 2

    path_res, bad_paths_list = compile_list(deny_path_lines())
    content_lines = pattern_lines(read_text(deny_content_file()))   # absent = no content check
    content_res, bad_content_list = compile_list(content_lines)
    allow_path_res, _ = compile_list(config_all(cwd, "agentdocs.allowPath"))
    allow_content_res, _ = compile_list(config_all(cwd, "agentdocs.allowContent"))
    if bad_paths_list or bad_content_list:   # fail closed: a list that does not compile would silently check less
        err.write("%s check: %d pattern line(s) in the deny lists do not compile; fix them (the check refuses meanwhile)\n"
                  % (PREFIX, bad_paths_list + bad_content_list))
        return 2

    added = []          # "path:text"
    if mode == "staged":
        res = git(cwd, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
        paths = _names(res.stdout)
        added = ["%s:%s" % pt for pt in diff_added_lines(git(cwd, "diff", "--cached", *DIFF_FLAGS).stdout)]
    elif mode == "range":
        res = git(cwd, "log", "--format=", "--name-only", "-z", "--diff-filter=ACMR", *args)
        if res.returncode != 0:
            err.write("%s check: git log failed: %s\n" % (PREFIX, res.stderr.strip()))
            return 2
        paths = _names(res.stdout)
        added = ["%s:%s" % pt for pt in diff_added_lines(git(cwd, "log", "-p", "--format=", *(DIFF_FLAGS + list(args))).stdout)]
    else:
        res = git(cwd, "ls-tree", "-r", "--name-only", "-z", rev)
        if res.returncode != 0:
            err.write("%s check: git ls-tree failed: %s\n" % (PREFIX, res.stderr.strip()))
            return 2
        paths = _names(res.stdout)
        if content_lines:
            gargs = ["grep", "-I", "-n", "-z", "--no-color", "-E"]
            for ln in content_lines:
                gargs += ["-e", ln]
            res = git(cwd, *(gargs + [rev, "--"]))
            if res.returncode > 1:
                err.write("%s check: git grep failed: %s\n" % (PREFIX, res.stderr.strip()))
                return 2
            for rec in res.stdout.split("\n"):
                parts = rec.split("\0", 2)
                if len(parts) == 3:
                    path = parts[0][len(rev) + 1:] if parts[0].startswith(rev + ":") else parts[0]
                    added.append("%s:%s" % (path, parts[2]))

    bad_paths = [p for p in paths if any(r.search(p) for r in path_res) and not any(a.search(p) for a in allow_path_res)]
    if mode == "tree":
        bad_lines = [s for s in added if not any(a.search(s) for a in allow_content_res)]
    else:
        bad_lines = [s for s in added if any(r.search(s) for r in content_res) and not any(a.search(s) for a in allow_content_res)]
    if not bad_paths and not bad_lines:
        return 0

    err.write("%s: private agent docs or private strings would enter this repo.\n" % PREFIX)
    if bad_paths:
        err.write("  Agent/internal doc paths (keep them in the private store: orch docs adopt <path>):\n")
        for p in bad_paths[:LIST_MAX]:
            err.write("    %s\n" % p)
    if bad_lines:
        err.write("  Private strings (home paths, LAN IPs, VM/cluster names):\n")
        for ln in bad_lines[:LIST_MAX]:
            err.write("    %s\n" % ln[:LINE_MAX])
    err.write("  A repo that must keep its docs: git config agentdocs.allow true\n")
    err.write("  A legitimate string: git config --add agentdocs.allowContent '<regex for path:line>'\n")
    if git(cwd, "config", "agentdocs.mode").stdout.strip() == "warn":
        err.write("  (agentdocs.mode=warn: not blocked; migrate this repo with orch docs adopt, then orch docs migrated)\n")
        return 0
    return 1


def cmd_check(ctx):
    if not ctx.paths:
        sys.stderr.write(CHECK_USAGE + "\n")
        return 2
    return run_check(ctx.paths[0], ctx.paths[1:], ctx.cwd)


# -- git hooks -----------------------------------------------------------------------------------

HOOK_NAMES = ("pre-commit", "pre-push", "post-checkout")
SHIM_MARKER = "# orch docs hook"


def shim_text(name):
    fail = "exit 0" if name == "post-checkout" else (
        'echo "orch docs hook: orch is not installed (fix: claude plugin update orch@orch; for one commit or push: '
        'AGENTDOCS_ALLOW=1)" >&2\nexit 1')
    allow = "" if name == "post-checkout" else '[ "${AGENTDOCS_ALLOW:-0}" = 1 ] && exit 0\n'
    return ("#!/bin/sh\n%s\n"
            "# Written by `orch docs install-hooks`: runs the orch check, then chains to %s.pre-orch or the repo's own hook.\n"
            'ORCH="${ORCH_BIN:-$HOME/.claude/orch-data/plugin/bin/orch}"\n'
            '[ -x "$ORCH" ] || ORCH="$(command -v orch 2>/dev/null)"\n'
            'if [ -x "$ORCH" ]; then exec "$ORCH" docs hook %s "$@"; fi\n'
            "%s%s\n") % (SHIM_MARKER, name, name, allow, fail)


def is_shim(path):
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    text = read_text(path)
    return text is not None and SHIM_MARKER in text.split("\n")[:3]


def describe_hook(path):
    if os.path.islink(path):
        return "a symlink to %s" % os.readlink(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()[:80]
        return "a file of %d bytes starting with %r" % (os.path.getsize(path), first)
    except OSError:
        return "an entry that cannot be read"


def write_exec(path, text):
    tmp = path + ".orch-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)


def install_hooks(hooks_dir, chain_existing=False, dry_run=False, record=None):
    """Write the three shims into hooks_dir. A foreign hook of the same name is refused, or with chain_existing
    renamed to <name>.pre-orch. Returns the changes: [("wrote", path) | ("renamed", from, to)]. Nothing is changed
    when anything has to be refused. With dry_run nothing is changed at all; record(change) is called right after
    each change is made, so a caller can keep a record that survives a failure half way."""
    plan = []
    for name in HOOK_NAMES:
        path = os.path.join(hooks_dir, name)
        pre = path + ".pre-orch"
        if not os.path.lexists(path) or is_shim(path):
            plan.append((name, path, None))
            continue
        if not chain_existing:
            raise OrchError("%s is %s, not an orch hook; use --chain-existing to keep it as %s.pre-orch and chain to it"
                            % (path, describe_hook(path), name), 1)
        if os.path.lexists(pre):
            raise OrchError("%s already exists; cannot keep %s as %s.pre-orch" % (pre, path, name), 1)
        plan.append((name, path, pre))
    if not dry_run:
        os.makedirs(hooks_dir, exist_ok=True)
    changes = []

    def made(change):
        changes.append(change)
        if record and not dry_run:
            record(change)

    for name, path, pre in plan:
        if pre:
            if not dry_run:
                os.rename(path, pre)
            made(("renamed", path, pre))
        if pre or read_text(path) != shim_text(name) or not os.access(path, os.X_OK):
            if not dry_run:
                write_exec(path, shim_text(name))
            made(("wrote", path))
    return changes


def cmd_install_hooks(ctx):
    hooks_dir = os.path.abspath(ctx.hooks_dir) if ctx.hooks_dir else os.path.join(common_dir(ctx.cwd), "hooks")
    changes = install_hooks(hooks_dir, ctx.chain_existing)
    if not changes:
        say("hooks already installed in %s" % hooks_dir)
    for ch in changes:
        say("%s %s" % (ch[0], " -> ".join(ch[1:])))
    return 0


def chain_hook(name, args, cwd, stdin_data=None):
    """Run the hook this shim stands in front of: <name>.pre-orch beside it, else the repo's own under the git common
    dir; never the shim itself. Returns its exit code (0 when there is none)."""
    hooks = git(cwd, "rev-parse", "--git-path", "hooks").stdout.strip()
    shim = os.path.join(os.path.abspath(hooks), name) if hooks else None
    pre = shim + ".pre-orch" if shim else None
    if pre and os.path.lexists(pre):
        target = pre
    else:
        target = os.path.join(common_dir(cwd), "hooks", name)
    if not os.path.lexists(target):
        return 0
    if shim and os.path.realpath(target) == os.path.realpath(shim):
        return 0
    if not (os.path.isfile(target) and os.access(target, os.X_OK)):
        if name == "pre-push":   # present but not runnable: fail closed rather than skip the repo's checks
            sys.stderr.write("pre-push: %s exists but can't run (dangling link or not executable); refusing the push\n" % target)
            return 1
        return 0
    try:
        return subprocess.run([target] + list(args), input=stdin_data, encoding="utf-8", errors="replace").returncode
    except OSError as exc:
        sys.stderr.write("%s hook: cannot run %s: %s\n" % (PREFIX, target, exc))
        return 1


def hook_pre_push(args, cwd):
    data = sys.stdin.read()
    remote = args[0] if args else ""
    for line in data.split("\n"):
        f = line.split()
        if len(f) < 4:
            continue
        lref, lsha, rsha = f[0], f[1], f[3]
        if not re.fullmatch(r"[0-9a-f]{40,64}", lsha) or not re.fullmatch(r"[0-9a-f]{40,64}", rsha):
            sys.stderr.write("pre-push: unexpected input from git for %s; refusing the push\n" % lref)
            return 1
        if set(lsha) == {"0"}:    # deleting a ref
            continue
        if set(rsha) == {"0"} or git(cwd, "cat-file", "-e", rsha + "^{commit}").returncode != 0:
            revs = [lsha, "--not", "--remotes=%s" % remote]     # new ref: commits not on this remote yet
        else:
            revs = ["%s..%s" % (rsha, lsha)]
        if run_check("range", revs, cwd) != 0:
            sys.stderr.write("pre-push: refused (orch docs) for %s. Nothing was pushed.\n" % lref)
            return 1
    return chain_hook("pre-push", args, cwd, stdin_data=data)


def cmd_hook(ctx):
    if not ctx.paths or ctx.paths[0] not in HOOK_NAMES:
        raise OrchError("usage: orch docs hook pre-commit | pre-push <remote> <url> | post-checkout <old> <new> <flag>", 2)
    name, args = ctx.paths[0], ctx.paths[1:]
    if name == "pre-commit":
        if run_check("staged", [], ctx.cwd) != 0:
            return 1
        return chain_hook(name, args, ctx.cwd)
    if name == "pre-push":
        return hook_pre_push(args, ctx.cwd)
    if len(args) >= 3 and args[2] == "1":    # a branch checkout: a fresh clone or a new worktree gets its links
        try:
            sub = types.SimpleNamespace(dir=None, paths=[], name=None, message=None, hooks_dir=None, chain_existing=False)
            with open(os.devnull, "w") as null, contextlib.redirect_stdout(null), contextlib.redirect_stderr(null):
                cmd_link(Ctx(sub))
        except (Exception, SystemExit):
            pass
    return chain_hook(name, args, ctx.cwd)


# -- one switch: enable / disable ------------------------------------------------------------------

RECORD_KEY = "agent_docs_hooks"


def git_global(*args):
    """git config --global ... (GIT_CONFIG_GLOBAL is honoured by git itself); returns the CompletedProcess."""
    return git(None, "config", "--global", *args)


def hooks_record():
    from . import settings
    rec = settings.load(warn=False).get(RECORD_KEY)
    return rec if isinstance(rec, dict) else None


def save_record(rec):
    from . import settings
    settings.update({RECORD_KEY: rec})


def global_hooks_path():
    """(raw value or None, expanded directory or None) of git's global core.hooksPath."""
    res = git_global("--get", "core.hooksPath")
    raw = res.stdout.rstrip("\n") if res.returncode == 0 else None
    return raw, (os.path.abspath(os.path.expanduser(raw)) if raw else None)


DRY_VERBS = {"wrote": "write", "renamed": "rename"}


def step(ctx, text):
    if ctx.dry_run:
        verb, _, rest = text.partition(" ")
        text = "would %s %s" % (DRY_VERBS.get(verb, verb), rest)
    say(text)


def block_step(ctx, value):
    """Re-render the managed CLAUDE.md block, printing what install-instructions prints; a refusal (hand-written rules,
    damaged markers) is reported, not fatal. Returns True when the file changed (or would)."""
    from . import instructions
    ns = types.SimpleNamespace(file=ctx.claude_md, uninstall=False, replace_existing=False, dry_run=ctx.dry_run,
                               overrides={"agent_docs": value})
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            instructions.cmd_install_instructions(ns, [])
    except OrchError as exc:
        sys.stdout.write(buf.getvalue())
        say("CLAUDE.md block not updated: %s" % exc)
        return False
    out = buf.getvalue()
    sys.stdout.write(out)
    return bool(out.strip()) and "nothing to do" not in out


def cmd_enable(ctx):
    from . import settings
    from .paths import data_root, require_write_allowed
    if not ctx.dry_run:
        require_write_allowed()
    rec = hooks_record()
    fresh = rec is None
    if fresh:
        raw, hdir = global_hooks_path()
        if raw is not None:
            rec = {"dir": hdir, "prev": raw, "set_hooks_path": False, "created_dir": False, "renamed": [], "wrote": [], "complete": False}
        else:
            rec = {"dir": os.path.join(data_root(), "git-hooks"), "prev": None, "set_hooks_path": False, "created_dir": False,
                   "renamed": [], "wrote": [], "complete": False}
    hdir = rec["dir"]
    changed = False
    # (a) the setting, and the record first: whatever happens next, disable can undo it
    if settings.get("agent_docs") != "on":
        step(ctx, "set agent_docs on")
        changed = True
    if not ctx.dry_run:
        if fresh or settings.get("agent_docs") != "on":
            settings.update({"agent_docs": "on", RECORD_KEY: rec})
    # (b) the managed block
    changed = block_step(ctx, "on") or changed
    # (c) the global git hooks
    if not os.path.isdir(hdir):
        step(ctx, "create %s" % hdir)
        changed = True
        if not ctx.dry_run:
            os.makedirs(hdir, exist_ok=True)
            rec["created_dir"] = True
            save_record(rec)

    def record(change):
        if change[0] == "renamed":
            rec["renamed"].append({"from": change[1], "to": change[2]})
        else:
            rec["wrote"].append(change[1])
        save_record(rec)

    for change in install_hooks(hdir, chain_existing=True, dry_run=ctx.dry_run, record=record):
        step(ctx, "%s %s" % (change[0], " -> ".join(change[1:])))
        changed = True
    if rec["prev"] is None and not rec["set_hooks_path"]:
        step(ctx, "set the global git hooks path to %s" % hdir)
        changed = True
        if not ctx.dry_run:
            res = git_global("core.hooksPath", hdir)
            if res.returncode != 0:
                raise OrchError("cannot set the global git hooks path: %s" % res.stderr.strip(), 1)
            rec["set_hooks_path"] = True
            save_record(rec)
    if not ctx.dry_run and not rec["complete"]:
        rec["complete"] = True
        save_record(rec)
    say("enable: %s" % ("would change the above" if ctx.dry_run and changed else "nothing to do" if not changed else "done (hooks in %s)" % hdir))
    return 0


def cmd_disable(ctx):
    from . import settings
    if not ctx.dry_run:
        from .paths import require_write_allowed
        require_write_allowed()
    rec = hooks_record()
    changed = False
    # (a) the setting; the record stays until the hooks are undone
    if settings.get("agent_docs") != "off":
        step(ctx, "set agent_docs off")
        changed = True
        if not ctx.dry_run:
            settings.update({"agent_docs": "off"})
    # (b) the managed block, rendered with the section dropped
    changed = block_step(ctx, "off") or changed
    # (c) undo exactly what was recorded, in reverse
    if rec:
        hdir = rec.get("dir")
        raw, now = global_hooks_path()
        if rec.get("set_hooks_path"):
            if now is not None and hdir and os.path.abspath(now) == os.path.abspath(hdir):
                step(ctx, "unset the global git hooks path (it was unset before)")
                changed = True
                if not ctx.dry_run:
                    git_global("--unset", "core.hooksPath")
            else:
                say("left the global git hooks path alone: it is not the one orch set")
        for path in reversed(rec.get("wrote", [])):
            if is_shim(path):
                step(ctx, "remove %s" % path)
                changed = True
                if not ctx.dry_run:
                    os.unlink(path)
        for mv in reversed(rec.get("renamed", [])):
            src, dst = mv.get("from"), mv.get("to")
            if src and dst and os.path.lexists(dst) and not os.path.lexists(src):
                step(ctx, "renamed %s -> %s" % (dst, src))
                changed = True
                if not ctx.dry_run:
                    os.rename(dst, src)
            elif src and dst and os.path.lexists(dst):
                say("left %s alone: %s exists again" % (dst, src))
        if rec.get("created_dir") and hdir and os.path.isdir(hdir) and not os.listdir(hdir):
            step(ctx, "remove the empty directory %s" % hdir)
            changed = True
            if not ctx.dry_run:
                os.rmdir(hdir)
        if not ctx.dry_run:
            settings.update(delete=[RECORD_KEY])
        else:
            changed = True
    say("disable: %s" % ("would change the above" if ctx.dry_run and changed else "nothing to do" if not changed else "done"))
    return 0


# -- session nudge -------------------------------------------------------------------------------

NUDGE_CANDIDATES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "GEMINI.md", "STATE.md", "DECISIONS.md", "HANDOFF.md",
                    "docs/STATE.md", "docs/DECISIONS.md")


def _nested_worktree(root):
    """True when root is a linked worktree whose main checkout contains it (orch's .claude/worktrees/*). Reads the
    .git file and the commondir file; no git process."""
    dotgit = os.path.join(root, ".git")
    if not os.path.isfile(dotgit):
        return False
    try:
        first = (read_text(dotgit) or "").split("\n")[0]
        if not first.startswith("gitdir:"):
            return False
        gitdir = os.path.normpath(os.path.join(root, first[len("gitdir:"):].strip()))
        common = (read_text(os.path.join(gitdir, "commondir")) or "").strip()
        if not common:
            return False
        main = os.path.dirname(os.path.normpath(os.path.join(gitdir, common)))
        return os.path.realpath(root).startswith(os.path.realpath(main).rstrip("/") + "/")
    except OSError:
        return False


def session_nudge(cwd):
    """The one SessionStart context line naming agent docs that are real files in this repo (not links into the private
    store), or None. Reads and stats only: it never moves or creates anything. The common no-hit path starts no process."""
    root = os.path.abspath(cwd)
    while not os.path.exists(os.path.join(root, ".git")):
        parent = os.path.dirname(root)
        if parent == root:
            return None
        root = parent
    if _nested_worktree(root):
        return None
    hits = []
    for rel in NUDGE_CANDIDATES:
        try:
            if stat.S_ISREG(os.lstat(os.path.join(root, rel)).st_mode):
                hits.append(rel)
        except OSError:
            pass
    if not hits:
        return None
    if git(root, "config", "--bool", "agentdocs.allow").stdout.strip() == "true":
        return None
    tracked = bool(git(root, "ls-files", "-z", "--", *hits).stdout.strip("\0"))
    return "%s: agent docs here are not in the private store: %s \u2014 move them: orch docs adopt %s%s" % (
        PREFIX, ", ".join(hits), " ".join(hits), " (tracked: commit the removal afterwards)" if tracked else "")


VERBS = {
    "init": cmd_init, "adopt": cmd_adopt, "add": cmd_add, "link": cmd_link, "status": cmd_status,
    "list": cmd_list, "commit": cmd_commit, "migrated": cmd_migrated,
    "check": cmd_check, "hook": cmd_hook, "install-hooks": cmd_install_hooks,
    "enable": cmd_enable, "disable": cmd_disable,
}


class Ctx:
    def __init__(self, ns):
        self.cwd = os.path.abspath(ns.dir or os.getcwd())
        self.paths = list(ns.paths or [])
        self.name = ns.name
        self.message = ns.message
        self.hooks_dir = ns.hooks_dir
        self.chain_existing = ns.chain_existing
        self.dry_run = getattr(ns, "dry_run", False)
        self.claude_md = getattr(ns, "claude_md", None)


def cmd_docs(ns, extra):
    verb = ns.verb or "status"
    if ns.paths and verb not in ("adopt", "add", "check", "hook"):
        raise OrchError("%s takes no positional argument" % verb, 1)
    return VERBS[verb](Ctx(ns))


def register(add):
    sp = add("docs", cmd_docs,
             "private agent-docs store: init | adopt PATH... | add PATH... | link | status | list | commit [-m MSG] | migrated"
             " | check staged|range|tree | hook NAME [ARGS] | install-hooks [--dir D] [--chain-existing]"
             " | enable [--dry-run] | disable [--dry-run]")
    sp.add_argument("verb", nargs="?", choices=list(VERBS), default=None,
                    help="init: register this repo and create .agent | adopt: move paths into the store and link them back"
                         " | add: link a path already in the store | link: (re)create .agent and every link (fresh clone,"
                         " worktree) | status: links, broken links, private paths still tracked | list: every repo in the"
                         " store | commit: commit the store | migrated: drop agentdocs.mode so the hooks block again | check: the path"
                         " and content check (staged; range -- <rev args>; tree [rev]) | hook: what a git hook shim runs"
                         " (pre-commit, pre-push, post-checkout) | install-hooks: write the shims into the repo's hooks dir | enable:"
                         " the one switch on (setting, CLAUDE.md block, global git hooks) | disable: undo exactly what enable recorded")
    sp.add_argument("paths", nargs="*", metavar="PATH")
    sp.add_argument("--name", default=None, help="init: the store entry name (default: from .agent, origin, or the directory)")
    sp.add_argument("-m", dest="message", default=None, help="commit: the store commit message")
    sp.add_argument("-C", dest="dir", default=None, help="run as if started in DIR")
    sp.add_argument("--dir", dest="hooks_dir", default=None, help="install-hooks: the hooks directory (default: the repo's)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true", help="enable, disable: print each change, make none")
    sp.add_argument("--file", dest="claude_md", default=None, help="enable, disable: the CLAUDE.md to re-render (default: the global one)")
    sp.add_argument("--chain-existing", dest="chain_existing", action="store_true",
                    help="install-hooks: keep a foreign hook as <name>.pre-orch and chain to it")
