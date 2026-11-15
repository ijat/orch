"""`orch hook tier`: a PreToolUse hook on the Agent tool with two checks.

Reads the PreToolUse JSON on stdin; prints the deny JSON or nothing (allow). Always exits 0 and fails open: a
parse error or any other surprise allows the call.

1. Cheap roles: orch:explorer, orch:executor, orch:graph run on haiku at effort low (sonnet is allowed only at
   effort low: the Haiku-ban cap). A spawn is denied when its `model` or `effort` is above that, or when the
   session env would put the role on another model (CLAUDE_CODE_SUBAGENT_MODEL, CLAUDE_CODE_SUBAGENT_MODEL_FORCE).
2. Escalation: every other orch role with a default tier (tiers.py). A spawn above its default needs a `basis:`
   first line in the prompt with a recorded, allowed reason (tiers.py checks the form, the token against the role
   and tier, and for a `user` basis finds the user's quote in the session transcript). A spawn at or below the
   default is never refused or delayed. A guard bug (an exception) allows the spawn and logs `decision: error`.

A `subagent_type` is read the way Claude Code resolves it when no agent matches exactly (NFKC, lowercase, white space,
dashes and `_` dropped): `orch:Planner` or `orch:plan_ner` run the real role, so they get that role's check, and an
`orch:` name no role has is refused.

ORCH_TIER_GUARD=0 in the env turns both off. Denials, and every decision on an above-default spawn, are appended
to `<orch data>/logs/tier-guard.jsonl`, never with the prompt text (the basis line, clipped, is the only part of it)."""
import json
import os
import sys

from . import tiers
from .paths import logs_dir

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
                "Respawn with effort: \"low\" (or %s)." % (role, ask, FIX))
    if eff is not None and eff != "low":
        return ("effort", "orch tier guard: %s runs at effort low; this call asked for %s. Respawn with %s."
                % (role, ask, FIX))
    return None


def _log(code, payload, role, tool_input, decision="deny", extra=None):
    """One JSON line, never the prompt. Never raises."""
    try:
        import datetime
        root = logs_dir()
        os.makedirs(root, exist_ok=True)
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "decision": decision, "code": code, "role": role, "model": tool_input.get("model"),
               "effort": tool_input.get("effort"), "caller": payload.get("agent_type"),
               "session_id": payload.get("session_id")}
        if extra:
            rec.update(extra)
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
    key = tiers.resolve_role(sub)
    if key is None:
        if tiers.is_orch_name(sub):     # `orch:` plus a name no role has: refused, not skipped
            _log("orch-name", payload, str(sub)[:60], tool_input, "deny", {"subagent_type": str(sub)[:60]})
            return _deny("orch tier guard: %r is not an orch role. Use the exact name orch:<role> "
                         "(planner, implementer, code-reviewer, security-reviewer, security-reviewer-mythos, "
                         "plan-reviewer, director, init, explorer, executor, graph)." % str(sub)[:60])
        return None
    # Claude Code runs the role a loosely spelled name resolves to (case, white space, `-`, `_` ignored), so the
    # check runs for that role; the raw name is logged when it was not the exact one.
    seen = {"subagent_type": sub[:60]} if sub != "orch:" + key[len("orch-"):] else None
    if key in GUARDED:
        role = "orch:" + key[len("orch-"):]
        res = _verdict(role, tool_input, env)
        if res is None:
            return None
        code, reason = res
        _log(code, payload, role, tool_input, extra=seen)
        return _deny(reason)
    return _escalation(payload, tool_input, key, seen)


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _escalation(payload, tool_input, key, seen=None):
    """The basis check for a spawn of a role with a default tier. Any exception allows the spawn and is logged."""
    role = "orch:" + key[len("orch-"):]
    try:
        verdict = tiers.check_spawn(dict(tool_input, subagent_type=role), payload.get("transcript_path"))
        if verdict.code == "default":
            return None
        line = tiers.basis_line(tool_input.get("prompt"))
        extra = {"basis": line[:tiers.BASIS_CLIP] if line else None, "found": verdict.found}
        if seen:
            extra.update(seen)
        try:
            parsed = tiers.parse_basis(tool_input.get("prompt"))
            extra["basis_kind"] = parsed.kind if parsed else None
        except tiers.BasisError:
            extra["basis_kind"] = None
        _log(verdict.code, payload, role, tool_input, "allow" if verdict.allow else "deny", extra)
        return None if verdict.allow else _deny(verdict.reason)
    except Exception as exc:
        _log("error", payload, role, tool_input, "error", {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
        return None


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
