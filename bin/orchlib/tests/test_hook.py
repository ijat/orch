"""orch hook delegate: the PreToolUse hook that sends repo-wide searches to orch-explorer.

Real throwaway git repos; the index binary is a fake script (ORCH_INDEX_BIN) that prints what a state file
says and logs every call. TMPDIR (the cache) and ORCH_DIR (the decision log) are per-test temp dirs."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN)

from orchlib import hook  # noqa: E402

FAKE = """#!/bin/sh
echo "$@" >> "$FAKE_CALLS"
[ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"
cat "$FAKE_STATE"
"""
INDEXED = "commit abcdef0123\nfiles     12\nnames     5\n"
EMPTY_STATE = "commit (none)\nfiles     0\nnames     0\n"
SECRET = "needle_zq_secret_pattern"


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd] + list(args), check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-hook-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(os.path.join(self.repo, "src"))
        os.makedirs(self.outside)
        for p in ("a.txt", os.path.join("src", "b.py")):
            with open(os.path.join(self.repo, p), "w") as f:
                f.write("x\n")
        with open(os.path.join(self.outside, "o.txt"), "w") as f:
            f.write("x\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "a.txt", "src/b.py")
        git(self.repo, "commit", "-qm", "init")
        self.state = os.path.join(self.tmp, "state")
        self.calls = os.path.join(self.tmp, "calls")
        self.fake = os.path.join(self.tmp, "orch-index")
        with open(self.fake, "w") as f:
            f.write(FAKE)
        os.chmod(self.fake, 0o755)
        self.orch_dir = os.path.join(self.tmp, "orchdir")
        self.tmpdir = os.path.join(self.tmp, "tmpdir")
        os.makedirs(self.tmpdir)
        self.set_state(INDEXED)
        self.make_db()
        env = {"ORCH_INDEX_BIN": self.fake, "FAKE_STATE": self.state, "FAKE_CALLS": self.calls,
               "ORCH_DIR": self.orch_dir, "TMPDIR": self.tmpdir}
        saved = dict((k, os.environ.get(k)) for k in env)
        os.environ.update(env)

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            os.environ.pop("FAKE_SLEEP", None)
        self.addCleanup(restore)

    # -- helpers --
    def set_state(self, text):
        with open(self.state, "w") as f:
            f.write(text)

    def db_path(self):
        return os.path.join(self.repo, ".git", "orch-index", "index.db")

    def make_db(self, content="db"):
        os.makedirs(os.path.dirname(self.db_path()), exist_ok=True)
        with open(self.db_path(), "w") as f:
            f.write(content)

    def payload(self, tool, tool_input, agent="orch-implementer", cwd=None):
        p = {"tool_name": tool, "tool_input": tool_input, "cwd": cwd or self.repo, "session_id": "s1"}
        if agent is not None:
            p["agent_type"] = agent
        return p

    def call(self, payload):
        out = io.StringIO()
        rc = hook.run(io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)), out)
        self.assertEqual(rc, 0)
        return out.getvalue()

    def bash(self, cmd, **kw):
        return self.call(self.payload("Bash", {"command": cmd}, **kw))

    def assertDenied(self, out, msg=None):
        self.assertTrue(out, msg or "expected a deny, got an allow")
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["hookEventName"], "PreToolUse")
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertEqual(d["permissionDecisionReason"].splitlines()[0], hook.REASON)

    def assertAllowed(self, out, msg=None):
        self.assertEqual(out, "", msg or "expected an allow, got %r" % out)

    def log(self):
        path = os.path.join(self.orch_dir, "delegate-hook.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def n_calls(self):
        if not os.path.exists(self.calls):
            return 0
        with open(self.calls) as f:
            return len(f.read().splitlines())


class ReasonText(HookCase):
    def test_exact_first_line(self):
        self.assertEqual(
            hook.REASON,
            "orch: this repo has a code index — send repo-wide searches to orch-explorer "
            "(batch your questions; ask for file:line). Searching one file is fine.")


class BroadSearches(HookCase):
    DENIED = [
        "rg foo", "rg foo .", "rg foo src", "rg -n -g '*.py' foo", "rg -e foo -e bar", "rg --files",
        "rg foo ./src", "rg foo *", "FOO=1 rg foo",
        "grep -r foo .", "grep -rn foo src", "grep -R foo", "grep --recursive foo .", "grep -nr foo",
        "grep -d recurse foo .", "egrep -r foo .",
        "git grep foo", "git -C . grep foo", "git grep -n foo src", "git grep foo -- src", "git grep foo -- '*.py'",
        "git --no-pager grep foo",
        "find . -name x", "find src -type f", "find", "find . -maxdepth 1",
        "ag foo", "ack foo src",
        "cd src && rg foo", "echo hi; rg foo", "ls || rg foo", "(rg foo)", "rg foo | head", "rg foo > /dev/null",
        "rg foo 2>&1", "/usr/bin/grep -r foo .", "env FOO=1 rg foo", "cd src\nrg foo",
    ]
    ALLOWED = [
        "rg foo a.txt", "rg foo src/b.py", "rg -n foo a.txt", "rg -e foo a.txt", "rg foo " + "OUTSIDE",
        "grep foo a.txt", "grep -r foo a.txt", "grep -rn foo src/b.py", "grep foo", "grep -c foo a.txt",
        "ls | grep x", "git log | rg foo", "cat a.txt | grep -r foo", "ls", "git status", "git log --oneline",
        "git grep foo -- a.txt", "git grep -e foo -- a.txt", "git -C OUTSIDE grep foo",
        "grep -r foo OUTSIDE", "find OUTSIDE -name x", "rg foo OUTSIDE/o.txt", "ag foo a.txt",
        "cd OUTSIDE && rg foo", "echo rg foo", "rg", "rg --version", "sed -n 1,5p a.txt",
        "find a.txt -name x", "cat a.txt",
    ]

    def test_denied_with_index(self):
        for cmd in self.DENIED:
            with self.subTest(cmd=cmd):
                self.assertDenied(self.bash(cmd), cmd)

    def test_allowed_with_index(self):
        for cmd in self.ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash(cmd.replace("OUTSIDE", self.outside)), cmd)

    def test_broad_allowed_without_index(self):
        os.remove(self.db_path())
        for cmd in self.DENIED:
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.bash(cmd), cmd)

    def test_working_in_a_subdirectory(self):
        self.assertDenied(self.call(self.payload("Bash", {"command": "rg foo"}, cwd=os.path.join(self.repo, "src"))))
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo b.py"}, cwd=os.path.join(self.repo, "src"))))
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo ../a.txt"}, cwd=os.path.join(self.repo, "src"))))
        self.assertDenied(self.call(self.payload("Bash", {"command": "rg foo .."}, cwd=os.path.join(self.repo, "src"))))

    def test_cwd_outside_repo(self):
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo"}, cwd=self.outside)))
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo " + self.outside}, cwd=self.outside)))
        # an absolute path into the repo from outside is still inside the repo
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo " + self.repo}, cwd=self.outside)))

    def test_unparsable_command_allows(self):
        self.assertAllowed(self.bash("rg 'unterminated"))


class GrepGlobTools(HookCase):
    def test_grep(self):
        self.assertDenied(self.call(self.payload("Grep", {"pattern": "x"})))
        self.assertDenied(self.call(self.payload("Grep", {"pattern": "x", "path": self.repo})))
        self.assertDenied(self.call(self.payload("Grep", {"pattern": "x", "path": "src"})))
        self.assertAllowed(self.call(self.payload("Grep", {"pattern": "x", "path": "a.txt"})))
        self.assertAllowed(self.call(self.payload("Grep", {"pattern": "x", "path": os.path.join(self.repo, "src", "b.py")})))
        self.assertAllowed(self.call(self.payload("Grep", {"pattern": "x", "path": self.outside})))
        self.assertAllowed(self.call(self.payload("Grep", {"pattern": "x", "path": "missing-dir"})))

    def test_grep_without_index(self):
        os.remove(self.db_path())
        self.assertAllowed(self.call(self.payload("Grep", {"pattern": "x"})))

    def test_glob(self):
        self.assertDenied(self.call(self.payload("Glob", {"pattern": "**/*.py"})))
        self.assertDenied(self.call(self.payload("Glob", {"pattern": "*.py", "path": self.repo})))
        self.assertDenied(self.call(self.payload("Glob", {"pattern": self.repo + "/src/*.py"})))
        self.assertAllowed(self.call(self.payload("Glob", {"pattern": "src/b.py"})))
        self.assertAllowed(self.call(self.payload("Glob", {"pattern": "**/*.md", "path": self.outside})))
        self.assertAllowed(self.call(self.payload("Glob", {"pattern": self.outside + "/*.txt"})))

    def test_other_tools(self):
        self.assertAllowed(self.call(self.payload("Read", {"file_path": "a.txt"})))
        self.assertAllowed(self.call(self.payload("Edit", {"file_path": "a.txt"})))


class Scope(HookCase):
    def test_reviewer_enforced(self):
        self.assertDenied(self.bash("rg foo", agent="orch-code-reviewer"))

    def test_other_agent_types_allowed_and_unlogged(self):
        for agent in ("orch-explorer", "orch-planner", "orch-security-reviewer", "general-purpose", "", None):
            with self.subTest(agent=agent):
                self.assertAllowed(self.bash("rg foo", agent=agent))
        self.assertEqual(self.log(), [])
        self.assertEqual(self.n_calls(), 0)

    def test_not_a_repo(self):
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo"}, cwd=self.outside)))
        self.assertEqual(self.log(), [])

    def test_missing_cwd(self):
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo"}, cwd=os.path.join(self.tmp, "gone"))))

    def test_linked_worktree_uses_the_common_index(self):
        wt = os.path.join(self.tmp, "wt")
        git(self.repo, "worktree", "add", "-q", wt, "-b", "side")
        self.assertDenied(self.call(self.payload("Bash", {"command": "rg foo"}, cwd=wt)))
        # the main checkout's tree is outside the worktree's tree: a search of it is not inside the work tree
        self.assertAllowed(self.call(self.payload("Bash", {"command": "rg foo " + self.repo}, cwd=wt)))


class Bypass(HookCase):
    def test_marker_allows_and_logs(self):
        out = self.bash("rg %s . # orch-self-search: need a regex the index cannot do" % SECRET)
        self.assertAllowed(out)
        self.assertEqual([r["decision"] for r in self.log()], ["bypass"])

    def test_marker_needs_a_reason(self):
        self.assertDenied(self.bash("rg foo . # orch-self-search:"))
        self.assertDenied(self.bash("rg foo . # orch-self-search"))

    def test_marker_on_grep_tool_does_not_apply(self):
        self.assertDenied(self.call(self.payload("Grep", {"pattern": "# orch-self-search: why"})))

    def test_marker_without_broad_search_is_not_logged(self):
        self.assertAllowed(self.bash("rg foo a.txt # orch-self-search: why"))
        self.assertEqual(self.log(), [])


class FailOpen(HookCase):
    def test_malformed_json(self):
        for text in ("", "{", "not json", "[]", "null", '"x"', '{"agent_type": "orch-implementer"}',
                     '{"agent_type": "orch-implementer", "tool_name": "Bash", "tool_input": "rg foo"}',
                     '{"agent_type": "orch-implementer", "tool_name": "Bash", "tool_input": {"command": 5}}'):
            with self.subTest(text=text):
                self.assertAllowed(self.call(text))

    def test_missing_binary(self):
        os.environ["ORCH_INDEX_BIN"] = os.path.join(self.tmp, "no-such-binary")
        self.assertAllowed(self.bash("rg foo"))

    def test_binary_fails(self):
        with open(self.fake, "w") as f:
            f.write("#!/bin/sh\nexit 2\n")
        self.assertAllowed(self.bash("rg foo"))

    def test_stats_timeout(self):
        os.environ["FAKE_SLEEP"] = "5"
        saved = hook.STATS_TIMEOUT
        hook.STATS_TIMEOUT = 0.3
        self.addCleanup(setattr, hook, "STATS_TIMEOUT", saved)
        self.assertAllowed(self.bash("rg foo"))
        os.environ.pop("FAKE_SLEEP")
        # the timeout was not cached: the next call asks again and is denied
        self.assertDenied(self.bash("rg foo"))
        self.assertEqual(self.n_calls(), 2)

    def test_log_failure_does_not_break_the_decision(self):
        with open(self.orch_dir, "w") as f:  # ORCH_DIR is a file: the log cannot be written
            f.write("x")
        self.assertDenied(self.bash("rg foo"))


class IndexCheck(HookCase):
    def test_stats_states(self):
        for text, want_deny in ((EMPTY_STATE, False), ("commit abc\nfiles 0\n", False), ("commit abc\nfiles 3\n", True),
                                ("garbage\n", False), ("", False)):
            with self.subTest(text=text):
                self.set_state(text)
                self.make_db(content=text or "empty")  # new size: a new cache key
                out = self.bash("rg foo")
                self.assertDenied(out) if want_deny else self.assertAllowed(out)

    def test_no_db_file_never_runs_the_binary(self):
        os.remove(self.db_path())
        self.assertAllowed(self.bash("rg foo"))
        self.assertEqual(self.n_calls(), 0)

    def test_cache_hit_and_mtime_key(self):
        self.assertDenied(self.bash("rg foo"))
        self.assertDenied(self.bash("rg bar"))
        self.assertDenied(self.call(self.payload("Grep", {"pattern": "x"})))
        self.assertEqual(self.n_calls(), 1, "second and third call must come from the cache")
        # the index is emptied and the db file changes: the key changes, stats is asked again
        self.set_state(EMPTY_STATE)
        st = os.stat(self.db_path())
        os.utime(self.db_path(), ns=(st.st_atime_ns, st.st_mtime_ns + 5000000000))
        self.assertAllowed(self.bash("rg foo"))
        self.assertEqual(self.n_calls(), 2)
        self.assertAllowed(self.bash("rg foo"))
        self.assertEqual(self.n_calls(), 2)

    def test_wal_change_busts_the_cache(self):
        self.set_state(EMPTY_STATE)
        self.assertAllowed(self.bash("rg foo"))
        self.assertEqual(self.n_calls(), 1)
        self.set_state(INDEXED)
        with open(self.db_path() + "-wal", "w") as f:
            f.write("w")
        self.assertDenied(self.bash("rg foo"))
        self.assertEqual(self.n_calls(), 2)

    def test_stats_runs_against_the_repo(self):
        self.bash("rg foo")
        with open(self.calls) as f:
            self.assertEqual(f.read().split(), ["stats", "--repo", self.repo])

    def test_non_broad_search_never_asks(self):
        self.bash("rg foo a.txt")
        self.assertEqual(self.n_calls(), 0)


class DecisionLog(HookCase):
    def test_lines_and_no_command_text(self):
        self.bash("rg %s ." % SECRET)
        os.remove(self.db_path())
        self.bash("grep -r %s ." % SECRET, agent="orch-code-reviewer")
        self.make_db()
        self.bash("rg %s . # orch-self-search: %s" % (SECRET, SECRET))
        self.bash("rg %s a.txt" % SECRET)  # not broad: no line
        recs = self.log()
        self.assertEqual([r["decision"] for r in recs], ["deny", "allow-broad-no-index", "bypass"])
        self.assertEqual([r["form"] for r in recs], ["rg", "grep", "rg"])
        self.assertEqual(recs[1]["agent_type"], "orch-code-reviewer")
        self.assertEqual(recs[0]["repo"], self.repo)
        with open(os.path.join(self.orch_dir, "delegate-hook.jsonl")) as f:
            self.assertNotIn(SECRET, f.read())
        for r in recs:
            self.assertTrue(r["ts"])


class CommandLine(HookCase):
    def run_cli(self, stdin_text):
        env = dict(os.environ)
        return subprocess.run([sys.executable, os.path.join(BIN, "orch"), "hook", "delegate"], input=stdin_text,
                              capture_output=True, text=True, env=env, cwd=self.tmp, timeout=30)

    def test_deny_through_the_launcher(self):
        r = self.run_cli(json.dumps(self.payload("Bash", {"command": "rg foo"})))
        self.assertEqual(r.returncode, 0)
        self.assertDenied(r.stdout.strip())
        self.assertEqual(r.stderr, "")

    def test_allow_and_garbage_through_the_launcher(self):
        for text in (json.dumps(self.payload("Bash", {"command": "rg foo a.txt"})), "not json", ""):
            r = self.run_cli(text)
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_listed_among_the_commands(self):
        r = subprocess.run([sys.executable, os.path.join(BIN, "orch"), "commands"], capture_output=True, text=True)
        self.assertIn("hook", r.stdout.split())


if __name__ == "__main__":
    unittest.main()
