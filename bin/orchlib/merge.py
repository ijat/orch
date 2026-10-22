"""orch merge: the guarded `git merge --no-ff` of a task branch into its base, and the sha rules of `orch journal`.

Everything the guard reads is read-only git or a file under the task dir; no git write happens until every
condition holds (checked again under the task lock). All git calls are argv lists with `-C <path>` and pass
through git(), which refuses the forbidden arguments below. Python 3.9 compatible, standard library only."""
import json
import os
import re
import shlex
import subprocess
from collections import namedtuple

from . import cli
from . import flow
from . import journal as jr
from . import policy as pol
from . import status as st
from .paths import OrchError, TaskLock, refresh_index, task_names, warn_stderr, write_atomic, require_write_allowed, now

# -- git layer -----------------------------------------------------------------------------------

# forbidden-begin
FORBIDDEN = frozenset(("push", "--force", "-D", "reset", "checkout", "clean", "--ff-only"))
FORBIDDEN_PREFIX = "--force"            # also catches --force-with-lease and --force-if-includes
# forbidden-end
# Repo-location variables of the caller's environment must not redirect `-C <path>`.
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")

HEX_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ABBREV_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
UNSAFE_REF_RE = re.compile(r"[\s\x00-\x1f\x7f\\~^:?*\[]|@\{|^-")
SECURITY_QUIET_STATES = ("not_offered", "declined", "n/a")
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
MERGE_TIMEOUT = 300

Res = namedtuple("Res", "rc out err")
Res.ok = property(lambda self: self.rc == 0)
Res.text = property(lambda self: self.out.strip())


def _env():
    env = dict((k, v) for k, v in os.environ.items() if k not in GIT_ENV_DROP)
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_MERGE_AUTOEDIT": "no",
                "GIT_EDITOR": ":", "LC_ALL": "C"})
    return env


def _exec(argv, timeout):
    """Run one git command; (returncode, stdout, stderr). Tests wrap this to record and to inject failures."""
    try:
        res = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout, env=_env())
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out after %ds" % timeout
    except OSError as exc:
        return 127, "", str(exc)
    return res.returncode, res.stdout.decode("utf-8", "replace"), res.stderr.decode("utf-8", "replace")


def git(path, *args, **kw):
    for a in args:
        if not isinstance(a, str) or "\0" in a:
            raise OrchError("internal: bad git argument %r" % (a,), 3)
        if a in FORBIDDEN or a.startswith(FORBIDDEN_PREFIX):
            raise OrchError("refusing to run git with the argument %r" % a, 2)
    rc, out, err = _exec(["git", "-C", path] + list(args), kw.get("timeout", 30))
    return Res(rc, out, err)


def first_lines(res, n=3):
    text = (res.err.strip() or res.out.strip())
    return " / ".join(text.splitlines()[:n]) if text else "(no output; exit %d)" % res.rc


def real(path):
    return os.path.realpath(path)


def resolve_path(base, out):
    out = out.strip()
    return real(out if os.path.isabs(out) else os.path.join(base, out))


def commit_of(repo, name):
    """Full sha of a commit-ish, or None. `name` is a full ref (refs/heads/x) or a hex string."""
    r = git(repo, "rev-parse", "--verify", "--quiet", "%s^{commit}" % name)
    return r.text if r.ok and HEX_RE.match(r.text) else None


def branch_tip(repo, branch):
    return commit_of(repo, "refs/heads/%s" % branch)


def git_dir(path):
    r = git(path, "rev-parse", "--absolute-git-dir")
    return real(r.text) if r.ok and r.text else None


def head_ref(path):
    r = git(path, "symbolic-ref", "-q", "HEAD")
    return r.text if r.ok else None


def in_progress(path):
    gd = git_dir(path)
    if gd is None:
        return ["cannot find the git dir"]
    return [name for name in IN_PROGRESS if os.path.exists(os.path.join(gd, name))]


def tracked_changes(path):
    """None when clean, else a short description."""
    r = git(path, "status", "--porcelain", "--untracked-files=no")
    if not r.ok:
        return "git status failed: %s" % first_lines(r, 1)
    return "%d changed tracked path(s)" % len([l for l in r.out.splitlines() if l.strip()]) if r.out.strip() else None


def bad_ref(name):
    if not name or name in ("unknown", "none"):
        return "empty or unknown"
    if UNSAFE_REF_RE.search(name):
        return "not a safe branch name"
    return None


def check_ref(repo, name, what):
    why = bad_ref(name)
    if why:
        return "%s %r is %s" % (what, name, why)
    r = git(repo, "check-ref-format", "--branch", name)
    if not r.ok or r.text != name:
        return "%s %r is not a valid branch name" % (what, name)
    return None


# -- task data -----------------------------------------------------------------------------------

def norm_ms(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        m = re.match(r"^\s*M?(\d+)\s*$", v)
        return int(m.group(1)) if m else None
    return None


def read_journal_strict(task_dir):
    """([record dicts], [numbers of lines that are not a JSON object]). Fail closed: callers refuse on bad lines."""
    path = os.path.join(task_dir, "journal.jsonl")
    recs, bad = [], []
    try:
        with open(path, "rb") as fh:
            for i, raw in enumerate(fh, 1):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    bad.append(i)
                    continue
                if isinstance(rec, dict):
                    recs.append(rec)
                else:
                    bad.append(i)
    except OSError:
        pass
    return recs, bad


def role_states(status):
    """[(role, state)] straight from the role table (rows are read even when a note has a stray `|`),
    or None when the table can't be read."""
    try:
        lay = status.layout()
    except OrchError:
        return None
    out = []
    ri, si = lay.cols["role"], lay.cols["state"]
    for i in range(lay.sep + 1, lay.last + 1):
        c = status._cells(status.lines[i])
        if c and len(c[1]) > max(ri, si):
            out.append((c[1][ri].strip(), c[1][si].strip()))
    return out


def read_roadmap(task_dir):
    """(header cells lowercased, {milestone: [rows of cells]}, last milestone) or raises ValueError(reason)."""
    path = os.path.join(task_dir, "roadmap.md")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        raise ValueError("roadmap.md is missing or unreadable")

    def cells(line):
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    for i, line in enumerate(lines):
        if line.strip().startswith("|") and cells(line)[0].lower() == "m" and i + 1 < len(lines) \
                and re.match(r"^\s*\|?\s*:?-+", lines[i + 1]):
            header = [c.lower() for c in cells(line)]
            rows = {}
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                row = cells(lines[j])
                m = norm_ms(row[0])
                if m is not None:
                    rows.setdefault(m, []).append(row)
                j += 1
            if not rows:
                raise ValueError("roadmap.md milestone table has no rows")
            return header, rows, max(rows)
    raise ValueError("roadmap.md has no `| M | … |` milestone table")


def you_text(header, row):
    """The `you` cell of a roadmap row, widened to every cell it could have been shifted to by a stray `|`.
    None when the row is shorter than the header (cannot be read: refuse)."""
    if "you" not in header:
        return None
    idx = header.index("you")
    extra = len(row) - len(header)
    if extra < 0:
        return None
    return " ".join(row[idx:idx + 1 + extra])


def skip_in_plan(recs, ms):
    plans = [r for r in recs if flow.kind_of(r) == "milestone_plan"]
    if not plans or not isinstance(plans[-1].get("rows"), list):
        return False
    for row in plans[-1]["rows"]:
        if not isinstance(row, dict):
            continue
        low = dict((str(k).strip().lower(), v) for k, v in row.items())
        if norm_ms(low.get("m", low.get("milestone"))) == ms and str(low.get("review", "")).strip().lower() == "skip":
            return True
    return False


Ctx = namedtuple("Ctx", "task_dir status slug program ms repo base branch tip base_tip wt wt_note policy recs bad "
                        "roadmap roadmap_error")


def load(task_dir):
    """Resolve everything the guard needs. Raises OrchError(1) for an unusable repo or task branch, OrchError(2)
    for an unsafe ref name. Only read-only git."""
    status = cli.load_status(task_dir)
    repo_name, slug = task_names(task_dir)
    program, ms = cli.program_info(status)
    rc = st.clean_value("repo", status.get("repo"))
    if rc.value == "unknown" or not os.path.isdir(rc.value):
        raise OrchError("repo %r not found; fix `repo:` in status.md" % rc.value, 1)
    repo = rc.value
    top = git(repo, "rev-parse", "--show-toplevel")
    if not top.ok:
        raise OrchError("%s is not a git repository" % repo, 1)
    if real(top.text) != real(repo):
        raise OrchError("%s is not the top of a git repository (it is inside %s)" % (repo, top.text), 1)
    gd, cd = git(repo, "rev-parse", "--git-dir"), git(repo, "rev-parse", "--git-common-dir")
    if not (gd.ok and cd.ok) or resolve_path(repo, gd.text) != resolve_path(repo, cd.text):
        raise OrchError("%s is a linked worktree, not the main checkout" % repo, 1)
    base = st.clean_value("base_branch", status.get("base_branch")).value
    branch = st.clean_value("task_branch", status.get("task_branch")).value
    problems = []
    for name, what in ((base, "base_branch"), (branch, "task_branch")):
        why = check_ref(repo, name, what)
        if why:
            problems.append(why)
    if not problems and branch != "orch/%s" % slug:
        problems.append("task_branch %r is not orch/%s" % (branch, slug))
    if not problems and branch == base:
        problems.append("task_branch and base_branch are the same")
    if problems:
        raise OrchError("merge refused:\n  - " + "\n  - ".join(problems), 2)
    tip = branch_tip(repo, branch)
    if not tip:
        raise OrchError("task branch %s not found in %s" % (branch, repo), 1)
    base_tip = branch_tip(repo, base)
    if not base_tip:
        raise OrchError("base branch %s not found in %s" % (base, repo), 1)
    wc = st.clean_value("worktree", status.get("worktree"))
    wt, wt_note = None, None
    if wc.value not in ("unknown",) + st.WORKTREE_LITERALS:
        if not os.path.isabs(wc.value):
            wt_note = "worktree %r is not an absolute path; treated as none" % wc.value
        elif not os.path.isdir(wc.value):
            wt_note = "worktree %s does not exist; treated as none" % wc.value
        else:
            wt = wc.value
    roadmap, rerr = None, None
    if program:
        try:
            roadmap = read_roadmap(task_dir)
        except ValueError as exc:
            rerr = str(exc)
    recs, bad = read_journal_strict(task_dir)
    return Ctx(task_dir, status, slug, program, ms, repo, base, branch, tip, base_tip, wt, wt_note,
               pol.read_policy(task_dir), recs, bad, roadmap, rerr)


# -- conditions ----------------------------------------------------------------------------------

Cond = namedtuple("Cond", "n label ok detail")


def scoped(ctx, kind, strict=True, null_ok=False):
    """Journal lines of a kind in the merge's scope. Single plan: all. Program: milestone == current; with
    strict=False also lines whose milestone is null or unreadable (used for signals that must fail closed)."""
    out = []
    for r in ctx.recs:
        if flow.kind_of(r) != kind:
            continue
        if ctx.program:
            m = norm_ms(r.get("milestone"))
            if m != ctx.ms and not ((null_ok or not strict) and m is None):
                continue
        out.append(r)
    return out


def is_zero(v):
    return type(v) is int and v == 0


def brief_problem(ctx, r):
    """None when the review line's sha is the one in its round's review brief (the commit the reviewer was
    briefed on), else the reason. A line written around `orch journal` has no such guarantee, so the merge
    re-checks it here; a missing or unreadable brief fails."""
    rnd, sha = r.get("round"), r.get("sha")
    if type(rnd) is not int or rnd < 1:
        return "review line has no usable round, so its review brief cannot be found"
    reviewer = r.get("reviewer")
    path = brief_path(ctx.task_dir, reviewer, rnd, ctx.program, ctx.ms if ctx.program else None)
    try:
        pinned = brief_header(path, rnd, ctx.program, ctx.ms if ctx.program else None)
    except OrchError as exc:
        return "round %d: no valid review brief (%s)" % (rnd, str(exc).splitlines()[0])
    if not isinstance(sha, str) or sha.lower() != pinned:
        return "round %d: line sha %s differs from the review brief's reviewed sha %s" % (rnd, str(sha)[:9], pinned[:9])
    return None


def review_problems(r, tip, strict_nits, ctx=None):
    sha = r.get("sha")
    out = []
    if not isinstance(sha, str) or not HEX_RE.match(sha.lower()):
        out.append("review line has no full sha (old line or hand-written)")
    elif sha.lower() != tip:
        out.append("reviewed %s but the tip is %s" % (sha[:9], tip[:9]))
    elif ctx is not None:
        why = brief_problem(ctx, r)
        if why:
            out.append(why)
    if r.get("verdict") != "pass":
        out.append("verdict is %r" % (r.get("verdict"),))
    fields = ("blockers", "should_fix") + (("nits",) if strict_nits else ())
    for f in fields:
        if not is_zero(r.get(f)):
            out.append("%s is %r" % (f, r.get(f)))
    return out


def cond_policy(ctx):
    p = ctx.policy
    return Cond(1, "policy", p.merge == "auto",
                "merge is %r (%s)" % (p.merge, p.path if p.exists else "no policy.md: auto"))


def cond_state(ctx):
    state = (ctx.status.get("task_state") or "").strip()
    return Cond(2, "task_state", state in ("reviewing", "awaiting_merge"), "task_state is %r" % state)


def cond_gate(ctx):
    gates = scoped(ctx, "final_gate")
    if not gates:
        return Cond(3, "final gate", False, "no final_gate line%s" % (" for M%s" % ctx.ms if ctx.program else ""))
    g = gates[-1]
    sha = g.get("sha")
    bad = []
    if g.get("verdict") != "pass":
        bad.append("verdict is %r" % (g.get("verdict"),))
    if not isinstance(sha, str) or sha.lower() != ctx.tip:
        bad.append("gate ran on %s, the tip is %s" % (str(sha)[:9], ctx.tip[:9]))
    return Cond(3, "final gate", not bad, "; ".join(bad) or "pass at %s" % ctx.tip[:9])


def code_rows_skipped(ctx):
    rows = role_states(ctx.status)
    return rows is not None and any(r == "code-reviewer" and s == "skipped" for r, s in rows)


def cond_code_review(ctx):
    rounds = [r for r in scoped(ctx, "review_round") if r.get("reviewer") == "code"]
    if rounds:
        problems = review_problems(rounds[-1], ctx.tip, False, ctx)
        if not problems:
            return Cond(4, "code review", True, "round %s passed at %s" % (rounds[-1].get("round"), ctx.tip[:9]))
    else:
        problems = ["no code review_round line%s" % (" for M%s" % ctx.ms if ctx.program else "")]
    if ctx.program:
        if skip_in_plan(ctx.recs, ctx.ms):
            return Cond(4, "code review", True, "M%s row has review=skip (latest milestone_plan)" % ctx.ms)
    else:
        overrides = [r for r in ctx.recs if flow.kind_of(r) == "override"
                     and re.search("review", str(r.get("what")), re.I) and re.search("skip", str(r.get("what")), re.I)]
        if code_rows_skipped(ctx) and overrides:
            return Cond(4, "code review", True, "code-reviewer row is skipped and an override line records it")
        if code_rows_skipped(ctx):
            problems.append("code-reviewer row is skipped but no override line says review/skip")
    return Cond(4, "code review", False, "; ".join(problems))


def security_signals(ctx):
    sig = []
    if ctx.policy.security == "always":
        sig.append("policy security: always")
    for r in scoped(ctx, "auto_security", strict=False):
        if r.get("ran") not in (False, None, "false", "False"):
            sig.append("auto_security ran:%s" % json.dumps(r.get("ran")))
    for r in scoped(ctx, "security_offer", strict=False, null_ok=True):
        if str(r.get("answer")).strip().lower() == "accepted":
            sig.append("security_offer accepted")
    for r in scoped(ctx, "spawn", strict=False, null_ok=True):
        if str(r.get("role")).startswith("security-reviewer") or r.get("agent") == "orch-security-reviewer":
            sig.append("security reviewer spawned")
    rows = role_states(ctx.status)
    if rows is None:
        sig.append("role table unreadable")
    else:
        for role, state in rows:
            if role.startswith("security-reviewer") and state not in SECURITY_QUIET_STATES:
                sig.append("security-reviewer row is %r" % state)
    return sig


def cond_security(ctx):
    sig = security_signals(ctx)
    if not sig:
        return Cond(5, "security", True, "not due (no signal)")
    rounds = [r for r in scoped(ctx, "review_round") if r.get("reviewer") == "security"]
    if not rounds:
        return Cond(5, "security", False, "review due (%s) but no security review_round line" % ", ".join(sorted(set(sig))))
    problems = review_problems(rounds[-1], ctx.tip, True, ctx)
    return Cond(5, "security", not problems, "; ".join(problems) or "due (%s); round %s passed at %s"
                % (", ".join(sorted(set(sig))), rounds[-1].get("round"), ctx.tip[:9]))


def cond_you(ctx):
    if not ctx.program:
        return Cond(6, "roadmap stops", True, "single plan")
    if ctx.ms is None:
        return Cond(6, "roadmap stops", False, "program without a current milestone in status.md")
    if ctx.roadmap is None:
        return Cond(6, "roadmap stops", False, ctx.roadmap_error)
    header, rows, last = ctx.roadmap
    if ctx.ms not in rows:
        return Cond(6, "roadmap stops", False, "roadmap.md has no row for milestone %s" % ctx.ms)
    for row in rows[ctx.ms]:
        text = you_text(header, row)
        if text is None:
            return Cond(6, "roadmap stops", False, "roadmap row for M%s cannot be read (no `you` column or short row)" % ctx.ms)
        if re.search(r"\bmerge\b", text, re.I):
            return Cond(6, "roadmap stops", False, "the `you` cell of M%s names a merge stop: %r" % (ctx.ms, text))
    return Cond(6, "roadmap stops", True, "no merge stop on M%s" % ctx.ms)


def cond_recheck(ctx, recheck_ok):
    return Cond(7, "sensitive re-check", bool(recheck_ok), "--recheck-ok given" if recheck_ok else "pass --recheck-ok once you have re-checked the diff for sensitive areas")


def cond_checkouts(ctx):
    bad = []
    head = head_ref(ctx.repo)
    if head != "refs/heads/%s" % ctx.base:
        bad.append("main checkout is on %s, not %s" % (head or "a detached HEAD", ctx.base))
    dirty = tracked_changes(ctx.repo)
    if dirty:
        bad.append("main checkout has uncommitted changes (%s)" % dirty)
    prog = in_progress(ctx.repo)
    if prog:
        bad.append("a merge/rebase/pick is in progress in the main checkout (%s)" % ", ".join(prog))
    if ctx.wt:
        w = ctx.wt
        common_main = git(ctx.repo, "rev-parse", "--git-common-dir")
        common_wt = git(w, "rev-parse", "--git-common-dir")
        if not (common_main.ok and common_wt.ok) or resolve_path(ctx.repo, common_main.text) != resolve_path(w, common_wt.text):
            bad.append("worktree %s does not belong to this repository" % w)
        elif real(w) == real(ctx.repo):
            bad.append("the worktree is the main checkout")
        else:
            if head_ref(w) != "refs/heads/%s" % ctx.branch:
                bad.append("worktree is on %s, not %s" % (head_ref(w) or "a detached HEAD", ctx.branch))
            wd = tracked_changes(w)
            if wd:
                bad.append("worktree has uncommitted changes (%s)" % wd)
            wp = in_progress(w)
            if wp:
                bad.append("a merge/rebase/pick is in progress in the worktree (%s)" % ", ".join(wp))
    return Cond(8, "checkouts", not bad, "; ".join(bad) or "main on %s, clean%s" % (ctx.base, ", worktree clean" if ctx.wt else ""))


def cond_base(ctx):
    if ctx.tip == ctx.base_tip:
        return Cond(9, "base contained", False, "nothing to merge: the task branch is at the base tip")
    r = git(ctx.repo, "merge-base", "--is-ancestor", ctx.base_tip, ctx.tip)
    if r.rc == 0:
        return Cond(9, "base contained", True, "%s is in the gated tip" % ctx.base)
    if r.rc == 1:
        return Cond(9, "base contained", False, "base moved after the task branch was gated — ask the user")
    return Cond(9, "base contained", False, "merge-base failed: %s" % first_lines(r, 1))


def journal_cond(ctx):
    if ctx.bad:
        return Cond(0, "journal", False, "journal.jsonl has %d unreadable line(s) (first: line %d); fix it, then retry"
                    % (len(ctx.bad), ctx.bad[0]))
    return Cond(0, "journal", True, "readable")


def evaluate(ctx, recheck_ok):
    conds = [journal_cond(ctx), cond_policy(ctx), cond_state(ctx), cond_gate(ctx), cond_code_review(ctx),
             cond_security(ctx), cond_you(ctx), cond_recheck(ctx, recheck_ok), cond_checkouts(ctx), cond_base(ctx),
             Cond(10, "task branch", True, "%s at %s" % (ctx.branch, ctx.tip[:9]))]
    return conds


def table(conds):
    return ["  %-4s %2s %s: %s" % ("ok" if c.ok else "FAIL", c.n if c.n else "-", c.label, c.detail) for c in conds]


# -- the merge -----------------------------------------------------------------------------------

def revert_command(repo, sha):
    return "git -C %s revert -m 1 %s" % (shlex.quote(repo), sha)


def merge_failure(ctx, pre, res):
    """The merge exited non-zero: abort only if a merge is pending, then describe the state. Never reset."""
    gd = git_dir(ctx.repo)
    aborted = None
    if gd and os.path.exists(os.path.join(gd, "MERGE_HEAD")):
        ab = git(ctx.repo, "merge", "--abort")
        aborted = ab.ok
    head = git(ctx.repo, "rev-parse", "HEAD").text
    dirty = tracked_changes(ctx.repo)
    state = []
    if aborted is True:
        state.append("merge aborted")
    elif aborted is False:
        state.append("MERGE --ABORT FAILED: resolve the main checkout by hand")
    if head != pre:
        state.append("HEAD is %s, not %s as before: something else moved it" % (head[:9], pre[:9]))
    if dirty:
        state.append("main checkout is not clean (%s)" % dirty)
    if not state:
        state.append("repository unchanged")
    return OrchError("merge failed: %s [%s]" % (first_lines(res), "; ".join(state)), 2)


def do_backmerge(ctx, merge_sha, ms, warnings):
    """Catch the task branch up with base after a program merge point: a --no-ff back-merge of M by sha in the
    task worktree. Returns (K or None, tree_equal or None)."""
    wt = ctx.wt
    if not wt:
        warnings.append("no task worktree: the task branch was not caught up (backmerge: null); the next merge point will ask")
        return None, None
    if head_ref(wt) != "refs/heads/%s" % ctx.branch or git(wt, "rev-parse", "HEAD").text != ctx.tip:
        warnings.append("the worktree is not at the gated tip of %s any more: no back-merge (backmerge: null)" % ctx.branch)
        return None, None
    msg = "Back-merge %s after M%s merge point" % (ctx.base, ms)
    res = git(wt, "merge", "--no-ff", merge_sha, "-m", msg, timeout=MERGE_TIMEOUT)
    if not res.ok:
        gd = git_dir(wt)
        if gd and os.path.exists(os.path.join(gd, "MERGE_HEAD")):
            git(wt, "merge", "--abort")
        at = git(wt, "rev-parse", "HEAD").text
        warnings.append("back-merge failed (%s); %s; the merge into %s stands. backmerge: null — the next merge point will ask"
                        % (first_lines(res, 2), "task branch still at %s" % at[:9] if at == ctx.tip else "TASK BRANCH MOVED to %s" % at[:9], ctx.base))
        return None, None
    k = git(wt, "rev-parse", "HEAD").text
    same = git(wt, "diff", "--quiet", ctx.tip, k).rc == 0
    if not same:
        warnings.append("back-merge commit %s has a different tree than the reviewed tip %s: reviewed_through stays at the tip" % (k[:9], ctx.tip[:9]))
    return k, same


def do_cleanup(ctx, sha, warnings):
    """Remove the worktree and delete the branch (no --force, no -D). Returns True when everything went through."""
    ok = True
    removed = False
    if ctx.wt:
        r = git(ctx.repo, "worktree", "remove", ctx.wt)
        if r.ok:
            removed = True
        else:
            ok = False
            warnings.append("worktree not removed: %s" % first_lines(r, 2))
    r = git(ctx.repo, "branch", "-d", ctx.branch)
    if not r.ok:
        ok = False
        warnings.append("branch %s not deleted: %s" % (ctx.branch, first_lines(r, 2)))
    lst = git(ctx.repo, "worktree", "list", "--porcelain")
    if not lst.ok:
        ok = False
        warnings.append("could not list worktrees: %s" % first_lines(lst, 1))
    else:
        left = []
        for line in lst.out.splitlines():
            if line.startswith("worktree "):
                p = line[len("worktree "):].strip()
                if os.path.basename(p.rstrip("/")) == "orch-%s" % ctx.slug or (ctx.wt and real(p) == real(ctx.wt)):
                    left.append(p)
        if left:
            ok = False
            warnings.append("worktree still listed: %s" % ", ".join(left))
    return ok and (removed or not ctx.wt)


def append_journal(ctx, task_dir, kind, items, schema, ts):
    rec, _ = jr.build_record(schema, kind, jr.parse_pairs(items), ts, task_names(task_dir)[0], ctx.slug,
                             ctx.program, ctx.ms)
    jr.append(os.path.join(task_dir, "journal.jsonl"), jr.encode(rec))


def run_merge(ctx, task_dir):
    """Under the task lock, all conditions true. Returns (output lines, warnings)."""
    ts = now()
    schema = jr.load_schema()
    warnings = []
    if ctx.wt_note:
        warnings.append(ctx.wt_note)
    # Re-read the two tips right before the write: a move since the checks voids them.
    pre = git(ctx.repo, "rev-parse", "HEAD").text
    if pre != ctx.base_tip or branch_tip(ctx.repo, ctx.branch) != ctx.tip:
        raise OrchError("merge refused: %s or %s moved while the guard ran; nothing merged" % (ctx.base, ctx.branch), 2)
    res = git(ctx.repo, "merge", "--no-ff", "--no-edit", "-m", "Merge branch '%s'" % ctx.branch, ctx.tip,
              timeout=MERGE_TIMEOUT)
    if not res.ok:
        raise merge_failure(ctx, pre, res)
    sha = git(ctx.repo, "rev-parse", "HEAD").text
    parents = git(ctx.repo, "rev-list", "--parents", "-n", "1", "HEAD").text.split()
    contained = git(ctx.repo, "merge-base", "--is-ancestor", ctx.tip, "HEAD").ok
    revert = revert_command(ctx.repo, sha)
    try:
        spath = os.path.join(task_dir, "status.md")
        append_journal(ctx, task_dir, "auto_merge", ["sha=%s" % sha, "base=%s" % ctx.base, "branch=%s" % ctx.branch,
                                                     "revert=%s" % revert], schema, ts)
        if not (HEX_RE.match(sha) and parents == [sha, pre, ctx.tip] and contained):
            raise OrchError("the merge produced an unexpected commit %s (parents %s; wanted %s and %s); auto_merge is "
                            "journaled, no cleanup was done. Inspect it; undo with: %s"
                            % (sha[:9], ", ".join(p[:9] for p in parents[1:]), pre[:9], ctx.tip[:9], revert), 3)
        status = st.Status.load(spath)
        out = ["merged %s into %s (%s)" % (sha[:9], ctx.base, ctx.branch), "revert: %s" % revert]
        mid = ctx.program and ctx.roadmap is not None and ctx.ms is not None and ctx.ms < ctx.roadmap[2]
        if mid:
            k, same = do_backmerge(ctx, sha, ctx.ms, warnings)
            items = ["milestone:=%d" % ctx.ms, "sha=%s" % sha, "backmerge:=%s" % json.dumps(k), "tree_equal:=%s" % json.dumps(same)]
            append_journal(ctx, task_dir, "merge_point", items, schema, ts)
            status.set_field("merged_through", sha, add=True)
            if k and same:
                old = st.clean_value("reviewed_through", status.get("reviewed_through")).value.lower()
                if ABBREV_RE.match(old) and ctx.tip.startswith(old):
                    status.set_field("reviewed_through", k, add=True)
            out.append("merge point M%d: %s" % (ctx.ms, "task branch caught up at %s" % k[:9] if k else "task branch NOT caught up (backmerge: null)"))
        else:
            clean = do_cleanup(ctx, sha, warnings)
            if clean and ctx.wt:
                status.set_field("worktree", "removed after merge (%s)" % sha[:7])
            out.append("cleanup: %s" % ("worktree removed, branch deleted" if clean else "incomplete (see warnings)"))
        if status.dirty:
            write_atomic(spath, status.render())
        return out, warnings
    except Exception as exc:
        if isinstance(exc, OrchError) and exc.code == 3:
            raise
        raise OrchError("the merge into %s was made (%s) but bookkeeping failed (%s: %s); the state may be incomplete. "
                        "Undo with: %s" % (ctx.base, sha[:9], type(exc).__name__, exc, revert), 3)


def cmd_merge(ns, extra):
    task_dir = cli.get_task(ns)
    if ns.check:
        ctx = load(task_dir)
        conds = evaluate(ctx, ns.recheck_ok)
        print("merge check for %s/%s: %s -> %s (tip %s)" % (task_names(task_dir) + (ctx.branch, ctx.base, ctx.tip[:9])))
        for line in table(conds):
            print(line)
        if ctx.wt_note:
            warn_stderr(ctx.wt_note)
        return 0 if all(c.ok for c in conds) else 2
    require_write_allowed()
    with TaskLock(task_dir):
        ctx = load(task_dir)
        conds = evaluate(ctx, ns.recheck_ok)
        failed = [c for c in conds if not c.ok]
        if failed:
            raise OrchError("merge refused (%d condition%s):\n%s" % (len(failed), "s" if len(failed) != 1 else "", "\n".join(table(failed))), 2)
        out, warnings = run_merge(ctx, task_dir)
    refresh_index(warn_stderr)
    for line in out:
        print(line)
    for w in warnings:
        warn_stderr(w)
    return 0


def register(add):
    sp = add("merge", cmd_merge, "guarded merge of the task branch into base (--check: conditions only)")
    sp.add_argument("--check", action="store_true")
    sp.add_argument("--recheck-ok", dest="recheck_ok", action="store_true")


# -- sha rules for `orch journal` ----------------------------------------------------------------

def brief_path(task_dir, reviewer, rnd, program, ms):
    name = "review-%s-m%02d-r%d.md" % (reviewer, ms, rnd) if program else "review-%s-r%d.md" % (reviewer, rnd)
    return os.path.join(task_dir, "briefs", name)


def brief_header(path, rnd, program, ms):
    """The `- reviewed sha:` of a review brief; the `- round:` and `- milestone:` lines, when present, must agree."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(60)]
    except OSError:
        raise OrchError("no review brief %s; run `orch brief review` before journaling the round" % path, 1)
    shas = [m.group(1) for m in (re.match(r"^- reviewed sha:\s*(\S+)\s*$", l) for l in head) if m]
    if len(shas) != 1:
        raise OrchError("%s must have exactly one `- reviewed sha:` header line (found %d)" % (path, len(shas)), 1)
    if not HEX_RE.match(shas[0].lower()):
        raise OrchError("%s: the reviewed sha %r is not a full commit sha" % (path, shas[0]), 1)
    for key, want in (("round", rnd), ("milestone", ms if program else None)):
        for l in head:
            m = re.match(r"^- %s:\s*M?(\d+)\s*$" % key, l)
            if m and want is not None and int(m.group(1)) != want:
                raise OrchError("%s: header `%s` is %s, expected %s" % (path, key, m.group(1), want), 1)
    return shas[0].lower()


def repo_of(status):
    rc = st.clean_value("repo", status.get("repo"))
    if rc.value == "unknown" or not os.path.isdir(rc.value):
        raise OrchError("repo %r not found; the sha cannot be verified (fix `repo:` in status.md)" % rc.value, 1)
    top = git(rc.value, "rev-parse", "--show-toplevel")
    if not top.ok:
        raise OrchError("%s is not a git repository" % rc.value, 1)
    return rc.value


def bind_sha(task_dir, status, kind, pairs, program, auto_ms):
    """For final_gate and review_round: return pairs whose `sha` is a verified full commit sha (C1).
    final_gate needs sha=; review_round takes it from the round's review brief. Other kinds pass through."""
    if kind not in ("final_gate", "review_round"):
        return pairs
    if status is None:
        raise OrchError("no status.md in %s (needed to find the repo)" % task_dir, 1)
    given = None
    rest = []
    for key, mode, raw in pairs:
        if key != "sha":
            rest.append((key, mode, raw))
        elif mode == "=":
            given = raw
        else:
            try:
                value = json.loads(raw)
            except ValueError:
                raise OrchError("sha:=%s is not valid JSON" % raw, 1)
            if value is not None and not isinstance(value, str):
                raise OrchError("sha must be a commit sha string", 1)
            given = value
    repo = repo_of(status)

    def full(raw):
        raw = raw.strip()
        if not ABBREV_RE.match(raw):
            raise OrchError("sha %r is not a hex commit sha" % raw, 1)
        found = commit_of(repo, raw.lower())
        if not found:
            raise OrchError("sha %s is not a commit of %s" % (raw, repo), 1)
        return found

    if kind == "final_gate":
        if given is None:
            raise OrchError("final_gate needs sha=<the commit the gate ran on>", 1)
        return rest + [("sha", "=", full(given))]
    values = dict((k, (m, raw)) for k, m, raw in pairs)
    reviewer = values.get("reviewer", (None, None))[1]
    if reviewer not in ("code", "security"):
        raise OrchError("review_round needs reviewer=code or reviewer=security", 1)
    if "round" not in values:
        raise OrchError("review_round needs round=<n> (it selects the review brief)", 1)
    rnd = jr.coerce(jr.load_schema(), "review_round", "round", *values["round"])
    if not isinstance(rnd, int) or isinstance(rnd, bool) or rnd < 1:
        raise OrchError("review_round round must be a positive integer", 1)
    ms = None
    if program:
        ms = jr.coerce(jr.load_schema(), "review_round", "milestone", *values["milestone"]) if "milestone" in values else auto_ms
        if not isinstance(ms, int) or isinstance(ms, bool):
            raise OrchError("review_round in a program needs milestone:=<n> (none resolvable from status.md)", 1)
    path = brief_path(task_dir, reviewer, rnd, program, ms)
    sha = brief_header(path, rnd, program, ms)
    if not commit_of(repo, sha):
        raise OrchError("%s: the reviewed sha %s is not a commit of %s" % (path, sha, repo), 1)
    if given is not None and full(given) != sha:
        raise OrchError("sha=%s differs from the review brief's reviewed sha %s (%s)" % (given, sha[:9], path), 1)
    return rest + [("sha", "=", sha)]
