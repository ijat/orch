"""orch dashboard: JSON computed from the task files, and a tiny local server for it (127.0.0.1, GET/HEAD only).

Read-only on the task tree: every file is opened without following symlinks, must be a regular file, and has a
size cap. The server answers only `/` (dashboard.html) and `/dashboard.json`; nothing else on disk is ever served.
"""
import base64
import calendar
import datetime
import errno
import hashlib
import http.server
import json
import os
import re
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time

from . import plan as pl
from . import questions as qs
from . import status as st
from .paths import OrchError, orch_root, tilde

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "dashboard.html")

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_FILE = 32 * 1024 * 1024
MAX_PLAN = 4 * 1024 * 1024
MAX_EVENTS = 30
MAX_ROLES = 20
MAX_QUESTIONS = 20
MAX_TARGET = 65536
MAX_CONNS = 64           # simultaneous connections; one more is closed at once
REQUEST_DEADLINE = 10.0  # seconds a connection may live in total (reads, reply and drain)
WATCH = ("status.md", "log.md", "journal.jsonl", "questions.md", "plan.md", "roadmap.md", "retro.md")
MILESTONE_DIR_RE = re.compile(r"^m\d\d-")

STATE_WORDS = {
    "planning": "Planning",
    "awaiting_approval": "Waiting for your approval",
    "implementing": "Building",
    "reviewing": "Reviewing",
    "awaiting_merge": "Ready to merge",
    "paused_usage_limit": "Paused: usage limit",
    "done": "Done",
    "abandoned": "Abandoned",
}
FINISHED = ("done", "abandoned", "merged")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")
DATE_RE = re.compile(r"\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d)?Z?)?|\d\d:\d\d(?::\d\d)?")
INT_TOKEN_RE = re.compile(r"\b\d+\b")


# -- safe reading ------------------------------------------------------------------------------------

def read_file(path, cap=None):
    """Bytes of a regular file (no symlink, no fifo, at most `cap` bytes), or None."""
    cap = MAX_FILE if cap is None else cap
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = None
            data = fh.read(cap + 1)
        return None if len(data) > cap else data
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def read_text(path, cap=None):
    data = read_file(path, cap)
    return None if data is None else data.decode("utf-8", "replace")


def real_dirs(path):
    """Sorted names of the real (not symlinked) directories in `path`, dot names skipped."""
    try:
        with os.scandir(path) as it:
            return sorted(e.name for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False))
    except OSError:
        return []


def stat_regular(path):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def task_dirs(root):
    """[(repo, slug, dir)] for every <repo>/<slug>/status.md that is a regular file."""
    out = []
    for repo in real_dirs(root):
        for slug in real_dirs(os.path.join(root, repo)):
            d = os.path.join(root, repo, slug)
            if stat_regular(os.path.join(d, "status.md")):
                out.append((repo, slug, d))
    return out


def watched(task_dir):
    """The files the data comes from: the fixed list, and m<NN>-*/plan.md one level down."""
    paths = [os.path.join(task_dir, n) for n in WATCH]
    for name in real_dirs(task_dir):
        if MILESTONE_DIR_RE.match(name):
            paths.append(os.path.join(task_dir, name, "plan.md"))
    return paths


def signature(root):
    """Cache key: (path, inode, mtime_ns, size) of every watched file. An atomic replace changes the inode, a
    migrate-style write that restores the mtime still changes it, a same-size edit changes the mtime."""
    sig = []
    for repo, slug, d in task_dirs(root):
        for p in watched(d):
            info = stat_regular(p)
            if info is not None:
                sig.append((p, info.st_ino, info.st_mtime_ns, info.st_size))
    return tuple(sig)


# -- small helpers -----------------------------------------------------------------------------------

def clean(value, limit=80):
    """A journal or file value as safe short text: control characters become spaces, long values are clipped."""
    if value is None:
        return "unknown"
    text = CTRL_RE.sub(" ", str(value)).strip()
    if not text:
        return "unknown"
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


def cell(value, limit=160):
    """A status cell: empty or missing is null."""
    if value is None:
        return None
    text = CTRL_RE.sub(" ", str(value)).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def iso_minute(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def now_epoch():
    fixed = os.environ.get("ORCH_NOW")
    if fixed and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$", fixed):
        return calendar.timegm(time.strptime(fixed, "%Y-%m-%dT%H:%MZ"))
    return time.time()


def plural(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


# -- events ------------------------------------------------------------------------------------------

def event_words(kind, rec):
    """The sentence for a journal line, or None when the kind is not shown."""
    if kind == "spawn":
        return "Started %s (%s)" % (clean(rec.get("role")), clean(rec.get("tier")))
    if kind == "return":
        return "%s finished: %s" % (clean(rec.get("role")), clean(rec.get("outcome")))
    if kind == "review_round":
        return "%s review round %s: %s" % (clean(rec.get("reviewer")), clean(rec.get("round"), 12),
                                           clean(rec.get("verdict")))
    if kind == "auto_merge":
        sha = rec.get("sha")
        sha7 = sha[:7] if isinstance(sha, str) and re.match(r"^[0-9a-fA-F]{7,64}$", sha) else "unknown"
        return "Merged %s into %s" % (sha7, clean(rec.get("base")))
    if kind == "gate":
        return "%s gate: %s" % (clean(rec.get("gate")), clean(rec.get("outcome")))
    if kind == "pause":
        n = rec.get("usage_5h")
        return "Paused for usage (5h %d%%)" % n if is_int(n) else "Paused for usage"
    if kind == "resume":
        return "Resumed"
    if kind == "checkpoint":
        if rec.get("asked") is not True:
            return None
        n = rec.get("questions")
        return "Asked you %s" % plural(n, "question", "questions") if is_int(n) and n >= 0 else "Asked you question(s)"
    if kind == "task_end":
        return "Task ended: %s" % clean(rec.get("outcome"))
    return None


class Journal(object):
    """One pass over journal.jsonl: the facts the dashboard needs."""

    def __init__(self, text, task_id):
        self.events = []          # (ts, index, {"ts","id","kind","words"})
        self.tokens = None
        self.usage_first = None
        self.usage_last = None
        self.milestones_done = set()
        self.end = None           # (ts, outcome) of the newest task_end
        if text is None:
            return
        for i, line in enumerate(text.split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("kind") if isinstance(rec.get("kind"), str) else rec.get("event")
            if not isinstance(kind, str):
                continue
            ts = rec.get("ts") if isinstance(rec.get("ts"), str) else None
            if kind == "return" and is_int(rec.get("tokens")) and rec["tokens"] >= 0:
                self.tokens = (self.tokens or 0) + rec["tokens"]
            if kind == "spawn":
                five, seven = rec.get("usage_5h"), rec.get("usage_7d")
                if is_int(five) or is_int(seven):
                    pair = {"5h": five if is_int(five) else None, "7d": seven if is_int(seven) else None}
                    if self.usage_first is None:
                        self.usage_first = pair
                    self.usage_last = pair
            if kind == "milestone_end" and rec.get("outcome") in ("done", "merged"):   # old programs wrote `merged`
                m = rec.get("milestone")
                self.milestones_done.add(m if is_int(m) else str(m))
            if kind == "task_end":
                self.end = (ts, rec.get("outcome"))
            words = event_words(kind, rec)
            if words is not None and ts is not None:
                self.events.append((ts[:32], i, {"ts": clean(ts, 32), "id": task_id, "kind": kind, "words": words}))
        self.events = sorted(self.events, key=lambda e: (e[0], e[1]))[-MAX_EVENTS:]


# -- plan, roadmap, questions --------------------------------------------------------------------------

def roadmap_rows(text):
    """Number of milestone rows in roadmap.md's table (header cell `M`, data rows `1` or `M1`)."""
    if text is None:
        return None
    count, in_table, in_comment = 0, False, False
    for line in text.split("\n"):
        s = line.strip()
        if in_comment:
            in_comment = "-->" not in s
            continue
        if s.startswith("<!--") and "-->" not in s:
            in_comment = True
            continue
        if not s.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not in_table:
            if cells and cells[0] == "M":
                in_table = True
            continue
        if re.match(r"^M?\d+$", cells[0]):
            count += 1
    return count if count else None


def step_total(task_dir, program, milestone_no):
    """Step blocks of the plan the task is working from (the milestone's plan in a program)."""
    if program:
        if milestone_no is None:
            return None
        hits = [n for n in real_dirs(task_dir) if n.startswith("m%02d-" % milestone_no)]
        if len(hits) != 1:
            return None
        path = os.path.join(task_dir, hits[0], "plan.md")
    else:
        path = os.path.join(task_dir, "plan.md")
    text = read_text(path, MAX_PLAN)
    if text is None:
        return None
    try:
        n = pl.parse_plan(text).step_count()
    except Exception:
        return None
    return n or None


def steps_done_from(rows, total):
    """Largest integer in an implementer row's `last completed step` cell that is <= total."""
    if not total:
        return None
    best = None
    for role, row in rows:
        if not role.lower().startswith("implementer"):
            continue
        text = DATE_RE.sub(" ", row.get("step") or "")
        for tok in INT_TOKEN_RE.findall(text):
            n = int(tok)
            if n <= total and (best is None or n > best):
                best = n
    return best


def questions_of(task_dir):
    try:
        text = read_file(os.path.join(task_dir, "questions.md"))
        if text is None:
            return 0, []
        lines = st.split_lines(text.decode("utf-8", "surrogateescape"))
        opened = [q for q in qs.parse(lines) if q.status == "open"]
    except Exception:
        return 0, []
    out = []
    for q in opened[:MAX_QUESTIONS]:
        out.append({"id": "Q%d" % q.n, "text": cell(q.text, 200), "blocks": cell(q.fields.get("blocks"), 60)})
    return len(opened), out


def log_tail(task_dir):
    text = read_text(os.path.join(task_dir, "log.md"))
    if text is None:
        return None
    last = None
    for line in text.split("\n"):
        if line.strip().startswith("-"):
            last = line.strip()
    return cell(last.lstrip("- ").strip(), 200) if last else None


def raw_state(text, state):
    """The state as status.md spells it (orch-index.sh's rules drop digits), for the 'Unknown state' words."""
    m = re.search(r"^- task_state:[ \t]*(\S+)", text, re.M)
    if m and re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", m.group(1)):
        return m.group(1)
    return state


def first_word(value):
    v = (value or "").strip().lower()
    m = re.match(r"^[a-z][a-z0-9_-]*", v)
    return m.group(0) if m else None


# -- the data ----------------------------------------------------------------------------------------

def build_task(repo, slug, d, status_text, state, journal):
    """(task dict, mtime) for one task. The age is filled in later (it changes without file changes)."""
    status = st.Status(status_text.encode("utf-8", "surrogateescape"))
    mtimes = [info.st_mtime for info in (stat_regular(os.path.join(d, n)) for n in ("status.md", "log.md")) if info]
    mtime = max(mtimes) if mtimes else 0
    try:
        rows = status.roles()
    except OrchError:
        rows = []
    shape = first_word(status.get("shape"))
    program = shape == "roadmap" or stat_regular(os.path.join(d, "roadmap.md")) is not None
    ms_text = status.get("milestone") or ""
    m = re.match(r"^M?(\d+)\b", ms_text)
    ms_no = int(m.group(1)) if m else None
    roles = [{"role": cell(r, 60), "state": cell(row.get("state"), 40), "step": cell(row.get("step"), 60),
              "note": cell(row.get("notes"), 160)} for r, row in rows[:MAX_ROLES]]
    total = step_total(d, program, ms_no)
    done = steps_done_from(rows, total)
    n_open, open_qs = questions_of(d)
    current = None   # what is going on now: the first working role that is not the orchestrator
    for want in ("in_progress", "waiting", "blocked"):
        for r in roles:
            if r["state"] == want and not (r["role"] or "").startswith("orchestrator"):
                current = cell("%s: %s" % (r["role"], r["note"] or r["state"]), 160)
                break
        if current:
            break
    wt = status.get("worktree")
    wt = re.sub(r"\s*\(.*$", "", wt).strip() if wt else None
    ms_total = roadmap_rows(read_text(os.path.join(d, "roadmap.md"))) if program else None
    paused = state == "paused_usage_limit"
    task = {
        "id": "%s/%s" % (repo, slug), "repo": repo, "slug": slug, "state": state,
        "state_words": STATE_WORDS.get(state, "Unknown state: %s" % clean(raw_state(status_text, state), 40)),
        "mode": first_word(status.get("mode")), "shape": shape,
        "milestone": None if not ms_text or ms_text.lower() == "n/a" else cell(ms_text, 60),
        "milestones_done": len(journal.milestones_done) if program else None,
        "milestones_total": ms_total if program else None,
        "steps_done": done, "steps_total": total,
        "percent": int(100.0 * done / total + 0.5) if done is not None and total else None,
        "current": current, "roles": roles,
        "questions_open": n_open, "questions": open_qs,
        "last_log": log_tail(d), "updated": iso_minute(mtime) if mtime else None, "updated_age_min": None,
        "tokens_reported": journal.tokens, "usage_first": journal.usage_first, "usage_last": journal.usage_last,
        "paused": paused, "worktree": cell(wt, 200), "task_dir": tilde(d),
    }
    return task, mtime


def compute(root):
    """The dashboard data for `root`, without the time-dependent fields (see `stamp`)."""
    tasks, merged, abandoned, events = [], [], [], []
    for repo, slug, d in task_dirs(root):
        text = read_text(os.path.join(d, "status.md"))
        if text is None:
            continue
        tid = "%s/%s" % (repo, slug)
        journal = Journal(read_text(os.path.join(d, "journal.jsonl")), tid)
        events.extend(journal.events)
        state = st.classify(text.replace("\r\n", "\n").replace("\r", "\n"), d)
        if state in FINISHED:
            info = stat_regular(os.path.join(d, "status.md"))
            ended = journal.end[0] if journal.end and journal.end[0] else (iso_minute(info.st_mtime) if info else None)
            outcome = journal.end[1] if journal.end else None
            row = {"id": tid, "ended": cell(ended, 32)}
            (abandoned if state == "abandoned" or outcome in ("abandoned", "declined") else merged).append(row)
            continue
        task, mtime = build_task(repo, slug, d, text, state, journal)
        task["_mtime"] = mtime
        tasks.append(task)
    tasks.sort(key=lambda t: (-t["_mtime"], t["id"]))
    key = lambda r: (r["ended"] or "", r["id"])
    merged.sort(key=key, reverse=True)
    abandoned.sort(key=key, reverse=True)
    events = sorted(events, key=lambda e: (e[0], e[1], e[2]["id"]), reverse=True)[:MAX_EVENTS]
    return {"tasks": tasks, "events": [e[2] for e in events], "finished": {"merged": merged, "abandoned": abandoned}}


def stamp(base, now, page_version):
    """A copy of the computed data with `generated`, `page_version` and the ages (the only time-dependent parts)."""
    tasks = []
    for t in base["tasks"]:
        t = dict(t)
        mtime = t.pop("_mtime")
        t["updated_age_min"] = max(0, int((now - mtime) / 60)) if t["updated"] else None
        tasks.append(t)
    return {"generated": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_version": page_version, "tasks": tasks, "events": base["events"], "finished": base["finished"]}


class Dashboard(object):
    """Cached computation over `root`; recomputed only when the signature of the watched files changes."""

    def __init__(self, root, html_path=None):
        self.root = root
        self.html_path = html_path or HTML_PATH
        self.lock = threading.Lock()
        self._sig = None
        self._base = None
        self._page_sig = None
        self._page = None
        self.computes = 0

    def page(self):
        """(html bytes, page_version) or (None, None) when dashboard.html is missing or unreadable."""
        info = stat_regular(self.html_path)
        if info is None:
            return None, None
        sig = (info.st_ino, info.st_mtime_ns, info.st_size)
        if sig != self._page_sig:
            data = read_file(self.html_path, 4 * 1024 * 1024)
            self._page = (data, hashlib.sha256(data).hexdigest()[:12]) if data is not None else (None, None)
            self._page_sig = sig
        return self._page

    def data(self, now=None):
        now = now_epoch() if now is None else now
        with self.lock:
            sig = signature(self.root)
            if sig != self._sig or self._base is None:
                self._base = compute(self.root)
                self._sig = sig
                self.computes += 1
            return stamp(self._base, now, self.page()[1])

    def json_bytes(self, now=None):
        text = json.dumps(self.data(now), separators=(",", ":"))
        # `<`, `>` and `&` only occur inside JSON strings, so the escapes keep the data and drop raw markup
        return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode("ascii")


# -- the server --------------------------------------------------------------------------------------

ERROR_BODIES = {
    400: b"Bad request\n", 404: b"Not found\n", 405: b"Method not allowed\n", 414: b"Request too long\n",
    431: b"Headers too large\n", 500: b"Server error\n", 505: b"Version not supported\n",
}


LINK_ORIGIN_RE = re.compile(rb"""<link\b[^>]*?\shref=["'](https://[A-Za-z0-9.-]+(?::\d+)?)(?=[/"'])""", re.I)


def csp_for(html):
    """A policy that lets only the page's own inline script run (by hash) and only the origins its `<link>` tags
    name be fetched for styles and fonts. A page with no such link gets none."""
    hashes = ["'sha256-%s'" % base64.b64encode(hashlib.sha256(m).digest()).decode("ascii")
              for m in re.findall(rb"<script>(.*?)</script>", html, re.S)]
    origins = []
    for m in LINK_ORIGIN_RE.findall(html):
        o = m.decode("ascii")
        if o not in origins:
            origins.append(o)
    style = " ".join(["'unsafe-inline'"] + origins)
    font = "font-src %s; " % " ".join(origins) if origins else ""
    return ("default-src 'none'; script-src %s; style-src %s; %sconnect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % (" ".join(hashes) or "'none'", style, font))


class Handler(http.server.BaseHTTPRequestHandler):
    """GET and HEAD for two exact routes. Nothing here writes, and nothing maps a request path to a file."""

    server_version = "orch-dashboard"
    sys_version = ""
    timeout = 5
    _timer = None

    def setup(self):
        """Start the connection's total deadline: `timeout` limits one read, so a client sending a byte every few
        seconds would otherwise keep its thread for as long as it likes. Shutting the socket down unblocks any
        read or write that is waiting."""
        http.server.BaseHTTPRequestHandler.setup(self)
        self._timer = threading.Timer(REQUEST_DEADLINE, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except (OSError, ValueError):
            pass

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        http.server.BaseHTTPRequestHandler.end_headers(self)

    def reply(self, code, ctype, body, extra=()):
        if self.request_version == "HTTP/0.9":   # the base class sends no status line or headers to a 0.9 client
            self.request_version = "HTTP/1.0"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD" and code >= 200 and code not in (204, 304):
            try:
                self.wfile.write(body)
            except OSError:   # the client went away
                pass

    def send_error(self, code, message=None, explain=None):
        """Fixed bodies only: the request line, path and headers are never echoed."""
        if code == 501:   # a method this handler has no do_<METHOD> for
            code = 405
        body = ERROR_BODIES.get(code, b"Error\n")
        extra = [("Allow", "GET, HEAD")] if code == 405 else []
        self.reply(code, "text/plain; charset=utf-8", body, extra)

    def target(self):
        """The request target as sent (the base class rewrites a leading `//`, differently by Python version)."""
        parts = (self.requestline or "").split()
        return parts[1] if len(parts) >= 2 else self.path

    def host_ok(self):
        values = self.headers.get_all("Host") or []
        if len(values) != 1:
            return False
        port = self.server.server_address[1]
        allowed = ["127.0.0.1:%d" % port, "localhost:%d" % port]
        if port == 80:
            allowed += ["127.0.0.1", "localhost"]
        return values[0].strip().lower() in allowed

    def serve(self):
        if self.request_version == "HTTP/0.9":   # a request line with no version: refused, like any odd form
            return self.send_error(400)
        if not self.host_ok():
            return self.send_error(400)
        target = self.target()
        if not target.startswith("/") or len(target) > MAX_TARGET:
            return self.send_error(400 if not target.startswith("/") else 414)
        route = target.split("?", 1)[0]
        dash = self.server.dashboard
        if route == "/":
            body, _ = dash.page()
            if body is None:
                return self.send_error(500)
            return self.reply(200, "text/html; charset=utf-8", body, [("Content-Security-Policy", csp_for(body))])
        if route == "/dashboard.json":
            try:
                body = dash.json_bytes()
            except Exception:
                return self.send_error(500)
            return self.reply(200, "application/json; charset=utf-8", body)
        return self.send_error(404)

    def do_GET(self):
        self.serve()

    def do_HEAD(self):
        self.serve()

    def finish(self):
        """A refused request may carry an unread body; closing on it would reset the client before it reads
        the reply. Drain a little, then close."""
        try:
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            self.connection.settimeout(0.3)
            left, deadline = 1 << 20, time.time() + 1.0
            while left > 0 and time.time() < deadline:
                chunk = self.connection.recv(65536)
                if not chunk:
                    break
                left -= len(chunk)
        except (OSError, ValueError):
            pass
        finally:
            if self._timer is not None:
                self._timer.cancel()
        http.server.BaseHTTPRequestHandler.finish(self)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request, client_address):
        """At most MAX_CONNS connections are served at once; one more is closed without a reply."""
        if not self.slots.acquire(False):
            self.close_request(request)
            return
        socketserver.ThreadingMixIn.process_request(self, request, client_address)

    def shutdown_request(self, request):
        try:
            http.server.HTTPServer.shutdown_request(self, request)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        """A broken connection is not worth a traceback on the terminal."""
        if not isinstance(sys.exc_info()[1], (OSError, ValueError)):
            http.server.HTTPServer.handle_error(self, request, client_address)

    def __init__(self, port, dashboard):
        self.dashboard = dashboard
        self.slots = threading.BoundedSemaphore(MAX_CONNS)
        http.server.HTTPServer.__init__(self, (HOST, port), Handler)


def make_server(root, port=DEFAULT_PORT, html_path=None):
    """A bound (not yet serving) server on 127.0.0.1. Raises OrchError (exit 1) when the port is taken."""
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise OrchError("--port must be 0-65535", 1)
    if port:
        # SO_REUSEADDR (kept so a quick restart works) lets macOS bind beside a wildcard listener, so ask first
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            taken = probe.connect_ex((HOST, port)) == 0
        finally:
            probe.close()
        if taken:
            raise OrchError("port %d is already in use on %s" % (port, HOST), 1)
    try:
        return Server(port, Dashboard(root, html_path))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise OrchError("port %d is already in use on %s" % (port, HOST), 1)
        raise OrchError("cannot listen on %s:%d: %s" % (HOST, port, exc.strerror or exc), 1)


def serve(root, port, open_browser):
    server = make_server(root, port)
    url = "http://%s:%d/" % (HOST, server.server_address[1])
    print("serving %s (Ctrl-C to stop)" % url)
    sys.stdout.flush()
    if open_browser:
        if sys.platform == "darwin":
            try:
                subprocess.run(["open", url], check=False)
            except OSError as exc:
                sys.stderr.write("warning: could not open the browser: %s\n" % exc)
        else:
            sys.stderr.write("warning: --open only works on macOS; open %s yourself\n" % url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# -- command -----------------------------------------------------------------------------------------

def cmd_dashboard(ns, extra):
    if ns.serve == ns.json_out:
        raise OrchError("give exactly one of --json or --serve", 1)
    root = orch_root()
    if ns.json_out:
        sys.stdout.write(json.dumps(Dashboard(root).data(), indent=2) + "\n")
        return 0
    return serve(root, ns.port, ns.open)


def register(add):
    sp = add("dashboard", cmd_dashboard, "JSON or a local server (127.0.0.1) for the task dashboard")
    sp.add_argument("--json", dest="json_out", action="store_true")
    sp.add_argument("--serve", action="store_true")
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--open", action="store_true")
