"""orch brief: one file per role holding exactly the context that role needs (implement, director, review,
security, fix). Deterministic: the same inputs give a byte-identical file. Nothing here writes outside
TASK/briefs/. Python 3.9 compatible, standard library only."""
import glob
import os
import re
import shlex
import shutil
import sys

from . import cli
from . import current
from . import findings as fd
from . import flow
from . import infer
from . import lessons as ls
from . import merge
from . import plan as pl
from . import questions as qs
from . import status as st
from .paths import BIN_DIR, OrchError, TaskLock, orch_root, require_write_allowed, task_names, write_atomic

SUPERSEDE_RE = re.compile(r"restat|replaces|replaced|supersed|\bnew block\b", re.I)
NO_WORKTREE = ("not yet created", "unknown", "removed after merge")
EXTRA_LIMIT = 1024 * 1024
PRIOR_LIMIT = 20
STAT_LIMIT = 200
CHECK_CYCLES = 3          # fix-and-check cycles per code review round (SKILL.md, Review loops)
GUARD_RE = re.compile(r"^(- (?:reviewed sha|round|milestone):)", re.M)
FIX_ID_RE = re.compile(r"^[FS]\d+$")
SUMMARY_GATE_RE = re.compile(r"^- (?:shape|decisions needing the user|implementer tier|sketched steps|guided steps|visual steps):")
ENTRY_RE = re.compile(r"^(?:-[ \t]+\*\*|###[ \t]+)(D\d+)(?![A-Za-z0-9_])")
BOUNDARY = r"(?<![^\s(\[<{'\"`=,:;])"      # start of text, whitespace, or one of ( [ < { ' " ` = , : ;
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


def target_plan(c, ns):
    """(path, milestone or None) of the document a planner revises or a plan reviewer reviews: --plan; in a program the
    current milestone's plan when it exists, else roadmap.md; in a single-plan task plan.md."""
    if ns.plan:
        return os.path.abspath(os.path.expanduser(ns.plan)), None
    if c.program:
        if c.ms is not None:
            hits = [h for h in sorted(glob.glob(os.path.join(glob.escape(c.task_dir), "m%02d-*" % c.ms))) if os.path.isdir(h)]
            if len(hits) == 1 and os.path.isfile(os.path.join(hits[0], "plan.md")):
                return os.path.join(hits[0], "plan.md"), c.ms
        return os.path.join(c.task_dir, "roadmap.md"), None
    return os.path.join(c.task_dir, "plan.md"), None


def load_ctx(ns, infer=False, target=False):
    """The task, its status fields and its plan. `target`: the brief is for the planner or the plan reviewer, so the
    document is the plan or the roadmap (target_plan) and a program needs no milestone yet."""
    c = Ctx()
    c.task_dir = cli.get_task(ns, infer=infer)
    c.repo_name, c.slug = task_names(c.task_dir)
    c.status = cli.load_status(c.task_dir)
    c.program, c.ms = cli.program_info(c.status)
    c.target_ms = None
    if c.program and c.ms is None and not target:
        raise OrchError("program task with no resolvable milestone in status.md (milestone: %s)" % c.status.get("milestone"), 1)
    c.review_dir = c.task_dir
    if target:
        c.plan_path, c.target_ms = target_plan(c, ns)
        c.review_dir = os.path.dirname(c.plan_path)
    elif ns.plan:
        c.plan_path = os.path.abspath(os.path.expanduser(ns.plan))
        if c.program:
            c.review_dir = os.path.dirname(c.plan_path)
    elif c.program:
        c.review_dir = milestone_dir(c.task_dir, c.ms)
        c.plan_path = os.path.join(c.review_dir, "plan.md")
    else:
        c.plan_path = os.path.join(c.task_dir, "plan.md")
    if not os.path.isfile(c.plan_path):
        raise OrchError("no %s at %s" % ("plan or roadmap" if target else "plan", c.plan_path), 1)
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


def orch_cli():
    """`orch` when PATH resolves to the copy writing this brief (D3), else that copy's absolute path, shell-quoted only
    when it needs it: a dev session's PATH holds the installed copy first, which lacks newer verbs."""
    mine = os.path.join(BIN_DIR, "orch")
    found = shutil.which("orch")
    if found and os.path.realpath(found) == os.path.realpath(mine):
        return "orch"
    return shlex.quote(mine)


def orch_head(c):
    """`<orch>` or `<orch> -t '<repo>/<slug>'`: the task is named only when a bare call from the role's shell would not
    land on it. The resolver is the one `orch role` uses, run on this session and ORCH_TASK with a cwd that is in no
    worktree: the role's shell starts in the session's original cwd, not wherever this process has `cd`ed to, so a cwd match
    here must not decide for it. Never the absolute dir."""
    try:
        found, _ = infer.resolve(os.sep, st.driver_session_id(), (os.environ.get("ORCH_TASK") or "").strip(), orch_root())
        if current.same_dir(found, c.task_dir):
            return orch_cli()
    except OrchError:
        pass
    return "%s -t %s" % (orch_cli(), sq("%s/%s" % task_names(c.task_dir)))


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


def fallback_text(c):
    head = orch_head(c)
    return ("Run %s plan for the plan's outline, then %s plan --section <name> or --step <id> for the part you need, "
            "and record brief-gap: <what was missing> with %s append notes." % (head, head, head))


def summary_slice(text):
    """Work and director briefs: drop the Summary's gate-only lines (and their indented continuation)."""
    out, skipping = [], False
    for line in text.split("\n"):
        if SUMMARY_GATE_RE.match(line):
            skipping = True
            continue
        if skipping and line[:1] in (" ", "\t") and line.strip():
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


def decisions_slice(c, text, keys, cited_text):
    """Work and director briefs: in a decision entry that matches none of the brief's step keys and whose id the steps do
    not cite, `Rejected:` up to `Status:` (or the entry's end) becomes a pointer to `orch plan --decision`."""
    lines = text.split("\n")
    mask = pl.fence_mask(lines)
    starts = [i for i, l in enumerate(lines) if not mask[i] and ENTRY_RE.match(l)]
    if not starts:
        return text
    out, pos = [], 0
    for i in starts:
        end = i + 1
        while end < len(lines) and (mask[end] or not (pl.HEAD_RE.match(lines[end]) or ENTRY_RE.match(lines[end]))):
            end += 1
        did = ENTRY_RE.match(lines[i]).group(1)
        entry = "\n".join(lines[i:end])
        cited = re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % did, cited_text)
        rej = re.search(r"Rejected:", entry)
        if rej and not cited and not keys.matches(entry):
            stat = re.search(r"Status:", entry[rej.end():])
            tail_end = rej.end() + stat.start() if stat else len(entry)
            ptr = "(alternatives: %s plan --decision %s)" % (orch_head(c), did)
            line_start = entry.rfind("\n", 0, tail_end) + 1
            if stat and re.match(r"^[ \t]*(?:[-*][ \t]+)?$", entry[line_start:tail_end]) and line_start > rej.start():
                entry = entry[:rej.start()] + ptr + "\n" + entry[line_start:]
            else:
                entry = entry[:rej.start()] + ptr + (" " + entry[tail_end:] if stat else "")
            out.extend(lines[pos:i])
            out.extend(entry.split("\n"))
            pos = end
    out.extend(lines[pos:])
    return "\n".join(out)


def path_mode(c):
    """'wt' (paths relative to the worktree), 'repo' (no worktree, the repo itself) or None (nothing is rewritten)."""
    if c.wt:
        return "wt"
    if c.no_wt_mode and c.repo_path != "unknown":
        return "repo"
    return None


def rel_fields(c, text):
    """Step blocks a brief prints: the values of `files:` and `context:` fields (as the plan parser delimits a field:
    up to the next known field line or heading) lose their `<worktree>/` and `<repo>/` prefixes and expand `~/`.
    Every other line is returned byte for byte. Paths outside both roots stay absolute."""
    mode = path_mode(c)
    if mode is None:
        return text
    roots = [r for r in ((c.wt if mode == "wt" else None), c.repo_path if c.repo_path != "unknown" else None) if r]
    home = os.path.expanduser("~").rstrip("/") + "/"
    lines = text.split("\n")
    mask = pl.fence_mask(lines)
    cur = None
    for i, l in enumerate(lines):
        if not mask[i]:
            if pl.HEAD_RE.match(l):
                cur = None
            else:
                m = pl.FIELD_LINE_RE.match(l)
                if m and m.group(1).strip().lower() in pl.KNOWN:
                    cur = m.group(1).strip().lower()
        if cur in ("files", "context"):
            l = re.sub(BOUNDARY + r"~/", lambda _: home, l)
            for r in roots:
                l = re.sub(BOUNDARY + re.escape(r.rstrip("/") + "/"), "", l)
            lines[i] = l
    return "\n".join(lines)


EST_PCT = "<0-99: your rough estimate of this assignment>"


def checkpoint_text(c, role, ids_text, last_id):
    head = "%s role %s" % (orch_head(c), sq(role))
    return "\n".join([
        "Run these as written (replace only the quoted placeholders):",
        "",
        "```",
        "%s in_progress --note %s --log %s --pct 0" % (head, sq("working on step(s) %s" % ids_text), sq("started step(s) %s" % ids_text)),
        "# after each step is committed:",
        "%s in_progress --step %s --log %s --pct %s" % (head, sq("<step id just finished>"), sq("step <id> done: <what> (<commit sha>)"), sq(EST_PCT)),
        "# when everything assigned to you is done:",
        "%s done --step %s --note %s --log %s" % (head, sq(last_id), sq("<one line>"), sq("done: <one line>")),
        "# when you must stop:",
        "%s blocked --note %s --log %s" % (head, sq("need: <what>"), sq("blocked: <what>")),
        "```",
    ])


def review_checkpoint_text(c, role):
    """The reviewer's checkpoint commands. The task is the one holding status.md: in a program that is the program
    dir, not the milestone dir the plan and review file sit in."""
    head = "%s role %s" % (orch_head(c), sq(role))
    return "\n".join([
        "Run these as written (replace only the quoted placeholders):",
        "",
        "```",
        "%s in_progress --note %s --log %s --pct 0" % (head, sq("reviewing"), sq("started review")),
        "# after each file group you finish:",
        "%s in_progress --log %s --pct %s" % (head, sq("reviewed <file group>"), sq(EST_PCT)),
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
    out.append("- notes: %s (read earlier entries here; append with %s append notes --as %s)"
               % (os.path.join(c.task_dir, "notes.md"), orch_head(c), role))
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
    mode = path_mode(c)
    if mode == "wt":
        out.append("- step files: and context: paths are relative to the worktree root above (Read/Edit/Write take %s/<path>); "
                   "all other plan text is as written: a path under %s there is a main-repo path, the same file in your "
                   "worktree is under %s" % (c.wt, c.repo_path if c.repo_path != "unknown" else "<repo>", c.wt))
    elif mode == "repo":
        out.append("- step files: and context: paths are relative to the repo root above")
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
                if own is not None and own.start >= sub.start:
                    continue      # the printed step is this block, or a later block that restates it
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
            parts.append("The orchestrator runs this during review; it's here so your work can pass it.\n\n"
                         + plan.block(sub.start, sub.end))
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


REUSE_ROLES = {"implement": "implementer", "fix": "implementer", "director": "director", "revise": "planner",
               "plan-review": "plan-reviewer"}     # brief kinds whose prompt prints a SendMessage line


def spawn_prompt(path, ns, kind, reuse=None):
    """The complete spawn prompt for this brief, so the orchestrator sends it unchanged: tier, escalation, tradeoff and
    the brief path, nothing else (no test scope, no gate, no operating instruction). A value not given is a visible
    placeholder."""
    given = (("tier", ns.tier), ("escalation", ns.escalation), ("tradeoff", getattr(ns, "tradeoff", None)))
    missing = [k for k, v in given if not v]
    if missing:
        sys.stderr.write("orch brief: no --%s given; the spawn prompt shows %s as a placeholder\n"
                         % (", --".join(missing), ", ".join("<%s>" % k for k in missing)))
    lines = ["", "--- spawn prompt (send unchanged) ---"]
    for k, v in given:
        lines.append("%s: %s" % (k, " ".join(v.split()) if v else "<%s>" % k))
    lines += ["", "Your whole context is this brief: %s" % path]
    reuse = reuse or REUSE_ROLES.get(kind)
    if reuse:
        lines += ["--- reused %s: SendMessage unchanged instead ---" % reuse, "orch: next work \u2014 brief %s" % path]
    lines.append("--- end ---")
    print("\n".join(lines))


def emit(path, text, plan_size, ns=None, kind=None, reuse=None):
    """Write the brief and print its path and spawn prompt. `reuse` names the role a SendMessage line is printed for
    (implement and fix briefs: the implementer; a code check or a security round 2+: that reviewer)."""
    data = (text.rstrip("\n") + "\n").encode("utf-8", "surrogateescape")
    write_atomic(path, data, 0o644)
    print(path)
    print("brief %.1f KB vs plan %.1f KB" % (len(data) / 1024.0, plan_size / 1024.0))
    if ns is not None:
        spawn_prompt(path, ns, kind, reuse)


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
    out.append(section("Summary", summary_slice(summary_text(plan))))
    decisions_at = len(out)
    out.append(None)       # Decisions: sliced below, once the steps' text says which entries they cite
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
        mine = rel_fields(c, "\n\n".join(blocks))
    out.append(section("Your steps", mine))
    out[decisions_at] = section("Decisions", decisions_slice(c, decisions_text(plan), keys, mine))
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
    out.append(section("If this brief is not enough", fallback_text(c)))
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
        emit(path, text, c.plan_size, ns, kind)
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


def code_lines(c):
    """The journaled code `review_round` lines in this task's scope (one milestone in a program), oldest first."""
    out = []
    for rec in flow.read_journal(c.task_dir):
        if flow.kind_of(rec) != "review_round" or rec.get("reviewer") != "code":
            continue
        if c.program and rec.get("milestone") != c.ms:
            continue
        out.append(rec)
    return out


def is_check(rec):
    return rec.get("check") is True


def cycle_of(rec):
    """The fix-and-check cycle a check line closes; a line without a usable `cycle` reads as cycle 1."""
    v = rec.get("cycle")
    return v if type(v) is int and v >= 1 else 1


def open_count(rec):
    """Blockers plus should-fix still open on a review line (non-integer counts read as 0: a line that does not
    say what is open starts no further cycle)."""
    return sum(rec.get(k) for k in ("blockers", "should_fix") if type(rec.get(k)) is int)


def cycles_used(checks):
    """How many fix-and-check cycles a round's check lines used. A corrected line for the same cycle (park the findings,
    journal that check again) replaces the earlier one: it never uses up a cycle, so count distinct cycles."""
    return len(set(cycle_of(r) for r in checks))


def answered_after(c, ts):
    """True when a question asked at or after `ts` has been answered with `orch answer` (status answered, not
    withdrawn or assumed). Timestamps are minute-resolution UTC strings; a question asked in the same minute counts."""
    if not isinstance(ts, str):
        return False
    for q in qs.load_questions(c.task_dir):
        m = re.search(r"\bat\s+(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?Z?)\s*$", q.fields.get("asked by", ""))
        if q.status == "answered" and m and m.group(1) >= ts:
            return True
    return False


def cap_unlocked(c, last):
    """True when the round's cycles are spent, check 3 left a blocker open, and the user has answered a question
    asked after that check line (D9): round N+1 may then be briefed over the open blocker."""
    if not (is_check(last) and type(last.get("blockers")) is int and last["blockers"] > 0):
        return False
    if cycles_used(round_chain(c, last.get("round"))[1]) < CHECK_CYCLES:
        return False
    return answered_after(c, last.get("ts"))


def round_chain(c, rnd):
    """The journaled code lines of round `rnd`: ([real round lines], [check lines in journal order])."""
    lines = [r for r in code_lines(c) if r.get("round") == rnd]
    return [r for r in lines if not is_check(r)], [r for r in lines if is_check(r)]


def fresh_round_start(c):
    """The round line to start the fresh round from, or None. A blocker chain (round N or any of its checks held a
    blocker) that ends on a passed check gets one fresh round N+1, read from round N's reviewed sha so the
    reviewer sees every fix. A clean should-fix chain keeps the range from the check's sha."""
    lines = code_lines(c)
    if not lines or not is_check(lines[-1]):
        return None
    if open_count(lines[-1]) and not cap_unlocked(c, lines[-1]):
        return None
    real, checks = round_chain(c, lines[-1].get("round"))
    if not real:
        return None
    if any(type(r.get("blockers")) is int and r["blockers"] > 0 for r in real[-1:] + checks):
        return real[-1]
    return None


def narrow_range(c, tip, start=None):
    """('<last reviewed sha>..<tip>', tip), or (None, why) when there is no usable earlier code review. `start` is
    the journal line whose sha begins the range (default: the last code line, check lines included)."""
    lines = code_lines(c)
    if not lines:
        return None, "no earlier code review is journaled"
    sha = (start or lines[-1]).get("sha")
    if not isinstance(sha, str) or not merge.HEX_RE.match(sha.lower()) or not merge.commit_of(c.repo_path, sha.lower()):
        return None, "the last code review line has no usable sha"
    sha = sha.lower()
    if not merge.git(c.repo_path, "merge-base", "--is-ancestor", sha, tip).ok:
        return None, "the last reviewed sha %s is not an ancestor of the tip" % sha[:9]
    return "%s..%s" % (sha, tip), None


def check_range(repo, text):
    """(left, op, right, right's sha) for a --range A..B or A...B; the refusals `orch brief review` and `orch diff` share."""
    m = RANGE_RE.match(text)
    if not m:
        raise OrchError("--range must look like A..B or A...B, got %r" % text, 1)
    left, op, right = m.groups()
    for side in (left, right):
        if merge.bad_ref(side):
            raise OrchError("--range: %r is %s" % (side, merge.bad_ref(side)), 1)
    sha = merge.commit_of(repo, right)
    base_sha = merge.commit_of(repo, left)
    if not sha or not base_sha:
        raise OrchError("--range %s does not resolve in %s" % (text, repo), 1)
    return left, op, right, sha


def review_range(c, ns, tip, reviewer="code", rnd=1, check=False, start=None):
    """(range text, reviewed sha). From round 2 (and for a check) the code review covers only what changed
    since the last reviewed sha; security and round 1 keep the whole branch."""
    if ns.range:
        left, op, _, sha = check_range(c.repo_path, ns.range)
        return "%s%s%s" % (left, op, sha), sha
    if reviewer == "code" and (rnd >= 2 or check):
        rng, why = narrow_range(c, tip, start)
        if rng:
            return rng, tip
        print("orch: %s; reviewing the whole range instead" % why, file=sys.stderr)
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


def check_not_journaled(c, reviewer, rnd, check=False, cycle=1):
    for rec in flow.read_journal(c.task_dir):
        if flow.kind_of(rec) != "review_round" or rec.get("reviewer") != reviewer or rec.get("round") != rnd:
            continue
        if (rec.get("check") is True) != check:
            continue
        if c.program and rec.get("milestone") != c.ms:
            continue
        if check:
            if cycle_of(rec) != cycle:
                continue
            raise OrchError("check %d of round %d is already journaled" % (cycle, rnd), 2)
        raise OrchError("round %d of the %s review is already journaled; brief the next round (--round %d)" % (rnd, reviewer, rnd + 1), 2)


def cap_text(rnd, last):
    """What to do when the round's cycles are spent and `last` (check 3) still has findings open. The cap is never raised."""
    cycle = cycle_of(last)
    fixed = ("journal check %d again with the parked findings not counted: `orch journal review_round reviewer=code "
             "round:=%d check:=true cycle:=%d blockers:=0 should_fix:=0 nits:=0 verdict=pass` (the later line replaces the "
             "earlier and uses up no cycle)" % (cycle, rnd, cycle))
    if type(last.get("blockers")) is int and last["blockers"] > 0:
        return ("round %d used its %d fix-and-check cycles and check %d left %d finding(s) open, a blocker among them. The "
                "cycle cap is never raised (SKILL.md step 9): park a question for the user (`orch ask`, options naming the "
                "stuck finding). After the user's answer (`orch answer`) `orch brief review` briefs round %d; there is no "
                "check %d." % (rnd, CHECK_CYCLES, cycle, open_count(last), rnd + 1, CHECK_CYCLES + 1))
    return ("round %d used its %d fix-and-check cycles and check %d left %d should-fix open. There is no check %d: park each "
            "with `orch follow-up ... --severity should-fix`, then %s; the review then passes." % (rnd, CHECK_CYCLES, cycle, open_count(last), CHECK_CYCLES + 1, fixed))


def check_round(c, ns):
    """(round, cycle) of the next fix-batch check: --round, else the latest journaled code round that is not itself
    a check. A round allows CHECK_CYCLES checks; check K needs check K-1 journaled with something open, and check 1
    needs the round itself to have something open (a blocker or a should-fix). Exit 2 when that round is not
    journaled, or when --round names a round that is not the latest real one (an older round's clean line would let a
    check follow a later round's open blocker)."""
    real = [r for r in code_lines(c) if not is_check(r) and type(r.get("round")) is int]
    if ns.round is not None:
        rnd = ns.round
    elif real:
        rnd = real[-1]["round"]
    else:
        raise OrchError("--check needs a journaled code review round whose fixes it checks; none is journaled", 2)
    if rnd < 1:
        raise OrchError("--round must be at least 1", 1)
    line = [r for r in real if r["round"] == rnd]
    if not line:
        raise OrchError("--check: round %d of the code review is not journaled" % rnd, 2)
    if rnd != real[-1]["round"]:
        raise OrchError("--check: round %d is not the latest code review round (%d); a check follows the latest round "
                        "only" % (rnd, real[-1]["round"]), 2)
    checks = round_chain(c, rnd)[1]
    last = checks[-1] if checks else line[-1]
    blockers, should_fix = last.get("blockers"), last.get("should_fix")
    if not (type(blockers) is int and blockers > 0) and type(should_fix) is int and should_fix == 0:
        if checks:
            raise OrchError("--check: check %d of round %d left nothing open, so there is nothing more to check; the "
                            "commit after a passed check takes round %d (`orch brief review`)."
                            % (len(checks), rnd, rnd + 1), 2)
        raise OrchError("--check: round %d routed no should-fix, so there is no fix batch to check; a commit after a "
                        "passed review is re-reviewed as a round. Brief round %d (`orch brief review`)."
                        % (rnd, rnd + 1), 2)
    if cycles_used(checks) >= CHECK_CYCLES:
        raise OrchError(cap_text(rnd, last), 2)
    return rnd, cycles_used(checks) + 1


def refuse_dirty(c):
    wd = work_dir(c)
    r = merge.git(wd, "status", "--porcelain") if wd else None
    if r is not None and r.ok and r.out.strip():
        n = len([l for l in r.out.splitlines() if l.strip()])
        raise OrchError("the worktree %s has %d uncommitted path(s); the review reads `git diff <range>`, which only "
                        "sees commits. Commit the fixes first." % (wd, n), 2)


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


def build_review_brief(c, ns, reviewer, rnd, rng, sha, check=False, cycle=1, fresh=False):
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
    rpath = os.path.join(c.review_dir, fname)
    prefix = "F" if reviewer == "code" else "S"
    next_id = fd.parse(rpath, reviewer != "code").next_id(prefix) if os.path.isfile(rpath) else prefix + "1"
    head = ["# Brief: %s review%s, round %d — %s/%s" % (reviewer, " fix-batch check" if check else "", rnd,
                                                      c.repo_name, c.slug),
            "- reviewed sha: %s" % sha,
            "- round: %d" % rnd]
    if c.program:
        head.append("- milestone: %d" % c.ms)
    head.append("- range: %s" % rng)
    cli_head = orch_head(c)
    head.append("- diff: %s diff --range %s  (add --step <id> or -- <path> to read one part)" % (cli_head, rng))
    if check:
        head.append("- check: true")
        head.append("- cycle: %d" % cycle)
    out = ["\n".join(head),
           "Review exactly this range. If the branch has moved past %s, say so in your review file and don't "
           "review the newer commits. Read it with the `- diff:` command above, run in the worktree." % sha]
    if check:
        heading = "## Check of round %d" % rnd if cycle == 1 else "## Check %d of round %d" % (cycle, rnd)
        out.append("This is check %d of the fix commits for round %d, not a new round. Judge whether the fixes are "
                   "right and whether they introduced a defect. Append your findings to the review file under a "
                   "`%s` heading; leave earlier text as it is." % (cycle, rnd, heading))
    elif fresh:
        out.append("This is the fresh round after a blocker. The range runs from the last real round's reviewed sha, "
                   "so it holds every fix and check commit of that round. Read the code around the fixes, not only "
                   "the diff hunks: the callers, the tests and what the changed lines depend on.")
    out += [
           "- worktree: %s" % (c.wt or "none (read the repo)"),
           "- branch: %s   base: %s" % (c.branch, c.base),
           "- plan: %s" % c.plan_path,
           "- task dir (status.md, log.md): %s" % c.task_dir,
           "- write findings with: %s write review%s (next id: %s)" % (cli_head, "" if reviewer == "code" else " --security", next_id),
           "- write your findings to: %s (the fallback; the verb checks the format)" % os.path.join(c.review_dir, fname)]
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


def security_journaled(c):
    """True when a security review_round line is journaled in this scope: only then does a security brief name a
    reviewer to resume (the first security review, whatever its number, has none). The round number is not used: it
    comes from the code review's counter."""
    return any(flow.kind_of(r) == "review_round" and r.get("reviewer") == "security"
               and (not c.program or r.get("milestone") == c.ms) for r in flow.read_journal(c.task_dir))


def refuse_open_check(c):
    """Round N+1 is refused while the latest check of round N left findings open: that is another fix cycle, or at the
    cap (D8) a user decision. After check 3 left a blocker open, the user's answer (`orch answer`) opens the way (D9)."""
    lines = code_lines(c)
    if lines and is_check(lines[-1]) and open_count(lines[-1]):
        last = lines[-1]
        if cap_unlocked(c, last):
            return
        rnd = last.get("round")
        if cycles_used(round_chain(c, rnd)[1]) >= CHECK_CYCLES:
            raise OrchError(cap_text(rnd, last), 2)
        raise OrchError("check %d of round %s left %d finding(s) open; fix them and brief the next check "
                        "(`orch brief review --check`). A round N+1 comes after a passed check."
                        % (cycle_of(last), rnd, open_count(last)), 2)


def cmd_brief_review(ns, c, reviewer):
    check = bool(ns.check)
    if check and reviewer != "code":
        raise OrchError("--check belongs to the code review (orch brief review --check)", 1)
    cycle = 1
    if check:
        rnd, cycle = check_round(c, ns)
    else:
        rnd = ns.round if ns.round is not None else next_round(c)
    if rnd < 1:
        raise OrchError("--round must be at least 1", 1)
    if reviewer == "code":
        refuse_dirty(c)
        if not check:
            refuse_open_check(c)
    tip = branch_sha(c)
    check_not_journaled(c, reviewer, rnd, check, cycle)
    start = fresh_round_start(c) if reviewer == "code" and not check else None
    rng, sha = review_range(c, ns, tip, reviewer, rnd, check, start)
    text = build_review_brief(c, ns, reviewer, rnd, rng, sha, check, cycle, start is not None and not ns.range)
    path = merge.brief_path(c.task_dir, reviewer, rnd, c.program, c.ms, check, cycle)
    with TaskLock(c.task_dir):
        check_not_repinned(c, reviewer, rnd, path, sha)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        reuse = "code-reviewer" if check else "security-reviewer" if reviewer == "security" and security_journaled(c) else None
        emit(path, text, c.plan_size, ns, "review" if reviewer == "code" else "security", reuse)
    return 0


# -- fix -----------------------------------------------------------------------------------------

def finding_blocks(path, wanted):
    """{id: verbatim finding block} and {id: [round-note texts]} from a review file (the parser `orch findings` uses)."""
    parsed = fd.parse(path, lenient_heads=True)     # the old reader's headings: `### F1: x` still names a finding here
    found = dict((f.id, f.block) for f in parsed.findings if f.id in wanted)
    return found, dict((w, list(parsed.notes.get(w, []))) for w in wanted)


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
    # the steps whose files a selected finding names on its `- file:` line; no such line: any mention in the findings
    file_lines = "\n".join(m.group(1) for w in wanted for m in re.finditer(r"^- file:[ \t]*(.*)$", blocks[w], re.M))
    haystack = file_lines if file_lines.strip() else findings_text
    for s in plan.steps:
        keys = pl.Keys([], s.files(repo), c.roots)
        if keys.re and keys.re.search(haystack):
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
    out.append(section("Findings to fix (verbatim; record each resolution with %s finding resolve <id>)" % orch_head(c), findings_text))
    out.append(section("Steps whose files the findings cite", rel_fields(c, "\n\n".join(s.text() for s in steps)) if steps else
                       ("(legacy plan: read the Steps section of %s)" % c.plan_path if plan.legacy else "")))
    out.append(section("Lessons", lessons_text(c, role, keys)))
    out.append(section("From the orchestrator (class and invariant)", read_extra(ns.extra)))
    out.append(section("If this brief is not enough", fallback_text(c)))
    text = "\n\n".join(out)
    with TaskLock(c.task_dir):
        os.makedirs(os.path.join(c.task_dir, "briefs"), exist_ok=True)
        path = unique_path(os.path.join(c.task_dir, "briefs"), "fix-r%d" % rnd)
        emit(path, text, c.plan_size, ns, "fix")
    return 0


# -- revise / plan-review -------------------------------------------------------------------------

def target_where(c, role, tier, escalation):
    out = ["- repo: %s" % c.repo_path,
           "- plan or roadmap: %s" % c.plan_path,
           "- task dir (status.md, log.md): %s" % c.task_dir,
           "- notes: %s (read earlier entries here; append with %s append notes --as %s)"
           % (os.path.join(c.task_dir, "notes.md"), orch_head(c), role),
           "- role row: %s" % role,
           "- tier: %s" % (tier or "not stated here (the spawn message names it)"),
           "- escalation: %s" % (escalation or "none")]
    if c.wt:
        out.insert(1, "- worktree (read only): %s" % c.wt)
    if c.target_ms is not None:
        out.insert(3, "- milestone: %d" % c.target_ms)
    return "\n".join(out)


def stem_for(c, base, letter, n):
    return "%s-m%02d-%s%d" % (base, c.target_ms, letter, n) if c.target_ms is not None else "%s-%s%d" % (base, letter, n)


def plan_review_pass(c):
    """1 + the plan_review lines already journaled for this document (the roadmap: those without a milestone)."""
    return 1 + sum(1 for r in flow.read_journal(c.task_dir)
                   if flow.kind_of(r) == "plan_review" and r.get("milestone") == c.target_ms)


def cmd_brief_revise(ns, c):
    if ns.arg:
        raise OrchError("brief revise takes no positional argument (the feedback goes in --extra FILE)", 1)
    n = len(c.plan.revisions()) + 1
    role = "planner"
    out = ["# Brief: revise %s, revision %d — %s/%s" % (os.path.basename(c.plan_path), n, c.repo_name, c.slug),
           section("Where you work", target_where(c, role, ns.tier, ns.escalation)),
           section("Checkpoint commands", checkpoint_text(c, role, "revision %d" % n, "revision %d" % n)),
           section("What to do",
                   "Append a `## Revision %d` section to %s. Never rewrite approved sections; a step the revision "
                   "restates is restated whole inside it. Run %s plan-check on the file and fix every FAIL before "
                   "you return." % (n, c.plan_path, orch_head(c))),
           section("Feedback to address", read_extra(ns.extra)),
           section("Lessons", lessons_text(c, role, pl.Keys([], [], c.roots))),
           section("If this brief is not enough", fallback_text(c))]
    text = "\n\n".join(out)
    with TaskLock(c.task_dir):
        os.makedirs(os.path.join(c.task_dir, "briefs"), exist_ok=True)
        path = unique_path(os.path.join(c.task_dir, "briefs"), stem_for(c, "revise", "r", n))
        emit(path, text, c.plan_size, ns, "revise")
    return 0


def cmd_brief_plan_review(ns, c):
    if ns.arg:
        raise OrchError("brief plan-review takes no positional argument", 1)
    n = plan_review_pass(c)
    role = "plan-reviewer"
    review_path = os.path.join(c.review_dir, "plan-review.md")
    what = ["Review %s and write your findings to %s (the format is in your role file). This is pass %d." % (c.plan_path, review_path, n)]
    if n >= 2:
        what.append("Re-review only what changed since the last pass; don't re-raise closed findings. Append "
                    "`## Round %d` to %s and re-check the open findings." % (n, review_path))
    out = ["# Brief: plan review, pass %d — %s/%s" % (n, c.repo_name, c.slug),
           section("Where you work", target_where(c, role, ns.tier, ns.escalation) + "\n- write findings to: %s" % review_path),
           section("Checkpoint commands", review_checkpoint_text(c, role)),
           section("What to do", "\n\n".join(what)),
           section("From the orchestrator", read_extra(ns.extra)),
           section("Lessons", lessons_text(c, role, pl.Keys([], [], c.roots))),
           section("If this brief is not enough", fallback_text(c))]
    text = "\n\n".join(out)
    with TaskLock(c.task_dir):
        os.makedirs(os.path.join(c.task_dir, "briefs"), exist_ok=True)
        path = unique_path(os.path.join(c.task_dir, "briefs"), stem_for(c, "plan-review", "p", n))
        emit(path, text, c.plan_size, ns, "plan-review")
    return 0


# -- command ------------------------------------------------------------------------------------

KINDS = ("implement", "director", "review", "security", "fix", "revise", "plan-review")


def cmd_brief(ns, extra):
    require_write_allowed()
    if ns.kind not in KINDS:
        raise OrchError("brief KIND must be one of: %s" % ", ".join(KINDS), 1)
    if ns.tier is not None and not flow.TIER_RE.match(ns.tier):
        raise OrchError("--tier must look like sonnet/high", 1)
    if ns.escalation is not None and ns.escalation not in ("none", "proactive", "post-failure"):
        raise OrchError("--escalation must be none, proactive or post-failure", 1)
    if ns.check and ns.kind != "review":
        raise OrchError("--check belongs to `orch brief review` (the fix-batch check of the code review)", 1)
    c = load_ctx(ns, target=ns.kind in ("revise", "plan-review"))
    if ns.kind == "revise":
        return cmd_brief_revise(ns, c)
    if ns.kind == "plan-review":
        return cmd_brief_plan_review(ns, c)
    if ns.kind in ("implement", "director"):
        return cmd_brief_work(ns, c, ns.kind)
    if ns.kind == "fix":
        return cmd_brief_fix(ns, c)
    if ns.arg:
        raise OrchError("brief %s takes no positional argument (use --round N)" % ns.kind, 1)
    return cmd_brief_review(ns, c, "code" if ns.kind == "review" else "security")


def register(add):
    sp = add("brief", cmd_brief, "write TASK/briefs/<kind>-….md: one role's context (implement|director|review|security|fix|revise|plan-review)")
    sp.add_argument("kind")
    sp.add_argument("arg", nargs="?", default=None, metavar="STEPS|STEP|ROUND")
    sp.add_argument("--findings", default=None)
    sp.add_argument("--plan", default=None)
    sp.add_argument("--role", default=None)
    sp.add_argument("--tier", default=None)
    sp.add_argument("--escalation", default=None)
    sp.add_argument("--tradeoff", default=None)
    sp.add_argument("--range", default=None)
    sp.add_argument("--round", type=int, default=None)
    sp.add_argument("--check", action="store_true")
    sp.add_argument("--extra", default=None)
    pl.register(add)
