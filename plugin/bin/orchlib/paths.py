"""Task resolution, locking, atomic writes, timestamps, index refresh."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import datetime

if os.name == "nt":
    import msvcrt
else:
    import fcntl

BIN_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TEMPLATES_DIR = os.path.normpath(os.path.join(BIN_DIR, "..", "skills", "orchestrate", "templates"))

NOW_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$")


class OrchError(Exception):
    """An error with an exit code: 1 usage/validation, 2 refused, 3 internal."""

    def __init__(self, message, code=1):
        Exception.__init__(self, message)
        self.code = code


# One data root for everything orch stores; every orch-owned path below derives from it. ORCH_DATA moves the whole
# root (tasks, logs, cache, the orch-index binary, private files); ORCH_DIR moves only the tasks root. The tasks root
# holds repo folders, ACTIVE.md and LESSONS.md only. (Before the plugin, tasks lived in ~/.claude/orchestrations; a
# compat link there points at TASKS during the migration.)
def data_root():
    return os.environ.get("ORCH_DATA") or os.path.join(os.path.expanduser("~"), ".claude", "orch-data")


def orch_root():
    """TASKS: the tasks root. ORCH_DIR overrides it, even when ORCH_DATA is set."""
    return os.environ.get("ORCH_DIR") or os.path.join(data_root(), "tasks")


def logs_dir():
    """LOGS: append-only logs (delegate-hook.jsonl, _agent-usage.jsonl)."""
    return os.path.join(data_root(), "logs")


def cache_dir():
    """CACHE: derived state that can be rebuilt (usage pacing, orch-ui's format dir)."""
    return os.path.join(data_root(), "cache")


def data_bin():
    """DATA_BIN: the orch shim and the orch-index binary."""
    return os.path.join(data_root(), "bin")


def pointer_path():
    """POINTER: a link to the live plugin root, written only by the installed copy (update_pointer)."""
    return os.path.join(data_root(), "plugin")


def private_dir():
    """PRIVATE: the denylist and the dashboard head file."""
    return os.path.join(data_root(), "private")


def backups_dir():
    return os.path.join(data_root(), "backups")


def denylist_path():
    return os.path.join(private_dir(), "orch-export-denylist.txt")


def plugins_cache():
    """The tree Claude Code installs plugins into."""
    return os.path.join(os.path.expanduser("~"), ".claude", "plugins", "cache")


def _inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path != root and path.startswith(root.rstrip(os.sep) + os.sep)


def is_live():
    """True iff this code runs from the copy Claude Code installed (inside ~/.claude/plugins/cache/). Every other
    copy (a checkout, a worktree, an archive, a copied tree, a --plugin-dir target) is a development copy."""
    return _inside(BIN_DIR, plugins_cache())


def writes_allowed():
    """The one live-versus-dev decision: the installed copy, or a copy told where to write (ORCH_DIR or ORCH_DATA)."""
    return is_live() or bool(os.environ.get("ORCH_DIR")) or bool(os.environ.get("ORCH_DATA"))


def require_write_allowed():
    if not writes_allowed():
        raise OrchError("development copy: set ORCH_DIR (only the plugin copy installed under ~/.claude/plugins/cache "
                        "writes the real orch data unless told where to write)", 1)


def update_pointer(root):
    """Point POINTER at realpath(root), only when this copy is live and root is this copy's own plugin root.
    A temp link plus os.replace, so readers never see the link missing. Returns True when the link changed."""
    if not is_live():
        return False
    target = os.path.realpath(root)
    if target != os.path.realpath(os.path.join(BIN_DIR, "..")):
        return False
    link = pointer_path()
    try:
        if os.readlink(link) == target:
            return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(link), exist_ok=True)
    tmp = "%s.tmp-%d" % (link, os.getpid())
    try:
        os.unlink(tmp)
    except OSError:
        pass
    os.symlink(target, tmp)
    try:
        os.replace(tmp, link)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


AGENT_PREFIX_RE = re.compile(r"^orch:")


def agent_key(agent_type):
    """Plugin agent names (`orch:<role>`) in today's spelling (`orch-<role>`), before any agent-type comparison, so old
    and new journals share every label. Anything else is returned unchanged (`foo:planner` and `orch:orch-planner`
    match no role). role-cost.py keeps its own copy (it is loaded by path); a test pins the two together."""
    if isinstance(agent_type, str):
        return AGENT_PREFIX_RE.sub("orch-", agent_type, count=1)
    return agent_type


def now():
    fixed = os.environ.get("ORCH_NOW")
    if fixed:
        if not NOW_RE.match(fixed):
            raise OrchError("ORCH_NOW must look like 2026-01-02T03:04Z, got %r" % fixed, 1)
        return fixed
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def tilde(path):
    """Display form: ~/… when the path is under the home directory."""
    home = os.path.expanduser("~")
    if home and home != "/" and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def task_names(task_dir):
    """(repo, slug) of a task dir: its parent's and its own basename."""
    task_dir = os.path.normpath(task_dir)
    return os.path.basename(os.path.dirname(task_dir)), os.path.basename(task_dir)


def resolve_task(arg, must_exist=True):
    """TASK = absolute dir (or ~/…) or <repo>/<slug> under ORCH_DIR. Returns an absolute dir."""
    if not arg:
        raise OrchError("no task: pass -t TASK (absolute dir or <repo>/<slug>)", 1)
    if arg.startswith("~"):
        arg = os.path.expanduser(arg)
    if os.path.isabs(arg):
        path = os.path.normpath(arg)
    else:
        parts = [p for p in arg.split("/") if p not in ("", ".")]
        if len(parts) != 2 or ".." in parts:
            raise OrchError("TASK must be an absolute dir or <repo>/<slug>, got %r" % arg, 1)
        path = os.path.join(orch_root(), parts[0], parts[1])
    if must_exist and not os.path.isdir(path):
        raise OrchError("no such task dir: %s" % path, 1)
    return path


class TaskLock(object):
    """An exclusive lock on TASK/.orch.lock around a read-modify-write (fcntl.flock; msvcrt.locking on Windows)."""

    def __init__(self, task_dir):
        self.path = os.path.join(task_dir, ".orch.lock")
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if os.name == "nt":
            # LK_LOCK gives up after 10 tries a second apart; keep trying, as flock would block.
            while True:
                try:
                    msvcrt.locking(self.fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    pass
        else:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
        return False


def write_atomic(path, data, mode=None):
    """Write bytes to a temp file in the same dir, then os.replace; keeps the existing mode."""
    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            mode = 0o644
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".%s.tmp-" % os.path.basename(path), dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_bytes(path, data):
    """Append with one os.write; O_APPEND keeps concurrent writers from interleaving."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OrchError("short write to %s (%d of %d bytes)" % (path, written, len(data)), 3)
    finally:
        os.close(fd)


def ends_with_newline(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return True
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"
    except OSError:
        return True


def refresh_index(warn):
    """Regenerate ACTIVE.md (orch-index.sh). Failure is a warning, never an error."""
    if os.environ.get("ORCH_NO_INDEX") == "1":
        return
    script = os.path.join(BIN_DIR, "orch-index.sh")
    if not os.path.exists(script):
        warn("index not refreshed: %s missing" % script)
        return
    argv = [script]
    if os.name == "nt":
        # Windows can't exec a .sh; Claude Code there runs on Git Bash, which provides bash.
        bash = shutil.which("bash")
        if not bash:
            warn("index not refreshed: no bash on PATH")
            return
        argv = [bash, script]
    try:
        res = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        if res.returncode != 0:
            warn("index refresh failed (rc %d): %s" % (res.returncode, res.stderr.decode("utf-8", "replace").strip()[:200]))
    except (OSError, subprocess.SubprocessError) as exc:
        warn("index refresh failed: %s" % exc)


def warn_stderr(message):
    sys.stderr.write("warning: %s\n" % message)
