"""orch hook git-guard: the Bash PreToolUse guard against hook bypasses and against switching agent_docs off."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BIN = os.path.join(ROOT, "plugin", "bin")
sys.path.insert(0, BIN)

from orchlib import gitguard, gitrules, settings  # noqa: E402

LAUNCHER = os.path.join(BIN, "orch")

BLOCKED = [   # the shell guard's own cases
    'git commit -m "x" --no-verify',
    'git push --no-verify origin main',
    'git -c core.hooksPath=/dev/null commit -m x',
    'AGENTDOCS_ALLOW=1 git commit -m x',
    'env AGENTDOCS_ALLOW=1 git push',
    'git commit -nm x',
    'git config agentdocs.mode warn',
    'cd r && git config --add agentdocs.allowPath x',
    'git -C /tmp/r config agentdocs.allow true',
    '/usr/bin/git push --no-verify',
    'git config set agentdocs.mode warn',
    # the same rules in other spellings
    'GIT_DIR=x AGENTDOCS_ALLOW=1 git commit -m x',
    'command AGENTDOCS_ALLOW=1 git push',
    'exec AGENTDOCS_ALLOW=1 git push',
    'sudo AGENTDOCS_ALLOW=1 git push',
    'env -i AGENTDOCS_ALLOW=1 git push',
    'sudo -u root AGENTDOCS_ALLOW=1 git push',
    'export AGENTDOCS_ALLOW=1',
    'export FOO=1 AGENTDOCS_ALLOW=1',
    'declare -x AGENTDOCS_ALLOW=1',
    'echo ok && export AGENTDOCS_ALLOW=1; git push',
    'git commit --no-verify -m x',
    'git -c user.name=x commit -n',
    'git commit -anm x',
    'git config --global core.hooksPath /tmp/h',
    'git config --global --replace-all agentdocs.allowContent .',
    'git push origin main && git push --no-verify',
    'git --git-dir=.git config agentdocs.allow true',
    # separators inside quoted messages do not hide a flag (F1)
    'git commit -m "fix: a; b" --no-verify',
    'git commit -m "a & b" --no-verify',
    'git commit -m "a | b" -n',
    'git commit -m "x" 2>&1 --no-verify',
    'git commit -m "x" &> /dev/null --no-verify',
    'git commit -m "line1\nline2" --no-verify',
    'git commit -m \'it"s; fine\' --no-verify',
    'ls && git commit -m "x" --no-verify',
    '(git commit --no-verify)',
    'echo $(git commit --no-verify)',
    '# don\'t\ngit commit --no-verify',
    'git commit -m x \\\n --no-verify',
    # direct one-line bypasses (S1)
    'git -c agentdocs.allow=true commit -m x',
    'git -c agentdocs.mode=warn push',
    'git -c AgentDocs.Allow=true push',
    'git --config-env=agentdocs.allow=X commit',
    'git --config-env agentdocs.allow=X commit',
    'git commit --no-verif',
    'git push --no-ve',
    'git push --no-v',
    'git commit --no-verify=1',
    'git config alias.c "commit --no-verify"',
    'HOME=/tmp/x git commit -m y',
    'XDG_CONFIG_HOME=/x git commit -m y',
    'GIT_CONFIG_GLOBAL=/x git commit -m y',
    'GIT_CONFIG_SYSTEM=/x git push',
    'GIT_CONFIG_NOSYSTEM=1 git commit -m y',
    'GIT_CONFIG_PARAMETERS="\'core.x=y\'" git commit -m y',
    'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=agentdocs.allow GIT_CONFIG_VALUE_0=true git commit -m y',
    'env HOME=/x git commit -m y',
    'sudo -u root HOME=/x git commit -m y',
    'export GIT_CONFIG_GLOBAL=/x',
    'declare -x XDG_CONFIG_HOME=/x',
    'PATH=/tmp:$PATH git commit -m y',
    'PYTHONPATH=/tmp git push',
    # the hook's own trust (S2)
    'ORCH_BIN=/usr/bin/true git commit -m x',
    'ORCH_DATA=/tmp git commit -m x',
    'ORCH_DIR=/tmp git push',
    'ORCH_DOCS_PRIVATE=/nonexistent git commit -m x',
    'export ORCH_BIN=/usr/bin/true',
    # the agent-docs switch
    'orch docs disable',
    '/opt/orch/bin/orch docs disable',
    '"${CLAUDE_PLUGIN_ROOT}/bin/orch" docs disable',
    'orch docs disable --dry-run',
    'orch -t r/s docs disable',
    'orch docs -C /tmp/r disable',
    'python3 /p/bin/orch docs disable',
    'AGENTDOCS_STORE=/tmp/s orch docs disable',
    'orch setting set agent_docs off',
    'orch -t x setting set agent_docs off',
    '/a/b/orch setting set agent_docs off',
    'env FOO=1 orch setting set agent_docs off',
]

ALLOWED = [
    'git commit -m "add n option"',
    'git commit -m "skip --no-verify docs"',
    'git status',
    'git log -n 5',
    'echo --no-verify',
    'git commit -am "fix"',
    'git config --get agentdocs.mode',
    'git config --unset agentdocs.mode',
    'git config --show-origin agentdocs.mode',
    'git config agentdocs.allow',
    'git config --unset agentdocs.allow',
    'python3 - <<\'EOF\'\nprint("git config agentdocs.mode warn --no-verify")\nEOF\ngit diff --stat',
    'git diff --stat && grep -e --no-verify README.md',
    # extras
    'git config --list',
    'git config user.name x',
    'git commit -m "x" 2>&1',
    'echo AGENTDOCS_ALLOW=1',
    'cat <<EOF\nAGENTDOCS_ALLOW=1 git commit --no-verify\nEOF',
    'ls -n',
    'git commit -m "n"',
    # quoted prose and separators in messages (F1), names in prose (S1/S2), env on other commands
    'git commit -m "fix: a; git commit --no-verify"',
    'git commit -m "x & y"',
    'git commit -m "a | b"',
    'git commit -m "mention --no-verify and -n and core flags"',
    'git commit -m "line1\nline2\n--no-verify"',
    'git commit -m "it\'s fine; really"',
    'git commit -m "use HOME=/x for tests"',
    'echo "HOME=/x git commit"',
    'echo "git -c agentdocs.allow=true commit"',
    'HOME=/x ls',
    'PATH=/tmp:$PATH ls',
    'export FOO=1',
    'git log --format=%H -n 3',
    'git status 2>&1',
    'git status &> /dev/null',
    'cd /tmp && git status; git diff | head',
    '# git commit --no-verify\ngit status',
    'git commit --no-verbose',
    'git commit -m "x" # --no-verify',
    'git -C /tmp/repo status',
    'git -c user.name=x commit -m y',
    'git config alias.st status',
    'git commit -m \'a\' && echo "AGENTDOCS_ALLOW is a human override"',
    # the switch: reads and enabling stay allowed
    'orch docs enable',
    'orch docs status',
    'orch docs adopt docs/STATE.md',
    'orch setting get agent_docs',
    'orch setting set agent_docs on',
    'orch setting set mythos_review off',
    'orch docs adopt notes.md && orch docs commit',
    'echo orch docs disable',
    'grep -n "agent_docs off" README.md',
    '',
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-guard-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.orch = os.path.join(self.tmp, "orch")
        os.makedirs(self.orch)

    def switch(self, value):
        with open(os.path.join(self.orch, ".orch-settings.json"), "w") as fh:
            json.dump({"agent_docs": value}, fh)

    def hook(self, payload, raw=False):
        env = dict(os.environ, ORCH_DIR=self.orch)
        env.pop("ORCH_DATA", None)
        data = payload if raw else json.dumps({"tool_name": "Bash", "tool_input": {"command": payload}})
        return subprocess.run([sys.executable, LAUNCHER, "hook", "git-guard"], input=data, capture_output=True,
                              text=True, env=env)


class TestRules(unittest.TestCase):
    def test_blocked_commands(self):
        for cmd in BLOCKED:
            self.assertTrue(gitrules.reasons(cmd), cmd)

    def test_allowed_commands(self):
        for cmd in ALLOWED:
            self.assertEqual(gitrules.reasons(cmd), [], cmd)

    def test_reasons_name_the_rule(self):
        self.assertEqual(gitrules.reasons("git -c core.hooksPath=/x commit --no-verify -n"),
                         ["--no-verify", "commit -n", "core.hooksPath"])
        self.assertEqual(gitrules.reasons("AGENTDOCS_ALLOW=1 git commit -nm x"), ["AGENTDOCS_ALLOW", "commit -n"])
        self.assertEqual(gitrules.reasons("orch docs disable"), ["orch docs disable"])
        self.assertEqual(gitrules.reasons("orch setting set agent_docs off"), ["orch setting set agent_docs off"])
        self.assertEqual(gitrules.reasons("git config agentdocs.mode warn"), ["git config agentdocs.*"])


class TestSwitchFile(unittest.TestCase):
    def test_settings_file_is_the_one_settings_py_uses(self):
        import contextlib
        from unittest import mock
        for env in ({"ORCH_DATA": "/d"}, {"ORCH_DIR": "/t"}, {"ORCH_DATA": "/d", "ORCH_DIR": "/t"}, {}):
            full = {"ORCH_DATA": "", "ORCH_DIR": ""}
            full.update(env)
            with mock.patch.dict(os.environ, full):
                self.assertEqual(gitguard.settings_file(), settings.settings_path(), env)


class TestHook(Base):
    def test_on_blocks_with_exit_2_and_one_message(self):
        self.switch("on")
        res = self.hook("git commit -m x --no-verify")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertEqual(len(res.stderr.strip().split("\n")), 1)
        self.assertIn("--no-verify", res.stderr)
        self.assertIn("orch docs adopt <path>", res.stderr)
        self.assertNotIn("agent-docs adopt", res.stderr)
        res = self.hook("orch docs disable")
        self.assertEqual(res.returncode, 2)
        self.assertIn("switch off", res.stderr)
        self.assertEqual(self.hook("git status").returncode, 0)
        self.assertEqual(self.hook("git status").stderr, "")

    def test_a_sample_of_every_case_through_the_launcher_when_on(self):
        self.switch("on")
        for cmd in BLOCKED[:11] + BLOCKED[-6:]:
            self.assertEqual(self.hook(cmd).returncode, 2, cmd)
        for cmd in ALLOWED[:13] + ALLOWED[-8:]:
            self.assertEqual(self.hook(cmd).returncode, 0, cmd)

    def test_switch_off_passes_everything(self):
        for value in ("off", None):
            if value:
                self.switch(value)
            for cmd in BLOCKED:
                res = self.hook(cmd)
                self.assertEqual((res.returncode, res.stderr), (0, ""), cmd)

    def test_off_exits_before_reading_stdin(self):
        res = self.hook("{ not json", raw=True)
        self.assertEqual((res.returncode, res.stderr), (0, ""))

    def test_malformed_input_fails_open_when_on(self):
        self.switch("on")
        for raw in ("", "{ not json", "[]", "null", '{"tool_input": 3}', '{"tool_input": {"command": 7}}',
                    '{"tool_input": {}}'):
            res = self.hook(raw, raw=True)
            self.assertEqual((res.returncode, res.stderr), (0, ""), raw)

    def test_a_malformed_settings_file_means_off(self):
        with open(os.path.join(self.orch, ".orch-settings.json"), "w") as fh:
            fh.write("{ nope")
        res = self.hook("git commit --no-verify")
        self.assertEqual((res.returncode, res.stderr), (0, ""))

    def test_cli_spelling_works_too(self):
        self.switch("on")
        env = dict(os.environ, ORCH_DIR=self.orch)
        res = subprocess.run([sys.executable, LAUNCHER, "-t", "x", "hook", "git-guard"],
                             input=json.dumps({"tool_input": {"command": "git push --no-verify"}}),
                             capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 2, res.stderr)

    def test_hooks_json_runs_it_on_bash(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json")) as fh:
            entries = json.load(fh)["hooks"]["PreToolUse"]
        mine = [e for e in entries if e["matcher"] == "Bash"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["hooks"][0]["command"], '"${CLAUDE_PLUGIN_ROOT}/bin/orch" hook git-guard')


if __name__ == "__main__":
    unittest.main()
