"""orch docs store verbs: layout, name resolution, adopt/add/link, status, commit. Temp store and temp repos only."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

from orchlib import cli, docs  # noqa: E402

LAUNCHER = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin", "orch")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-docs-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = os.path.join(self.tmp, "store")
        gitconfig = os.path.join(self.tmp, "gitconfig")
        with open(gitconfig, "w") as fh:   # a private global config: the machine's own hooks never see the temp repos
            fh.write("[user]\n\tname = T\n\temail = t@example.invalid\n[init]\n\tdefaultBranch = main\n")
        self.data = os.path.join(self.tmp, "data")
        self.env = {"HOME": os.path.join(self.tmp, "home"), "GIT_CONFIG_GLOBAL": gitconfig, "GIT_CONFIG_NOSYSTEM": "1",
                    "AGENTDOCS_STORE": self.store, "ORCH_NO_INDEX": "1", "ORCH_DATA": self.data,
                    "ORCH_BIN": LAUNCHER, "AGENTDOCS_ALLOW": "0"}
        os.makedirs(self.env["HOME"])

    def private(self, name, *lines):
        d = os.path.join(self.data, "private")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

    def sh(self, cwd, *argv, env=None, stdin=None):
        """Run a command with the isolated env; returns the CompletedProcess (no assertion)."""
        e = dict(os.environ)
        e.update(self.env)
        e.update(env or {})
        return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True, env=e, input=stdin)

    def git(self, cwd, *args):
        env = dict(os.environ)
        env.update(self.env)
        res = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0, "git %r: %s" % (args, res.stderr))
        return res.stdout

    def repo(self, name, origin=None, files=None, commit=True):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        self.git(d, "init", "-q", "-b", "main")
        if origin:
            self.git(d, "remote", "add", "origin", origin)
        for rel, text in (files or {"README.md": "x\n"}).items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        if commit:
            self.git(d, "add", "-A")
            self.git(d, "commit", "-q", "-m", "first")
        return d

    def docs(self, cwd, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        full = dict(self.env)
        full.update(env or {})
        with mock.patch.dict(os.environ, full, clear=False), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = cli.main(["docs"] + (["-C", cwd] if cwd else []) + list(argv))
        return rc, out.getvalue(), err.getvalue()

    def ok(self, cwd, *argv):
        rc, out, err = self.docs(cwd, *argv)
        self.assertEqual(rc, 0, "argv=%r\nstdout=%s\nstderr=%s" % (argv, out, err))
        return out

    def excludes(self, repo):
        with open(os.path.join(repo, ".git", "info", "exclude")) as fh:
            return fh.read().split("\n")


class TestRoundTrip(Base):
    def test_init_adopt_link_round_trip(self):
        r = self.repo("proj", "git@github.com:Own/Proj.git")
        self.ok(r, "init")
        entry = os.path.join(self.store, "proj")
        self.assertEqual(os.readlink(os.path.join(r, ".agent")), entry)
        with open(os.path.join(entry, ".origin")) as fh:
            self.assertEqual(fh.read(), "git@github.com:Own/Proj.git\n")
        self.assertEqual(os.path.getsize(os.path.join(entry, ".links")), 0)
        self.assertEqual(self.git(self.store, "config", "agentdocs.allow").strip(), "true")
        self.assertEqual(self.git(self.store, "symbolic-ref", "--short", "HEAD").strip(), "main")

        os.makedirs(os.path.join(r, "docs"))
        with open(os.path.join(r, "docs", "notes.md"), "w") as fh:
            fh.write("secret\n")
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        self.ok(r, "adopt", "CLAUDE.md", "./docs/notes.md/")
        self.assertEqual(os.readlink(os.path.join(r, "CLAUDE.md")), ".agent/CLAUDE.md")
        self.assertEqual(os.readlink(os.path.join(r, "docs", "notes.md")), "../.agent/docs/notes.md")
        with open(os.path.join(r, "docs", "notes.md")) as fh:
            self.assertEqual(fh.read(), "secret\n")
        with open(os.path.join(entry, ".links")) as fh:
            self.assertEqual(fh.read(), "CLAUDE.md\ndocs/notes.md\n")
        self.assertIn("/CLAUDE.md", self.excludes(r))
        self.assertIn("/docs/notes.md", self.excludes(r))
        self.assertIn("/.agent", self.excludes(r))
        # adopting again does not duplicate entries
        out = self.ok(r, "adopt", "CLAUDE.md")
        self.assertIn("already a link", out)
        with open(os.path.join(entry, ".links")) as fh:
            self.assertEqual(fh.read(), "CLAUDE.md\ndocs/notes.md\n")
        self.assertEqual(self.excludes(r).count("/CLAUDE.md"), 1)

        # a fresh checkout: link recreates .agent and the links; a real file is never replaced
        fresh = self.repo("fresh", "https://github.com/own/proj/")
        out = self.ok(fresh, "link")
        self.assertEqual(os.readlink(os.path.join(fresh, ".agent")), entry)
        self.assertEqual(os.readlink(os.path.join(fresh, "docs", "notes.md")), "../.agent/docs/notes.md")
        self.assertIn("linked CLAUDE.md", out)
        os.unlink(os.path.join(fresh, "CLAUDE.md"))
        with open(os.path.join(fresh, "CLAUDE.md"), "w") as fh:
            fh.write("mine\n")
        out = self.ok(fresh, "link")
        self.assertIn("not replaced", out)
        self.assertFalse(os.path.islink(os.path.join(fresh, "CLAUDE.md")))

        out = self.ok(r, "status")
        self.assertIn("  ok      CLAUDE.md", out)
        os.unlink(os.path.join(entry, "docs", "notes.md"))
        self.assertIn("  BROKEN  docs/notes.md", self.ok(r, "status"))
        os.unlink(os.path.join(r, "docs", "notes.md"))
        self.assertIn("  MISSING docs/notes.md", self.ok(r, "status"))

    def test_adopt_of_a_tracked_file_untracks_it(self):
        r = self.repo("tr", files={"README.md": "x\n", "STATE.md": "s\n"})
        self.assertIn("STATE.md", self.git(r, "ls-files"))
        out = self.ok(r, "status")
        self.assertIn("private paths still tracked:", out)
        self.assertIn("  STATE.md", out)
        self.ok(r, "adopt", "STATE.md")
        self.assertNotIn("STATE.md", self.git(r, "ls-files"))
        self.assertIn("D  STATE.md", self.git(r, "status", "--short"))
        self.assertTrue(os.path.islink(os.path.join(r, "STATE.md")))
        self.assertNotIn("still tracked", self.ok(r, "status").replace("(none)", ""))

    def test_adopt_skips_and_refuses(self):
        r = self.repo("sk")
        out = self.ok(r, "adopt", "nothere.md")
        self.assertIn("does not exist, skipped", out)
        entry = os.path.join(self.store, "sk")
        os.makedirs(os.path.join(entry, "x"))
        with open(os.path.join(entry, "x", "a.md"), "w") as fh:
            fh.write("in store\n")
        os.makedirs(os.path.join(r, "x"))
        with open(os.path.join(r, "x", "a.md"), "w") as fh:
            fh.write("in repo\n")
        rc, out, err = self.docs(r, "adopt", "x/a.md")
        self.assertEqual(rc, 1)
        self.assertIn("already exists in the store", err)
        with open(os.path.join(r, "x", "a.md")) as fh:
            self.assertEqual(fh.read(), "in repo\n")
        rc, out, err = self.docs(r, "adopt", "../outside")
        self.assertEqual(rc, 1)

    def test_add_links_a_file_already_in_the_store(self):
        r = self.repo("ad")
        self.ok(r, "init")
        entry = os.path.join(self.store, "ad")
        os.makedirs(os.path.join(entry, "docs"))
        with open(os.path.join(entry, "docs", "STATE.md"), "w") as fh:
            fh.write("s\n")
        self.ok(r, "add", "docs/STATE.md")
        self.assertEqual(os.readlink(os.path.join(r, "docs", "STATE.md")), "../.agent/docs/STATE.md")
        with open(os.path.join(entry, ".links")) as fh:
            self.assertEqual(fh.read(), "docs/STATE.md\n")
        self.assertIn("/docs/STATE.md", self.excludes(r))
        self.ok(r, "add", "docs/STATE.md")      # idempotent
        with open(os.path.join(entry, ".links")) as fh:
            self.assertEqual(fh.read(), "docs/STATE.md\n")
        rc, out, err = self.docs(r, "add", "docs/nope.md")
        self.assertEqual(rc, 1)
        self.assertIn("not in the store", out)


class TestNames(Base):
    def test_nested_worktree_skips_claude_and_agents_md(self):
        r = self.repo("nest")
        self.ok(r, "init")
        entry = os.path.join(self.store, "nest")
        os.makedirs(os.path.join(entry, "docs"))
        for rel in ("CLAUDE.md", "AGENTS.md", "docs/STATE.md"):
            with open(os.path.join(entry, rel), "w") as fh:
                fh.write("x\n")
        with open(os.path.join(entry, ".links"), "w") as fh:
            fh.write("CLAUDE.md\nAGENTS.md\ndocs/STATE.md\n")
        wt = os.path.join(r, ".claude", "worktrees", "wt")
        self.git(r, "worktree", "add", "-q", "-b", "wt", wt)
        out = self.ok(wt, "link")
        self.assertEqual(os.readlink(os.path.join(wt, ".agent")), entry)
        self.assertFalse(os.path.lexists(os.path.join(wt, "CLAUDE.md")))
        self.assertFalse(os.path.lexists(os.path.join(wt, "AGENTS.md")))
        self.assertTrue(os.path.islink(os.path.join(wt, "docs", "STATE.md")))
        self.assertIn("linked docs/STATE.md", out)
        # a worktree outside the main checkout gets all of them
        far = os.path.join(self.tmp, "far-wt")
        self.git(r, "worktree", "add", "-q", "-b", "far", far)
        self.ok(far, "link")
        self.assertTrue(os.path.islink(os.path.join(far, "CLAUDE.md")))
        self.assertIn("  ok      docs/STATE.md", self.ok(wt, "status"))

    def test_name_by_origin_from_a_clone_in_another_dir(self):
        a = self.repo("alpha", "git@github.com:Own/Thing.git")
        self.ok(a, "init")
        b = self.repo("other-dir-name", "https://GitHub.com/own/thing")
        self.assertEqual(docs.repo_name(b, self.store), "alpha")
        c = self.repo("unrelated", "https://github.com/own/else")
        self.assertEqual(docs.repo_name(c, self.store), "unrelated")
        self.assertEqual(docs.repo_name(a, self.store), "alpha")
        # an .agent link in the main checkout names the entry for a worktree
        wt = os.path.join(self.tmp, "wt-of-alpha")
        self.git(a, "worktree", "add", "-q", "-b", "w2", wt)
        self.assertEqual(docs.repo_name(wt, self.store), "alpha")

    def test_norm_url(self):
        for u in ("git@github.com:o/r.git", "https://github.com/o/R.git", "ssh://git@github.com/o/r/",
                  "http://github.com/o/r"):
            self.assertEqual(docs.norm_url(u), "github.com/o/r", u)

    def test_init_refuses_a_foreign_origin_and_a_real_agent_file(self):
        a = self.repo("one", "git@github.com:own/one.git")
        self.ok(a, "init")
        b = self.repo("one-too", "git@github.com:own/different.git")
        rc, out, err = self.docs(b, "init", "--name", "one")
        self.assertEqual(rc, 1)
        self.assertIn("belongs to git@github.com:own/one.git", err)
        self.assertFalse(os.path.lexists(os.path.join(b, ".agent")))
        c = self.repo("two")
        os.mkdir(os.path.join(c, ".agent"))
        rc, out, err = self.docs(c, "init")
        self.assertEqual(rc, 1)
        self.assertIn("not a symlink", err)
        rc, out, err = self.docs(c, "init", "--name", "../x")
        self.assertEqual(rc, 1)


class TestStore(Base):
    def test_commit_and_list_and_migrated(self):
        r = self.repo("cm", "git@github.com:own/cm.git")
        self.ok(r, "init")
        out = self.ok(r, "commit", "-m", "register cm")
        self.assertIn("register cm", out)
        self.assertIn("store clean", self.ok(r, "commit"))
        self.assertIn("cm", self.ok(r, "list"))
        self.assertIn("git@github.com:own/cm.git", self.ok(r, "list"))
        self.git(r, "config", "agentdocs.mode", "warn")
        self.ok(r, "migrated")
        res = subprocess.run(["git", "-C", r, "config", "agentdocs.mode"], capture_output=True, text=True,
                             env=dict(os.environ, **self.env))
        self.assertNotEqual(res.returncode, 0)
        self.ok(r, "migrated")      # already unset: still ok

    def test_status_without_a_store_entry(self):
        r = self.repo("no")
        out = self.ok(r, "status")
        self.assertIn("(none)", out)
        self.assertIn(".agent: missing", out)
        self.assertIn("no store entry", self.ok(r, "link"))

    def test_outside_a_repo(self):
        d = os.path.join(self.tmp, "plain")
        os.makedirs(d)
        rc, out, err = self.docs(d, "status")
        self.assertEqual(rc, 1)
        self.assertIn("not in a git repo", err)

    def test_extends_an_entry_made_by_the_shell_tool(self):
        tool = shutil.which("agent-docs")
        if not tool:
            self.skipTest("the shell tool is not installed here")
        r = self.repo("legacy", "git@github.com:own/legacy.git")
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("old\n")
        env = dict(os.environ, **self.env)
        for argv in (["init"], ["adopt", "CLAUDE.md"]):
            res = subprocess.run([tool] + argv, cwd=r, capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
        entry = os.path.join(self.store, "legacy")
        # a fresh clone is linked by orch, and orch's own adopt extends the same entry
        fresh = self.repo("legacy-clone", "https://github.com/own/legacy.git")
        self.ok(fresh, "link")
        self.assertEqual(os.readlink(os.path.join(fresh, "CLAUDE.md")), ".agent/CLAUDE.md")
        with open(os.path.join(fresh, "extra.md"), "w") as fh:
            fh.write("e\n")
        self.ok(fresh, "adopt", "extra.md")
        with open(os.path.join(entry, ".links")) as fh:
            self.assertEqual(fh.read(), "CLAUDE.md\nextra.md\n")
        # the shell tool reads what orch wrote
        res = subprocess.run([tool, "status"], cwd=fresh, capture_output=True, text=True, env=env)
        self.assertIn("ok      extra.md", res.stdout)


PRIV = "zqprivatezq"     # a synthetic private string; the real list is never read by tests


def slurp(path):
    with open(path) as fh:
        return fh.read()


def commit(test, repo, rel, text, msg="c", allow=False):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    test.git(repo, "add", "--", rel)
    if allow:   # a commit past the pre-commit shim, to build history the push check then sees
        res = test.sh(repo, "git", "commit", "-q", "-m", msg, env={"AGENTDOCS_ALLOW": "1"})
        test.assertEqual(res.returncode, 0, res.stderr)
    else:
        test.git(repo, "commit", "-q", "-m", msg)


class TestCheck(Base):
    def stage(self, repo, rel, text):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        self.git(repo, "add", "--", rel)

    def test_clean_and_usage(self):
        r = self.repo("ck")
        self.stage(r, "src/a.txt", "hello\n")
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual((rc, err), (0, ""))
        for argv in ((), ("bogus",), ("range",), ("tree", "--", "-x"), ("tree", "a", "b")):
            self.assertEqual(self.docs(r, "check", *argv)[0], 2, argv)

    def test_staged_path_is_blocked_and_the_advice_names_orch(self):
        r = self.repo("ck")
        self.stage(r, "CLAUDE.md", "rules\n")
        self.stage(r, "docs/STATE.md", "s\n")
        self.stage(r, "src/ok.txt", "fine\n")
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual(rc, 1)
        self.assertIn("    CLAUDE.md", err)
        self.assertIn("    docs/STATE.md", err)
        self.assertNotIn("src/ok.txt", err)
        self.assertIn("orch docs adopt", err)
        self.assertNotIn("agent-docs", err)

    def test_content_check_needs_the_private_list_and_looks_at_added_lines_only(self):
        r = self.repo("ck", files={"README.md": "old %s1\n" % PRIV})
        self.stage(r, "src/a.txt", "has %s2 here\n" % PRIV)
        self.assertEqual(self.docs(r, "check", "staged")[0], 0)      # no list: content check off, path check on
        self.private("agent-docs-deny-content.txt", "# comment", "", PRIV + "[0-9]")
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual(rc, 1)
        self.assertIn("    src/a.txt:has %s2 here" % PRIV, err)
        # a line already in HEAD that the commit does not touch is not reported
        self.git(r, "reset", "-q")
        with open(os.path.join(r, "README.md"), "a") as fh:
            fh.write("new clean line\n")
        self.git(r, "add", "README.md")
        self.assertEqual(self.docs(r, "check", "staged")[0], 0)

    def test_allow_switches(self):
        r = self.repo("ck")
        self.private("agent-docs-deny-content.txt", PRIV)
        self.stage(r, "CLAUDE.md", "x\n")
        self.stage(r, "n.txt", "a %s b\n" % PRIV)
        self.assertEqual(self.docs(r, "check", "staged")[0], 1)
        rc, out, err = self.docs(r, "check", "staged", env={"AGENTDOCS_ALLOW": "1"})
        self.assertEqual(rc, 0)
        self.assertIn("AGENTDOCS_ALLOW=1, skipped", err)
        self.git(r, "config", "agentdocs.allowPath", "^CLAUDE\\.md$")
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual(rc, 1)
        self.assertNotIn("    CLAUDE.md", err)
        self.git(r, "config", "agentdocs.allowContent", "^n\\.txt:")
        self.assertEqual(self.docs(r, "check", "staged")[0], 0)
        self.git(r, "config", "--unset-all", "agentdocs.allowPath")
        self.git(r, "config", "--unset-all", "agentdocs.allowContent")
        self.git(r, "config", "agentdocs.mode", "warn")
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual(rc, 0)
        self.assertIn("agentdocs.mode=warn: not blocked", err)
        self.git(r, "config", "--unset", "agentdocs.mode")
        self.git(r, "config", "--bool", "agentdocs.allow", "true")
        self.assertEqual(self.docs(r, "check", "staged"), (0, "", ""))

    def test_output_is_capped(self):
        r = self.repo("ck")
        self.private("agent-docs-deny-content.txt", PRIV)
        for i in range(35):
            self.stage(r, "notes/handoff-%02d.md" % i, "%s %s\n" % (PRIV, "x" * 300))
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual(rc, 1)
        lines = [ln for ln in err.split("\n") if ln.startswith("    ")]
        self.assertEqual(len(lines), 60)
        self.assertTrue(all(len(ln) <= 4 + 200 for ln in lines))

    def test_a_list_that_does_not_compile_fails_closed(self):
        r = self.repo("ck")
        self.private("agent-docs-deny-content.txt", "(unclosed")
        self.stage(r, "a.txt", "x\n")
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual(rc, 2)
        self.assertNotIn("unclosed", err)       # a private list's content is never echoed

    def test_range_and_tree(self):
        r = self.repo("ck")
        self.private("agent-docs-deny-content.txt", PRIV)
        base = self.git(r, "rev-parse", "HEAD").strip()
        commit(self, r, "AGENTS.md", "a\n", "one")
        commit(self, r, "src/x.txt", "%s tail\n" % PRIV, "two")
        rc, out, err = self.docs(r, "check", "range", "--", base + "..HEAD")
        self.assertEqual(rc, 1)
        self.assertIn("    AGENTS.md", err)
        self.assertIn("    src/x.txt:%s tail" % PRIV, err)
        self.assertEqual(self.docs(r, "check", "range", "--", base + "..HEAD~2")[0], 0)
        rc, out, err = self.docs(r, "check", "tree")
        self.assertEqual(rc, 1)
        self.assertIn("    AGENTS.md", err)
        self.assertIn("    src/x.txt:%s tail" % PRIV, err)
        self.assertEqual(self.docs(r, "check", "tree", base)[0], 0)
        self.git(r, "config", "agentdocs.allowContent", "^src/")
        rc, out, err = self.docs(r, "check", "tree")
        self.assertNotIn("src/x.txt", err)

    def test_tree_works_in_a_bare_mirror_with_only_git_dir_set(self):
        r = self.repo("src-repo")
        self.private("agent-docs-deny-content.txt", PRIV)
        commit(self, r, "CLAUDE.md", "r\n")
        commit(self, r, "a/b.txt", "%s z\n" % PRIV)
        mirror = os.path.join(self.tmp, "mirror.git")
        self.git(self.tmp, "clone", "-q", "--bare", r, mirror)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        for gd in (mirror, "mirror.git"):
            cwd = elsewhere if gd == mirror else self.tmp
            res = self.sh(cwd, LAUNCHER, "docs", "check", "tree", "HEAD", env={"GIT_DIR": gd})
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertIn("    CLAUDE.md", res.stderr)
            self.assertIn("    a/b.txt:%s z" % PRIV, res.stderr)
        rc, out, err = self.docs(None, "check", "tree", "HEAD", env={"GIT_DIR": mirror, "AGENTDOCS_ALLOW": "1"})
        self.assertEqual(rc, 0)

    def test_same_exit_code_as_the_shell_tool(self):
        tool = shutil.which("agent-docs-check")
        if not tool:
            self.skipTest("the shell tool is not installed here")
        content = self.private("agent-docs-deny-content.txt", PRIV + "[0-9]")

        def both(repo, *argv, env=None):
            ours = self.docs(repo, "check", *argv, env=env)
            theirs = self.sh(repo, tool, *argv, env=dict({"AGENTDOCS_DENY_CONTENT": content}, **(env or {})))
            self.assertEqual(ours[0], theirs.returncode, "argv=%r orch=%r shell=%r" % (argv, ours[2], theirs.stderr))
            ind = lambda t: sorted(ln for ln in t.split("\n") if ln.startswith("    "))
            self.assertEqual(ind(ours[2]), ind(theirs.stderr))
            return ours[0]

        r = self.repo("eq")
        self.assertEqual(both(r, "staged"), 0)
        self.git(r, "config", "agentdocs.allowPath", "^$")      # a pattern that matches no path: still blocked below
        self.git(r, "config", "--unset-all", "agentdocs.allowPath")
        p = os.path.join(r, "CLAUDE.md")
        with open(p, "w") as fh:
            fh.write("x\n")
        self.git(r, "add", "CLAUDE.md")
        self.assertEqual(both(r, "staged"), 1)
        self.git(r, "config", "agentdocs.allowPath", "^CLAUDE\\.md$")
        self.assertEqual(both(r, "staged"), 0)
        self.git(r, "config", "--unset-all", "agentdocs.allowPath")
        with open(os.path.join(r, "s.txt"), "w") as fh:
            fh.write("one\ntwo %s3 three\n" % PRIV)
        self.git(r, "add", "s.txt")
        self.assertEqual(both(r, "staged"), 1)
        self.git(r, "config", "agentdocs.allowContent", "^s\\.txt:two")
        self.git(r, "config", "agentdocs.allowPath", "CLAUDE")
        self.assertEqual(both(r, "staged"), 0)
        self.git(r, "config", "--unset-all", "agentdocs.allowContent")
        self.git(r, "config", "--unset-all", "agentdocs.allowPath")
        self.git(r, "config", "agentdocs.mode", "warn")
        self.assertEqual(both(r, "staged"), 0)
        self.git(r, "config", "--unset", "agentdocs.mode")
        self.assertEqual(both(r, "staged", env={"AGENTDOCS_ALLOW": "1"}), 0)
        self.git(r, "commit", "-q", "-m", "c")
        self.assertEqual(both(r, "range", "HEAD"), 1)
        self.assertEqual(both(r, "tree", "HEAD"), 1)
        self.assertEqual(both(r, "bogus"), 2)
        self.assertEqual(both(r, "range"), 2)
        self.git(r, "config", "--bool", "agentdocs.allow", "true")
        self.assertEqual(both(r, "tree", "HEAD"), 0)


class TestHooks(Base):
    def install(self, repo, *extra):
        res = self.sh(repo, LAUNCHER, "docs", "install-hooks", *extra)
        return res

    def test_install_writes_idempotent_shims_and_refuses_foreign_hooks(self):
        r = self.repo("ih")
        hooks = os.path.join(r, ".git", "hooks")
        res = self.install(r)
        self.assertEqual(res.returncode, 0, res.stderr)
        for name in docs.HOOK_NAMES:
            path = os.path.join(hooks, name)
            self.assertTrue(os.access(path, os.X_OK))
            with open(path) as fh:
                text = fh.read()
            self.assertEqual(text.split("\n")[:2], ["#!/bin/sh", "# orch docs hook"])
            self.assertIn('exec "$ORCH" docs hook %s "$@"' % name, text)
            self.assertIn("ORCH_BIN:-$HOME/.claude/orch-data/plugin/bin/orch", text)
        before = {n: slurp(os.path.join(hooks, n)) for n in docs.HOOK_NAMES}
        res = self.install(r)
        self.assertIn("already installed", res.stdout)
        self.assertEqual(before, {n: slurp(os.path.join(hooks, n)) for n in docs.HOOK_NAMES})

        r2 = self.repo("ih2")
        own = os.path.join(r2, ".git", "hooks", "pre-push")
        with open(own, "w") as fh:
            fh.write("#!/bin/sh\necho own\n")
        os.chmod(own, 0o755)
        res = self.install(r2)
        self.assertEqual(res.returncode, 1)
        self.assertIn("not an orch hook", res.stderr)
        self.assertIn("starting with '#!/bin/sh'", res.stderr)
        self.assertFalse(os.path.exists(os.path.join(r2, ".git", "hooks", "pre-commit")))   # nothing written
        res = self.install(r2, "--chain-existing")
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(own + ".pre-orch") as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\necho own\n")
        self.assertTrue(docs.is_shim(own))
        self.assertEqual(self.install(r2, "--chain-existing").returncode, 0)

        custom = os.path.join(self.tmp, "hookdir")
        res = self.sh(self.tmp, LAUNCHER, "docs", "install-hooks", "--dir", custom)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(sorted(os.listdir(custom)), sorted(docs.HOOK_NAMES))

    def test_commit_is_blocked_by_the_pre_commit_shim(self):
        r = self.repo("pc")
        self.assertEqual(self.install(r).returncode, 0)
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        self.git(r, "add", "CLAUDE.md")
        res = self.sh(r, "git", "commit", "-q", "-m", "x")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("    CLAUDE.md", res.stderr)
        self.assertEqual(self.git(r, "rev-list", "--count", "HEAD").strip(), "1")
        res = self.sh(r, "git", "commit", "-q", "-m", "x", env={"AGENTDOCS_ALLOW": "1"})
        self.assertEqual(res.returncode, 0, res.stderr)
        # the staged diff is readable inside the hook: a private string in added content blocks too
        self.private("agent-docs-deny-content.txt", PRIV)
        with open(os.path.join(r, "leak.txt"), "w") as fh:
            fh.write("a %s b\n" % PRIV)
        self.git(r, "add", "leak.txt")
        res = self.sh(r, "git", "commit", "-q", "-m", "z")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("    leak.txt:a %s b" % PRIV, res.stderr)
        self.git(r, "reset", "-q", "leak.txt")
        os.unlink(os.path.join(r, "leak.txt"))
        # a clean commit passes through
        with open(os.path.join(r, "ok.txt"), "w") as fh:
            fh.write("ok\n")
        self.git(r, "add", "ok.txt")
        self.assertEqual(self.sh(r, "git", "commit", "-q", "-m", "y").returncode, 0)

    def test_commit_chains_to_the_repo_hook_and_never_to_itself(self):
        r = self.repo("ch")
        own = os.path.join(r, ".git", "hooks", "pre-commit")
        marker = os.path.join(self.tmp, "ran")
        with open(own, "w") as fh:
            fh.write("#!/bin/sh\necho ran > %s\nexit 1\n" % marker)
        os.chmod(own, 0o755)
        self.assertEqual(self.install(r, "--chain-existing").returncode, 0)
        with open(os.path.join(r, "ok.txt"), "w") as fh:
            fh.write("ok\n")
        self.git(r, "add", "ok.txt")
        res = self.sh(r, "git", "commit", "-q", "-m", "y")
        self.assertNotEqual(res.returncode, 0)          # the chained hook said no
        self.assertTrue(os.path.exists(marker))
        # a shim alone (the default dir holds the shim itself) does not loop
        r2 = self.repo("ch2")
        self.assertEqual(self.install(r2).returncode, 0)
        self.git(r2, "commit", "-q", "--allow-empty", "-m", "e")

    def test_push_is_blocked_for_a_commit_holding_agent_docs(self):
        r = self.repo("pp")
        bare = os.path.join(self.tmp, "remote.git")
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", bare)
        self.git(r, "remote", "add", "origin", bare)
        commit(self, r, "CLAUDE.md", "rules\n", "agent doc")
        self.assertEqual(self.install(r).returncode, 0)
        res = self.sh(r, "git", "push", "-q", "origin", "main")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("    CLAUDE.md", res.stderr)
        self.assertIn("Nothing was pushed", res.stderr)
        self.assertEqual(self.git(bare, "for-each-ref").strip(), "")
        # a new branch with only clean commits (the dirty one is already on the remote): allowed
        res = self.sh(r, "git", "push", "-q", "origin", "main", env={"AGENTDOCS_ALLOW": "1"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.git(r, "checkout", "-q", "-b", "feature")
        commit(self, r, "src/ok.txt", "ok\n", "clean")
        res = self.sh(r, "git", "push", "-q", "origin", "feature")
        self.assertEqual(res.returncode, 0, res.stderr)
        commit(self, r, "AGENTS.md", "a\n", "dirty again", allow=True)
        res = self.sh(r, "git", "push", "-q", "origin", "feature")
        self.assertNotEqual(res.returncode, 0)
        # deleting a ref is not checked
        res = self.sh(r, "git", "push", "-q", "origin", ":feature")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_push_passes_stdin_to_the_repo_hook_and_refuses_a_hook_that_cannot_run(self):
        r = self.repo("ps")
        bare = os.path.join(self.tmp, "remote2.git")
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", bare)
        self.git(r, "remote", "add", "origin", bare)
        own = os.path.join(r, ".git", "hooks", "pre-push")
        seen = os.path.join(self.tmp, "stdin.txt")
        with open(own, "w") as fh:
            fh.write("#!/bin/sh\ncat > %s\necho \"$1\" >> %s\n" % (seen, seen))
        os.chmod(own, 0o755)
        self.assertEqual(self.install(r, "--chain-existing").returncode, 0)
        res = self.sh(r, "git", "push", "-q", "origin", "main")
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(seen) as fh:
            lines = fh.read().split("\n")
        self.assertTrue(lines[0].startswith("refs/heads/main "), lines[0])
        self.assertEqual(lines[1], "origin")
        os.chmod(own + ".pre-orch", 0o644)       # present but not runnable
        commit(self, r, "src/n.txt", "n\n", "more")
        res = self.sh(r, "git", "push", "-q", "origin", "main")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("can't run", res.stderr)

    def test_post_checkout_links_a_new_worktree(self):
        r = self.repo("pw", "git@github.com:own/pw.git")
        self.ok(r, "init")
        entry = os.path.join(self.store, "pw")
        os.makedirs(os.path.join(entry, "docs"))
        with open(os.path.join(entry, "docs", "STATE.md"), "w") as fh:
            fh.write("s\n")
        with open(os.path.join(entry, ".links"), "w") as fh:
            fh.write("docs/STATE.md\n")
        self.assertEqual(self.install(r).returncode, 0)
        far = os.path.join(self.tmp, "far")
        res = self.sh(r, "git", "worktree", "add", "-q", "-b", "far", far)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(os.readlink(os.path.join(far, ".agent")), entry)
        self.assertTrue(os.path.islink(os.path.join(far, "docs", "STATE.md")))

    def test_shim_without_orch_fails_closed_for_commit_and_open_for_checkout(self):
        r = self.repo("no")
        self.assertEqual(self.install(r).returncode, 0)
        gitdir = os.path.dirname(shutil.which("git"))
        env = {"ORCH_BIN": os.path.join(self.tmp, "missing"), "PATH": gitdir + os.pathsep + "/usr/bin:/bin"}
        self.assertIsNone(shutil.which("orch", path=env["PATH"]))
        with open(os.path.join(r, "ok.txt"), "w") as fh:
            fh.write("ok\n")
        self.git(r, "add", "ok.txt")
        res = self.sh(r, "git", "commit", "-q", "-m", "y", env=env)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("claude plugin update orch@orch", res.stderr)
        env["AGENTDOCS_ALLOW"] = "1"
        self.assertEqual(self.sh(r, "git", "commit", "-q", "-m", "y", env=env).returncode, 0)
        env["AGENTDOCS_ALLOW"] = "0"
        res = self.sh(r, "git", "checkout", "-q", "-b", "other", env=env)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
