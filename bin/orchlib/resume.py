"""resume: a read-only packet for a fresh orchestrator session (writes nothing, not even the lock file)."""
import glob
import os
import re
import subprocess
import time

from . import cli
from . import flow
from . import plan as pl
from . import questions as qs
from . import status as st
from .paths import OrchError, orch_root, resolve_task, task_names, tilde

BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
RECENT_SECONDS = 15 * 60
MAX_LINES = 40
# state -> what to do next (a suggestion; the session verifies it against the files first)
NEXT = {
    "planning": "plan.md missing or half-written: read log.md; no live planner -> `orch spawn planner` (tier from TIERS.md)",
    "awaiting_approval": "plan is written: summarise it for the user and ask at the plan gate; revisions go back to the planner",
    "implementing": "read the implementer row and notes.md; no live session -> `orch brief implement <next step>`, `orch spawn implementer`",
    "reviewing": "read review.md and review_round: no review yet -> `orch brief review`, spawn the reviewer; findings open -> fix round",
    "awaiting_merge": "check reviews and the final gate are current for the branch tip, then `orch merge`; ask only if it refuses",
    "paused_usage_limit": "arm `orch -t <task> usage wait --until-reset` in the background (RESET = exit 10), or check `orch usage`; "
                          "below the line -> `orch journal resume`, `orch set task_state=...`, continue the paused step",
    "done": "finished: nothing to resume (retro.md, `orch end` already run or not needed)",
    "abandoned": "finished: nothing to resume",
}


def find_task(arg):
    """Absolute dir, <repo>/<slug>, or a bare slug that exactly one repo has."""
    if arg and "/" not in arg and not arg.startswith("~"):
        if arg in ("", ".", ".."):
            raise OrchError("bad task %r" % arg, 1)
        hits = sorted(glob.glob(os.path.join(glob.escape(orch_root()), "*", glob.escape(arg), "status.md")))
        dirs = [os.path.dirname(h) for h in hits]
        if len(dirs) == 1:
            return dirs[0]
        if len(dirs) > 1:
            raise OrchError("slug %r is ambiguous, use <repo>/<slug>: %s"
                            % (arg, ", ".join("%s/%s" % task_names(d) for d in dirs)), 1)
        raise OrchError("no task with slug %r under %s" % (arg, orch_root()), 1)
    return resolve_task(arg)


def git(repo, *args):
    try:
        res = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=15, universal_newlines=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def rev(repo, name):
    if not BRANCH_RE.match(name or ""):
        return None
    return git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/%s^{commit}" % name) or \
        git(repo, "rev-parse", "--verify", "--quiet", "%s^{commit}" % name)


def branch_line(status):
    repo = st.clean_value("repo", status.get("repo"))
    branch = st.clean_value("task_branch", status.get("task_branch")).value
    base = st.clean_value("base_branch", status.get("base_branch")).value
    if branch in ("unknown", "none"):
        return "branch: no task branch recorded (task_branch: %s)" % branch
    if repo.value == "unknown" or repo.missing or not os.path.isdir(repo.value):
        return "branch: %s (repo not found, nothing checked)" % branch
    tip = rev(repo.value, branch)
    if not tip:
        return "branch: %s does not exist in the repo yet" % branch
    base_tip = rev(repo.value, base)
    if not base_tip:
        return "branch: %s tip %s (base %s not found)" % (branch, tip[:10], base)
    ahead = git(repo.value, "rev-list", "--count", "%s..%s" % (base_tip, tip))
    return "branch: %s tip %s, %s commit(s) ahead of %s" % (branch, tip[:10], ahead if ahead is not None else "?", base)


def plan_line(task_dir):
    for name in ("plan.md", "roadmap.md"):
        path = os.path.join(task_dir, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if name == "plan.md":
                parsed = pl.parse_plan(text)
                if parsed.legacy:
                    return "plan: %s (legacy format, no step blocks)" % path
                return "plan: %s (%d steps)" % (path, parsed.step_count())
            rows = len(re.findall(r"^\|\s*M?\d+\s*\|", text, re.M))
            return "plan: %s (roadmap, %d milestones)" % (path, rows)
    return "plan: none"


def last_activity(task_dir):
    times = []
    for name in ("status.md", "log.md", "journal.jsonl"):
        try:
            times.append(os.path.getmtime(os.path.join(task_dir, name)))
        except OSError:
            pass
    return max(times) if times else None


def age_text(seconds):
    m = int(max(0, seconds) // 60)
    if m < 90:
        return "%d min" % m
    if m < 60 * 48:
        return "%d h %d min" % (m // 60, m % 60)
    return "%d days" % (m // 1440)


def packet(task_dir):
    status = cli.load_status(task_dir)
    repo, slug = task_names(task_dir)
    text = st.read_text_like_index(cli.status_path(task_dir))
    state = st.classify(text, task_dir)
    raw = status.get("task_state")
    head = ["task: %s/%s (%s)" % (repo, slug, tilde(task_dir))]
    state_line = "state: %s" % state
    if raw and raw.strip() != state:
        state_line += " (status.md says: %s)" % cli.clip(raw.strip(), 40)
    head.append("%s  mode: %s  shape: %s  milestone: %s" % (
        state_line, status.get("mode") or "unknown", status.get("shape") or "unknown", cli.clip(status.get("milestone") or "unknown", 40)))
    for name, label in (("repo", "repo"), ("base_branch", "base"), ("worktree", "worktree")):
        head.append("%s: %s" % (label, st.show_value(name, status.get(name))))
    head.append(branch_line(status))
    head.append(plan_line(task_dir))
    roles = []
    try:
        for role, row in status.roles():
            roles.append("  %s | %s | %s | %s" % (role, row.get("state", ""), cli.clip(row.get("step", ""), 30), cli.clip(row.get("notes", ""), 60)))
    except OrchError as exc:
        roles = ["  unreadable (%s)" % str(exc).split(";")[0]]
    opens = qs.open_ones(task_dir)
    qlines = ["open questions: %d" % len(opens)]
    for q in sorted(opens, key=qs.sort_key)[:4]:
        qlines.append("  Q%d (blocks %s): %s" % (q.n, q.fields.get("blocks", "all"), cli.clip(q.text, 90)))
    logs = ["log (last 8):"] + ["  " + cli.clip(l, 140) for l in cli.last_log_lines(task_dir, 8)]
    tail = []
    act = last_activity(task_dir)
    if act is not None:
        age = flow.clock() - act
        line = "last activity: %s ago" % age_text(age)
        if age < RECENT_SECONDS:
            line += " - another session may be active"
        tail.append(line)
    tail.append("suggested next (verify): %s" % NEXT.get(state, "read status.md and log.md"))
    # fixed parts first; role rows, questions and log lines give way when the packet would pass the cap
    fixed = len(head) + len(tail) + 1
    room = MAX_LINES - fixed - len(qlines) - len(logs)
    if len(roles) > max(room, 3):
        roles = roles[:max(room, 3) - 1] + ["  … (%d more rows)" % (len(roles) - max(room, 3) + 1)]
    out = head + ["roles:"] + roles + qlines + logs + tail
    return out[:MAX_LINES]


def cmd_resume(ns, extra):
    arg = ns.task_arg or getattr(ns, "task_sub", None) or ns.task
    if not arg:
        raise OrchError("resume needs a task: <repo>/<slug>, a unique slug, or an absolute dir", 1)
    for line in packet(find_task(arg)):
        print(line)
    return 0


def register(add):
    sp = add("resume", cmd_resume, "read-only packet for a fresh orchestrator session")
    sp.add_argument("task_arg", nargs="?", default=None, metavar="TASK")
