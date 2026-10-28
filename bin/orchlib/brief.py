"""orch brief: one file per role holding exactly the context that role needs (implement, director, review,
security, fix). Deterministic: the same inputs give a byte-identical file. Nothing here writes outside
TASK/briefs/. Python 3.9 compatible, standard library only."""
import glob
import os
import re

from . import cli
from . import flow
from . import lessons as ls
from . import merge
from . import plan as pl
from . import questions as qs
from . import status as st
from .paths import OrchError, TaskLock, orch_root, require_write_allowed, task_names, write_atomic

SUPERSEDE_RE = re.compile(r"restat|replaces|replaced|supersed|\bnew block\b", re.I)
NO_WORKTREE = ("not yet created", "unknown", "removed after merge")
EXTRA_LIMIT = 1024 * 1024
PRIOR_LIMIT = 20
STAT_LIMIT = 200
GUARD_RE = re.compile(r"^(- (?:reviewed sha|round|milestone):)", re.M)
FIX_ID_RE = re.compile(r"^[FS]\d+$")
FALLBACK = ("Grep plan.md for the section you need (`grep -n '^#' <plan>`), read only that section, and write "
            "`brief-gap: <what was missing>` in notes.md so the next brief can include it.")
SIM_FILES = ("size", "files", "do", "contract", "done when")
REVIEW_FIELDS = ("files", "do", "contract", "done when")


# -- context -------------------------------------------------------------------------------------

class Ctx(object):
    pass


def milestone_dir(task_dir, ms):
    hits = sorted(glob.glob(os.path.join(glob.escape(task_dir), "m%02d-*" % ms)))
    hits = [h for h in hits if os.path.isdir(h)]
    if not hits:
        raise OrchError("program task: no milestone dir m%02d-* in %s" % (ms, task_dir), 1)
    if len(hits) > 1:
        raise OrchError("program task: more than one milestone dir for M%d: %s" % (ms, ", ".join(os.path.basename(h) for h in hits)), 1)
    return hits[0]


def load_ctx(ns):
    c = Ctx()
    c.task_dir = cli.get_task(ns)
    c.repo_name, c.slug = task_names(c.task_dir)
    c.status = cli.load_status(c.task_dir)
    c.program, c.ms = cli.program_info(c.status)
    if c.program and c.ms is None:
        raise OrchError("program task with no resolvable milestone in status.md (milestone: %s)" % c.status.get("milestone"), 1)
    c.review_dir = c.task_dir
    if ns.plan:
        c.plan_path = os.path.abspath(os.path.expanduser(ns.plan))
        if c.program:
            c.review_dir = os.path.dirname(c.plan_path)
    elif c.program:
        c.review_dir = milestone_dir(c.task_dir, c.ms)
        c.plan_path = os.path.join(c.review_dir, "plan.md")
    else:
        c.plan_path = os.path.join(c.task_dir, "plan.md")
    if not os.path.isfile(c.plan_path):
        raise OrchError("no plan at %s" % c.plan_path, 1)
    c.plan = pl.parse_file(c.plan_path)
    c.plan_size = os.path.getsize(c.plan_path)
    cv = st.clean_value
    c.repo_path = cv("repo", c.status.get("repo")).value
    if c.repo_path == "unknown" and c.plan.header.get("repo"):
        c.repo_path = cv("repo", c.plan.header.get("repo")).value
    c.branch = cv("task_branch", c.status.get("task_branch")).value
    c.base = cv("base_branch", c.status.get("base_branch")).value
    wt = cv("worktree", c.status.get("worktree")).value
    c.wt_value = wt
    c.wt = wt if wt not in NO_WORKTREE and wt != "none" and os.path.isdir(wt) else None
    c.no_wt_mode = wt == "none"
    c.reviewed_through = cv("reviewed_through", c.status.get("reviewed_through")).value
    c.roots = tuple(r for r in (c.repo_path if c.repo_path != "unknown" else None, c.wt) if r)
    return c


def work_dir(c):
    """Directory git commands run in: the worktree, else the repo."""
    if c.wt:
        return c.wt
    if c.repo_path != "unknown" and os.path.isdir(c.repo_path):
        return c.repo_path
    return None


def to_worktree(c, path):
    rel = pl.relative_to(path, c.repo_path) if c.repo_path != "unknown" else None
    return os.path.join(c.wt, rel) if (rel and c.wt) else path


def sq(text):
    """Always single-quoted, so the line reads `orch -t '<task>' role '<role>' …` and survives spaces in paths."""
    return "'" + text.replace("'", "'\\''") + "'"


# -- shared pieces -------------------------------------------------------------------------------

def read_extra(path):
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read(EXTRA_LIMIT + 1)
    except OSError as exc:
        raise OrchError("cannot read --extra %s: %s" % (path, exc), 1)
    if len(data) > EXTRA_LIMIT:
        raise OrchError("--extra %s is larger than %d bytes" % (path, EXTRA_LIMIT), 1)
    return data.decode("utf-8", "surrogateescape").rstrip("\n")


def section(title, body):
    body = (body or "").strip("\n")
    return "## %s\n\n%s" % (title, body if body.strip() else "None.")


def omitted_or(plan, sec, text_fn):
    if sec is None:
        return None
    if plan.is_omitted(sec):
        return "(omitted in this lite plan)"
    return text_fn(sec)


def summary_text(plan):
    sec = plan.find("summary")
    if sec is None:
        return "(this plan has no Summary section)"
    return omitted_or(plan, sec, plan.body)


def decisions_text(plan):
    secs = plan.decision_sections()
    if not secs:
        return "(this plan has no Decisions section)"
    if len(secs) == 1:
        return omitted_or(plan, secs[0], plan.body)
    parts = []
    for s in secs:
        body = "(omitted in this lite plan)" if plan.is_omitted(s) else plan.body(s)
        parts.append("### %s\n\n%s" % (re.sub(r"^#+\s*", "", plan.lines[s.start]), body))
    return "\n\n".join(parts)


def assumed_text(c):
    lines = qs.read_lines(c.task_dir)
    out = []
    for q in qs.parse(lines):
        if q.status == "assumed":
            out.append("".join(lines[q.start:q.end]).rstrip("\n"))
    return "\n\n".join(out)


def checkpoint_text(c, role, ids_text, last_id):
    t, r = sq(c.task_dir), sq(role)
    orch = "~/.claude/bin/orch"
    head = "%s -t %s role %s" % (orch, t, r)
    return "\n".join([
        "Run these as written (replace only the quoted placeholders):",
        "",
        "```",
        "%s in_progress --note %s --log %s" % (head, sq("working on step(s) %s" % ids_text), sq("started step(s) %s" % ids_text)),
        "# after each step is committed:",
        "%s in_progress --step %s --log %s" % (head, sq("<step id just finished>"), sq("step <id> done: <what> (<commit sha>)")),
        "# when everything assigned to you is done:",
        "%s done --step %s --note %s --log %s" % (head, sq(last_id), sq("<one line>"), sq("done: <one line>")),
        "# when you must stop:",
        "%s blocked --note %s --log %s" % (head, sq("need: <what>"), sq("blocked: <what>")),
        "```",
    ])


def review_checkpoint_text(c, role):
    """The reviewer's checkpoint commands. `-t` names the task dir, the one holding status.md: in a program
    that is the program dir, not the milestone dir the plan and review file sit in."""
    head = "~/.claude/bin/orch -t %s role %s" % (sq(c.task_dir), sq(role))
    return "\n".join([
        "Run these as written (replace only the quoted placeholders):",
        "",
        "```",
        "%s in_progress --note %s --log %s" % (head, sq("reviewing"), sq("started review")),
        "# after each file group you finish:",
        "%s in_progress --log %s" % (head, sq("reviewed <file group>")),
        "# when you finish:",
        "%s done --note %s --log %s" % (head, sq("<verdict>, <N> findings"), sq("done: <verdict>")),
        "# when you must stop:",
        "%s blocked --note %s --log %s" % (head, sq("need: <what>"), sq("blocked: <what>")),
        "```",
    ])


def where_text(c, role, tier, escalation, steps, extra_lines=()):
    out = []
    if c.wt:
        out.append("- worktree: %s  <- this is the worktree, not the main repo. Edit and commit here." % c.wt)
        if c.repo_path != "unknown":
            out.append("- main repo (do not edit): %s" % c.repo_path)
    elif c.no_wt_mode:
        out.append("- no worktree (the user chose to work in the repo itself): %s" % c.repo_path)
    else:
        out.append("- worktree: none exists yet (status.md says %r). Do not edit anything; set `blocked` with `need: worktree`." % c.wt_value)
    out.append("- branch: %s" % c.branch)
    out.append("- base: %s" % c.base)
    out.append("- plan: %s" % c.plan_path)
    out.append("- notes: %s (contract-check entries and brief-gap notes go here)" % os.path.join(c.task_dir, "notes.md"))
    for s in steps:
        sk = s.field_text("sketch")
        if sk and not re.match(r"^none\b", sk, re.I):
            path = sk.split()[0].strip("`")
            if not os.path.isabs(path):
                path = os.path.join(c.task_dir, path)
            out.append("- sketch for step %s (non-binding): %s%s" % (s.id, path, "" if os.path.exists(path) else " (missing)"))
        gd = s.field_text("guide")
        if gd and not re.match(r"^none\b", gd, re.I):
            path = gd.split()[0].strip("`")
            if not os.path.isabs(path):
                path = os.path.join(os.path.dirname(c.plan_path), path)
            out.append("- guide for step %s (the planner's map of the pattern to copy; read it first, work its Checklist): %s%s"
                       % (s.id, path, "" if os.path.exists(path) else " (missing)"))
    out.append("- role row: %s" % role)
    out.append("- tier: %s" % (tier or "not stated here (the spawn message names it)"))
    out.append("- escalation: %s" % (escalation or "none"))
    if c.wt and c.repo_path != "unknown":
        out.append("- plan paths under %s are main-repo paths; the same file in your worktree is under %s" % (c.repo_path, c.wt))
    out.extend(extra_lines)
    return "\n".join(out)


def step_keys(c, steps, ids):
    paths = []
    for s in steps:
        for p in s.files(c.repo_path if c.repo_path != "unknown" else None):
            if p not in paths:
                paths.append(p)
    return pl.Keys(ids, paths, c.roots)


def expand_steps(plan, spec):
    """'3', '3,4', '3-5', '3a' -> step ids in the order given (ranges follow the plan's order)."""
    ids = []
    for tok in re.split(r"[,\s]+", spec.strip()):
        if not tok:
            continue
        m = re.match(r"^(\d+[a-z]?)\s*[-–—]\s*(\d+[a-z]?)$", tok, re.I)
        if m:
            order = plan.ids()
            a, b = m.group(1).lower(), m.group(2).lower()
            if a not in order or b not in order or order.index(a) > order.index(b):
                raise OrchError("bad step range %r (plan steps: %s)" % (tok, ", ".join(order)), 1)
            new = order[order.index(a):order.index(b) + 1]
        elif re.match(r"^\d+[a-z]?$", tok, re.I):
            new = [tok.lower()]
        else:
            raise OrchError("bad step id %r (use 3, 3,4, 3-5 or 3a)" % tok, 1)
        for i in new:
            if i not in ids:
                ids.append(i)
    if not ids:
        raise OrchError("no steps named", 1)
    if not plan.legacy:
        unknown = [i for i in ids if plan.step(i) is None]
        if unknown:
            raise OrchError("unknown step(s) %s (plan steps: %s)" % (", ".join(unknown), ", ".join(plan.ids())), 1)
    return ids


def revision_parts(plan, keys, ids):
    """(superseding, other): texts of the revisions that touch these steps. `superseding` goes first in
    the step section; later revisions win over earlier ones and over the plan."""
    sup, other = [], []
    for rev in plan.revisions():
        head = re.sub(r"^#+\s*", "", plan.lines[rev.start])
        parts = []
        pre = plan.block(rev.start + 1, rev.pre_end).strip("\n") if rev.subs else ""
        if not rev.subs:
            for u in pl.filter_region(plan, rev.start + 1, rev.end, keys):
                if SUPERSEDE_RE.search(u) and pl.refs_hit(u, set(ids)):
                    sup.append("From %s: this revision wins.\n\n%s" % (head, u))
                else:
                    parts.append(u)
        for sub in rev.subs:
            m = pl.STEP_HEAD_RE.match(sub.title)
            block = plan.block(sub.start, sub.end)
            if m and m.group(1).lower() in set(ids):
                own = plan.step(m.group(1).lower())
                if own is not None and own.start == sub.start:
                    continue      # a step the revision introduced: it is already printed as the step
                if SUPERSEDE_RE.search(sub.title):
                    sup.append("%s supersedes this step or part of it: this revision wins.\n\n%s" % (head, block))
                else:
                    parts.append("#" + block)
            elif re.search(r"decision", sub.title, re.I):
                parts.append("#" + block)
            else:
                kept = pl.filter_region(plan, sub.start + 1, sub.end, keys)
                if kept:
                    parts.append("#" + plan.lines[sub.start] + "\n\n" + "\n\n".join(kept))
        if parts and pre.strip():
            parts.insert(0, pre)
        if parts:
            other.append("### %s\n\n%s" % (head, "\n\n".join(parts)))
    return sup, other


def depends_text(plan, steps, ids):
    order = plan.ids()
    want = []
    for s in steps:
        singles, ranges = pl.parse_depends(s.field_text("depends"))
        names = list(singles)
        for lo, hi in ranges:
            names.extend(i for i in order if pl._num(i) is not None and lo <= pl._num(i) <= hi)
        for d in names:
            if d not in ids and d not in want and plan.step(d) is not None:
                want.append(d)
    out = []
    for d in sorted(want, key=order.index):
        s = plan.step(d)
        out.append("### Step %s — %s\n\n%s" % (s.id, s.title, "\n".join(
            "\n".join(s.field_lines(f)) for f in ("contract", "done when") if s.field_lines(f))))
    return "\n\n".join(out)


def verification_text(plan, keys, ids):
    sec = plan.find("verification")
    if sec is None:
        return ""
    if plan.is_omitted(sec):
        return "(omitted in this lite plan)"
    last = plan.last_step()
    parts = []
    pre = pl.filter_region(plan, sec.start + 1, sec.pre_end, keys, keep_paras=True, split_labels=True)
    if pre:
        parts.append("\n\n".join(pre))
    for sub in sec.subs:
        if last and last in ids and re.match(r"final gate", sub.title, re.I):
            parts.append(plan.block(sub.start, sub.end))
            continue
        kept = pl.filter_region(plan, sub.start + 1, sub.end, keys, keep_paras=True, split_labels=True)
        if re.match(r"final gate", sub.title, re.I):
            kept = []
        if kept:
            parts.append(plan.lines[sub.start] + "\n\n" + "\n\n".join(kept))
    return "\n\n".join(parts)


CONTEXT_SEP_RE = re.compile(r"^\s*[-—–:]+\s*")


def context_entries(c, steps):
    out, seen = [], set()
    repo = c.repo_path if c.repo_path != "unknown" else None
    for s in steps:
        text = s.field_text("context") or ""
        for line in text.splitlines():
            line = re.sub(r"^\s*[-*+]\s+", "", line).strip()
            toks = pl.path_tokens(line, repo)
            if not toks:
                continue
            path = toks[0]
            if path in seen:
                continue
            seen.add(path)
            idx = line.find(os.path.basename(path.rstrip("/")))
            rest = line[idx + len(os.path.basename(path.rstrip("/"))):] if idx != -1 else ""
            out.append((path, CONTEXT_SEP_RE.sub("", rest.strip().strip("`")).strip()))
    return out


def freshness(c, path):
    pa = c.plan.planned_against
    if not pa or not merge.ABBREV_RE.match(pa) or not c.wt:
        return "freshness unknown"
    r = merge.git(c.wt, "diff", "--quiet", pa, "HEAD", "--", to_worktree(c, path))
    if r.rc == 0:
        return "unchanged since planning"
    if r.rc == 1:
        return "changed since planning — re-read"
    return "freshness unknown"


def context_text(c, steps):
    lines = []
    for path, desc in context_entries(c, steps):
        lines.append("- %s%s [%s]" % (to_worktree(c, path), " — " + desc if desc else "", freshness(c, path)))
    return "\n".join(lines)


def prior_text(c):
    d = work_dir(c)
    if d is None or merge.bad_ref(c.base):
        return "(no checkout to read)"
    tip = "HEAD" if c.wt else c.branch
    if not c.wt and merge.bad_ref(c.branch):
        return "(no task branch to read)"
    r = merge.git(d, "log", "--oneline", "--no-decorate", "-n", str(PRIOR_LIMIT + 1), "%s..%s" % (c.base, tip))
    if not r.ok:
        return "(git log failed: %s)" % merge.first_lines(r, 1)
    rows = r.out.splitlines()
    if not rows:
        return "No commits on the task branch yet."
    more = "\n... (more commits on the branch)" if len(rows) > PRIOR_LIMIT else ""
    return "\n".join(rows[:PRIOR_LIMIT]) + more


def lessons_text(c, role, keys, repo_full=True):
    return ls.lessons_section(c.task_dir, orch_root(), c.repo_name, role, keys, repo_full)


def legacy_note(c):
    return ("WARNING: %s has no `### Step <id> — <title>` blocks (legacy format). This brief carries the whole "
            "Steps section instead of per-step blocks; nothing below is filtered by step." % c.plan_path)


def whole_steps_text(c):
    plan = c.plan
    if plan.step_sections:
        return "\n\n".join("### %s\n\n%s" % (re.sub(r"^#+\s*", "", plan.lines[s.start]), plan.body(s)) for s in plan.step_sections)
    return "(no Steps section found; the whole plan follows)\n\n" + plan.text.strip("\n")


def unique_path(directory, stem):
    base = os.path.join(directory, stem + ".md")
    if not os.path.exists(base):
        return base
    n = 2
    while True:
        cand = os.path.join(directory, "%s-%d.md" % (stem, n))
        if not os.path.exists(cand):
            return cand
        n += 1


def emit(path, text, plan_size):
    data = (text.rstrip("\n") + "\n").encode("utf-8", "surrogateescape")
    write_atomic(path, data, 0o644)
    print(path)
    print("brief %.1f KB vs plan %.1f KB" % (len(data) / 1024.0, plan_size / 1024.0))


# -- implement / director -----------------------------------------------------------------------

def build_work_brief(c, ns, ids, role, kind):
    plan = c.plan
    steps = [plan.step(i) for i in ids] if not plan.legacy else []
    steps = [s for s in steps if s]
    keys = step_keys(c, steps, ids)
    ids_text = ", ".join(ids)
    legacy = plan.legacy
    out = ["# Brief: %s step(s) %s — %s/%s" % (kind, ids_text, c.repo_name, c.slug)]
    if legacy:
        out.append(legacy_note(c))
    out.append(section("Where you work", where_text(c, role, ns.tier, ns.escalation, steps)))
    out.append(section("Checkpoint commands", checkpoint_text(c, role, ids_text, ids[-1])))
    out.append(section("Summary", summary_text(plan)))
    out.append(section("Decisions", decisions_text(plan)))
    out.append(section("Assumed decisions", assumed_text(c)))
    sup, other = ([], [])
    if not legacy:
        sup, other = revision_parts(plan, keys, ids)
    if legacy:
        mine = whole_steps_text(c)
    else:
        blocks = []
        if sup:
            blocks.append("Later revisions win over the plan and over earlier revisions.\n\n" + "\n\n".join(sup))
        blocks.extend(s.text() for s in steps)
        mine = "\n\n".join(blocks)
    out.append(section("Your steps", mine))
    if kind == "director":
        vis = "\n\n".join("Step %s: %s" % (s.id, s.field_text("visual") or "(no `visual:` line)") for s in steps)
        out.append(section("What you judge (visual)", vis if steps else "(legacy plan: read the step text)"))
    out.append(section("Steps yours depend on", depends_text(plan, steps, ids) if steps else ""))
    out.append(section("Revisions that touch your steps",
                       "\n\n".join(other) if other else ""))
    fsec = plan.find("findings")
    out.append(section("Findings about your files",
                       omitted_or(plan, fsec, lambda s: pl.filter_section(plan, s, keys)) if fsec else ""))
    rsec = plan.find("risks")
    out.append(section("Risks about your steps",
                       omitted_or(plan, rsec, lambda s: pl.filter_section(plan, s, keys)) if rsec else ""))
    out.append(section("Verification for your steps", verification_text(plan, keys, set(ids))))
    out.append(section("Context files", context_text(c, steps) if steps else ""))
    out.append(section("Lessons", lessons_text(c, "director" if kind == "director" else "implementer", keys)))
    out.append(section("Prior work", prior_text(c)))
    out.append(section("From the orchestrator", read_extra(ns.extra)))
    out.append(section("If this brief is not enough", FALLBACK))
    return "\n\n".join(out)


def cmd_brief_work(ns, c, kind):
    spec = ns.arg
    if not spec:
        raise OrchError("brief %s needs the step id(s)" % kind, 1)
    ids = expand_steps(c.plan, spec)
    if kind == "director" and len(ids) != 1:
        raise OrchError("brief director takes exactly one step", 1)
    role = ns.role or ("director" if kind == "director" else "implementer")
    if not flow.ROLE_RE.match(role):
        raise OrchError("bad --role %r" % role, 1)
    text = build_work_brief(c, ns, ids, role, kind)
    stem = ("director-s%s" if kind == "director" else "implement-s%s") % "-".join(ids)
    with TaskLock(c.task_dir):
        os.makedirs(os.path.join(c.task_dir, "briefs"), exist_ok=True)
        path = unique_path(os.path.join(c.task_dir, "briefs"), stem)
        emit(path, text, c.plan_size)
    return 0


# -- review / security --------------------------------------------------------------------------

def guard(text):
    return GUARD_RE.sub(r"  \1", text)


def branch_sha(c):
    if c.repo_path == "unknown" or not os.path.isdir(c.repo_path):
        raise OrchError("repo %r not found; the reviewed sha cannot be read" % c.repo_path, 1)
    why = merge.bad_ref(c.branch)
    if why:
        raise OrchError("task_branch %r is %s" % (c.branch, why), 1)
    sha = merge.branch_tip(c.repo_path, c.branch)
    if not sha:
        raise OrchError("task branch %s does not exist in %s" % (c.branch, c.repo_path), 1)
    return sha


RANGE_RE = re.compile(r"^(.+?)(\.\.\.?)(.+)$")


def review_range(c, ns, tip):
    """(range text, reviewed sha)."""
    if ns.range:
        m = RANGE_RE.match(ns.range)
        if not m:
            raise OrchError("--range must look like A..B or A...B, got %r" % ns.range, 1)
        left, op, right = m.groups()
        for side in (left, right):
            if merge.bad_ref(side):
                raise OrchError("--range: %r is %s" % (side, merge.bad_ref(side)), 1)
        sha = merge.commit_of(c.repo_path, right)
        base_sha = merge.commit_of(c.repo_path, left)
        if not sha or not base_sha:
            raise OrchError("--range %s does not resolve in %s" % (ns.range, c.repo_path), 1)
        return "%s%s%s" % (left, op, sha), sha
    if c.program and re.match(r"^[0-9a-fA-F]{7,64}$", c.reviewed_through or ""):
        if not merge.commit_of(c.repo_path, c.reviewed_through.lower()):
            raise OrchError("reviewed_through %s is not a commit of %s" % (c.reviewed_through, c.repo_path), 1)
        return "%s..%s" % (c.reviewed_through.lower(), tip), tip
    why = merge.bad_ref(c.base)
    if why:
        raise OrchError("base_branch %r is %s" % (c.base, why), 1)
    return "%s...%s" % (c.base, tip), tip


def diff_stat(c, rng):
    r = merge.git(c.repo_path, "diff", "--stat=200", rng)
    if not r.ok:
        return "(git diff --stat failed: %s)" % merge.first_lines(r, 1)
    rows = r.out.rstrip("\n").splitlines()
    if len(rows) > STAT_LIMIT:
        rows = rows[:STAT_LIMIT - 1] + ["... (%d more lines; run `git diff --stat %s`)" % (len(rows) - STAT_LIMIT + 1, rng)]
    return "\n".join(rows) if rows else "(empty diff)"


def next_round(c):
    m = re.match(r"^\s*(\d+)\s*/", c.status.get("review_round") or "")
    return int(m.group(1)) + 1 if m else 1


def check_not_journaled(c, reviewer, rnd):
    for rec in flow.read_journal(c.task_dir):
        if flow.kind_of(rec) != "review_round" or rec.get("reviewer") != reviewer or rec.get("round") != rnd:
            continue
        if c.program and rec.get("milestone") != c.ms:
            continue
        raise OrchError("round %d of the %s review is already journaled; brief the next round (--round %d)" % (rnd, reviewer, rnd + 1), 2)


def check_not_repinned(c, reviewer, rnd, path, sha):
    """A round's brief pins the sha the reviewer is briefed on, and `orch merge` trusts that pin. Re-briefing
    a round at a different sha would silently move it, so refuse; the same sha (e.g. new --extra) is harmless."""
    if not os.path.exists(path):
        return
    try:
        old = merge.brief_header(path, rnd, c.program, c.ms)
    except OrchError as exc:
        raise OrchError("%s exists but its reviewed sha cannot be read (%s); brief the next round (--round %d)"
                        % (path, exc, rnd + 1), 2)
    if old != sha:
        raise OrchError("brief for round %d of the %s review is already written at %s; the branch is now at %s. "
                        "Brief the next round (--round %d), not this one." % (rnd, reviewer, old[:9], sha[:9], rnd + 1), 2)


def build_review_brief(c, ns, reviewer, rnd, rng, sha):
    plan = c.plan
    steps = plan.steps
    all_files = []
    for s in steps:
        for p in s.files(c.repo_path if c.repo_path != "unknown" else None):
            if p not in all_files:
                all_files.append(p)
    keys = pl.Keys([], all_files, c.roots)
    role = "%s-reviewer" % reviewer
    fname = "review.md" if reviewer == "code" else "security-review.md"
    head = ["# Brief: %s review, round %d — %s/%s" % (reviewer, rnd, c.repo_name, c.slug),
            "- reviewed sha: %s" % sha,
            "- round: %d" % rnd]
    if c.program:
        head.append("- milestone: %d" % c.ms)
    head.append("- range: %s" % rng)
    out = ["\n".join(head),
           "Review exactly this range. If the branch has moved past %s, say so in your review file and don't "
           "review the newer commits." % sha,
           "- worktree: %s" % (c.wt or "none (read the repo)"),
           "- branch: %s   base: %s" % (c.branch, c.base),
           "- plan: %s" % c.plan_path,
           "- task dir (status.md, log.md): %s" % c.task_dir,
           "- write your findings to: %s" % os.path.join(c.review_dir, fname)]
    body = []
    if plan.legacy:
        body.append(legacy_note(c))
    body.append(section("Summary", summary_text(plan)))
    body.append(section("Decisions", decisions_text(plan)))
    body.append(section("Assumed decisions", assumed_text(c)))
    if plan.legacy:
        body.append(section("Steps", whole_steps_text(c)))
    else:
        blocks = []
        for s in steps:
            lines = ["### Step %s — %s" % (s.id, s.title)]
            for f in REVIEW_FIELDS:
                lines.extend(s.field_lines(f))
            blocks.append("\n".join(lines))
        body.append(section("Steps (files, do, contract, done when)", "\n\n".join(blocks)))
    for key, title in (("files", "Files and areas touched"), ("risks", "Risks and unknowns"), ("verification", "Verification")):
        sec = plan.find(key)
        body.append(section(title, omitted_or(plan, sec, plan.body) if sec else ""))
    revs = plan.revisions()
    body.append(section("Revisions (all, verbatim; later ones win)",
                        "\n\n".join(plan.block(r.start, r.end) for r in revs)))
    body.append(section("Diff stat (%s)" % rng, "```\n%s\n```" % diff_stat(c, rng)))
    body.append(section("Lessons", lessons_text(c, role, keys)))
    body.append(section("From the orchestrator", read_extra(ns.extra)))
    out.append(section("Checkpoint commands", review_checkpoint_text(c, role)))
    out.append(guard("\n\n".join(body)))
    return "\n\n".join(out)


def cmd_brief_review(ns, c, reviewer):
    rnd = ns.round if ns.round is not None else next_round(c)
    if rnd < 1:
        raise OrchError("--round must be at least 1", 1)
    tip = branch_sha(c)
    rng, sha = review_range(c, ns, tip)
    check_not_journaled(c, reviewer, rnd)
    text = build_review_brief(c, ns, reviewer, rnd, rng, sha)
    path = merge.brief_path(c.task_dir, reviewer, rnd, c.program, c.ms)
    with TaskLock(c.task_dir):
        check_not_repinned(c, reviewer, rnd, path, sha)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        emit(path, text, c.plan_size)
    return 0


# -- fix -----------------------------------------------------------------------------------------

def finding_blocks(path, wanted):
    """{id: verbatim finding block} and {id: [round-note texts]} from a review file."""
    plan = pl.parse_file(path)
    lines, mask = plan.lines, plan.mask
    heads = [(i, m.group(1)) for i, l in enumerate(lines) if not mask[i]
             for m in [re.match(r"^###\s+([FS]\d+)\b", l)] if m]
    ends = [i for i, l in enumerate(lines) if not mask[i] and re.match(r"^#{1,3}\s", l)]
    found = {}
    for i, fid in heads:
        if fid not in wanted or fid in found:
            continue
        end = next((e for e in ends if e > i), len(lines))
        found[fid] = plan.block(i, end)
    notes = dict((w, []) for w in wanted)
    for sec in plan.sections:
        if not re.match(r"round\s+\d+", sec.norm):
            continue
        for s, e in pl.split_units(lines, mask, sec.start + 1, sec.end):
            text = plan.block(s, e)
            m = re.match(r"^[-*]\s+([FS]\d+)\s*:", text)
            if m and m.group(1) in notes:
                notes[m.group(1)].append("(%s) %s" % (re.sub(r"^#+\s*", "", lines[sec.start]), text))
    return found, notes


def cmd_brief_fix(ns, c):
    try:
        rnd = int(ns.arg)
    except (TypeError, ValueError):
        raise OrchError("brief fix needs the round number: `brief fix ROUND --findings F1,S2`", 1)
    if rnd < 1:
        raise OrchError("fix ROUND must be at least 1", 1)
    if not ns.findings:
        raise OrchError("brief fix needs --findings F1,S2", 1)
    wanted = [w for w in re.split(r"[,\s]+", ns.findings.strip()) if w]
    for w in wanted:
        if not FIX_ID_RE.match(w):
            raise OrchError("bad finding id %r (F<n> for review.md, S<n> for security-review.md)" % w, 1)
    wanted = list(dict.fromkeys(wanted))
    blocks, notes = {}, {}
    for prefix, fname in (("F", "review.md"), ("S", "security-review.md")):
        ids = [w for w in wanted if w.startswith(prefix)]
        if not ids:
            continue
        path = os.path.join(c.review_dir, fname)
        if not os.path.isfile(path):
            raise OrchError("%s not found (needed for %s)" % (path, ", ".join(ids)), 1)
        b, n = finding_blocks(path, ids)
        blocks.update(b)
        notes.update(n)
    missing = [w for w in wanted if w not in blocks]
    if missing:
        raise OrchError("finding(s) not found: %s" % ", ".join(missing), 1)
    text_blocks = []
    for w in wanted:
        text_blocks.append(blocks[w] + ("\n\n" + "\n\n".join(notes[w]) if notes[w] else ""))
    findings_text = "\n\n".join(text_blocks)
    plan = c.plan
    steps = []
    repo = c.repo_path if c.repo_path != "unknown" else None
    for s in plan.steps:
        keys = pl.Keys([], s.files(repo), c.roots)
        if keys.re and keys.re.search(findings_text):
            steps.append(s)
    role = ns.role or "implementer"
    if not flow.ROLE_RE.match(role):
        raise OrchError("bad --role %r" % role, 1)
    ids = [s.id for s in steps]
    keys = step_keys(c, steps, ids)
    out = ["# Brief: fix round %d (%s) — %s/%s" % (rnd, ", ".join(wanted), c.repo_name, c.slug)]
    if plan.legacy:
        out.append(legacy_note(c))
    out.append(section("Where you work", where_text(c, role, ns.tier, ns.escalation, steps)))
    out.append(section("Checkpoint commands", checkpoint_text(c, role, "fix round %d" % rnd, "fix round %d" % rnd)))
    out.append(section("Findings to fix (verbatim; write each resolution under the finding in its review file)", findings_text))
    out.append(section("Steps whose files the findings cite", "\n\n".join(s.text() for s in steps) if steps else
                       ("(legacy plan: read the Steps section of %s)" % c.plan_path if plan.legacy else "")))
    out.append(section("Lessons", lessons_text(c, role, keys)))
    out.append(section("From the orchestrator (class and invariant)", read_extra(ns.extra)))
    out.append(section("If this brief is not enough", FALLBACK))
    text = "\n\n".join(out)
    with TaskLock(c.task_dir):
        os.makedirs(os.path.join(c.task_dir, "briefs"), exist_ok=True)
        path = unique_path(os.path.join(c.task_dir, "briefs"), "fix-r%d" % rnd)
        emit(path, text, c.plan_size)
    return 0


# -- command ------------------------------------------------------------------------------------

KINDS = ("implement", "director", "review", "security", "fix")


def cmd_brief(ns, extra):
    require_write_allowed()
    if ns.kind not in KINDS:
        raise OrchError("brief KIND must be one of: %s" % ", ".join(KINDS), 1)
    if ns.tier is not None and not flow.TIER_RE.match(ns.tier):
        raise OrchError("--tier must look like sonnet/high", 1)
    if ns.escalation is not None and ns.escalation not in ("none", "proactive", "post-failure"):
        raise OrchError("--escalation must be none, proactive or post-failure", 1)
    c = load_ctx(ns)
    if ns.kind in ("implement", "director"):
        return cmd_brief_work(ns, c, ns.kind)
    if ns.kind == "fix":
        return cmd_brief_fix(ns, c)
    if ns.arg:
        raise OrchError("brief %s takes no positional argument (use --round N)" % ns.kind, 1)
    return cmd_brief_review(ns, c, "code" if ns.kind == "review" else "security")


def register(add):
    sp = add("brief", cmd_brief, "write TASK/briefs/<kind>-….md: one role's context (implement|director|review|security|fix)")
    sp.add_argument("kind")
    sp.add_argument("arg", nargs="?", default=None, metavar="STEPS|STEP|ROUND")
    sp.add_argument("--findings", default=None)
    sp.add_argument("--plan", default=None)
    sp.add_argument("--role", default=None)
    sp.add_argument("--tier", default=None)
    sp.add_argument("--escalation", default=None)
    sp.add_argument("--range", default=None)
    sp.add_argument("--round", type=int, default=None)
    sp.add_argument("--extra", default=None)
    pl.register(add)
