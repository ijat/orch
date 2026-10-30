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


def orch_root():
    return os.environ.get("ORCH_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "orchestrations")


def is_live():
    """True when this code runs from ~/.claude/bin (the installed copy)."""
    live = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "bin"))
    return os.path.realpath(BIN_DIR) == live


def require_write_allowed():
    if not is_live() and not os.environ.get("ORCH_DIR"):
        raise OrchError("development copy: set ORCH_DIR (write commands refuse to touch the real orchestrations dir)", 1)


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
