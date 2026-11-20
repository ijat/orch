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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-docs-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = os.path.join(self.tmp, "store")
        gitconfig = os.path.join(self.tmp, "gitconfig")
        with open(gitconfig, "w") as fh:   # a private global config: the machine's own hooks never see the temp repos
            fh.write("[user]\n\tname = T\n\temail = t@example.invalid\n[init]\n\tdefaultBranch = main\n")
        self.env = {"HOME": os.path.join(self.tmp, "home"), "GIT_CONFIG_GLOBAL": gitconfig, "GIT_CONFIG_NOSYSTEM": "1",
                    "AGENTDOCS_STORE": self.store, "ORCH_NO_INDEX": "1"}
        os.makedirs(self.env["HOME"])

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

    def docs(self, cwd, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=False), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = cli.main(["docs", "-C", cwd] + list(argv))
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


if __name__ == "__main__":
    unittest.main()
