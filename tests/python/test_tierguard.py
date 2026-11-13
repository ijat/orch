"""orch hook tier: the PreToolUse hook that keeps orch:explorer, orch:executor and orch:graph on haiku/low.

decide() is called with explicit env dicts; the log goes to a per-test ORCH_DATA; the CLI path runs the real
launcher with a scrubbed env."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin")
sys.path.insert(0, BIN)

from orchlib import tierguard  # noqa: E402


def payload(sub="orch:explorer", tool="Agent", **tool_input):
    if sub is not None:
        tool_input["subagent_type"] = sub
    return {"tool_name": tool, "tool_input": tool_input, "session_id": "s1", "prompt": "SECRET_PROMPT"}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-tier-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = os.environ.get("ORCH_DATA")
        os.environ["ORCH_DATA"] = self.tmp

        def restore():
            if saved is None:
                os.environ.pop("ORCH_DATA", None)
            else:
                os.environ["ORCH_DATA"] = saved
        self.addCleanup(restore)

    def reason(self, p, env=None):
        out = tierguard.decide(p, env or {})
        if out is None:
            return None
        h = out["hookSpecificOutput"]
        self.assertEqual(h["hookEventName"], "PreToolUse")
        self.assertEqual(h["permissionDecision"], "deny")
        return h["permissionDecisionReason"]

    def allowed(self, p, env=None):
        self.assertIsNone(tierguard.decide(p, env or {}))

    def denied(self, p, env=None):
        r = self.reason(p, env)
        self.assertIsNotNone(r)
        return r


class Allowed(Case):
    def test_rows(self):
        self.allowed(payload())
        self.allowed(payload(model="haiku"))
        self.allowed(payload(model="haiku", effort="low"))
        self.allowed(payload(model="Haiku", effort="LOW"))
        self.allowed(payload(model="claude-haiku-4-5", effort="low"))
        self.allowed(payload(model="sonnet", effort="low"))
        self.allowed(payload(effort="low"))

    def test_every_guarded_role(self):
        for role in ("orch:explorer", "orch:executor", "orch:graph"):
            self.allowed(payload(role, model="haiku", effort="low"))
            self.denied(payload(role, model="opus"))

    def test_task_tool_name(self):
        self.allowed(payload(tool="Task", model="haiku"))
        self.denied(payload(tool="Task", model="opus"))

    def test_env_haiku_is_fine(self):
        self.allowed(payload(), {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"})
        self.allowed(payload(), {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-4-5"})


class Denied(Case):
    def test_models(self):
        for m in ("opus", "Opus", "fable", "claude-opus-4-1", "claude-opus-4-1-20250805", "inherit", "gpt-9"):
            r = self.denied(payload(model=m, effort="low"))
            self.assertIn("orch:explorer", r)
            self.assertIn(m, r)
            self.assertIn('model: "haiku", effort: "low"', r)

    def test_sonnet_needs_low(self):
        self.denied(payload(model="sonnet"))
        self.denied(payload(model="sonnet", effort="high"))
        self.denied(payload(model="claude-sonnet-4-5", effort="medium"))

    def test_effort_above_low(self):
        for e in ("medium", "high", "xhigh", "max", "HIGH"):
            r = self.denied(payload(model="haiku", effort=e))
            self.assertIn(e, r)
        self.denied(payload(effort="high"))

    def test_env_model_when_spawn_omits_model(self):
        r = self.denied(payload(), {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"})
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL=sonnet", r)
        self.denied(payload(effort="low"), {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-4-1"})

    def test_env_model_ignored_when_spawn_sets_model(self):
        self.allowed(payload(model="haiku"), {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"})

    def test_env_force(self):
        for v in ("1", "true", "yes"):
            r = self.denied(payload(model="haiku", effort="low"), {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": v})
            self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", r)
        for v in ("", "0", "false"):
            self.allowed(payload(model="haiku"), {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": v})

    def test_denial_is_logged_without_the_prompt(self):
        self.denied(payload(model="opus"))
        with open(os.path.join(self.tmp, "logs", "tier-guard.jsonl")) as f:
            text = f.read()
        rec = json.loads(text.splitlines()[0])
        self.assertEqual((rec["decision"], rec["role"], rec["model"], rec["code"]),
                         ("deny", "orch:explorer", "opus", "model"))
        self.assertNotIn("SECRET_PROMPT", text)

    def test_allow_is_not_logged(self):
        self.allowed(payload(model="haiku"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "logs", "tier-guard.jsonl")))


class OptOut(Case):
    def test_off_allows_everything(self):
        env = {"ORCH_TIER_GUARD": "0", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "CLAUDE_CODE_SUBAGENT_MODEL": "opus"}
        self.allowed(payload(model="opus", effort="max"), env)

    def test_other_values_do_not_disable(self):
        self.denied(payload(model="opus"), {"ORCH_TIER_GUARD": "1"})
        self.denied(payload(model="opus"), {"ORCH_TIER_GUARD": ""})


class NotGuarded(Case):
    def test_other_subagents(self):
        self.allowed(payload("general-purpose", model="opus", effort="max"))
        self.allowed(payload("orch:implementer", model="opus", effort="max"))
        self.allowed(payload("orch:planner", model="opus", effort="max"))
        self.allowed(payload("foo:explorer", model="opus"))
        self.allowed(payload(None, model="opus"))

    def test_other_tools(self):
        self.allowed(payload(tool="Bash", model="opus"))
        self.allowed({"tool_name": "Read", "tool_input": {"file_path": "/x"}})

    def test_old_role_names(self):
        self.denied(payload("orch-explorer", model="opus"))
        self.denied(payload("orch-executor", effort="high"))
        self.allowed(payload("orch-graph", model="haiku", effort="low"))

    def test_nested_call_payload(self):
        p = payload(model="opus")
        p.update({"agent_id": "a1", "agent_type": "orch:implementer"})
        self.denied(p)


class FailOpen(Case):
    def run_hook(self, text):
        out = io.StringIO()
        rc = tierguard.run(io.StringIO(text), out)
        return rc, out.getvalue()

    def test_malformed(self):
        for text in ("", "not json", "[]", "null", '{"tool_name": "Agent"}',
                     '{"tool_name": "Agent", "tool_input": "x"}',
                     '{"tool_name": "Agent", "tool_input": {"subagent_type": "orch:explorer", "model": 5}}',
                     '{"tool_name": "Agent", "tool_input": {"subagent_type": ["a"]}}'):
            self.assertEqual(self.run_hook(text), (0, ""), text)

    def test_deny_through_run(self):
        saved = {k: os.environ.pop(k, None) for k in ("ORCH_TIER_GUARD", "CLAUDE_CODE_SUBAGENT_MODEL",
                                                       "CLAUDE_CODE_SUBAGENT_MODEL_FORCE")}
        self.addCleanup(lambda: os.environ.update({k: v for k, v in saved.items() if v is not None}))
        rc, out = self.run_hook(json.dumps(payload(model="opus")))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")


class Launcher(Case):
    def run_cli(self, text, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("ORCH_", "CLAUDE_CODE_SUBAGENT"))}
        base.update(ORCH_DATA=self.tmp, **env)
        return subprocess.run([sys.executable, os.path.join(BIN, "orch"), "hook", "tier"], input=text,
                              capture_output=True, text=True, env=base)

    def test_deny_and_allow(self):
        res = self.run_cli(json.dumps(payload(model="opus")))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        res = self.run_cli(json.dumps(payload(model="haiku", effort="low")))
        self.assertEqual((res.returncode, res.stdout), (0, ""))
        res = self.run_cli(json.dumps(payload(model="opus")), ORCH_TIER_GUARD="0")
        self.assertEqual((res.returncode, res.stdout), (0, ""))

    def test_malformed_stdin(self):
        res = self.run_cli("garbage")
        self.assertEqual((res.returncode, res.stdout), (0, ""))

    def test_hooks_json_wires_it(self):
        with open(os.path.join(BIN, "..", "hooks", "hooks.json")) as f:
            hooks = json.load(f)["hooks"]["PreToolUse"]
        self.assertEqual([h["matcher"] for h in hooks], ["Agent|Task"])
        self.assertEqual(hooks[0]["hooks"][0]["command"], '"${CLAUDE_PLUGIN_ROOT}/bin/orch" hook tier')


if __name__ == "__main__":
    unittest.main()
