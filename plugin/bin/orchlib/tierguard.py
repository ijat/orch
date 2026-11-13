"""`orch hook tier`: a PreToolUse hook on the Agent tool that keeps the cheap roles cheap.

Reads the PreToolUse JSON on stdin; prints the deny JSON or nothing (allow). Always exits 0 and fails open: a
parse error or any other surprise allows the call.

Guarded roles: orch:explorer, orch:executor, orch:graph. They run on haiku at effort low (sonnet is allowed only
at effort low: the Haiku-ban cap). A spawn is denied when its `model` or `effort` is above that, or when the
session env would put the role on another model (CLAUDE_CODE_SUBAGENT_MODEL, CLAUDE_CODE_SUBAGENT_MODEL_FORCE).
ORCH_TIER_GUARD=0 in the env turns the guard off. Denials are appended to `<orch data>/logs/tier-guard.jsonl`,
never with the prompt text."""
import json
import os
import sys

from .paths import agent_key, logs_dir

GUARDED = ("orch-explorer", "orch-executor", "orch-graph")
AGENT_TOOLS = ("Agent", "Task")
FIX = 'model: "haiku", effort: "low"'
FALSE_WORDS = ("", "0", "false", "no", "off")


def _family(model):
    """haiku / sonnet / other, from an alias or a full model ID, case-insensitively."""
    m = model.strip().lower()
    for fam in ("haiku", "sonnet"):
        if fam in m:
            return fam
    return "other"


def _truthy(value):
    return isinstance(value, str) and value.strip().lower() not in FALSE_WORDS


def _verdict(role, tool_input, env):
    """Returns (code, reason) for a denied spawn, or None."""
    model = tool_input.get("model")
    effort = tool_input.get("effort")
    if model is not None and not isinstance(model, str):
        return None
    if effort is not None and not isinstance(effort, str):
        return None
    fam = _family(model) if model is not None else None
    eff = effort.strip().lower() if effort is not None else None
    ask = "model %s, effort %s" % (repr(model) if model is not None else "(not set)",
                                   repr(effort) if effort is not None else "(not set)")
    if _truthy(env.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE")):
        return ("env-force", "orch tier guard: %s runs on haiku at low effort, but CLAUDE_CODE_SUBAGENT_MODEL_FORCE is set, "
                "which would run it on another model whatever the call says. Do the lookup yourself, or ask the "
                "user to unset the variable (or set ORCH_TIER_GUARD=0 to allow it)." % role)
    env_model = env.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if model is None and isinstance(env_model, str) and env_model.strip() and _family(env_model) != "haiku":
        return ("env-model", "orch tier guard: %s runs on haiku at low effort, but CLAUDE_CODE_SUBAGENT_MODEL=%s would run "
                "it on that model. Pass %s, or do the lookup yourself, or ask the user to unset the variable "
                "(or set ORCH_TIER_GUARD=0 to allow it)." % (role, env_model.strip(), FIX))
    if fam == "other":
        return ("model", "orch tier guard: %s runs on haiku at low effort; this call asked for %s. Respawn with %s."
                % (role, ask, FIX))
    if fam == "sonnet" and eff != "low":
        return ("sonnet-effort", "orch tier guard: %s may run on sonnet only at effort low; this call asked for %s. "
                "Respawn with %s." % (role, ask, FIX))
    if eff is not None and eff != "low":
        return ("effort", "orch tier guard: %s runs at effort low; this call asked for %s. Respawn with %s."
                % (role, ask, FIX))
    return None


def _log(code, payload, role, tool_input):
    """One JSON line, never the prompt. Never raises."""
    try:
        import datetime
        root = logs_dir()
        os.makedirs(root, exist_ok=True)
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "decision": "deny", "code": code, "role": role, "model": tool_input.get("model"),
               "effort": tool_input.get("effort"), "caller": payload.get("agent_type"),
               "session_id": payload.get("session_id")}
        with open(os.path.join(root, "tier-guard.jsonl"), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except Exception:
        pass


def decide(payload, env=None):
    """Returns the deny dict, or None to allow."""
    env = os.environ if env is None else env
    if not isinstance(payload, dict) or payload.get("tool_name") not in AGENT_TOOLS:
        return None
    if str(env.get("ORCH_TIER_GUARD", "")).strip() == "0":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    sub = tool_input.get("subagent_type")
    if agent_key(sub) not in GUARDED:
        return None
    role = "orch:" + agent_key(sub)[len("orch-"):]
    res = _verdict(role, tool_input, env)
    if res is None:
        return None
    code, reason = res
    _log(code, payload, role, tool_input)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


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
