#!/usr/bin/env python3
"""Tests for sync-live.py.

Each test builds a throwaway world under a temp dir: a git repo whose scripts/export.py is the REAL
export.py with only its table block replaced (so the file-set code under test is the real one), a
live dir written by the test itself as overlay(repo), and a private overlay, private file and
denylist. Every run uses a temp HOME, a temp TMPDIR and an explicit --live; nothing reads the real
~/.claude.

Run: python3 scripts/test_sync_live.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_EXPORT = os.path.join(HERE, "export.py")
SYNC = os.path.join(HERE, "sync-live.py")
BUILDER = os.path.join(os.path.dirname(HERE), "bin", "orch-build-agents.py")
sys.dont_write_bytecode = True

ANCHOR_A = "\nFILES = ["
ANCHOR_B = "\n\ndef fill("

PRIVATE = {"AUD": "aud-x", "DOC": "why-doc"}

TABLES = '''
FILES = ["skills/orchestrate/SKILL.md", "skills/orchestrate/roles/orch-a.md", "agents/orch-a.md",
         "bin/x.sh", "bin/orch-build-agents.py"%(extra_files)s]
TREES = ["bin/orchlib"]
TREE_SUFFIXES = (".py", ".html", ".mjs")
TREE_ALL = ("tests/fixtures",)
MOVED = {"skills/@@AUD@@/scripts/tool.py": "bin/tool.py"}
PRIVATE_EDITS = [("skills/orchestrate/SKILL.md", "See @@DOC@@ for why.\\n", "")]
EDITS = [("bin/x.sh", "# live comment\\n", "# public comment\\n")]
REGEX_EDITS = [("bin/orchlib/page.html", r'^<link [^>]*>\\n', re.M, "", 1, '<link rel="x">\\n')]
'''

# Repo files (public form) and their exec bits.
REPO_FILES = {
    "skills/orchestrate/SKILL.md": ("# Skill\nBody line.\nTwin.\nTwin.\n", False),
    "skills/orchestrate/roles/orch-a.md": ("Role A.\n", False),
    "agents/orch-a.md": ("Role A.\n", False),
    "bin/x.sh": ("#!/bin/bash\n# public comment\necho hi\n", True),
    "bin/tool.py": ("print('tool')\n", True),
    "bin/orchlib/a.py": ("A = 1\n", False),
    "bin/orchlib/page.html": ("<html>\n<head>\n<title>t</title>\n</head>\n</html>\n", False),
    "bin/orchlib/tests/fixtures/f.txt": ("fixture data\n", False),
    "notes.txt": ("not managed\n", False),
}

# The hand-written exact inverse of the three export edits above.
EDITS_OV = [
    {"path": "skills/orchestrate/SKILL.md", "public": "# Skill\n", "live": "# Skill\nSee why-doc for why.\n"},
    {"path": "bin/x.sh", "public": "# public comment\n", "live": "# live comment\n"},
    {"path": "bin/orchlib/page.html", "public": "<head>\n",
     "live": "<head>\n<link rel=\"stylesheet\" href=\"https://h.example/a.css\">\n"},
]


def trimmed_export(extra_files=""):
    with open(REAL_EXPORT, encoding="utf-8") as f:
        src = f.read()
    a, b = src.index(ANCHOR_A), src.index(ANCHOR_B)
    return src[:a] + "\n" + (TABLES % {"extra_files": extra_files}) + src[b:]


def write(path, data, exe=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)
    os.chmod(path, 0o755 if exe else 0o644)


def listing(root):
    out = {}
    for cur, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(cur, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = (hashlib.sha256(f.read()).hexdigest(), os.stat(p).st_mode)
    return out


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sync-live-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        self.live = os.path.join(self.tmp, "live")
        self.home = os.path.join(self.tmp, "home")
        self.tmpdir = os.path.join(self.tmp, "t")
        for d in (self.repo, self.live, self.home, self.tmpdir):
            os.makedirs(d)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=self.home, PYTHONDONTWRITEBYTECODE="1", TMPDIR=self.tmpdir)
        self.overlay = os.path.join(self.live, "orch-live-overlay.json")
        self.private = os.path.join(self.live, "orch-export-private.json")
        self.deny = os.path.join(self.live, "orch-export-denylist.txt")
        self.ov = {"version": 1, "edits": [dict(e) for e in EDITS_OV], "protect": [], "checks": []}
        self.write_overlay()
        write(self.private, json.dumps(PRIVATE)); os.chmod(self.private, 0o600)
        write(self.deny, "# test denylist\nSECRETWORD\n"); os.chmod(self.deny, 0o600)
        self.git("init", "-q")
        for rel, (text, exe) in REPO_FILES.items():
            write(os.path.join(self.repo, rel), text, exe)
        write(os.path.join(self.repo, "scripts", "export.py"), trimmed_export(), True)
        with open(SYNC, encoding="utf-8") as f:
            write(os.path.join(self.repo, "scripts", "sync-live.py"), f.read(), True)
        with open(BUILDER, encoding="utf-8") as f:
            write(os.path.join(self.repo, "bin", "orch-build-agents.py"), f.read(), True)
        self.commit("world")
        self.build_live()

    # --- helpers ---

    def git(self, *args):
        cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
               "-c", "core.hooksPath=/dev/null", "-c", "init.defaultBranch=main", "-C", self.repo, *args]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def write_overlay(self, mode=0o600):
        write(self.overlay, json.dumps(self.ov))
        os.chmod(self.overlay, mode)

    def managed(self):
        """live_rel -> repo_rel for the world's tables, computed by the test itself."""
        m = {r: r for r in ("skills/orchestrate/SKILL.md", "skills/orchestrate/roles/orch-a.md",
                            "agents/orch-a.md", "bin/x.sh", "bin/orch-build-agents.py")}
        m["skills/aud-x/scripts/tool.py"] = "bin/tool.py"
        for r in ("bin/orchlib/a.py", "bin/orchlib/page.html", "bin/orchlib/tests/fixtures/f.txt"):
            m[r] = r
        return m

    def build_live(self):
        """Live = overlay applied to the repo's files, written independently of sync-live."""
        for live_rel, repo_rel in self.managed().items():
            src = os.path.join(self.repo, repo_rel)
            with open(src, encoding="utf-8") as f:
                text = f.read()
            for e in self.ov["edits"]:
                if e["path"] == repo_rel:
                    self.assertEqual(text.count(e["public"]), 1)
                    text = text.replace(e["public"], e["live"])
            write(os.path.join(self.live, live_rel), text, bool(os.stat(src).st_mode & 0o100))

    def run_sync(self, *args, live=True, env=None):
        cmd = [sys.executable, os.path.join(self.repo, "scripts", "sync-live.py")]
        if live:
            cmd += ["--live", self.live]
        r = subprocess.run(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env or self.env, universal_newlines=True)
        return r.returncode, r.stdout + r.stderr

    def set_tables(self, extra_files):
        write(os.path.join(self.repo, "scripts", "export.py"), trimmed_export(extra_files), True)


class CoreTests(World):
    def test_T2_1_clean_world_nothing_to_do(self):
        before = listing(self.live)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to do", out)
        self.assertEqual(listing(self.live), before)
        self.assertFalse(os.path.exists(os.path.join(self.live, ".orch-sync")))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(os.listdir(self.tmpdir), [], "temp dir left behind")

    def test_T2_2_changed_files_and_tree_file_update(self):
        write(os.path.join(self.repo, "bin/x.sh"), "#!/bin/bash\n# public comment\necho bye\n", True)
        write(os.path.join(self.repo, "bin/orchlib/a.py"), "A = 2\n")
        self.commit("change")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("update bin/x.sh\n", out)
        self.assertIn("update bin/orchlib/a.py\n", out)
        self.assertIn("--- live/bin/x.sh", out)
        self.assertIn("+++ new/bin/x.sh", out)
        self.assertIn("-echo hi\n+echo bye\n", out)
        self.assertIn("-A = 1\n+A = 2\n", out)
        self.assertIn("0 create, 2 update", out)

    def test_T2_3_new_files_path_create(self):
        self.set_tables(', "bin/new.sh"')
        write(os.path.join(self.repo, "bin/new.sh"), "#!/bin/bash\necho new\n", True)
        self.commit("new file")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("create bin/new.sh\n", out)
        self.assertIn("1 create, 0 update", out)

    def test_T2_4_public_not_exactly_once(self):
        for public, count in (("No such text.\n", 0), ("Twin.\n", 2)):
            with self.subTest(count=count):
                self.ov["edits"] = [dict(e) for e in EDITS_OV] + [
                    {"path": "skills/orchestrate/SKILL.md", "public": public, "live": "Other.\n"}]
                self.write_overlay()
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, 2, out)
                self.assertIn("edits[3]", out)
                self.assertIn("skills/orchestrate/SKILL.md", out)
                self.assertIn("found %d times" % count, out)

    def test_T2_6_overlay_errors(self):
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        cases = {
            "unknown key": dict(self.ov, extra=1),
            "agents edit": dict(self.ov, edits=[{"path": "agents/orch-a.md", "public": "Role A.\n",
                                                 "live": "Role B.\n"}]),
            "unmanaged edit": dict(self.ov, edits=[{"path": "notes.txt", "public": "not", "live": "x"}]),
        }
        for name, ov in cases.items():
            with self.subTest(name):
                write(self.overlay, json.dumps(ov)); os.chmod(self.overlay, 0o600)
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, 2, out)
                self.assertIn({"unknown key": "extra", "agents edit": "agents/orch-a.md",
                               "unmanaged edit": "notes.txt"}[name], out)
        self.write_overlay(mode=0o644)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 2, out)
        self.assertIn("chmod 600", out)
        self.write_overlay()
        inside = os.path.join(self.repo, "ov.json")
        write(inside, json.dumps(self.ov)); os.chmod(inside, 0o600)
        rc, out = self.run_sync("--dry-run", "--rev", "HEAD", "--overlay", inside)
        self.assertEqual(rc, 2, out)
        self.assertIn("inside the repo", out)
        os.remove(inside)
        missing = os.path.join(self.tmp, "absent.json")
        rc, out = self.run_sync("--dry-run", "--overlay", missing)
        self.assertEqual(rc, 2, out)
        self.assertIn(missing, out)

    def test_T2_7_moved_key_missing_from_private(self):
        write(self.private, json.dumps({"DOC": "why-doc"})); os.chmod(self.private, 0o600)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 2, out)
        self.assertIn("bin/tool.py", out)

    def test_T2_8_unsafe_files_paths(self):
        write(os.path.join(self.repo, "settings.json"), "{}\n")
        write(os.path.join(self.repo, ".h/x"), "x\n")
        self.commit("odd files")
        for bad in ("../x", ".h/x", "settings.json", "/abs"):
            with self.subTest(bad):
                self.set_tables(', "%s"' % bad)
                self.commit("tables " + bad)
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, 2, out)
                self.assertIn("unsafe managed path", out)

    def test_T2_9_symlinks(self):
        target = os.path.join(self.tmp, "elsewhere")
        os.makedirs(target)
        shutil.copy2(os.path.join(self.live, "bin/x.sh"), os.path.join(target, "x.sh"))
        os.rename(os.path.join(self.live, "bin/x.sh"), os.path.join(self.tmp, "x.sh.bak"))
        os.symlink(os.path.join(target, "x.sh"), os.path.join(self.live, "bin/x.sh"))
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 2, out)
        self.assertIn("symlink", out)
        os.remove(os.path.join(self.live, "bin/x.sh"))
        os.rename(os.path.join(self.tmp, "x.sh.bak"), os.path.join(self.live, "bin/x.sh"))
        self.assertEqual(self.run_sync("--dry-run")[0], 0)
        tree = os.path.join(self.live, "bin/orchlib")
        shutil.move(tree, os.path.join(target, "orchlib"))
        os.symlink(os.path.join(target, "orchlib"), tree)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 2, out)
        self.assertIn("symlink", out)

    def test_T2_10_protect(self):
        self.ov["protect"] = ["skills/orchestrate"]
        self.write_overlay()
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 2, out)
        self.assertIn("protected", out)
        self.ov["protect"] = ["skills/aud-x"]
        self.write_overlay()
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to do", out)

    def test_T2_14_dirty_tree(self):
        write(os.path.join(self.repo, "notes.txt"), "modified\n")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: working tree not clean", out)
        rc, out = self.run_sync("--dry-run", "--rev", "HEAD")
        self.assertEqual(rc, 0, out)
        self.git("checkout", "--", "notes.txt")
        write(os.path.join(self.repo, "untracked.txt"), "u\n")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: working tree not clean", out)
        self.assertIn("1 refusal(s)", out)
        rc, out = self.run_sync("--dry-run", "--rev", "HEAD")
        self.assertEqual(rc, 0, out)

    def test_T2_16_cli(self):
        rc, out = self.run_sync("--dry-run", "--bogus")
        self.assertEqual(rc, 2, out)
        empty_home = os.path.join(self.tmp, "emptyhome")
        os.makedirs(empty_home)
        env = dict(self.env, HOME=empty_home)
        rc, out = self.run_sync("--dry-run", live=False, env=env)
        self.assertEqual(rc, 2, out)
        self.assertIn(os.path.join(empty_home, ".claude", "orch-live-overlay.json"), out)

    def test_T2_17_harness_anchors_once(self):
        with open(REAL_EXPORT, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count(ANCHOR_A), 1)
        self.assertEqual(src.count(ANCHOR_B), 1)


if __name__ == "__main__":
    unittest.main()
