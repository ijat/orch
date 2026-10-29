"""orch status --json (statusjson.py): neutral task trees built in a temp dir, plus the dashboard fixtures."""
import contextlib
import io
import json
import os
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

| role | state | timestamp (UTC) | last completed step | notes |
|---|---|---|---|---|
{rows}
"""


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
             mode="lite", shape="single", milestone="n/a", files=None, journal=None, repo_name="app"):
        d = os.path.join(self.root, repo_name, slug)
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write(STATUS.format(slug=slug, state=state, mode=mode, shape=shape, milestone=milestone,
                                   repo=self.repo, worktree=self.wt, rows=rows))
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
