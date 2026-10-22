"""Core CLI tests: init, set, role, log, journal, show, shared machinery. Neutral fixtures only."""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN)

from orchlib import cli, journal as jr, paths, status as st  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
LAUNCHER = os.path.join(BIN, "orch")
DEFAULT_NOW = "2026-03-01T10:00Z"


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def tree_hash(root):
    h = hashlib.sha256()
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.root = os.path.join(self.home, ".claude", "orchestrations")
        os.makedirs(self.root)
        self.env = {"HOME": self.home, "ORCH_DIR": self.root, "ORCH_NO_INDEX": "1", "ORCH_NOW": DEFAULT_NOW}

    def run_orch(self, *argv, **kw):
        env = dict(self.env)
        env.update(kw.get("env", {}))
        if "now" in kw:
            env["ORCH_NOW"] = kw["now"]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def ok(self, *argv, **kw):
        rc, out, err = self.run_orch(*argv, **kw)
        self.assertEqual(rc, 0, "argv=%r\nstdout=%s\nstderr=%s" % (argv, out, err))
        return out, err

    def refuse(self, code, *argv, **kw):
        rc, out, err = self.run_orch(*argv, **kw)
        self.assertEqual(rc, code, "argv=%r\nstdout=%s\nstderr=%s" % (argv, out, err))
        return err

    def tdir(self, repo="myapp", slug="add-login"):
        return os.path.join(self.root, repo, slug)

    def init(self, repo="myapp", slug="add-login", mode="standard", **kw):
        self.ok("init", repo, slug, "--repo-path", "/home/u/Projects/%s" % repo, "--base", "main",
                "--mode", mode, "--request", "Add a login page", **kw)
        return self.tdir(repo, slug)

    def fixture_task(self, name, repo="myapp", slug="fix-menu"):
        d = self.tdir(repo, slug)
        os.makedirs(d)
        shutil.copy(os.path.join(FIX, name), os.path.join(d, "status.md"))
        return d

    def read(self, *parts):
        with open(os.path.join(*parts), "rb") as fh:
            return fh.read()

    def journal_lines(self, d):
        return [json.loads(l) for l in self.read(d, "journal.jsonl").decode("utf-8").splitlines()]


class TestLauncherAndGuard(Base):
    def run_launcher(self, *argv, **env):
        e = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1"}
        e.update(env)
        return subprocess.run([sys.executable, LAUNCHER] + list(argv), env=e, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def test_version_and_commands(self):
        r = self.run_launcher("--version")
        self.assertEqual((r.returncode, r.stdout), (0, "orch %s\n" % __import__("orchlib").__version__))
        r = self.run_launcher("commands")
        self.assertEqual(r.returncode, 0)
        names = r.stdout.split()
        for want in ("init", "set", "role", "log", "journal", "show", "commands"):
            self.assertIn(want, names)

    def test_launcher_is_executable_and_uses_its_own_orchlib(self):
        self.assertTrue(os.access(LAUNCHER, os.X_OK))
        r = self.run_launcher("show")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no active tasks"))

    def test_development_guard_blocks_writes_without_orch_dir(self):
        before = tree_hash(self.home)
        for argv in (["init", "myapp", "add-login", "--repo-path", "/home/u/p", "--base", "main", "--request", "x"],
                     ["log", "hello", "-t", "myapp/add-login"],
                     ["set", "mode=lite", "-t", "myapp/add-login"],
                     ["role", "planner", "done", "-t", "myapp/add-login"],
                     ["journal", "override", "what=x", "-t", "myapp/add-login"]):
            r = self.run_launcher(*argv)
            self.assertEqual(r.returncode, 1, argv)
            self.assertIn("development copy: set ORCH_DIR", r.stderr)
        self.assertEqual(tree_hash(self.home), before)

    def test_symlinked_launcher_uses_the_real_bin_and_its_templates(self):
        link_dir = os.path.join(self.tmp, "linkbin")
        os.makedirs(link_dir)
        link = os.path.join(link_dir, "orch")
        os.symlink(LAUNCHER, link)
        e = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1",
             "ORCH_DIR": self.root, "ORCH_NO_INDEX": "1"}
        r = subprocess.run([sys.executable, link, "init", "myapp", "linked", "--repo-path", "/home/u/p", "--base",
                            "main", "--request", "x"], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(paths.TEMPLATES_DIR.startswith(os.path.realpath(os.path.join(BIN, ".."))))
        with open(os.path.join(self.tdir("myapp", "linked"), "status.md"), encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("# Status: linked\n"))

    def test_write_allowed_with_orch_dir_set(self):
        r = self.run_launcher("init", "myapp", "add-login", "--repo-path", "/home/u/p", "--base", "main",
                              "--request", "x", ORCH_DIR=self.root, ORCH_NO_INDEX="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.tdir(), "status.md")))


class TestExitCodes(Base):
    def test_usage_errors_exit_1(self):
        self.refuse(1)
        self.refuse(1, "nonsense")
        self.refuse(1, "set")
        self.refuse(1, "role", "planner")
        self.refuse(1, "init", "myapp")

    def test_missing_task_and_bad_task_arg(self):
        self.refuse(1, "log", "x")
        self.refuse(1, "log", "x", "-t", "myapp/none")
        self.refuse(1, "log", "x", "-t", "justaslug")
        self.refuse(1, "log", "x", "-t", "../x/y")

    def test_exists_exit_2(self):
        self.init()
        err = self.refuse(2, "init", "myapp", "add-login", "--repo-path", "/home/u/p", "--base", "main", "--request", "x")
        self.assertIn("exists", err)
        self.assertIn("orch resume", err)

    def test_internal_error_exit_3_and_nothing_written(self):
        d = self.init()
        before = tree_hash(d)
        with mock.patch.object(st.Status, "set_field", side_effect=RuntimeError("boom")):
            rc, out, err = self.run_orch("set", "mode=lite", "-t", "myapp/add-login")
        self.assertEqual(rc, 3)
        self.assertIn("internal error", err)
        self.assertEqual(tree_hash(d), before)

    def test_t_flag_before_or_after_command(self):
        self.init()
        self.ok("-t", "myapp/add-login", "log", "before")
        self.ok("log", "after", "-t", "myapp/add-login")
        self.ok("log", "abs", "--task", self.tdir())
        text = self.read(self.tdir(), "log.md").decode()
        for w in ("before", "after", "abs"):
            self.assertIn(": %s\n" % w, text)


class TestInit(Base):
    def test_fields_rows_and_files(self):
        d = self.init()
        text = self.read(d, "status.md").decode()
        want = {
            "task_state": "planning", "mode": "standard", "shape": "tbd (planner decides)", "milestone": "n/a",
            "reviewed_through": "none", "merged_through": "none",
            "task_dir": "~/.claude/orchestrations/myapp/add-login", "repo": "/home/u/Projects/myapp",
            "base_branch": "main", "task_branch": "orch/add-login", "worktree": "not yet created",
            "planner_tier": "tbd", "implementer_tier": "per step (TIERS.md routing)", "review_round": "0/3",
            "usage_override": "none",
        }
        s = st.Status(text.encode())
        for k, v in want.items():
            self.assertEqual(s.get(k), v, k)
        roles = dict(s.roles())
        self.assertEqual(roles["orchestrator"]["state"], "in_progress")
        self.assertEqual(roles["orchestrator"]["ts"], DEFAULT_NOW)
        self.assertEqual(roles["planner"]["state"], "pending")
        self.assertEqual(roles["security-reviewer"]["state"], "not_offered")
        self.assertTrue(text.startswith("# Status: add-login\n"))
        self.assertEqual(os.stat(os.path.join(d, "status.md")).st_mode & 0o777, 0o644)
        log = self.read(d, "log.md").decode()
        self.assertTrue(log.startswith("# Log: add-login\n"))
        self.assertRegex(log.splitlines()[-1], r"^- 2026-03-01T10:00Z orchestrator: task dir created")
        (rec,) = self.journal_lines(d)
        self.assertEqual(rec, {"ts": DEFAULT_NOW, "repo": "myapp", "task": "add-login", "kind": "task_start",
                               "mode": "standard", "request": "Add a login page"})

    def test_lite_planner_row(self):
        d = self.init(mode="lite")
        roles = dict(st.Status.load(os.path.join(d, "status.md")).roles())
        self.assertEqual(roles["planner"]["state"], "n/a (lite)")
        self.assertEqual(self.journal_lines(d)[0]["mode"], "lite")

    def test_repo_with_space_and_task_arg_forms(self):
        d = self.init(repo="My App", slug="fix-menu")
        self.assertEqual(d, os.path.join(self.root, "My App", "fix-menu"))
        self.ok("log", "hello", "-t", "My App/fix-menu")
        self.assertIn(": hello\n", self.read(d, "log.md").decode())
        self.assertEqual(self.journal_lines(d)[0]["repo"], "My App")

    def test_bad_names_and_mode(self):
        for repo, slug in (("my/app", "x"), (".hidden", "x"), ("myapp", "bad slug"), ("myapp", "-x"), ("myapp", "a/b")):
            self.refuse(1, "init", repo, slug, "--repo-path", "/home/u/p", "--base", "main", "--request", "x")
        self.refuse(1, "init", "myapp", "x", "--repo-path", "/home/u/p", "--base", "main", "--request", "x",
                  "--mode", "huge")
        self.assertEqual(os.listdir(self.root), [])

    def test_multiline_request_is_one_line(self):
        d = self.init()
        self.ok("init", "myapp", "two", "--repo-path", "/home/u/p", "--base", "main", "--request", "line one\nline two")
        self.assertEqual(self.journal_lines(self.tdir("myapp", "two"))[0]["request"], "line one line two")
        self.assertTrue(d)


class TestGolden(Base):
    SEQUENCE = [
        (("init", "myapp", "add-login", "--repo-path", "/home/u/Projects/myapp", "--base", "main", "--request",
          "Add a login page"), "2026-03-01T10:00Z"),
        (("role", "planner", "in_progress", "--note", "reading the code", "-t", "myapp/add-login"), "2026-03-01T10:01Z"),
        (("journal", "tier_choice", "role=planner", "agent=orch-planner", "tier=opus/high",
          "reason=cross-cutting change", 'alternatives:=["opus/max"]', "tradeoff=default", "-t", "myapp/add-login"),
         "2026-03-01T10:02Z"),
        (("role", "planner", "done", "--step", "plan written", "--note", "plan.md ready", "-t", "myapp/add-login"),
         "2026-03-01T10:05Z"),
        (("set", "task_state=awaiting_approval", "-t", "myapp/add-login"), "2026-03-01T10:05Z"),
        (("log", "plan ready for approval", "-t", "myapp/add-login"), "2026-03-01T10:06Z"),
        (("set", "task_state=implementing", "implementer_tier=sonnet/high", "--log", "plan approved", "-t",
          "myapp/add-login"), "2026-03-01T10:20Z"),
        (("role", "implementer", "in_progress", "--step", "step 1", "-t", "myapp/add-login"), "2026-03-01T10:20Z"),
        (("journal", "spawn", "role=implementer", "agent=orch-implementer", "tier=sonnet/high", "steps=1,2",
          "usage_5h:=12", "usage_7d:=40", "-t", "myapp/add-login"), "2026-03-01T10:21Z"),
        (("role", "implementer", "done", "--step", "step 2", "--note", "2 commits", "-t", "myapp/add-login"),
         "2026-03-01T11:00Z"),
        (("journal", "return", "role=implementer", "agent=orch-implementer", "outcome=done", "tokens:=81000",
          "tool_uses:=64", "duration_ms:=null", "-t", "myapp/add-login"), "2026-03-01T11:00Z"),
        (("role", "code-reviewer", "in_progress", "-t", "myapp/add-login"), "2026-03-01T11:05Z"),
        (("set", "worktree=/home/u/Projects/myapp/.claude/worktrees/orch-add-login", "review_round=1/3", "-t",
          "myapp/add-login"), "2026-03-01T11:10Z"),
        (("journal", "checkpoint", "questions:=2", "asked:=true", "-t", "myapp/add-login"), "2026-03-01T11:30Z"),
        (("role", "code-reviewer", "done", "--step", "round 1", "--note", "1 should-fix", "-t", "myapp/add-login"),
         "2026-03-01T11:30Z"),
        (("log", "round 1 findings routed to the implementer", "--as", "orchestrator", "-t", "myapp/add-login"),
         "2026-03-01T11:31Z"),
    ]

    def test_sequence_matches_fixtures(self):
        for argv, now in self.SEQUENCE:
            self.ok(*argv, now=now)
        d = self.tdir()
        for name in ("status.md", "log.md", "journal.jsonl"):
            with open(os.path.join(FIX, "golden", name), "rb") as fh:
                want = fh.read()
            self.assertEqual(self.read(d, name), want, name)

    def test_counts_in_the_contract(self):
        kinds = [a[0] for a, _ in self.SEQUENCE]
        self.assertEqual(kinds.count("init"), 1)
        self.assertEqual(kinds.count("role"), 6)
        self.assertEqual(kinds.count("set"), 3)
        self.assertEqual(kinds.count("journal"), 4)
        self.assertEqual(kinds.count("log"), 2)


class TestStatusFidelity(Base):
    def edit_and_diff(self, data, *argv):
        d = self.tdir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "status.md")
        with open(path, "wb") as fh:
            fh.write(data)
        self.ok(*argv)
        new = self.read(d, "status.md")
        old_lines = data.split(b"\n")
        new_lines = new.split(b"\n")
        return old_lines, new_lines

    def test_only_target_line_changes(self):
        data = self.read(FIX, "status_variant_a.md")
        old, new = self.edit_and_diff(data, "set", "task_state=reviewing", "-t", "myapp/add-login")
        self.assertEqual(len(old), len(new))
        diff = [i for i in range(len(old)) if old[i] != new[i]]
        self.assertEqual(len(diff), 1)
        self.assertEqual(new[diff[0]], b"- task_state: reviewing")

    def test_trailing_spaces_and_row_spacing_kept(self):
        data = self.read(FIX, "status_variant_a.md")
        old, new = self.edit_and_diff(data, "role", "implementer", "in_progress", "-t", "myapp/add-login")
        diff = [i for i in range(len(old)) if old[i] != new[i]]
        self.assertEqual(len(diff), 1)
        self.assertIn(b"| orchestrator | in_progress | 2026-03-01T10:00Z | task dir created | keep   trailing  spaces |",
                      new)
        self.assertIn(b"- review_round: 0/3   ", new)
        self.assertEqual(new[diff[0]], b"| implementer | in_progress | 2026-03-01T10:00Z |  |  |")

    def test_crlf_and_missing_final_newline(self):
        data = self.read(FIX, "status_variant_b.md").replace(b"\n", b"\r\n").rstrip(b"\r\n")
        self.assertFalse(data.endswith(b"\n"))
        old, new = self.edit_and_diff(data, "set", "mode=standard", "-t", "myapp/add-login")
        self.assertEqual(new[3], b"- mode: standard\r")
        self.assertEqual(len([i for i in range(len(old)) if old[i] != new[i]]), 1)
        self.assertFalse(b"\n".join(new).endswith(b"\n"))
        # now edit the final row (no newline after it) and insert a row after it
        d = self.tdir()
        self.ok("role", "implementer", "in_progress", "--note", "n", "-t", "myapp/add-login")
        self.ok("role", "director", "pending", "-t", "myapp/add-login")
        out = self.read(d, "status.md")
        self.assertFalse(out.endswith(b"\n"))
        self.assertEqual(out.count(b"\r\n"), data.count(b"\r\n") + 1)
        self.assertTrue(out.split(b"\r\n")[-1].startswith(b"| director | pending |"))

    def test_non_utf8_bytes_survive_an_edit(self):
        data = self.read(FIX, "status_variant_b.md").replace(b"two commits", b"caf\xe9 \xff bytes")
        old, new = self.edit_and_diff(data, "set", "mode=standard", "-t", "myapp/add-login")
        self.assertIn(b"caf\xe9 \xff bytes", b"\n".join(new))
        self.assertEqual(len([i for i in range(len(old)) if old[i] != new[i]]), 1)

    def test_unchanged_value_does_not_touch_the_file(self):
        d = self.init()
        p = os.path.join(d, "status.md")
        os.utime(p, (1000000, 1000000))
        out, _ = self.ok("set", "mode=standard", "-t", "myapp/add-login")
        self.assertIn("no change", out)
        self.assertEqual(os.stat(p).st_mtime, 1000000)
        out, _ = self.ok("role", "planner", "pending", "--ts", "", "-t", "myapp/add-login")
        self.assertIn("no change", out)
        self.assertEqual(os.stat(p).st_mtime, 1000000)

    def test_mode_is_kept(self):
        d = self.init()
        p = os.path.join(d, "status.md")
        os.chmod(p, 0o600)
        self.ok("set", "mode=lite", "-t", "myapp/add-login")
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_no_temp_files_left_and_failed_replace_keeps_original(self):
        d = self.init()
        before = sha(os.path.join(d, "status.md"))
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            rc, _, err = self.run_orch("set", "mode=lite", "-t", "myapp/add-login")
        self.assertEqual(rc, 3)
        self.assertEqual(sha(os.path.join(d, "status.md")), before)
        self.assertEqual(sorted(os.listdir(d)), [".orch.lock", "journal.jsonl", "log.md", "status.md"])


class TestSet(Base):
    def test_unknown_field_needs_add(self):
        d = self.init()
        before = sha(os.path.join(d, "status.md"))
        err = self.refuse(1, "set", "colour=blue", "-t", "myapp/add-login")
        self.assertIn("--add", err)
        self.assertEqual(sha(os.path.join(d, "status.md")), before)
        self.ok("set", "colour=blue", "--add", "-t", "myapp/add-login")
        self.assertEqual(st.Status.load(os.path.join(d, "status.md")).get("colour"), "blue")

    def test_task_state_enum_from_template(self):
        d = self.init()
        for good in st.task_state_enum():
            self.ok("set", "task_state=%s" % good, "-t", "myapp/add-login")
        before = sha(os.path.join(d, "status.md"))
        for bad in ("**done**", "done (merged)", "planning | done", "Done", ""):
            self.refuse(1, "set", "task_state=%s" % bad, "-t", "myapp/add-login")
        self.assertEqual(sha(os.path.join(d, "status.md")), before)

    def test_enum_is_read_from_the_template_not_hardcoded(self):
        tpl = os.path.join(self.tmp, "tpl")
        os.makedirs(tpl)
        with open(os.path.join(tpl, "status.md"), "w") as fh:
            fh.write("# Status: x\n\n- task_state: alpha | beta\n")
        self.assertEqual(st.task_state_enum(tpl), ["alpha", "beta"])

    def test_one_bad_pair_writes_nothing(self):
        d = self.init()
        before = tree_hash(d)
        self.refuse(1, "set", "mode=lite", "nofield=1", "-t", "myapp/add-login")
        self.refuse(1, "set", "mode=lite", "oops", "-t", "myapp/add-login")
        self.assertEqual(tree_hash(d), before)

    def test_multiline_value_refused(self):
        d = self.init()
        before = sha(os.path.join(d, "status.md"))
        self.refuse(1, "set", "usage_override=a\nb", "-t", "myapp/add-login")
        self.assertEqual(sha(os.path.join(d, "status.md")), before)

    def test_value_with_equals_and_interspersed_options(self):
        d = self.init()
        self.ok("set", "usage_override=a=b", "--log", "tweak", "review_round=1/3", "--as", "implementer",
                "-t", "myapp/add-login")
        s = st.Status.load(os.path.join(d, "status.md"))
        self.assertEqual((s.get("usage_override"), s.get("review_round")), ("a=b", "1/3"))
        self.assertTrue(self.read(d, "log.md").decode().endswith(" implementer: tweak\n"))

    def test_field_in_free_text_after_table_is_not_a_field(self):
        d = self.fixture_task("status_variant_a.md")
        with open(os.path.join(d, "status.md"), "a") as fh:
            fh.write("- mode: mentioned in prose\n")
        self.refuse(1, "set", "mode=lite", "-t", "myapp/fix-menu")

    def test_add_inserts_after_last_field(self):
        d = self.fixture_task("status_variant_a.md")
        self.ok("set", "mode=lite", "--add", "-t", "myapp/fix-menu")
        lines = self.read(d, "status.md").decode().split("\n")
        i = lines.index("- mode: lite")
        self.assertTrue(lines[i - 1].startswith("- review_round:"))
        self.assertEqual(lines[i + 1], "")


class TestRole(Base):
    def test_header_variants(self):
        # (fixture, header columns) -> which cells exist
        d = self.fixture_task("status_variant_b.md")
        self.ok("role", "implementer", "blocked", "--step", "ignored", "--note", "stuck", "--ts", "2026-03-01T12:00Z",
                "-t", "myapp/fix-menu")
        row = [l for l in self.read(d, "status.md").decode().splitlines() if l.startswith("| implementer")][0]
        self.assertEqual(row, "| implementer | blocked | 2026-03-01T12:00Z | stuck |")
        d = self.fixture_task("status_variant_c.md", slug="fix-menu-c")
        self.ok("role", "code-reviewer", "in_progress", "--step", "round 1", "--note", "n", "-t", "myapp/fix-menu-c")
        row = [l for l in self.read(d, "status.md").decode().splitlines() if l.startswith("| code-reviewer")][0]
        self.assertEqual(row, "| code-reviewer | in_progress | round 1 | 2026-03-01T10:00Z |")

    def test_missing_column_is_a_warning_not_an_error(self):
        self.fixture_task("status_variant_b.md")
        rc, out, err = self.run_orch("role", "implementer", "done", "--step", "s", "-t", "myapp/fix-menu")
        self.assertEqual(rc, 0)
        self.assertIn("no column for: step", err)

    def test_row_with_other_cell_count_refused(self):
        d = self.fixture_task("status_variant_d.md")
        before = sha(os.path.join(d, "status.md"))
        err = self.refuse(1, "role", "orchestrator", "done", "-t", "myapp/fix-menu")
        self.assertIn("cells", err)
        self.assertEqual(sha(os.path.join(d, "status.md")), before)
        self.ok("role", "planner", "waiting", "-t", "myapp/fix-menu")  # a well-formed row still works

    def test_unknown_layout_refused(self):
        d = self.fixture_task("status_no_table.md")
        before = sha(os.path.join(d, "status.md"))
        err = self.refuse(1, "role", "planner", "done", "-t", "myapp/fix-menu")
        self.assertIn("role", err)
        self.assertEqual(sha(os.path.join(d, "status.md")), before)

    def test_states(self):
        self.init()
        for s in st.ROLE_STATES:
            self.ok("role", "implementer", s, "-t", "myapp/add-login")
        self.refuse(1, "role", "implementer", "finished", "-t", "myapp/add-login")
        self.refuse(1, "role", "implementer", "Done", "-t", "myapp/add-login")

    def test_missing_row_inserted_after_last_row(self):
        d = self.init()
        self.ok("role", "implementer:ui", "in_progress", "--step", "ui", "--note", "n", "-t", "myapp/add-login")
        lines = self.read(d, "status.md").decode().split("\n")
        i = [k for k, l in enumerate(lines) if l.startswith("| implementer:ui")][0]
        self.assertTrue(lines[i - 1].startswith("| security-reviewer"))
        self.assertEqual(lines[i], "| implementer:ui | in_progress | 2026-03-01T10:00Z | ui | n |")
        self.assertEqual(lines[i + 1], "")
        self.refuse(1, "role", "nobody", "pending", "-t", "myapp/add-login")

    def test_pipes_and_newlines_in_values(self):
        d = self.init()
        self.ok("role", "planner", "done", "--note", "a | b\nc", "--step", "x|y", "-t", "myapp/add-login")
        row = [l for l in self.read(d, "status.md").decode().splitlines() if l.startswith("| planner")][0]
        self.assertEqual(row, "| planner | done | 2026-03-01T10:00Z | x/y | a / b c |")

    def test_options_not_given_keep_cells(self):
        d = self.init()
        self.ok("role", "planner", "in_progress", "--step", "s1", "--note", "n1", "-t", "myapp/add-login")
        self.ok("role", "planner", "done", "-t", "myapp/add-login", now="2026-03-01T10:09Z")
        row = [l for l in self.read(d, "status.md").decode().splitlines() if l.startswith("| planner")][0]
        self.assertEqual(row, "| planner | done | 2026-03-01T10:09Z | s1 | n1 |")

    def test_log_line_as_role(self):
        d = self.init()
        self.ok("role", "implementer", "in_progress", "--log", "step 1 started", "-t", "myapp/add-login")
        self.assertTrue(self.read(d, "log.md").decode().endswith("- 2026-03-01T10:00Z implementer: step 1 started\n"))

    def test_legacy_role_names_still_updatable(self):
        d = self.fixture_task("status_variant_a.md")
        text = self.read(d, "status.md").decode().replace("| implementer | pending |  |  |  |",
                                                           "| implementer (fix round 1) | pending |  |  |  |")
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write(text)
        self.ok("role", "implementer (fix round 1)", "done", "-t", "myapp/fix-menu")


class TestValueRule(Base):
    def test_patterns(self):
        proj = os.path.join(self.tmp, "Projects")
        space = os.path.join(proj, "My App")
        os.makedirs(space)
        c = st.clean_value("repo", "%s (main, no worktree)" % space)
        self.assertEqual((c.value, c.missing), (space, False))
        c = st.clean_value("repo", "%s/gone (main)" % space)
        self.assertEqual((c.value, c.missing), (space + "/gone", True))
        self.assertEqual(st.show_value("repo", "%s/gone (main)" % space), space + "/gone (missing)")
        c = st.clean_value("repo", space)
        self.assertEqual((c.value, c.missing), (space, False))
        self.assertEqual(st.clean_value("worktree", "none (user: no worktree)"), ("none", False))
        self.assertEqual(st.clean_value("worktree", "not yet created"), ("not yet created", False))
        self.assertEqual(st.clean_value("worktree", "removed after merge (2026-03-01)"), ("removed after merge", False))
        self.assertEqual(st.clean_value("base_branch", "main (direct, no worktree)"), ("main", False))
        self.assertEqual(st.clean_value("task_branch", "orch/add-login"), ("orch/add-login", False))

    def test_template_text_is_unknown(self):
        tpl = {
            "worktree": "/abs/path/to/repo/.claude/worktrees/orch-<task-slug> | not yet created | none (user: no worktree)",
            "base_branch": "<branch>", "task_branch": "orch/<task-slug>",
            "reviewed_through": "none | <sha>   (roadmap only)", "merged_through": "none | <sha>   (roadmap only)",
            "repo": "<repo>", "task_dir": "~/.claude/orchestrations/<repo>/<slug>",
        }
        for name, raw in tpl.items():
            self.assertEqual(st.clean_value(name, raw), ("unknown", False), name)

    def test_tilde_and_absent(self):
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            os.makedirs(os.path.join(self.tmp, "x"))
            self.assertEqual(st.clean_value("task_dir", "~/x"), (os.path.join(self.tmp, "x"), False))
            self.assertEqual(st.clean_value("task_dir", "~/y"), (os.path.join(self.tmp, "y"), True))
        self.assertEqual(st.clean_value("repo", None), ("unknown", False))
        self.assertEqual(st.clean_value("repo", ""), ("unknown", False))
        self.assertEqual(st.clean_value("repo", "   (none)"), ("(none)", True))

    def test_show_applies_the_rule(self):
        d = self.fixture_task("status_variant_a.md")
        out, _ = self.ok("show", "-t", "myapp/fix-menu")
        self.assertIn("repo: /home/u/Projects/myapp (missing)", out)
        self.assertIn("base_branch: main\n", out)
        self.assertIn("worktree: none\n", out)
        self.assertTrue(d)


class TestJournal(Base):
    def setUp(self):
        Base.setUp(self)
        self.d = self.init()

    def j(self, *args, **kw):
        return self.ok("journal", *(list(args) + ["-t", "myapp/add-login"]), **kw)

    def last(self):
        return self.journal_lines(self.d)[-1]

    def last_raw(self):
        return self.read(self.d, "journal.jsonl").decode("utf-8").splitlines()[-1]

    def test_schema_is_parsed_from_the_template(self):
        sch = jr.load_schema()
        self.assertEqual(sch.kinds["task_start"]["fields"], ["mode", "request"])
        self.assertEqual(sch.kinds["return"]["fields"], ["role", "agent", "outcome", "tokens", "tool_uses", "duration_ms"])
        self.assertTrue(sch.kinds["tier_choice"]["choice"])
        self.assertFalse(sch.kinds["spawn"]["choice"])
        self.assertIn("pause", sch.kinds)
        self.assertIn("resume", sch.kinds)
        self.assertEqual(sch.kinds["pause"]["fields"], ["usage_5h"])
        # backticked values inside parentheses are not fields
        self.assertNotIn("done", sch.kinds["return"]["fields"])
        self.assertEqual(sch.kinds["security_offer"]["fields"], ["areas", "answer", "late"])
        self.assertEqual(sch.kinds["milestone_start"]["fields"], ["milestone"])
        self.assertEqual(sch.kinds["milestone_end"]["fields"], ["milestone", "outcome", "tiers", "rounds"])
        self.assertEqual(sch.kinds["replan"]["fields"], ["milestone", "changed", "why"])
        self.assertEqual(sch.kinds["review_round"]["fields"][-1], "sha")
        for k, f in (("checkpoint", ["questions", "asked"]), ("auto_merge", ["sha", "base", "branch", "revert"]),
                     ("auto_security", ["areas", "ran", "rule"]),
                     ("assumed_decision", ["decision", "reversible", "role", "question"]),
                     ("final_gate", ["verdict", "sha", "command"])):
            self.assertEqual(sch.kinds[k]["fields"], f, k)

    def test_types_are_per_kind(self):
        sch = jr.load_schema()
        self.assertEqual(sch.type_of("review_point", "late"), "integer")
        self.assertEqual(sch.type_of("security_offer", "late"), "boolean")
        self.assertEqual(sch.type_of("spawn", "steps"), "array")
        self.assertEqual(sch.type_of("anything", "alternatives"), "array")
        self.assertEqual(sch.type_of("replan", "milestone"), "integer")
        self.assertEqual(sch.type_of("spawn", "tier"), None)
        self.assertEqual(sch.type_of("merge_point", "tree_equal"), "boolean")
        self.assertIsNone(sch.type_of("merge_point", "ff"))

    def test_line_format_and_key_order(self):
        self.j("spawn", "role=implementer", "agent=orch-implementer", "tier=sonnet/high", "steps=1,2",
               "usage_5h=12", "usage_7d=40", "extra=x")
        self.assertEqual(self.last_raw(),
                         '{"ts":"2026-03-01T10:00Z","repo":"myapp","task":"add-login","kind":"spawn","role":"implementer",'
                         '"agent":"orch-implementer","tier":"sonnet/high","steps":[1,2],"usage_5h":12,"usage_7d":40,'
                         '"extra":"x"}')

    def test_schema_order_wins_over_argument_order(self):
        self.j("return", "tokens=5", "outcome=done", "agent=a", "role=r", "tool_uses=2", "duration_ms=3")
        self.assertEqual(list(self.last()), ["ts", "repo", "task", "kind", "role", "agent", "outcome", "tokens",
                                             "tool_uses", "duration_ms"])

    def test_missing_fields_become_null_with_one_warning(self):
        out, err = self.j("spawn", "role=implementer", "agent=x")
        self.assertEqual(err.count("warning:"), 1)
        self.assertIn("tier, steps, usage_5h, usage_7d", err)
        rec = self.last()
        self.assertTrue(all(rec[k] is None for k in ("tier", "steps", "usage_5h", "usage_7d")))

    def test_choice_kinds_need_reason_and_alternatives(self):
        self.refuse(1, "journal", "tier_choice", "role=planner", "tier=opus/high", "tradeoff=t", "-t", "myapp/add-login")
        self.refuse(1, "journal", "tier_choice", "role=planner", "reason=r", "-t", "myapp/add-login")
        self.refuse(1, "journal", "judgement", "decision=d", "confidence=low", "reason=r", "alternatives:=null",
                  "-t", "myapp/add-login")
        n = len(self.journal_lines(self.d))
        self.j("judgement", "decision=d", "confidence=low", "reason=r", "alternatives:=[]")
        self.assertEqual(len(self.journal_lines(self.d)), n + 1)
        self.assertEqual(self.last()["alternatives"], [])
        self.j("plan_shape", "shape=single", "criteria=1,3", "tier=opus/high", "overridden=false", "reason=r",
               "alternatives=")
        self.assertEqual(self.last()["criteria"], [1, 3])
        self.assertEqual(self.last()["alternatives"], [])

    def test_unknown_kind_and_bad_keys(self):
        self.refuse(1, "journal", "nope", "-t", "myapp/add-login")
        self.refuse(1, "journal", "override", "ts=2026-01-01T00:00Z", "-t", "myapp/add-login")
        self.refuse(1, "journal", "override", "kind=x", "-t", "myapp/add-login")
        self.refuse(1, "journal", "override", "what=a", "what=b", "-t", "myapp/add-login")
        self.refuse(1, "journal", "override", "Bad-Key=a", "-t", "myapp/add-login")
        self.refuse(1, "journal", "override", "justaword", "-t", "myapp/add-login")
        self.assertEqual(len(self.journal_lines(self.d)), 1)

    def test_extras_allowed(self):
        self.j("override", "what=x", "note=y", "n:=3", "flag:=true", "obj:={\"a\":[1,2]}")
        rec = self.last()
        self.assertEqual((rec["note"], rec["n"], rec["flag"], rec["obj"]), ("y", 3, True, {"a": [1, 2]}))

    def test_typed_coercion_table(self):
        good = [
            ("return", "tokens=81000", ("tokens", 81000)), ("return", "tokens=-3", ("tokens", -3)),
            ("return", "tokens:=7", ("tokens", 7)), ("return", "tokens:=null", ("tokens", None)),
            ("checkpoint", "asked=true", ("asked", True)), ("checkpoint", "asked=false", ("asked", False)),
            ("checkpoint", "asked:=true", ("asked", True)),
            ("spawn", "steps=1,2,3", ("steps", [1, 2, 3])), ("spawn", "steps=M1 phase 1", ("steps", ["M1 phase 1"])),
            ("spawn", "steps=1,x", ("steps", ["1", "x"])), ("spawn", "steps=", ("steps", [])),
            ("spawn", 'steps:=["a",2]', ("steps", ["a", 2])),
            ("review_point", "late=2", ("late", 2)), ("security_offer", "late=true", ("late", True)),
            ("spawn", "tier=7", ("tier", "7")),  # untyped stays a string
            ("override", "what=true", ("what", "true")),
            ("replan", "milestone=3", ("milestone", 3)),
        ]
        for kind, arg, (key, want) in good:
            self.j(kind, arg)
            self.assertEqual(self.last()[key], want, (kind, arg))
            self.assertIs(type(self.last()[key]), type(want), (kind, arg))
        bad = [
            ("return", "tokens=12x"), ("return", "tokens=1.5"), ("return", "tokens="), ("return", "tokens=null"),
            ("return", "tokens:=\"7\""), ("return", "tokens:=true"), ("return", "tokens:=1.5"),
            ("checkpoint", "asked=yes"), ("checkpoint", "asked=1"), ("checkpoint", "asked:=1"),
            ("spawn", "steps:=\"1\""), ("spawn", "steps:={}"), ("review_point", "late=true"),
            ("security_offer", "late=2"), ("replan", "milestone=x"),
            ("override", "what:={bad"), ("override", "what:=NaN"), ("override", "what:="),
        ]
        for kind, arg in bad:
            n = len(self.journal_lines(self.d))
            self.refuse(1, "journal", kind, arg, "-t", "myapp/add-login")
            self.assertEqual(len(self.journal_lines(self.d)), n, (kind, arg))

    def test_integers_must_not_be_floats_or_bools(self):
        self.refuse(1, "journal", "return", "tokens:=true", "-t", "myapp/add-login")
        self.refuse(1, "journal", "return", "tokens:=2.0", "-t", "myapp/add-login")

    def test_unicode_is_raw_utf8_and_round_trips(self):
        self.j("override", "what=caf\u00e9 \u2014 \u65e5\u672c")
        raw = self.read(self.d, "journal.jsonl")
        self.assertIn("caf\u00e9 \u2014 \u65e5\u672c".encode("utf-8"), raw)
        self.assertNotIn(b"\\u00e9", raw)
        self.assertEqual(self.last()["what"], "caf\u00e9 \u2014 \u65e5\u672c")

    def test_quotes_backslashes_and_newlines_in_values(self):
        val = 'say "hi" \\ back\nslash\tTab'
        self.j("override", "what=%s" % val)
        self.assertEqual(self.last()["what"], val)
        self.assertEqual(len(self.read(self.d, "journal.jsonl").split(b"\n")), 3)  # 2 lines + final empty

    def test_lone_surrogate_refused(self):
        rc, _, err = self.run_orch("journal", "override", "what:=\"\\ud800\"", "-t", "myapp/add-login")
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.journal_lines(self.d)), 1)

    def test_ts_flag_and_orch_now(self):
        self.j("override", "what=a", "--ts", "2026-01-01T00:00Z")
        self.assertEqual(self.last()["ts"], "2026-01-01T00:00Z")
        self.j("override", "what=b", now="2026-05-05T05:05Z")
        self.assertEqual(self.last()["ts"], "2026-05-05T05:05Z")
        self.refuse(1, "journal", "override", "what=c", "-t", "myapp/add-login", env={"ORCH_NOW": "yesterday"})

    def test_appends_newline_if_file_lacks_one(self):
        p = os.path.join(self.d, "journal.jsonl")
        data = self.read(self.d, "journal.jsonl").rstrip(b"\n")
        with open(p, "wb") as fh:
            fh.write(data)
        self.j("override", "what=x")
        lines = self.read(self.d, "journal.jsonl").decode().split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[0])["kind"], "task_start")

    def test_set_and_log_ride_along(self):
        self.j("gate", "gate=plan", "outcome=approved", "--set", "task_state=implementing", "--set", "review_round=0/3",
               "--log", "plan approved", "--as", "orchestrator")
        s = st.Status.load(os.path.join(self.d, "status.md"))
        self.assertEqual(s.get("task_state"), "implementing")
        self.assertTrue(self.read(self.d, "log.md").decode().endswith("orchestrator: plan approved\n"))
        n = len(self.journal_lines(self.d))
        self.refuse(1, "journal", "gate", "gate=plan", "--set", "task_state=bogus", "-t", "myapp/add-login")
        self.assertEqual(len(self.journal_lines(self.d)), n)

    def test_program_adds_milestone_after_kind(self):
        self.ok("set", "shape=roadmap", "milestone=M3 \u2014 implementing", "-t", "myapp/add-login")
        self.j("override", "what=x")
        self.assertEqual(list(self.last())[:6], ["ts", "repo", "task", "kind", "milestone", "what"])
        self.assertEqual(self.last()["milestone"], 3)
        self.j("override", "what=y", "milestone=5")
        self.assertEqual(self.last()["milestone"], 5)
        self.j("milestone_start", "milestone=4")
        self.assertEqual(list(self.last()), ["ts", "repo", "task", "kind", "milestone"])
        self.assertEqual(self.last()["milestone"], 4)
        self.j("milestone_end", "milestone=4", "outcome=done", "tiers=sonnet/high", "rounds=1")
        self.assertEqual(self.last()["tiers"], ["sonnet/high"])
        self.ok("set", "milestone=n/a", "-t", "myapp/add-login")
        self.j("override", "what=z")
        self.assertIsNone(self.last()["milestone"])
        self.assertIn('"milestone":null', self.last_raw())

    def test_only_shape_roadmap_exactly_is_a_program(self):
        for shape in ("single", "roadmap (draft)", "tbd (planner decides)", "Roadmap"):
            self.ok("set", "shape=%s" % shape, "-t", "myapp/add-login")
            self.j("override", "what=x")
            self.assertNotIn("milestone", self.last(), shape)

    def test_non_program_milestone_given_is_an_extra(self):
        self.j("override", "what=x", "milestone=2")
        self.assertEqual(list(self.last())[-1], "milestone")
        self.assertEqual(self.last()["milestone"], 2)

    def test_journal_without_status_md(self):
        d = os.path.join(self.root, "myapp", "bare")
        os.makedirs(d)
        self.ok("journal", "override", "what=x", "-t", "myapp/bare")
        self.assertEqual(self.journal_lines(d)[0]["task"], "bare")
        self.refuse(1, "journal", "override", "what=x", "--set", "mode=lite", "-t", "myapp/bare")

    def test_journal_template_table_edit_is_picked_up(self):
        tpl = os.path.join(self.tmp, "tpl")
        os.makedirs(tpl)
        with open(os.path.join(tpl, "journal.md"), "w") as fh:
            fh.write("| kind | when | extra fields |\n|---|---|---|\n| `ping` | x | `a`, `b` (`x`/`y`) |\n\n"
                     "- integer: `ping.a`\n")
        sch = jr.load_schema(tpl)
        self.assertEqual(sch.kinds["ping"]["fields"], ["a", "b"])
        self.assertEqual(sch.type_of("ping", "a"), "integer")

    def test_template_types_line_covers_known_kinds_and_fields(self):
        sch = jr.load_schema()
        for (kind, field) in sch.types:
            if kind == "*":
                continue
            self.assertIn(kind, sch.kinds, kind)
            self.assertIn(field, sch.kinds[kind]["fields"] + ["reason", "alternatives"], (kind, field))


class TestLog(Base):
    def test_format_and_default_role(self):
        d = self.init()
        self.ok("log", "something happened", "-t", "myapp/add-login")
        self.ok("log", "other", "--as", "code-reviewer", "-t", "myapp/add-login", now="2026-03-01T11:00Z")
        tail = self.read(d, "log.md").decode().splitlines()[-2:]
        self.assertEqual(tail, ["- 2026-03-01T10:00Z orchestrator: something happened",
                                "- 2026-03-01T11:00Z code-reviewer: other"])

    def test_multiline_text_collapsed_and_empty_refused(self):
        d = self.init()
        self.ok("log", "a\nb  \n c", "-t", "myapp/add-login")
        self.assertTrue(self.read(d, "log.md").decode().endswith("orchestrator: a b c\n"))
        self.refuse(1, "log", "  ", "-t", "myapp/add-login")
        self.refuse(1, "log", "x", "--as", "bad|role", "-t", "myapp/add-login")

    def test_created_from_template_when_missing_and_newline_repaired(self):
        d = self.init()
        os.unlink(os.path.join(d, "log.md"))
        self.ok("log", "first", "-t", "myapp/add-login")
        text = self.read(d, "log.md").decode()
        self.assertTrue(text.startswith("# Log: add-login\n"))
        self.assertTrue(text.endswith("orchestrator: first\n"))
        with open(os.path.join(d, "log.md"), "ab") as fh:
            fh.write(b"- partial line without newline")
        self.ok("log", "second", "-t", "myapp/add-login")
        lines = self.read(d, "log.md").decode().splitlines()
        self.assertEqual(lines[-2], "- partial line without newline")
        self.assertTrue(lines[-1].endswith("orchestrator: second"))


class TestConcurrency(Base):
    def spawn(self, *argv):
        e = dict(os.environ)
        e.update(self.env)
        return subprocess.Popen([sys.executable, LAUNCHER] + list(argv), env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)

    def test_parallel_journal_log_and_role_writers(self):
        d = self.init()
        procs = []
        for i in range(8):
            procs.append(self.spawn("journal", "override", "what=w%d" % i, "-t", "myapp/add-login"))
            procs.append(self.spawn("log", "line %d" % i, "-t", "myapp/add-login"))
        roles = ["planner", "implementer", "director", "plan-reviewer", "code-reviewer", "security-reviewer"]
        for r in roles:
            procs.append(self.spawn("role", r, "done", "--note", "n-" + r, "-t", "myapp/add-login"))
        for i in range(4):
            procs.append(self.spawn("set", "colour=c%d" % i, "--add", "-t", "myapp/add-login"))
        for p in procs:
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err)
        lines = self.journal_lines(d)
        self.assertEqual(sorted(l["what"] for l in lines if l["kind"] == "override"), ["w%d" % i for i in range(8)])
        log = self.read(d, "log.md").decode()
        for i in range(8):
            self.assertIn("orchestrator: line %d\n" % i, log)
        s = st.Status.load(os.path.join(d, "status.md"))
        rows = dict(s.roles())
        for r in roles:
            self.assertEqual((rows[r]["state"], rows[r]["notes"]), ("done", "n-" + r))
        self.assertEqual(len([n for _, n, _ in s.fields() if n == "colour"]), 1)


class TestIndexAndShow(Base):
    def test_index_refreshed_after_status_writes_only(self):
        self.init()
        env = {"ORCH_NO_INDEX": ""}
        active = os.path.join(self.root, "ACTIVE.md")
        self.assertFalse(os.path.exists(active))
        self.ok("set", "task_state=implementing", "-t", "myapp/add-login", env=env)
        self.assertTrue(os.path.exists(active))
        self.assertIn("myapp", self.read(active).decode())
        self.assertIn("add-login", self.read(active).decode())
        self.assertIn("implementing", self.read(active).decode())
        os.unlink(active)
        self.ok("log", "x", "-t", "myapp/add-login", env=env)
        self.ok("journal", "override", "what=x", "-t", "myapp/add-login", env=env)
        self.assertFalse(os.path.exists(active))
        self.ok("role", "planner", "done", "-t", "myapp/add-login", env=env)
        self.assertTrue(os.path.exists(active))
        os.unlink(active)
        self.ok("set", "task_state=implementing", "-t", "myapp/add-login", env=env)  # no change: no refresh
        self.assertFalse(os.path.exists(active))

    def test_init_refreshes_and_no_index_env_skips(self):
        self.init()
        self.assertFalse(os.path.exists(os.path.join(self.root, "ACTIVE.md")))
        self.ok("init", "myapp", "other", "--repo-path", "/home/u/p", "--base", "main", "--request", "x",
                env={"ORCH_NO_INDEX": ""})
        self.assertIn("other", self.read(self.root, "ACTIVE.md").decode())

    def test_index_failure_is_a_warning(self):
        fake = os.path.join(self.tmp, "fakebin")
        os.makedirs(fake)
        script = os.path.join(fake, "orch-index.sh")
        with open(script, "w") as fh:
            fh.write("#!/bin/sh\necho broken >&2\nexit 7\n")
        os.chmod(script, 0o755)
        warnings = []
        with mock.patch.object(paths, "BIN_DIR", fake), mock.patch.dict(os.environ, {"ORCH_NO_INDEX": ""}):
            paths.refresh_index(warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("rc 7", warnings[0])
        warnings = []
        with mock.patch.object(paths, "BIN_DIR", os.path.join(self.tmp, "nothing")), \
                mock.patch.dict(os.environ, {"ORCH_NO_INDEX": ""}):
            paths.refresh_index(warnings.append)
        self.assertEqual(len(warnings), 1)

    def test_show_task_is_at_most_40_lines(self):
        d = self.init()
        for i in range(12):
            self.ok("role", "implementer:p%d" % i, "pending", "-t", "myapp/add-login")
            self.ok("log", "line %d" % i, "-t", "myapp/add-login")
        out, _ = self.ok("show", "-t", "myapp/add-login")
        self.assertLessEqual(len(out.splitlines()), 40)
        self.assertIn("log (last 5):", out)
        self.assertIn("line 11", out)
        self.assertNotIn("line 6\n", out)
        self.assertIn("open questions: 0", out)
        self.assertIn("plan: none", out)
        with open(os.path.join(d, "plan.md"), "w") as fh:
            fh.write("# Plan\n")
        out, _ = self.ok("show", "-t", "myapp/add-login")
        self.assertIn("plan: %s" % os.path.join(d, "plan.md"), out)

    def test_show_counts_open_questions(self):
        d = self.init()
        with open(os.path.join(d, "questions.md"), "w") as fh:
            fh.write("# Questions\n\n## Q1 \u2014 first\n- status: open\n\n## Q2 \u2014 second\n- status: answered\n\n"
                     "## Q3 \u2014 third\n- status: open\n")
        out, _ = self.ok("show", "-t", "myapp/add-login")
        self.assertIn("open questions: 2", out)

    def test_show_is_read_only(self):
        d = self.init()
        before = tree_hash(self.root)
        self.ok("show", "-t", "myapp/add-login")
        self.ok("show")
        self.assertEqual(tree_hash(self.root), before)
        self.assertTrue(d)

    STATES = [
        ("t-planning", "- task_state: planning", True),
        ("t-impl", "- task_state: implementing", True),
        ("t-done", "- task_state: done", False),
        ("t-abandoned", "- task_state: abandoned", False),
        ("t-bold", "- task_state: **done**", False),
        ("t-upper", "- task_state: DONE", False),
        ("t-merged", "- task_state: m3_merged_stopped", None),
        ("t-custom", "- task_state: holding_at_gate", True),
        ("t-enumtext", "- task_state: planning | awaiting_approval | done", None),
        ("t-nostate-retro", "", False),
        ("t-nostate-orch-done", "", None),
        ("t-nostate-removed", "- worktree: removed after merge", False),
        ("t-nostate-none", "", None),
    ]

    def make_states(self):
        for slug, line, _ in self.STATES:
            d = os.path.join(self.root, "myapp", slug)
            os.makedirs(d)
            orch_row = "done" if slug == "t-nostate-orch-done" else "in_progress"
            with open(os.path.join(d, "status.md"), "w") as fh:
                fh.write("# Status: %s\n\n%s\n\n| role | state | notes |\n|---|---|---|\n| orchestrator | %s | |\n"
                         % (slug, line, orch_row))
            with open(os.path.join(d, "log.md"), "w") as fh:
                fh.write("# Log\n- 2026-03-01T10:00Z orchestrator: hello | pipe\n")
            if slug == "t-nostate-retro":
                open(os.path.join(d, "retro.md"), "w").close()

    def test_show_alone_matches_orch_index(self):
        self.make_states()
        res = subprocess.run([os.path.join(BIN, "orch-index.sh"), "--stdout"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True,
                             env=dict(os.environ, ORCH_DIR=self.root))
        self.assertEqual(res.returncode, 0, res.stderr)
        index_tasks = {}
        for line in res.stdout.splitlines():
            m = re.match(r"^\| (\S+) \| (\S+) \| (\S+) \|", line)
            if m and m.group(1) not in ("repo", "---", "_none_"):
                index_tasks[m.group(1) + "/" + m.group(2)] = m.group(3)
        out, _ = self.ok("show")
        mine = {}
        for line in out.splitlines():
            parts = line.split()
            mine[parts[0]] = parts[1]
        self.assertEqual(mine, index_tasks)
        self.assertNotIn("myapp/t-done", mine)
        for slug, _, expect in self.STATES:
            if expect is True:
                self.assertIn("myapp/" + slug, mine, slug)
            elif expect is False:
                self.assertNotIn("myapp/" + slug, mine, slug)


if __name__ == "__main__":
    unittest.main()
