"""C2: only the copy Claude Code installed (under ~/.claude/plugins/cache/) writes real state or the pointer.

Every test runs the real scripts by subprocess with HOME=<scratch>, ORCH_DATA and ORCH_DIR unset, and
$HOME/.claude/orch-data/plugin seeded as a link to a fixture dir. Neutral fixtures only."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE = os.path.dirname(os.path.dirname(HERE))
PLUGIN = os.path.join(WORKTREE, "plugin")
SID = "0f0e0d0c-0b0a-4908-8706-050403020100"


def tree_hash(root):
    """Names, link targets and file bytes under root (links not followed)."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(dirs + files):
            p = os.path.join(d, name)
            h.update(os.path.relpath(p, root).encode() + b"\0")
            if os.path.islink(p):
                h.update(b"L" + os.readlink(p).encode())
            elif os.path.isfile(p):
                with open(p, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()


def has_git_above(path):
    path = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def copy_plugin(dest):
    shutil.copytree(PLUGIN, dest, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


class LiveRule(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-live-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.data = os.path.join(self.home, ".claude", "orch-data")
        os.makedirs(self.data)
        self.fixture = os.path.join(self.tmp, "fixture-plugin")
        os.makedirs(self.fixture)
        self.pointer = os.path.join(self.data, "plugin")
        os.symlink(self.fixture, self.pointer)
        self.cache = os.path.join(self.home, ".claude", "plugins", "cache", "orch", "orch")

    def env(self, **kw):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1",
             "ORCH_NOW": "2026-03-01T10:00Z", "CLAUDE_CODE_SESSION_ID": "", "TMPDIR": self.tmp}
        e.update(kw)
        return e

    def run_bin(self, plugin_root, script, *argv, **env):
        return subprocess.run([os.path.join(plugin_root, "bin", script)] + list(argv), env=self.env(**env),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)

    def role(self, plugin_root, **env):
        return self.run_bin(plugin_root, "orch", "-t", "t/x", "role", "planner", "in_progress", "--note", "n", **env)

    def init(self, plugin_root, **env):
        return self.run_bin(plugin_root, "orch", "init", "t", "x", "--repo-path", "/home/u/p", "--base", "main",
                            "--request", "r", **env)

    def session_start(self, plugin_root, **env):
        return self.run_bin(plugin_root, "orch-session-start.sh", plugin_root, **env)

    def assertDevCopy(self, plugin_root):
        before = tree_hash(self.data)
        r = self.role(plugin_root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("development copy: set ORCH_DIR", r.stderr)
        self.assertEqual(tree_hash(self.data), before, "a dev copy wrote under orch-data")
        r = self.session_start(plugin_root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.pointer), self.fixture)
        self.assertEqual(tree_hash(self.data), before, "a dev copy's session start wrote under orch-data")

    def test_the_worktree_is_a_dev_copy(self):
        self.assertTrue(os.path.isdir(os.path.join(WORKTREE, ".git")) or os.path.isfile(os.path.join(WORKTREE, ".git")))
        self.assertDevCopy(PLUGIN)

    def test_a_copy_with_no_git_anywhere_above_is_a_dev_copy(self):
        dest = copy_plugin(os.path.join(self.tmp, "copied", "plugin"))
        self.assertFalse(has_git_above(dest), "fixture must have no .git above it")
        self.assertDevCopy(dest)

    def test_a_copy_beside_the_cache_is_a_dev_copy(self):
        """A prefix match is not containment: ~/.claude/plugins/cache-old/… is outside the cache."""
        dest = copy_plugin(os.path.join(self.home, ".claude", "plugins", "cache-old", "orch", "orch", "a" * 12))
        self.assertDevCopy(dest)

    def test_the_installed_copy_writes_and_moves_the_pointer(self):
        v1 = copy_plugin(os.path.join(self.cache, "0123456789ab"))
        r = self.init(v1)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.role(v1)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.data, "tasks", "t", "x", "status.md")) as fh:
            self.assertIn("| planner | in_progress |", fh.read())
        r = self.session_start(v1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.islink(self.pointer))
        self.assertEqual(os.readlink(self.pointer), v1)
        self.assertTrue(os.path.isfile(os.path.join(self.data, "tasks", "ACTIVE.md")))
        # a second version replaces the link and leaves the first dir untouched
        v1_hash = tree_hash(v1)
        v2 = copy_plugin(os.path.join(self.cache, "ba9876543210"))
        r = self.session_start(v2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.pointer), v2)
        self.assertEqual(tree_hash(v1), v1_hash)
        self.assertEqual([n for n in os.listdir(self.data) if n.startswith("plugin")], ["plugin"])  # no temp link left

    def test_the_installed_copy_points_only_at_its_own_root(self):
        v1 = copy_plugin(os.path.join(self.cache, "0123456789ab"))
        r = self.run_bin(v1, "orch-session-start.sh", PLUGIN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.pointer), self.fixture)

    def test_dev_copy_with_orch_dir_writes_there_and_never_moves_the_pointer(self):
        scratch2 = os.path.join(self.tmp, "scratch2")
        os.makedirs(scratch2)
        before = tree_hash(self.data)
        r = self.init(PLUGIN, ORCH_DIR=scratch2)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.role(PLUGIN, ORCH_DIR=scratch2)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(scratch2, "t", "x", "status.md")) as fh:
            self.assertIn("| planner | in_progress |", fh.read())
        for env in ({"ORCH_DIR": scratch2}, {"ORCH_DATA": os.path.join(self.tmp, "data2")}):
            r = self.session_start(PLUGIN, **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.readlink(self.pointer), self.fixture)
        self.assertFalse(os.path.lexists(os.path.join(self.tmp, "data2", "plugin")))
        self.assertEqual(tree_hash(self.data), before)

    def test_resume_records_no_driver_from_a_dev_copy(self):
        v1 = copy_plugin(os.path.join(self.cache, "0123456789ab"))
        self.assertEqual(self.init(v1).returncode, 0)
        status = os.path.join(self.data, "tasks", "t", "x", "status.md")
        with open(status, "rb") as fh:
            before = fh.read()
        r = self.run_bin(PLUGIN, "orch", "resume", "t/x", CLAUDE_CODE_SESSION_ID=SID)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(status, "rb") as fh:
            self.assertEqual(fh.read(), before)
        # control: the installed copy does record it
        r = self.run_bin(v1, "orch", "resume", "t/x", CLAUDE_CODE_SESSION_ID=SID)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(status) as fh:
            self.assertIn(SID, fh.read())


if __name__ == "__main__":
    unittest.main()
