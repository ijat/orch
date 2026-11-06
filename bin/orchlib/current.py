"""current: which task this directory belongs to (read-only), for a bare "resume" or "continue" prompt."""
import datetime
import os
import re

from . import cli
from . import resume
from . import status as st
from .paths import orch_root, task_names, tilde

STALE_DAYS = 7  # the same line as ACTIVE.md's STALE>7d
MAX_OTHERS = 20


def git_root(cwd):
    """The main checkout's root, also from a linked worktree; None outside git."""
    common = resume.git(cwd, "rev-parse", "--git-common-dir")
    if not common:
        return None
    common = os.path.realpath(os.path.join(cwd, common))
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    top = resume.git(cwd, "rev-parse", "--show-toplevel")  # a submodule: its git dir sits under the parent's
    return os.path.realpath(top) if top else None


def inside(path, parent):
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def same_dir(a, b):
    try:
        return os.path.samefile(a, b)  # also right on a case-insensitive disk
    except OSError:
        return False


def field(text, name):
    m = re.search(r"^- %s:[ \t]*(.*)$" % name, text, re.M)
    return re.sub(r"\s*\(.*$", "", m.group(1).strip()) if m else ""


def match(cwd, root, task_dir, text):
    """How this task belongs here: worktree, repo, name (its repo path is gone, only the name agrees) or None."""
    wt = os.path.expanduser(field(text, "worktree"))
    if os.path.isabs(wt) and os.path.isdir(wt) and inside(cwd, os.path.realpath(wt)):
        return "worktree"
    repo = os.path.expanduser(field(text, "repo"))
    if os.path.isabs(repo) and os.path.isdir(repo):
        if root:
            return "repo" if same_dir(repo, root) else None
        return "repo" if inside(cwd, os.path.realpath(repo)) else None
    return "name" if task_names(task_dir)[0] == os.path.basename(root or cwd) else None


def age_seconds(task_dir):
    paths = [os.path.join(task_dir, n) for n in ("status.md", "log.md")]
    return datetime.datetime.now().timestamp() - max(os.path.getmtime(p) for p in paths if os.path.exists(p))


def decide(hits):
    """(action, task). Only a live, certain, single task is taken over; anything less goes to the user."""
    if not hits:
        return "none", None
    best = [h for h in hits if h["match"] == "worktree"] or hits
    if any(h["busy"] for h in best):  # two orchestrators on one task is worse than one question
        return "ask", None
    sure = [h for h in best if not h["stale"] and h["match"] != "name" and h["state"] != "unknown"]
    return ("resume", sure[0]["task"]) if len(sure) == 1 else ("ask", None)


def row(h):
    tags = [t for t, on in ((h["match"], h["match"]), ("stale", h["stale"]), ("another session may be active", h["busy"])) if on]
    opens = (", %d open question(s)" % h["questions"]) if h["questions"] else ""
    head = "  %s  %s  updated %s%s" % (h["task"], h["state"], h["updated"], opens)
    return head + ("  [%s]" % ", ".join(tags) if tags else "")


def cmd_current(ns, extra):
    cwd = os.path.realpath(os.getcwd())
    root = git_root(cwd)
    hits, others = [], []
    for repo, slug, state, _, updated, _, last, task_dir in cli.active_tasks(orch_root()):
        text = st.read_text_like_index(cli.status_path(task_dir))
        how = match(cwd, root, task_dir, text)
        age = age_seconds(task_dir)
        item = {"task": "%s/%s" % (repo, slug), "state": state, "updated": updated, "match": how, "last": last,
                "stale": age / 86400 > STALE_DAYS, "busy": age < resume.RECENT_SECONDS,
                "questions": cli.open_questions(task_dir)}
        (hits if how else others).append(item)
    hits.sort(key=lambda h: h["updated"], reverse=True)
    action, task = decide(hits)
    print("here: %s" % tilde(root or cwd))
    print("action: %s" % ("resume %s" % task if task else action))
    if hits:
        print("tasks here:")
        for h in hits:
            print(row(h))
            print("    last: %s" % cli.clip(h["last"], 120))
    if task:
        print("next: `orch resume %s`, then continue from it" % task)
    elif action == "ask":
        print("next: ask the user which task; none of these is certain enough to take over")
    else:
        print("no active task for this directory")
        fresh = sorted((h for h in others if not h["stale"]), key=lambda h: h["updated"], reverse=True)
        stale = sorted((h for h in others if h["stale"]), key=lambda h: h["updated"], reverse=True)
        shown = (fresh + stale)[:MAX_OTHERS]
        if shown:
            print("other repos (%d):" % len(others))
            for h in shown:
                print(row(h))
    return 0


def register(add):
    add("current", cmd_current, "the active task of this directory, and whether to resume it (read-only)")
