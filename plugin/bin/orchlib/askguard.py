"""`orch hook ask`: a PreToolUse hook on the AskUserQuestion tool.

A pop-up question holds every notification (a subagent's finish notice included) until the user answers it, so the
orchestrator that asks one while a subagent is running stalls the task. This hook refuses AskUserQuestion in a session
that drives a task with an open spawn (a journal `spawn` of a role with no later `return`, less than a day old) and
names the way out: park the question with `orch ask`, or `orch return` a role that already finished.

Reads the PreToolUse JSON on stdin; prints the deny JSON or nothing (allow). Always exits 0 and fails open: a parse
error, no task for the session, no open spawn or any other surprise allows the call. ORCH_ASK_GUARD=0 turns it off.
Denials and errors go to `<orch data>/logs/ask-guard.jsonl`, never with the question text."""
import glob
import json
import os
import sys
import time

from . import current, flow
from . import status as st
from .paths import logs_dir, orch_root, task_names

TOOL = "AskUserQuestion"
MAX_AGE = 24 * 3600     # an older open spawn is a left-behind journal line, not a running subagent


def open_spawn(session_id, root, now):
    """-> (role, "<repo>/<slug>") of an open spawn in a live task this session drives, else None."""
    for sf in sorted(glob.glob(os.path.join(glob.escape(root), "*", "*", "status.md"))):
        try:
            text = st.read_text_like_index(sf)
        except OSError:
            continue
        if current.field(text, st.DRIVER_FIELD) != session_id:
            continue
        d = os.path.dirname(sf)
        if st.classify(text, d) in st.INDEX_TERMINAL:
            continue
        journal = flow.read_journal(d)
        for role, i in sorted(flow.open_spawns(journal).items(), key=lambda kv: -kv[1]):
            t = flow.ts_epoch(journal[i].get("ts"))
            if t is not None and now - t < MAX_AGE:
                return role, "%s/%s" % task_names(d)
    return None


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _log(code, payload, decision, extra=None):
    """One JSON line, never the question. Never raises."""
    try:
        import datetime
        root = logs_dir()
        os.makedirs(root, exist_ok=True)
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "decision": decision, "code": code, "session_id": payload.get("session_id") if isinstance(payload, dict) else None}
        if extra:
            rec.update(extra)
        with open(os.path.join(root, "ask-guard.jsonl"), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except Exception:
        pass


def decide(payload, env=None, now=None):
    """Returns the deny dict, or None to allow."""
    env = os.environ if env is None else env
    if not isinstance(payload, dict) or payload.get("tool_name") != TOOL:
        return None
    if str(env.get("ORCH_ASK_GUARD", "")).strip() == "0":
        return None
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return None
    try:
        found = open_spawn(sid.strip(), orch_root(), flow.clock() if now is None else now)
    except Exception as exc:
        _log("error", payload, "error", {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
        return None
    if not found:
        return None
    role, task = found
    _log("open-spawn", payload, "deny", {"role": role, "task": task})
    return _deny("orch: a subagent (%s, task %s) is running; a pop-up question blocks its finish notice until answered. "
                 "Park it with `orch ask` (answered in orch-pane), or `orch return %s` first if it already finished."
                 % (role, task, role))


def run(stdin=None, stdout=None):
    """The hook: always returns 0."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        out = decide(json.loads(stdin.read()))
        if out is not None:
            stdout.write(json.dumps(out) + "\n")
            stdout.flush()
    except BaseException:  # fail open
        pass
    return 0
