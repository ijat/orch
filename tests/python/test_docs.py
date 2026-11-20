"""orch docs store verbs: layout, name resolution, adopt/add/link, status, commit. Temp store and temp repos only."""
import contextlib
import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
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
                    "AGENTDOCS_ALLOW": "0"}
        os.makedirs(self.env["HOME"])

    def private(self, name, *lines):
        d = os.path.join(self.data, "private")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

    def sh(self, cwd, *argv, env=None, stdin=None, timeout=None):
        """Run a command with the isolated env; returns the CompletedProcess (no assertion)."""
        e = dict(os.environ)
        e.update(self.env)
        e.update(env or {})
        return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True, env=e, input=stdin, timeout=timeout)

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
        # the same repo under another spelling of its URL is not refused (compared normalised, as repo_name matches it)
        c = self.repo("one-https", "https://github.com/Own/one")
        self.assertEqual(docs.repo_name(c, self.store), "one")
        self.ok(c, "init")
        self.assertEqual(os.readlink(os.path.join(c, ".agent")), os.path.join(self.store, "one"))
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

    def test_a_type_change_symlink_to_file_is_checked(self):
        r = self.repo("ty")
        self.private("agent-docs-deny-content.txt", PRIV)
        base = self.git(r, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(r, "docs"))
        os.symlink("../README.md", os.path.join(r, "docs", "STATE.md"))
        os.symlink("README.md", os.path.join(r, "n.txt"))
        self.git(r, "add", "docs/STATE.md", "n.txt")
        self.git(r, "commit", "-q", "-m", "links")      # no hook is installed here: the links are tracked
        for rel, text in (("docs/STATE.md", "state\n"), ("n.txt", "a %s b\n" % PRIV)):
            os.unlink(os.path.join(r, rel))
            with open(os.path.join(r, rel), "w") as fh:
                fh.write(text)
        self.git(r, "add", "docs/STATE.md", "n.txt")
        self.assertIn("T\tdocs/STATE.md", self.git(r, "diff", "--cached", "--name-status"))
        rc, out, err = self.docs(r, "check", "staged")
        self.assertEqual(rc, 1)
        self.assertIn("    docs/STATE.md", err)                       # the path check sees the typechange
        self.assertIn("    n.txt:a %s b" % PRIV, err)                 # and the content check reads the new file's lines
        self.git(r, "commit", "-q", "-m", "now files")
        rc, out, err = self.docs(r, "check", "range", "--", "HEAD~1..HEAD")
        self.assertEqual(rc, 1)
        self.assertIn("    docs/STATE.md", err)
        self.assertIn("    n.txt:a %s b" % PRIV, err)
        rc, out, err = self.docs(r, "check", "range", "--", base + "..HEAD~1")            # the symlink commit alone
        self.assertEqual(rc, 1)
        self.assertIn("    docs/STATE.md", err)
        self.assertNotIn(PRIV, err)

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
    def install(self, repo, *extra, orch_bin=LAUNCHER):
        """Install the shims with an explicit launcher (a shim runs the path written into it, never one from the env)."""
        return self.sh(repo, LAUNCHER, "docs", "install-hooks", "--orch-bin", orch_bin, *extra)

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
            self.assertIn("ORCH=%s\n" % shlex.quote(LAUNCHER), text)             # the launcher is written into the shim
            self.assertIn("unset PYTHONPATH PYTHONHOME PYTHONSTARTUP", text)
            self.assertNotIn("ORCH_BIN", text)
            if name != "post-checkout":
                self.assertIn("ORCH_DOCS_PRIVATE=%s exec" % shlex.quote(os.path.join(self.data, "private")), text)
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
        res = self.sh(self.tmp, LAUNCHER, "docs", "install-hooks", "--orch-bin", LAUNCHER, "--dir", custom)
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

    def test_the_committing_environment_cannot_redirect_the_shim_or_the_lists(self):
        r = self.repo("tr")
        self.private("agent-docs-deny-content.txt", PRIV)
        self.assertEqual(self.install(r).returncode, 0)      # the shims bake the launcher and this private dir
        other = os.path.join(self.tmp, "elsewhere")
        os.makedirs(other)
        fake = os.path.join(self.tmp, "fake-orch")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake, 0o755)
        with open(os.path.join(r, "leak.txt"), "w") as fh:
            fh.write("a %s b\n" % PRIV)
        self.git(r, "add", "leak.txt")
        for env in ({"ORCH_BIN": fake}, {"ORCH_DATA": other}, {"ORCH_DIR": other}, {"ORCH_DOCS_PRIVATE": other},
                    {"HOME": other}, {"PYTHONPATH": other},
                    {"ORCH_BIN": "/usr/bin/true", "ORCH_DATA": other, "ORCH_DOCS_PRIVATE": "/nonexistent"}):
            res = self.sh(r, "git", "commit", "-q", "-m", "x", env=env)
            self.assertNotEqual(res.returncode, 0, env)
            self.assertIn("    leak.txt:a %s b" % PRIV, res.stderr)
        # and a CLAUDE.md is refused the same way
        self.git(r, "reset", "-q", "leak.txt")
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        self.git(r, "add", "CLAUDE.md")
        self.assertNotEqual(self.sh(r, "git", "commit", "-q", "-m", "x", env={"ORCH_BIN": fake}).returncode, 0)

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
        self.assertEqual(self.install(r, orch_bin=os.path.join(self.tmp, "missing")).returncode, 0)
        gitdir = os.path.dirname(shutil.which("git"))
        env = {"PATH": gitdir + os.pathsep + "/usr/bin:/bin"}
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

    def global_hooks(self, name="gh"):
        """A global hooks dir (core.hooksPath in the temp global config) holding orch shims, as `orch docs enable` leaves it."""
        d = os.path.join(self.tmp, name)
        res = self.sh(self.tmp, LAUNCHER, "docs", "install-hooks", "--orch-bin", LAUNCHER, "--dir", d)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.git(self.tmp, "config", "--global", "core.hooksPath", d)
        return d

    def looping_pre_orch(self, hooks, log):
        """The hook that hung the first live commit: it finds the repo's hook with `git rev-parse --git-path` (under a
        global hooks dir that is the shim again) and compares it with itself only."""
        path = os.path.join(hooks, "pre-commit.pre-orch")
        with open(path, "w") as fh:
            fh.write('#!/bin/sh\necho pre-orch >> %s\nrepo_hook="$(git rev-parse --git-path hooks/pre-commit)"\n'
                     '[ -x "$repo_hook" ] && [ "$repo_hook" != "$0" ] && exec "$repo_hook" "$@"\nexit 0\n' % shlex.quote(log))
        os.chmod(path, 0o755)

    def hook_temp_files(self):
        """Entries in the temp dir with the name the old re-entry marker used (this user's prefix)."""
        prefix = "orch-docs-hook-%d-" % os.getuid()
        return {n for n in os.listdir(tempfile.gettempdir()) if n.startswith(prefix)}

    def test_a_pre_orch_hook_that_calls_the_shim_again_neither_loops_nor_checks_twice(self):
        hooks = self.global_hooks()
        log = os.path.join(self.tmp, "ran.log")
        self.looping_pre_orch(hooks, log)
        r = self.repo("lp")
        own = os.path.join(r, ".git", "hooks", "pre-commit")
        with open(own, "w") as fh:
            fh.write("#!/bin/sh\necho repo-hook >> %s\n" % shlex.quote(log))
        os.chmod(own, 0o755)
        if os.path.exists(log):     # the repo's first commit already went through the hooks
            os.unlink(log)
        self.git(r, "config", "agentdocs.mode", "warn")       # the finding is reported on each check run, never blocks
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        self.git(r, "add", "-f", "CLAUDE.md")
        before = self.hook_temp_files()
        res = self.sh(r, "git", "commit", "-q", "-m", "x", timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr.count("would enter this repo"), 1, res.stderr)     # the orch check ran once
        self.assertEqual(slurp(log).split(), ["pre-orch", "repo-hook"])                # each hook once
        self.assertEqual(self.git(r, "rev-list", "--count", "HEAD").strip(), "2")
        self.assertEqual(self.hook_temp_files() - before, set())                       # no marker files any more

    def forge_repo(self, name):
        """A repo with CLAUDE.md staged under global orch shims whose pre-commit.pre-orch calls the shim again."""
        hooks = self.global_hooks()
        self.looping_pre_orch(hooks, os.path.join(self.tmp, "ran.log"))
        r = self.repo(name)
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        self.git(r, "add", "-f", "CLAUDE.md")
        return r

    def assert_refused(self, r, rc, err):
        """The commit in r was refused by the orch check (rc nonzero, the finding on stderr) and made nothing."""
        self.assertNotEqual(rc, 0, err)
        self.assertIn("    CLAUDE.md", err)
        self.assertEqual(self.git(r, "rev-list", "--count", "HEAD").strip(), "1")

    def test_the_old_marker_forge_gets_the_full_check(self):
        """S6/F7: a marker file plus the variable, written by the committer's own shell, is not believed."""
        r = self.forge_repo("fg")
        tmpdir = os.path.join(self.tmp, "tmpdir")
        os.makedirs(tmpdir)
        token = "ab" * 16
        line = ('printf "pre-commit %s" > "$TMPDIR/orch-docs-hook-$(id -u)-$$"; ORCH_DOCS_HOOK=$$:%s git commit -q -m x'
                % (token, token))
        res = self.sh(r, "sh", "-c", line, env={"TMPDIR": tmpdir}, timeout=60)
        self.assert_refused(r, res.returncode, res.stderr)
        for value in ("1:" + "0" * 32, "%d:%s" % (os.getpid(), "a" * 32), "1", "x:y", ""):
            res = self.sh(r, "git", "commit", "-q", "-m", "x", env={"ORCH_DOCS_HOOK": value}, timeout=60)
            self.assert_refused(r, res.returncode, res.stderr)

    def test_a_look_alike_ancestor_gets_the_full_check(self):
        r = self.forge_repo("la")
        py = os.path.realpath(sys.executable)
        # argv[0] made to read like the orch hook (ps shows the same text)
        res = self.sh(r, "bash", "-c", 'exec -a "%s %s docs hook pre-commit" bash -c "git commit -q -m x"'
                      % (py, LAUNCHER), timeout=60)
        self.assert_refused(r, res.returncode, res.stderr)
        # exactly the orch hook's argv, but the kernel runs bash, which runs the committer's BASH_ENV file first
        forge = os.path.join(self.tmp, "forge.sh")
        err, rc = os.path.join(self.tmp, "forge.err"), os.path.join(self.tmp, "forge.rc")
        with open(forge, "w") as fh:
            fh.write("git commit -q -m x 2>%s; echo $? > %s; exit 0\n" % (shlex.quote(err), shlex.quote(rc)))
        self.sh(r, "bash", LAUNCHER, "docs", "hook", "pre-commit", env={"BASH_ENV": forge}, timeout=60)
        self.assert_refused(r, int(slurp(rc)), slurp(err))

    def test_a_real_orch_hook_run_for_another_directory_does_not_skip_the_check(self):
        """The real launcher run as a hook in a scratch repo, whose own hook commits in the target repo."""
        self.global_hooks()
        r = self.repo("target")
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        self.git(r, "add", "-f", "CLAUDE.md")
        s = self.repo("scratch")
        err, rc = os.path.join(self.tmp, "nested.err"), os.path.join(self.tmp, "nested.rc")
        own = os.path.join(s, ".git", "hooks", "pre-commit")
        with open(own, "w") as fh:
            fh.write("#!/bin/sh\ncd %s && git commit -q -m x 2>%s; echo $? > %s\n"
                     % (shlex.quote(r), shlex.quote(err), shlex.quote(rc)))
        os.chmod(own, 0o755)
        res = self.sh(s, LAUNCHER, "docs", "hook", "pre-commit", timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assert_refused(r, int(slurp(rc)), slurp(err))

    def test_without_ps_the_check_runs_again_and_the_depth_limit_ends_the_loop(self):
        r = self.forge_repo("np")
        self.git(r, "config", "agentdocs.mode", "warn")
        bindir = os.path.join(self.tmp, "nops")
        os.makedirs(bindir)
        for tool in ("git", "python3", "id", "dirname", "basename", "cat"):
            found = shutil.which(tool)
            if found:
                os.symlink(found, os.path.join(bindir, tool))
        self.assertIsNone(shutil.which("ps", path=bindir))
        res = self.sh(r, "git", "commit", "-q", "-m", "x", env={"PATH": bindir}, timeout=60)
        self.assertNotEqual(res.returncode, 0, res.stderr)
        self.assertIn("hooks deep", res.stderr)
        self.assertEqual(res.stderr.count("would enter this repo"), docs.MAX_HOOK_DEPTH, res.stderr)  # checked each time
        self.assertEqual(self.git(r, "rev-list", "--count", "HEAD").strip(), "1")

    def test_a_depth_value_can_only_refuse(self):
        self.global_hooks()
        r = self.repo("dp")
        here = "pre-commit:" + os.path.realpath(r)
        for value, refused in (("3:" + here, True), ("99:" + here, True), ("99:pre-commit:" + self.tmp, False),
                               ("99:post-checkout:" + os.path.realpath(r), False), ("3", False), ("99", False),
                               ("-5:" + here, False), ("x:" + here, False), ("x", False), ("", False)):
            res = self.sh(r, "git", "commit", "-q", "--allow-empty", "-m", "x", env={docs.DEPTH_ENV: value}, timeout=60)
            self.assertEqual(res.returncode != 0, refused, (value, res.stderr))
            if refused:
                self.assertIn("hooks deep", res.stderr)

    def nested_commits(self, with_pre_orch):
        """F9: four repos under the global shims; each repo's own pre-commit commits in the next one (a clean
        environment for that repo, as a real hook must), so the commit in the first runs the hooks four levels deep."""
        hooks = self.global_hooks()
        log = os.path.join(self.tmp, "ran.log")
        if with_pre_orch:
            self.looping_pre_orch(hooks, log)
        repos = [self.repo("n%d" % i) for i in range(4)]
        for i, r in enumerate(repos):
            self.git(r, "config", "agentdocs.mode", "warn")     # each check reports CLAUDE.md once, never blocks
            with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
                fh.write("rules\n")
            self.git(r, "add", "-f", "CLAUDE.md")
            own = os.path.join(r, ".git", "hooks", "pre-commit")
            body = "#!/bin/sh\necho repo-hook-%d >> %s\n" % (i, shlex.quote(log))
            if i + 1 < len(repos):
                body += ("unset GIT_INDEX_FILE GIT_DIR GIT_WORK_TREE GIT_PREFIX\ncd %s && git commit -q -m nested\n"
                         % shlex.quote(repos[i + 1]))
            with open(own, "w") as fh:
                fh.write(body)
            os.chmod(own, 0o755)
        if os.path.exists(log):
            os.unlink(log)
        res = self.sh(repos[0], "git", "commit", "-q", "-m", "x", timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("refusing instead of looping", res.stderr)
        self.assertEqual(res.stderr.count("would enter this repo"), len(repos), res.stderr)    # each repo checked once
        ran = slurp(log).split()
        self.assertEqual([w for w in ran if w.startswith("repo-hook")], ["repo-hook-%d" % i for i in range(4)])
        self.assertEqual(ran.count("pre-orch"), len(repos) if with_pre_orch else 0)
        for r in repos:
            self.assertEqual(self.git(r, "rev-list", "--count", "HEAD").strip(), "2")

    def test_a_repo_hook_committing_in_other_repos_is_never_refused_by_the_depth_limit(self):
        self.nested_commits(with_pre_orch=False)

    def test_a_repo_hook_committing_in_other_repos_under_a_re_entering_pre_orch(self):
        self.nested_commits(with_pre_orch=True)

    def test_a_repo_hook_that_keeps_calling_the_shim_is_refused_not_looped(self):
        """A recognised run passes the depth counter on unchanged; a repo hook that calls the global shim again is
        ended by the count of orch runs above it instead."""
        hooks = self.global_hooks()
        log = os.path.join(self.tmp, "ran.log")
        r = self.repo("rl")
        own = os.path.join(r, ".git", "hooks", "pre-commit")
        with open(own, "w") as fh:
            fh.write('#!/bin/sh\necho repo-hook >> %s\nexec %s "$@"\n'
                     % (shlex.quote(log), shlex.quote(os.path.join(hooks, "pre-commit"))))
        os.chmod(own, 0o755)
        res = self.sh(r, "git", "commit", "-q", "--allow-empty", "-m", "x", timeout=60)
        self.assertNotEqual(res.returncode, 0, res.stderr)
        self.assertIn("already above this one", res.stderr)
        self.assertEqual(slurp(log).split(), ["repo-hook"] * docs.MAX_HOOK_DEPTH)
        self.assertEqual(self.git(r, "rev-list", "--count", "HEAD").strip(), "1")

    def test_is_outer_hook_needs_the_same_interpreter_launcher_name_and_directory(self):
        me = ("/usr/bin/python3", ["python3", LAUNCHER, "docs", "hook", "pre-commit"], self.tmp)
        good = ("/usr/bin/python3", ["python3", LAUNCHER, "docs", "hook", "pre-commit"], self.tmp)
        self.assertTrue(docs.is_outer_hook(good, me, "pre-commit"))
        self.assertTrue(docs.is_outer_hook(("/usr/bin/python3", ["py", LAUNCHER, "docs", "hook", "pre-push", "o", "u"],
                                            self.tmp), me, "pre-push"))
        for info in (("/bin/bash", good[1], self.tmp),                                           # another program
                     (good[0], good[1], os.path.join(self.tmp, "..")),                          # another directory
                     (good[0], ["python3", LAUNCHER, "docs", "hook", "pre-push"], self.tmp),     # another hook
                     (good[0], ["python3", "orch", "docs", "hook", "pre-commit"], self.tmp),     # a relative launcher
                     (good[0], ["python3", "/x/orch", "docs", "hook", "pre-commit"], self.tmp),  # another launcher
                     (good[0], ["%s %s docs hook pre-commit" % (good[0], LAUNCHER)], self.tmp),  # one argv word
                     (good[0], ["python3", "-c", LAUNCHER, "docs", "hook", "pre-commit"], self.tmp)):
            self.assertFalse(docs.is_outer_hook(info, me, "pre-commit"), info)

    def test_process_info_reads_the_kernel_executable_and_exact_argv(self):
        me = docs.process_info(os.getpid())
        if me is None:
            self.skipTest("no process reader on this platform")
        self.assertEqual(os.path.realpath(me[2]), os.path.realpath(os.getcwd()))
        child = subprocess.Popen(["bash", "-c", 'exec -a "%s docs hook pre-commit" sleep 30' % LAUNCHER])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        want = ["%s docs hook pre-commit" % LAUNCHER, "30"]
        deadline = time.monotonic() + 15       # bash must exec sleep first; under load that can take a while
        info = None
        while time.monotonic() < deadline:
            info = docs.process_info(child.pid)
            if info and info[1] == want and os.path.basename(info[0]) == "sleep":
                break
            time.sleep(0.05)
        else:
            self.fail("child never became sleep with the expected argv within 15 s; last info: %r" % (info,))
        self.assertEqual(info[1], ["%s docs hook pre-commit" % LAUNCHER, "30"])
        self.assertEqual(docs.ancestors()[0], os.getppid())

    def test_warning_only_for_a_kept_hook_that_uses_git_path(self):
        for body, warned in (('#!/bin/sh\nh="$(git rev-parse --git-path hooks/pre-commit)"\n', True),
                             ('#!/bin/sh\nh="$(git rev-parse --git-common-dir)/hooks/pre-commit"\n', False)):
            d = os.path.join(self.tmp, "w%d" % warned)
            os.makedirs(d)
            with open(os.path.join(d, "pre-commit"), "w") as fh:
                fh.write(body)
            os.chmod(os.path.join(d, "pre-commit"), 0o755)
            res = self.sh(self.tmp, LAUNCHER, "docs", "install-hooks", "--orch-bin", LAUNCHER, "--dir", d, "--chain-existing")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stderr.count("warning:"), 1 if warned else 0, res.stderr)
            if warned:
                self.assertIn("pre-commit.pre-orch", res.stderr)
                self.assertEqual(self.sh(self.tmp, LAUNCHER, "docs", "install-hooks", "--orch-bin", LAUNCHER, "--dir", d,
                                         "--chain-existing").stderr, "")     # an installed shim is not warned about again

    def test_a_shim_from_an_earlier_version_is_rewritten(self):
        d = os.path.join(self.tmp, "old")
        os.makedirs(d)
        for n in docs.HOOK_NAMES:
            with open(os.path.join(d, n), "w") as fh:
                fh.write("#!/bin/sh\n# orch docs hook\n# an older body\nexit 0\n")
            os.chmod(os.path.join(d, n), 0o755)
        res = self.sh(self.tmp, LAUNCHER, "docs", "install-hooks", "--orch-bin", LAUNCHER, "--dir", d)
        self.assertEqual(res.returncode, 0, res.stderr)
        for n in docs.HOOK_NAMES:
            self.assertIn('docs hook %s "$@"' % n, slurp(os.path.join(d, n)))
            self.assertNotIn("older body", slurp(os.path.join(d, n)))


def tree_state(root):
    """{relative path: (kind, mode, content or link target)} for everything under root; {} when it is absent."""
    out = {}
    for d, dirs, files in os.walk(root):
        for n in sorted(dirs + files):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                out[rel] = ("link", None, os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ("dir", None, None)
            else:
                with open(p, "rb") as fh:
                    out[rel] = ("file", os.stat(p).st_mode & 0o777, fh.read())
    return out


class TestEnable(Base):
    def setUp(self):
        Base.setUp(self)
        self.cm = os.path.join(self.tmp, "CLAUDE.md")
        with open(self.cm, "w") as fh:
            fh.write("# Mine\n")
        self.gitconfig = self.env["GIT_CONFIG_GLOBAL"]
        self.settings_file = os.path.join(self.data, "settings.json")
        os.makedirs(self.data, exist_ok=True)       # the stable plugin link enable bakes into the shims
        os.symlink(os.path.dirname(os.path.dirname(LAUNCHER)), os.path.join(self.data, "plugin"))

    def cmd(self, *argv):
        rc, out, err = self.docs(None, *argv, "--file", self.cm)
        return rc, out, err

    def hooks_path(self):
        res = self.sh(self.tmp, "git", "config", "--global", "--get", "core.hooksPath")
        return res.stdout.strip() if res.returncode == 0 else None

    def setting(self):
        return self.sh(self.tmp, sys.executable, LAUNCHER, "setting", "get", "agent_docs").stdout.strip()

    def foreign_hooks(self, name="foreign-hooks"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        for n, body in (("pre-commit", "#!/bin/sh\necho gitleaks-ish\n"), ("commit-msg", "#!/bin/sh\nexit 0\n")):
            p = os.path.join(d, n)
            with open(p, "w") as fh:
                fh.write(body)
            os.chmod(p, 0o755)
        return d

    def test_round_trip_with_a_preset_hooks_path_and_a_foreign_hook(self):
        hooks = self.foreign_hooks()
        self.sh(self.tmp, "git", "config", "--global", "core.hooksPath", hooks)
        cfg0, hooks0, cm0 = slurp(self.gitconfig), tree_state(hooks), slurp(self.cm)
        rc, out, err = self.cmd("enable")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.setting(), "on")
        self.assertEqual(slurp(self.gitconfig), cfg0)             # the path was already set: git config untouched
        for n in docs.HOOK_NAMES:
            self.assertTrue(docs.is_shim(os.path.join(hooks, n)), n)
        self.assertEqual(slurp(os.path.join(hooks, "pre-commit.pre-orch")), "#!/bin/sh\necho gitleaks-ish\n")
        self.assertEqual(slurp(os.path.join(hooks, "commit-msg")), "#!/bin/sh\nexit 0\n")     # other hooks left alone
        self.assertIn("## Agent docs stay out of repos", slurp(self.cm))
        self.assertTrue(out.strip().split("\n")[-1].startswith("orch docs: enable: done"), out)
        state_on = (tree_state(hooks), slurp(self.gitconfig), slurp(self.cm), slurp(self.settings_file))
        rc, out, err = self.cmd("enable")                         # a second run changes nothing
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().split("\n")[-1], "orch docs: enable: nothing to do")
        self.assertEqual((tree_state(hooks), slurp(self.gitconfig), slurp(self.cm), slurp(self.settings_file)), state_on)
        rc, out, err = self.cmd("disable")
        self.assertEqual(rc, 0, err)
        self.assertEqual(tree_state(hooks), hooks0)               # byte-identical, modes included
        self.assertEqual(slurp(self.gitconfig), cfg0)
        self.assertEqual(self.setting(), "off")
        self.assertNotIn("agent_docs_hooks", slurp(self.settings_file))
        cm = slurp(self.cm)
        self.assertTrue(cm.startswith("# Mine\n") and "orch:begin" in cm)
        self.assertNotIn("Agent docs stay out of repos", cm)
        self.assertEqual(self.cmd("disable")[1].strip().split("\n")[-1], "orch docs: disable: nothing to do")

    def test_round_trip_with_no_hooks_path_set(self):
        cfg0 = slurp(self.gitconfig)
        self.assertIsNone(self.hooks_path())
        rc, out, err = self.cmd("enable")
        self.assertEqual(rc, 0, err)
        mine = os.path.join(self.data, "git-hooks")
        self.assertEqual(os.path.realpath(self.hooks_path()), os.path.realpath(mine))
        self.assertEqual(sorted(os.listdir(mine)), sorted(docs.HOOK_NAMES))
        state_on = (tree_state(mine), slurp(self.gitconfig), slurp(self.settings_file))
        self.assertEqual(self.cmd("enable")[1].strip().split("\n")[-1], "orch docs: enable: nothing to do")
        self.assertEqual((tree_state(mine), slurp(self.gitconfig), slurp(self.settings_file)), state_on)
        rc, out, err = self.cmd("disable")
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(mine))                    # the directory enable made is gone
        self.assertEqual(slurp(self.gitconfig), cfg0)             # byte-identical: the key and its section are gone
        self.assertIsNone(self.hooks_path())
        self.assertEqual(self.setting(), "off")

    def test_dry_run_prints_each_change_and_makes_none(self):
        hooks = self.foreign_hooks()
        self.sh(self.tmp, "git", "config", "--global", "core.hooksPath", hooks)
        before = (tree_state(hooks), slurp(self.gitconfig), slurp(self.cm), os.path.exists(self.settings_file))
        rc, out, err = self.cmd("enable", "--dry-run")
        self.assertEqual(rc, 0, err)
        for want in ("would set agent_docs on", "would write ", "would rename ", "+## Agent docs stay out of repos",
                     "enable: would change the above"):
            self.assertIn(want, out)
        self.assertEqual((tree_state(hooks), slurp(self.gitconfig), slurp(self.cm), os.path.exists(self.settings_file)), before)
        self.assertEqual(self.cmd("enable")[0], 0)
        on = (tree_state(hooks), slurp(self.cm), slurp(self.settings_file))
        rc, out, err = self.cmd("disable", "--dry-run")
        self.assertIn("would set agent_docs off", out)
        self.assertIn("would remove ", out)
        self.assertEqual((tree_state(hooks), slurp(self.cm), slurp(self.settings_file)), on)

    def failing_enable(self, fail_at):
        """Run enable with the Nth shim write failing; returns the exit code."""
        real = docs.write_exec
        calls = []

        def flaky(path, text):
            calls.append(path)
            if len(calls) == fail_at:
                raise OSError("disk says no")
            return real(path, text)

        with mock.patch.object(docs, "write_exec", flaky):
            return self.cmd("enable")[0]

    def undo_case(self, preset, fail_at, rerun):
        hooks = self.foreign_hooks()
        if preset:
            self.sh(self.tmp, "git", "config", "--global", "core.hooksPath", hooks)
        cfg0, hooks0 = slurp(self.gitconfig), tree_state(hooks)
        mine = os.path.join(self.data, "git-hooks")
        self.assertEqual(self.failing_enable(fail_at), 3)
        self.assertIn("agent_docs_hooks", slurp(self.settings_file))
        if rerun:                                            # a re-run finishes what was left
            rc, out, err = self.cmd("enable")
            self.assertEqual(rc, 0, err)
            for n in docs.HOOK_NAMES:
                self.assertTrue(docs.is_shim(os.path.join(hooks if preset else mine, n)), n)
        rc, out, err = self.cmd("disable")
        self.assertEqual(rc, 0, err)
        self.assertEqual(tree_state(hooks), hooks0)
        self.assertEqual(slurp(self.gitconfig), cfg0)
        self.assertFalse(os.path.exists(mine))
        self.assertEqual(self.setting(), "off")
        self.assertNotIn("agent_docs_hooks", slurp(self.settings_file))

    def test_a_failure_in_the_middle_of_enable_leaves_a_record_disable_can_undo(self):
        for preset in (True, False):
            for fail_at in (1, 2, 3):
                for rerun in (False, True):
                    with self.subTest(preset=preset, fail_at=fail_at, rerun=rerun):
                        self.setUp()
                        self.undo_case(preset, fail_at, rerun)

    def test_a_blocked_block_update_is_reported_and_the_rest_still_applies(self):
        with open(self.cm, "w") as fh:
            fh.write("## Orchestration mode\n\n- my own rules\n")
        rc, out, err = self.cmd("enable")
        self.assertEqual(rc, 0, err)
        self.assertIn("CLAUDE.md block not updated: hand-written orch rules found", out)
        self.assertEqual(slurp(self.cm), "## Orchestration mode\n\n- my own rules\n")
        self.assertEqual(self.setting(), "on")
        self.assertTrue(docs.is_shim(os.path.join(self.data, "git-hooks", "pre-commit")))
        self.assertEqual(self.cmd("disable")[0], 0)
        self.assertEqual(self.setting(), "off")

    def test_enabled_hooks_block_a_commit_and_disabled_ones_do_not(self):
        r = self.repo("sw")
        self.assertEqual(self.cmd("enable")[0], 0)
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        self.git(r, "add", "CLAUDE.md")
        res = self.sh(r, "git", "commit", "-q", "-m", "x")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("    CLAUDE.md", res.stderr)
        self.assertEqual(self.cmd("disable")[0], 0)
        self.assertEqual(self.sh(r, "git", "commit", "-q", "-m", "x").returncode, 0)


class TestNudge(Base):
    def switch(self, value):
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, "settings.json"), "w") as fh:
            fh.write('{"agent_docs": "%s"}' % value)

    def start(self, cwd):
        res = self.sh(cwd, sys.executable, LAUNCHER, "session-start", os.path.join(self.tmp, "plugin-root"))
        return res

    def line(self, cwd):
        res = self.start(cwd)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def test_off_is_silent_even_with_agent_docs_present(self):
        r = self.repo("n1")
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("x\n")
        self.assertEqual(self.line(r), "")
        self.switch("off")
        self.assertEqual(self.line(r), "")

    def test_on_names_real_files_and_the_adopt_command(self):
        r = self.repo("n2")
        self.switch("on")
        self.assertEqual(self.line(r), "")          # nothing there yet
        for rel in ("CLAUDE.md", "docs/STATE.md", "src/CLAUDE.md"):   # the last is not a candidate: never walks the tree
            p = os.path.join(r, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("x\n")
        out = self.line(r)
        self.assertEqual(out, "orch docs: agent docs here are not in the private store: CLAUDE.md, docs/STATE.md "
                              "\u2014 move them: orch docs adopt CLAUDE.md docs/STATE.md\n")
        sub = os.path.join(r, "src")
        self.assertEqual(self.line(sub), out)       # found from a subdirectory
        self.assertEqual(self.sh(r, "git", "status", "--short").stdout.count("??"), 3)   # nothing was moved or created

    def test_tracked_hits_add_the_commit_note(self):
        r = self.repo("n3", files={"README.md": "x\n", "STATE.md": "s\n"})
        self.switch("on")
        self.assertTrue(self.line(r).endswith("orch docs adopt STATE.md (tracked: commit the removal afterwards)\n"))

    def test_links_the_exempt_the_nested_and_the_non_repo_are_silent(self):
        r = self.repo("n4")
        self.switch("on")
        entry = os.path.join(self.tmp, "somewhere")
        os.makedirs(entry)
        with open(os.path.join(entry, "CLAUDE.md"), "w") as fh:
            fh.write("x\n")
        os.symlink(os.path.join(entry, "CLAUDE.md"), os.path.join(r, "CLAUDE.md"))
        self.assertEqual(self.line(r), "")
        os.unlink(os.path.join(r, "CLAUDE.md"))
        with open(os.path.join(r, "CLAUDE.md"), "w") as fh:
            fh.write("x\n")
        self.assertNotEqual(self.line(r), "")
        self.git(r, "config", "--bool", "agentdocs.allow", "true")
        self.assertEqual(self.line(r), "")
        self.git(r, "config", "--unset", "agentdocs.allow")
        nested = os.path.join(r, ".claude", "worktrees", "wt")
        self.git(r, "worktree", "add", "-q", "-b", "wt", nested)
        with open(os.path.join(nested, "CLAUDE.md"), "w") as fh:
            fh.write("x\n")
        self.assertEqual(self.line(nested), "")
        far = os.path.join(self.tmp, "far-wt")
        self.git(r, "worktree", "add", "-q", "-b", "far", far)
        with open(os.path.join(far, "CLAUDE.md"), "w") as fh:
            fh.write("x\n")
        self.assertIn("CLAUDE.md", self.line(far))
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        with open(os.path.join(plain, "CLAUDE.md"), "w") as fh:
            fh.write("x\n")
        self.assertEqual(self.line(plain), "")

    def run_branch(self, **patches):
        """Run the launcher's session-start branch in this process; returns (exit code, stdout)."""
        import runpy
        out = io.StringIO()
        argv = [LAUNCHER, "session-start", os.path.join(self.tmp, "plugin-root")]
        with mock.patch.dict(os.environ, self.env), mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), contextlib.ExitStack() as stack:
            for target, value in patches.items():
                stack.enter_context(mock.patch(target, value))
            with self.assertRaises(SystemExit) as cm:
                runpy.run_path(LAUNCHER, run_name="__main__")
        return cm.exception.code, out.getvalue()

    def test_a_failing_nudge_changes_neither_the_exit_code_nor_the_pointer_update(self):
        self.switch("on")
        calls = []
        os.makedirs(os.path.join(self.tmp, "cwd", ".git"))
        old = os.getcwd()
        os.chdir(os.path.join(self.tmp, "cwd"))
        self.addCleanup(os.chdir, old)
        boom = mock.Mock(side_effect=RuntimeError("boom"))
        code, out = self.run_branch(**{"orchlib.docs.session_nudge": boom,
                                       "orchlib.paths.update_pointer": lambda root: calls.append(root)})
        self.assertEqual((code, out), (0, ""))
        self.assertTrue(boom.called)
        self.assertEqual(len(calls), 1)
        # a pointer failure still exits 3, and the nudge still runs
        nudge = mock.Mock(return_value="orch docs: hello")
        def bad(root):
            raise OSError("no link")
        code, out = self.run_branch(**{"orchlib.docs.session_nudge": nudge, "orchlib.paths.update_pointer": bad})
        self.assertEqual((code, out), (3, "orch docs: hello\n"))


if __name__ == "__main__":
    unittest.main()
