"""orch merge: guard conditions, the merge, cleanup, program merge points, sha binding, policy.
Every git test runs in throwaway repos under a temp dir; the recorder asserts it. Neutral fixtures only."""
import ast
import json
import os
import re
import shlex
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import test_core as core  # noqa: E402
from orchlib import merge, policy, status as st  # noqa: E402
from orchlib.paths import OrchError  # noqa: E402

T = "myapp/add-login"
# the test's own list (merge.FORBIDDEN is checked against it, not the other way round)
NEVER = ("push", "--force", "-D", "reset", "checkout", "clean", "--ff-only")
READ_ONLY = ("rev-parse", "symbolic-ref", "status", "merge-base", "check-ref-format", "rev-list", "diff")


def rd(path, mode="r"):
    with open(path, mode) as fh:
        return fh.read()


def wr(path, text, mode="w"):
    with open(path, mode) as fh:
        fh.write(text)


def is_write(argv):
    sub = argv[3]
    if sub in READ_ONLY:
        return False
    if sub == "worktree":
        return argv[4] != "list"
    return True


class GitBase(core.Base):
    def setUp(self):
        core.Base.setUp(self)
        self.calls = []
        self.hooks = []
        real = merge._exec

        def recorder(argv, timeout):
            self.calls.append(list(argv))
            for h in self.hooks:
                if not h["fired"] and h["pred"](argv):
                    h["fired"] = True
                    res = h["action"](argv)
                    if res is not None:
                        return res
            return real(argv, timeout)

        p = mock.patch.object(merge, "_exec", recorder)
        p.start()
        self.addCleanup(p.stop)
        self.projects = os.path.join(self.tmp, "Projects")
        os.makedirs(self.projects)
        self.repo = os.path.join(self.projects, "myapp")
        self.wt = os.path.join(self.projects, "myapp-worktrees", "orch-add-login")
        self.d = self.tdir()
        self.n = 0

    def tearDown(self):
        """Every git call of the test: -C under the temp root, no forbidden argument, --no-ff on every merge
        (except --abort), branch only with -d."""
        root = os.path.realpath(self.tmp)
        for argv in self.calls:
            self.assertEqual(argv[:2], ["git", "-C"], argv)
            path = os.path.realpath(argv[2])
            self.assertTrue(path == root or path.startswith(root + os.sep), argv)
            args = argv[3:]
            for a in args:
                self.assertNotIn(a, NEVER, argv)
                self.assertFalse(a.startswith("--force"), argv)
            if args[0] == "merge" and "--abort" not in args:
                self.assertIn("--no-ff", args, argv)
            if args[0] == "branch":
                self.assertEqual(args[1], "-d", argv)
        core.Base.tearDown(self)

    # -- direct git (not recorded) -----------------------------------------
    def git(self, cwd, *args, **kw):
        env = dict(os.environ)
        env.update({"HOME": self.home, "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            env.pop(k, None)
        r = subprocess.run(["git", "-C", cwd] + list(args), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        if kw.get("check", True) and r.returncode != 0:
            raise AssertionError("git %s failed: %s%s" % (" ".join(args), r.stdout, r.stderr))
        return r.stdout.strip()

    def init_repo(self, path):
        os.makedirs(path)
        self.git(path, "init", "-q", "-b", "main")
        for k, v in (("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            self.git(path, "config", k, v)
        self.commit(path, "a.txt", "one\n")

    def commit(self, cwd, name, text, msg=None):
        with open(os.path.join(cwd, name), "w") as fh:
            fh.write(text)
        self.git(cwd, "add", name)
        self.git(cwd, "commit", "-q", "-m", msg or "edit %s" % name)
        return self.git(cwd, "rev-parse", "HEAD")

    def add_worktree(self, repo, path, branch):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.git(repo, "worktree", "add", "-q", "-b", branch, path)

    def setup_task(self, commit=True, slug="add-login", state="reviewing"):
        """Repo + worktree on orch/<slug> with one commit + a task dir pointing at them."""
        self.init_repo(self.repo)
        self.wt = os.path.join(self.projects, "myapp-worktrees", "orch-%s" % slug)
        self.add_worktree(self.repo, self.wt, "orch/%s" % slug)
        if commit:
            self.commit(self.wt, "feature.txt", "new\n")
        self.ok("init", "myapp", slug, "--repo-path", self.repo, "--base", "main", "--mode", "standard",
                "--request", "Add a login page")
        self.ok("set", "worktree=%s" % self.wt, "task_state=%s" % state, "-t", "myapp/%s" % slug)
        self.d = self.tdir("myapp", slug)

    def drop_worktree(self, value):
        """Turn the setup into a task without a worktree: the branch lives on in the main repo."""
        self.git(self.repo, "worktree", "remove", "--force", self.wt)
        self.git(self.repo, "branch", "-q", "-D", "orch/add-login")
        self.git(self.repo, "branch", "-q", "orch/add-login", "main")
        self.git(self.repo, "checkout", "-q", "orch/add-login")
        self.commit(self.repo, "feature.txt", "new\n")
        self.git(self.repo, "checkout", "-q", "main")
        self.set_field(worktree=value)

    def orch_branches(self, repo=None):
        return self.git(repo or self.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/orch/").split()

    def tip(self, branch="orch/add-login"):
        return self.git(self.repo, "rev-parse", "refs/heads/%s" % branch)

    def cmd(self, *args):
        return self.run_orch(*(list(args) + ["-t", T]))

    def cmd_ok(self, *args):
        rc, out, err = self.cmd(*args)
        self.assertEqual(rc, 0, "args=%r out=%s err=%s" % (args, out, err))
        return out, err

    # -- journal helpers -----------------------------------------------------
    def gate(self, sha=None, verdict="pass", ms=None):
        args = ["journal", "final_gate", "verdict=%s" % verdict, "sha=%s" % (sha or self.tip()), "command=make test"]
        self.cmd_ok(*args)

    def brief(self, reviewer, rnd, sha, ms=None, lines=None):
        name = "review-%s-m%02d-r%d.md" % (reviewer, ms, rnd) if ms else "review-%s-r%d.md" % (reviewer, rnd)
        path = os.path.join(self.d, "briefs", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        head = lines if lines is not None else (
            ["- reviewed sha: %s" % sha, "- round: %d" % rnd] + (["- milestone: %d" % ms] if ms else [])
            + ["- range: main...%s" % sha])
        with open(path, "w") as fh:
            fh.write("# Brief: %s review\n\n%s\n\n## Task\nreview it\n" % (reviewer, "\n".join(head)))
        return path

    def review(self, reviewer="code", rnd=1, sha=None, blockers=0, should_fix=0, nits=0, verdict="pass", ms=None,
               brief=True):
        sha = sha or self.tip()
        if brief:
            self.brief(reviewer, rnd, sha, ms)
        args = ["journal", "review_round", "reviewer=%s" % reviewer, "round:=%d" % rnd, "blockers:=%d" % blockers,
                "should_fix:=%d" % should_fix, "nits:=%d" % nits, "verdict=%s" % verdict]
        if ms:
            args.append("milestone:=%d" % ms)
        self.cmd_ok(*args)

    def raw_journal(self, rec):
        with open(os.path.join(self.d, "journal.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def ready(self):
        self.gate()
        self.review()

    # -- running merge -------------------------------------------------------
    def merge(self, *flags):
        return self.cmd("merge", *(flags or ("--recheck-ok",)))

    def task_snapshot(self):
        h = []
        for n in ("status.md", "log.md", "journal.jsonl"):
            p = os.path.join(self.d, n)
            h.append(rd(p, "rb") if os.path.exists(p) else b"-")
        return h

    def repo_snapshot(self):
        return [self.git(self.repo, "for-each-ref"), self.git(self.repo, "status", "--porcelain"),
                self.git(self.repo, "worktree", "list"), self.git(self.repo, "rev-parse", "HEAD")]

    def writes_since(self, n):
        return [c for c in self.calls[n:] if is_write(c)]

    def refuse_merge(self, *needles, **kw):
        flags = kw["flags"] if "flags" in kw else ("--recheck-ok",)
        code = kw.get("code", 2)
        before = (self.task_snapshot(), self.repo_snapshot())
        n = len(self.calls)
        rc, out, err = self.cmd("merge", *flags)
        self.assertEqual(rc, code, "out=%s err=%s" % (out, err))
        for needle in needles:
            self.assertIn(needle, err)
        self.assertEqual(self.writes_since(n), [], "a git write ran for a refused merge")
        self.assertEqual((self.task_snapshot(), self.repo_snapshot()), before)
        return err

    def merge_ok(self, *flags):
        rc, out, err = self.merge(*flags)
        self.assertEqual(rc, 0, "out=%s err=%s" % (out, err))
        return out, err

    def journal(self):
        return self.journal_lines(self.d)

    def kinds(self, kind):
        return [r for r in self.journal() if r["kind"] == kind]

    def before_call(self, pred, action):
        self.hooks.append({"pred": pred, "action": action, "fired": False})

    def set_field(self, **kv):
        self.ok("set", *["%s=%s" % kv_ for kv_ in kv.items()] + ["-t", T])


class TestPolicy(GitBase):
    def policy_file(self, text):
        with open(os.path.join(self.root, "myapp", "policy.md"), "w") as fh:
            fh.write(text)

    def test_missing_file_and_fields_mean_auto_rule(self):
        self.setup_task()
        p = policy.read_policy(self.d)
        self.assertEqual((p.merge, p.security, p.exists), ("auto", "rule", False))
        self.policy_file("# Policy: myapp\n\n- security: always\n")
        p = policy.read_policy(self.d)
        self.assertEqual((p.merge, p.security, p.exists), ("auto", "always", True))

    def test_values_and_fail_closed_reading(self):
        self.setup_task()
        cases = [("- merge: auto\n", "auto"), ("- merge: ask\n", "ask"), ("- merge: maybe\n", "maybe"),
                 ("- merge: auto | ask\n", "auto | ask"),                      # unedited template text
                 ("- merge: auto\n- merge: ask\n", "ask"),                      # any non-auto line wins
                 ("- merge:\n", "(empty)"),
                 ("<!-- - merge: ask -->\n- merge: auto\n", "auto"),            # comments are not policy
                 ("<!--\n- merge: ask\n-->\n", "auto"),
                 ("- MERGE: Ask\n", "Ask"),                                      # not exactly `auto`
                 ("- merge: auto # trailing\n", "auto # trailing")]
        for text, want in cases:
            self.policy_file(text)
            self.assertEqual(policy.read_policy(self.d).merge, want, text)
        self.policy_file("- security: whatever\n")
        self.assertEqual(policy.read_policy(self.d).security, "ask")
        self.policy_file("- security: always\n- security: ask\n")
        self.assertEqual(policy.read_policy(self.d).security, "ask")

    def test_unreadable_policy_asks(self):
        self.setup_task()
        os.makedirs(os.path.join(self.root, "myapp", "policy.md"))   # a directory where the file should be
        self.assertEqual(policy.read_policy(self.d).merge, "ask")

    def test_template_matches_the_contract(self):
        path = os.path.join(core.BIN, "..", "skills", "orchestrate", "templates", "policy.md")
        text = rd(path)
        self.assertTrue(text.startswith("# Policy: <repo-name>\n"))
        self.assertIn("- merge: auto | ask\n", text)
        self.assertIn("- security: rule | always | ask\n", text)
        self.assertEqual(text.count("<!--"), 1)
        self.assertIn("absent file means `merge: auto` and `security: rule`", text)

    def test_merge_refuses_on_ask_unknown_and_template_text(self):
        self.setup_task()
        self.ready()
        for text in ("- merge: ask\n", "- merge: sometimes\n", "- merge: auto | ask\n"):
            self.policy_file(text)
            self.refuse_merge("policy")
        self.policy_file("- merge: auto\n")
        self.merge_ok("--check", "--recheck-ok")

    def test_policy_applies_per_repo_directory(self):
        self.setup_task()
        self.ready()
        other = os.path.join(self.root, "otherapp")
        os.makedirs(other)
        with open(os.path.join(other, "policy.md"), "w") as fh:
            fh.write("- merge: ask\n")
        self.merge_ok("--check", "--recheck-ok")


class TestMergeAndCleanup(GitBase):
    def test_clean_merge_and_cleanup(self):
        self.setup_task()
        self.ready()
        base_before = self.tip("main")
        tip = self.tip()
        out, err = self.merge_ok()
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 3)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.git(self.repo, "rev-list", "--parents", "-n", "1", "HEAD").split(), [head, base_before, tip])
        self.assertTrue(lines[0].startswith("merged %s into main (orch/add-login)" % head[:9]), lines)
        revert = "git -C %s revert -m 1 %s" % (shlex.quote(self.repo), head)
        self.assertEqual(lines[1], "revert: " + revert)
        self.assertEqual(lines[2], "cleanup: worktree removed, branch deleted")
        self.assertEqual(err, "")
        self.assertFalse(os.path.exists(self.wt))
        self.assertEqual(self.orch_branches(), [])
        self.assertNotIn("orch-add-login", self.git(self.repo, "worktree", "list"))
        s = st.Status.load(os.path.join(self.d, "status.md"))
        self.assertEqual(s.get("worktree"), "removed after merge (%s)" % head[:7])
        am = self.kinds("auto_merge")
        self.assertEqual(len(am), 1)
        self.assertEqual((am[0]["sha"], am[0]["base"], am[0]["branch"], am[0]["revert"]), (head, "main", "orch/add-login", revert))
        # the printed revert works in the throwaway repo and restores the previous tree
        self.git(self.repo, "revert", "-m", "1", "--no-edit", head)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD^{tree}"), self.git(self.repo, "rev-parse", base_before + "^{tree}"))

    def test_guard_reads_come_before_the_first_write(self):
        self.setup_task()
        self.ready()
        n = len(self.calls)
        self.merge_ok()
        calls = self.calls[n:]
        first = min(i for i, c in enumerate(calls) if is_write(c))
        self.assertEqual(calls[first][3], "merge")
        for i, c in enumerate(calls):
            if c[3] in ("check-ref-format", "symbolic-ref", "status"):
                self.assertLess(i, first, c)
            if c[3] == "merge-base" and i > first:
                self.assertEqual(c[-1], "HEAD")                      # only the post-merge ancestry check
        self.assertEqual([c[3:5] for c in calls if is_write(c)][1:], [["worktree", "remove"], ["branch", "-d"]])

    def test_merge_pins_the_gated_sha_and_keeps_the_branch_message(self):
        self.setup_task()
        self.ready()
        self.merge_ok()
        mcalls = [c for c in self.calls if c[3] == "merge"]
        self.assertEqual(len(mcalls), 1)
        self.assertEqual(mcalls[0][3:6], ["merge", "--no-ff", "--no-edit"])
        self.assertTrue(re.match(r"^[0-9a-f]{40}$", mcalls[0][-1]))
        self.assertEqual(self.git(self.repo, "log", "-1", "--format=%s"), "Merge branch 'orch/add-login'")

    def test_repo_path_with_a_space(self):
        self.repo = os.path.join(self.projects, "My App")
        self.setup_task()
        self.ready()
        out, _ = self.merge_ok()
        self.assertIn("revert: git -C '%s' revert -m 1 " % self.repo, out)
        self.assertFalse(os.path.exists(self.wt))

    def test_without_recheck_ok_and_every_failure_is_listed(self):
        self.setup_task(state="implementing")
        err = self.refuse_merge("merge refused (", "task_state", "final gate", "code review", "sensitive re-check", flags=())
        self.assertGreaterEqual(err.count("FAIL"), 4)

    def test_check_prints_the_table_and_writes_nothing(self):
        self.setup_task()
        self.ready()
        before = (self.task_snapshot(), self.repo_snapshot(), sorted(os.listdir(self.d)))
        n = len(self.calls)
        out, _ = self.merge_ok("--check", "--recheck-ok")
        self.assertEqual(self.writes_since(n), [])
        for label in ("policy", "task_state", "final gate", "code review", "security", "roadmap stops",
                      "sensitive re-check", "checkouts", "base contained", "task branch"):
            self.assertIn(label, out)
        self.assertNotIn("FAIL", out)
        self.assertEqual((self.task_snapshot(), self.repo_snapshot(), sorted(os.listdir(self.d))), before)
        rc, out, _ = self.merge("--check")                      # no --recheck-ok: listed, exit 2
        self.assertEqual(rc, 2)
        self.assertIn("FAIL", out)
        self.assertEqual(self.writes_since(n), [])
        self.assertEqual(self.tip("main"), self.git(self.repo, "rev-parse", "HEAD"))

    def test_state_may_be_awaiting_merge(self):
        self.setup_task(state="awaiting_merge")
        self.ready()
        self.merge_ok("--check", "--recheck-ok")

    def test_state_other_than_reviewing_or_awaiting_merge_refuses(self):
        self.setup_task(state="implementing")
        self.ready()
        for state in ("planning", "implementing", "done", "paused_usage_limit", "abandoned"):
            self.set_field(task_state=state)
            self.refuse_merge("task_state")
        self.set_field(task_state="reviewing")
        self.merge_ok("--check", "--recheck-ok")

    def test_development_guard_needs_orch_dir_for_a_real_merge(self):
        self.setup_task()
        self.ready()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([sys.executable, core.LAUNCHER, "merge", "--recheck-ok", "-t", self.d], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("development copy: set ORCH_DIR", r.stderr)
        self.assertEqual(self.tip("main"), self.git(self.repo, "rev-parse", "HEAD"))


class TestReviewAndGate(GitBase):
    def test_final_gate_must_be_pass_and_at_the_tip(self):
        self.setup_task()
        self.review()
        self.refuse_merge("final gate", "no final_gate line")
        old = self.tip()
        self.gate(sha=old, verdict="fail")
        self.refuse_merge("final gate", "verdict is 'fail'")
        self.gate(sha=old)
        self.merge_ok("--check", "--recheck-ok")
        self.commit(self.wt, "more.txt", "x\n")                  # the branch moves on: the gate is stale
        self.refuse_merge("final gate", "gate ran on %s" % old[:9])
        self.review()                                           # fresh review at the new tip is not enough
        self.refuse_merge("final gate")
        self.gate()
        self.merge_ok("--check", "--recheck-ok")

    def test_the_latest_gate_line_decides(self):
        self.setup_task()
        self.review()
        self.gate()
        self.gate(verdict="fail")
        self.refuse_merge("final gate")

    def test_review_sha_must_equal_the_tip(self):
        self.setup_task()
        self.review()
        self.commit(self.wt, "more.txt", "x\n")
        self.gate()
        self.refuse_merge("code review", "reviewed")

    def test_review_counts_and_verdict(self):
        self.setup_task()
        self.gate()
        cases = [dict(blockers=1), dict(should_fix=1), dict(verdict="findings"), dict(verdict="fail")]
        for i, kw in enumerate(cases, 1):
            self.review(rnd=i, **kw)
            self.refuse_merge("code review")
        self.review(rnd=9, nits=3)                               # nits don't block a code review
        self.merge_ok("--check", "--recheck-ok")

    def test_a_later_failing_round_supersedes_an_earlier_pass(self):
        self.setup_task()
        self.gate()
        self.review(rnd=1)
        self.merge_ok("--check", "--recheck-ok")
        self.review(rnd=2, should_fix=1)
        self.refuse_merge("code review")

    def test_a_security_review_never_stands_in_for_the_code_review(self):
        self.setup_task()
        self.gate()
        self.review(reviewer="security")
        self.refuse_merge("code review", "no code review_round line")

    def test_old_and_hand_written_review_lines_never_satisfy(self):
        self.setup_task()
        self.gate()
        tip = self.tip()
        base = {"ts": "2026-01-01T00:00Z", "repo": "myapp", "task": "add-login", "kind": "review_round",
                "round": 1, "reviewer": "code", "blockers": 0, "should_fix": 0, "nits": 0, "verdict": "pass"}
        hand = [dict(base),                                                       # no sha (old line)
                dict(base, sha=tip[:12]),                                         # abbreviated sha
                dict(base, sha=tip + "0"),                                        # not a sha
                dict(base, sha=tip, blockers="0"),                                # string count
                dict(base, sha=tip, should_fix=False),                            # bool count
                dict(base, sha=tip, verdict="Pass"),
                dict(base, sha=tip, blockers=None),
                dict(base, sha=tip, reviewer="Code"),
                {k: v for k, v in dict(base, sha=tip).items() if k != "should_fix"}]
        for i, rec in enumerate(hand):                                             # the latest line decides
            self.raw_journal(rec)
            self.refuse_merge("code review", *(("no full sha",) if i < 3 else ()))

    def hand_line(self, reviewer="code", rnd=1, sha=None, ms=None, **kw):
        rec = {"ts": "2026-01-01T00:00Z", "repo": "myapp", "task": "add-login", "kind": "review_round",
               "round": rnd, "reviewer": reviewer, "blockers": 0, "should_fix": 0, "nits": 0, "verdict": "pass",
               "sha": sha or self.tip()}
        if ms:
            rec["milestone"] = ms
        rec.update(kw)
        self.raw_journal(rec)

    def test_a_hand_written_review_line_without_a_brief_never_satisfies(self):
        self.setup_task()
        self.gate()
        self.hand_line()                                           # sha == tip, no briefs/ file at all
        self.refuse_merge("code review", "round 1: no valid review brief")

    def test_a_review_line_must_carry_the_sha_of_its_rounds_brief(self):
        self.setup_task()
        self.gate()
        briefed = self.tip()
        self.brief("code", 1, briefed)
        self.commit(self.wt, "more.txt", "x\n")                    # the branch moves past what was briefed
        self.gate()
        self.hand_line(sha=self.tip())                             # re-review journaled by hand at the new tip
        self.refuse_merge("code review", "differs from the review brief's reviewed sha %s" % briefed[:9])
        self.brief("code", 1, self.tip())                          # only a brief at that sha makes the line valid
        self.merge_ok("--check", "--recheck-ok")

    def test_a_review_line_with_an_unreadable_brief_or_round_fails(self):
        self.setup_task()
        self.gate()
        self.brief("code", 1, self.tip(), lines=["- round: 1"])    # no reviewed-sha header
        self.hand_line()
        self.refuse_merge("code review", "reviewed sha:")
        self.brief("code", 2, self.tip())
        self.hand_line(rnd="2")                                    # round is not an integer
        self.refuse_merge("code review", "no usable round")
        self.hand_line(rnd=0)
        self.refuse_merge("code review", "no usable round")

    def test_a_brief_for_another_reviewer_or_round_does_not_count(self):
        self.setup_task()
        self.gate()
        self.brief("security", 1, self.tip())
        self.brief("code", 2, self.tip())
        self.hand_line(rnd=1)
        self.refuse_merge("code review", "round 1: no valid review brief")

    def test_damaged_journal_line_is_a_refusal(self):
        self.setup_task()
        self.ready()
        with open(os.path.join(self.d, "journal.jsonl"), "a") as fh:
            fh.write('{"ts":"2026-01-01T00:00Z","kind":"security_offer","answer":"accep\n')
        err = self.refuse_merge("journal", "unreadable line")
        self.assertIn("line", err)

    def test_a_journal_line_that_is_not_an_object_is_a_refusal(self):
        self.setup_task()
        self.ready()
        with open(os.path.join(self.d, "journal.jsonl"), "a") as fh:
            fh.write("[1, 2]\n")
        self.refuse_merge("unreadable")


class TestSecurityCondition(GitBase):
    """Fail closed: any signal in scope makes a security review due; `ran:false` never cancels one."""

    def setUp(self):
        GitBase.setUp(self)
        self.setup_task()
        self.ready()

    def offer(self, answer="accepted", **kw):
        self.cmd_ok("journal", "security_offer", "areas=auth", "answer=" + answer, "late:=%s" % kw.get("late", "false"))

    def auto(self, ran, rule="step5", ms=None):
        args = ["journal", "auto_security", "areas=auth", "ran:=%s" % ran, "rule=%s" % rule]
        if ms is not None:
            args.append("milestone:=%d" % ms)
        self.cmd_ok(*args)

    def spawn_sec(self):
        self.cmd_ok("journal", "spawn", "role=security-reviewer", "agent=orch-security-reviewer", "tier=opus/high",
                    "steps:=null", "usage_5h:=1", "usage_7d:=1")

    def due(self):
        self.refuse_merge("security")

    def test_not_due_with_nothing_to_report(self):
        self.merge_ok("--check", "--recheck-ok")

    def test_ran_false_does_not_hide_a_pending_row(self):
        self.auto("false")
        self.set_role("pending")
        self.due()

    def set_role(self, state):
        self.cmd_ok("role", "security-reviewer", state)

    def test_ran_false_then_a_late_accepted_offer(self):
        self.auto("false")
        self.offer(late="true")
        self.due()

    def test_ran_false_then_a_security_reviewer_spawn(self):
        self.auto("false")
        self.spawn_sec()
        self.due()

    def test_a_spawn_by_role_alone_counts(self):
        self.cmd_ok("journal", "spawn", "role=security-reviewer", "agent=orch-implementer", "tier=opus/high",
                    "steps:=null", "usage_5h:=1", "usage_7d:=1")
        self.due()

    def test_a_spawn_by_agent_name_alone_counts(self):
        self.cmd_ok("journal", "spawn", "role=reviewer", "agent=orch-security-reviewer", "tier=opus/high",
                    "steps:=null", "usage_5h:=1", "usage_7d:=1")
        self.due()

    def test_ran_false_then_ran_true(self):
        self.auto("false")
        self.auto("true", rule="request")
        self.due()

    def test_ran_true_then_a_later_ran_false(self):
        self.auto("true")
        self.auto("false", rule="late")
        self.due()

    def test_ran_false_with_a_quiet_row_is_not_due(self):
        self.auto("false")
        self.set_role("not_offered")
        self.merge_ok("--check", "--recheck-ok")
        for state in ("declined", "n/a"):
            self.set_role(state)
            self.merge_ok("--check", "--recheck-ok")

    def test_declined_row_without_signals_is_not_due(self):
        self.set_role("declined")
        self.merge_ok("--check", "--recheck-ok")

    def test_declined_offer_is_not_a_signal(self):
        self.offer("declined")
        self.offer("not_offered")
        self.merge_ok("--check", "--recheck-ok")

    def test_row_states_that_signal(self):
        for state in ("pending", "in_progress", "waiting", "blocked", "failed", "done", "skipped"):
            self.set_role(state)
            self.due()

    def test_row_done_and_no_review_line_refuses(self):
        self.set_role("done")
        self.due()

    def test_unreadable_role_table_is_due(self):
        path = os.path.join(self.d, "status.md")
        text = rd(path)
        wr(path, text.replace("| role |", "| who |"))
        self.due()

    def test_row_with_a_stray_pipe_is_still_read(self):
        path = os.path.join(self.d, "status.md")
        text = rd(path)
        text = re.sub(r"\| security-reviewer \| not_offered \|.*\|", "| security-reviewer | pending | | | a | b |", text)
        wr(path, text)
        self.due()

    def test_a_due_review_must_pass_at_the_tip_with_no_findings(self):
        self.set_role("done")
        self.review("security", nits=1)
        self.refuse_merge("security", "nits is 1")
        self.review("security", rnd=2, should_fix=1)
        self.refuse_merge("security", "should_fix is 1")
        self.review("security", rnd=3, blockers=1)
        self.refuse_merge("security", "blockers is 1")
        self.review("security", rnd=4, verdict="findings")
        self.refuse_merge("security")
        self.review("security", rnd=5)
        self.merge_ok("--check", "--recheck-ok")

    def test_a_due_review_for_an_older_tip_refuses(self):
        self.set_role("done")
        self.review("security")
        self.commit(self.wt, "more.txt", "x\n")
        self.gate()
        self.review()
        self.refuse_merge("security", "reviewed")

    def test_a_code_review_does_not_satisfy_a_due_security_review(self):
        self.set_role("done")
        self.refuse_merge("no security review_round line")

    def policy_file(self, text):
        with open(os.path.join(self.root, "myapp", "policy.md"), "w") as fh:
            fh.write(text)

    def test_policy_security_always_makes_a_review_due(self):
        self.policy_file("- security: always\n")
        self.refuse_merge("security", "policy security: always", "no security review_round line")
        self.review("security")                                  # a passing round at the tip satisfies it
        self.merge_ok("--check", "--recheck-ok")

    def test_policy_security_always_needs_the_review_even_with_a_quiet_row(self):
        self.policy_file("- security: always\n")
        self.set_role("declined")
        self.due()
        self.auto("false", rule="policy")                        # ran:false never cancels it
        self.due()

    def test_policy_security_rule_and_ask_add_no_signal_of_their_own(self):
        for text in ("- security: rule\n", "- security: ask\n", ""):
            self.policy_file(text)
            self.merge_ok("--check", "--recheck-ok")

    def test_a_security_line_must_carry_the_sha_of_its_rounds_brief(self):
        self.auto("true")
        rec = {"ts": "2026-01-01T00:00Z", "repo": "myapp", "task": "add-login", "kind": "review_round",
               "round": 1, "reviewer": "security", "blockers": 0, "should_fix": 0, "nits": 0, "verdict": "pass",
               "sha": self.tip()}
        self.raw_journal(rec)                                    # no brief
        self.refuse_merge("security", "round 1: no valid review brief")
        old = self.tip()
        self.brief("security", 1, old)
        self.commit(self.wt, "more.txt", "x\n")
        self.gate()
        self.review()
        self.raw_journal(dict(rec, sha=self.tip()))              # briefed at old, line at the new tip
        self.refuse_merge("security", "differs from the review brief's reviewed sha %s" % old[:9])

    def test_a_due_review_still_stops_a_real_merge(self):
        self.auto("true")
        self.refuse_merge("security")
        self.assertEqual(self.tip("main"), self.git(self.repo, "rev-parse", "HEAD"))
        self.assertTrue(os.path.exists(self.wt))


class TestRoadmapStopsAndSkips(GitBase):
    def program(self, you="-", review="code", rows=3, ms=1, extra_rows=""):
        self.setup_task()
        self.set_field(shape="roadmap", milestone="M%d — reviewing" % ms)
        table = ["| M | goal | depends on | mode | tiers | sensitive | review | merge | you | size | confidence |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
        for m in range(1, rows + 1):
            table.append("| %d | goal %d | - | standard | sonnet/high | - | %s | yes | %s | M | high |"
                         % (m, m, review, you if m == ms else "-"))
        with open(os.path.join(self.d, "roadmap.md"), "w") as fh:
            fh.write("# Roadmap: add-login\n\n## Milestones\n\n" + "\n".join(table) + "\n" + extra_rows + "\n## Contracts\n")

    def prog_ready(self, ms=1):
        self.gate()
        self.review(ms=ms)

    def set_you(self, cell):
        path = os.path.join(self.d, "roadmap.md")
        text = re.sub(r"(\| 1 \| goal 1 \| - \| standard \| sonnet/high \| - \| \w+ \| yes \| )[^|]*( \|)",
                      lambda m: m.group(1) + cell + m.group(2), rd(path))
        wr(path, text)

    def test_you_cells_that_name_a_merge_stop(self):
        self.program()
        self.prog_ready()
        for cell in ("merge; archive GitHub repos", "eye-check guided setup on a local run; merge", "MERGE", "merge",
                     "decision D-3, then Merge now"):
            self.set_you(cell)
            self.refuse_merge("`you` cell", "merge stop")

    def test_you_cells_that_do_not(self):
        self.program()
        self.prog_ready()
        for cell in ("-", "R2 test bucket", "mock; eye-check", "merged earlier", "emerge"):
            self.set_you(cell)
            self.merge_ok("--check", "--recheck-ok")

    def test_a_pipe_inside_the_goal_cannot_hide_the_you_cell(self):
        self.program(you="merge")
        path = os.path.join(self.d, "roadmap.md")
        text = rd(path).replace("| 1 | goal 1 |", "| 1 | goal | with a pipe |")
        wr(path, text)
        self.prog_ready()
        self.refuse_merge("merge stop")

    def test_a_short_roadmap_row_is_a_refusal(self):
        self.program()
        path = os.path.join(self.d, "roadmap.md")
        text = rd(path).replace("| 1 | goal 1 | - | standard | sonnet/high | - | code | yes | - | M | high |",
                                          "| 1 | goal 1 | - |")
        wr(path, text)
        self.prog_ready()
        self.refuse_merge("cannot be read")

    def test_roadmap_missing_or_without_the_milestone_row(self):
        self.program()
        self.prog_ready()
        os.rename(os.path.join(self.d, "roadmap.md"), os.path.join(self.d, "roadmap.old"))
        self.refuse_merge("roadmap.md is missing")
        os.rename(os.path.join(self.d, "roadmap.old"), os.path.join(self.d, "roadmap.md"))
        self.set_field(milestone="M7 — reviewing")
        self.refuse_merge("no row for milestone 7")

    def test_single_plan_skip_needs_row_and_override(self):
        self.setup_task()
        self.gate()
        self.cmd_ok("role", "code-reviewer", "skipped")
        self.refuse_merge("code review", "no override")
        self.cmd_ok("journal", "override", "what=skip it")                    # no word `review`
        self.refuse_merge("code review")
        self.cmd_ok("journal", "override", "what=Skip the code REVIEW: docs-only change")
        self.merge_ok("--check", "--recheck-ok")

    def test_an_override_without_the_skipped_row_does_not_skip(self):
        self.setup_task()
        self.gate()
        self.cmd_ok("journal", "override", "what=skip code review")
        self.refuse_merge("code review")
        self.cmd_ok("role", "code-reviewer", "done")
        self.refuse_merge("code review")

    def test_program_skip_through_the_latest_milestone_plan(self):
        self.program()
        self.gate()
        self.refuse_merge("code review")
        plan = lambda rev, review: self.cmd_ok("journal", "milestone_plan", "revision:=%d" % rev,   # noqa: E731
                                               "rows:=" + json.dumps([{"M": "1", "review": review}, {"M": "2", "review": "code"}]))
        plan(0, "code")
        self.refuse_merge("code review")
        plan(1, "skip")
        self.merge_ok("--check", "--recheck-ok")
        plan(2, "code+sec")                                                                         # the latest line decides
        self.refuse_merge("code review")

    def test_program_skip_row_of_another_milestone_does_not_count(self):
        self.program()
        self.gate()
        self.cmd_ok("journal", "milestone_plan", "revision:=1",
                    "rows:=" + json.dumps([{"M": 1, "review": "code"}, {"M": 2, "review": "skip"}]))
        self.refuse_merge("code review")

    def test_program_final_gate_is_scoped_to_the_milestone(self):
        self.program(ms=1)
        self.prog_ready(ms=1)
        self.merge_ok("--check", "--recheck-ok")
        self.set_field(milestone="M2 — reviewing")
        self.review(ms=2)
        self.refuse_merge("final gate", "no final_gate line for M2")
        self.gate()                                                      # auto-filled with milestone 2
        self.merge_ok("--check", "--recheck-ok")

    def test_program_reviews_are_scoped_to_the_current_milestone(self):
        self.program(ms=2)
        self.gate()
        self.review(ms=1)                                                 # an M1 review does not pass M2
        self.refuse_merge("code review", "no code review_round line for M2")
        self.review(ms=2)
        self.merge_ok("--check", "--recheck-ok")

    def test_program_security_scope(self):
        self.program(ms=5, rows=6)
        self.prog_ready(ms=5)
        # M3 had a security run; M5's own line says ran:false and its row was reset: not due
        self.cmd_ok("journal", "auto_security", "areas=auth", "ran:=true", "rule=step5", "milestone:=3")
        self.cmd_ok("journal", "spawn", "role=security-reviewer", "agent=orch-security-reviewer", "tier=opus/high",
                    "steps:=null", "usage_5h:=1", "usage_7d:=1", "milestone:=3")
        self.cmd_ok("journal", "auto_security", "areas=-", "ran:=false", "rule=step5")
        self.merge_ok("--check", "--recheck-ok")
        # an old roadmap-gate line with a null milestone counts for every milestone
        self.cmd_ok("journal", "security_offer", "areas=auth", "answer=accepted", "late:=false", "milestone:=null")
        self.refuse_merge("security")

    def test_program_null_milestone_auto_security_run_still_counts(self):
        self.program(ms=2)
        self.prog_ready(ms=2)
        self.raw_journal({"ts": "2026-01-01T00:00Z", "repo": "myapp", "task": "add-login", "kind": "auto_security",
                          "areas": ["auth"], "ran": True, "rule": "step5", "milestone": None})
        self.refuse_merge("security")

    def test_program_unreadable_milestone_on_a_signal_counts(self):
        self.program(ms=2)
        self.prog_ready(ms=2)
        self.raw_journal({"ts": "2026-01-01T00:00Z", "repo": "myapp", "task": "add-login", "kind": "security_offer",
                          "areas": ["auth"], "answer": "accepted", "late": False, "milestone": "two"})
        self.refuse_merge("security")

    def test_program_old_style_accepted_offer_and_pending_row(self):
        self.program(ms=2)
        self.prog_ready(ms=2)
        self.cmd_ok("role", "security-reviewer", "pending")                  # an old program never reset the row
        self.refuse_merge("security-reviewer row is 'pending'")

    def test_auto_security_in_a_program_needs_a_resolvable_milestone(self):
        self.program()
        self.set_field(milestone="n/a")
        n = len(rd(os.path.join(self.d, "journal.jsonl")).splitlines())
        rc, _, err = self.cmd("journal", "auto_security", "areas=x", "ran:=true", "rule=step5")
        self.assertEqual(rc, 1, err)
        self.assertEqual(len(rd(os.path.join(self.d, "journal.jsonl")).splitlines()), n)
        rc, _, err = self.cmd("journal", "auto_security", "areas=x", "ran:=true", "rule=step5", "milestone:=2")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.kinds("auto_security")[-1]["milestone"], 2)
        self.set_field(milestone="M3 — reviewing")                          # auto-fill from status
        self.cmd_ok("journal", "auto_security", "areas=x", "ran:=false", "rule=step5")
        self.assertEqual(self.kinds("auto_security")[-1]["milestone"], 3)
        self.cmd_ok("journal", "auto_security", "areas=x", "ran:=false", "rule=step5", "milestone:=9")   # explicit wins
        self.assertEqual(self.kinds("auto_security")[-1]["milestone"], 9)

    def test_single_plan_auto_security_needs_no_milestone(self):
        self.setup_task()
        self.cmd_ok("journal", "auto_security", "areas=x", "ran:=true", "rule=step5")


class TestShaBinding(GitBase):
    def setUp(self):
        GitBase.setUp(self)
        self.setup_task()

    def jlen(self):
        return len(rd(os.path.join(self.d, "journal.jsonl")).splitlines())

    def test_final_gate_needs_a_resolvable_sha(self):
        n = self.jlen()
        rc, _, err = self.cmd("journal", "final_gate", "verdict=pass", "command=make")
        self.assertEqual(rc, 1)
        self.assertIn("final_gate needs sha", err)
        for bad in ("0" * 40, "zz", "HEAD", "--upload-pack=x", "-x", "main", "orch/add-login", "abc", self.tip()[:6]):
            n_calls = len(self.calls)
            rc, _, err = self.cmd("journal", "final_gate", "verdict=pass", "sha=" + bad, "command=make")
            self.assertEqual(rc, 1, (bad, err))
            self.assertFalse(any(bad in a for c in self.calls[n_calls:] for a in c[3:] if bad in ("--upload-pack=x", "-x")))
        self.assertEqual(self.jlen(), n)

    def test_final_gate_stores_the_full_sha(self):
        tip = self.tip()
        self.cmd_ok("journal", "final_gate", "verdict=pass", "sha=" + tip[:10].upper(), "command=make")
        self.assertEqual(self.kinds("final_gate")[-1]["sha"], tip)
        self.cmd_ok("journal", "final_gate", "verdict=pass", "sha:=" + json.dumps(tip), "command=make")
        rc, _, _ = self.cmd("journal", "final_gate", "verdict=pass", "sha:=12", "command=make")
        self.assertEqual(rc, 1)
        rc, _, _ = self.cmd("journal", "final_gate", "verdict=pass", "sha:=null", "command=make")
        self.assertEqual(rc, 1)

    def test_other_kinds_are_not_touched(self):
        self.cmd_ok("journal", "override", "what=x")
        self.cmd_ok("journal", "checkpoint", "questions:=1", "asked:=false")

    def test_review_round_without_a_brief_is_refused(self):
        n = self.jlen()
        rc, _, err = self.cmd("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0",
                              "nits:=0", "verdict=pass")
        self.assertEqual(rc, 1)
        self.assertIn("no review brief", err)
        self.assertEqual(self.jlen(), n)

    def test_review_round_argument_rules(self):
        self.brief("code", 1, self.tip())
        wr(os.path.join(self.d, "briefs", "review-visual-r1.md"), "- reviewed sha: %s\n- round: 1\n" % self.tip())
        base = ["journal", "review_round", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass"]
        for extra in (["round:=1"], ["reviewer=code"], ["reviewer=visual", "round:=1"], ["reviewer=code", "round:=0"],
                      ["reviewer=code", "round=x"], ["reviewer=../code", "round:=1"], ["reviewer=code", "round:=2"]):
            rc, _, err = self.cmd(*(base + extra))
            self.assertEqual(rc, 1, (extra, err))
        self.assertEqual(self.kinds("review_round"), [])

    def test_the_sha_comes_from_the_brief_not_from_the_branch(self):
        a = self.tip()
        self.brief("code", 1, a)
        b = self.commit(self.wt, "later.txt", "x\n")
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(self.kinds("review_round")[-1]["sha"], a)
        self.gate(sha=b)
        self.refuse_merge("code review", "reviewed %s" % a[:9])

    def test_a_given_sha_that_differs_is_refused_and_a_matching_one_accepted(self):
        a = self.tip()
        self.brief("code", 1, a)
        b = self.commit(self.wt, "later.txt", "x\n")
        n = self.jlen()
        base = ["journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass"]
        for given in (b, b[:10], "0" * 40, "zz"):
            rc, _, err = self.cmd(*(base + ["sha=" + given]))
            self.assertEqual(rc, 1, (given, err))
        self.assertEqual(self.jlen(), n)
        self.cmd_ok(*(base + ["sha=" + a[:12]]))
        self.assertEqual(self.kinds("review_round")[-1]["sha"], a)

    def test_brief_header_problems(self):
        a = self.tip()
        base = ["journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass"]
        cases = {"two sha lines": ["- reviewed sha: %s" % a, "- reviewed sha: %s" % a],
                 "no sha line": ["- round: 1"],
                 "abbreviated sha": ["- reviewed sha: %s" % a[:10]],
                 "not hex": ["- reviewed sha: HEAD"],
                 "unknown commit": ["- reviewed sha: %s" % ("1" * 40)],
                 "round mismatch": ["- reviewed sha: %s" % a, "- round: 2"]}
        for name, lines in cases.items():
            self.brief("code", 1, a, lines=lines)
            rc, _, err = self.cmd(*base)
            self.assertEqual(rc, 1, (name, err))
        self.assertEqual(self.kinds("review_round"), [])

    def test_a_sha_line_in_the_body_is_not_the_header(self):
        a = self.tip()
        path = self.brief("code", 1, a, lines=["- round: 1"])
        with open(path, "a") as fh:
            fh.write("\n" * 70 + "- reviewed sha: %s\n" % a)
        rc, _, _ = self.cmd("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(rc, 1)

    def test_rounds_bind_their_own_brief(self):
        a = self.tip()
        self.brief("code", 1, a)
        b = self.commit(self.wt, "later.txt", "x\n")
        self.brief("code", 2, b)
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=2", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual([r["sha"] for r in self.kinds("review_round")], [a, b])
        self.brief("security", 1, b)
        self.cmd_ok("journal", "review_round", "reviewer=security", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(self.kinds("review_round")[-1]["sha"], b)

    def test_program_briefs_are_chosen_by_milestone_and_round(self):
        self.set_field(shape="roadmap", milestone="M5 — reviewing")
        a = self.tip()
        b = self.commit(self.wt, "later.txt", "x\n")
        self.brief("code", 2, a, ms=2)
        self.brief("code", 1, b, ms=5)
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        rec = self.kinds("review_round")[-1]
        self.assertEqual((rec["sha"], rec["milestone"]), (b, 5))
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=2", "milestone:=2", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        rec = self.kinds("review_round")[-1]
        self.assertEqual((rec["sha"], rec["milestone"]), (a, 2))
        rc, _, _ = self.cmd("journal", "review_round", "reviewer=code", "round:=2", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(rc, 1)                                           # M5 has no round-2 brief

    def test_program_brief_header_milestone_must_agree_and_must_resolve(self):
        self.set_field(shape="roadmap", milestone="M5 — reviewing")
        a = self.tip()
        self.brief("code", 1, a, ms=5, lines=["- reviewed sha: %s" % a, "- round: 1", "- milestone: 4"])
        args = ["journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass"]
        self.assertEqual(self.cmd(*args)[0], 1)
        self.set_field(milestone="n/a")
        self.assertEqual(self.cmd(*args)[0], 1)

    def test_the_repo_must_be_usable(self):
        self.brief("code", 1, self.tip())
        self.set_field(repo=os.path.join(self.tmp, "nowhere"))
        self.assertEqual(self.cmd("journal", "final_gate", "verdict=pass", "sha=" + "a" * 40, "command=x")[0], 1)
        self.assertEqual(self.cmd("journal", "review_round", "reviewer=code", "round:=1", "verdict=pass")[0], 1)
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.set_field(repo=plain)
        rc, _, err = self.cmd("journal", "final_gate", "verdict=pass", "sha=" + "a" * 40, "command=x")
        self.assertEqual(rc, 1)
        self.assertIn("not a git repository", err)


class TestBaseMoved(GitBase):
    def test_base_advanced_after_the_gate_refuses_with_the_message(self):
        self.setup_task()
        self.ready()
        self.commit(self.repo, "b.txt", "base moved\n")
        err = self.refuse_merge("base moved after the task branch was gated — ask the user")
        self.assertIn("base contained", err)

    def test_nothing_to_merge(self):
        self.setup_task(commit=False)
        self.ready()
        self.refuse_merge("nothing to merge")

    def test_base_merged_into_the_branch_is_fine(self):
        self.setup_task()
        self.commit(self.repo, "b.txt", "base moved\n")
        self.git(self.wt, "merge", "-q", "--no-edit", "main")
        self.ready()
        self.merge_ok()
        self.assertTrue(os.path.exists(os.path.join(self.repo, "b.txt")))


class TestRepoAndRefs(GitBase):
    def test_unsafe_base_branch_names_are_refused_before_any_git_call_with_them(self):
        self.setup_task()
        self.ready()
        self.git(self.repo, "checkout", "-q", "-b", "other")                   # so that `@{-1}` names a real branch
        self.git(self.repo, "checkout", "-q", "main")
        for bad in ("--upload-pack=x", "-x", "@{-1}", "a b", "x~1", "x:y", "a\\b", "-", "--"):
            self.set_field(base_branch=bad)
            n = len(self.calls)
            self.refuse_merge("base_branch", "not a safe branch name")
            self.assertFalse(any(a == bad or (len(bad) > 3 and bad in a) for c in self.calls[n:] for a in c[3:]), bad)   # never reaches git
        self.set_field(base_branch="main..x")                                    # git itself rejects this one
        self.refuse_merge("base_branch", "not a valid branch name")
        self.set_field(base_branch="orch/add-login")
        self.refuse_merge("same")
        self.set_field(base_branch="main")
        self.merge_ok("--check", "--recheck-ok")

    def test_the_value_rule_still_applies_to_the_base_branch(self):
        self.setup_task()
        self.ready()
        self.set_field(base_branch="main (direct, no worktree)")
        self.merge_ok("--check", "--recheck-ok")

    def test_task_branch_must_be_orch_slug(self):
        self.setup_task()
        self.ready()
        for bad in ("feature", "main", "orch/other", "--upload-pack=x", "-D", "orch/add-login/x", "unknown"):
            self.set_field(task_branch=bad)
            self.refuse_merge("task_branch")
        self.assertEqual(self.writes_since(0), [])

    def test_missing_task_branch_is_exit_1(self):
        self.setup_task()
        self.ready()
        self.git(self.repo, "worktree", "remove", "--force", self.wt)
        self.git(self.repo, "branch", "-D", "orch/add-login")
        self.refuse_merge("not found", code=1)

    def test_unusable_repos_are_exit_1(self):
        self.setup_task()
        self.ready()
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        sub = os.path.join(self.repo, "sub")
        os.makedirs(sub)
        needles = {plain: "not a git repository", sub: "not the top of a git repository", self.wt: "linked worktree"}
        for value in (os.path.join(self.tmp, "nowhere"), plain, sub, "unknown", self.wt, "<abs path>"):
            self.set_field(repo=value)
            self.refuse_merge(*([needles[value]] if value in needles else ["not found"]), code=1)
        self.set_field(repo=self.repo)
        self.merge_ok("--check", "--recheck-ok")

    def test_main_checkout_on_another_branch(self):
        self.setup_task()
        self.ready()
        self.git(self.repo, "checkout", "-q", "-b", "other")
        self.refuse_merge("main checkout is on refs/heads/other, not main")
        self.git(self.repo, "checkout", "-q", "--detach")
        self.refuse_merge("detached HEAD")

    def test_dirty_tracked_file_in_the_main_checkout(self):
        self.setup_task()
        self.ready()
        with open(os.path.join(self.repo, "a.txt"), "a") as fh:
            fh.write("local edit\n")
        self.refuse_merge("uncommitted changes")
        self.git(self.repo, "add", "a.txt")                       # staged is dirty too
        self.refuse_merge("uncommitted changes")

    def test_untracked_files_alone_do_not_block(self):
        self.setup_task()
        self.ready()
        with open(os.path.join(self.repo, "scratch.txt"), "w") as fh:
            fh.write("x")
        self.merge_ok()

    def test_merge_or_rebase_in_progress(self):
        self.setup_task()
        self.ready()
        gd = os.path.join(self.repo, ".git")
        for name, is_dir in (("MERGE_HEAD", False), ("CHERRY_PICK_HEAD", False), ("REVERT_HEAD", False),
                             ("rebase-merge", True), ("rebase-apply", True)):
            path = os.path.join(gd, name)
            if is_dir:
                os.makedirs(path)
            else:
                wr(path, self.tip() + "\n")
            self.refuse_merge("in progress")
            if is_dir:
                os.rmdir(path)
            else:
                os.unlink(path)
        self.merge_ok("--check", "--recheck-ok")

    def test_worktree_with_uncommitted_tracked_changes(self):
        self.setup_task()
        self.ready()
        with open(os.path.join(self.wt, "feature.txt"), "a") as fh:
            fh.write("edit\n")
        self.refuse_merge("worktree has uncommitted changes")

    def test_merge_in_progress_inside_the_worktree(self):
        self.setup_task()
        self.ready()
        gd = self.git(self.wt, "rev-parse", "--absolute-git-dir")
        wr(os.path.join(gd, "MERGE_HEAD"), self.tip() + "\n")
        self.refuse_merge("in progress in the worktree")
        os.unlink(os.path.join(gd, "MERGE_HEAD"))
        self.merge_ok("--check", "--recheck-ok")

    def test_worktree_from_another_repository(self):
        self.setup_task()
        self.ready()
        other = os.path.join(self.projects, "other")
        self.init_repo(other)
        other_wt = os.path.join(self.projects, "other-wt", "orch-add-login")
        self.add_worktree(other, other_wt, "orch/add-login")
        self.set_field(worktree=other_wt)
        self.refuse_merge("does not belong to this repository")
        self.assertTrue(os.path.exists(other_wt))
        self.assertEqual(self.orch_branches(other), ["orch/add-login"])

    def test_worktree_that_is_not_a_git_checkout(self):
        self.setup_task()
        self.ready()
        self.set_field(worktree=self.projects)                       # a directory, but not a worktree of this repo
        self.refuse_merge("does not belong to this repository")
        self.assertFalse(any(c[3:5] == ["worktree", "remove"] for c in self.calls))

    def test_worktree_that_is_another_branchs_checkout(self):
        self.setup_task()
        self.ready()
        stranger = os.path.join(self.projects, "myapp-worktrees", "orch-stranger")
        self.add_worktree(self.repo, stranger, "orch/stranger")
        self.set_field(worktree=stranger)
        self.refuse_merge("worktree is on refs/heads/orch/stranger")
        self.set_field(worktree=self.repo)
        self.refuse_merge("main checkout")
        self.assertTrue(os.path.exists(stranger))

    def test_worktree_forms_that_mean_none_still_merge_and_delete_the_branch(self):
        for value in ("none (user: no worktree)", "not yet created", "unknown", "relative/path"):
            with self.subTest(value=value):
                self.tearDown_world()
                self.setup_task()
                self.drop_worktree(value)
                self.ready()
                self.merge_ok()
                self.assertEqual(self.orch_branches(), [])
                self.assertEqual(st.Status.load(os.path.join(self.d, "status.md")).get("worktree"), value)

    def tearDown_world(self):
        """Fresh temp world for the next subTest of a loop."""
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(os.path.join(self.projects, "myapp-worktrees"), ignore_errors=True)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_git_dir_in_the_environment_cannot_redirect_the_merge(self):
        self.setup_task()
        self.ready()
        other = os.path.join(self.projects, "other")
        self.init_repo(other)
        before = self.git(other, "for-each-ref")
        env = {"GIT_DIR": os.path.join(other, ".git"), "GIT_WORK_TREE": other, "GIT_INDEX_FILE": os.path.join(other, ".git", "index")}
        rc, out, err = self.run_orch("merge", "--recheck-ok", "-t", T, env=env)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.git(other, "for-each-ref"), before)
        self.assertEqual(len(self.git(self.repo, "rev-list", "--parents", "-n", "1", "HEAD").split()), 3)

    def test_the_git_environment_is_scrubbed(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/x", "GIT_WORK_TREE": "/y", "GIT_INDEX_FILE": "/z", "KEEP": "1"}):
            env = merge._env()
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.assertNotIn(k, env)
        self.assertEqual((env["KEEP"], env["GIT_TERMINAL_PROMPT"], env["GIT_OPTIONAL_LOCKS"]), ("1", "0", "0"))


class TestMergeFailures(GitBase):
    def setUp(self):
        GitBase.setUp(self)
        self.setup_task()
        self.ready()

    def is_main_merge(self, argv):
        return argv[2] == self.repo and argv[3] == "merge" and "--abort" not in argv

    def test_untracked_colliding_file_refuses_without_changing_anything(self):
        mine = os.path.join(self.repo, "feature.txt")
        with open(mine, "w") as fh:
            fh.write("precious\n")
        before = self.repo_snapshot()
        rc, out, err = self.merge()
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.repo_snapshot(), before)
        self.assertIn("merge failed", err)
        self.assertIn("untracked", err)
        self.assertIn("repository unchanged", err)
        self.assertEqual(rd(mine), "precious\n")
        self.assertFalse(any(c[3:5] == ["merge", "--abort"] for c in self.calls))
        self.assertTrue(os.path.exists(self.wt))
        self.assertEqual(self.kinds("auto_merge"), [])

    def test_pre_merge_commit_hook_failure_aborts_and_restores_head_and_index(self):
        hook = os.path.join(self.repo, ".git", "hooks", "pre-merge-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        pre = self.git(self.repo, "rev-parse", "HEAD")
        before = self.repo_snapshot()
        rc, out, err = self.merge()
        self.assertEqual(rc, 2, err)
        self.assertIn("merge failed", err)
        self.assertIn("hook says no", err)
        self.assertIn("merge aborted", err)
        self.assertTrue(any(c[3:5] == ["merge", "--abort"] for c in self.calls))
        self.assertEqual(self.repo_snapshot(), before)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), pre)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))
        self.assertEqual(self.git(self.repo, "diff", "--cached", "--stat"), "")
        self.assertEqual(self.kinds("auto_merge"), [])
        self.assertTrue(os.path.exists(self.wt))
        self.assertEqual(self.orch_branches(), ["orch/add-login"])

    def test_a_real_conflict_in_the_race_window_is_aborted(self):
        # the task branch edits a.txt; main gets a conflicting edit after the guard's last read (the merge call)
        self.commit(self.wt, "a.txt", "task side\n")
        self.gate()
        self.review()
        moved = []

        def race(argv):
            moved.append(self.commit(self.repo, "a.txt", "base side\n"))
        self.before_call(self.is_main_merge, race)
        rc, out, err = self.merge()
        self.assertEqual(rc, 2, err)
        self.assertIn("merge failed", err)
        self.assertIn("merge aborted", err)
        self.assertIn("something else moved it", err)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), moved[0])
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))
        self.assertEqual(self.git(self.repo, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(self.kinds("auto_merge"), [])
        self.assertTrue(os.path.exists(self.wt))

    def test_base_moving_between_the_checks_and_the_write_is_refused_before_merging(self):
        def race(argv):
            self.commit(self.repo, "b.txt", "x\n")
        self.before_call(lambda a: a[2] == self.repo and a[3] == "status", race)
        n = len(self.calls)
        rc, out, err = self.merge()
        self.assertEqual(rc, 2, err)
        self.assertIn("moved while the guard ran", err)
        self.assertEqual(self.writes_since(n), [])
        self.assertEqual(self.kinds("auto_merge"), [])

    def test_a_clean_race_commit_is_recorded_and_stops_before_cleanup(self):
        def race(argv):
            self.commit(self.repo, "b.txt", "x\n")
        self.before_call(self.is_main_merge, race)
        rc, out, err = self.merge()
        self.assertEqual(rc, 3, err)
        self.assertIn("unexpected commit", err)
        self.assertIn("revert -m 1", err)
        self.assertEqual(len(self.kinds("auto_merge")), 1)
        self.assertTrue(os.path.exists(self.wt))                                  # no cleanup after a surprise
        self.assertEqual(self.orch_branches(), ["orch/add-login"])

    def test_a_commit_added_to_the_branch_in_the_race_window_is_not_merged(self):
        # the merge is by the gated sha: a later commit on the task branch cannot ride along
        self.before_call(self.is_main_merge, lambda argv: self.commit(self.wt, "smuggled.txt", "x\n") and None)
        gated = self.tip()
        rc, out, err = self.merge()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.parents("HEAD")[2], gated)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "smuggled.txt")))
        self.assertIn("warning: branch orch/add-login not deleted", err)          # `branch -d` refuses: it holds a commit not in base
        self.assertEqual(self.orch_branches(), ["orch/add-login"])

    def parents(self, rev):
        return self.git(self.repo, "rev-list", "--parents", "-n", "1", rev).split()

    def test_untracked_file_in_the_worktree_blocks_removal_and_is_reported(self):
        with open(os.path.join(self.wt, "scratch.txt"), "w") as fh:
            fh.write("keep me\n")
        rc, out, err = self.merge()
        self.assertEqual(rc, 0, err)                                              # the merge stands
        self.assertEqual(len(self.kinds("auto_merge")), 1)
        self.assertIn("warning: worktree not removed", err)
        self.assertIn("warning: branch orch/add-login not deleted", err)
        self.assertIn("worktree still listed", err)
        self.assertIn("cleanup: incomplete", out)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "scratch.txt")))
        s = st.Status.load(os.path.join(self.d, "status.md"))
        self.assertNotIn("removed after merge", s.get("worktree"))
        self.assertFalse(any(c[3:5] == ["branch", "-D"] for c in self.calls))
        self.assertFalse(any("--force" in c for c in self.calls))

    def test_bookkeeping_failure_after_the_merge_reports_the_sha_and_the_revert(self):
        with mock.patch.object(merge, "append_journal", side_effect=OSError("disk full")):
            rc, out, err = self.merge()
        self.assertEqual(rc, 3, err)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.assertIn(head[:9], err)
        self.assertIn("bookkeeping failed", err)
        self.assertIn("disk full", err)
        self.assertIn("revert -m 1 %s" % head, err)
        self.assertTrue(os.path.exists(self.wt))                                  # nothing was cleaned up

    def test_the_merge_is_not_journaled_when_git_refuses(self):
        self.before_call(self.is_main_merge, lambda argv: (1, "", "simulated refusal\n"))
        rc, out, err = self.merge()
        self.assertEqual(rc, 2)
        self.assertIn("simulated refusal", err)
        self.assertEqual(self.kinds("auto_merge"), [])

    def test_git_timeout_is_a_failure_not_a_hang(self):
        self.before_call(self.is_main_merge, lambda argv: (124, "", "git timed out after 300s"))
        rc, out, err = self.merge()
        self.assertEqual(rc, 2)
        self.assertIn("timed out", err)


class TestForbiddenGit(GitBase):
    def test_the_runtime_guard_refuses_each_forbidden_argument(self):
        self.init_repo(self.repo)
        n = len(self.calls)
        for args in (("push",), ("push", "origin", "main"), ("--force",), ("--force-with-lease",), ("-D", "x"),
                     ("reset", "--hard"), ("checkout", "x"), ("clean", "-fd"), ("merge", "--ff-only", "x"),
                     ("branch", "-D", "x"), ("status", "--force-x")):
            with self.assertRaises(OrchError, msg=args) as cm:
                merge.git(self.repo, *args)
            self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.calls[n:], [])
        self.assertEqual(merge.git(self.repo, "rev-parse", "HEAD").rc, 0)         # an allowed one still runs

    def test_runtime_guard_matches_the_independent_list(self):
        self.assertEqual(set(merge.FORBIDDEN), set(a for a in NEVER if not a.startswith("--force")) | {"--force"})

    def test_source_has_no_forbidden_literals_and_one_subprocess_call(self):
        for name in ("merge.py", "policy.py"):
            path = os.path.join(core.BIN, "orchlib", name)
            src = rd(path)
            tree = ast.parse(src)
            allowed = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("FORBIDDEN", "FORBIDDEN_PREFIX")
                                                        for t in node.targets):
                    allowed.update(id(n) for n in ast.walk(node))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in allowed:
                    v = node.value
                    self.assertNotIn(v, NEVER, "%s:%d" % (name, node.lineno))
                    self.assertFalse(v.startswith("--force"), "%s:%d" % (name, node.lineno))
            stripped = re.sub(r"# forbidden-begin.*?# forbidden-end", "", src, flags=re.S)
            self.assertNotIn("--ff-only", stripped)
            for word in ("push", "reset", "checkout", "clean", "-D"):
                self.assertNotRegex(stripped, r"""['"]%s['"]""" % word)
            self.assertNotIn("shell=True", src)
            self.assertNotIn("os.system", src)
            self.assertNotIn("Popen", src)
            self.assertLessEqual(src.count("subprocess.run"), 1)
            self.assertNotRegex(src, r"\bgit push\b")


class TestProgramMergePoints(GitBase):
    ROADMAP_ROWS = 3

    def setUp(self):
        GitBase.setUp(self)
        self.setup_task()
        self.set_field(shape="roadmap", milestone="M1 — reviewing", reviewed_through="none", merged_through="none")
        self.write_roadmap()

    def write_roadmap(self, rows=None):
        rows = rows or self.ROADMAP_ROWS
        table = ["| M | goal | depends on | mode | tiers | sensitive | review | merge | you | size | confidence |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
        for m in range(1, rows + 1):
            table.append("| %d | goal %d | - | standard | sonnet/high | - | code | yes | - | M | high |" % (m, m))
        with open(os.path.join(self.d, "roadmap.md"), "w") as fh:
            fh.write("# Roadmap: add-login\n\n## Milestones\n\n" + "\n".join(table) + "\n\n## Contracts\n")

    def review_point(self, ms):
        self.set_field(milestone="M%d — reviewing" % ms)
        self.gate()
        self.review(ms=ms)
        self.set_field(reviewed_through=self.tip())

    def status(self):
        return st.Status.load(os.path.join(self.d, "status.md"))

    def parents(self, rev, cwd=None):
        return self.git(cwd or self.repo, "rev-list", "--parents", "-n", "1", rev).split()

    def test_two_consecutive_merge_points_then_the_last_milestone(self):
        self.review_point(1)
        t1 = self.tip()
        out, err = self.merge_ok()
        m1 = self.git(self.repo, "rev-parse", "HEAD")
        k1 = self.tip()
        self.assertNotEqual(k1, t1)
        self.assertEqual(self.parents(k1), [k1, t1, m1])                         # K has parents (T, M)
        self.assertEqual(self.git(self.repo, "rev-parse", k1 + "^{tree}"), self.git(self.repo, "rev-parse", t1 + "^{tree}"))
        self.assertEqual(self.git(self.wt, "rev-parse", "HEAD"), k1)
        self.assertEqual(self.git(self.repo, "log", "-1", "--format=%s", k1), "Back-merge main after M1 merge point")
        lines = out.splitlines()
        self.assertEqual(lines[2], "merge point M1: task branch caught up at %s" % k1[:9])
        self.assertEqual(err, "")
        s = self.status()
        self.assertEqual((s.get("merged_through"), s.get("reviewed_through")), (m1, k1))
        mp = self.kinds("merge_point")
        self.assertEqual([(r["milestone"], r["sha"], r["backmerge"], r["tree_equal"]) for r in mp], [(1, m1, k1, True)])
        self.assertEqual(len(self.kinds("auto_merge")), 1)
        self.assertTrue(os.path.exists(self.wt))                                  # no cleanup at a merge point
        self.assertEqual(self.orch_branches(), ["orch/add-login"])
        self.assertNotIn("removed", s.get("worktree"))
        # M2: more commits, a review at the new tip; condition 9 holds because the back-merge brought base in
        self.commit(self.wt, "m2.txt", "two\n")
        self.review_point(2)
        t2 = self.tip()
        self.merge_ok()
        m2 = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.parents(m2), [m2, m1, t2])
        k2 = self.tip()
        self.assertEqual(self.parents(k2), [k2, t2, m2])
        self.assertEqual(self.status().get("merged_through"), m2)
        # M3 is the last milestone: a normal merge with cleanup
        self.commit(self.wt, "m3.txt", "three\n")
        self.review_point(3)
        out, _ = self.merge_ok()
        self.assertIn("cleanup: worktree removed, branch deleted", out)
        self.assertFalse(os.path.exists(self.wt))
        self.assertEqual(len(self.kinds("merge_point")), 2)                       # none for the last milestone
        self.assertEqual(len(self.kinds("auto_merge")), 3)

    def test_a_program_review_line_must_match_its_milestone_brief(self):
        self.set_field(milestone="M1 — reviewing")
        self.gate()
        rec = {"ts": "2026-01-01T00:00Z", "repo": "myapp", "task": "add-login", "kind": "review_round",
               "round": 1, "reviewer": "code", "blockers": 0, "should_fix": 0, "nits": 0, "verdict": "pass",
               "sha": self.tip(), "milestone": 1}
        self.raw_journal(rec)
        self.refuse_merge("code review", "no valid review brief")
        self.brief("code", 1, self.tip())                            # a non-program brief name is the wrong file
        self.refuse_merge("code review", "no valid review brief")
        self.brief("code", 1, "0" * 40, ms=1)                        # the milestone brief pins another sha
        self.refuse_merge("code review", "differs from the review brief")
        self.brief("code", 1, self.tip(), ms=1)
        self.merge_ok("--check", "--recheck-ok")

    def test_every_merge_call_is_no_ff_and_none_uses_ff_only(self):
        self.review_point(1)
        self.merge_ok()
        merges = [c for c in self.calls if c[3] == "merge"]
        self.assertEqual(len(merges), 2)                                         # into base, then the back-merge
        for c in merges:
            self.assertIn("--no-ff", c)
            self.assertNotIn("--ff-only", c)
        self.assertEqual(merges[1][2], self.wt)

    def test_back_merge_failure_keeps_the_merge_and_the_next_point_asks(self):
        self.review_point(1)
        t1 = self.tip()
        self.before_call(lambda a: a[2] == self.wt and a[3] == "merge", lambda a: (1, "", "simulated back-merge failure\n"))
        out, err = self.merge_ok()
        m1 = self.git(self.repo, "rev-parse", "HEAD")
        self.assertIn("back-merge failed", err)
        self.assertIn("backmerge: null", err)
        self.assertEqual(self.tip(), t1)                                          # the task branch stays at T
        mp = self.kinds("merge_point")[0]
        self.assertEqual((mp["sha"], mp["backmerge"], mp["tree_equal"]), (m1, None, None))
        s = self.status()
        self.assertEqual((s.get("merged_through"), s.get("reviewed_through")), (m1, t1))
        self.assertIn("NOT caught up", out)
        self.commit(self.wt, "m2.txt", "two\n")
        self.review_point(2)
        self.refuse_merge("base moved after the task branch was gated — ask the user")

    def test_back_merge_takes_the_merge_commit_not_whatever_base_is_now(self):
        self.review_point(1)
        t1 = self.tip()
        late = []
        self.before_call(lambda a: a[2] == self.wt and a[3] == "merge", lambda a: late.append(self.commit(self.repo, "late.txt", "x\n")) and None)
        self.merge_ok()
        m1 = late and self.git(self.repo, "rev-parse", "HEAD~1")
        k = self.tip()
        self.assertEqual(self.parents(k), [k, t1, m1])                           # (T, M), not (T, late base tip)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "late.txt")))
        self.assertEqual(self.kinds("merge_point")[0]["tree_equal"], True)

    def test_a_real_hook_failure_in_the_worktree_is_aborted(self):
        hook = os.path.join(self.repo, ".git", "hooks", "pre-merge-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\ncase \"$(git rev-parse --git-dir)\" in *worktrees*)\n"
                     "  echo 'hook refuses the back-merge' >&2; exit 1;; esac\nexit 0\n")
        os.chmod(hook, 0o755)
        self.review_point(1)
        t1 = self.tip()
        out, err = self.merge_ok()
        self.assertIn("hook refuses the back-merge", err)
        self.assertEqual(self.tip(), t1)
        self.assertEqual(self.git(self.wt, "status", "--porcelain", "--untracked-files=no"), "")
        gd = self.git(self.wt, "rev-parse", "--absolute-git-dir")
        self.assertFalse(os.path.exists(os.path.join(gd, "MERGE_HEAD")))
        self.assertTrue(any(c[2] == self.wt and c[3:5] == ["merge", "--abort"] for c in self.calls)
                        or not os.path.exists(os.path.join(gd, "MERGE_HEAD")))
        self.assertEqual(self.kinds("merge_point")[0]["backmerge"], None)

    def test_worktree_that_moved_on_is_not_back_merged(self):
        self.review_point(1)
        self.before_call(lambda a: a[3] == "merge" and a[2] == self.repo,
                         lambda a: self.commit(self.wt, "late.txt", "x\n") and None)
        # the hook edits the worktree during the merge window: the gated tip is no longer its HEAD
        rc, out, err = self.merge()
        self.assertEqual(rc, 0, err)
        self.assertIn("no back-merge", err)
        self.assertEqual(self.kinds("merge_point")[0]["backmerge"], None)

    def test_tree_difference_keeps_reviewed_through_at_the_tip(self):
        self.review_point(1)
        t1 = self.tip()
        real_diff = merge.git

        def lying_git(path, *args, **kw):
            if args[:2] == ("diff", "--quiet"):
                return merge.Res(1, "", "")
            return real_diff(path, *args, **kw)
        with mock.patch.object(merge, "git", lying_git):
            out, err = self.merge_ok()
        self.assertIn("different tree", err)
        s = self.status()
        self.assertEqual(s.get("reviewed_through"), t1)
        self.assertEqual(s.get("merged_through"), self.git(self.repo, "rev-parse", "HEAD"))
        mp = self.kinds("merge_point")[0]
        self.assertEqual(mp["tree_equal"], False)
        self.assertIsNotNone(mp["backmerge"])

    def test_reviewed_through_elsewhere_is_left_alone(self):
        self.review_point(1)
        earlier = self.git(self.repo, "rev-parse", "main")
        self.set_field(reviewed_through="%s (M0)" % earlier[:10])
        self.merge_ok()
        self.assertTrue(self.status().get("reviewed_through").startswith(earlier[:10]))

    def test_program_worktree_with_a_tracked_change_refuses(self):
        self.review_point(1)
        with open(os.path.join(self.wt, "feature.txt"), "a") as fh:
            fh.write("pending\n")
        self.refuse_merge("worktree has uncommitted changes")

    def test_no_worktree_means_no_back_merge_but_the_merge_stands(self):
        self.drop_worktree("none (user: no worktree)")
        self.review_point(1)
        out, err = self.merge_ok()
        self.assertIn("no task worktree", err)
        self.assertEqual(self.kinds("merge_point")[0]["backmerge"], None)
        self.assertEqual(self.orch_branches(), ["orch/add-login"])

    def test_condition_9_refuses_a_base_that_the_task_branch_lacks(self):
        self.review_point(1)
        self.commit(self.repo, "b.txt", "x\n")
        self.refuse_merge("base moved")

    def test_milestone_unknown_or_last_row_missing(self):
        self.review_point(1)
        self.set_field(milestone="n/a")
        self.refuse_merge("without a current milestone")


class TestConcurrency(GitBase):
    def test_merge_waits_for_the_task_lock(self):
        import fcntl
        import time
        self.setup_task()
        self.ready()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1",
               "ORCH_DIR": self.root, "ORCH_NO_INDEX": "1", "ORCH_NOW": core.DEFAULT_NOW}
        fd = os.open(os.path.join(self.d, ".orch.lock"), os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            p = subprocess.Popen([sys.executable, core.LAUNCHER, "merge", "--recheck-ok", "-t", T], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            time.sleep(2.0)
            self.assertIsNone(p.poll(), "merge ran while another process held the task lock")
            self.assertEqual(self.kinds("auto_merge"), [])
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        out, err = p.communicate(timeout=120)
        self.assertEqual(p.returncode, 0, err)
        self.assertEqual(len(self.kinds("auto_merge")), 1)

    def test_two_merges_at_once_merge_once(self):
        self.setup_task()
        self.ready()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1",
               "ORCH_DIR": self.root, "ORCH_NO_INDEX": "1", "ORCH_NOW": core.DEFAULT_NOW}
        procs = [subprocess.Popen([sys.executable, core.LAUNCHER, "merge", "--recheck-ok", "-t", T], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                 for _ in range(2)]
        results = []
        for p in procs:
            out, err = p.communicate(timeout=120)
            results.append((p.returncode, out, err))
        codes = sorted(r[0] for r in results)
        self.assertEqual(codes[0], 0, results)
        self.assertNotEqual(codes[1], 0, results)
        self.assertEqual(len(self.kinds("auto_merge")), 1)
        merges = self.git(self.repo, "log", "--merges", "--format=%H")
        self.assertEqual(len(merges.split()), 1)


if __name__ == "__main__":
    unittest.main()
