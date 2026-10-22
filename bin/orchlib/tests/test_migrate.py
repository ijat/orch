"""orch migrate: status.md upgrades, backup, idempotence, dry run, mtimes. Neutral fixtures only."""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import test_core as core  # noqa: E402
from orchlib import migrate as mg, status as st  # noqa: E402

OLD = os.path.join(core.FIX, "old")
PLANS = os.path.join(core.FIX, "plans")
BIN = core.BIN
FIELDS = ["task_state", "mode", "shape", "milestone", "reviewed_through", "merged_through", "task_dir", "repo",
          "base_branch", "task_branch", "worktree", "planner_tier", "implementer_tier", "review_round", "usage_override"]


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


def non_status_hash(root):
    """Hash of every file except status.md files, lock files and migrate backups."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".migrate-backup"))
        for f in sorted(files):
            if f in ("status.md", ".orch.lock"):
                continue
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, root).encode())
            h.update(read(p))
    return h.hexdigest()


def field_names(path):
    return [m.group(1) for m in re.finditer(r"^- ([A-Za-z_]+):", read(path, "r"), re.M)]


class MigrateBase(core.Base):
    def make(self, repo, slug, status, journal=None, plan=None, log=None, retro=False, roadmap=False, age_days=None,
             extra=None):
        """status: fixture name under fixtures/old, or literal text/bytes."""
        d = self.tdir(repo, slug)
        os.makedirs(d)
        if isinstance(status, bytes):
            data = status
        elif status.endswith(".md") and "\n" not in status:
            data = read(os.path.join(OLD, status))
        else:
            data = status.encode()
        with open(os.path.join(d, "status.md"), "wb") as fh:
            fh.write(data)
        if journal:
            data = read(os.path.join(OLD, journal)) if journal.endswith(".jsonl") else journal.encode()
            with open(os.path.join(d, "journal.jsonl"), "wb") as fh:
                fh.write(data)
        if plan:
            with open(os.path.join(d, "plan.md"), "wb") as fh:
                fh.write(read(os.path.join(PLANS, plan)))
        if log:
            with open(os.path.join(d, "log.md"), "w") as fh:
                fh.write("# Log: %s\n\n%s\n" % (slug, log))
        if retro:
            with open(os.path.join(d, "retro.md"), "w") as fh:
                fh.write("# Retro\n")
        if roadmap:
            with open(os.path.join(d, "roadmap.md"), "w") as fh:
                fh.write("# Roadmap\n")
        for name, text in (extra or {}).items():
            os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        if age_days is not None:
            t = time.time() - age_days * 86400
            for n in os.listdir(d):
                os.utime(os.path.join(d, n), (t, t))
        return d

    def status_path(self, d):
        return os.path.join(d, "status.md")

    def migrate(self, *args, **kw):
        out, err = self.ok("migrate", *args, **kw)
        return out

    def backup_dirs(self):
        return sorted(n for n in os.listdir(self.root) if n.startswith(".migrate-backup"))


class TestTaskState(MigrateBase):
    def norm(self, raw):
        return mg.normalise_state(raw, st.task_state_enum())

    def test_enum_values_under_case_markup_and_punctuation(self):
        for raw, want in (("**done**", "done"), ("DONE", "done"), ("Done.", "done"), ("`awaiting_merge`", "awaiting_merge"),
                          ("_done_", "done"), ("  Implementing;  ", "implementing"), ("(abandoned)", "abandoned"),
                          ("PAUSED_USAGE_LIMIT", "paused_usage_limit"), ("reviewing", "reviewing")):
            self.assertEqual(self.norm(raw), want, raw)

    def test_everything_else_is_not_an_enum_value(self):
        for raw in ("**done** — merged to main", "DONE / MERGED", "m3_merged_stopped (M3 merged)", "paused-usage-limit",
                    "planning | awaiting_approval | implementing", "", "do ne", "merged", "unknown", "done?"):
            self.assertIsNone(self.norm(raw), raw)

    def test_value_is_rewritten_and_the_rest_of_the_file_is_kept(self):
        for raw, want in (("**done**", "done"), ("DONE", "done"), ("Done.", "done")):
            d = self.make("myapp", "t-%s" % want + str(len(raw)), "# Status: x\n\n- task_state: %s\n- repo: /home/u/Projects/myapp\n"
                          "\n| role | state | timestamp (UTC) | notes |\n|---|---|---|---|\n| orchestrator | done | | |\n" % raw)
            before = read(self.status_path(d), "r").splitlines()
            self.migrate("--all", "-t", d)
            after = read(self.status_path(d), "r").splitlines()
            self.assertEqual(after[2], "- task_state: %s" % want)
            self.assertEqual(after[0], before[0])
            self.assertIn("| orchestrator | done | | |", after)

    def test_prose_after_the_value_is_unchanged_and_reported_unknown(self):
        d = self.make("myapp", "add-login", "prose_state.md")
        out = self.migrate("-t", d)
        self.assertIn("- task_state: **done** — merged to main at `abc1234`, worktree removed", read(self.status_path(d), "r"))
        self.assertRegex(out, r"unknown: .*task_state '\*\*done\*\* — merged to main at `abc1234`, worktree removed' is not a single "
                              r"enum value \(index derives done\)")
        self.assertIn("task_state not a single enum value: 1", out)

    def test_placeholder_line_and_empty_value_are_unchanged(self):
        enum = " | ".join(st.task_state_enum())
        d1 = self.make("myapp", "t1", "# Status: t1\n\n- task_state: %s\n- repo: /home/u/Projects/myapp\n" % enum)
        d2 = self.make("myapp", "t2", "# Status: t2\n\n- task_state:\n- repo: /home/u/Projects/myapp\n")
        out = self.migrate("--all")
        for d in (d1, d2):
            self.assertEqual(read(self.status_path(d), "r").splitlines()[2], "- task_state:" + (" " + enum if d == d1 else ""))
        self.assertIn("task_state not a single enum value: 2", out)


class TestAddedFields(MigrateBase):
    def fields(self, d):
        s = st.Status.load(self.status_path(d))
        return [(n, v.strip()) for _, n, v in s.fields()]

    def test_missing_fields_in_template_order_after_the_last_field(self):
        d = self.make("myapp", "fix-menu", "legacy_done.md", journal='{"ts":"2026-01-01T10:00Z","kind":"task_start","mode":"standard"}\n')
        self.migrate("--all")
        got = self.fields(d)
        self.assertEqual([n for n, _ in got][:4], ["task_state", "repo", "base_branch", "worktree"])
        self.assertEqual([n for n, _ in got][4:], ["mode", "shape", "milestone", "reviewed_through", "merged_through", "task_dir",
                                                 "task_branch", "planner_tier", "implementer_tier", "review_round", "usage_override"])
        self.assertEqual(dict(got), {
            "task_state": "done", "repo": "/home/u/Projects/myapp", "base_branch": "main", "worktree": "removed after merge",
            "mode": "standard", "shape": "single", "milestone": "n/a", "reviewed_through": "none", "merged_through": "none",
            "task_dir": "~/.claude/orchestrations/myapp/fix-menu", "task_branch": "unknown", "planner_tier": "unknown", "implementer_tier": "unknown",
            "review_round": "unknown", "usage_override": "none"})
        # the new lines sit directly under the last old field, the table is untouched
        text = read(self.status_path(d), "r")
        self.assertRegex(text, r"- worktree: removed after merge\n- mode: standard\n")
        self.assertIn("\n\n| role | state | updated | notes |\n|---|---|---|---|\n| orchestrator | done |", text)

    def test_task_dir_uses_the_home_form(self):
        d = self.make("myapp", "fix-menu", "legacy_done.md")
        self.migrate("--all")
        self.assertEqual(dict(self.fields(d))["task_dir"], "~/.claude/orchestrations/myapp/fix-menu")

    def test_template_order_is_read_from_the_template(self):
        self.assertEqual(list(mg.template_fields()), FIELDS)

    def test_mode_comes_from_task_start_in_either_key_style(self):
        d1 = self.make("myapp", "t1", "legacy_done.md", journal="journal_events.jsonl")
        d2 = self.make("myapp", "t2", "legacy_done.md", journal='{"kind":"task_start","mode":"standard"}\n')
        d3 = self.make("myapp", "t3", "legacy_done.md", journal='{"kind":"task_start","mode":"weird"}\n')
        d4 = self.make("myapp", "t4", "legacy_done.md")
        d5 = self.make("myapp", "t5", "legacy_done.md", journal='{"kind":"spawn","mode":"lite"}\n')
        self.migrate("--all")
        self.assertEqual([dict(self.fields(d))["mode"] for d in (d1, d2, d3, d4, d5)],
                         ["lite", "standard", "unknown", "unknown", "unknown"])

    def test_shape_milestone_and_gates_follow_the_roadmap_file(self):
        d1 = self.make("myapp", "single", "legacy_done.md")
        d2 = self.make("myapp", "prog", "legacy_done.md", roadmap=True)
        self.migrate("--all")
        f1, f2 = dict(self.fields(d1)), dict(self.fields(d2))
        self.assertEqual((f1["shape"], f1["milestone"], f1["reviewed_through"], f1["merged_through"]), ("single", "n/a", "none", "none"))
        self.assertEqual((f2["shape"], f2["milestone"], f2["reviewed_through"], f2["merged_through"]),
                         ("roadmap", "unknown", "unknown", "unknown"))

    def test_an_existing_shape_decides_and_a_strange_one_is_not_guessed_at(self):
        body = "# Status: x\n\n- task_state: done\n- shape: %s\n\n| role | state |\n|---|---|\n| orchestrator | done |\n"
        d1 = self.make("myapp", "t1", body % "single — one plan", roadmap=True)
        d2 = self.make("myapp", "t2", body % "roadmap")
        d3 = self.make("myapp", "t3", body % "tbd (planner decides)")
        self.migrate("--all")
        got = [dict(self.fields(d)) for d in (d1, d2, d3)]
        self.assertEqual([(g["shape"][:6], g["milestone"], g["reviewed_through"]) for g in got],
                         [("single", "n/a", "none"), ("roadma", "unknown", "unknown"), ("tbd (p", "unknown", "unknown")])

    def test_never_adds_task_state_or_worktree(self):
        d = self.make("myapp", "add-login", "no_state.md", retro=True)
        out = self.migrate("--all")
        names = field_names(self.status_path(d))
        self.assertNotIn("task_state", names)
        self.assertNotIn("worktree", names)
        self.assertIn("task_state: absent (index derives done)", out)
        self.assertIn("worktree: absent", out)
        self.assertEqual(names, ["repo", "branch", "mode", "shape", "milestone", "reviewed_through", "merged_through", "task_dir",
                                 "base_branch", "task_branch", "planner_tier", "implementer_tier", "review_round", "usage_override"])

    def test_existing_fields_are_never_overwritten(self):
        d = self.make("myapp", "prose", "prose_state.md")
        before = read(self.status_path(d), "r").splitlines()[:6]
        self.migrate("-t", d)
        after = read(self.status_path(d), "r").splitlines()
        self.assertEqual(after[:6], before[:6])
        self.assertEqual(sum(1 for l in after if l.startswith("- mode:")), 1)

    def test_a_field_after_the_table_counts_as_present(self):
        d = self.make("myapp", "t1", "# Status: x\n\n- task_state: done\n\n| role | state |\n|---|---|\n| orchestrator | done |\n\n"
                      "- usage_override: none\n")
        self.migrate("--all")
        text = read(self.status_path(d), "r")
        self.assertEqual(text.count("- usage_override:"), 1)

    def test_no_role_table_milestone_table_gets_fields_before_the_table(self):
        d = self.make("myapp", "big-change", "program_table.md", roadmap=True)
        out = self.migrate("-t", d)
        lines = read(self.status_path(d), "r").splitlines()
        first_table = next(i for i, l in enumerate(lines) if l.startswith("|"))
        added = [i for i, l in enumerate(lines) if re.match(r"- (mode|shape|milestone|usage_override):", l)]
        self.assertTrue(added and max(added) < first_table)
        self.assertEqual(lines[first_table:first_table + 2], ["| milestone | dir | state | timestamp (UTC) | notes |", "|---|---|---|---|---|"])
        self.assertEqual(lines[-1], "- note: this bullet is free text after the table, not a field block")
        self.assertRegex(out, r"note: .*roles: unreadable layout")
        self.assertIn("- shape: roadmap", lines)
        self.assertNotIn("- worktree: unknown", lines)

    def test_unreadable_role_layout_and_ragged_rows_are_reported_not_fixed(self):
        d = self.make("myapp", "t1", "# Status: x\n\n- task_state: planning\n\n| role | state | notes |\n|---|---|---|\n"
                      "| planner | done | a | b |\n| orchestrator | done | ok |\n")
        out = self.migrate("-t", d)
        self.assertIn("1 row(s) with a cell count different from the header", out)
        self.assertIn("| planner | done | a | b |\n", read(self.status_path(d), "r"))

    def test_unknown_values_are_listed_by_name_and_counted(self):
        d = self.make("myapp", "t1", "legacy_done.md")
        out = self.migrate("--all")
        self.assertRegex(out, r"unknown: mode; task_branch; planner_tier; implementer_tier; review_round")
        self.assertRegex(out, r"unknown values written: 5;")


class TestSafety(MigrateBase):
    def tasks(self):
        self.make("myapp", "fix-menu", "legacy_done.md", journal="journal_events.jsonl", plan="legacy.md", log="- 2026-01-02T03:04Z orchestrator: done", retro=True,
                  extra={"notes.md": "n\n", "artifacts/a.txt": "a\n", "briefs/b.md": "b\n"})
        self.make("myapp", "add-login", "no_state.md", plan="standard.md", log="- 2026-01-02T03:04Z orchestrator: x", retro=True)
        self.make("myapp", "big-change", "program_table.md", roadmap=True, extra={"m01-core/plan.md": "# Plan\n"})

    def test_only_status_md_files_change(self):
        self.tasks()
        before = non_status_hash(self.root)
        out = self.migrate("--all")
        self.assertIn("did change", out)
        self.assertEqual(non_status_hash(self.root), before)

    def test_dry_run_writes_nothing_and_reports_what_a_run_would_do(self):
        self.tasks()
        before = core.tree_hash(self.root)
        dry = self.migrate("--dry-run", "--all")
        self.assertEqual(core.tree_hash(self.root), before)
        self.assertEqual(self.backup_dirs(), [])
        self.assertNotIn(".orch.lock", "".join(f for _, _, fs in os.walk(self.root) for f in fs))
        real = self.migrate("--all")

        def body(text):
            return [l for l in text.splitlines()[1:] if not l.startswith(("total:", "backup:"))]
        self.assertEqual(body(dry), body(real))
        self.assertIn("(dry run, nothing written)", dry)
        self.assertRegex(dry, r"3 would change")
        self.assertRegex(real, r"3 did change")

    def test_dry_run_needs_no_orch_dir_but_a_real_run_does(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1"}
        run = lambda *a: subprocess.run([sys.executable, core.LAUNCHER] + list(a), env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, universal_newlines=True)
        r = run("migrate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("development copy: set ORCH_DIR", r.stderr)
        r = run("migrate", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_backup_holds_the_original_bytes_and_nothing_is_made_when_nothing_changes(self):
        self.tasks()
        orig = read(self.status_path(self.tdir("myapp", "add-login")))
        self.migrate("--dry-run", "--all")
        self.assertEqual(self.backup_dirs(), [])
        self.migrate("--all")
        dirs = self.backup_dirs()
        self.assertEqual(dirs, [".migrate-backup-2026-03-01T1000Z"])
        b = os.path.join(self.root, dirs[0], "myapp", "add-login", "status.md")
        self.assertEqual(read(b), orig)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, dirs[0], "myapp"))), ["add-login", "big-change", "fix-menu"])
        # a second run neither changes nor creates a backup
        self.migrate("--all", now="2026-03-02T11:00Z")
        self.assertEqual(self.backup_dirs(), dirs)

    def test_backup_dir_option(self):
        self.tasks()
        bd = os.path.join(self.tmp, "my backups")
        out = self.migrate("--all", "--backup-dir", bd)
        self.assertTrue(os.path.exists(os.path.join(bd, "myapp", "fix-menu", "status.md")))
        self.assertEqual(self.backup_dirs(), [])
        self.assertIn("backup: %s" % bd, out)

    def test_backup_comes_before_the_write_and_an_existing_backup_is_never_overwritten(self):
        d1 = self.make("myapp", "aa-first", "legacy_done.md")
        d2 = self.make("myapp", "bb-second", "legacy_done.md")
        bd = os.path.join(self.tmp, "bk")
        os.makedirs(os.path.join(bd, "myapp", "aa-first"))
        with open(os.path.join(bd, "myapp", "aa-first", "status.md"), "w") as fh:
            fh.write("precious\n")
        before1, before2 = read(self.status_path(d1)), read(self.status_path(d2))
        rc, out, err = self.run_orch("migrate", "--all", "--backup-dir", bd)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("aa-first  [done, finished]  ERROR: backup", out)
        self.assertEqual(read(self.status_path(d1)), before1)
        self.assertEqual(read(os.path.join(bd, "myapp", "aa-first", "status.md")), b"precious\n")
        self.assertNotEqual(read(self.status_path(d2)), before2)
        self.assertEqual(read(os.path.join(bd, "myapp", "bb-second", "status.md")), before2)

    def test_second_run_reports_no_changes_and_writes_nothing(self):
        self.tasks()
        self.migrate("--all")
        before = core.tree_hash(self.root)
        out = self.migrate("--all", now="2026-04-01T10:00Z")
        self.assertEqual(core.tree_hash(self.root), before)
        self.assertEqual(self.backup_dirs(), [".migrate-backup-2026-03-01T1000Z"])
        self.assertRegex(out, r"0 did change")
        self.assertEqual(len(re.findall(r"  no changes$", out, re.M)), 3)
        self.assertTrue(out.rstrip().endswith("no changes"))

    def test_mtime_is_restored_and_the_index_row_does_not_move(self):
        d = self.make("myapp", "add-login", "no_state.md", log="- 2026-01-02T03:04Z orchestrator: x", age_days=30)
        old = os.stat(self.status_path(d)).st_mtime_ns
        self.migrate("--all")
        self.assertEqual(os.stat(self.status_path(d)).st_mtime_ns, old)

    def test_permissions_and_odd_bytes_survive(self):
        crlf = b"# Status: x\r\n\r\n- task_state: done\r\n- repo: /home/u/Projects/myapp\r\n\r\n| role | state |\r\n|---|---|\r\n| orchestrator | done |\r\n"
        nonl = b"# Status: y\n\n- task_state: done\n- repo: /home/u/Projects/myapp (caf\xe9 \xff)\n\n| role | state |\n|---|---|\n| orchestrator | done |"
        d1 = self.make("myapp", "crlf", crlf)
        d2 = self.make("myapp", "nonl", nonl)
        os.chmod(self.status_path(d1), 0o600)
        self.migrate("--all")
        new = read(self.status_path(d1))
        self.assertNotIn(b"\n", new.replace(b"\r\n", b""))
        self.assertIn(b"- usage_override: none\r\n", new)
        self.assertEqual(os.stat(self.status_path(d1)).st_mode & 0o777, 0o600)
        new2 = read(self.status_path(d2))
        self.assertIn(b"(caf\xe9 \xff)\n", new2)
        self.assertTrue(new2.endswith(b"| orchestrator | done |"))
        self.assertRegex(new2.decode("latin-1"), r"- usage_override: none\n")

    def test_the_task_lock_is_taken_for_a_write(self):
        d = self.make("myapp", "fix-menu", "legacy_done.md")
        env = dict(os.environ, ORCH_DIR=self.root, ORCH_NO_INDEX="1", HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        fd = os.open(os.path.join(d, ".orch.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            p = subprocess.Popen([sys.executable, core.LAUNCHER, "migrate", "--all"], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            time.sleep(1.0)
            self.assertIsNone(p.poll(), "migrate must wait for the task lock")
            self.assertNotIn(b"usage_override", read(self.status_path(d)))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn(b"usage_override", read(self.status_path(d)))

    def test_a_task_changed_after_the_report_pass_is_written_from_the_fresh_bytes(self):
        d = self.make("myapp", "fix-menu", "legacy_done.md")
        seen = {}
        real_analyse = mg.analyse_file

        def analyse_then_edit(task_dir, order, enum):
            res = real_analyse(task_dir, order, enum)
            if not seen:
                seen["x"] = 1
                with open(self.status_path(d), "a") as fh:     # another session edits between the two passes
                    fh.write("\nEdited meanwhile.\n")
            return res
        mg.analyse_file = analyse_then_edit
        try:
            self.migrate("--all")
        finally:
            mg.analyse_file = real_analyse
        text = read(self.status_path(d), "r")
        self.assertIn("Edited meanwhile.", text)
        self.assertIn("- usage_override: none", text)
        backup = read(os.path.join(self.root, self.backup_dirs()[0], "myapp", "fix-menu", "status.md"), "r")
        self.assertIn("Edited meanwhile.", backup)


    def test_a_task_fixed_by_someone_else_after_the_report_pass_is_left_alone(self):
        d = self.make("myapp", "fix-menu", "legacy_done.md")
        fixed = read(os.path.join(OLD, "legacy_done.md"), "r").replace("- task_state: DONE", "- task_state: done").replace(
            "- worktree: removed after merge\n", "- worktree: removed after merge\n" + "".join(
                "- %s: x\n" % n for n in ("mode", "shape", "milestone", "reviewed_through", "merged_through", "task_dir",
                                          "task_branch", "planner_tier", "implementer_tier", "review_round", "usage_override")))
        seen = {}
        real_analyse = mg.analyse_file

        def analyse_then_fix(task_dir, order, enum):
            res = real_analyse(task_dir, order, enum)
            if not seen:
                seen["x"] = 1
                with open(self.status_path(d), "w") as fh:
                    fh.write(fixed)
            return res
        mg.analyse_file = analyse_then_fix
        try:
            out = self.migrate("--all")
        finally:
            mg.analyse_file = real_analyse
        self.assertEqual(read(self.status_path(d), "r"), fixed)
        self.assertEqual(self.backup_dirs(), [])
        self.assertIn("0 did change", out)


class TestScopeAndReport(MigrateBase):
    def setUp(self):
        MigrateBase.setUp(self)
        self.active = self.make("myapp", "act", "# Status: act\n\n- task_state: implementing\n- repo: /home/u/Projects/myapp\n\n| role | state |\n|---|---|\n| orchestrator | in_progress |\n")
        self.done = self.make("myapp", "fin", "legacy_done.md")
        self.legacy = self.make("myapp", "leg", "no_state.md", retro=True)

    def test_default_scope_is_active_tasks(self):
        before_done = read(self.status_path(self.done))
        out = self.migrate()
        self.assertIn("myapp/act  [implementing]", out)
        self.assertNotIn("myapp/fin", out)
        self.assertNotIn("myapp/leg", out)
        self.assertIn("2 finished skipped (use --all)", out)
        self.assertEqual(read(self.status_path(self.done)), before_done)
        self.assertIn("usage_override", read(self.status_path(self.active), "r"))

    def test_all_adds_finished_tasks(self):
        out = self.migrate("--all")
        for name in ("myapp/act", "myapp/fin", "myapp/leg"):
            self.assertIn(name + "  [", out)
        self.assertIn("usage_override", read(self.status_path(self.done), "r"))

    def test_t_selects_one_task_whatever_its_state(self):
        out = self.migrate("-t", "myapp/fin")
        self.assertIn("myapp/fin  [done, finished]", out)
        self.assertNotIn("myapp/act", out)
        self.assertNotIn("usage_override", read(self.status_path(self.active), "r"))
        out = self.migrate("-t", self.legacy)
        self.assertIn("myapp/leg", out)

    def test_t_without_status_md_is_a_usage_error(self):
        os.makedirs(self.tdir("myapp", "empty"))
        self.refuse(1, "migrate", "-t", "myapp/empty")

    def test_report_has_plan_and_journal_stats_per_task(self):
        d = self.make("myapp", "stats", "legacy_done.md", plan="standard.md", journal="journal_events.jsonl")
        plan_steps = len(__import__("orchlib.plan", fromlist=["x"]).parse_file(os.path.join(PLANS, "standard.md")).steps)
        out = self.migrate("--all")
        block = out[out.index("myapp/stats"):]
        block = block[:block.index("\nmyapp/", 1)] if "\nmyapp/" in block[1:] else block
        self.assertRegex(block, r"plans: plan\.md: %d steps \(files: unknown for \d+, depends: unknown for \d+\)" % plan_steps)
        self.assertIn("journal: 4 lines, 1 invalid, 2 event-keyed", block)
        leg = self.migrate("--all", "-t", self.legacy)
        self.assertIn("plans: none | journal: 0 lines, 0 invalid, 0 event-keyed, task_start mode unknown", leg)

    def test_legacy_plan_is_reported_as_legacy(self):
        self.make("myapp", "oldplan", "legacy_done.md", plan="legacy.md")
        self.assertIn("plans: plan.md: legacy (no step blocks)", self.migrate("--all"))

    def test_program_milestone_plans_are_reported_and_never_written(self):
        d = self.make("myapp", "big-change", "program_table.md", roadmap=True,
                      extra={"m01-core/plan.md": read(os.path.join(PLANS, "standard.md"), "r"), "m02-ui/plan.md": "# Plan\nlegacy text\n"})
        before = non_status_hash(self.root)
        out = self.migrate("-t", d)
        self.assertRegex(out, r"plans: roadmap\.md present; m01-core/plan\.md: \d+ steps .*; m02-ui/plan\.md: legacy \(no step blocks\)")
        self.assertEqual(non_status_hash(self.root), before)
        self.assertEqual(sorted(os.listdir(os.path.join(d, "m01-core"))), ["plan.md"])

    def test_totals_line(self):
        out = self.migrate("--dry-run", "--all")
        self.assertRegex(out, r"total: 3 task\(s\) scanned, 3 reported; 3 would change; unknown values that would be written: \d+; "
                              r"task_state not a single enum value: 0")
        self.assertRegex(out, r"fields: .*usage_override 3")

    def test_unusable_status_file_is_reported_per_task_not_fatal(self):
        os.makedirs(os.path.join(self.root, "myapp", "dir-status"))
        os.makedirs(os.path.join(self.root, "myapp", "dir-status", "status.md"))
        out = self.migrate("--all")
        self.assertIn("myapp/dir-status", out)
        self.assertIn("ERROR", out)
        self.assertIn("myapp/fin  [done, finished]", out)


class TestIndexStaysTheSame(MigrateBase):
    def index_rows(self):
        env = dict(os.environ, ORCH_DIR=self.root)
        r = subprocess.run([os.path.join(BIN, "orch-index.sh"), "--stdout"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout[r.stdout.index("## Active"):]

    def test_orch_index_rows_are_identical_after_migrate(self):
        self.make("myapp", "fresh", "# Status: fresh\n\n- task_state: implementing\n- worktree: /home/u/wt/fresh (main)\n\n| role | state |\n|---|---|\n| orchestrator | in_progress |\n",
                  log="- 2026-01-02T03:04Z orchestrator: building")
        self.make("myapp", "old-unknown", "# Status: u\n\n- repo: /home/u/Projects/myapp\n\n| role | state |\n|---|---|\n| orchestrator | in_progress |\n",
                  age_days=30, log="- 2026-01-02T03:04Z orchestrator: old")                                            # needs triage
        self.make("myapp", "old-done", "no_state.md", retro=True, age_days=60)                                           # finished
        self.make("myapp", "removed-wt", "# Status: r\n\n- repo: /home/u/Projects/myapp\n- worktree: removed after merge\n\n| role | state |\n|---|---|\n| orchestrator | in_progress |\n", age_days=12)
        self.make("myapp", "prose", "prose_state.md", age_days=3)
        self.make("myapp", "recent-unknown", "# Status: u\n\n- repo: /home/u/Projects/myapp\n\n| role | state |\n|---|---|\n| orchestrator | in_progress |\n", age_days=5)
        before = self.index_rows()
        self.assertIn("Needs triage", before)
        self.migrate("--all")
        self.assertEqual(self.index_rows(), before)
        for slug in ("old-unknown", "fresh", "old-done"):
            self.assertIn("usage_override", read(self.status_path(self.tdir("myapp", slug)), "r"))

    def test_resume_prints_the_same_paths_before_and_after(self):
        """`unknown` written by migrate must read like an absent field (value rule), not `unknown (missing)`."""
        d = self.make("myapp", "add-login", "# Status: u\n\n- task_state: implementing\n\n| role | state |\n|---|---|\n| orchestrator | in_progress |\n",
                      log="- 2026-01-02T03:04Z orchestrator: x")
        pick = lambda text: [l for l in text.splitlines() if l.split(":")[0] in ("repo", "base", "worktree", "branch", "plan")]
        before = pick(self.ok("resume", d)[0])
        self.migrate("-t", d)
        self.assertIn("- repo: unknown", read(self.status_path(d), "r"))
        self.assertEqual(pick(self.ok("resume", d)[0]), before)
        self.assertEqual(st.clean_value("repo", "unknown"), ("unknown", False))
        self.assertEqual(st.show_value("worktree", "unknown"), "unknown")

    def test_role_commands_still_work_on_a_migrated_old_file(self):
        d = self.make("myapp", "add-login", "no_state.md", log="- 2026-01-02T03:04Z orchestrator: x")
        self.migrate("--all")
        self.ok("role", "orchestrator", "in_progress", "-t", d, "--note", "probe", "--log", "probe")
        self.ok("resume", d)


if __name__ == "__main__":
    unittest.main()
