"""orch doctor [--json] [--repo PATH]: what is wrong with orch on this machine, and how to fix it.

Sixteen checks in four areas (index, install, settings, tasks). Each is `ok`, `warn` or `fail` and carries a fix: `auto`
(`orch doctor --fix` can apply it), `manual` (a command or an instruction for the user) or `none`. Reading only: no file
is created or changed, no settings value is printed (a settings file is read in-process; only key names and a parse
position ever appear), and a check that raises is reported as a failure while the others still run.

`--fix` applies the safe `auto` fixes and nothing else (see FIXERS): create the missing data folders, repoint the plugin
pointer (installed copy only), install the pinned orch-index, remove a dead indexing marker. It never rebuilds the index,
removes a worktree or task folder, or edits any settings file. The checks run again afterwards and decide the exit code.

Exit codes: 0 no `fail`, 2 at least one `fail` (also: `--fix` refused), 1 usage error, 3 internal.
"""
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

from . import memory, paths, settings as orch_settings, status as st, usage
from .paths import (BIN_DIR, OrchError, cache_dir, data_bin, data_root, is_live, logs_dir, orch_root, plugins_cache,
                    pointer_path, task_names, tilde, writes_allowed)

SCHEMA = 1
INSTALL_TIMEOUT = 300          # seconds for orch-install-code-index.sh
AREAS = ("index", "install", "settings", "tasks")
SUBPROCESS_TIMEOUT = 10        # seconds, each external command
MARKER_MAX_AGE = 30 * 60       # an indexing marker older than this is dead whatever its pid says (roles/graph.md)
STALE_TASK_DAYS = 7
USAGE_MAX_AGE = 30 * 60
MIN_PYTHON = (3, 9)
LIST_LIMIT = 5

TARGETS = {
    ("Darwin", "arm64"): "aarch64-apple-darwin", ("Darwin", "aarch64"): "aarch64-apple-darwin",
    ("Darwin", "x86_64"): "x86_64-apple-darwin",
    ("Linux", "x86_64"): "x86_64-unknown-linux-musl", ("Linux", "amd64"): "x86_64-unknown-linux-musl",
    ("Linux", "aarch64"): "aarch64-unknown-linux-musl", ("Linux", "arm64"): "aarch64-unknown-linux-musl",
}
WINDOWS_TARGET = "x86_64-pc-windows-msvc"


# ---- helpers ------------------------------------------------------------------------------------------------------

def run_cmd(argv, cwd=None):
    """(returncode, stdout) of an external command, or None when it cannot run or times out."""
    try:
        res = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             timeout=SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.returncode, res.stdout.decode("utf-8", "replace")


def git(repo, *args):
    """stdout (stripped) of `git -C repo ARGS`, or None when git fails."""
    res = run_cmd(["git", "-C", repo] + list(args))
    if res is None or res[0] != 0:
        return None
    return res[1].strip()


def index_target():
    """The orch-index release target of this machine, as bin/orch-index maps it; '' when there is none."""
    if os.name == "nt":
        return WINDOWS_TARGET
    return TARGETS.get((platform.system(), platform.machine()), "")


def exe_suffix(target):
    return ".exe" if "-windows-" in target else ""


def _usable(path):
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def find_index():
    """(path, source) of the orch-index binary the launcher (bin/orch-index) would run, or None. Same order:
    ORCH_INDEX_REAL, the bundled bin/vendor binary, <data root>/bin/orch-index, then the first one on PATH
    (never the launcher's own folder)."""
    target = index_target()
    exe = exe_suffix(target)
    real = os.environ.get("ORCH_INDEX_REAL")
    if _usable(real):
        return real, "ORCH_INDEX_REAL"
    if target:
        bundled = os.path.join(BIN_DIR, "vendor", "orch-index-%s%s" % (target, exe))
        if _usable(bundled):
            return bundled, "bundled"
    data = os.path.join(data_bin(), "orch-index" + exe)
    if _usable(data):
        return data, "data folder"
    here = os.path.realpath(BIN_DIR)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        cand = os.path.join(d, "orch-index" + exe)
        if _usable(cand) and os.path.realpath(d) != here:
            return cand, "PATH"
    return None


def pinned_version():
    """The orch-index version pinned in orch-install-code-index.sh, without the leading v; None when unreadable."""
    try:
        with open(os.path.join(BIN_DIR, "orch-install-code-index.sh"), encoding="utf-8", errors="replace") as fh:
            m = re.search(r"^readonly ORCH_INDEX_VERSION=v?(\d+\.\d+\.\d+)\s*$", fh.read(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def pid_alive(pid):
    """True when a process with this pid runs. Windows has no safe probe here (os.kill would terminate it): True."""
    if os.name == "nt":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_marker(path):
    """(pid or None, age in seconds) of an indexing marker; its one line is `pid=<n> started=<UTC>`."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(512)
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return None, None
    m = re.match(r"pid=(\d+)\b", text.strip())
    return (int(m.group(1)) if m else None), age


def marker_is_live(pid, age):
    """The rule of roles/graph.md: live when the pid runs and the file is under 30 minutes old. A marker with no pid
    counts as live while it is young (never removed on a guess)."""
    if age is None or age >= MARKER_MAX_AGE:
        return False
    return True if pid is None else pid_alive(pid)


def parse_object(path):
    """('absent'|'ok'|'bad', info, data): info is a parse position or reason, never file content."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return "absent", "", None
    except OSError as exc:
        return "bad", "unreadable (%s)" % type(exc).__name__, None
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return "bad", "not UTF-8", None
    except ValueError as exc:
        where = " at line %d, column %d" % (exc.lineno, exc.colno) if hasattr(exc, "lineno") else ""
        return "bad", "not valid JSON%s" % where, None
    if not isinstance(data, dict):
        return "bad", "not a JSON object", None
    return "ok", "", data


def short_list(items, limit=LIST_LIMIT):
    items = list(items)
    shown = ", ".join(items[:limit])
    return shown + (" (+%d more)" % (len(items) - limit) if len(items) > limit else "")


def installed_roots():
    """The install paths installed_plugins.json lists for orch@orch ([] when unreadable or unlisted)."""
    path = os.path.join(os.path.dirname(plugins_cache()), "installed_plugins.json")
    state, _, data = parse_object(path)
    if state != "ok" or not isinstance(data.get("plugins"), dict):
        return []
    entries = data["plugins"].get("orch@orch")
    out = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and isinstance(e.get("installPath"), str) and e["installPath"]:
            out.append(e["installPath"])
    return out


def _within(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def fix_scope_allows(path):
    """True when `--fix` may write at path. The installed copy writes anywhere orch owns; a development copy only inside
    ORCH_DATA (when set) or ORCH_DIR, never the real orch data."""
    if is_live():
        return True
    for var in ("ORCH_DATA", "ORCH_DIR"):
        root = os.environ.get(var)
        if root and _within(path, root):
            return True
    return False


def own_plugin_root():
    return os.path.realpath(os.path.join(BIN_DIR, ".."))


# ---- context -------------------------------------------------------------------------------------------------------

class Ctx(object):
    """What the checks share: the repo, and the facts that more than one check needs (each computed once)."""

    def __init__(self, repo_arg=None):
        if repo_arg is not None and not os.path.isdir(repo_arg):
            raise OrchError("--repo %s is not a folder" % repo_arg, 1)
        start = os.path.abspath(repo_arg or os.getcwd())
        top = git(start, "rev-parse", "--show-toplevel")
        self.repo = os.path.realpath(top) if top else None
        self.main_root = None
        self.common_dir = None
        self.index_repo = None      # the main checkout: the index lives in its common dir, and only it is rebuilt
        if self.repo:
            out = git(self.repo, "worktree", "list", "--porcelain") or ""
            first = re.search(r"^worktree (.+)$", out, re.M)
            self.main_root = os.path.realpath(first.group(1)) if first else self.repo
            common = git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
            self.common_dir = common or None
            if common and os.path.basename(os.path.normpath(common)) == ".git":
                self.index_repo = os.path.realpath(os.path.dirname(os.path.normpath(common)))
            else:
                self.index_repo = self.repo
        self._index = False
        self._tasks = None

    def index(self):
        if self._index is False:
            self._index = find_index()
        return self._index

    def tasks(self):
        """[dict(dir, repo_slug, state, repo_path, worktree, mtime, readable, has_state)] for every <root>/*/*/status.md."""
        if self._tasks is None:
            rows = []
            root = orch_root()
            for sf in sorted(glob.glob(os.path.join(glob.escape(root), "*", "*", "status.md"))):
                d = os.path.dirname(sf)
                repo, slug = task_names(d)
                row = {"dir": d, "name": "%s/%s" % (repo, slug), "readable": True, "has_state": False, "state": None,
                       "repo_path": None, "worktree": "", "mtime": 0.0}
                try:
                    with open(sf, "rb") as fh:
                        fh.read().decode("utf-8")           # a status.md that cannot be decoded is unreadable
                    text = st.read_text_like_index(sf)
                    mtimes = [os.path.getmtime(p) for p in (sf, os.path.join(d, "log.md")) if os.path.exists(p)]
                    row["mtime"] = max(mtimes)
                except (OSError, UnicodeDecodeError):
                    row["readable"] = False
                    rows.append(row)
                    continue
                row["has_state"] = re.search(r"^- task_state:", text, re.M) is not None
                row["state"] = st.classify(text, d)
                m = re.search(r"^- repo:[ \t]*(.*)$", text, re.M)
                row["repo_path"] = re.sub(r"\s*\(.*$", "", m.group(1).strip()) if m else None
                m = re.search(r"^- worktree:[ \t]*(.*)$", text, re.M)
                row["worktree"] = re.sub(r"\s*\(.*$", "", m.group(1).strip()) if m else ""
                rows.append(row)
            self._tasks = rows
        return self._tasks

    def repo_tasks(self):
        """The tasks whose status.md `repo:` is this repo (or its main checkout)."""
        if not self.repo:
            return []
        want = {self.repo, self.main_root}
        out = []
        for t in self.tasks():
            p = t["repo_path"]
            if p and os.path.realpath(os.path.expanduser(p)) in want:
                out.append(t)
        return out


# ---- results -------------------------------------------------------------------------------------------------------

def result(check_id, area, title, status, detail, fix_kind="none", fix_text=""):
    return {"id": check_id, "area": area, "status": status, "title": title, "detail": detail,
            "fix": {"kind": fix_kind, "text": fix_text}}


def ok(detail):
    return ("ok", detail, "none", "")


def warn(detail, kind="manual", text=""):
    return ("warn", detail, kind, text)


def fail(detail, kind="manual", text=""):
    return ("fail", detail, kind, text)


# ---- index checks --------------------------------------------------------------------------------------------------

def check_index_binary(ctx):
    found = ctx.index()
    if found:
        return ok("%s (%s)" % (tilde(found[0]), found[1]))
    script = os.path.join(BIN_DIR, "orch-install-code-index.sh")
    if fix_scope_allows(data_bin()):
        return fail("no orch-index binary found", "auto",
                    "install the pinned orch-index into %s (%s verifies its checksum and never overwrites)"
                    % (tilde(data_bin()), "orch-install-code-index.sh"))
    return fail("no orch-index binary found", "manual",
                "run %s (a development copy installs only into ORCH_DATA)" % tilde(script))


def check_index_version(ctx):
    found = ctx.index()
    if not found:
        return warn("not checked: no orch-index binary", "none")
    res = run_cmd([found[0], "--version"])
    if res is None or res[0] != 0:
        return fail("%s does not run (--version failed)" % tilde(found[0]), "manual",
                    "remove it and install the pinned release: orch-install-code-index.sh")
    m = re.search(r"(\d+\.\d+\.\d+)", res[1])
    if not m:
        return warn("%s printed no version number" % tilde(found[0]), "manual",
                    "reinstall the pinned release: orch-install-code-index.sh")
    pin = pinned_version()
    if pin is None:
        return warn("runs as %s; the pinned version could not be read" % m.group(1), "none")
    if m.group(1) != pin:
        return warn("runs as %s; this plugin pins %s" % (m.group(1), pin), "manual",
                    "the installer never overwrites: remove %s, then run orch-install-code-index.sh" % tilde(found[0]))
    return ok("%s (the pinned version)" % m.group(1))


def check_index_state(ctx):
    if not ctx.repo:
        return ok("not in a git repo")
    found = ctx.index()
    if not found:
        return warn("not checked: no orch-index binary", "none")
    res = run_cmd([found[0], "status", "--json", "--repo", ctx.index_repo])
    data = None
    if res is not None:
        try:
            data = json.loads(res[1])
        except ValueError:
            data = None
    if not isinstance(data, dict) or not isinstance(data.get("state"), str):
        return warn("orch-index status gave no usable answer for this repo", "manual",
                    "run `orch-index status --json --repo %s` and read its error" % ctx.index_repo)
    state = data["state"]
    where = "main checkout %s" % tilde(ctx.index_repo)
    if state == "current":
        return ok("current (%s)" % where)
    behind = data.get("commits_behind")
    extra = " (%d commits behind)" % behind if isinstance(behind, int) and behind > 0 else ""
    running = " (a rebuild is running)" if data.get("indexing") is True else ""
    return warn("%s%s%s (%s)" % (state, extra, running, where), "manual",
                "ask orch:graph, or run `orch-index index --repo %s`" % ctx.index_repo)


def check_index_marker(ctx):
    if not ctx.common_dir:
        return ok("not in a git repo")
    marker = os.path.join(ctx.common_dir, "orch-index", "indexing")
    if not os.path.exists(marker):
        return ok("no indexing marker")
    pid, age = read_marker(marker)
    if marker_is_live(pid, age):
        return ok("a rebuild is running%s" % (" (pid %d)" % pid if pid else ""))
    where = "main checkout %s" % tilde(ctx.index_repo)
    text = "remove the dead indexing marker %s" % tilde(marker)
    if fix_scope_allows(marker):
        return warn("dead indexing marker, no rebuild runs (%s)" % where, "auto", text)
    return warn("dead indexing marker, no rebuild runs (%s)" % where, "manual", text)


# ---- install checks ------------------------------------------------------------------------------------------------

def check_install_copy(ctx):
    root = tilde(own_plugin_root())
    if is_live():
        return ok("installed copy (%s)" % root)
    if writes_allowed():
        return ok("development copy (%s), told where to write" % root)
    return warn("development copy (%s): writes to the real orch data are refused" % root, "manual",
                "set ORCH_DIR to a scratch folder to try it, or run the installed copy")


def check_install_pointer(ctx):
    link = pointer_path()
    listed = [os.path.realpath(p) for p in installed_roots()]
    try:
        current = os.path.realpath(link) if os.path.lexists(link) else None
    except OSError:
        current = None
    exists = current is not None and os.path.isdir(current)
    if exists and (not listed or current in listed):
        return ok("%s -> %s%s" % (tilde(link), tilde(current), "" if listed else " (no installed_plugins.json entry to compare)"))
    if not os.path.lexists(link):
        what = "%s is missing" % tilde(link)
    elif not exists:
        what = "%s points at a folder that is gone" % tilde(link)
    else:
        what = "%s points at %s, not the installed copy" % (tilde(link), tilde(current))
    own = own_plugin_root()
    text = "point %s at %s" % (tilde(link), tilde(own))
    if is_live() and (not listed or own in listed):
        return warn(what, "auto", text)
    if exists:
        return warn(what, "manual", "start a new session or run /reload-plugins; the pointer moves at SessionStart")
    return warn(what, "manual", "start a new Claude Code session (its start-up hook moves the pointer), or run "
                                "`orch session-start <plugin root>` from the installed copy")


def check_install_python(ctx):
    v = sys.version_info
    shown = "%d.%d.%d" % (v[0], v[1], v[2])
    if (v[0], v[1]) >= MIN_PYTHON:
        return ok("Python %s" % shown)
    return fail("Python %s is older than %d.%d" % (shown, MIN_PYTHON[0], MIN_PYTHON[1]), "manual",
                "install Python %d.%d or newer and put it first on PATH" % MIN_PYTHON)


def data_folders():
    """[(label, path)] of the folders orch needs under its data root."""
    return [("data root", data_root()), ("tasks", orch_root()), ("logs", logs_dir()), ("cache", cache_dir())]


def check_install_data(ctx):
    missing, locked = [], []
    for label, path in data_folders():
        if not os.path.isdir(path):
            missing.append((label, path))
        elif not os.access(path, os.W_OK):
            locked.append((label, path))
    if locked:
        return fail("not writable: %s" % short_list("%s (%s)" % (l, tilde(p)) for l, p in locked), "manual",
                    "give your user write access: chmod u+w %s" % " ".join(tilde(p) for _, p in locked))
    if missing:
        detail = "missing: %s" % short_list("%s (%s)" % (l, tilde(p)) for l, p in missing)
        allowed = [p for _, p in missing if fix_scope_allows(p)]
        blocked = [p for _, p in missing if not fix_scope_allows(p)]
        text = "create %s" % ", ".join(tilde(p) for _, p in missing)
        if blocked and allowed:
            text = "create %s; create %s yourself (a development copy leaves anything outside ORCH_DATA / ORCH_DIR alone)" % (
                ", ".join(tilde(p) for p in allowed), ", ".join(tilde(p) for p in blocked))
        return warn(detail, "auto" if allowed else "manual", text)
    return ok("data root %s, tasks %s" % (tilde(data_root()), tilde(orch_root())))


# ---- settings checks -----------------------------------------------------------------------------------------------

def check_settings_orch(ctx):
    path = orch_settings.settings_path()
    state, info, data = parse_object(path)
    if state == "absent":
        return ok("no settings file (defaults apply)")
    if state == "bad":
        return warn("%s is %s; the defaults apply" % (tilde(path), info), "manual",
                    "fix or delete %s (orch only reads it; it never prints its content)" % tilde(path))
    bad = [name for name, (_, allowed) in sorted(orch_settings.SETTINGS.items())
           if name in data and data[name] not in allowed]
    if bad:
        return warn("%s: value not allowed for %s; the defaults apply" % (tilde(path), ", ".join(bad)), "manual",
                    "set it with `orch setting set NAME VALUE`")
    return ok("%s readable" % tilde(path))


def claude_settings_files(ctx):
    files = [("global", memory.global_settings())]
    if ctx.repo:
        files += [("project shared", memory.shared_settings(ctx.repo)), ("project local", memory.local_settings(ctx.repo))]
    return files


def check_settings_claude(ctx):
    bad, present = [], []
    for label, path in claude_settings_files(ctx):
        state, info, _ = parse_object(path)
        if state == "bad":
            bad.append("%s (%s) is %s" % (label, tilde(path), info))
        elif state == "ok":
            present.append(label)
    if bad:
        return warn("; ".join(bad), "manual", "fix the JSON in the named file; orch never edits it for this")
    return ok("%s readable" % ", ".join(present) if present else "no settings files present")


def check_memory_auto(ctx):
    if not ctx.repo:
        return ok("not in a git repo; the global setting decides")
    files = [p for _, p in claude_settings_files(ctx)]
    if memory.says_off(files):
        return ok("off for %s" % ctx.repo)
    return ok("on for %s (no settings file or %s switches it off)" % (ctx.repo, memory.ENV))


def check_usage_samples(ctx):
    s5, s7 = usage.load_log(usage.home_dir())
    times = [s[0] for s in s5 + s7]
    fix = "check that the statusline writer runs (it appends to %s)" % tilde(
        os.path.join(usage.home_dir(), ".statusline-usage-log"))
    if not times:
        return warn("no usage samples; usage pacing reads UNKNOWN", "manual", fix)
    age = time.time() - max(times)
    if age < 0 or age < USAGE_MAX_AGE:
        return ok("newest sample %d min ago" % max(0, int(age // 60)))
    return warn("newest sample is %s old; usage pacing reads STALE" % _ago(age), "manual", fix)


def _ago(seconds):
    if seconds >= 86400:
        return "%d days" % (seconds // 86400)
    if seconds >= 3600:
        return "%d hours" % (seconds // 3600)
    return "%d min" % (seconds // 60)


# ---- tasks checks --------------------------------------------------------------------------------------------------

def check_tasks_parse(ctx):
    tasks = ctx.tasks()
    bad = [t["name"] for t in tasks if not t["readable"]]
    if bad:
        return fail("status.md cannot be read in: %s" % short_list(bad), "manual",
                    "check the file's permissions and that it is UTF-8 text")
    old = sum(1 for t in tasks if not t["has_state"])      # old format (no task_state line): works as it is
    return ok("%d task folders read (%d old-format)" % (len(tasks), old))


def active(tasks):
    return [t for t in tasks if t["readable"] and t["state"] not in st.INDEX_TERMINAL]


def check_tasks_stale(ctx):
    now = time.time()
    stale = [t["name"] for t in active(ctx.tasks()) if (now - t["mtime"]) / 86400 > STALE_TASK_DAYS]
    if stale:
        return warn("untouched for over %d days: %s" % (STALE_TASK_DAYS, short_list(stale)), "manual",
                    "resume them with `orch resume`, or close them with `orch set task_state=abandoned -t <repo>/<slug>`")
    return ok("no active task idle for over %d days" % STALE_TASK_DAYS)


def recorded_worktree(task):
    """The task's recorded worktree as an absolute path, or None when the field holds no path (none, removed, …)."""
    wt = os.path.expanduser(task["worktree"].strip())
    return wt if os.path.isabs(wt) else None


def check_tasks_worktree(ctx):
    if not ctx.repo:
        return ok("not in a git repo")
    gone = []
    for t in active(ctx.repo_tasks()):
        wt = recorded_worktree(t)
        if wt and not os.path.exists(wt):
            gone.append("%s (%s)" % (t["name"], tilde(wt)))
    if gone:
        return warn("recorded worktree no longer exists: %s" % short_list(gone), "manual",
                    "recreate it, or close the task: `orch set task_state=abandoned -t <repo>/<slug>`")
    return ok("every active task of this repo has its worktree")


def check_tasks_orphans(ctx):
    if not ctx.repo:
        return ok("not in a git repo")
    out = git(ctx.repo, "worktree", "list", "--porcelain")
    if out is None:
        return warn("git worktree list failed", "manual", "run `git worktree list` in %s and read its error" % ctx.repo)
    parent = os.path.realpath(os.path.join(ctx.main_root, ".claude", "worktrees"))
    known = set()
    for t in active(ctx.repo_tasks()):        # a finished task's leftover worktree is an orphan too
        wt = recorded_worktree(t)
        if wt:
            known.add(os.path.realpath(wt))
    orphans = []
    for m in re.finditer(r"^worktree (.+)$", out, re.M):
        path = os.path.realpath(m.group(1))
        if os.path.dirname(path) == parent and os.path.basename(path).startswith("orch-") and path not in known:
            orphans.append(path)
    if orphans:
        return warn("no task records: %s" % short_list(tilde(p) for p in orphans), "manual",
                    "; ".join("git worktree remove %s" % p for p in orphans[:LIST_LIMIT])
                    + " (check that nothing in it is unmerged first)")
    return ok("every orch worktree of this repo belongs to a task")


CHECKS = [
    ("index.binary", "index", "orch-index binary", check_index_binary),
    ("index.version", "index", "orch-index version", check_index_version),
    ("index.state", "index", "Code index", check_index_state),
    ("index.marker", "index", "Indexing marker", check_index_marker),
    ("install.copy", "install", "Plugin copy", check_install_copy),
    ("install.pointer", "install", "Plugin pointer", check_install_pointer),
    ("install.python", "install", "Python", check_install_python),
    ("install.data", "install", "Data folders", check_install_data),
    ("settings.orch", "settings", "orch settings", check_settings_orch),
    ("settings.claude", "settings", "Claude settings files", check_settings_claude),
    ("memory.auto", "settings", "Auto-memory", check_memory_auto),
    ("usage.samples", "settings", "Usage samples", check_usage_samples),
    ("tasks.parse", "tasks", "Task files", check_tasks_parse),
    ("tasks.stale", "tasks", "Stale tasks", check_tasks_stale),
    ("tasks.worktree", "tasks", "Task worktrees", check_tasks_worktree),
    ("tasks.orphans", "tasks", "Orphan worktrees", check_tasks_orphans),
]


# ---- fixes (--fix) -------------------------------------------------------------------------------------------------
# Each fixer takes the Ctx and returns (result, detail), result in done|skipped|failed. Only the checks named in FIXERS
# can ever be fixed, whatever another check's `fix.kind` says.

def last_line(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[-1][:200] if lines else "no output"


def fix_data_folders(ctx):
    made, left = [], []
    try:
        for _, path in data_folders():
            if os.path.isdir(path):
                continue
            if not fix_scope_allows(path):
                left.append(tilde(path))
                continue
            os.makedirs(path, exist_ok=True)
            made.append(tilde(path))
    except OSError as exc:
        return "failed", "%s: %s" % (type(exc).__name__, exc.strerror or exc)
    note = "; left alone (outside ORCH_DATA / ORCH_DIR): %s" % ", ".join(left) if left else ""
    if made:
        return "done", "created %s%s" % (", ".join(made), note)
    return "skipped", ("nothing in scope to create" + note) if left else "nothing was missing"


def fix_pointer(ctx):
    if not is_live():
        return "skipped", "only the installed copy moves the pointer (development copy)"
    try:
        changed = paths.update_pointer(own_plugin_root())
    except OSError as exc:
        return "failed", "%s: %s" % (type(exc).__name__, exc.strerror or exc)
    if changed:
        return "done", "%s -> %s" % (tilde(pointer_path()), tilde(own_plugin_root()))
    return "skipped", "the pointer already names this copy"


def windows_bash():
    """The bash that ships with Git for Windows, found next to git; None when there is none."""
    git_exe = shutil.which("git")
    if not git_exe:
        return None
    here = os.path.dirname(os.path.realpath(git_exe))
    for rel in (("bash.exe",), ("..", "bin", "bash.exe"), ("..", "usr", "bin", "bash.exe")):
        cand = os.path.normpath(os.path.join(here, *rel))
        if os.path.isfile(cand):
            return cand
    return None


def fix_index_binary(ctx):
    if not fix_scope_allows(data_bin()):
        return "skipped", "%s is outside ORCH_DATA; a development copy installs only there" % tilde(data_bin())
    script = os.path.join(BIN_DIR, "orch-install-code-index.sh")
    if not os.path.isfile(script):
        return "failed", "%s is missing" % tilde(script)
    if os.name == "nt":
        bash = windows_bash()
        if not bash:
            return "skipped", "no bash next to git; run %s in Git Bash" % tilde(script)
    else:
        bash = shutil.which("bash") or "/bin/bash"
    try:
        res = subprocess.run([bash, script, "--dest", data_bin()], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, timeout=INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "failed", "the installer ran over %d s and was stopped" % INSTALL_TIMEOUT
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed", type(exc).__name__
    line = last_line(res.stdout.decode("utf-8", "replace"))
    if res.returncode == 0:
        if find_index() is None:
            return "failed", "the installer finished but no orch-index binary was found (%s)" % line
        return "done", line
    if res.returncode == 3:      # disabled by ORCH_INDEX_AUTOINSTALL=0, or no release for this platform
        return "skipped", line
    return "failed", line


def fix_index_marker(ctx):
    if not ctx.common_dir:
        return "skipped", "not in a git repo"
    path = os.path.join(ctx.common_dir, "orch-index", "indexing")
    if not fix_scope_allows(path):
        return "skipped", "the marker is outside ORCH_DATA / ORCH_DIR; a development copy leaves it alone"
    try:
        with open(path, "rb") as fh:
            first = fh.read()
        mtime = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return "skipped", "the marker is already gone"
    except OSError as exc:
        return "failed", type(exc).__name__
    m = re.match(rb"pid=(\d+)\b", first.strip())
    pid = int(m.group(1)) if m else None
    age = time.time() - mtime / 1e9
    if marker_is_live(pid, age):
        return "skipped", "a rebuild holds the marker (live)"
    try:     # compare, then remove: the marker must still be the one that was judged dead
        with open(path, "rb") as fh:
            again = fh.read()
        if again != first or os.stat(path).st_mtime_ns != mtime:
            return "skipped", "the marker changed while it was being checked"
        os.unlink(path)
    except FileNotFoundError:
        return "skipped", "the marker is already gone"
    except OSError as exc:
        return "failed", type(exc).__name__
    return "done", "removed the dead indexing marker"


FIXERS = [      # in this order: folders, pointer, binary, marker
    ("install.data", fix_data_folders),
    ("install.pointer", fix_pointer),
    ("index.binary", fix_index_binary),
    ("index.marker", fix_index_marker),
]


def apply_fixes(results, ctx):
    """Run each fixer whose check is a problem with an `auto` fix. Returns [{"id","result","detail"}]."""
    by_id = dict((r["id"], r) for r in results)
    fixed = []
    for check_id, fn in FIXERS:
        r = by_id.get(check_id)
        if r is None or r["status"] == "ok" or r["fix"]["kind"] != "auto":
            continue
        try:
            outcome, detail = fn(ctx)
        except Exception as exc:     # a fixer that raises is a failed fix; the next one still runs
            outcome, detail = "failed", "internal: %s" % type(exc).__name__
        fixed.append({"id": check_id, "result": outcome, "detail": detail})
    return fixed


# ---- running and printing ------------------------------------------------------------------------------------------

def run_checks(ctx):
    results = []
    for check_id, area, title, fn in CHECKS:
        try:
            status, detail, kind, text = fn(ctx)
        except Exception as exc:    # one broken check never stops the others
            status, detail, kind, text = "fail", "internal: %s" % type(exc).__name__, "none", ""
        results.append(result(check_id, area, title, status, detail, kind, text))
    return results


def counts(results):
    c = {"ok": 0, "warn": 0, "fail": 0}
    for r in results:
        c[r["status"]] += 1
    return c


def report(ctx, results):
    return {"schema": SCHEMA, "repo": ctx.repo, "checks": results, "counts": counts(results)}


MARKS = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}


def render_text(rep):
    lines = []
    for area in AREAS:
        rows = [r for r in rep["checks"] if r["area"] == area]
        if not rows:
            continue
        lines.append(area)
        for r in rows:
            lines.append("  %s %s: %s" % (MARKS[r["status"]], r["title"], r["detail"]))
            if r["status"] != "ok" and r["fix"]["kind"] != "none":
                lines.append("       fix (%s): %s" % (r["fix"]["kind"], r["fix"]["text"]))
    c = rep["counts"]
    lines.append("%d ok, %d warn, %d fail" % (c["ok"], c["warn"], c["fail"]))
    return lines


def exit_code(rep):
    return 2 if rep["counts"]["fail"] else 0


def cmd_doctor(ns, extra):
    ctx = Ctx(ns.repo)
    fixed = None
    if ns.fix:
        if not writes_allowed():
            raise OrchError("development copy: --fix changes real orch data, so it needs the installed copy or "
                            "ORCH_DIR / ORCH_DATA set; nothing was changed", 2)
        fixed = apply_fixes(run_checks(ctx), ctx)
        ctx = Ctx(ns.repo)      # the facts the checks cache are stale after a fix
    rep = report(ctx, run_checks(ctx))
    if fixed is not None:
        rep["fixed"] = fixed
    if ns.json:
        print(json.dumps(rep, indent=2))
    else:
        if fixed is not None:
            for f in fixed:
                print("fix %s: %s (%s)" % (f["id"], f["result"], f["detail"]))
            if not fixed:
                print("nothing to fix automatically")
        for line in render_text(rep):
            print(line)
    return exit_code(rep)


def register(add):
    sp = add("doctor", cmd_doctor, "check orch's own setup (index, install, settings, tasks) and say how to fix each problem")
    sp.add_argument("--json", action="store_true", help="JSON report")
    sp.add_argument("--repo", default=None, help="the repo to check (default: the current directory's)")
    sp.add_argument("--fix", action="store_true", help="apply the safe automatic fixes, then check again")
