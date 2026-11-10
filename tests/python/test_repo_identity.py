"""Repo identity (D19): tasks/<repo>/repo.json, the collision rule and backfill. Neutral fixtures only."""
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

from orchlib import paths  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(repo, *args, **env):
    e = dict(os.environ)
    e.update(GIT_ENV)
    e.update(env)
    out = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e,
                         check=True, universal_newlines=True)
    return out.stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-ident-"))
        self.tasks = os.path.join(self.tmp, "tasks")
        os.makedirs(self.tasks)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_repo(self, parent, name, commit=True, message="first"):
        path = os.path.join(self.tmp, parent, name)
        os.makedirs(path)
        git(path, "init", "-q")
        if commit:
            self.commit(path, message)
        return path

    def commit(self, path, message, **env):
        git(path, "commit", "-q", "--allow-empty", "-m", message, **env)

    def recorded(self, name):
        with open(os.path.join(self.tasks, name, "repo.json")) as fh:
            return json.load(fh)["root_commit"]


class RepoIdentity(Fixture):
    def test_same_name_same_root_same_folder(self):
        repo = self.make_repo("a", "proj")
        self.assertEqual(paths.repo_folder(repo, self.tasks), "proj")
        self.assertEqual(self.recorded("proj"), git(repo, "rev-list", "--max-parents=0", "HEAD"))
        self.commit(repo, "second")  # HEAD moves, the root doesn't
        self.assertEqual(paths.repo_folder(repo, self.tasks), "proj")
        clone = os.path.join(self.tmp, "b", "proj")
        os.makedirs(os.path.dirname(clone))
        git(self.tmp, "clone", "-q", repo, clone)
        self.assertEqual(paths.repo_folder(clone, self.tasks), "proj")
        self.assertEqual(sorted(os.listdir(self.tasks)), ["proj"])

    def test_same_name_different_root_gets_suffix(self):
        first = self.make_repo("a", "proj", message="one")
        second = self.make_repo("b", "proj", message="two")
        self.assertEqual(paths.repo_folder(first, self.tasks), "proj")
        folder = paths.repo_folder(second, self.tasks)
        root2 = git(second, "rev-list", "--max-parents=0", "HEAD")
        self.assertEqual(folder, "proj-" + root2[:4])
        self.assertEqual(self.recorded(folder), root2)
        self.assertEqual(paths.repo_folder(second, self.tasks), folder)  # stable on a second call
        self.assertEqual(paths.repo_folder(first, self.tasks), "proj")  # the existing folder is never renamed

    def test_no_commits_then_first_commit(self):
        repo = self.make_repo("a", "proj", commit=False)
        self.assertEqual(paths.repo_folder(repo, self.tasks), "proj")
        self.assertFalse(os.path.exists(os.path.join(self.tasks, "proj", "repo.json")))
        self.assertEqual(os.listdir(self.tasks), [])
        self.commit(repo, "first")
        self.assertEqual(paths.repo_folder(repo, self.tasks), "proj")
        self.assertEqual(self.recorded("proj"), git(repo, "rev-parse", "HEAD"))

    def test_oldest_of_several_roots(self):
        repo = self.make_repo("a", "proj", message="newer")
        branch = git(repo, "symbolic-ref", "--short", "HEAD")
        git(repo, "checkout", "-q", "--orphan", "other")
        self.commit(repo, "older", GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        old_root = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "-f", branch)
        git(repo, "merge", "-q", "--allow-unrelated-histories", "-m", "join", "other")
        self.assertEqual(len(git(repo, "rev-list", "--max-parents=0", "HEAD").split()), 2)
        self.assertEqual(paths.root_commit(repo), old_root)

    def test_repo_json_bad_raises(self):
        repo = self.make_repo("a", "proj")
        os.makedirs(os.path.join(self.tasks, "proj"))
        with open(os.path.join(self.tasks, "proj", "repo.json"), "w") as fh:
            fh.write("{not json")
        with self.assertRaises(paths.OrchError):
            paths.repo_folder(repo, self.tasks)

    def test_default_tasks_dir_is_orch_root(self):
        repo = self.make_repo("a", "proj")
        old = os.environ.get("ORCH_DIR")
        os.environ["ORCH_DIR"] = self.tasks
        try:
            self.assertEqual(paths.repo_folder(repo), "proj")
        finally:
            if old is None:
                del os.environ["ORCH_DIR"]
            else:
                os.environ["ORCH_DIR"] = old
        self.assertTrue(os.path.exists(os.path.join(self.tasks, "proj", "repo.json")))

    def write_status(self, folder, slug, repo_line):
        d = os.path.join(self.tasks, folder, slug)
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write("# Status\n\n- repo: %s\n- base_branch: main\n" % repo_line)

    def test_backfill_writes_found_skips_rest_and_dot_entries(self):
        repo = self.make_repo("a", "proj")
        gone = os.path.join(self.tmp, "gone")
        self.write_status("proj", "t1", gone)  # missing path: skipped, the next task is used
        self.write_status("proj", "t2", repo + " (main checkout)")
        self.write_status("orphan", "t1", gone)  # no path on disk: skipped
        self.write_status("already", "t1", repo)
        paths.write_repo_json(os.path.join(self.tasks, "already"), "f" * 40)
        # dot entries and files: never repo folders, even when they look like one
        self.write_status(".migrate-backup-1", "t1", repo)
        with open(os.path.join(self.tasks, ".DS_Store"), "w") as fh:
            fh.write("x")
        with open(os.path.join(self.tasks, "ACTIVE.md"), "w") as fh:
            fh.write("x")
        os.makedirs(os.path.join(self.tasks, "norepo", ".hidden"))
        root = git(repo, "rev-list", "--max-parents=0", "HEAD")
        self.assertEqual(paths.backfill(self.tasks), {"proj": root})
        self.assertEqual(self.recorded("proj"), root)
        self.assertEqual(self.recorded("already"), "f" * 40)  # existing files are kept
        for skipped in ("orphan", "norepo", ".migrate-backup-1"):
            self.assertFalse(os.path.exists(os.path.join(self.tasks, skipped, "repo.json")), skipped)
        self.assertEqual(sorted(n for n in os.listdir(self.tasks) if n.startswith(".")), [".DS_Store", ".migrate-backup-1"])
        self.assertEqual(paths.backfill(self.tasks), {})  # idempotent
        self.assertEqual(paths.backfill(os.path.join(self.tmp, "nowhere")), {})


class InitUsesIdentity(Fixture):
    """`orch init` resolves the tasks folder through repo_folder."""

    def init(self, name, slug, repo):
        env = dict(os.environ, ORCH_DIR=self.tasks, ORCH_NO_INDEX="1")
        return subprocess.run([sys.executable, os.path.join(BIN, "orch"), "init", name, slug, "--repo-path", repo,
                               "--base", "main", "--request", "x"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def test_fresh_name_keeps_name_and_records_root(self):
        repo = self.make_repo("a", "proj")
        res = self.init("proj", "one", repo)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.tasks, "proj", "one")))
        self.assertEqual(self.recorded("proj"), git(repo, "rev-list", "--max-parents=0", "HEAD"))
        self.assertNotIn("repo folder:", res.stdout)
        self.assertEqual(self.init("proj", "two", repo).returncode, 0)  # the same repo again: same folder
        self.assertEqual(sorted(os.listdir(self.tasks)), ["proj"])

    def test_clashing_name_gets_suffix_and_says_so(self):
        first = self.make_repo("a", "proj", message="one")
        second = self.make_repo("b", "proj", message="two")
        self.assertEqual(self.init("proj", "one", first).returncode, 0)
        res = self.init("proj", "two", second)
        self.assertEqual(res.returncode, 0, res.stderr)
        folder = "proj-" + git(second, "rev-list", "--max-parents=0", "HEAD")[:4]
        self.assertIn("repo folder: %s" % folder, res.stdout)
        self.assertTrue(os.path.isdir(os.path.join(self.tasks, folder, "two")))
        self.assertFalse(os.path.exists(os.path.join(self.tasks, "proj", "two")))

    def test_name_argument_is_respected_and_missing_repo_is_fine(self):
        repo = self.make_repo("a", "proj")
        self.assertEqual(self.init("custom", "one", repo).returncode, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.tasks, "custom", "one")))
        self.assertEqual(self.init("nowhere", "one", os.path.join(self.tmp, "gone")).returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tasks, "nowhere", "repo.json")))


if __name__ == "__main__":
    unittest.main()
