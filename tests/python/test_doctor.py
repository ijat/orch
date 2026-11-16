"""orch doctor: every check's ok and non-ok states, the report shapes, exit codes, read-only behaviour.

Everything runs under a temporary HOME / ORCH_DIR / PATH; a fake orch-index script stands in for the binary; the plugin's
own folder (doctor.BIN_DIR) is replaced by a scratch one. Nothing of the real orch data or settings is read.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

import test_core as core  # noqa: E402
from orchlib import doctor  # noqa: E402

POSIX = os.name != "nt"
NO_WRITE = {"ORCH_DIR": "", "ORCH_DATA": ""}      # a development copy not told where to write
PIN = "0.2.0"
FAKE_INDEX = """#!/bin/sh
case "$1" in
  --version) [ -n "$FAKE_VERSION_RC" ] && exit "$FAKE_VERSION_RC"; echo "orch-index ${FAKE_VERSION:-%s}" ;;
  status) echo "$FAKE_STATUS"; exit "${FAKE_RC:-0}" ;;
  *) exit 9 ;;
esac
""" % PIN
GIT = shutil.which("git")


def make_exec(path, text="#!/bin/sh\nexit 0\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o755)
    return path


def git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                   "-c", "maintenance.auto=false", "-c", "gc.auto=0"]
                   + list(args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DoctorBase(core.Base):
    def setUp(self):
        core.Base.setUp(self)
        self.plugin = os.path.join(self.tmp, "plugin")
        self.bin = os.path.join(self.plugin, "bin")
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, "orch-install-code-index.sh"), "w") as fh:
            fh.write("#!/bin/sh\nreadonly ORCH_INDEX_VERSION=v%s\n" % PIN)
        p = mock.patch.object(doctor, "BIN_DIR", self.bin)
        p.start()
        self.addCleanup(p.stop)
        self.data = os.path.join(self.home, ".claude", "orch-data")
        p = mock.patch.object(doctor, "data_bin", return_value=os.path.join(self.data, "bin"))
        p.start()
        self.addCleanup(p.stop)
        # PATH holds git only, so no real orch-index is ever found there
        self.gitbin = os.path.join(self.tmp, "gitbin")
        os.makedirs(self.gitbin)
        os.symlink(GIT, os.path.join(self.gitbin, "git"))
        self.claude = os.path.join(self.home, ".claude")
        self.nogit = os.path.join(self.tmp, "nogit")
        os.makedirs(self.nogit)
        self.env.update({"PATH": self.gitbin, "ORCH_INDEX_REAL": "", "FAKE_VERSION_RC": "", "FAKE_STATUS": "",
                         "ORCH_DATA": self.data, "ORCH_CLAUDE_HOME": "", "ORCH_USAGE_HOME": "", "ORCH_USAGE_STATE": ""})
        saved = {k: os.environ.get(k) for k in ("CLAUDE_CODE_DISABLE_AUTO_MEMORY",)}
        os.environ.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY", None)
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in saved.items() if v is not None])

    def live_copy(self):
        """Doctor's own answer for the installed copy (the one place a fix may reach a repo's .git)."""
        p = mock.patch.object(doctor, "is_live", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def fake_index(self, **env):
        path = make_exec(os.path.join(self.tmp, "fake", "orch-index"), FAKE_INDEX)
        self.env["ORCH_INDEX_REAL"] = path
        self.env.update({k.upper(): v for k, v in env.items()})
        return path

    def make_repo(self, name="repo"):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        git(path, "init", "-q")
        git(path, "commit", "--allow-empty", "-q", "-m", "x")
        return os.path.realpath(path)

    def doctor(self, repo=None, env=None, *flags):
        rc, out, err = self.run_orch("doctor", "--json", "--repo", repo or self.nogit, *flags, env=env or {})
        self.assertIn(rc, (0, 2), "stderr=%s" % err)
        return json.loads(out), rc

    def check(self, check_id, repo=None, env=None):
        rep, _ = self.doctor(repo, env)
        return next(c for c in rep["checks"] if c["id"] == check_id)

    def write_task(self, repo, slug, state="implementing", repo_path="", worktree="", age_days=0, state_line=True,
                   raw=None):
        d = self.tdir(repo, slug)
        os.makedirs(d, exist_ok=True)
        lines = ["# Status: %s" % slug, ""]
        if state_line:
            lines.append("- task_state: %s" % state)
        lines += ["- repo: %s" % repo_path, "- worktree: %s" % worktree, ""]
        sf = os.path.join(d, "status.md")
        with open(sf, "wb") as fh:
            fh.write(raw if raw is not None else "\n".join(lines).encode("utf-8"))
        if age_days:
            t = time.time() - age_days * 86400
            os.utime(sf, (t, t))
        return d


class TestReport(DoctorBase):
    def test_json_shape_ids_areas_and_counts(self):
        rep, _ = self.doctor()
        self.assertEqual(rep["schema"], 1)
        self.assertIsNone(rep["repo"])
        ids = [c["id"] for c in rep["checks"]]
        self.assertEqual(ids, [i for i, _, _, _ in doctor.CHECKS])
        self.assertEqual(len(ids), 16)
        for c in rep["checks"]:
            self.assertEqual(sorted(c), ["area", "detail", "fix", "id", "status", "title"])
            self.assertIn(c["area"], ("index", "install", "settings", "tasks"))
            self.assertIn(c["status"], ("ok", "warn", "fail"))
            self.assertIn(c["fix"]["kind"], ("auto", "manual", "none"))
            self.assertEqual(sorted(c["fix"]), ["kind", "text"])
        want = {s: sum(1 for c in rep["checks"] if c["status"] == s) for s in ("ok", "warn", "fail")}
        self.assertEqual(rep["counts"], want)

    def test_repo_is_the_resolved_toplevel(self):
        repo = self.make_repo()
        rep, _ = self.doctor(os.path.join(repo))
        self.assertEqual(rep["repo"], repo)

    def test_text_output_groups_by_area_with_fix_and_counts_line(self):
        rc, out, _ = self.run_orch("doctor", "--repo", self.nogit)
        lines = out.splitlines()
        areas = [l for l in lines if l and not l.startswith(" ") and not l[0].isdigit()]
        self.assertEqual(areas, ["index", "install", "settings", "tasks"])
        self.assertIn("  FAIL orch-index binary: no orch-index binary found", lines)
        i = lines.index("  FAIL orch-index binary: no orch-index binary found")
        self.assertTrue(lines[i + 1].startswith("       fix (auto): "))
        self.assertRegex(lines[-1], r"^\d+ ok, \d+ warn, \d+ fail$")
        self.assertEqual(rc, 2)

    def test_exit_codes_warnings_alone_exit_0_a_fail_exits_2(self):
        self.fake_index(fake_status='{"state":"current"}')
        os.makedirs(os.path.join(self.data, "logs"))
        os.makedirs(os.path.join(self.data, "cache"))
        os.makedirs(self.root, exist_ok=True)
        rep, rc = self.doctor()
        self.assertEqual(rep["counts"]["fail"], 0, rep["checks"])
        self.assertGreater(rep["counts"]["warn"], 0)       # the dev copy and the missing usage log
        self.assertEqual(rc, 0)
        self.write_task("myapp", "broken", raw=b"\xff\xfe not utf-8")
        rep, rc = self.doctor()
        self.assertEqual(rc, 2)
        self.assertEqual(self.check("tasks.parse")["status"], "fail")

    def test_usage_errors_exit_1(self):
        self.assertEqual(self.run_orch("doctor", "--repo", os.path.join(self.tmp, "nope"))[0], 1)
        self.assertEqual(self.run_orch("doctor", "--bogus")[0], 1)

    def test_a_check_that_raises_is_a_fail_and_the_others_still_run(self):
        def boom(ctx):
            raise RuntimeError("secret detail must not appear")
        checks = [("a.boom", "index", "Boom", boom), ("a.fine", "index", "Fine", lambda ctx: doctor.ok("fine"))]
        with mock.patch.object(doctor, "CHECKS", checks):
            res = doctor.run_checks(doctor.Ctx(self.nogit))
        self.assertEqual([r["status"] for r in res], ["fail", "ok"])
        self.assertEqual(res[0]["detail"], "internal: RuntimeError")
        self.assertNotIn("secret", json.dumps(res))

    def test_read_only_nothing_is_created_or_changed(self):
        repo = self.make_repo()
        self.fake_index(fake_status='{"state":"stale","commits_behind":2}')
        self.write_task("myapp", "t1", repo_path=repo)
        os.makedirs(self.claude, exist_ok=True)
        with open(os.path.join(self.claude, "settings.json"), "w") as fh:
            fh.write("{bad")

        def snap():
            out = {}
            for base in (self.home, repo):
                for dp, _, names in os.walk(base):
                    for n in names:
                        p = os.path.join(dp, n)
                        s = os.stat(p)
                        out[p] = (s.st_size, s.st_mtime_ns)
                    out[dp] = None
            return out
        before = snap()
        rep, _ = self.doctor(repo)
        self.assertEqual(snap(), before)
        self.assertEqual(self.check("index.state", repo)["status"], "warn")
        self.assertEqual(snap(), before)


@unittest.skipUnless(POSIX, "fake orch-index is a shell script")
class TestIndex(DoctorBase):
    def test_binary_found_through_orch_index_real(self):
        path = self.fake_index()
        c = self.check("index.binary")
        self.assertEqual(c["status"], "ok")
        self.assertIn("ORCH_INDEX_REAL", c["detail"])
        self.assertIn(os.path.basename(path), c["detail"])

    def test_binary_missing_is_fail_with_auto_install_fix(self):
        c = self.check("index.binary")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("fail", "auto"))
        self.assertIn("orch-index", c["fix"]["text"])

    def test_binary_missing_in_a_development_copy_is_a_manual_fix(self):
        c = self.check("index.binary", env=NO_WRITE)
        self.assertEqual((c["status"], c["fix"]["kind"]), ("fail", "manual"))

    def test_lookup_order_real_then_bundled_then_data_then_path(self):
        target = "aarch64-apple-darwin"
        with mock.patch.object(doctor, "index_target", return_value=target):
            bundled = make_exec(os.path.join(self.bin, "vendor", "orch-index-%s" % target))
            data = make_exec(os.path.join(self.data, "bin", "orch-index"))
            onpath = make_exec(os.path.join(self.tmp, "pathbin", "orch-index"))
            real = make_exec(os.path.join(self.tmp, "real", "x"))
            env = {"PATH": os.path.dirname(onpath), "ORCH_INDEX_REAL": real}
            with mock.patch.dict(os.environ, dict(self.env, **env)):
                self.assertEqual(doctor.find_index(), (real, "ORCH_INDEX_REAL"))
                os.environ["ORCH_INDEX_REAL"] = os.path.join(self.tmp, "absent")
                self.assertEqual(doctor.find_index(), (bundled, "bundled"))
                os.unlink(bundled)
                with mock.patch.object(doctor, "data_bin", return_value=os.path.dirname(data)):
                    self.assertEqual(doctor.find_index(), (data, "data folder"))
                    os.unlink(data)
                    self.assertEqual(doctor.find_index(), (onpath, "PATH"))

    def test_the_launcher_own_folder_on_path_is_skipped(self):
        make_exec(os.path.join(self.bin, "orch-index"))
        with mock.patch.dict(os.environ, dict(self.env, PATH=self.bin, ORCH_INDEX_REAL="")), \
                mock.patch.object(doctor, "index_target", return_value=""):
            self.assertIsNone(doctor.find_index())

    def test_a_non_executable_file_is_not_a_binary(self):
        path = os.path.join(self.tmp, "real", "orch-index")
        make_exec(path)
        os.chmod(path, 0o644)
        with mock.patch.dict(os.environ, dict(self.env, ORCH_INDEX_REAL=path, PATH=self.gitbin)), \
                mock.patch.object(doctor, "index_target", return_value=""):
            self.assertIsNone(doctor.find_index())

    def test_windows_uses_the_windows_target_and_exe_names(self):
        target = "x86_64-pc-windows-msvc"
        bundled = make_exec(os.path.join(self.bin, "vendor", "orch-index-%s.exe" % target))
        make_exec(os.path.join(self.bin, "vendor", "orch-index-%s" % target))     # the wrong name is never picked
        with mock.patch.dict(os.environ, dict(self.env, ORCH_INDEX_REAL="", PATH=self.gitbin)), \
                mock.patch.object(os, "name", "nt"):
            self.assertEqual(doctor.index_target(), target)
            self.assertEqual(doctor.find_index(), (bundled, "bundled"))
            os.unlink(bundled)
            data = make_exec(os.path.join(self.data, "bin", "orch-index.exe"))
            with mock.patch.object(doctor, "data_bin", return_value=os.path.dirname(data)):
                self.assertEqual(doctor.find_index(), (data, "data folder"))
            os.unlink(data)
            onpath = make_exec(os.path.join(self.tmp, "winpath", "orch-index.exe"))
            os.environ["PATH"] = os.path.dirname(onpath)
            self.assertEqual(doctor.find_index(), (onpath, "PATH"))

    def test_unix_targets_map_like_the_launcher(self):
        cases = {("Darwin", "arm64"): "aarch64-apple-darwin", ("Darwin", "x86_64"): "x86_64-apple-darwin",
                 ("Linux", "x86_64"): "x86_64-unknown-linux-musl", ("Linux", "aarch64"): "aarch64-unknown-linux-musl",
                 ("FreeBSD", "amd64"): ""}
        for (system, machine), want in cases.items():
            with mock.patch("platform.system", return_value=system), mock.patch("platform.machine", return_value=machine):
                self.assertEqual(doctor.index_target(), want, (system, machine))

    def test_version_ok_when_it_matches_the_pin(self):
        self.fake_index()
        c = self.check("index.version")
        self.assertEqual(c["status"], "ok")
        self.assertIn(PIN, c["detail"])

    def test_version_mismatch_is_a_warning_naming_both(self):
        self.fake_index(fake_version="0.1.0")
        c = self.check("index.version")
        self.assertEqual(c["status"], "warn")
        self.assertIn("0.1.0", c["detail"])
        self.assertIn(PIN, c["detail"])

    def test_version_that_does_not_run_is_a_fail(self):
        self.fake_index(fake_version_rc="3")
        self.assertEqual(self.check("index.version")["status"], "fail")

    def test_version_without_a_binary_is_not_checked(self):
        c = self.check("index.version")
        self.assertEqual(c["status"], "warn")
        self.assertIn("not checked", c["detail"])

    def test_state_current_is_ok(self):
        repo = self.make_repo()
        self.fake_index(fake_status='{"schema":1,"state":"current"}')
        self.assertEqual(self.check("index.state", repo)["status"], "ok")

    def test_state_stale_outdated_none_warn_with_a_manual_fix(self):
        repo = self.make_repo()
        for state in ("stale", "outdated", "none"):
            self.fake_index(fake_status=json.dumps({"schema": 1, "state": state, "commits_behind": 3}))
            c = self.check("index.state", repo)
            self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"), state)
            self.assertIn(state, c["detail"])
            self.assertIn("orch:graph", c["fix"]["text"])

    def test_state_and_marker_check_the_main_checkout_from_a_linked_worktree(self):
        repo = self.make_repo()
        wt = os.path.join(self.tmp, "linked")
        git(repo, "worktree", "add", "-q", "-b", "side", wt)
        self.fake_index(fake_status='{"schema":1,"state":"stale"}')
        c = self.check("index.state", wt)
        self.assertEqual(c["status"], "warn")
        self.assertIn("main checkout %s" % repo, c["detail"])
        self.assertIn("--repo %s" % repo, c["fix"]["text"])
        self.assertNotIn("linked", c["fix"]["text"])
        self.marker(repo, "pid=%d\n" % self.dead_pid())
        c = self.check("index.marker", wt)
        self.assertEqual(c["status"], "warn")
        self.assertIn("main checkout %s" % repo, c["detail"])

    def test_state_unusable_answer_warns(self):
        repo = self.make_repo()
        self.fake_index(fake_status="not json", fake_rc="1")
        self.assertEqual(self.check("index.state", repo)["status"], "warn")

    def test_state_outside_a_repo_is_ok(self):
        self.fake_index()
        c = self.check("index.state")
        self.assertEqual((c["status"], c["detail"]), ("ok", "not in a git repo"))

    def marker(self, repo, text, age=0):
        d = os.path.join(repo, ".git", "orch-index")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "indexing")
        with open(path, "w") as fh:
            fh.write(text)
        if age:
            t = time.time() - age
            os.utime(path, (t, t))
        return path

    def dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def test_marker_absent_is_ok(self):
        repo = self.make_repo()
        self.assertEqual(self.check("index.marker", repo)["status"], "ok")

    def test_marker_of_a_running_rebuild_is_ok(self):
        repo = self.make_repo()
        self.marker(repo, "pid=%d started=2026-11-16T00:00:00Z\n" % os.getpid())
        c = self.check("index.marker", repo)
        self.assertEqual(c["status"], "ok")
        self.assertIn("running", c["detail"])

    def test_marker_with_a_dead_pid_warns_with_an_auto_fix(self):
        self.live_copy()
        repo = self.make_repo()
        self.marker(repo, "pid=%d started=2026-11-16T00:00:00Z\n" % self.dead_pid())
        c = self.check("index.marker", repo)
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "auto"))

    def test_marker_older_than_30_minutes_is_dead_even_with_a_live_pid(self):
        repo = self.make_repo()
        self.marker(repo, "pid=%d started=x\n" % os.getpid(), age=31 * 60)
        self.assertEqual(self.check("index.marker", repo)["status"], "warn")

    def test_marker_without_a_pid_is_live_while_young(self):
        repo = self.make_repo()
        self.marker(repo, "garbage\n")
        self.assertEqual(self.check("index.marker", repo)["status"], "ok")

    def test_marker_fix_is_manual_in_a_development_copy(self):
        repo = self.make_repo()
        self.marker(repo, "pid=%d\n" % self.dead_pid())
        self.assertEqual(self.check("index.marker", repo, env=NO_WRITE)["fix"]["kind"], "manual")

    def test_marker_is_found_through_a_linked_worktree(self):
        repo = self.make_repo()
        wt = os.path.join(self.tmp, "linked")
        git(repo, "worktree", "add", "-q", "-b", "side", wt)
        self.marker(repo, "pid=%d\n" % self.dead_pid())
        self.assertEqual(self.check("index.marker", wt)["status"], "warn")


class TestInstall(DoctorBase):
    def test_copy_dev_copy_told_where_to_write_is_ok(self):
        c = self.check("install.copy")
        self.assertEqual(c["status"], "ok")
        self.assertIn("development copy", c["detail"])

    def test_copy_dev_copy_with_writes_refused_warns_naming_orch_dir(self):
        c = self.check("install.copy", env=NO_WRITE)
        self.assertEqual(c["status"], "warn")
        self.assertIn("ORCH_DIR", c["fix"]["text"])

    def test_copy_installed_copy_is_ok(self):
        with mock.patch.object(doctor, "is_live", return_value=True):
            c = doctor.result("x", "install", "t", *doctor.check_install_copy(None))
        self.assertEqual((c["status"], c["detail"].split(" (")[0]), ("ok", "installed copy"))

    def listing(self, *paths):
        d = os.path.join(self.home, ".claude", "plugins")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "installed_plugins.json"), "w") as fh:
            json.dump({"version": 2, "plugins": {"orch@orch": [{"scope": "user", "installPath": p} for p in paths]}}, fh)

    @unittest.skipUnless(POSIX, "symlinks")
    def test_pointer_to_the_listed_root_is_ok(self):
        listed = os.path.join(self.tmp, "cache", "abc")
        os.makedirs(listed)
        self.listing(listed)
        os.makedirs(self.data)
        os.symlink(listed, os.path.join(self.data, "plugin"))
        self.assertEqual(self.check("install.pointer")["status"], "ok")

    @unittest.skipUnless(POSIX, "symlinks")
    def test_pointer_to_another_root_warns(self):
        listed = os.path.join(self.tmp, "cache", "new")
        old = os.path.join(self.tmp, "cache", "old")
        os.makedirs(listed)
        os.makedirs(old)
        self.listing(listed)
        os.makedirs(self.data)
        os.symlink(old, os.path.join(self.data, "plugin"))
        c = self.check("install.pointer")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))      # a development copy cannot move it
        self.assertIn("not the installed copy", c["detail"])
        self.assertIn("start a new session or run /reload-plugins; the pointer moves at SessionStart", c["fix"]["text"])

    @unittest.skipUnless(POSIX, "symlinks")
    def test_pointer_dangling_or_missing_warns_and_is_auto_only_for_the_live_copy(self):
        self.listing(os.path.realpath(self.plugin))
        c = self.check("install.pointer")
        self.assertIn("missing", c["detail"])
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        os.makedirs(self.data)
        os.symlink(os.path.join(self.tmp, "gone"), os.path.join(self.data, "plugin"))
        with mock.patch.dict(os.environ, self.env), mock.patch.object(doctor, "is_live", return_value=True):
            kind, detail = doctor.check_install_pointer(None)[2], doctor.check_install_pointer(None)[1]
        self.assertEqual(kind, "auto")
        self.assertIn("gone", detail)

    @unittest.skipUnless(POSIX, "symlinks")
    def test_pointer_without_a_listing_is_ok_when_it_resolves(self):
        os.makedirs(self.data)
        os.symlink(self.plugin, os.path.join(self.data, "plugin"))
        c = self.check("install.pointer")
        self.assertEqual(c["status"], "ok")
        self.assertIn("no installed_plugins.json entry", c["detail"])

    def test_python_version(self):
        self.assertEqual(self.check("install.python")["status"], "ok")
        with mock.patch.object(sys, "version_info", (3, 8, 0, "final", 0)):
            status, detail, kind, _ = doctor.check_install_python(None)
        self.assertEqual((status, kind), ("fail", "manual"))
        self.assertIn("3.8.0", detail)

    def test_data_folders_missing_warn_with_an_auto_fix_then_ok(self):
        c = self.check("install.data")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "auto"))
        self.assertIn("missing", c["detail"])
        for sub in ("logs", "cache"):
            os.makedirs(os.path.join(self.data, sub))
        self.assertEqual(self.check("install.data")["status"], "ok")

    def test_data_folders_missing_in_a_development_copy_is_manual(self):
        self.assertEqual(self.check("install.data", env=NO_WRITE)["fix"]["kind"], "manual")

    @unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "needs a non-root POSIX user")
    def test_data_folder_not_writable_is_a_manual_fail(self):
        for sub in ("logs", "cache"):
            os.makedirs(os.path.join(self.data, sub))
        os.chmod(os.path.join(self.data, "logs"), 0o500)
        self.addCleanup(os.chmod, os.path.join(self.data, "logs"), 0o700)
        c = self.check("install.data")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("fail", "manual"))
        self.assertIn("logs", c["detail"])


class TestSettings(DoctorBase):
    SECRET = "sk-secret-value-123"

    def orch_file(self):
        os.makedirs(self.data, exist_ok=True)
        return os.path.join(self.data, "settings.json")

    def test_orch_settings_absent_valid_malformed_and_disallowed(self):
        self.assertEqual(self.check("settings.orch")["status"], "ok")
        with open(self.orch_file(), "w") as fh:
            json.dump({"mythos_review": "on"}, fh)
        self.assertEqual(self.check("settings.orch")["status"], "ok")
        with open(self.orch_file(), "w") as fh:
            fh.write('{"mythos_review": "%s",\n bad' % self.SECRET)
        c = self.check("settings.orch")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        self.assertIn("line 2", c["detail"])
        self.assertNotIn(self.SECRET, json.dumps(c))
        with open(self.orch_file(), "w") as fh:
            json.dump({"mythos_review": self.SECRET}, fh)
        c = self.check("settings.orch")
        self.assertEqual(c["status"], "warn")
        self.assertIn("mythos_review", c["detail"])
        self.assertNotIn(self.SECRET, json.dumps(c))

    def test_orch_settings_that_is_not_an_object_warns(self):
        with open(self.orch_file(), "w") as fh:
            fh.write("[1, 2]")
        self.assertEqual(self.check("settings.orch")["status"], "warn")

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def test_claude_settings_ok_when_absent_or_valid(self):
        self.assertEqual(self.check("settings.claude")["status"], "ok")
        self.write(os.path.join(self.claude, "settings.json"), json.dumps({"env": {"TOKEN": self.SECRET}}))
        c = self.check("settings.claude")
        self.assertEqual(c["status"], "ok")
        self.assertNotIn(self.SECRET, json.dumps(c))

    def test_claude_settings_malformed_names_the_file_and_position_only(self):
        repo = self.make_repo()
        self.write(os.path.join(self.claude, "settings.json"), '{"env": {"TOKEN": "%s"},\n  oops}' % self.SECRET)
        self.write(os.path.join(repo, ".claude", "settings.local.json"), '["%s"]' % self.SECRET)
        c = self.check("settings.claude", repo)
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        self.assertIn("global", c["detail"])
        self.assertIn("line 2", c["detail"])
        self.assertIn("project local", c["detail"])
        self.assertIn("not a JSON object", c["detail"])
        self.assertNotIn(self.SECRET, json.dumps(c))

    def test_memory_auto_states(self):
        self.assertEqual(self.check("memory.auto")["status"], "ok")
        repo = self.make_repo()
        c = self.check("memory.auto", repo)
        self.assertEqual(c["status"], "ok")
        self.assertTrue(c["detail"].startswith("on for"), c["detail"])
        self.write(os.path.join(repo, ".claude", "settings.local.json"), json.dumps({"autoMemoryEnabled": False}))
        self.assertTrue(self.check("memory.auto", repo)["detail"].startswith("off for"))

    def log(self, age_seconds=None):
        path = os.path.join(self.claude, ".statusline-usage-log")
        if age_seconds is None:
            return
        t = int(time.time() - age_seconds)
        self.write(path, "%d 10 %d 20 %d 33ed49e5-80c1-4118-97a2-6586ed30f40a\n" % (t, t + 3600, t + 86400))

    def test_usage_samples_missing_stale_fresh(self):
        c = self.check("usage.samples")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        self.assertIn("UNKNOWN", c["detail"])
        self.log(3 * 3600)
        c = self.check("usage.samples")
        self.assertEqual(c["status"], "warn")
        self.assertIn("STALE", c["detail"])
        self.log(60)
        self.assertEqual(self.check("usage.samples")["status"], "ok")


class TestTasks(DoctorBase):
    def test_parse_an_old_format_status_is_counted_never_a_problem(self):
        self.write_task("myapp", "good")
        c = self.check("tasks.parse")
        self.assertEqual((c["status"], c["detail"]), ("ok", "1 task folders read (0 old-format)"))
        self.write_task("myapp", "no-state", state_line=False)
        c = self.check("tasks.parse")
        self.assertEqual((c["status"], c["detail"]), ("ok", "2 task folders read (1 old-format)"))

    def test_parse_an_undecodable_status_fails_listing_the_folder(self):
        self.write_task("myapp", "good")
        self.write_task("myapp", "garbled", raw=b"- task_state: implementing\n\xff\xfe\n")
        c = self.check("tasks.parse")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("fail", "manual"))
        self.assertIn("myapp/garbled", c["detail"])
        self.assertNotIn("myapp/good", c["detail"])

    @unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "needs a non-root POSIX user")
    def test_parse_unreadable_status_is_a_fail(self):
        d = self.write_task("myapp", "locked")
        os.chmod(os.path.join(d, "status.md"), 0)
        self.addCleanup(os.chmod, os.path.join(d, "status.md"), 0o644)
        self.assertEqual(self.check("tasks.parse")["status"], "fail")

    def test_stale_lists_only_active_tasks_idle_over_7_days(self):
        self.write_task("myapp", "fresh", age_days=1)
        self.write_task("myapp", "old-done", state="done", age_days=30)
        self.assertEqual(self.check("tasks.stale")["status"], "ok")
        self.write_task("myapp", "old-active", age_days=8)
        c = self.check("tasks.stale")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        self.assertIn("myapp/old-active", c["detail"])
        self.assertNotIn("old-done", c["detail"])
        self.assertNotIn("myapp/fresh", c["detail"])

    def test_stale_a_recent_log_keeps_a_task_fresh(self):
        d = self.write_task("myapp", "logged", age_days=20)
        with open(os.path.join(d, "log.md"), "w") as fh:
            fh.write("- now\n")
        self.assertEqual(self.check("tasks.stale")["status"], "ok")

    def test_worktree_recorded_but_gone_warns_for_this_repo_only(self):
        repo = self.make_repo()
        other = self.make_repo("other")
        here = os.path.join(self.tmp, "wt-here")
        os.makedirs(here)
        self.write_task("repo", "present", repo_path=repo, worktree=here)
        self.write_task("repo", "none-yet", repo_path=repo, worktree="none (not yet created)")
        self.write_task("repo", "finished", state="done", repo_path=repo, worktree=os.path.join(self.tmp, "wt-done"))
        self.write_task("other", "elsewhere", repo_path=other, worktree=os.path.join(self.tmp, "wt-other"))
        self.assertEqual(self.check("tasks.worktree", repo)["status"], "ok")
        self.write_task("repo", "lost", repo_path=repo, worktree=os.path.join(self.tmp, "wt-lost"))
        c = self.check("tasks.worktree", repo)
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        self.assertIn("repo/lost", c["detail"])
        self.assertNotIn("elsewhere", c["detail"])
        self.assertNotIn("present", c["detail"])

    def test_orphans_orch_worktree_with_no_task_warns_with_the_remove_command(self):
        repo = self.make_repo()
        wt = os.path.join(repo, ".claude", "worktrees", "orch-x")
        git(repo, "worktree", "add", "-q", "-b", "orch/x", wt)
        git(repo, "worktree", "add", "-q", "-b", "other", os.path.join(self.tmp, "elsewhere"))
        c = self.check("tasks.orphans", repo)
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        self.assertIn("orch-x", c["detail"])
        self.assertIn("git worktree remove %s" % os.path.realpath(wt), c["fix"]["text"])
        self.assertNotIn("elsewhere", c["detail"])

    def test_orphans_a_task_that_records_it_clears_the_warning(self):
        repo = self.make_repo()
        wt = os.path.join(repo, ".claude", "worktrees", "orch-x")
        git(repo, "worktree", "add", "-q", "-b", "orch/x", wt)
        self.write_task("repo", "x", repo_path=repo, worktree=wt)
        self.assertEqual(self.check("tasks.orphans", repo)["status"], "ok")

    def test_orphans_a_finished_task_that_kept_its_worktree_does_not_hide_it(self):
        repo = self.make_repo()
        wt = os.path.join(repo, ".claude", "worktrees", "orch-done")
        git(repo, "worktree", "add", "-q", "-b", "orch/done", wt)
        self.write_task("repo", "done-task", state="done", repo_path=repo, worktree=wt)
        c = self.check("tasks.orphans", repo)
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        self.assertIn("orch-done", c["detail"])
        self.write_task("repo", "live-task", repo_path=repo, worktree=wt)        # an active task recording it clears the warning
        self.assertEqual(self.check("tasks.orphans", repo)["status"], "ok")

    def test_orphans_seen_from_inside_a_linked_worktree(self):
        repo = self.make_repo()
        wt = os.path.join(repo, ".claude", "worktrees", "orch-x")
        git(repo, "worktree", "add", "-q", "-b", "orch/x", wt)
        self.assertEqual(self.check("tasks.orphans", wt)["status"], "warn")

    def test_worktree_and_orphan_checks_outside_a_repo_are_ok(self):
        for cid in ("tasks.worktree", "tasks.orphans"):
            self.assertEqual(self.check(cid)["detail"], "not in a git repo")


INSTALLER_OK = """#!/bin/sh
[ "$1" = --dest ] || exit 2
mkdir -p "$2" && printf '#!/bin/sh\\nexit 0\\n' > "$2/orch-index" && chmod +x "$2/orch-index"
echo "installed $2/orch-index v%s"
""" % PIN


@unittest.skipUnless(POSIX, "fake installer and orch-index are shell scripts")
class TestFix(DoctorBase):
    def setUp(self):
        DoctorBase.setUp(self)
        self.env["PATH"] = self.gitbin + os.pathsep + "/bin" + os.pathsep + "/usr/bin"     # the installer needs mkdir

    def fix(self, repo=None, env=None, *flags):
        rc, out, err = self.run_orch("doctor", "--fix", "--json", "--repo", repo or self.nogit, *flags, env=env or {})
        self.assertIn(rc, (0, 2), "stderr=%s" % err)
        return json.loads(out), rc

    def results(self, rep):
        return dict((f["id"], f) for f in rep["fixed"])

    def installer(self, text):
        return make_exec(os.path.join(self.bin, "orch-install-code-index.sh"), text)

    def marker(self, repo, text, age=0):
        d = os.path.join(repo, ".git", "orch-index")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "indexing")
        with open(path, "w") as fh:
            fh.write(text)
        if age:
            t = time.time() - age
            os.utime(path, (t, t))
        return path

    def dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def live(self):
        """The installed copy: doctor's and paths' own liveness answers."""
        return mock.patch.multiple(doctor, is_live=lambda: True), mock.patch.object(doctor.paths, "is_live", lambda: True), \
            mock.patch.object(doctor.paths, "BIN_DIR", self.bin)

    def test_a_development_copy_refuses_and_changes_nothing(self):
        rc, out, err = self.run_orch("doctor", "--fix", "--repo", self.nogit, env=NO_WRITE)
        self.assertEqual(rc, 2)
        self.assertIn("development copy", err)
        self.assertEqual(out, "")
        self.assertFalse(os.path.exists(self.data))

    # F3: from a development copy no fix writes outside ORCH_DATA (when set) or ORCH_DIR.
    def scratch_tasks(self):
        return os.path.join(self.tmp, "scratch-tasks")

    def test_dev_copy_with_only_orch_dir_never_creates_the_real_data_folders(self):
        env = {"ORCH_DATA": "", "ORCH_DIR": self.scratch_tasks()}
        c = self.check("install.data", env=env)
        self.assertEqual(c["status"], "warn")
        self.assertEqual(c["fix"]["kind"], "auto")                 # the tasks folder is in ORCH_DIR's scope
        self.assertIn("create", c["fix"]["text"])
        self.assertIn("yourself", c["fix"]["text"])                # the data root, logs and cache are not
        rep, _ = self.fix(env=env)
        f = self.results(rep)["install.data"]
        self.assertEqual(f["result"], "done")
        self.assertIn("left alone", f["detail"])
        self.assertTrue(os.path.isdir(self.scratch_tasks()))
        self.assertFalse(os.path.exists(self.data), "the real data root must not be created")
        c = next(c for c in rep["checks"] if c["id"] == "install.data")
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))

    def test_dev_copy_with_only_orch_dir_does_not_install_the_binary(self):
        ran = os.path.join(self.tmp, "installer-ran")
        self.installer("#!/bin/sh\ntouch %s\n" % ran)
        env = {"ORCH_DATA": "", "ORCH_DIR": self.root}
        c = self.check("index.binary", env=env)
        self.assertEqual((c["status"], c["fix"]["kind"]), ("fail", "manual"))
        rep, _ = self.fix(env=env)
        self.assertNotIn("index.binary", self.results(rep))
        self.assertFalse(os.path.exists(ran))
        self.assertFalse(os.path.exists(os.path.join(self.data, "bin")))

    def test_dev_copy_leaves_a_repos_marker_alone_and_the_fixers_guard_themselves(self):
        repo = self.make_repo()
        path = self.marker(repo, "pid=%d\n" % self.dead_pid())
        c = self.check("index.marker", repo)                       # ORCH_DATA is set, the repo's .git is outside it
        self.assertEqual((c["status"], c["fix"]["kind"]), ("warn", "manual"))
        rep, _ = self.fix(repo)
        self.assertNotIn("index.marker", self.results(rep))
        self.assertTrue(os.path.exists(path))
        with mock.patch.dict(os.environ, self.env):                # called directly: the fixer refuses by itself
            self.assertEqual(doctor.fix_index_marker(doctor.Ctx(repo))[0], "skipped")
        self.assertTrue(os.path.exists(path))

    def test_the_fixers_refuse_a_path_outside_the_scope_even_when_called_directly(self):
        with mock.patch.dict(os.environ, dict(self.env, ORCH_DATA="", ORCH_DIR=self.scratch_tasks())):
            self.assertEqual(os.path.expanduser("~"), self.home)   # the temp home: never the real ~/.claude
            result, detail = doctor.fix_data_folders(None)
            self.assertEqual(result, "done")                       # only ORCH_DIR's tasks folder
            for name in ("orch-data", "logs", "cache"):            # the temp home's data folders are named as left alone
                self.assertIn(name, detail.split("left alone")[1])
            self.assertEqual(doctor.fix_index_binary(None)[0], "skipped")
        self.assertTrue(os.path.isdir(self.scratch_tasks()))
        self.assertFalse(os.path.exists(self.data))

    def test_the_installed_copy_may_write_anywhere_orch_owns(self):
        self.live_copy()
        with mock.patch.dict(os.environ, dict(self.env, ORCH_DATA="", ORCH_DIR="")):
            self.assertTrue(doctor.fix_scope_allows(os.path.join(self.tmp, "anywhere")))

    def test_scope_is_exact_not_a_prefix_match(self):
        sibling = self.scratch_tasks() + "-other"
        with mock.patch.dict(os.environ, dict(self.env, ORCH_DATA="", ORCH_DIR=self.scratch_tasks())):
            self.assertTrue(doctor.fix_scope_allows(self.scratch_tasks()))
            self.assertTrue(doctor.fix_scope_allows(os.path.join(self.scratch_tasks(), "sub")))
            self.assertFalse(doctor.fix_scope_allows(sibling))

    def test_data_folders_are_created_then_the_check_passes(self):
        rep, _ = self.fix()
        f = self.results(rep)["install.data"]
        self.assertEqual(f["result"], "done")
        for sub in ("logs", "cache"):
            self.assertTrue(os.path.isdir(os.path.join(self.data, sub)))
        self.assertTrue(os.path.isdir(self.root))
        self.assertEqual(next(c for c in rep["checks"] if c["id"] == "install.data")["status"], "ok")

    def test_data_folders_that_cannot_be_created_are_a_failed_fix(self):
        with open(self.data, "w") as fh:            # a file where the data root should be
            fh.write("x")
        rep, _ = self.fix()
        self.assertEqual(self.results(rep)["install.data"]["result"], "failed")
        with open(self.data) as fh:
            self.assertEqual(fh.read(), "x")

    def test_pointer_is_repointed_only_from_the_installed_copy(self):
        os.makedirs(os.path.join(self.home, ".claude", "plugins"))
        with open(os.path.join(self.home, ".claude", "plugins", "installed_plugins.json"), "w") as fh:
            json.dump({"plugins": {"orch@orch": [{"installPath": os.path.realpath(self.plugin)}]}}, fh)
        rep, _ = self.fix()                                    # development copy: the fix is manual, never applied
        self.assertNotIn("install.pointer", self.results(rep))
        self.assertFalse(os.path.lexists(os.path.join(self.data, "plugin")))
        a, b, c = self.live()
        with a, b, c:
            rep, _ = self.fix()
        self.assertEqual(self.results(rep)["install.pointer"]["result"], "done")
        self.assertEqual(os.path.realpath(os.path.join(self.data, "plugin")), os.path.realpath(self.plugin))
        self.assertEqual(next(c for c in rep["checks"] if c["id"] == "install.pointer")["status"], "ok")

    def test_pointer_fix_skipped_from_a_development_copy_and_failed_on_an_oserror(self):
        self.assertEqual(doctor.fix_pointer(None)[0], "skipped")
        a, b, c = self.live()
        with a, b, c, mock.patch.object(doctor.paths, "update_pointer", side_effect=OSError(13, "denied")):
            self.assertEqual(doctor.fix_pointer(None)[0], "failed")

    def test_binary_is_installed_by_the_script(self):
        self.installer(INSTALLER_OK)
        rep, rc = self.fix()
        f = self.results(rep)["index.binary"]
        self.assertEqual(f["result"], "done")
        self.assertIn("installed", f["detail"])
        self.assertTrue(os.access(os.path.join(self.data, "bin", "orch-index"), os.X_OK))
        self.assertEqual(next(c for c in rep["checks"] if c["id"] == "index.binary")["status"], "ok")
        self.assertEqual(rc, 0)         # the re-check decides the exit code

    def test_binary_script_failure_is_failed_with_its_last_line_and_the_exit_stays_2(self):
        self.installer("#!/bin/sh\necho first\necho 'failed: checksum mismatch'\nexit 1\n")
        rep, rc = self.fix()
        f = self.results(rep)["index.binary"]
        self.assertEqual((f["result"], f["detail"]), ("failed", "failed: checksum mismatch"))
        self.assertEqual(rc, 2)

    def test_binary_script_that_exits_0_without_installing_is_failed(self):
        self.installer("#!/bin/sh\necho 'all good'\n")
        f = self.results(self.fix()[0])["index.binary"]
        self.assertEqual(f["result"], "failed")
        self.assertIn("no orch-index binary", f["detail"])

    def test_binary_script_exit_3_is_skipped(self):
        self.installer("#!/bin/sh\necho 'disabled by ORCH_INDEX_AUTOINSTALL'\nexit 3\n")
        f = self.results(self.fix()[0])["index.binary"]
        self.assertEqual(f["result"], "skipped")

    def test_binary_script_timeout_is_failed(self):
        self.installer("#!/bin/sh\nexec /bin/sleep 30\n")
        with mock.patch.object(doctor, "INSTALL_TIMEOUT", 1):
            f = self.results(self.fix()[0])["index.binary"]
        self.assertEqual(f["result"], "failed")
        self.assertIn("stopped", f["detail"])

    def test_binary_script_missing_is_failed(self):
        os.unlink(os.path.join(self.bin, "orch-install-code-index.sh"))
        self.assertEqual(self.results(self.fix()[0])["index.binary"]["result"], "failed")

    def test_binary_on_windows_without_git_bash_is_skipped(self):
        self.installer(INSTALLER_OK)
        with mock.patch.dict(os.environ, self.env), mock.patch.object(os, "name", "nt"), \
                mock.patch.object(doctor, "windows_bash", return_value=None):
            result, detail = doctor.fix_index_binary(None)
        self.assertEqual(result, "skipped")
        self.assertIn("Git Bash", detail)
        self.assertFalse(os.path.exists(os.path.join(self.data, "bin", "orch-index")))

    def test_windows_bash_is_found_next_to_git(self):
        gitdir = os.path.join(self.tmp, "Git", "cmd")
        make_exec(os.path.join(gitdir, "git.exe"))
        bash = make_exec(os.path.join(self.tmp, "Git", "bin", "bash.exe"))
        with mock.patch.object(shutil, "which", return_value=os.path.join(gitdir, "git.exe")):
            self.assertEqual(doctor.windows_bash(), os.path.realpath(bash))

    def test_a_dead_marker_is_removed(self):
        self.live_copy()
        repo = self.make_repo()
        path = self.marker(repo, "pid=%d started=x\n" % self.dead_pid())
        rep, _ = self.fix(repo)
        self.assertEqual(self.results(rep)["index.marker"]["result"], "done")
        self.assertFalse(os.path.exists(path))
        self.assertEqual(next(c for c in rep["checks"] if c["id"] == "index.marker")["status"], "ok")

    def test_an_old_marker_is_removed_even_when_its_pid_runs(self):
        self.live_copy()
        repo = self.make_repo()
        path = self.marker(repo, "pid=%d\n" % os.getpid(), age=31 * 60)
        self.assertEqual(self.results(self.fix(repo)[0])["index.marker"]["result"], "done")
        self.assertFalse(os.path.exists(path))

    def test_a_live_marker_survives(self):
        self.live_copy()
        repo = self.make_repo()
        path = self.marker(repo, "pid=%d\n" % os.getpid())
        rep, _ = self.fix(repo)
        self.assertNotIn("index.marker", self.results(rep))     # nothing wrong with it, so nothing to fix
        self.assertTrue(os.path.exists(path))
        ctx = doctor.Ctx(repo)
        self.assertEqual(doctor.fix_index_marker(ctx)[0], "skipped")   # and the fixer itself refuses it
        self.assertTrue(os.path.exists(path))

    def test_a_marker_that_changes_after_it_was_judged_survives(self):
        self.live_copy()
        repo = self.make_repo()
        path = self.marker(repo, "pid=%d\n" % self.dead_pid())
        ctx = doctor.Ctx(repo)

        def judged_dead_then_rewritten(pid, age):
            with open(path, "w") as fh:                 # another rebuild starts between the read and the unlink
                fh.write("pid=%d started=new\n" % os.getpid())
            return False
        with mock.patch.object(doctor, "marker_is_live", judged_dead_then_rewritten):
            result, detail = doctor.fix_index_marker(ctx)
        self.assertEqual(result, "skipped")
        self.assertIn("changed", detail)
        self.assertTrue(os.path.exists(path))

    def test_marker_fix_without_a_repo_is_skipped(self):
        self.assertEqual(doctor.fix_index_marker(doctor.Ctx(self.nogit))[0], "skipped")

    def test_fixes_run_in_order_folders_pointer_binary_marker(self):
        repo = self.make_repo()
        self.installer(INSTALLER_OK)
        self.marker(repo, "pid=%d\n" % self.dead_pid())
        os.makedirs(os.path.join(self.home, ".claude", "plugins"))
        with open(os.path.join(self.home, ".claude", "plugins", "installed_plugins.json"), "w") as fh:
            json.dump({"plugins": {"orch@orch": [{"installPath": os.path.realpath(self.plugin)}]}}, fh)
        a, b, c = self.live()
        with a, b, c:
            rep, _ = self.fix(repo)
        self.assertEqual([f["id"] for f in rep["fixed"]], ["install.data", "install.pointer", "index.binary", "index.marker"])
        self.assertEqual([f["result"] for f in rep["fixed"]], ["done"] * 4)

    def test_only_the_fixers_of_decision_d5_ever_run(self):
        other = doctor.result("tasks.orphans", "tasks", "t", "warn", "d", "auto", "remove it")
        self.assertEqual(doctor.apply_fixes([other], None), [])
        self.assertEqual([i for i, _ in doctor.FIXERS], ["install.data", "install.pointer", "index.binary", "index.marker"])

    def test_a_fixer_that_raises_is_failed_and_the_next_one_still_runs(self):
        results = [doctor.result("install.data", "install", "t", "warn", "d", "auto", "x"),
                   doctor.result("index.marker", "index", "t", "warn", "d", "auto", "x")]
        fixers = [("install.data", lambda ctx: 1 / 0), ("index.marker", lambda ctx: ("done", "ok"))]
        with mock.patch.object(doctor, "FIXERS", fixers):
            fixed = doctor.apply_fixes(results, None)
        self.assertEqual([(f["id"], f["result"]) for f in fixed], [("install.data", "failed"), ("index.marker", "done")])
        self.assertEqual(fixed[0]["detail"], "internal: ZeroDivisionError")

    def test_nothing_to_fix_and_the_text_form(self):
        os.makedirs(os.path.join(self.data, "logs"))
        os.makedirs(os.path.join(self.data, "cache"))
        self.fake_index(fake_status='{"state":"current"}')
        rep, rc = self.fix()
        self.assertEqual(rep["fixed"], [])
        self.assertEqual(rc, 0)
        rc, out, _ = self.run_orch("doctor", "--fix", "--repo", self.nogit)
        self.assertIn("nothing to fix automatically", out)
        self.installer(INSTALLER_OK)
        os.unlink(self.env["ORCH_INDEX_REAL"])
        rc, out, _ = self.run_orch("doctor", "--fix", "--repo", self.nogit)
        self.assertIn("fix index.binary: done (installed", out)


if __name__ == "__main__":
    unittest.main()
