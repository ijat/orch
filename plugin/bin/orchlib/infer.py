"""infer: which task a subagent verb (role, log, plan, diff, append, write, finding, findings) means, when `-t` is missing.

An explicit `-t` decides alone and never reaches this module. Otherwise two signals name a task and `ORCH_TASK` may
confirm one: the cwd (inside an active task's `worktree:`) and this session (equal to the task's `driver_session:`). A task
is chosen only when every signal that resolves agrees with it; plural or disagreeing signals refuse (exit 1) before
any lock or write. `ORCH_TASK` never breaks a tie and never overrides the cwd or the session; it decides alone only when
they give nothing.
"""
import glob
import os

from . import current
from . import status as st
from .paths import OrchError, resolve_task, task_names

SUBAGENT_VERBS = ("role", "log", "plan", "diff", "append", "write", "finding", "findings")
PASS_T = "pass -t <repo>/<slug> (your brief's title names it)"
NO_TASK = "no task: pass -t TASK (absolute dir or <repo>/<slug>)"


class Refusal(OrchError):
    """A refused inference (exit 1); `.text` is the message without any ORCH_TASK note."""

    def __init__(self, text, code=1):
        OrchError.__init__(self, text, code)
        self.text = text

    def also(self, note):
        return Refusal("%s; %s" % (self.text, note))


def label(task_dir):
    return "%s/%s" % task_names(task_dir)


def candidates(root):
    """[(task_dir, worktree, driver_session)] for every task whose state is not finished (as `orch show` lists them)."""
    out = []
    for sf in sorted(glob.glob(os.path.join(glob.escape(root), "*", "*", "status.md"))):
        d = os.path.dirname(sf)
        try:
            text = st.read_text_like_index(sf)
        except OSError:
            continue
        if st.classify(text, d) in st.INDEX_TERMINAL:
            continue
        out.append((d, current.field(text, "worktree"), current.field(text, st.DRIVER_FIELD)))
    return out


def infer_task(cwd, session_id, root):
    """-> (task_dir, source) with source cwd, cwd+session or session; raises Refusal when nothing, or no single task, fits."""
    real_cwd = os.path.realpath(cwd)
    by_cwd, by_sid = [], []
    for d, wt, drv in candidates(root):
        wt = os.path.expanduser(wt)
        if os.path.isabs(wt) and os.path.isdir(wt) and current.inside(real_cwd, os.path.realpath(wt)):
            by_cwd.append(d)
        if session_id and drv == session_id:
            by_sid.append(d)
    names = lambda ds: ", ".join(label(d) for d in ds)
    if len(by_cwd) > 1:
        raise Refusal("ambiguous task: the cwd is inside the worktrees of %s; %s" % (names(by_cwd), PASS_T))
    if by_cwd:
        x = by_cwd[0]
        if by_sid and x not in by_sid:
            raise Refusal("conflicting task: the cwd is inside the worktree of %s but this session drives %s; %s"
                          % (label(x), names(by_sid), PASS_T))
        return x, "cwd+session" if x in by_sid else "cwd"
    if len(by_sid) == 1:
        return by_sid[0], "session"
    if len(by_sid) > 1:
        raise Refusal("ambiguous task: this session drives %s; %s" % (names(by_sid), PASS_T))
    raise Refusal("%s; none inferred from the cwd or this session" % NO_TASK)


def _state_of(task_dir):
    path = os.path.join(task_dir, "status.md")
    try:
        return st.classify(st.read_text_like_index(path), task_dir)
    except OSError:
        return None


def resolve(cwd, session_id, env_task, root):
    """-> (task_dir, source): -t is not here; `env_task` is the ORCH_TASK value ('' when unset). source is cwd,
    cwd+session, session or ORCH_TASK (the last only when the cwd and the session give nothing)."""
    env = None
    if env_task:
        env = resolve_task(env_task, True)  # its own errors, exit 1
        state = _state_of(env)
        if state in st.INDEX_TERMINAL:
            raise Refusal("ORCH_TASK names %s (%s); unset it or pass -t" % (label(env), state))
    try:
        found, how = infer_task(cwd, session_id, root)
    except Refusal as exc:
        if env is None:
            raise
        if exc.text.startswith(NO_TASK):
            return env, "ORCH_TASK"  # the cwd and the session give nothing: ORCH_TASK decides alone
        raise exc.also("ORCH_TASK=%s does not settle it" % label(env))
    if env is not None and not current.same_dir(env, found):
        raise Refusal("conflicting task: ORCH_TASK names %s, the %s gives %s; %s"
                      % (label(env), "cwd" if how.startswith("cwd") else "session", label(found), PASS_T))
    return found, how
