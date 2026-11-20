"""orch docs <verb>: keep a repo's agent/internal docs out of git, in a private store on this machine.

The store (paths.docs_store()) is a local git repo with one directory per repo:
  <store>/<name>/            the repo's private files, at their repo-relative paths
  <store>/<name>/.links      paths symlinked into the checkout (one per line)
  <store>/<name>/.origin     the repo's origin URL when it was registered
In the checkout `.agent` points at <store>/<name> and each path in .links is a symlink into `.agent/`.
The layout and the agentdocs.* git-config keys are the ones the older shell tool used, so existing entries keep working.
Commands here work whatever the `agent_docs` setting says: it only gates orch's automatic behaviour, not these verbs.
"""
import contextlib
import os
import re
import shutil
import subprocess

from .paths import OrchError, docs_store

PREFIX = "orch docs"
STORE_README = "# Private agent docs store (managed by `orch docs`). Never push to a public remote.\n"
DENY_PATHS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs_deny_paths.txt")


def say(msg):
    print("%s: %s" % (PREFIX, msg))


# -- git plumbing --------------------------------------------------------------------------------

def git(cwd, *args, check=False):
    """Run git in cwd with an argument list (never a shell string); returns the CompletedProcess (text)."""
    try:
        res = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)
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


def deny_path_regexes():
    """The compiled path patterns of the shipped list (one extended regex per line, # comments)."""
    out = []
    for ln in (read_text(DENY_PATHS_FILE) or "").split("\n"):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(re.compile(ln))
    return out


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


VERBS = {
    "init": cmd_init, "adopt": cmd_adopt, "add": cmd_add, "link": cmd_link, "status": cmd_status,
    "list": cmd_list, "commit": cmd_commit, "migrated": cmd_migrated,
}


class Ctx:
    def __init__(self, ns):
        self.cwd = os.path.abspath(ns.dir or os.getcwd())
        self.paths = list(ns.paths or [])
        self.name = ns.name
        self.message = ns.message


def cmd_docs(ns, extra):
    verb = ns.verb or "status"
    if ns.paths and verb not in ("adopt", "add"):
        raise OrchError("%s takes no positional argument" % verb, 1)
    return VERBS[verb](Ctx(ns))


def register(add):
    sp = add("docs", cmd_docs,
             "private agent-docs store: init | adopt PATH... | add PATH... | link | status | list | commit [-m MSG] | migrated")
    sp.add_argument("verb", nargs="?", choices=list(VERBS), default=None,
                    help="init: register this repo and create .agent | adopt: move paths into the store and link them back"
                         " | add: link a path already in the store | link: (re)create .agent and every link (fresh clone,"
                         " worktree) | status: links, broken links, private paths still tracked | list: every repo in the"
                         " store | commit: commit the store | migrated: drop agentdocs.mode so the hooks block again")
    sp.add_argument("paths", nargs="*", metavar="PATH")
    sp.add_argument("--name", default=None, help="init: the store entry name (default: from .agent, origin, or the directory)")
    sp.add_argument("-m", dest="message", default=None, help="commit: the store commit message")
    sp.add_argument("-C", dest="dir", default=None, help="run as if started in DIR")
