#!/usr/bin/env python3
"""Tests for sync-live.py.

Each test builds a throwaway world under a temp dir: a git repo whose scripts/export.py is the REAL
export.py with only its table block replaced (so the file-set code under test is the real one), a
live dir written by the test itself as overlay(repo) with a manifest of the hashes the test computed
from its own files, and a private overlay, private file and denylist. Every run uses a temp HOME, a temp TMPDIR and an explicit --live; nothing reads the real
~/.claude.

Run: python3 scripts/test_sync_live.py
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_EXPORT = os.path.join(HERE, "export.py")
SYNC = os.path.join(HERE, "sync-live.py")
BUILDER = os.path.join(os.path.dirname(HERE), "bin", "orch-build-agents.py")
sys.dont_write_bytecode = True

BUILD_CMD = "python3 bin/orch-build-agents.py skills/orchestrate/roles agents"

# An overlay check: behaves per MODE, telling live from stage by ORCH_HOME == the {live} argument.
CHECK_SCRIPT = """import os, sys, time
mode, live = sys.argv[1], sys.argv[2]
root = os.environ["ORCH_HOME"]
on_live = root == live
if mode == "both":
    print("WARN same thing")
elif mode == "stage_only" and not on_live:
    print("WARN fresh thing")
elif mode == "root":
    print("WARN referenced template missing: " + root + "/templates/x.md")
elif mode == "realpath":
    print("WARN referenced template missing: " + os.path.realpath(root) + "/templates/x.md")
elif mode == "fail_live" and on_live:
    sys.exit(3)
elif mode == "sleep":
    time.sleep(30)
print("ok")
"""

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

    def build_live(self, manifest=True):
        """Live = overlay applied to the repo's files, written independently of sync-live; then a
        manifest of exactly those files at HEAD (unless manifest=False)."""
        for live_rel, repo_rel in self.managed().items():
            src = os.path.join(self.repo, repo_rel)
            with open(src, encoding="utf-8") as f:
                text = f.read()
            for e in self.ov["edits"]:
                if e["path"] == repo_rel:
                    self.assertEqual(text.count(e["public"]), 1)
                    text = text.replace(e["public"], e["live"])
            write(os.path.join(self.live, live_rel), text, bool(os.stat(src).st_mode & 0o100))
        if manifest:
            self.write_manifest()

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def write_manifest(self, files=None, rev=None, record="20260101T000000Z", raw=None):
        """<live>/.orch-sync/manifest.json for `files` (live_rel -> repo_rel, default managed()), with
        the sha256 and owner-exec bit the test reads from the live files it wrote."""
        state = os.path.join(self.live, ".orch-sync")
        os.makedirs(state, exist_ok=True)
        if raw is None:
            entries = {}
            for lr, rr in (files or self.managed()).items():
                lp = os.path.join(self.live, lr)
                with open(lp, "rb") as f:
                    sha = hashlib.sha256(f.read()).hexdigest()
                entries[lr] = {"sha256": sha, "exec": bool(os.stat(lp).st_mode & 0o100), "repo_path": rr}
            raw = json.dumps({"version": 1, "rev": rev or self.head(), "tree": "0" * 40,
                              "synced_at": "2026-01-01T00:00:00Z", "record": record,
                              "overlay_sha256": "0" * 64, "files": entries})
        write(os.path.join(state, "manifest.json"), raw)

    def write_record(self, stamp, status):
        write(os.path.join(self.live, ".orch-sync", "syncs", stamp, "record.json"),
              json.dumps({"version": 1, "stamp": stamp, "kind": "sync", "status": status, "ops": []}))

    def drop_state(self):
        shutil.rmtree(os.path.join(self.live, ".orch-sync"))

    def run_sync(self, *args, live=True, env=None):
        cmd = [sys.executable, os.path.join(self.repo, "scripts", "sync-live.py")]
        if live:
            cmd += ["--live", self.live]
        r = subprocess.run(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env or self.env, universal_newlines=True)
        return r.returncode, r.stdout + r.stderr

    def set_tables(self, extra_files):
        write(os.path.join(self.repo, "scripts", "export.py"), trimmed_export(extra_files), True)

    def set_check(self, mode, name="chk"):
        script = os.path.join(self.tmp, "check.py")
        write(script, CHECK_SCRIPT)
        self.ov["checks"] = [{"name": name, "argv": [sys.executable, "-I", "-B", script, mode, "{live}"]}]
        self.write_overlay()

    def module(self):
        """sync-live.py imported in-process (for gate-level tests), bytecode off."""
        spec = importlib.util.spec_from_file_location("sync_live_under_test", SYNC)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def sandbox_env(self):
        return mock.patch.dict(os.environ, {"HOME": self.home, "TMPDIR": self.tmpdir,
                                            "PYTHONDONTWRITEBYTECODE": "1"})


class CoreTests(World):
    def test_T2_1_clean_world_nothing_to_do(self):
        before = listing(self.live)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to do", out)
        self.assertEqual(listing(self.live), before)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(os.listdir(self.tmpdir), [], "temp dir left behind")
        # without any state, the (init) dry run creates none
        self.drop_state()
        before = listing(self.live)
        rc, out = self.run_sync("--dry-run", "--init", "HEAD")
        self.assertEqual(rc, 0, out)
        self.assertEqual(listing(self.live), before)
        self.assertFalse(os.path.exists(os.path.join(self.live, ".orch-sync")))
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


class GateTests(World):
    def test_T2_5_overlay_not_inverting_export(self):
        self.ov["edits"].append({"path": "bin/orchlib/a.py", "public": "A = 1\n", "live": "A = 1  # live\n"})
        self.write_overlay()
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: round trip: bin/orchlib/a.py differs", out)

    def test_T2_11_stale_agent_render(self):
        write(os.path.join(self.repo, "agents/orch-a.md"), "Role A, stale.\n")
        self.commit("stale render")
        self.build_live()
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: agents:", out)
        self.assertIn(BUILD_CMD, out)

    def test_T2_12_syntax_errors(self):
        cases = {"bin/x.sh": ("#!/bin/bash\n# public comment\nif then fi (\n", True),
                 "bin/orchlib/a.py": ("A = (\n", False)}
        for rel, (text, exe) in cases.items():
            with self.subTest(rel):
                good = os.path.join(self.tmp, "good")
                shutil.copy2(os.path.join(self.repo, rel), good)
                write(os.path.join(self.repo, rel), text, exe)
                self.commit("syntax error in " + rel)
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, 1, out)
                self.assertIn("REFUSE: syntax: %s:" % rel, out)
                shutil.copy2(good, os.path.join(self.repo, rel))
                self.commit("fix " + rel)

    def test_T2_13_checks(self):
        for mode, want in (("both", 0), ("root", 0), ("stage_only", 1), ("fail_live", 1)):
            with self.subTest(mode):
                self.set_check(mode)
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, want, out)
                if mode == "stage_only":
                    self.assertIn("REFUSE: check chk: new WARN fresh thing", out)
                if mode == "fail_live":
                    self.assertIn("REFUSE: check chk fails on current live", out)

    def test_T2_15_tree_extras(self):
        extra = os.path.join(self.live, "bin/orchlib/extra.py")
        write(extra, "E = 1\n")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: round trip: bin/orchlib/extra.py differs", out)
        os.remove(extra)
        ignored = os.path.join(self.live, "bin/orchlib/readme.txt")
        write(ignored, "not taken by the tree filter\n")
        before = listing(self.live)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to do", out)
        self.assertEqual(listing(self.live), before)

    def test_T2b_1_exec_bit_lost_in_stage(self):
        mod = self.module()
        with self.sandbox_env():
            snap = os.path.join(self.tmp, "snap")
            os.makedirs(snap)
            files = mod.snapshot(self.repo, "HEAD", snap)
            self.assertTrue(files["bin/x.sh"])
            stage = os.path.join(self.tmp, "stage")
            shutil.copytree(self.live, stage)
            work = os.path.join(self.tmp, "work")
            os.makedirs(work)
            self.assertEqual(mod.gate_roundtrip(snap, files, stage, self.private, self.deny, work), [])
            os.chmod(os.path.join(stage, "bin/x.sh"), 0o644)
            self.assertEqual(mod.gate_roundtrip(snap, files, stage, self.private, self.deny, work),
                             ["round trip: bin/x.sh differs"])

    def test_T2b_2_snapshot_stays_pristine(self):
        mod = self.module()
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        old = sys.dont_write_bytecode
        sys.dont_write_bytecode = False  # so only sync-live's own guard keeps bytecode out
        try:
            with self.sandbox_env(), contextlib.redirect_stdout(io.StringIO()) as buf:
                rc = mod.run(mod.parse_args(["--dry-run", "--live", self.live]), self.repo, work)
        finally:
            sys.dont_write_bytecode = old
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("nothing to do", buf.getvalue())
        snap = os.path.join(work, "snapshot")
        got = sorted(os.path.relpath(os.path.join(c, n), snap) for c, _d, ns in os.walk(snap) for n in ns)
        want = sorted(self.git("ls-tree", "-r", "--name-only", "HEAD").split())
        self.assertEqual(got, want)

    def test_T2b_3_realpath_of_root_in_warn(self):
        link = os.path.join(self.tmp, "lnk")
        os.symlink(self.tmp, link)
        live = os.path.join(link, "live")
        self.assertNotEqual(os.path.realpath(live), live)
        self.set_check("realpath")
        env = dict(self.env, TMPDIR=os.path.join(link, "t"))
        rc, out = self.run_sync("--dry-run", "--live", live, live=False, env=env)
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to do", out)

    def test_T2b_4_check_timeout(self):
        self.set_check("sleep", name="slow")
        env = dict(self.env, SYNC_LIVE_CHECK_TIMEOUT="0.5")
        t0 = time.time()
        rc, out = self.run_sync("--dry-run", env=env)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: check slow fails on current live: timed out", out)
        self.assertLess(time.time() - t0, 20)


class StateTests(World):
    def test_T3_1_manifest_and_init_presence(self):
        rc, out = self.run_sync("--dry-run", "--init", "HEAD")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: --init: a manifest already exists", out)
        self.drop_state()
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: first sync: pass --init <rev>", out)

    def test_T3_2_init_proof(self):
        self.drop_state()
        base = self.head()
        write(os.path.join(self.repo, "bin/orchlib/a.py"), "A = 2\n")
        self.commit("change")
        rc, out = self.run_sync("--dry-run", "--init", base)
        self.assertEqual(rc, 0, out)
        self.assertIn("update bin/orchlib/a.py\n", out)
        self.assertIn("1 update, 0 delete, 0 adopt", out)
        self.assertNotIn("nothing to do", out)
        sk = os.path.join(self.live, "skills/orchestrate/SKILL.md")
        with open(sk, encoding="utf-8") as f:
            text = f.read()
        write(sk, text.replace("Body line.\n", "Body line, hand edited.\n"))
        rc, out = self.run_sync("--dry-run", "--init", base)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: init proof failed: round trip: skills/orchestrate/SKILL.md differs", out)

    def test_T3_3_init_adopt_conflict_left_extra(self):
        self.drop_state()
        base = self.head()
        self.set_tables(', "bin/new.sh"')
        write(os.path.join(self.repo, "bin/new.sh"), "#!/bin/bash\necho new\n", True)
        self.commit("new file")
        for live_text, want in (("#!/bin/bash\necho new\n", 0), ("#!/bin/bash\necho other\n", 1)):
            with self.subTest(want=want):
                write(os.path.join(self.live, "bin/new.sh"), live_text, True)
                rc, out = self.run_sync("--dry-run", "--init", base)
                self.assertEqual(rc, want, out)
                if want == 0:
                    self.assertIn("adopt bin/new.sh\n", out)
                    self.assertIn("0 create, 0 update, 0 delete, 1 adopt", out)
                else:
                    self.assertIn("conflict bin/new.sh\n", out)
                    self.assertIn("REFUSE: conflict bin/new.sh: unmanaged live file would be overwritten", out)
        os.remove(os.path.join(self.live, "bin/new.sh"))
        # baseline with an extra FILES path and an extra tree file, both dropped by HEAD
        self.set_tables(', "bin/old.sh"')
        write(os.path.join(self.repo, "bin/old.sh"), "#!/bin/bash\necho old\n", True)
        write(os.path.join(self.repo, "bin/orchlib/old.py"), "OLD = 1\n")
        self.commit("baseline with old files")
        base = self.head()
        for rel in ("bin/old.sh", "bin/orchlib/old.py"):
            shutil.copy2(os.path.join(self.repo, rel), os.path.join(self.live, rel))
        self.set_tables("")
        os.remove(os.path.join(self.repo, "bin/old.sh"))
        os.remove(os.path.join(self.repo, "bin/orchlib/old.py"))
        self.commit("drop old files")
        rc, out = self.run_sync("--dry-run", "--init", base)
        self.assertEqual(rc, 1, out)
        self.assertIn("left in place bin/old.sh\n", out)
        self.assertNotIn("delete bin/old.sh", out)
        self.assertNotIn("REFUSE: extra bin/old.sh", out)
        self.assertNotIn("REFUSE: init proof failed", out)
        self.assertIn("REFUSE: extra bin/orchlib/old.py", out)
        self.assertIn("0 delete", out)

    def test_T3_4_drift(self):
        cases = {
            "content": ("bin/orchlib/a.py", lambda p: write(p, "A = 99\n")),
            "deletion": ("bin/orchlib/tests/fixtures/f.txt", os.remove),
            "exec bit": ("bin/x.sh", lambda p: os.chmod(p, 0o644)),
        }
        for name, (rel, act) in cases.items():
            with self.subTest(name):
                p = os.path.join(self.live, rel)
                keep = os.path.join(self.tmp, "keep")
                shutil.copy2(p, keep)
                act(p)
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, 1, out)
                self.assertIn("REFUSE: drift %s" % rel, out)
                shutil.copy2(keep, p)
                self.assertEqual(self.run_sync("--dry-run")[0], 0)

    def test_T3_5_delete(self):
        self.set_tables(', "bin/old.sh"')
        write(os.path.join(self.repo, "bin/old.sh"), "#!/bin/bash\necho old\n", True)
        write(os.path.join(self.repo, "bin/orchlib/old.py"), "OLD = 1\n")
        self.commit("with old files")
        files = dict(self.managed(), **{"bin/old.sh": "bin/old.sh", "bin/orchlib/old.py": "bin/orchlib/old.py"})
        for rel in ("bin/old.sh", "bin/orchlib/old.py"):
            shutil.copy2(os.path.join(self.repo, rel), os.path.join(self.live, rel))
        self.write_manifest(files)
        self.assertEqual(self.run_sync("--dry-run")[0], 0)
        self.set_tables("")
        os.remove(os.path.join(self.repo, "bin/old.sh"))
        os.remove(os.path.join(self.repo, "bin/orchlib/old.py"))
        self.commit("drop old files")
        before = listing(self.live)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)  # the deleted tree file is not staged as an extra: round trip passes
        self.assertIn("delete bin/old.sh\n", out)
        self.assertIn("delete bin/orchlib/old.py\n", out)
        self.assertIn("0 create, 0 update, 2 delete, 0 adopt", out)
        self.assertEqual(listing(self.live), before)

    def test_T3_5b_unsafe_manifest_path(self):
        files = dict(self.managed(), **{"settings.json": "settings.json"})
        write(os.path.join(self.live, "settings.json"), "{}\n")
        self.write_manifest(files)
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 2, out)
        self.assertIn("unsafe managed path settings.json", out)

    def test_T3_6_tree_extra(self):
        write(os.path.join(self.live, "bin/orchlib/stray.py"), "S = 1\n")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: extra bin/orchlib/stray.py", out)

    def test_T3_7_interrupted(self):
        for status in ("pending", "rollback-failed"):
            with self.subTest(status):
                self.write_record("20260202T000000Z", status)
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, 1, out)
                self.assertIn("REFUSE: interrupted sync 20260202T000000Z: run --rollback", out)
        for status in ("done", "rolled-back"):
            with self.subTest(status):
                self.write_record("20260202T000000Z", status)
                self.assertEqual(self.run_sync("--dry-run")[0], 0)

    def test_T3_8_malformed_manifest(self):
        good = {"version": 1, "rev": self.head(), "tree": "0" * 40, "synced_at": "x", "record": "s",
                "overlay_sha256": "0" * 64, "files": {}}
        cases = {
            "not json": "{nope",
            "bad version": json.dumps(dict(good, version=2)),
            "unknown key": json.dumps(dict(good, extra=1)),
            "bad rev": json.dumps(dict(good, rev="--output=x")),
            "bad entry": json.dumps(dict(good, files={"bin/x.sh": {"sha256": "z", "exec": True,
                                                                   "repo_path": "bin/x.sh"}})),
        }
        for name, raw in cases.items():
            with self.subTest(name):
                self.write_manifest(raw=raw)
                rc, out = self.run_sync("--dry-run")
                self.assertEqual(rc, 2, out)
                self.assertIn("manifest", out)

    def _branch_a_synced(self):
        """Branch b at the world commit; branch a changes bin/orchlib/a.py; live + manifest = a's image."""
        self.git("branch", "b")
        self.git("checkout", "-q", "-b", "a")
        write(os.path.join(self.repo, "bin/orchlib/a.py"), "A = 2\n")
        self.commit("a: change a.py")
        self.build_live()
        self.git("checkout", "-q", "b")

    def test_T3_9_not_descendant_refused(self):
        self._branch_a_synced()
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: not-descendant: last synced rev", out)
        self.assertIn("1 managed file(s) changed by it would revert: bin/orchlib/a.py", out)
        self.assertIn("(1 file(s) differ from the last sync in total)", out)

    def test_T3_10_not_descendant_allowed(self):
        self._branch_a_synced()
        rc, out = self.run_sync("--dry-run", "--allow-non-descendant")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE not-descendant: ", out)
        self.assertNotIn("REFUSE", out)
        self.assertIn("update bin/orchlib/a.py\n", out)

    def test_T3_11_merged_with_no_ff(self):
        self._branch_a_synced()
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge a", "a")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("not-descendant", out)
        self.assertIn("nothing to do", out)

    def test_T3_12_unknown_manifest_rev(self):
        self.write_manifest(rev="0123456789abcdef0123456789abcdef01234567")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: not-descendant: last synced rev 0123456", out)
        self.assertIn("unknown managed file(s)", out)
        rc, out = self.run_sync("--dry-run", "--allow-non-descendant")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE not-descendant: ", out)

    def test_T3_13_completed_pending(self):
        self.write_manifest(record="20260303T000000Z")
        self.write_record("20260303T000000Z", "pending")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE completed sync 20260303T000000Z was left pending; the next real run marks it done",
                      out)
        self.assertNotIn("interrupted", out)
        self.write_record("20260304T000000Z", "pending")
        rc, out = self.run_sync("--dry-run")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSE: interrupted sync 20260304T000000Z: run --rollback", out)


if __name__ == "__main__":
    unittest.main()
