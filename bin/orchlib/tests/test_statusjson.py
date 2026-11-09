"""orch status --json (statusjson.py): neutral task trees built in a temp dir, plus the dashboard fixtures."""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN)

from orchlib import cli, dashboard as db, statusjson as sj  # noqa: E402

NOW_ENV = "2026-03-01T13:00Z"
DASH = os.path.join(HERE, "fixtures", "dash", "orch")

STATUS = """# Status: {slug}

- task_state: {state}
- mode: {mode}
- shape: {shape}
- milestone: {milestone}
- repo: {repo}
- worktree: {worktree}

{head}
{rows}
"""
OLD_HEAD = "| role | state | timestamp (UTC) | last completed step | notes |\n|---|---|---|---|---|"
NEW_HEAD = "| role | state | timestamp (UTC) | last completed step | progress | notes |\n|---|---|---|---|---|---|"


def plan_text(ids, summary="- goal: add a login form\n"):
    out = ["# Plan: t", "", "## Summary", "", summary, "## Steps", ""]
    for sid in ids:
        out += ["### Step %s — title %s" % (sid, sid), "", "- size: S", "- do: change thing %s" % sid, ""]
    return "\n".join(out) + "\n"


def run_cli(root, *argv):
    env = {"ORCH_DIR": root, "ORCH_NO_INDEX": "1", "ORCH_NOW": NOW_ENV}
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        rc = cli.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def snapshot(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            info = os.lstat(os.path.join(d, f))
            out.append((os.path.relpath(os.path.join(d, f), root), info.st_size, info.st_mtime_ns, info.st_ino))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-status-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "orch")
        self.repo = os.path.join(self.tmp, "proj")
        self.wt = os.path.join(self.tmp, "wt")
        for d in (self.root, os.path.join(self.repo, "src"), self.wt, os.path.join(self.tmp, "proj2")):
            os.makedirs(d)

    def task(self, slug, rows="| implementer | in_progress | 2026-03-01T12:00Z | | |", state="implementing",
             mode="lite", shape="single", milestone="n/a", files=None, journal=None, repo_name="app",
             head=OLD_HEAD):
        d = os.path.join(self.root, repo_name, slug)
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write(STATUS.format(slug=slug, state=state, mode=mode, shape=shape, milestone=milestone,
                                   repo=self.repo, worktree=self.wt, rows=rows, head=head))
        for name, text in (files or {}).items():
            path = os.path.join(d, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)
        if journal is not None:
            with open(os.path.join(d, "journal.jsonl"), "w") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in journal))
        return d

    def data(self, cwd=None):
        argv = ["status", "--json"] + (["--cwd", cwd] if cwd else [])
        rc, out, err = run_cli(self.root, *argv)
        self.assertEqual((rc, err), (0, ""))
        return json.loads(out)

    def one(self, cwd=None):
        tasks = self.data(cwd)["tasks"]
        self.assertEqual(len(tasks), 1)
        return tasks[0]


class TestSteps(Base):
    def test_lite_task_exact_percent(self):                      # (a)
        self.task("add-login", rows="| implementer | in_progress | 2026-03-01T12:00Z | 2 | |",
                  files={"plan.md": plan_text(["1", "2", "3"])})
        t = self.one()
        s = t["steps"]
        self.assertEqual((s["total"], s["done"], s["percent"]), (3, 2, 67))
        self.assertEqual([i["done"] for i in s["items"]], [True, True, False])
        self.assertEqual(s["items"][2], {"id": "3", "title": "title 3", "done": False, "current": False,
                                         "summary": "change thing 3"})
        self.assertEqual(t["overall"], {"percent": 67, "estimate": False, "basis": "steps"})
        self.assertIs(t["program"], False)
        self.assertIsNone(t["milestones"])
        self.assertEqual(list(t), list(sj.KEYS))
        self.assertIsNone(t["error"])

    def test_lettered_ids_and_milestone_tokens(self):            # (b)
        self.task("a", rows="| implementer | in_progress | 2026-03-01T12:00Z | 2a | |",
                  files={"plan.md": plan_text(["1", "2", "2a", "3"])})
        self.assertEqual(self.one()["steps"]["done"], 3)
        shutil.rmtree(os.path.join(self.root, "app", "a"))
        self.task("b", rows="| implementer | in_progress | 2026-03-01T12:00Z | step 1 of M2 (2026-03-01T12:00Z) | |",
                  files={"plan.md": plan_text(["1", "2", "3", "4", "5"])})
        self.assertEqual(self.one()["steps"]["done"], 1)

    def test_no_match_is_zero_and_no_plan_is_null(self):
        self.task("a", rows="| implementer | in_progress | 2026-03-01T12:00Z | plan | |",
                  files={"plan.md": plan_text(["1", "2"])})
        self.assertEqual(self.one()["steps"]["done"], 0)
        shutil.rmtree(os.path.join(self.root, "app", "a"))
        self.task("b")
        s = self.one()["steps"]
        self.assertEqual((s["total"], s["done"], s["percent"], s["items"]), (None, None, None, []))
        self.assertEqual(self.one()["overall"]["percent"], None)

    def test_legacy_plan_without_step_blocks(self):
        self.task("a", files={"plan.md": "# Plan\n\n## Summary\n\nold style\n\n## Steps\n\n1. do it\n"})
        t = self.one()
        self.assertEqual((t["steps"]["total"], t["steps"]["items"]), (None, []))
        self.assertTrue(t["steps"]["plan"].endswith("plan.md"))
        self.assertEqual(t["summary"], "old style")


class TestProgressRealShapes(Base):
    """Fix round 1 (F1, F2): cell shapes and states seen in the real task tree; done never over- or under-counts."""

    def row(self, step, ts="2026-03-01T12:00Z", role="implementer", state="done"):
        return "| %s | %s | %s | %s | |" % (role, state, ts, step)

    def done_of(self, slug, step_rows, ids, state="implementing", journal=None, plan=None):
        self.task(slug, rows="\n".join(step_rows), state=state, files={"plan.md": plan or plan_text(ids)},
                  journal=journal)
        t = self.one()
        shutil.rmtree(os.path.join(self.root, "app", slug))
        return t["steps"]

    def test_fix_round_cells_mean_all_steps_done(self):
        ids12 = [str(i) for i in range(12)]   # a plan whose ids start at 0
        spawn11 = [{"kind": "spawn", "role": "implementer", "steps": [11]}]
        for slug, cell, ids, state, journal in (
                ("ext", "fix round 1", ids12, "awaiting_approval", spawn11),
                ("mocks", "fix round 1: F1-F6 fixed, gate green", ["1", "2", "3", "4", "5"], "awaiting_merge", None),
                ("launch", "fix round 3 (e51cf8f)", [str(i) for i in range(1, 8)], "reviewing", None),
                ("impl", "fix round 2", ["1", "2", "3"], "implementing", None),
                ("short", "fix-r1", ["1", "2", "3"], "implementing", None)):
            s = self.done_of(slug, [self.row(cell)], ids, state, journal)
            self.assertEqual((s["done"], s["total"]), (len(ids), len(ids)), slug)
            self.assertTrue(all(i["done"] for i in s["items"]), slug)

    def test_round_number_is_never_a_step_id(self):
        # an older fix-round row beside a newer part row (an earlier milestone's review): the round is no step
        s = self.done_of("old-round", [self.row("fix round 4", ts="2026-03-01T09:00Z"),
                                       self.row("2", ts="2026-03-01T12:00Z", role="implementer:2")],
                         ["1", "2", "3", "4", "5"])
        self.assertEqual(s["done"], 2)
        s = self.done_of("m-round", [self.row("fix-m3-r1 F3, F2", ts="2026-03-01T09:00Z"),
                                     self.row("1", ts="2026-03-01T12:00Z", role="implementer:1")], ["1", "2", "3"])
        self.assertEqual(s["done"], 1)

    def test_stale_fix_round_row_beside_newer_parts(self):
        # a long single plan with part rows: the main row says `fix round 3` (old), later part rows reach 18 of 23
        ids = [str(i) for i in range(1, 9)] + ["9a", "9b"] + [str(i) for i in range(10, 20)] + ["20a", "20b", "21"]
        rows = [self.row("fix round 3: 81ee848", ts="2026-03-01T06:25Z"),
                self.row("F1-F4, S1-S3", ts="2026-03-01T07:00Z", role="implementer:fix-m2-r1"),
                self.row("18", ts="2026-03-01T08:00Z", role="implementer:18"),
                self.row("F10/P2 (a9c9150), F9 (d791391)", ts="2026-03-01T09:00Z", role="implementer:fix-m3-r2")]
        s = self.done_of("social", rows, ids, "m3_merged_stopped")
        self.assertEqual(s["done"], 19)   # 1-8, 9a, 9b, 10-18
        self.assertEqual([i["id"] for i in s["items"] if not i["done"]], ["19", "20a", "20b", "21"])

    def test_review_states_mean_all_done(self):
        for state in ("reviewing", "awaiting_merge"):
            s = self.done_of("r-" + state, [self.row("3")], ["1", "2", "3", "4"], state)
            self.assertEqual(s["done"], 4, state)

    def test_awaiting_approval_only_after_the_last_step_was_given(self):
        ids = ["1", "2", "3"]
        last = [{"kind": "spawn", "role": "implementer", "steps": [3]}]
        self.assertEqual(self.done_of("after", [self.row("2")], ids, "awaiting_approval", last)["done"], 3)
        lite = [{"kind": "spawn", "role": "implementer", "steps": ["plan"]}]   # lite phase 1 at its plan gate
        self.assertEqual(self.done_of("lite", [self.row("plan")], ids, "awaiting_approval", lite)["done"], 0)
        early = [{"kind": "spawn", "role": "implementer", "steps": [1, 2]}]   # a plan revision mid-way
        self.assertEqual(self.done_of("mid", [self.row("2")], ids, "awaiting_approval", early)["done"], 2)

    def test_awaiting_approval_counts_this_milestone_only(self):
        files = {"roadmap.md": TestProgram.ROADMAP, "m02-two/plan.md": plan_text(["1", "2"])}
        other = [{"kind": "spawn", "role": "implementer", "steps": [2], "milestone": 1}]
        self.task("p", state="awaiting_approval", shape="roadmap", milestone="M2", files=files, journal=other,
                  rows=self.row("1"))
        self.assertEqual(self.one()["steps"]["done"], 1)

    def test_revision_steps_sort_by_number(self):
        # Revision blocks add 6a-6c and 7a after step 10; work stopped at 6c
        plan = plan_text([str(i) for i in range(1, 11)]) + "\n## Revision 1\n\n" + "".join(
            "### Step %s \u2014 r %s\n\n- do: x\n\n" % (sid, sid) for sid in ("6a", "6b", "6c", "7a"))
        s = self.done_of("rev", [self.row("4", role="implementer:4d"), self.row("6c", role="implementer:6c",
                                                                               state="blocked")],
                         None, "paused_usage_limit", plan=plan)
        self.assertEqual([i["id"] for i in s["items"]],
                         ["1", "2", "3", "4", "5", "6", "6a", "6b", "6c", "7", "7a", "8", "9", "10"])
        self.assertEqual((s["done"], s["total"]), (9, 14))
        self.assertEqual([i["id"] for i in s["items"] if i["done"]][-1], "6c")
        self.assertFalse(next(i for i in s["items"] if i["id"] == "7")["done"])


class TestTruthfulProgress(Base):
    """A running implementer's step is never done; a spawn's `m10-11` is plan step 11; a merge_point is a done milestone."""

    def spawn(self, steps, **kw):
        return dict({"kind": "spawn", "role": "implementer", "steps": steps}, **kw)

    def steps_of(self, slug, cell, ids, journal, **kw):
        self.task(slug, rows="| implementer | in_progress | 2026-03-01T12:00Z | %s | |" % cell,
                  files={"plan.md": plan_text(ids)}, journal=journal, **kw)
        s = self.one()["steps"]
        shutil.rmtree(os.path.join(self.root, "app", slug))
        return s

    def check_invariant(self, s):
        self.assertEqual(s["done"], sum(1 for i in s["items"] if i["done"]))
        for i in s["items"]:
            if i["current"]:
                self.assertFalse(i["done"])

    def test_high_water_with_step_running(self):
        ids = [str(i) for i in range(1, 16)]
        s = self.steps_of("hw", "15", ids, [self.spawn([11])])
        self.assertEqual((s["done"], s["total"], s["percent"]), (14, 15, 93))
        self.assertEqual([i["id"] for i in s["items"] if i["current"]], ["11"])
        self.assertEqual([i["id"] for i in s["items"] if not i["done"]], ["11"])
        self.assertEqual(s["current"], ["11"])
        self.check_invariant(s)

    def test_milestone_prefixed_spawn_step(self):
        ids = [str(i) for i in range(1, 16)]
        s = self.steps_of("pfx", "15", ids, [self.spawn(["m10-11"])])
        self.assertEqual((s["done"], s["current"]), (14, ["11"]))
        self.assertEqual([i["id"] for i in s["items"] if i["current"]], ["11"])
        self.check_invariant(s)
        # a program: M10's prefix matches in M10, not in M2
        files = {"roadmap.md": TestProgram.ROADMAP, "m02-two/plan.md": plan_text(["1", "2", "11"])}
        self.task("prog", state="implementing", shape="roadmap", milestone="M2", files=files,
                  rows="| implementer | in_progress | 2026-03-01T12:00Z | 2 | |",
                  journal=[self.spawn(["m2-11"], milestone=2)])
        s = self.one()["steps"]
        self.assertEqual((s["current"], s["done"]), (["11"], 2))
        shutil.rmtree(os.path.join(self.root, "app", "prog"))
        self.task("prog", state="implementing", shape="roadmap", milestone="M2", files=files,
                  rows="| implementer | in_progress | 2026-03-01T12:00Z | 11 | |",
                  journal=[self.spawn(["m10-11"])])   # row names 11, spawn's `m10-11` is another milestone's
        s = self.one()["steps"]
        self.assertEqual(s["current"], ["m10-11"])   # names another milestone: no match, stays as written
        self.assertFalse(any(i["current"] for i in s["items"]))
        self.assertEqual(s["done"], 3)

    def test_no_running_spawn_keeps_todays_result(self):
        ids = [str(i) for i in range(1, 16)]
        returned = [self.spawn([11]), {"kind": "return", "role": "implementer"}]
        for journal in (None, [], returned):
            s = self.steps_of("none", "15", ids, journal)
            self.assertEqual((s["done"], s["current"]), (15, None))
            self.assertFalse(any(i["current"] for i in s["items"]))

    def test_all_done_state_unchanged_unless_a_spawn_runs(self):
        ids = ["1", "2", "3", "4"]
        s = self.steps_of("rev", "3", ids, None, state="reviewing")
        self.assertEqual(s["done"], 4)
        s = self.steps_of("rev", "3", ids, [self.spawn([2])], state="reviewing")
        self.assertEqual((s["done"], s["current"]), (3, ["2"]))
        self.check_invariant(s)

    def test_fix_round_row_unless_a_step_is_running(self):
        ids = ["1", "2", "3"]
        self.assertEqual(self.steps_of("fx", "fix round 1", ids, None)["done"], 3)
        s = self.steps_of("fx", "fix round 1", ids, [self.spawn([3])])
        self.assertEqual((s["done"], s["current"]), (2, ["3"]))
        self.check_invariant(s)

    def test_multi_step_spawn_counts_its_completed_steps(self):   # Revision 3
        ids = [str(i) for i in range(1, 7)]
        s = self.steps_of("ms", "5", ids, [self.spawn([4, 5, 6])])    # row names 5, in the list: 4 and 5 done, 6 held
        self.assertEqual((s["done"], s["current"]), (5, ["6"]))
        self.assertEqual([i["id"] for i in s["items"] if i["current"]], ["6"])
        self.check_invariant(s)
        s = self.steps_of("ms", "3", ids, [self.spawn([4, 5, 6])])    # row names 3, not in the list: all three held
        self.assertEqual((s["done"], s["current"]), (3, ["4", "5", "6"]))
        s = self.steps_of("ms", "6", ids, [self.spawn([2, 3])])       # row beyond the list (rework): both held
        self.assertEqual((s["done"], s["current"]), (4, ["2", "3"]))
        self.assertEqual([i["id"] for i in s["items"] if not i["done"]], ["2", "3"])
        self.check_invariant(s)
        s = self.steps_of("ms", "3", ids, [self.spawn([1, 2, 3])])    # the whole list is done: nothing held
        self.assertEqual((s["done"], s["current"]), (3, []))
        self.check_invariant(s)

    def test_unreturned_spawn_beside_a_finished_row_holds_nothing(self):
        self.task("rest", rows="| implementer | done | 2026-03-01T12:00Z | 15 | |",
                  files={"plan.md": plan_text([str(i) for i in range(1, 16)])}, journal=[self.spawn([11])])
        s = self.one()["steps"]
        self.assertEqual((s["done"], s["current"]), (15, []))   # held nothing, and a done step is never also current
        self.assertFalse(any(i["current"] for i in s["items"]))
        self.check_invariant(s)

    def rows(self, *rows):
        return "\n".join("| %s | %s | 2026-03-01T12:00Z | %s | |" % r for r in rows)

    def test_parallel_implementer_is_held_whichever_spawn_is_newest(self):   # F1 probe (a)
        spawn = lambda role, steps: self.spawn(steps, role=role)               # noqa: E731
        self.task("par", rows=self.rows(("implementer-a", "in_progress", "1"), ("implementer-b", "done", "3")),
                  files={"plan.md": plan_text(["1", "2", "3", "4"])},
                  journal=[spawn("implementer-a", [2]), spawn("implementer-b", [3]),
                           {"kind": "return", "role": "implementer-b"}])
        s = self.one()["steps"]
        self.assertEqual((s["done"], s["current"]), (2, ["2"]))
        self.assertEqual([i["id"] for i in s["items"] if i["done"]], ["1", "3"])
        self.check_invariant(s)

    def test_two_running_implementers_both_hold(self):
        spawn = lambda role, steps: self.spawn(steps, role=role)               # noqa: E731
        self.task("par2", rows=self.rows(("implementer-a", "in_progress", "1"), ("implementer-b", "in_progress", "3")),
                  files={"plan.md": plan_text(["1", "2", "3", "4"])},
                  journal=[spawn("implementer-a", [2]), spawn("implementer-b", [4])])
        s = self.one()["steps"]
        self.assertEqual((s["done"], s["current"]), (2, ["2", "4"]))
        self.assertEqual([i["id"] for i in s["items"] if i["done"]], ["1", "3"])
        self.check_invariant(s)

    def test_a_later_spawn_of_another_role_does_not_release_the_step(self):   # F1 probe (b)
        ids = [str(i) for i in range(1, 6)]
        s = self.steps_of("graph", "5", ids, [self.spawn([3]), {"kind": "spawn", "role": "orch-graph"}])
        self.assertEqual((s["done"], s["total"], s["current"]), (4, 5, ["3"]))
        self.assertEqual([i["id"] for i in s["items"] if not i["done"]], ["3"])
        self.check_invariant(s)

    def test_dashboard_steps_done_agrees(self):                                # F3
        ids = [str(i) for i in range(1, 16)]
        self.task("dash", rows=self.rows(("implementer", "in_progress", "15")),
                  files={"plan.md": plan_text(ids)}, journal=[self.spawn([11])])
        self.task("dash-rest", rows=self.rows(("implementer", "done", "15")),
                  files={"plan.md": plan_text(ids)}, journal=[self.spawn([11])])
        self.task("dash-pf", rows=self.rows(("implementer-a", "in_progress", "15")),
                  files={"plan.md": plan_text(ids)},
                  journal=[self.spawn([11], role="implementer-a"), {"kind": "spawn", "role": "orch-graph"}])
        tasks = {t["slug"]: t for t in db.compute(self.root)["tasks"]}
        self.assertEqual((tasks["dash"]["steps_done"], tasks["dash"]["steps_total"]), (14, 15))
        self.assertEqual(tasks["dash"]["steps_done"], self.sj_done("dash"))
        self.assertEqual(tasks["dash-rest"]["steps_done"], 15)
        self.assertEqual(tasks["dash-pf"]["steps_done"], 14)

    def sj_done(self, slug):
        return next(t for t in self.data()["tasks"] if t["slug"] == slug)["steps"]["done"]

    def test_lite_plan_spawn_is_unchanged(self):
        s = self.steps_of("lite", "plan", ["1", "2"], [self.spawn(["plan"])])
        self.assertEqual((s["done"], s["current"]), (0, ["plan"]))

    def test_merge_point_milestone_is_done(self):
        files = {"roadmap.md": TestProgram.ROADMAP, "m02-two/plan.md": plan_text(["1", "2"])}
        self.task("mp", mode="standard", shape="roadmap", milestone="M2 (two)", files=files,
                  rows="| implementer | in_progress | 2026-03-01T12:00Z | 1 | |",
                  journal=[{"kind": "merge_point", "milestone": 1, "sha": "a" * 40}])
        t = self.one()
        self.assertEqual([i["state"] for i in t["milestones"]["items"]], ["done", "current", "pending"])
        self.assertEqual(t["milestones"]["done"], 1)

    def test_milestone_end_and_merge_point_count_once(self):
        files = {"roadmap.md": TestProgram.ROADMAP, "m02-two/plan.md": plan_text(["1", "2"])}
        self.task("mp2", mode="standard", shape="roadmap", milestone="M3", files=files,
                  rows="| implementer | in_progress | 2026-03-01T12:00Z | 1 | |",
                  journal=[{"kind": "merge_point", "milestone": 1}, {"kind": "milestone_end", "milestone": 1,
                                                                      "outcome": "done"},
                           {"kind": "milestone_end", "milestone": 2, "outcome": "done"}])
        ms = self.one()["milestones"]
        self.assertEqual((ms["done"], [i["state"] for i in ms["items"]]), (2, ["done", "done", "current"]))


class TestProgram(Base):
    ROADMAP = ("# Roadmap: p\n\n## Summary\n\n- goal: a program\n<!-- hidden -->\n\n## Milestones\n\n"
               "<!-- | M | not | the table | -->\n\n"
               "| M | goal | depends on | mode | tiers | sensitive | review | merge | you | size | confidence |\n"
               "|---|---|---|---|---|---|---|---|---|---|---|\n"
               "| 1 | first | - | lite | x | - | - | - | - | S | high |\n"
               "| 2 | second | 1 | standard | x | - | code | yes | - | L | med |\n"
               "| M3 | third | 2 | standard | x | - | - | - | eye-check | M | low |\n\n## Contracts\n\n| 9 | no |\n")

    def program(self, slug="p", extra=None, journal=None):
        files = {"roadmap.md": self.ROADMAP, "m02-two/plan.md": plan_text(["1", "2"])}
        files.update(extra or {})
        if journal is None:
            journal = [{"kind": "milestone_end", "milestone": 1, "outcome": "done"}]
        return self.task(slug, mode="standard", shape="roadmap", milestone="M2 (two)", files=files, journal=journal,
                         rows="| implementer | in_progress | 2026-03-01T12:00Z | 1 | |")

    def test_weighted_estimate(self):                            # (c)
        self.program()
        t = self.one()
        self.assertEqual((t["program"], t["milestone_no"]), (True, 2))
        self.assertEqual(t["overall"], {"percent": 43, "estimate": True, "basis": "milestone size (S=1 M=2 L=4)"})
        ms = t["milestones"]
        self.assertEqual((ms["done"], ms["total"]), (1, 3))
        self.assertEqual([i["state"] for i in ms["items"]], ["done", "current", "pending"])
        self.assertEqual([i["n"] for i in ms["items"]], [1, 2, 3])
        self.assertEqual(ms["items"][1]["summary"], "second\nreview: code · merge: yes · size: L")
        self.assertEqual(set(ms["items"][0]), {"n", "goal", "size", "mode", "depends", "review", "merge", "you",
                                               "confidence", "state", "summary"})
        self.assertTrue(t["steps"]["plan"].endswith(os.path.join("m02-two", "plan.md")))
        self.assertEqual((t["steps"]["done"], t["steps"]["total"]), (1, 2))
        self.assertEqual(t["summary"], "- goal: a program")

    def test_earlier_milestones_rows_do_not_count(self):
        start = {"ts": "2026-03-01T10:00Z", "kind": "milestone_start", "milestone": 2}
        files = {"roadmap.md": self.ROADMAP, "m02-two/plan.md": plan_text(["1", "2", "3"])}
        for slug, rows, journal, done in (
                ("part", "| implementer:m1a | done | 2026-03-01T09:00Z | 3 | |\n"
                         "| implementer | in_progress | 2026-03-01T12:00Z | 1 | |", [start], 1),
                ("fix", "| implementer | done | 2026-03-01T09:00Z | fix round 2 | |", [start], 0),
                ("same", "| implementer:a | done | 2026-03-01T10:00Z | 2 | |", [start], 2),
                ("nots", "| implementer:a | done | | 2 | |", [start], 2),
                ("nojournal", "| implementer:m1a | done | 2026-03-01T09:00Z | 3 | |", [], 3)):
            self.task(slug, mode="standard", shape="roadmap", milestone="M2 (two)", files=files, journal=journal,
                      rows=rows)
            self.assertEqual(self.one()["steps"]["done"], done, slug)
            shutil.rmtree(os.path.join(self.root, "app", slug))

    def test_dashboard_counts_the_same_rows(self):
        journal = db.Journal(json.dumps({"ts": "2026-03-01T10:00Z", "kind": "milestone_start", "milestone": 2}), "a/b")
        rows = [("implementer:m1a", {"ts": "2026-03-01T09:00Z", "step": "3"}),
                ("implementer", {"ts": "2026-03-01T12:00Z", "step": "1"})]
        self.assertEqual(db.steps_done_from(db.rows_of_milestone(rows, journal, 2), 3), 1)
        self.assertEqual(db.rows_of_milestone(rows, journal, 9), rows)

    def test_ambiguous_milestone_dir(self):                      # (d)
        self.program(extra={"m02-other/plan.md": plan_text(["1"])})
        t = self.one()
        self.assertEqual((t["steps"]["total"], t["steps"]["items"], t["steps"]["plan"]), (None, [], None))
        self.assertEqual(t["overall"]["percent"], 14)   # 1 of 7: the current milestone adds nothing
        self.assertIsNone(t["error"])

    def test_minimal_roadmap_table_and_unknown_size(self):
        self.task("p", shape="roadmap", milestone="M1", files={"roadmap.md": "| M | goal |\n|---|---|\n| 1 | a |\n| 2 | b |\n"})
        t = self.one()
        self.assertEqual([i["size"] for i in t["milestones"]["items"]], [None, None])
        self.assertEqual(t["overall"]["percent"], 0)
        self.assertEqual(t["milestones"]["items"][0]["summary"], "a")

    def test_program_without_table(self):
        self.task("p", shape="roadmap", milestone="M1", files={"roadmap.md": "# Roadmap\n\nnothing yet\n"})
        t = self.one()
        self.assertEqual(t["milestones"], {"done": 0, "total": None, "items": []})
        self.assertEqual(t["overall"]["percent"], None)
        self.assertTrue(t["overall"]["estimate"])


class TestQuestionsBlock(Base):
    """Step 3: the `questions` block, built from files `orch ask` and `orch answers` wrote."""

    SLUG = "app/qs"

    def setUp(self):
        Base.setUp(self)
        self.d = self.task("qs")

    def cli(self, *argv):
        rc, out, err = run_cli(self.root, *(list(argv) + ["-t", self.SLUG]))
        self.assertEqual(rc, 0, (argv, err))
        return out

    def block(self, group=None, skip=None):
        argv = ["status", "--json"] + (["--question-group", group] if group is not None else []) + \
               (["--question-skip", str(skip)] if skip is not None else [])
        rc, out, err = run_cli(self.root, *argv)
        self.assertEqual((rc, err), (0, ""))
        t = json.loads(out)["tasks"][0]
        self.assertIsNone(t["error"])
        return t["questions"], len(out.encode())

    def test_no_questions_file(self):
        q, _ = self.block()
        self.assertEqual(q, {"counts": {"open": 0, "answered": 0, "assumed": 0, "withdrawn": 0}, "groups": [],
                             "open_group": None, "open": [], "history": [], "history_total": 0})

    def seed(self):
        self.cli("ask", "Pick a colour", "--options", "red :: warm|blue :: cool", "--recommended", "blue",
                 "--reversible", "no", "--blocks", "2", "--group", "look", "--topic", "Colour")
        self.cli("ask", "Font?", "--group", "look", "--blocks", "all")
        self.cli("ask", "Which db?", "--options", "pg :: solid|sqlite !! only the owner can say", "--group", "data")
        self.cli("ask", "Loose one")
        self.cli("ask", "Take b", "--options", "a :: x|b :: y", "--reversible", "yes", "--assume", "b")
        self.cli("ask", "Dropped", "--group", "data")
        self.cli("answer", "6", "--withdraw")
        self.cli("answers", "2", "Inter")

    def test_counts_groups_open_group_and_history(self):
        self.seed()
        q, _ = self.block()
        self.assertEqual(q["counts"], {"open": 3, "answered": 1, "assumed": 1, "withdrawn": 1})
        self.assertEqual([(g["group"], g["label"], g["counts"]["open"]) for g in q["groups"]],
                         [("data", "data", 1), (None, "(ungrouped)", 1), ("look", "look", 1)])
        self.assertEqual(q["groups"][0]["counts"], {"open": 1, "answered": 0, "assumed": 0, "withdrawn": 1})
        self.assertEqual(q["groups"][2]["counts"], {"open": 1, "answered": 1, "assumed": 0, "withdrawn": 0})
        # Q1 blocks 2; Q2 is answered; Q3 and Q4 block all and Q3 comes first, so its group is the one listed in full
        self.assertEqual(q["open_group"], "data")
        self.assertEqual([e["id"] for e in q["open"]], ["Q3"])

    def test_open_group_is_that_of_the_first_open_question_by_sort_key(self):
        self.seed()
        q, _ = self.block()
        first = qs_sort_first(self.d)
        self.assertEqual(q["open_group"], first)

    def test_question_entry_fields_in_full(self):
        self.seed()
        q, _ = self.block("look")
        self.assertEqual(q["open_group"], "look")
        (e,) = q["open"]
        self.assertEqual((e["id"], e["n"], e["status"], e["text"], e["topic"], e["group"], e["reversible"], e["blocks"]),
                         ("Q1", 1, "open", "Pick a colour", "Colour", "look", "no", "2"))
        self.assertEqual((e["asked_by"], e["asked"], e["answer"], e["answered"], e["detail"]),
                         ("orchestrator", "2026-03-01T13:00Z", None, None, "full"))
        self.assertEqual(e["options"], [{"label": "red", "text": "warm", "kind": "explanation", "recommended": False},
                                        {"label": "blue", "text": "cool", "kind": "explanation", "recommended": True}])
        q, _ = self.block("data")
        self.assertEqual(q["open"][0]["options"][1],
                         {"label": "sqlite", "text": "only the owner can say", "kind": "reason", "recommended": False})
        q, _ = self.block("(ungrouped)")
        self.assertEqual([e["text"] for e in q["open"]], ["Loose one"])
        self.assertIsNone(q["open"][0]["group"])
        q, _ = self.block("nope")
        self.assertEqual((q["open_group"], q["open"]), ("nope", []))

    def test_history_is_answered_and_assumed_newest_first(self):
        self.seed()
        q, _ = self.block()
        self.assertEqual(q["history_total"], 2)
        self.assertEqual([(e["id"], e["status"], e["answer"], e["answered"]) for e in q["history"]],
                         [("Q5", "assumed", "b", "2026-03-01T13:00Z"), ("Q2", "answered", "Inter", "2026-03-01T13:00Z")])
        self.assertEqual(q["history"][0]["options"][1]["label"], "b")

    def test_history_keeps_the_newest_200(self):
        for i in range(210):
            self.cli("ask", "q%d" % i)
        for lo in range(1, 211, 70):
            args = []
            for n in range(lo, lo + 70):
                args += [str(n), "a%d" % n]
            self.cli("answers", *args)
        q, _ = self.block()
        self.assertEqual((q["counts"]["answered"], q["history_total"], len(q["history"])), (210, 210, 200))
        self.assertEqual(q["open"], [])

    def test_200_open_and_200_answered_stay_under_256_KiB(self):
        text = ("word " * 80).strip()
        opts = "|".join("opt%d :: %s" % (i, "explains " * 25) for i in range(5))
        for i in range(400):
            self.cli("ask", "%d %s" % (i, text), "--options", opts, "--topic", "t" * 70, "--group", "g")
        args = []
        for n in range(1, 201):
            args += [str(n), "answer " + "x" * 280]
        self.cli("answers", *args)
        q, size = self.block()
        self.assertLess(size, 256 * 1024)
        self.assertEqual(len(q["open"]), 200)                      # every open question of the group is listed
        self.assertEqual(len(q["history"]), 200)
        self.assertTrue(all(e["answer"] and e["topic"] for e in q["history"]))   # F8: every one of the 200
        self.assertEqual(q["open"][0]["detail"], "full")
        self.assertEqual(q["open"][-1]["detail"], "minimal")       # the budget ran out: later ones shrink
        self.assertEqual(q["open"][-1]["options"], [])
        self.assertEqual({e["detail"] for e in q["open"]}, {"full", "compact", "minimal"})
        self.assertEqual([e["n"] for e in q["open"]], list(range(201, 401)))
        self.assertTrue(all(e["text"] for e in q["open"]))

    def worst_case(self, ch):
        """200 open and 200 answered questions, every field past its limit, in characters of width `ch`. `orch ask` and
        `orch answers` write one of each; the file repeats those blocks under new numbers (400 runs take minutes)."""
        long = lambda n: ch * n   # noqa: E731
        opts = "|".join("%d%s :: %s" % (i, long(190), long(2500)) for i in range(6))
        for _ in range(2):
            self.cli("ask", long(2500), "--options", opts, "--topic", long(300), "--blocks", "x" * 90, "--group", long(200))
        self.cli("answers", "1", long(1500))
        path = os.path.join(self.d, "questions.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        head = re.compile(r"^## Q(\d+)\b", re.M)
        marks = [m.start() for m in head.finditer(text)]
        lead, answered, open_ = text[:marks[0]], text[marks[0]:marks[1]], text[marks[1]:]
        blocks = [head.sub("## Q%d" % n, (answered if n <= 200 else open_), count=1) for n in range(1, 401)]
        with open(path, "w", encoding="utf-8") as f:
            f.write(lead + "".join(b if b.endswith("\n") else b + "\n" for b in blocks))
        q, size = self.block()
        self.assertLess(size, 256 * 1024)
        self.assertEqual((len(q["open"]), len(q["history"])), (200, 200))
        for e in q["history"]:
            self.assertTrue(e["answer"] and e["topic"] and e["answered"], e["id"])   # shortened, never lost
        return size

    def test_worst_case_every_field_at_its_limit_stays_under_256_KiB(self):   # F15
        self.worst_case("x")

    def test_worst_case_in_characters_that_print_as_12_bytes_stays_under_256_KiB(self):   # F15
        self.worst_case("\U0001F600")

    def test_history_at_ordinary_sizes_keeps_the_usual_clips(self):   # F15
        for i in range(30):
            self.cli("ask", "q%d" % i, "--topic", "Topic %d" % i)
        args = []
        for n in range(1, 31):
            args += [str(n), "a" * 150]
        self.cli("answers", *args)
        q, _ = self.block()
        self.assertTrue(all(e["answer"] == "a" * 150 for e in q["history"]))

    def test_a_question_with_huge_options_does_not_overrun_the_full_budget(self):   # F15
        opts = "|".join("%d :: %s" % (i, "e" * 2000) for i in range(40))
        for i in range(3):
            self.cli("ask", "q%d" % i, "--options", opts, "--group", "g")
        q, _ = self.block()
        self.assertEqual(q["open"][0]["detail"], "full")          # the first of the window is always whole
        self.assertTrue(all(e["detail"] != "full" and e["options"] == [] for e in q["open"][1:]))

    def test_history_keeps_answer_and_topic_at_every_detail_level(self):   # F8
        text = ("word " * 80).strip()
        for i in range(120):
            self.cli("ask", "%d %s" % (i, text), "--options", "yes :: " + "explains " * 25 + "|no :: skip",
                     "--topic", "Topic %d" % i, "--group", "g")
        args = []
        for n in range(1, 121):
            args += [str(n), "yes" if n % 2 else "Answer %d with some typed words" % n]
        self.cli("answers", *args)
        q, size = self.block()
        self.assertLess(size, 256 * 1024)
        self.assertEqual(len(q["history"]), 120)
        details = {e["detail"] for e in q["history"]}
        self.assertTrue({"full", "history"} <= details, details)
        for e in q["history"]:
            self.assertTrue(e["answer"] and e["answered"] and e["topic"].startswith("Topic"), e["id"])
        self.assertTrue(all(e["options"] == [] for e in q["history"] if e["detail"] != "full"))
        self.assertEqual(q["history"][-1]["answer"], "yes")

    def test_a_full_entry_keeps_the_whole_label_and_explanation_and_auto_answer_takes_it(self):   # F7
        label = "L" * 100
        expl = "explains " * 80
        self.cli("ask", "Q " + "word " * 120, "--options", "%s :: %s|other :: short" % (label, expl),
                 "--recommended", label, "--reversible", "yes")
        q, _ = self.block()
        (e,) = q["open"]
        self.assertEqual([o["label"] for o in e["options"]], [label, "other"])
        self.assertEqual(e["options"][0]["text"], expl.strip())
        self.assertTrue(e["text"].startswith("Q word") and not e["text"].endswith("\u2026"))
        self.cli("answers", "--auto", "1", e["options"][0]["label"])
        self.assertEqual(self.block()[0]["history"][0]["answer"], label)

    def test_question_skip_moves_the_window_of_full_entries_and_stays_bounded(self):
        text = ("word " * 80).strip()
        opts = "|".join("opt%d :: %s" % (i, "explains " * 25) for i in range(5))
        for i in range(200):
            self.cli("ask", "%d %s" % (i, text), "--options", opts, "--topic", "t" * 70, "--group", "g")
        base, _ = self.block()
        self.assertEqual(base["open"][150]["detail"], "minimal")
        self.assertEqual(base["open"][150]["options"], [])
        for skip in (0, 100, 150, 199):
            q, size = self.block("g", skip)
            self.assertLess(size, 256 * 1024, skip)
            self.assertEqual([e["n"] for e in q["open"]], list(range(1, 201)))     # every question, same order
            self.assertTrue(all(e["detail"] == "minimal" and e["options"] == [] for e in q["open"][:skip]), skip)
            self.assertEqual(q["open"][skip]["detail"], "full", skip)
            self.assertEqual(len(q["open"][skip]["options"]), 5, skip)
        q, _ = self.block("g", 150)
        self.assertEqual(q["open"][150]["detail"], "full")                         # the one the base list drew without options
        self.assertEqual(q["open"][-1]["detail"], "minimal")
        q, _ = self.block("g", 0)
        self.assertEqual(q["open"], base["open"])                                  # 0 is today's list

    def test_question_skip_beyond_the_list_and_bad_values(self):
        self.cli("ask", "one", "--options", "a :: x|b :: y")
        q, _ = self.block(None, 5)
        self.assertEqual([e["detail"] for e in q["open"]], ["minimal"])
        rc, out, err = run_cli(self.root, "status", "--json", "--question-skip", "x")
        self.assertNotEqual(rc, 0)

    def test_200_open_in_a_normal_size_stay_full_and_small(self):
        for i in range(200):
            self.cli("ask", "Question number %d, short and plain" % i, "--options", "yes :: do it|no :: skip it")
        q, size = self.block()
        self.assertLess(size, 256 * 1024)
        self.assertEqual(len(q["open"]), 200)

    def test_a_group_name_cannot_be_the_reserved_one(self):
        rc, out, err = run_cli(self.root, "ask", "x", "--group", "(ungrouped)", "-t", self.SLUG)
        self.assertEqual(rc, 1)


def qs_sort_first(d):
    from orchlib import questions as qs_mod
    first = sorted(qs_mod.open_ones(d), key=qs_mod.sort_key)[0]
    return first.fields.get("group") or qs_mod.UNGROUPED


class TestRunning(Base):
    ROWS = ("| orchestrator | in_progress | 2026-03-01T12:00Z | | |\n"
            "| implementer | in_progress | 2026-03-01T12:00Z | 1 | step 2 |\n"
            "| director | waiting | 2026-03-01T12:00Z | | waiting: spec 1 |\n"
            "| code-reviewer | pending | | | |")

    def test_roles_and_spawn(self):                              # (e)
        spawn = {"kind": "spawn", "role": "implementer", "agent": "orch-implementer", "tier": "sonnet/high",
                 "steps": [2], "ts": "2026-03-01T12:00Z"}
        self.task("a", rows=self.ROWS, files={"plan.md": plan_text(["1", "2"])}, journal=[spawn])
        t = self.one()
        self.assertEqual([r["role"] for r in t["running"]["roles"]], ["implementer", "director"])
        self.assertEqual(t["running"]["last_spawn"], {"ts": "2026-03-01T12:00Z", "role": "implementer",
                                                      "agent": "orch-implementer", "tier": "sonnet/high",
                                                      "steps": ["2"], "milestone": None, "returned": False})
        self.assertEqual(t["steps"]["current"], ["2"])
        self.assertEqual([i["current"] for i in t["steps"]["items"]], [False, True])
        shutil.rmtree(os.path.join(self.root, "app", "a"))
        self.task("b", rows=self.ROWS, files={"plan.md": plan_text(["1", "2"])},
                  journal=[spawn, {"kind": "return", "role": "implementer", "outcome": "done"}])
        t = self.one()
        self.assertIs(t["running"]["last_spawn"]["returned"], True)
        self.assertIsNone(t["steps"]["current"])

    def test_each_running_role_has_its_own_spawn(self):
        rows = ("| implementer:a | in_progress | 2026-03-01T12:00Z | | |\n"
                "| implementer:b | in_progress | 2026-03-01T12:00Z | | |\n"
                "| implementer:c | in_progress | 2026-03-01T12:00Z | | |\n"
                "| planner | done | 2026-03-01T11:00Z | | |")

        def spawn(role, tier, step):
            return {"kind": "spawn", "role": role, "agent": "orch-implementer", "tier": tier, "steps": [step],
                    "ts": "2026-03-01T12:00Z"}
        self.task("a", rows=rows, files={"plan.md": plan_text(["1", "2", "3"])}, journal=[
            spawn("planner", "opus/high", 0), spawn("implementer:a", "haiku/low", 1),
            spawn("implementer:a", "opus/medium", 1), spawn("implementer:b", "sonnet/high", 2),
            {"kind": "return", "role": "implementer:a", "outcome": "done"}])
        running = self.one()["running"]
        self.assertEqual([(s["role"], s["tier"], s["steps"], s["returned"]) for s in running["spawns"]],
                         [("implementer:a", "opus/medium", ["1"], True),
                          ("implementer:b", "sonnet/high", ["2"], False)])   # not planner (done), not c (no spawn)
        self.assertEqual(running["last_spawn"]["role"], "implementer:b")
        self.assertIs(running["last_spawn"]["returned"], False)

    def test_pct_through_status_json(self):
        rows = ("| orchestrator | in_progress | 2026-03-01T12:00Z | | | |\n"
                "| implementer | in_progress | 2026-03-01T12:00Z | 1 | 40% | step 2 |\n"
                "| director | waiting | 2026-03-01T12:00Z | | | waiting |")
        self.task("a", rows=rows, files={"plan.md": plan_text(["1", "2"])}, head=NEW_HEAD)
        t = self.one()
        self.assertEqual([(r["role"], r["pct"]) for r in t["running"]["roles"]],
                         [("implementer", 40), ("director", None)])
        self.assertEqual(t["running"]["roles"][0]["note"], "step 2")

    def test_pct_missing_column_is_null(self):
        self.task("a", rows=self.ROWS, files={"plan.md": plan_text(["1", "2"])})
        self.assertEqual([r["pct"] for r in self.one()["running"]["roles"]], [None, None])

    def test_junk_journal_and_no_role_table(self):
        d = self.task("a", files={"plan.md": plan_text(["1"])})
        with open(os.path.join(d, "journal.jsonl"), "w") as fh:
            fh.write('not json\n[1]\n{"kind":"spawn","role":7,"steps":"x"}\n{"kind":"task_start","request":5}\n')
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write("# Status\n\n- task_state: implementing\n- repo: unknown\n")
        t = self.one(cwd=self.repo)
        self.assertEqual(t["running"]["roles"], [])
        self.assertEqual(t["running"]["last_spawn"]["steps"], None)
        self.assertEqual(t["steps"]["done"], 0)
        self.assertEqual((t["repo_path"], t["in_cwd"]), (None, False))
        self.assertEqual(t["summary"], "- goal: add a login form")   # a non-string request is no fallback
        self.assertIsNone(t["error"])


class TestScopeAndSummary(Base):
    def test_in_cwd(self):                                       # (f)
        self.task("a")
        self.assertIsNone(self.one()["in_cwd"])
        self.assertIs(self.one(cwd=self.repo)["in_cwd"], True)
        self.assertIs(self.one(cwd=os.path.join(self.repo, "src"))["in_cwd"], True)
        self.assertIs(self.one(cwd=self.wt)["in_cwd"], True)
        self.assertIs(self.one(cwd=os.path.join(self.tmp, "proj2"))["in_cwd"], False)
        self.assertEqual(self.data(cwd=self.repo)["cwd"], self.repo)
        self.assertEqual(self.one()["repo_path"], self.repo)

    def test_summary_fallbacks(self):                            # (g)
        long = "- goal: " + "word " * 300 + "\n<!-- a comment -->\n"
        self.task("a", files={"plan.md": plan_text(["1"], summary=long)},
                  journal=[{"kind": "task_start", "request": "the request"}])
        s = self.one()["summary"]
        self.assertLessEqual(len(s), sj.SUMMARY_MAX)
        self.assertTrue(s.endswith("…"))
        self.assertNotIn("comment", s)
        shutil.rmtree(os.path.join(self.root, "app", "a"))
        self.task("b", journal=[{"kind": "task_start", "request": "the request"}])
        self.assertEqual(self.one()["summary"], "the request")
        shutil.rmtree(os.path.join(self.root, "app", "b"))
        self.task("c")
        self.assertIsNone(self.one()["summary"])

    def test_finished_tasks_are_left_out(self):
        self.task("a", state="done")
        self.task("b", state="abandoned")
        self.assertEqual(self.data()["tasks"], [])


class TestCommand(Base):
    def test_dashboard_fixtures_same_set_and_order(self):        # (h)
        root = os.path.join(self.tmp, "dash")
        shutil.copytree(DASH, root)
        rc, out, err = run_cli(root, "status", "--json", "--cwd", self.tmp)
        self.assertEqual((rc, err), (0, ""))
        data = json.loads(out)
        self.assertEqual((data["schema"], data["generated"]), (1, NOW_ENV))
        want = [t["id"] for t in db.compute(root)["tasks"]]
        self.assertEqual([t["id"] for t in data["tasks"]], want)
        self.assertTrue(want)
        for t in data["tasks"]:
            self.assertEqual(list(t), list(sj.KEYS))
            self.assertIsNone(t["error"], t["id"])

    def test_read_only(self):                                    # (i)
        root = os.path.join(self.tmp, "dash")
        shutil.copytree(DASH, root)
        before = snapshot(root)
        rc, _, _ = run_cli(root, "status", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(snapshot(root), before)
        self.assertFalse([p for p, *_ in before if p.endswith(".orch.lock")])

    def test_usage_errors_and_listing(self):                     # (j)
        rc, out, err = run_cli(self.root, "status")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("--json", err)
        rc, out, err = run_cli(self.root, "-t", "app/x", "status", "--json")
        self.assertEqual((rc, out), (1, ""))
        rc, out, _ = run_cli(self.root, "commands")
        self.assertIn("status", out.split())


if __name__ == "__main__":
    unittest.main()
