"""migrate: bring old status.md files up to the current field set. status.md only; nothing is guessed.

What it does (D12): normalises an existing `task_state` that is an enum value under case, markup and trailing
punctuation; adds fixed fields that are missing, with a derived value or `unknown`. It never adds `task_state`
or `worktree` (orch-index.sh derives a state from their absence), never rebuilds a role table, and never writes
any file but status.md (plans, roadmaps, journals and logs are only read for the report). A backup of the
original comes first, the original mtime is restored (orch-index uses it as the last-activity time), and a
second run finds nothing to do.
"""
import glob
import json
import os
import re
import shutil

from . import plan as pl
from . import status as st
from .paths import (OrchError, TEMPLATES_DIR, TaskLock, now, orch_root, refresh_index, require_write_allowed,
                    task_names, tilde, warn_stderr, write_atomic)

# Never added when absent: orch-index.sh reads their absence (retro.md, the orchestrator row, `removed`).
NEVER_ADD = ("task_state", "worktree")
# Fallback when the template cannot be read (the template's own order wins).
DEFAULT_ORDER = ("task_state", "mode", "shape", "milestone", "reviewed_through", "merged_through", "task_dir", "repo",
                 "base_branch", "task_branch", "worktree", "planner_tier", "implementer_tier", "review_round",
                 "usage_override")
STRIP_CHARS = "*`_~\"' \t.,;:!()[]<>"
MODES = ("standard", "lite")


def template_fields():
    path = os.path.join(TEMPLATES_DIR, "status.md")
    names = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^- ([a-z][a-z0-9_]*):", line)
                if m and m.group(1) not in names:
                    names.append(m.group(1))
    except OSError:
        pass
    return tuple(names) or DEFAULT_ORDER


def normalise_state(raw, enum):
    """The enum value `raw` stands for under case/markup/punctuation, or None."""
    v = raw.strip().strip(STRIP_CHARS).lower()
    return v if v in enum else None


# -- reading what the report needs -----------------------------------------------------------------

def journal_info(task_dir):
    """(lines, invalid, event_keyed, start_mode) from journal.jsonl; read-only, damaged lines are counted."""
    path = os.path.join(task_dir, "journal.jsonl")
    lines = invalid = event_keyed = 0
    mode = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                lines += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    rec = None
                if not isinstance(rec, dict):
                    invalid += 1
                    continue
                if "kind" not in rec and "event" in rec:
                    event_keyed += 1
                if mode is None and (rec.get("kind") or rec.get("event")) == "task_start":
                    m = rec.get("mode")
                    if isinstance(m, str) and m in MODES:
                        mode = m
    except OSError:
        pass
    return lines, invalid, event_keyed, mode


def plan_info(path):
    """'N steps, M without files:, K without depends:' or 'legacy (no step blocks)'; never raises."""
    try:
        plan = pl.parse_file(path)
    except OrchError as exc:
        return "unreadable (%s)" % exc
    if plan.legacy:
        return "legacy (no step blocks)"
    steps = plan.steps + plan.extra_steps
    no_files = sum(1 for s in steps if "files" not in s.fields)
    no_deps = sum(1 for s in steps if "depends" not in s.fields)
    return "%d steps (files: unknown for %d, depends: unknown for %d)" % (plan.step_count(), no_files, no_deps)


def plan_lines(task_dir):
    out = []
    plan = os.path.join(task_dir, "plan.md")
    if os.path.exists(plan):
        out.append("plan.md: %s" % plan_info(plan))
    if os.path.exists(os.path.join(task_dir, "roadmap.md")):
        out.append("roadmap.md present")
    for mp in sorted(glob.glob(os.path.join(glob.escape(task_dir), "m[0-9]*", "plan.md"))):
        out.append("%s/plan.md: %s" % (os.path.basename(os.path.dirname(mp)), plan_info(mp)))
    return out


# -- the analysis of one status.md ------------------------------------------------------------------

class Result(object):
    def __init__(self, task_dir):
        self.dir = task_dir
        self.repo, self.slug = task_names(task_dir)
        self.name = "%s/%s" % (self.repo, self.slug)
        self.state = "?"
        self.finished = False
        self.changes = []      # (field, old or None, new)
        self.unknowns = []     # fields written as `unknown` or values that could not be normalised
        self.notes = []        # absent task_state / worktree, unreadable role layout
        self.info = []         # plan and journal lines
        self.new_bytes = None  # rendered status.md when something changes
        self.error = None
        self.backup = None

    @property
    def changed(self):
        return bool(self.changes)


def first_task_state(status):
    for _, name, raw in status.fields():
        if name == "task_state":
            return raw
    return None


def present_anywhere(status):
    names = set()
    for line in status.lines:
        m = st.FIELD_RE.match(st.split_ending(line)[0])
        if m:
            names.add(m.group(1))
    return names


def block_end(status):
    """Index where the fixed-field block ends: the role table, or the first table row of any kind."""
    end = status._head_end()
    for i in range(min(end, len(status.lines))):
        if status.lines[i].lstrip().startswith("|"):
            return i
    return end


def add_field(status, name, value):
    be = block_end(status)
    fields = [f for f in status.fields() if f[0] < be]
    if fields:
        at = fields[-1][0] + 1
    else:
        at = 1
        while at < len(status.lines) and at < be and not status.lines[at].strip():
            at += 1
        at = min(at, be)
    prev = status.lines[at - 1] if at > 0 else ""
    ending = st.split_ending(prev)[1] or "\n"
    if at > 0 and not prev.endswith("\n"):
        status.lines[at - 1] = prev + ending
    status.lines.insert(at, "- %s: %s%s" % (name, value, ending))


def analyse(task_dir, status, order, enum):
    """Pure: the changes migrate would make to `status` (applied to it in memory), plus the report facts."""
    res = Result(task_dir)
    text = status.render().decode("utf-8", "replace")
    res.state = st.classify(text, task_dir)
    res.finished = res.state in st.INDEX_TERMINAL
    j_lines, j_invalid, j_event, j_mode = journal_info(task_dir)
    present = present_anywhere(status)

    # task_state: normalise an existing enum-ish value, never add one
    raw = first_task_state(status)
    if raw is not None:
        value = raw.strip()
        norm = normalise_state(value, enum)
        if norm is not None and norm != value:
            status.set_field("task_state", norm)
            res.changes.append(("task_state", value, norm))
        elif norm is None:
            res.unknowns.append("task_state %r is not a single enum value (index derives %s)"
                                % (value if len(value) <= 60 else value[:57] + "...", res.state))
    elif "task_state" not in present:
        res.notes.append("task_state: absent (index derives %s)" % res.state)
    if "worktree" not in present:
        res.notes.append("worktree: absent")

    # shape decides the roadmap-only fields; an existing value that is not plainly `single` is not guessed at
    shape_raw = status.get("shape") if "shape" in present else None
    if shape_raw is not None:
        words = shape_raw.split()
        single = bool(words) and words[0].strip("`*").lower() == "single"
    else:
        single = not os.path.exists(os.path.join(task_dir, "roadmap.md"))
    derived = {
        "mode": j_mode or "unknown",
        "shape": "single" if single else "roadmap",
        "milestone": "n/a" if single else "unknown",
        "reviewed_through": "none" if single else "unknown",
        "merged_through": "none" if single else "unknown",
        "task_dir": tilde(task_dir),
        "usage_override": "none",
    }
    for name in order:
        if name in NEVER_ADD or name in present:
            continue
        value = derived.get(name, "unknown")
        add_field(status, name, value)
        res.changes.append((name, None, value))
        if value == "unknown":
            res.unknowns.append(name)

    try:
        lay = status.layout()
        bad = 0
        for i in range(lay.sep + 1, lay.last + 1):
            c = status._cells(status.lines[i])
            if not c or len(c[1]) != lay.ncols:
                bad += 1
        if bad:
            res.notes.append("roles: %d row(s) with a cell count different from the header (read as far as possible)" % bad)
    except OrchError as exc:
        res.notes.append("roles: unreadable layout (%s)" % str(exc).split(";")[0])

    res.info = ["plans: " + ("; ".join(plan_lines(task_dir)) or "none"),
                "journal: %d lines, %d invalid, %d event-keyed%s"
                % (j_lines, j_invalid, j_event, "" if j_mode else ", task_start mode unknown")]
    if res.changes:
        res.new_bytes = status.render()
    return res


def analyse_file(task_dir, order, enum):
    status = st.Status.load(os.path.join(task_dir, "status.md"))
    return analyse(task_dir, status, order, enum), status


# -- running -----------------------------------------------------------------------------------------

def all_task_dirs(root):
    return [os.path.dirname(p) for p in sorted(glob.glob(os.path.join(glob.escape(root), "*", "*", "status.md")))]


def backup_stamp():
    return now().replace(":", "")


def write_one(res, backup_root, order, enum):
    """Under the task lock: re-read, recompute, back up the bytes about to be replaced, write, restore the mtime."""
    path = os.path.join(res.dir, "status.md")
    with TaskLock(res.dir):
        fresh, _ = analyse_file(res.dir, order, enum)
        if not fresh.changed:
            res.changes, res.unknowns, res.new_bytes = [], fresh.unknowns, None
            return False
        res.changes, res.unknowns, res.new_bytes = fresh.changes, fresh.unknowns, fresh.new_bytes
        dest = os.path.join(backup_root, res.repo, res.slug, "status.md")
        if os.path.exists(dest):
            raise OrchError("backup %s already exists (not overwritten); give --backup-dir a new directory" % dest, 2)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        res.backup = dest
        before = os.stat(path)
        write_atomic(path, res.new_bytes)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    return True


def render(res):
    head = "%s  [%s%s]" % (res.name, res.state, ", finished" if res.finished else "")
    if res.error:
        return ["%s  ERROR: %s" % (head, res.error)]
    out = ["%s  %s" % (head, "%d change(s)" % len(res.changes) if res.changes else "no changes")]
    added = ["%s=%s" % (f, new) for f, old, new in res.changes if old is None]
    fixed = ["%s: %s -> %s" % (f, old, new) for f, old, new in res.changes if old is not None]
    if added:
        out.append("  add: " + ", ".join(added))
    if fixed:
        out.append("  fix: " + "; ".join(fixed))
    if res.unknowns:
        out.append("  unknown: " + "; ".join(res.unknowns))
    if res.notes:
        out.append("  note: " + "; ".join(res.notes))
    out.append("  " + " | ".join(res.info))
    return out


def cmd_migrate(ns, extra):
    from . import cli
    dry = ns.dry_run
    if not dry:
        require_write_allowed()
    enum = st.task_state_enum()
    order = template_fields()
    root = orch_root()
    arg = getattr(ns, "task_sub", None) or ns.task
    scanned = skipped_finished = 0
    results = []
    if arg:
        d = cli.get_task(ns)
        if not os.path.exists(os.path.join(d, "status.md")):
            raise OrchError("no status.md in %s" % d, 1)
        dirs = [d]
    else:
        dirs = all_task_dirs(root)
    for d in dirs:
        scanned += 1
        try:
            res, _ = analyse_file(d, order, enum)
        except OrchError as exc:
            res = Result(d)
            res.error = str(exc)
        if not arg and not ns.all and res.finished and not res.error:
            skipped_finished += 1
            continue
        results.append(res)

    backup_root = None
    if not dry and any(r.changed for r in results):
        backup_root = os.path.abspath(ns.backup_dir) if ns.backup_dir else os.path.join(root, ".migrate-backup-" + backup_stamp())
    failed = 0
    wrote = 0
    for res in results:
        if res.changed and not dry:
            try:
                if write_one(res, backup_root, order, enum):
                    wrote += 1
            except (OrchError, OSError) as exc:
                res.error = str(exc)
                res.changes, res.new_bytes, res.backup = [], None, None
                failed += 1

    print("orch migrate%s: %s" % (" (dry run, nothing written)" if dry else "", root if not arg else results[0].dir))
    for res in results:
        for line in render(res):
            print(line)
    changed = [r for r in results if r.changed]
    added = {}
    for r in changed:
        for f, old, _ in r.changes:
            key = ("fixed " if old is not None else "") + f
            added[key] = added.get(key, 0) + 1
    n_unknown = sum(len([u for u in r.unknowns if not u.startswith("task_state ")]) for r in results)
    n_state = sum(len([u for u in r.unknowns if u.startswith("task_state ")]) for r in results)
    print("total: %d task(s) scanned, %d reported%s; %d %s change; unknown values %s: %d; task_state not a single enum value: %d"
          % (scanned, len(results), "" if arg or ns.all else ", %d finished skipped (use --all)" % skipped_finished,
             len(changed), "would" if dry else "did", "that would be written" if dry else "written", n_unknown, n_state))
    if added:
        print("fields: " + ", ".join("%s %d" % (k, added[k]) for k in sorted(added)))
    if wrote:
        print("backup: %s (%d status.md); restore by copying the files back" % (backup_root, wrote))
        refresh_index(warn_stderr)
    elif not changed:
        print("no changes")
    if failed:
        print("%d task(s) not migrated (see ERROR above)" % failed)
        return 2
    return 0


def register(add):
    sp = add("migrate", cmd_migrate, "bring old status.md files up to the current field set (report, backup, idempotent)")
    sp.add_argument("--dry-run", action="store_true", help="the same report, nothing written")
    sp.add_argument("--all", action="store_true", help="include finished tasks (default: active ones)")
    sp.add_argument("--backup-dir", default=None, metavar="D", help="default: $ORCH_DIR/.migrate-backup-<UTC>")
