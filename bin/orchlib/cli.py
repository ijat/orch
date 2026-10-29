"""orch command line: argument parsing and the core commands (init, set, role, log, journal, show)."""
import argparse
import datetime
import glob
import os
import re
import sys
import traceback

from . import __version__
from . import journal as jr
from . import status as st
from .paths import (OrchError, TaskLock, TEMPLATES_DIR, append_bytes, ends_with_newline, now, orch_root,
                    refresh_index, require_write_allowed, resolve_task, task_names, tilde, warn_stderr,
                    write_atomic)

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _:.()-]*$")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise OrchError("%s\n%s" % (message, self.format_usage().rstrip()), 1)


# -- shared helpers ------------------------------------------------------------------------------

def one_line(text):
    return re.sub(r"\s*(\r\n|\r|\n)\s*", " ", text).strip()


def status_path(task_dir):
    return os.path.join(task_dir, "status.md")


def load_status(task_dir):
    path = status_path(task_dir)
    if not os.path.exists(path):
        raise OrchError("no status.md in %s" % task_dir, 1)
    return st.Status.load(path)


def apply_sets(status, pairs, add, enum):
    """pairs: ['F=V', …]. Validates every pair, then applies. Returns [(name, value)] actually changed."""
    parsed = []
    for p in pairs:
        if "=" not in p:
            raise OrchError("expected FIELD=VALUE, got %r" % p, 1)
        name, value = p.split("=", 1)
        value = st.single_line(value, "value of %s" % name)
        if name == "task_state" and value not in enum:
            raise OrchError("task_state must be one of: %s (got %r)" % (" | ".join(enum), value), 1)
        parsed.append((name, value))
    known = set(n for _, n, _ in status.fields())
    for name, _ in parsed:
        if not st.FIELD_NAME_RE.match(name):
            raise OrchError("bad field name %r" % name, 1)
        if name not in known and not add:
            raise OrchError("unknown field %r (use --add to create it)" % name, 1)
    changed = []
    for name, value in parsed:
        if status.set_field(name, value, add=add):
            changed.append((name, value))
    return changed


def log_line(ts, role, text):
    role = one_line(role)
    text = one_line(text)
    if not text:
        raise OrchError("log text is empty", 1)
    if not ROLE_NAME_RE.match(role):
        raise OrchError("bad role name for the log line: %r" % role, 1)
    return "- %s %s: %s\n" % (ts, role, text)


def append_log(task_dir, line):
    path = os.path.join(task_dir, "log.md")
    data = line.encode("utf-8", "surrogateescape")
    if not os.path.exists(path):
        slug = os.path.basename(task_dir)
        try:
            with open(os.path.join(TEMPLATES_DIR, "log.md"), encoding="utf-8") as fh:
                head = fh.read()
        except OSError as exc:
            raise OrchError("cannot read log template: %s" % exc, 3)
        data = (head.replace("<task-slug>", slug).rstrip("\n") + "\n").encode("utf-8") + data
    elif not ends_with_newline(path):
        data = b"\n" + data
    append_bytes(path, data)


def program_info(status):
    """(is_program, milestone number or None). A task is a program only when shape is exactly `roadmap`."""
    program = (status.get("shape") or "") == "roadmap"
    ms = status.get("milestone") or ""
    m = re.match(r"^M?(\d+)\b", ms)
    return program, (int(m.group(1)) if m else None)


def get_task(ns, must_exist=True):
    return resolve_task(getattr(ns, "task_sub", None) or ns.task, must_exist)


# -- commands ------------------------------------------------------------------------------------

def cmd_init(ns, extra):
    require_write_allowed()
    repo, slug = ns.repo, ns.slug
    if not repo or "/" in repo or repo.startswith(".") or "\n" in repo or "|" in repo:
        raise OrchError("bad repo name %r (no '/', no leading '.', no '|')" % repo, 1)
    if not SLUG_RE.match(slug):
        raise OrchError("bad slug %r (letters, digits, . _ -; must start with a letter or digit)" % slug, 1)
    if ns.mode not in ("standard", "lite", "interactive"):
        raise OrchError("--mode must be standard, lite or interactive", 1)
    for name, val in (("--repo-path", ns.repo_path), ("--base", ns.base)):
        st.single_line(val, name)
        if not val.strip():
            raise OrchError("%s is empty" % name, 1)
    request = one_line(ns.request)
    task_dir = os.path.join(orch_root(), repo, slug)
    if os.path.exists(task_dir):
        raise OrchError("exists — use `orch resume %s/%s`" % (repo, slug), 2)
    ts = now()
    enum = st.task_state_enum()
    schema = jr.load_schema()
    try:
        with open(os.path.join(TEMPLATES_DIR, "status.md"), "rb") as fh:
            status = st.Status(fh.read())
        with open(os.path.join(TEMPLATES_DIR, "log.md"), encoding="utf-8") as fh:
            log_head = fh.read()
    except OSError as exc:
        raise OrchError("cannot read templates: %s" % exc, 3)
    status.lines[0] = status.lines[0].replace("<task-slug>", slug)
    values = [
        ("task_state", "planning"), ("mode", ns.mode), ("shape", "tbd (planner decides)"), ("milestone", "n/a"),
        ("reviewed_through", "none"), ("merged_through", "none"),
        ("task_dir", tilde(task_dir)), ("repo", ns.repo_path.strip()), ("base_branch", ns.base.strip()),
        ("task_branch", "orch/%s" % slug), ("worktree", "not yet created"), ("planner_tier", "tbd"),
        ("implementer_tier", "per step (TIERS.md routing)"), ("review_round", "0/3"), ("usage_override", "none"),
    ]
    apply_sets(status, ["%s=%s" % kv for kv in values], False, enum)
    status.set_role("orchestrator", ts=ts, validate=False)
    if ns.mode == "lite":
        status.set_role("planner", state="n/a (lite)", validate=False)
    rec, _ = jr.build_record(schema, "task_start", jr.parse_pairs(["mode=%s" % ns.mode, "request=%s" % request]),
                             ts, repo, slug, False, None)
    jdata = jr.encode(rec)
    first = log_line(ts, "orchestrator", "task dir created (%s mode; repo %s; base %s)" % (ns.mode, ns.repo_path.strip(), ns.base.strip()))
    os.makedirs(os.path.dirname(task_dir), exist_ok=True)
    try:
        os.mkdir(task_dir)
    except FileExistsError:
        raise OrchError("exists — use `orch resume %s/%s`" % (repo, slug), 2)
    with TaskLock(task_dir):
        write_atomic(status_path(task_dir), status.render(), 0o644)
        write_atomic(os.path.join(task_dir, "log.md"), (log_head.replace("<task-slug>", slug).rstrip("\n") + "\n" + first).encode("utf-8"), 0o644)
        jr.append(os.path.join(task_dir, "journal.jsonl"), jdata)
    refresh_index(warn_stderr)
    print("created %s" % task_dir)
    print("journal task_start; status planning")
    return 0


def cmd_set(ns, extra):
    require_write_allowed()
    pairs = list(ns.items) + extra
    if not pairs:
        raise OrchError("set needs at least one FIELD=VALUE", 1)
    task_dir = get_task(ns)
    ts = now()
    line = log_line(ts, ns.as_role or "orchestrator", ns.log) if ns.log is not None else None
    enum = st.task_state_enum()
    with TaskLock(task_dir):
        status = load_status(task_dir)
        changed = apply_sets(status, pairs, ns.add, enum)
        if changed:
            write_atomic(status_path(task_dir), status.render())
        if line:
            append_log(task_dir, line)
    if changed:
        refresh_index(warn_stderr)
    print("set: %s" % (", ".join("%s=%s" % c for c in changed) if changed else "no change"))
    return 0


def cmd_role(ns, extra):
    require_write_allowed()
    task_dir = get_task(ns)
    ts_cell = now() if ns.ts is None else st.single_line(ns.ts, "--ts")
    line = log_line(now(), ns.as_role or ns.role, ns.log) if ns.log is not None else None
    with TaskLock(task_dir):
        status = load_status(task_dir)
        changed, skipped = status.set_role(ns.role, state=ns.state, ts=ts_cell, step=ns.step, note=ns.note)
        if changed:
            write_atomic(status_path(task_dir), status.render())
        if line:
            append_log(task_dir, line)
    if skipped:
        warn_stderr("this status.md has no column for: %s (not written)" % ", ".join(skipped))
    if changed:
        refresh_index(warn_stderr)
    print("role %s -> %s%s" % (ns.role, ns.state, "" if changed else " (no change)"))
    return 0


def cmd_log(ns, extra):
    require_write_allowed()
    task_dir = get_task(ns)
    line = log_line(now(), ns.as_role or "orchestrator", ns.text)
    with TaskLock(task_dir):
        append_log(task_dir, line)
    print("logged")
    return 0


def cmd_journal(ns, extra):
    require_write_allowed()
    task_dir = get_task(ns)
    repo, slug = task_names(task_dir)
    items = list(ns.items) + extra
    schema = jr.load_schema()
    ts = now() if ns.ts is None else st.single_line(ns.ts, "--ts")
    if not ts:
        raise OrchError("--ts is empty", 1)
    pairs = jr.parse_pairs(items)
    enum = st.task_state_enum()
    line = log_line(now(), ns.as_role or "orchestrator", ns.log) if ns.log is not None else None
    with TaskLock(task_dir):
        spath = status_path(task_dir)
        status = st.Status.load(spath) if os.path.exists(spath) else None
        program, auto_ms = program_info(status) if status else (False, None)
        if ns.kind in ("final_gate", "review_round"):
            from . import merge  # sha rules (C1): the commit comes from the repo or the review brief
            pairs = merge.bind_sha(task_dir, status, ns.kind, pairs, program, auto_ms)
        rec, missing = jr.build_record(schema, ns.kind, pairs, ts, repo, slug, program, auto_ms)
        if program and ns.kind == "auto_security" and rec.get("milestone") is None:
            raise OrchError("auto_security in a program needs milestone:=<n> (none resolvable from status.md)", 1)
        data = jr.encode(rec)
        changed = []
        if ns.set_fields:
            if status is None:
                raise OrchError("no status.md in %s (needed for --set)" % task_dir, 1)
            changed = apply_sets(status, ns.set_fields, False, enum)
        jr.append(os.path.join(task_dir, "journal.jsonl"), data)
        if changed:
            write_atomic(spath, status.render())
        if line:
            append_log(task_dir, line)
    if missing:
        warn_stderr("journal %s: no value for %s (written as null)" % (ns.kind, ", ".join(missing)))
    if changed:
        refresh_index(warn_stderr)
    print("journal %s%s" % (ns.kind, "; set " + ", ".join(n for n, _ in changed) if changed else ""))
    return 0


def open_questions(task_dir):
    path = os.path.join(task_dir, "questions.md")
    if not os.path.exists(path):
        return 0
    count, in_q, opened = 0, False, False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if re.match(r"^## Q\d+\b", line):
                if in_q and opened:
                    count += 1
                in_q, opened = True, False
            elif in_q and re.match(r"^- status:\s*open\b", line):
                opened = True
    return count + (1 if in_q and opened else 0)


def last_log_lines(task_dir, n):
    path = os.path.join(task_dir, "log.md")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        rows = [l.rstrip("\n") for l in fh if l.strip().startswith("-")]
    return rows[-n:]


def clip(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def show_task(task_dir):
    status = load_status(task_dir)
    repo, slug = task_names(task_dir)
    out = ["task: %s/%s   (%s)" % (repo, slug, tilde(task_dir))]
    fields = status.fields()[:18]
    for _, name, raw in fields:
        out.append("%s: %s" % (name, clip(st.show_value(name, raw), 100)))
    lay_rows = []
    try:
        lay_rows = status.roles()
    except OrchError as exc:
        out.append("roles: unreadable (%s)" % str(exc).split(";")[0])
    if lay_rows:
        out.append("roles:")
        for role, row in lay_rows[:10]:
            out.append("  %s | %s | %s | %s | %s" % (role, row.get("state", ""), row.get("ts", ""),
                                                     clip(row.get("step", ""), 40), clip(row.get("notes", ""), 60)))
    tail = last_log_lines(task_dir, 5)
    out.append("log (last %d):" % len(tail))
    out.extend("  " + clip(l, 120) for l in tail)
    out.append("open questions: %d" % open_questions(task_dir))
    plan = next((p for p in ("plan.md", "roadmap.md") if os.path.exists(os.path.join(task_dir, p))), None)
    out.append("plan: %s" % (os.path.join(task_dir, plan) if plan else "none"))
    if len(out) > 40:
        out = out[:39] + ["… (truncated)"]
    return out


def active_tasks(root):
    """[(repo, slug, state, worktree, updated, stale_note, last_log, dir)] for every non-finished task."""
    rows = []
    for sf in sorted(glob.glob(os.path.join(glob.escape(root), "*", "*", "status.md"))):
        d = os.path.dirname(sf)
        repo, slug = task_names(d)
        text = st.read_text_like_index(sf)
        state = st.classify(text, d)
        if state in st.INDEX_TERMINAL:
            continue
        log = os.path.join(d, "log.md")
        last = ""
        if os.path.exists(log):
            ls = [l.strip() for l in open(log, errors="replace") if l.strip().startswith("-")]
            last = ls[-1].lstrip("- ").replace("|", "/")[:100] if ls else ""
        mt = max(os.path.getmtime(p) for p in (sf, log) if os.path.exists(p))
        m = re.search(r"^- worktree:\s*(.*)$", text, re.M)
        wt = re.sub(r"\s*\(.*$", "", m.group(1).strip()) if m else ""
        stale = (datetime.datetime.now().timestamp() - mt) / 86400 > 7
        rows.append((repo, slug, state, wt or "-", datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M"),
                     " [needs triage]" if state == "unknown" and stale else "", last, d))
    return rows


def cmd_show(ns, extra):
    arg = getattr(ns, "task_sub", None) or ns.task
    if arg:
        for line in show_task(get_task(ns)):
            print(line)
        return 0
    rows = active_tasks(orch_root())
    if not rows:
        print("no active tasks")
    for repo, slug, state, wt, upd, note, last, _ in rows:
        print("%s/%s  %s%s  %s  %s  %s" % (repo, slug, state, note, wt, upd, clip(last, 80)))
    return 0


def cmd_commands(ns, extra):
    for name in COMMAND_ORDER:
        print(name)
    return 0


# -- parser --------------------------------------------------------------------------------------

COMMAND_ORDER = []
HANDLERS = {}


def build_parser():
    p = Parser(prog="orch", description="orchestration bookkeeping: one call per state transition",
               allow_abbrev=False)
    p.add_argument("--version", action="version", version="orch %s" % __version__)
    p.add_argument("-t", "--task", dest="task", default=None, help="task dir or <repo>/<slug> under $ORCH_DIR")
    sub = p.add_subparsers(dest="command", metavar="COMMAND", parser_class=Parser)

    def add(name, handler, help_text):
        sp = sub.add_parser(name, help=help_text, allow_abbrev=False)
        sp.add_argument("-t", "--task", dest="task_sub", default=argparse.SUPPRESS)
        HANDLERS[name] = handler
        if name not in COMMAND_ORDER:
            COMMAND_ORDER.append(name)
        return sp

    sp = add("init", cmd_init, "create a task dir")
    sp.add_argument("repo")
    sp.add_argument("slug")
    sp.add_argument("--repo-path", required=True)
    sp.add_argument("--base", required=True)
    sp.add_argument("--mode", default="standard")
    sp.add_argument("--request", required=True)

    sp = add("set", cmd_set, "set status.md fixed fields")
    sp.add_argument("items", nargs="*", metavar="FIELD=VALUE")
    sp.add_argument("--add", action="store_true")
    sp.add_argument("--log", default=None)
    sp.add_argument("--as", dest="as_role", default=None)

    sp = add("role", cmd_role, "update a role's table row")
    sp.add_argument("role")
    sp.add_argument("state")
    sp.add_argument("--step", default=None)
    sp.add_argument("--note", default=None)
    sp.add_argument("--log", default=None)
    sp.add_argument("--ts", default=None, help="timestamp cell (replay and tests only)")
    sp.add_argument("--as", dest="as_role", default=None)

    sp = add("log", cmd_log, "append a log.md line")
    sp.add_argument("text")
    sp.add_argument("--as", dest="as_role", default=None)

    sp = add("journal", cmd_journal, "append a validated journal line")
    sp.add_argument("kind")
    sp.add_argument("items", nargs="*", metavar="k=v | k:=json")
    sp.add_argument("--ts", default=None, help="timestamp (replay and tests only)")
    sp.add_argument("--set", dest="set_fields", action="append", default=[], metavar="FIELD=VALUE")
    sp.add_argument("--log", default=None)
    sp.add_argument("--as", dest="as_role", default=None)

    sp = add("show", cmd_show, "one-screen task view (no -t: active tasks)")
    from . import brief, cont, dashboard, flow, merge, migrate, questions, resume, statusjson  # here, not at the top: they import this module
    flow.register(add)
    merge.register(add)
    questions.register(add)
    resume.register(add)
    brief.register(add)
    cont.register(add)
    migrate.register(add)
    dashboard.register(add)
    statusjson.register(add)
    sp = add("commands", cmd_commands, "list subcommands")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        parser = build_parser()
        ns, rest = parser.parse_known_args(argv)
        if not ns.command:
            raise OrchError("no command given\n%s" % parser.format_usage().rstrip(), 1)
        extra = []
        for r in rest:
            if r.startswith("-") and r != "-":
                raise OrchError("unrecognized argument %r" % r, 1)
            extra.append(r)
        if extra and ns.command not in ("set", "journal"):
            raise OrchError("unrecognized arguments: %s" % " ".join(extra), 1)
        return HANDLERS[ns.command](ns, extra)
    except OrchError as exc:
        sys.stderr.write("orch: %s\n" % exc)
        return exc.code
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BrokenPipeError:
        return 0
    except Exception as exc:  # internal error: nothing is written half-way (atomic writes)
        if os.environ.get("ORCH_DEBUG"):
            traceback.print_exc()
        sys.stderr.write("orch: internal error: %s: %s\n" % (type(exc).__name__, exc))
        return 3
