"""scripts/check-public.py and githooks/pre-push. Fixture denylists and neutral words only; the real denylist is
touched by one test that is skipped without it, and never printed."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CHECK = os.path.join(REPO, "scripts", "check-public.py")
HOOK = os.path.join(REPO, "githooks", "pre-push")

AUTHOR = "authorword tester"          # matches fixture pattern #1: a header scan would find it in every commit
PATTERNS = "# fixture denylist\nauthorword\n\n  plantedword  \nsecret-[0-9]+\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-pub-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.data, "private"))
        self.deny = os.path.join(self.tmp, "deny.txt")
        self.write(self.deny, PATTERNS)
        self.gitconfig = os.path.join(self.tmp, "gitconfig")
        self.write(self.gitconfig, "")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", os.path.join(self.repo, ".git", "hooks"))  # never the user's global hooks
        self.git("config", "commit.gpgsign", "false")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "ORCH_"))}
        env.update(HOME=self.home, GIT_CONFIG_GLOBAL=self.gitconfig, GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_AUTHOR_NAME=AUTHOR, GIT_COMMITTER_NAME=AUTHOR, GIT_AUTHOR_EMAIL="a@example.invalid",
                   GIT_COMMITTER_EMAIL="a@example.invalid", ORCH_DATA=self.data)
        env.update(extra)
        return env

    def git(self, *args, cwd=None, check=True):
        res = subprocess.run(["git"] + list(args), cwd=cwd or self.repo, env=self.env(), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True)
        if check and res.returncode != 0:
            self.fail("git %s: %s" % (args[0], res.stderr.strip()[:200]))
        return res

    def write(self, path, text, binary=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if binary else "w") as fh:
            fh.write(text)

    def commit(self, files=None, message="update", remove=()):
        for rel, text in (files or {}).items():
            self.write(os.path.join(self.repo, rel), text, isinstance(text, bytes))
        for rel in remove:
            os.unlink(os.path.join(self.repo, rel))
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def check(self, *args, deny=True, env=None):
        argv = [sys.executable, CHECK] + list(args)
        if deny:
            argv += ["--denylist", self.deny]
        return subprocess.run(argv, cwd=self.repo, env=env or self.env(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)


class Scanner(Fixture):
    def test_clean_tree_is_zero(self):
        self.commit({"a.txt": "nothing here\n"})
        res = self.check("--tree", ".")
        self.assertEqual((res.returncode, res.stderr), (0, ""))
        self.assertIn("0 hits", res.stdout)

    def test_hit_reports_path_line_and_pattern_number_without_text(self):
        self.commit({"a.txt": "fine\nsome PlantedWord here\n", "b/c.txt": "x\nx\nsecret-42\n"})
        res = self.check("--tree", ".")
        self.assertEqual(res.returncode, 1)
        lines = res.stdout.splitlines()
        self.assertIn("a.txt:2: pattern #2", lines)       # case-insensitive; the padded pattern is stripped
        self.assertIn("b/c.txt:3: pattern #3", lines)
        self.assertNotIn("PlantedWord", res.stdout)
        self.assertNotIn("secret-42", res.stdout)

    def test_show_prints_the_text(self):
        self.commit({"a.txt": "some plantedword here\n"})
        res = self.check("--tree", ".", "--show")
        self.assertIn("a.txt:1: pattern #2: some plantedword here", res.stdout)

    def test_license_is_exempt_but_other_licenses_are_not(self):
        self.commit({"LICENSE": "plantedword\n", "sub/LICENSE": "plantedword\n"})
        res = self.check("--tree", ".")
        self.assertEqual(res.returncode, 1)
        self.assertEqual([l for l in res.stdout.splitlines() if "pattern" in l], ["sub/LICENSE:1: pattern #2"])
        self.assertEqual(self.check("--rev", "HEAD").stdout.count("pattern #"), 1)

    def test_path_hit(self):
        self.commit({"dir/plantedword.txt": "clean\n"})
        for args in (("--tree", "."), ("--rev", "HEAD")):
            res = self.check(*args)
            self.assertEqual(res.returncode, 1, args)
            self.assertIn("dir/plantedword.txt:path: pattern #2", res.stdout)

    def test_ignored_file_is_not_scanned_but_untracked_one_is(self):
        self.commit({".gitignore": "private.txt\n", "ok.txt": "ok\n"})
        self.write(os.path.join(self.repo, "private.txt"), "plantedword\n")
        self.assertEqual(self.check("--tree", ".").returncode, 0)
        self.write(os.path.join(self.repo, "new.txt"), "plantedword\n")  # git would commit it
        self.assertIn("new.txt:1: pattern #2", self.check("--tree", ".").stdout)

    def test_deleted_but_indexed_file_is_skipped_and_unreadable_one_is_exit_2(self):
        self.commit({"a.txt": "x\n", "b.txt": "y\n"})
        os.unlink(os.path.join(self.repo, "b.txt"))
        self.assertEqual(self.check("--tree", ".").returncode, 0)
        os.chmod(os.path.join(self.repo, "a.txt"), 0)
        self.addCleanup(os.chmod, os.path.join(self.repo, "a.txt"), 0o644)
        if os.geteuid() != 0:
            res = self.check("--tree", ".")
            self.assertEqual(res.returncode, 2)
            self.assertIn("cannot read a.txt", res.stderr)

    def test_binary_blob_and_symlink_target_are_found(self):
        self.commit({"bin.dat": b"\x00\x01plantedword\xff\xfe\n"})
        os.symlink("secret-7", os.path.join(self.repo, "link"))
        self.commit()
        res = self.check("--rev", "HEAD")
        self.assertIn("bin.dat:1: pattern #2", res.stdout)
        self.assertIn("link:1: pattern #3", res.stdout)
        res = self.check("--tree", ".")
        self.assertIn("link:1: pattern #3", res.stdout)

    def test_range_finds_a_hit_in_a_middle_commit_under_a_clean_tip(self):
        base = self.commit({"a.txt": "one\n"}, message="base")
        mid = self.commit({"leak.txt": "plantedword\n"}, message="add")
        self.commit(remove=["leak.txt"], message="remove")
        self.assertEqual(self.check("--tree", ".").returncode, 0)
        self.assertEqual(self.check("--rev", "HEAD").returncode, 0)
        res = self.check("--range", "%s..HEAD" % base)
        self.assertEqual(res.returncode, 1)
        self.assertIn("%s:leak.txt:1: pattern #2" % mid[:12], res.stdout)
        self.assertEqual(self.check("--range", "%s..HEAD" % mid).returncode, 0)  # the clean commits after it

    def test_range_message_hit_and_headers_are_not_scanned(self):
        base = self.commit({"a.txt": "one\n"}, message="base")
        clean = self.commit({"b.txt": "two\n"}, message="subject\n\nbody line\nCo-Authored-By: Someone <x@example.invalid>")
        res = self.check("--range", "%s..HEAD" % base)    # author and committer both match pattern #1
        self.assertEqual((res.returncode, res.stdout.count("pattern #")), (0, 0), res.stdout)
        bad = self.commit({"c.txt": "three\n"}, message="subject\n\nthe body mentions secret-9 here")
        res = self.check("--range", "%s..HEAD" % clean)
        self.assertEqual(res.returncode, 1)
        self.assertIn("%s:message:3: pattern #3" % bad[:12], res.stdout)
        self.assertNotIn("secret-9", res.stdout)

    def test_range_covers_a_root_commit_and_a_merge(self):
        root = self.commit({"a.txt": "plantedword\n"}, message="root")
        self.assertIn("%s:a.txt:1: pattern #2" % root[:12], self.check("--range", "HEAD").stdout)
        self.git("checkout", "-q", "-b", "side")
        side = self.commit({"s.txt": "secret-1\n"}, message="side")
        self.git("checkout", "-q", "main")
        self.commit({"m.txt": "main\n"}, message="main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "side")
        res = self.check("--range", "%s..HEAD" % root)
        self.assertEqual(res.returncode, 1)
        self.assertIn("%s:s.txt:1: pattern #3" % side[:12], res.stdout)

    def test_range_without_dots_is_the_commits_on_no_remote_tracking_ref(self):
        bare = os.path.join(self.tmp, "bare.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True, env=self.env())
        self.commit({"leak.txt": "plantedword\n"}, message="already published")
        self.git("remote", "add", "origin", bare)
        self.git("push", "-q", "origin", "main")        # the repo's core.hooksPath has no pre-push here
        res = self.check("--range", "HEAD")
        self.assertEqual((res.returncode, res.stdout.count("pattern #")), (0, 0))   # nothing unpushed
        self.assertIn("0 commits", res.stdout)
        self.commit({"b.txt": "x\n"}, message="unpushed")
        res = self.check("--range", "HEAD")
        self.assertEqual(res.returncode, 0)
        self.assertIn(" 1 commits", res.stdout)
        self.commit({"c.txt": "secret-5\n"}, message="unpushed too")
        res = self.check("--range", "HEAD")
        self.assertEqual(res.returncode, 1)
        self.assertIn(" 2 commits", res.stdout)
        self.assertNotIn("leak.txt", res.stdout)          # the published commit is not rescanned

    def test_unknown_rev_and_unknown_remote_sha_are_exit_2(self):
        self.commit({"a.txt": "x\n"})
        for args in (("--rev", "nosuchrev"), ("--range", "%s..HEAD" % ("1" * 40)), ("--range", "nosuchrev")):
            res = self.check(*args)
            self.assertEqual(res.returncode, 2, args)
            self.assertNotIn("0 hits", res.stdout)

    def test_bad_input_is_exit_2(self):
        self.commit({"a.txt": "x\n"})
        self.assertEqual(self.check("--tree", ".", deny=False).returncode, 2)   # no denylist anywhere
        self.assertEqual(self.check().returncode, 2)                              # nothing to scan
        self.write(self.deny, "ok\n(unclosed\n")
        res = self.check("--tree", ".")
        self.assertEqual(res.returncode, 2)
        self.assertIn("bad pattern", res.stderr)
        for text in ("", "# only a comment\n\n   \n"):
            self.write(self.deny, text)
            res = self.check("--tree", ".")
            self.assertEqual(res.returncode, 2, repr(text))        # an empty alternation would match every line
        res = self.check("--tree", ".", "--bogus")
        self.assertEqual(res.returncode, 2)

    def test_missing_explicit_denylist_is_exit_2(self):
        self.commit({"a.txt": "x\n"})
        res = self.check("--tree", ".", "--denylist", os.path.join(self.tmp, "nope.txt"), deny=False)
        self.assertEqual(res.returncode, 2)
        self.assertIn("nothing checked", res.stderr)

    def test_default_denylist_falls_back_to_the_legacy_file_and_says_so(self):
        self.commit({"a.txt": "plantedword\n"})
        legacy = os.path.join(self.home, ".claude", "orch-export-denylist.txt")
        self.write(legacy, PATTERNS)
        res = self.check("--tree", ".", deny=False)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.stdout.splitlines()[0], "denylist: legacy")
        self.assertNotIn(self.home, res.stdout)
        # the new file wins as soon as it exists
        new = os.path.join(self.data, "private", "orch-export-denylist.txt")
        self.write(new, "unrelated-word\n")
        res = self.check("--tree", ".", deny=False)
        self.assertEqual((res.returncode, res.stdout.splitlines()[0]), (0, "denylist: private"))
        # an unreadable new file is exit 2, not a fallback
        os.chmod(new, 0)
        self.addCleanup(os.chmod, new, 0o644)
        if os.geteuid() != 0:
            self.assertEqual(self.check("--tree", ".", deny=False).returncode, 2)
        # an explicit file that is missing never falls back, even though the legacy file exists
        res = self.check("--tree", ".", "--denylist", os.path.join(self.tmp, "nope.txt"), deny=False)
        self.assertEqual(res.returncode, 2)
        self.assertNotIn("legacy", res.stdout)

    @unittest.skipUnless(os.path.isfile(os.path.expanduser("~/.claude/orch-export-denylist.txt")) or
                         os.path.isfile(os.path.expanduser("~/.claude/orch-data/private/orch-export-denylist.txt")),
                         "no real denylist on this machine")
    def test_real_denylist_over_head(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        res = subprocess.run([sys.executable, CHECK, "--rev", "HEAD"], cwd=REPO, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(res.returncode, 0, "check-public --rev HEAD exit %d (counts only: %s)" % (
            res.returncode, res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "no output"))


class PrePushHook(Fixture):
    def setUp(self):
        Fixture.setUp(self)
        os.makedirs(os.path.join(self.repo, ".git", "hooks"), exist_ok=True)
        os.symlink(HOOK, os.path.join(self.repo, ".git", "hooks", "pre-push"))
        self.write(os.path.join(self.data, "private", "orch-export-denylist.txt"), PATTERNS)
        self.bare = os.path.join(self.tmp, "bare.git")
        subprocess.run(["git", "init", "-q", "--bare", self.bare], check=True, env=self.env())
        self.git("remote", "add", "origin", self.bare)

    def push(self, *refspec):
        return self.git("push", "origin", *refspec, check=False)

    def remote_head(self):
        res = subprocess.run(["git", "-C", self.bare, "rev-parse", "--verify", "-q", "refs/heads/main"],
                             env=self.env(), stdout=subprocess.PIPE, universal_newlines=True)
        return res.stdout.strip()

    def test_clean_push_passes_and_a_planted_hit_is_refused(self):
        first = self.commit({"a.txt": "fine\n"})
        res = self.push("main")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.remote_head(), first)
        self.commit({"b.txt": "plantedword\n"}, message="bad")
        res = self.push("main")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(self.remote_head(), first)
        self.assertIn("b.txt:1: pattern #2", res.stdout + res.stderr)
        self.assertNotIn("plantedword", res.stdout + res.stderr)

    def test_hit_in_a_middle_commit_under_a_clean_tip_is_refused(self):
        first = self.commit({"a.txt": "fine\n"})
        self.assertEqual(self.push("main").returncode, 0)
        self.commit({"leak.txt": "plantedword\n"}, message="leak")
        self.commit(remove=["leak.txt"], message="clean tip")
        self.assertEqual(self.check("--tree", ".").returncode, 0)
        res = self.push("main")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(self.remote_head(), first)
        self.assertIn("leak.txt:1: pattern #2", res.stdout + res.stderr)

    def test_a_message_hit_is_refused_and_a_new_ref_checks_the_unpublished_commits(self):
        self.commit({"a.txt": "fine\n"}, message="mentions secret-3")
        res = self.push("main")                      # a new remote ref: every commit not on a remote-tracking ref
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("message:1: pattern #3", res.stdout + res.stderr)
        self.assertEqual(self.remote_head(), "")

    def test_deletion_passes_and_an_annotated_tag_is_refused(self):
        first = self.commit({"a.txt": "fine\n"})
        self.assertEqual(self.push("main").returncode, 0)
        self.assertEqual(self.push("main:refs/heads/other").returncode, 0)
        self.assertEqual(self.push(":other").returncode, 0)
        self.git("tag", "-a", "v1", "-m", "release")
        res = self.push("v1")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("annotated tag", res.stderr)
        self.git("tag", "light")
        self.assertEqual(self.push("light").returncode, 0)
        self.assertEqual(self.remote_head(), first)

    def test_remote_sha_missing_locally_asks_to_fetch_first(self):
        self.commit({"a.txt": "fine\n"})
        stdin = "refs/heads/main %s refs/heads/main %s\n" % (self.git("rev-parse", "HEAD").stdout.strip(), "2" * 40)
        res = subprocess.run([HOOK, "origin", self.bare], cwd=self.repo, env=self.env(), input=stdin,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("fetch first", res.stderr)

    def test_missing_denylist_refuses_the_push(self):
        self.commit({"a.txt": "fine\n"})
        os.unlink(os.path.join(self.data, "private", "orch-export-denylist.txt"))
        res = self.push("main")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(self.remote_head(), "")


if __name__ == "__main__":
    unittest.main()
