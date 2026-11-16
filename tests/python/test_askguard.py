"""orch hook ask: the PreToolUse hook that refuses AskUserQuestion while the session has an open spawn.

decide() is called with explicit env and clock; tasks live in a scratch ORCH_DIR, the log in a scratch ORCH_DATA; the
CLI path runs the real launcher with a scrubbed env."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin")
sys.path.insert(0, BIN)

from orchlib import askguard  # noqa: E402

SID = "11111111-aaaa-bbbb-cccc-222222222222"
NOW = 1800000000 - 1800000000 % 60


def ts(t):
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(t))


def payload(sid=SID, tool="AskUserQuestion"):
    return {"tool_name": tool, "tool_input": {"questions": [{"question": "SECRET_QUESTION"}]}, "session_id": sid}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-ask-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "tasks")
        self.data = os.path.join(self.tmp, "data")
        saved = {k: os.environ.get(k) for k in ("ORCH_DIR", "ORCH_DATA")}
        os.environ["ORCH_DIR"], os.environ["ORCH_DATA"] = self.root, self.data

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)

    def task(self, slug="task-x", sid=SID, state="implementing", journal=()):
        d = os.path.join(self.root, "proj-a", slug)
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write("# Status: %s\n\n- task_state: %s\n- driver_session: %s\n\n| role | state | timestamp (UTC) | last completed step | notes |\n"
                     "|---|---|---|---|---|\n| implementer | in_progress | %s | | |\n" % (slug, state, sid, ts(NOW)))
        with open(os.path.join(d, "journal.jsonl"), "w") as fh:
            for rec in journal:
                fh.write(json.dumps(rec) + "\n")
        return d

    def spawn(self, age=600, role="implementer"):
        return {"ts": ts(NOW - age), "kind": "spawn", "role": role}

    def decide(self, p=None, env=None):
        return askguard.decide(p or payload(), env or {}, now=NOW)

    def log(self):
        try:
            with open(os.path.join(self.data, "logs", "ask-guard.jsonl")) as fh:
                return [json.loads(line) for line in fh]
        except OSError:
            return []


class TestDecide(Case):
    def test_an_open_spawn_is_denied_with_the_way_out(self):
        self.task(journal=[self.spawn()])
        out = self.decide()
        h = out["hookSpecificOutput"]
        self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertEqual(h["permissionDecisionReason"],
                         "orch: a subagent (implementer, task proj-a/task-x) is running; a pop-up question blocks its "
                         "finish notice until answered. Park it with `orch ask` (answered in orch-pane), or `orch return "
                         "implementer` first if it already finished.")

    def test_a_returned_spawn_is_allowed(self):
        self.task(journal=[self.spawn(), {"ts": ts(NOW - 60), "kind": "return", "role": "implementer"}])
        self.assertIsNone(self.decide())

    def test_a_new_spawn_after_the_return_is_denied(self):
        self.task(journal=[self.spawn(900), {"ts": ts(NOW - 600), "kind": "return", "role": "implementer"},
                           self.spawn(300, "code-reviewer")])
        self.assertIn("code-reviewer", self.decide()["hookSpecificOutput"]["permissionDecisionReason"])

    def test_another_session_is_allowed(self):
        self.task(sid="99999999-aaaa-bbbb-cccc-222222222222", journal=[self.spawn()])
        self.assertIsNone(self.decide())

    def test_no_task_for_the_session_or_no_session_is_allowed(self):
        self.assertIsNone(self.decide())
        self.task(journal=[self.spawn()])
        self.assertIsNone(self.decide(payload(sid="")))
        self.assertIsNone(self.decide({"tool_name": "AskUserQuestion"}))

    def test_a_25_hour_old_spawn_is_allowed_and_a_23_hour_one_is_not(self):
        d = self.task(journal=[self.spawn(25 * 3600)])
        self.assertIsNone(self.decide())
        with open(os.path.join(d, "journal.jsonl"), "w") as fh:
            fh.write(json.dumps(self.spawn(23 * 3600)) + "\n")
        self.assertIsNotNone(self.decide())

    def test_an_ended_task_is_allowed(self):
        for state in ("done", "abandoned"):
            shutil.rmtree(self.root, True)
            self.task(state=state, journal=[self.spawn()])
            self.assertIsNone(self.decide(), state)

    def test_env_off_allows(self):
        self.task(journal=[self.spawn()])
        self.assertIsNone(self.decide(env={"ORCH_ASK_GUARD": "0"}))

    def test_other_tools_are_ignored(self):
        self.task(journal=[self.spawn()])
        self.assertIsNone(self.decide(payload(tool="Agent")))

    def test_an_exception_allows_and_is_logged_without_the_question(self):
        self.task(journal=[self.spawn()])
        orig = askguard.open_spawn
        askguard.open_spawn = lambda *a: 1 / 0
        try:
            self.assertIsNone(self.decide())
        finally:
            askguard.open_spawn = orig
        rec = self.log()[-1]
        self.assertEqual((rec["decision"], rec["code"]), ("error", "error"))
        self.assertNotIn("SECRET_QUESTION", json.dumps(self.log()))

    def test_a_denial_is_logged_without_the_question(self):
        self.task(journal=[self.spawn()])
        self.decide()
        rec = self.log()[-1]
        self.assertEqual((rec["decision"], rec["role"], rec["task"], rec["session_id"]),
                         ("deny", "implementer", "proj-a/task-x", SID))
        self.assertNotIn("SECRET_QUESTION", json.dumps(self.log()))

    def test_it_is_fast_on_a_big_tasks_tree(self):
        for i in range(120):
            self.task(slug="t%03d" % i, sid="00000000-0000-0000-0000-%012d" % i, journal=[self.spawn()])
        self.task(slug="zz", journal=[self.spawn()])
        t = time.time()
        self.assertIsNotNone(self.decide())
        self.assertLess(time.time() - t, 0.2)


class TestCli(Case):
    def run_cli(self, stdin, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("ORCH_", "CLAUDE"))}
        e.update(ORCH_DIR=self.root, ORCH_DATA=self.data, **env)
        return subprocess.run([sys.executable, os.path.join(BIN, "orch"), "hook", "ask"], input=stdin, env=e,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)

    def test_deny_json_on_stdout_and_exit_0(self):
        self.task(journal=[{"ts": ts(time.time() - 600), "kind": "spawn", "role": "implementer"}])
        res = self.run_cli(json.dumps(payload()))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_env_off_and_garbage_stdin_allow_with_exit_0(self):
        self.task(journal=[{"ts": ts(time.time() - 600), "kind": "spawn", "role": "implementer"}])
        res = self.run_cli(json.dumps(payload()), ORCH_ASK_GUARD="0")
        self.assertEqual((res.returncode, res.stdout), (0, ""))
        res = self.run_cli("garbage")
        self.assertEqual((res.returncode, res.stdout), (0, ""))

    def test_hooks_json_wires_it(self):
        with open(os.path.join(BIN, "..", "hooks", "hooks.json")) as f:
            hooks = json.load(f)["hooks"]["PreToolUse"]
        ask = [h for h in hooks if h["matcher"] == "AskUserQuestion"]
        self.assertEqual(len(ask), 1)
        self.assertEqual(ask[0]["hooks"][0]["command"], '"${CLAUDE_PLUGIN_ROOT}/bin/orch" hook ask')


if __name__ == "__main__":
    unittest.main()
