"""Task inference for the subagent verbs (role, log): the cwd, the session and ORCH_TASK. Neutral fixtures only."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

import test_core as core  # noqa: E402
from orchlib import infer, paths  # noqa: E402

S, S2 = "sess-aaaa", "sess-bbbb"


class InferBase(core.Base):
    def setUp(self):
        core.Base.setUp(self)
        self.env["ORCH_TASK"] = ""   # the developer's own export must not leak into the fixtures
        self.outside = os.path.realpath(os.path.join(self.tmp, "elsewhere"))
        os.makedirs(self.outside)
        self.repo_dir = os.path.realpath(os.path.join(self.tmp, "repos", "myapp"))
        os.makedirs(self.repo_dir)

    def task(self, slug, sid=None, worktree=True, state=None, repo="myapp"):
        """An active task `repo/slug` driven by session `sid`; worktree=True makes <repo_dir>/.claude/worktrees/orch-<slug>."""
        d = self.init(repo, slug, env={"CLAUDE_CODE_SESSION_ID": sid} if sid else {})
        sets = []
        if worktree:
            wt = os.path.join(self.repo_dir, ".claude", "worktrees", "orch-" + slug)
            os.makedirs(wt, exist_ok=True)
            sets.append("worktree=" + os.path.realpath(wt))
        if state:
            sets.append("task_state=" + state)
        if sets:
            self.ok("set", *sets, "-t", "%s/%s" % (repo, slug))
        return d

    def resolve(self, cwd, sid, env_task):
        """infer.resolve over the fixture root (resolve_task reads ORCH_DIR)."""
        with mock.patch.dict(os.environ, {"ORCH_DIR": self.root}):
            return infer.resolve(cwd, sid, env_task, self.root)

    def wt(self, slug):
        return os.path.realpath(os.path.join(self.repo_dir, ".claude", "worktrees", "orch-" + slug))

    def run_in(self, cwd, *argv, **kw):
        old = os.getcwd()
        os.chdir(cwd)
        try:
            return self.run_orch(*argv, **kw)
        finally:
            os.chdir(old)

    def refused(self, cwd, *argv, **kw):
        """Run, expect exit 1, and prove no task file changed."""
        before = core.tree_hash(self.root)
        rc, out, err = self.run_in(cwd, *argv, **kw)
        self.assertEqual((rc, out), (1, ""), "stderr=%s" % err)
        self.assertEqual(core.tree_hash(self.root), before, "a refusal wrote something")
        return err

    def role_row(self, d):
        return [l for l in self.read(d, "status.md").decode().splitlines() if l.startswith("| implementer")][0]


class TestInference(InferBase):
    def test_t1_one_active_task_by_session(self):
        a = self.task("a", sid=S)
        rc, out, err = self.run_in(self.outside, "role", "implementer", "in_progress", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.endswith("[myapp/a]\n"), out)
        self.assertIn("in_progress", self.role_row(a))

    def test_t2_two_tasks_one_session_is_ambiguous(self):
        self.task("a", sid=S)
        self.task("b", sid=S)
        err = self.refused(self.outside, "role", "implementer", "done", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertIn("ambiguous task: this session drives myapp/a, myapp/b", err)
        self.assertIn("pass -t <repo>/<slug>", err)

    def test_t3_cwd_breaks_the_session_tie(self):
        a, b = self.task("a", sid=S), self.task("b", sid=S)
        sub = os.path.join(self.wt("a"), "src")
        os.makedirs(sub)
        rc, out, err = self.run_in(sub, "log", "hello", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertEqual((rc, out), (0, "logged [myapp/a]\n"), err)
        self.assertIn("hello", self.read(a, "log.md").decode())
        self.assertNotIn("hello", self.read(b, "log.md").decode())

    def test_t4_cwd_and_session_disagree(self):
        self.task("a")
        self.task("b", sid=S)
        err = self.refused(self.wt("a"), "log", "hello", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertIn("conflicting task", err)
        self.assertIn("myapp/a", err)
        self.assertIn("myapp/b", err)

    def test_t5_no_session_no_cwd(self):
        self.task("a", sid=S)
        err = self.refused(self.outside, "log", "hello")
        self.assertIn("no task: pass -t TASK (absolute dir or <repo>/<slug>)", err)
        self.assertIn("none inferred from the cwd or this session", err)

    def test_t6_finished_tasks_are_not_candidates(self):
        self.task("old", sid=S, state="done")
        self.task("gone", sid=S, state="abandoned")
        c = self.task("cur", sid=S)
        rc, out, err = self.run_in(self.outside, "log", "hi", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertEqual((rc, out), (0, "logged [myapp/cur]\n"), err)
        self.assertIn("hi", self.read(c, "log.md").decode())

    def test_t8_explicit_task_wins_and_prints_no_tag(self):
        self.task("a", sid=S)
        b = self.task("b", sid=S2)
        for argv in (("log", "x", "-t", "myapp/b"), ("-t", "myapp/b", "log", "x")):
            rc, out, err = self.run_in(self.outside, *argv, env={"CLAUDE_CODE_SESSION_ID": S, "ORCH_TASK": "myapp/a"})
            self.assertEqual((rc, out), (0, "logged\n"), err)
        rc, out, err = self.run_in(self.wt("a"), "role", "implementer", "done", "-t", "myapp/b")
        self.assertEqual((rc, out), (0, "role implementer -> done\n"), err)
        self.assertIn("done", self.role_row(b))

    def test_t10_placeholder_worktrees_never_match(self):
        self.task("a", worktree=False)                       # worktree: not yet created
        self.run_orch("set", "worktree=none (user: no worktree)", "-t", "myapp/a")
        err = self.refused(self.repo_dir, "log", "x")
        self.assertIn("none inferred", err)
        self.task("m")
        os.rmdir(self.wt("m"))                               # a worktree that is gone
        err = self.refused(os.path.join(self.repo_dir, ".claude"), "log", "x")
        self.assertIn("none inferred", err)

    def test_t11_the_main_checkout_is_not_inside_a_worktree(self):
        a = self.task("a", sid=S)
        rc, out, err = self.run_in(self.repo_dir, "log", "x", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertEqual((rc, out), (0, "logged [myapp/a]\n"), err)    # the session decides, the cwd gave nothing
        self.assertEqual(infer.infer_task(self.repo_dir, S, self.root), (a, "session"))
        self.assertEqual(infer.infer_task(self.wt("a"), S, self.root), (a, "cwd+session"))

    def test_nested_worktrees_two_matches_refuse(self):
        self.task("a")
        self.task("b")
        nested = os.path.join(self.wt("a"), "inner")
        os.makedirs(nested)
        self.ok("set", "worktree=" + os.path.realpath(nested), "-t", "myapp/b")
        err = self.refused(nested, "log", "x")
        self.assertIn("ambiguous task: the cwd is inside the worktrees of myapp/a, myapp/b", err)


class TestOrchTask(InferBase):
    """ORCH_TASK is a signal: it must agree with the cwd and the session, and decides alone only when they give nothing."""

    def test_t7a_env_disagrees_with_the_session(self):
        self.task("a", sid=S)
        self.task("b")
        err = self.refused(self.outside, "log", "x", env={"CLAUDE_CODE_SESSION_ID": S, "ORCH_TASK": "myapp/b"})
        self.assertIn("conflicting task: ORCH_TASK names myapp/b, the session gives myapp/a", err)
        self.assertIn("pass -t <repo>/<slug>", err)

    def test_t7b_env_agrees_with_the_session(self):
        self.task("a", sid=S)
        rc, out, err = self.run_in(self.outside, "log", "x", env={"CLAUDE_CODE_SESSION_ID": S, "ORCH_TASK": "myapp/a"})
        self.assertEqual((rc, out), (0, "logged [myapp/a]\n"), err)

    def test_t7c_env_does_not_break_a_session_tie(self):
        self.task("a", sid=S)
        self.task("b", sid=S)
        err = self.refused(self.outside, "log", "x", env={"CLAUDE_CODE_SESSION_ID": S, "ORCH_TASK": "myapp/a"})
        self.assertIn("ambiguous task: this session drives myapp/a, myapp/b", err)
        self.assertIn("ORCH_TASK=myapp/a does not settle it", err)

    def test_t7d_env_disagrees_with_the_cwd(self):
        self.task("a")
        self.task("b")
        err = self.refused(self.wt("a"), "log", "x", env={"ORCH_TASK": "myapp/b"})
        self.assertIn("conflicting task: ORCH_TASK names myapp/b, the cwd gives myapp/a", err)

    def test_t7e_env_decides_alone_when_nothing_else_does(self):
        b = self.task("b")
        rc, out, err = self.run_in(self.outside, "log", "x", env={"ORCH_TASK": "myapp/b"})
        self.assertEqual((rc, out), (0, "logged [myapp/b]\n"), err)
        self.assertIn("x", self.read(b, "log.md").decode())
        self.assertEqual(self.resolve(self.outside, None, "myapp/b"), (b, "ORCH_TASK"))
        # the absolute dir form names the same task
        self.assertEqual(self.resolve(self.outside, None, b), (b, "ORCH_TASK"))

    def test_t7f_env_naming_a_finished_or_malformed_task(self):
        self.task("a", state="done")
        err = self.refused(self.outside, "log", "x", env={"ORCH_TASK": "myapp/a"})
        self.assertIn("ORCH_TASK names myapp/a (done); unset it or pass -t", err)
        err = self.refused(self.outside, "log", "x", env={"ORCH_TASK": "just-one-part"})
        self.assertIn("TASK must be an absolute dir or <repo>/<slug>", err)
        err = self.refused(self.outside, "log", "x", env={"ORCH_TASK": "myapp/missing"})
        self.assertIn("no such task dir", err)

    def test_env_agreeing_with_cwd_and_session(self):
        a = self.task("a", sid=S)
        self.assertEqual(self.resolve(self.wt("a"), S, "myapp/a"), (a, "cwd+session"))

    def test_explicit_task_ignores_a_stale_env(self):
        self.task("a")
        self.task("b", state="done")
        rc, out, err = self.run_in(self.outside, "log", "x", "-t", "myapp/a", env={"ORCH_TASK": "myapp/b"})
        self.assertEqual((rc, out), (0, "logged\n"), err)


class TestOtherVerbsUnchanged(InferBase):
    def test_t9_verbs_outside_the_set_never_infer(self):
        self.task("a", sid=S)
        env = {"CLAUDE_CODE_SESSION_ID": S, "ORCH_TASK": "myapp/a"}
        before = core.tree_hash(self.root)
        for argv in (("set", "mode=lite"), ("journal", "override", "what=x")):
            rc, out, err = self.run_in(self.outside, *argv, env=env)
            self.assertEqual((rc, out), (1, ""), argv)
            self.assertEqual(err.splitlines()[0], "orch: no task: pass -t TASK (absolute dir or <repo>/<slug>)", argv)
        self.assertEqual(core.tree_hash(self.root), before)
        rc, out, err = self.run_in(self.outside, "show", env=env)
        self.assertEqual(rc, 0, err)
        self.assertIn("myapp/a", out)
        self.assertNotIn("role", out)                         # the active-task list, not one task's card
        rc, out, err = self.run_in(self.outside, "plan-check", env=env)
        self.assertEqual(rc, 1)
        self.assertIn("plan-check needs a PATH or -t TASK", err)

    def test_the_resolver_sees_the_task_root_given_to_it(self):
        a = self.task("a", sid=S)
        self.assertEqual(infer.infer_task(self.outside, S, self.root), (a, "session"))
        self.assertEqual(paths.task_names(a), ("myapp", "a"))
        with self.assertRaises(infer.Refusal):
            infer.infer_task(self.outside, None, self.root)
        with self.assertRaises(infer.Refusal):
            infer.infer_task(self.outside, S, os.path.join(self.tmp, "no-such-root"))


if __name__ == "__main__":
    unittest.main()
