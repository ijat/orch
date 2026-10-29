"""orch status --json: read-only per-task progress for a pane beside the chat (the orch-pane mod).

Built on the dashboard's safe readers and `build_task` (same active-task set as `orch show`). Writes nothing:
no lock file, no index refresh. Anything missing reads as null or an empty list, never an error.
"""
import json
import os
import re
import sys

from . import dashboard as db
from . import plan as pl
from . import status as st
from .paths import OrchError, now, orch_root, tilde

SCHEMA = 1
SUMMARY_MAX = 600
ITEM_MAX = 400
WEIGHT = {"S": 1, "M": 2, "L": 4}   # bin/orch-program-check.py's weights
DEFAULT_WEIGHT = WEIGHT["M"]
COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
CTRL_KEEP_NL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f  ]+")
STEP_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(\d+[a-z]?)(?![A-Za-z0-9])", re.I)
# a review fix round in a `last completed step` cell: `fix round 3`, `round 2`, `fix-r1`, `fix-m3-r1`, `r2`
FIX_ROUND_RE = re.compile(r"(?<![A-Za-z0-9])(?:fix[\s_-]*)?(?:m\d+[\s_-]*)?(?:round|r)[\s_-]*\d+(?![A-Za-z0-9])", re.I)
FIX_WORD_RE = re.compile(r"(?<![A-Za-z0-9])fix(?![A-Za-z0-9])", re.I)
ALL_DONE_STATES = ("reviewing", "awaiting_merge")   # a review or the merge gate comes after the last step
STEP_ID_RE = re.compile(r"^(\d+)(.*)$")
MS_ROW_RE = re.compile(r"^M?(\d+)$")
ROADMAP_COLS = {"goal": "goal", "depends on": "depends", "mode": "mode", "review": "review", "merge": "merge",
                "you": "you", "size": "size", "confidence": "confidence"}


# -- text --------------------------------------------------------------------------------------------

def trim(text, limit):
    """Multi-line text: comments dropped, control characters (but newlines) become spaces, blank runs folded,
    cut at a line or word boundary at most `limit` characters long (with an ellipsis). None when empty."""
    if text is None:
        return None
    text = COMMENT_RE.sub("", str(text))
    text = CTRL_KEEP_NL_RE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = re.sub(r"\n\s*\n+", "\n\n", "\n".join(l.rstrip() for l in text.split("\n"))).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    at = max(cut.rfind("\n"), cut.rfind(" "))
    if at > limit // 2:
        cut = cut[:at]
    return cut.rstrip() + "…"


# -- journal -----------------------------------------------------------------------------------------

def journal_facts(text):
    """(task_start request, last spawn dict or None, [(milestone, steps)] of every implementer spawn) from
    journal.jsonl text; junk lines are skipped."""
    request, spawn, impl = None, None, []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("kind")
        if kind == "task_start" and request is None and isinstance(rec.get("request"), str):
            request = rec["request"]
        elif kind == "spawn":
            steps = rec.get("steps")
            if isinstance(steps, list):
                steps = [db.cell(s, 20) for s in steps if isinstance(s, (int, str)) and not isinstance(s, bool)]
                steps = [s for s in steps if s]
            else:
                steps = None
            ms = rec.get("milestone")
            spawn = {"ts": db.cell(rec.get("ts"), 32), "role": db.cell(rec.get("role"), 60),
                     "agent": db.cell(rec.get("agent"), 60), "tier": db.cell(rec.get("tier"), 40),
                     "steps": steps, "milestone": ms if db.is_int(ms) else None, "returned": False}
            if (spawn["role"] or "").lower().startswith("implementer") and steps:
                impl.append((spawn["milestone"], steps))
        elif kind == "return" and spawn is not None and spawn["role"] is not None \
                and db.cell(rec.get("role"), 60) == spawn["role"]:
            spawn["returned"] = True
    return request, spawn, impl


# -- plans -------------------------------------------------------------------------------------------

def plan_path(task_dir, program, ms_no):
    """The plan the task works from: plan.md, or in a program the unique m<NN>-*/plan.md of the milestone."""
    if not program:
        return os.path.join(task_dir, "plan.md")
    if ms_no is None:
        return None
    hits = [n for n in db.real_dirs(task_dir) if n.startswith("m%02d-" % ms_no)]
    return os.path.join(task_dir, hits[0], "plan.md") if len(hits) == 1 else None


def parse(text):
    if text is None:
        return None
    try:
        return pl.parse_plan(text)
    except Exception:
        return None


def summary_of(plan):
    if plan is None:
        return None
    sec = plan.find("summary")
    return trim(plan.body(sec), SUMMARY_MAX) if sec is not None else None


def step_order(steps):
    """Steps by (leading integer, suffix), so a revision's `6a` sits after `6` and before `7` (the plan parser
    only takes ids of digits plus an optional letter; plan order breaks ties)."""
    def key(item):
        i, s = item
        m = STEP_ID_RE.match(s.id)
        return (int(m.group(1)), m.group(2).lower(), i) if m else (float("inf"), "", i)
    return [s for _, s in sorted(enumerate(steps), key=key)]


def steps_done(rows, ids, all_done=False):
    """Count of done steps, `ids` in step order (D4, fix round 1): every step up to the furthest one an
    implementer row's `last completed step` cell names. A fix-round number is never a step id. When the
    newest implementer row (by its timestamp cell, then table order) names a fix round, or `all_done`, every
    step is done; an older fix-round row (an earlier milestone's review, beside later part rows) is not."""
    if not ids:
        return None
    pos = {sid.lower(): i for i, sid in enumerate(ids)}
    best, newest = -1, None
    for n, (role, row) in enumerate(rows):
        if not role.lower().startswith("implementer"):
            continue
        cell = row.get("step") or ""
        if newest is None or (row.get("ts") or "", n) >= newest[0]:
            newest = ((row.get("ts") or "", n), cell)
        text = FIX_ROUND_RE.sub(" ", db.DATE_RE.sub(" ", cell))
        for tok in STEP_TOKEN_RE.findall(text):
            i = pos.get(tok.lower())
            if i is not None and i > best:
                best = i
    fixing = newest is not None and bool(FIX_ROUND_RE.search(newest[1]) or FIX_WORD_RE.search(newest[1]))
    return len(ids) if all_done or fixing else best + 1


def implemented_to_end(impl, ids, program, ms_no):
    """True when an implementer spawn (of this milestone, in a program) was given the last step."""
    last = ids[-1].lower()
    for ms, steps in impl:
        if program and ms is not None and ms != ms_no:
            continue
        if last in (s.lower() for s in steps):
            return True
    return False


def steps_block(task_dir, program, ms_no, rows, spawn, state=None, impl=()):
    path = plan_path(task_dir, program, ms_no)
    out = {"plan": None, "total": None, "done": None, "percent": None, "current": None, "items": []}
    if path is None:
        return out
    plan = parse(db.read_text(path, db.MAX_PLAN))
    if plan is None:
        return out
    out["plan"] = tilde(path)
    steps = step_order(plan.steps + plan.extra_steps)
    if not steps:
        return out
    ids = [s.id for s in steps]
    all_done = state in ALL_DONE_STATES or (state == "awaiting_approval" and implemented_to_end(impl, ids, program, ms_no))
    done = steps_done(rows, ids, all_done)
    current = None
    if spawn and (spawn["role"] or "").lower().startswith("implementer") and not spawn["returned"] \
            and spawn["steps"] and (not program or spawn["milestone"] is None or spawn["milestone"] == ms_no):
        current = spawn["steps"]
    cur = set(s.lower() for s in current or [])
    out.update(total=len(steps), done=done, percent=int(100.0 * done / len(steps) + 0.5), current=current)
    out["items"] = [{"id": s.id, "title": db.cell(s.title, 200), "done": i < done, "current": s.id.lower() in cur,
                     "summary": trim(s.field_text("do"), ITEM_MAX)} for i, s in enumerate(steps)]
    return out


# -- roadmap -----------------------------------------------------------------------------------------

def roadmap_table(text):
    """Rows of the first table whose header's first cell is `M`: [{n, goal, …}] (missing columns None)."""
    if text is None:
        return []
    text = COMMENT_RE.sub("", text)
    cols, rows = None, []
    for line in text.split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            if cols is not None:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cols is None:
            if cells and cells[0] == "M":
                cols = [ROADMAP_COLS.get(c.lower()) for c in cells]
            continue
        m = MS_ROW_RE.match(cells[0]) if cells else None
        if not m:
            continue
        row = {"n": int(m.group(1))}
        for key in ROADMAP_COLS.values():
            row[key] = None
        for i, key in enumerate(cols):
            if key and i < len(cells):
                row[key] = db.cell(cells[i], 2000)
        rows.append(row)
    return rows


def milestones_block(task_dir, ms_no, journal):
    rows = roadmap_table(db.read_text(os.path.join(task_dir, "roadmap.md"), db.MAX_PLAN))
    done_set = set(str(m) for m in journal.milestones_done)
    items = []
    for r in rows:
        state = "done" if str(r["n"]) in done_set else ("current" if r["n"] == ms_no else "pending")
        gates = " · ".join("%s: %s" % (k, r[k]) for k in ("review", "merge", "you", "size") if r[k] and r[k] != "-")
        goal = trim(r["goal"], ITEM_MAX)
        summary = "\n".join(x for x in (goal, gates) if x) or None
        items.append(dict(r, goal=db.cell(r["goal"], ITEM_MAX), state=state, summary=summary))
    return {"done": sum(1 for i in items if i["state"] == "done"), "total": len(items) or None, "items": items}


def overall(program, steps, milestones):
    if not program:
        p = steps["percent"]
        return {"percent": p, "estimate": False, "basis": "steps"}
    out = {"percent": None, "estimate": True, "basis": "milestone size (S=1 M=2 L=4)"}
    items = (milestones or {}).get("items") or []
    if not items:
        return out
    total = done = 0.0
    for it in items:
        w = WEIGHT.get((it["size"] or "").strip().upper(), DEFAULT_WEIGHT)
        total += w
        if it["state"] == "done":
            done += w
        elif it["state"] == "current" and steps["total"] and steps["done"] is not None:
            done += w * steps["done"] / steps["total"]
    out["percent"] = max(0, min(100, int(100.0 * done / total + 0.5)))
    return out


# -- scope -------------------------------------------------------------------------------------------

def inside(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def in_cwd(cwd, status):
    if cwd is None:
        return None
    for name in ("repo", "worktree"):
        c = st.clean_value(name, status.get(name))
        if c.value in ("unknown",) + st.WORKTREE_LITERALS or c.missing or not os.path.isabs(c.value):
            continue
        if inside(cwd, os.path.realpath(c.value)):
            return True
    return False


# -- the data ----------------------------------------------------------------------------------------

KEYS = ("id", "repo", "slug", "task_dir", "repo_path", "worktree", "in_cwd", "state", "state_words", "mode",
        "shape", "program", "milestone", "milestone_no", "summary", "updated", "last_log", "questions_open",
        "running", "steps", "milestones", "overall", "error")


def build(repo, slug, d, text, state, cwd):
    jtext = db.read_text(os.path.join(d, "journal.jsonl"))
    journal = db.Journal(jtext, "%s/%s" % (repo, slug))
    base, _ = db.build_task(repo, slug, d, text, state, journal)
    status = st.Status(text.encode("utf-8", "surrogateescape"))
    repo_c = st.clean_value("repo", status.get("repo"))
    t = dict.fromkeys(KEYS)
    t.update({k: base[k] for k in ("id", "repo", "slug", "task_dir", "worktree", "state", "state_words", "mode",
                                   "shape", "milestone", "updated", "last_log", "questions_open")})
    t["repo_path"] = None if repo_c.value == "unknown" else repo_c.value
    t["running"] = {"roles": [], "last_spawn": None}
    t["steps"] = {"plan": None, "total": None, "done": None, "percent": None, "current": None, "items": []}
    t["overall"] = {"percent": None, "estimate": False, "basis": "steps"}
    try:
        t["in_cwd"] = in_cwd(cwd, status)
        try:
            rows = status.roles()
        except OrchError:
            rows = []
        program = base["shape"] == "roadmap" or db.stat_regular(os.path.join(d, "roadmap.md")) is not None
        m = re.match(r"^M?(\d+)\b", status.get("milestone") or "")
        ms_no = int(m.group(1)) if m else None
        t["program"], t["milestone_no"] = program, ms_no
        request, spawn, impl = journal_facts(jtext)
        roadmap = parse(db.read_text(os.path.join(d, "roadmap.md"), db.MAX_PLAN)) if program else None
        plan = None if program else parse(db.read_text(os.path.join(d, "plan.md"), db.MAX_PLAN))
        t["summary"] = summary_of(roadmap if program else plan) or trim(request, SUMMARY_MAX)
        t["running"] = {"roles": [r for r in base["roles"] if r["state"] in ("in_progress", "waiting")
                                  and not (r["role"] or "").startswith("orchestrator")],
                        "last_spawn": spawn}
        t["steps"] = steps_block(d, program, ms_no, rows, spawn, state, impl)
        t["milestones"] = milestones_block(d, ms_no, journal) if program else None
        t["overall"] = overall(program, t["steps"], t["milestones"])
    except Exception as exc:   # one odd task never takes the list down
        t["error"] = db.cell("%s: %s" % (type(exc).__name__, exc), 200)
    return t


def compute(root, cwd=None):
    cwd = os.path.realpath(cwd) if cwd else None
    found = []
    for repo, slug, d in db.task_dirs(root):
        text = db.read_text(os.path.join(d, "status.md"))
        if text is None:
            continue
        state = st.classify(text.replace("\r\n", "\n").replace("\r", "\n"), d)
        if state in db.FINISHED:
            continue
        mtimes = [i.st_mtime for i in (db.stat_regular(os.path.join(d, n)) for n in ("status.md", "log.md")) if i]
        found.append((-(max(mtimes) if mtimes else 0), "%s/%s" % (repo, slug), build(repo, slug, d, text, state, cwd)))
    found.sort(key=lambda f: (f[0], f[1]))
    return {"schema": SCHEMA, "generated": now(), "cwd": cwd, "tasks": [f[2] for f in found]}


def cmd_status(ns, extra):
    if getattr(ns, "task_sub", None) or ns.task:
        raise OrchError("status lists every active task; -t is not supported", 1)
    if not ns.json_out:
        raise OrchError("status needs --json", 1)
    sys.stdout.write(json.dumps(compute(orch_root(), ns.cwd), indent=1) + "\n")
    return 0


def register(add):
    sp = add("status", cmd_status, "read-only JSON of each active task's progress (for the orch-pane mod)")
    sp.add_argument("--json", dest="json_out", action="store_true")
    sp.add_argument("--cwd", default=None, help="mark tasks whose repo or worktree holds this directory (in_cwd)")
