"""orch plan and orch diff: read-only views of a task's plan and of its branch, for the subagents. The task is inferred
when -t is missing (cli.get_task infer=True). Nothing here writes."""
import re
import sys

from . import brief
from . import merge
from . import plan as pl
from .paths import OrchError, warn_stderr

DECISION_ID_RE = re.compile(r"^D\d+$", re.I)
ENTRY_RE = re.compile(r"^(?:-[ \t]+\*\*|###[ \t]+)(D\d+)(?![A-Za-z0-9_])")


def load(ns):
    ns.plan = None
    return brief.load_ctx(ns, infer=True)


# -- orch plan -----------------------------------------------------------------------------------

def outline(plan):
    """One line per `##` section and `### Step` heading: `L<line>  <heading>  (<n> chars)`."""
    heads = []
    for i, line in enumerate(plan.lines):
        m = None if plan.mask[i] else pl.HEAD_RE.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    rows = []
    for k, (i, lvl, title) in enumerate(heads):
        if not (lvl == 2 or (lvl == 3 and pl.STEP_HEAD_RE.match(title))):
            continue
        end = next((j for j, l2, _ in heads[k + 1:] if l2 <= lvl), len(plan.lines))
        rows.append("L%d  %s  (%d chars)" % (i + 1, plan.lines[i].rstrip(), len(plan.block(i, end))))
    return rows


def step_text(plan, sid):
    sid = sid.lower()
    step = plan.step(sid)
    if step is None:
        raise OrchError("unknown step %r (plan steps: %s)" % (sid, ", ".join(plan.ids()) or "none"), 1)
    sup, _ = brief.revision_parts(plan, pl.Keys([sid], []), [sid])
    return "\n\n".join(sup + [step.text()])


def section_text(plan, name):
    name = name.strip().lower()
    sec = plan.find(name) if name else None
    if sec is None:
        names = [s.title for s in plan.sections if s.rev is None]
        raise OrchError("no section %r (sections: %s)" % (name, ", ".join(names) or "none"), 1)
    return plan.block(sec.start, sec.end)


def decision_scopes(plan):
    """(label, first line, end) of every decision section and every decision subsection of a revision, in file order."""
    scopes = [(plan.lines[s.start].rstrip(), s.start + 1, s.end) for s in plan.decision_sections()]
    for rev in plan.revisions():
        for sub in rev.subs:
            if re.search(r"decision", sub.title, re.I):
                label = "%s / %s" % (plan.lines[rev.start].rstrip(), plan.lines[sub.start].rstrip())
                scopes.append((label, sub.start + 1, sub.end))
    return sorted(scopes, key=lambda sc: sc[1])


def decision_entries(plan):
    """[(label, id, text)] for every entry: a `- **D<n>` bullet or a `### D<n>` heading, running to the next entry
    or heading (or the end of its scope)."""
    out = []
    for label, lo, hi in decision_scopes(plan):
        i = lo
        while i < hi:
            m = None if plan.mask[i] else ENTRY_RE.match(plan.lines[i])
            if not m:
                i += 1
                continue
            end = i + 1
            while end < hi and (plan.mask[end] or not (pl.HEAD_RE.match(plan.lines[end]) or ENTRY_RE.match(plan.lines[end]))):
                end += 1
            out.append((label, m.group(1).upper(), plan.block(i, end)))
            i = end
    return out


def decision_text(plan, did):
    if not DECISION_ID_RE.match(did):
        raise OrchError("--decision takes an id such as D2 (got %r)" % did, 1)
    did = did.upper()
    entries = decision_entries(plan)
    hits = ["%s\n%s" % (label, text) for label, eid, text in entries if eid == did]
    if not hits:
        ids = []
        for _, eid, _ in entries:
            if eid not in ids:
                ids.append(eid)
        raise OrchError("no decision %s in this plan (decisions: %s)" % (did, ", ".join(ids) or "none found"), 1)
    return "\n\n".join(hits)


def cmd_plan(ns, extra):
    c = load(ns)
    if ns.step is not None:
        text = step_text(c.plan, ns.step)
    elif ns.section is not None:
        text = section_text(c.plan, ns.section)
    elif ns.decision is not None:
        text = decision_text(c.plan, ns.decision)
    else:
        text = "\n".join(outline(c.plan))
    print(text)
    return 0


# -- orch diff -----------------------------------------------------------------------------------

def step_paths(c, sid):
    """The repo-relative paths of a step's `files` and `context` fields that lie inside the repo."""
    sid = sid.lower()
    step = c.plan.step(sid)
    if step is None:
        raise OrchError("unknown step %r (plan steps: %s)" % (sid, ", ".join(c.plan.ids()) or "none"), 1)
    root = c.repo_path if c.repo_path != "unknown" else None
    rels, outside = [], []
    for p in step.files(root):
        rel = pl.relative_to(p, root)
        if rel is None:
            outside.append(p)
        elif rel not in rels:
            rels.append(rel)
    for p in outside:
        warn_stderr("diff: skipped %s (outside the repo)" % p)
    if not rels:
        raise OrchError("step %s names no file in the repo: nothing to diff" % sid, 1)
    return rels


def cmd_diff(ns, extra):
    c = load(ns)
    wd = brief.work_dir(c)
    if wd is None:
        raise OrchError("no worktree or repo to diff in (worktree: %s, repo: %s)" % (c.wt_value, c.repo_path), 1)
    if ns.range is not None:
        left, op, _, sha = brief.check_range(c.repo_path, ns.range)
        rng = "%s%s%s" % (left, op, sha)
    else:
        for label, value in (("base_branch", c.base), ("task_branch", c.branch)):
            why = merge.bad_ref(value)
            if why:
                raise OrchError("%s %r is %s" % (label, value, why), 1)
        rng = "%s...%s" % (c.base, c.branch)
    paths = list(ns.paths)
    for sid in ns.step or []:
        for p in step_paths(c, sid):
            if p not in paths:
                paths.append(p)
    args = ["diff", "--no-ext-diff"]
    if ns.stat:
        args.append("--stat=200")
    elif ns.name_only:
        args.append("--name-only")
    args.append(rng)
    if paths:
        args.extend(["--"] + paths)
    r = merge.git(wd, *args)
    if not r.ok:
        raise OrchError("git diff failed: %s" % merge.first_lines(r, 1), 1)
    if not r.out.strip():
        print("(empty diff: %s)" % rng)
    else:
        sys.stdout.write(r.out if r.out.endswith("\n") else r.out + "\n")
    return 0


# -- registration --------------------------------------------------------------------------------

def register(add):
    sp = add("plan", cmd_plan, "a plan's outline, or one step, section or decision (read-only; no option: outline)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--step", default=None, metavar="ID", help="the step's binding text, with any revision that supersedes it")
    g.add_argument("--section", default=None, metavar="NAME", help="one `##` section by its title (first words suffice)")
    g.add_argument("--decision", default=None, metavar="D<n>", help="every entry for a decision id, with its section")

    sp = add("diff", cmd_diff, "git diff of the task branch (default base...branch), limited to --step files or PATHs (read-only)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--stat", action="store_true")
    g.add_argument("--name-only", action="store_true")
    sp.add_argument("--range", default=None, metavar="A..B", help="the range, checked as `orch brief review --range` checks it")
    sp.add_argument("--step", action="append", default=None, metavar="ID", help="the step's files (repeatable)")
    sp.add_argument("paths", nargs="*", metavar="PATH", help="pathspecs (after --)")
