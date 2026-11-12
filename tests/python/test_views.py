"""orch plan and orch diff: read-only views of a plan and of the task branch. Real git repos in a temp dir, neutral values."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

import test_core as core  # noqa: E402
from test_merge import GitBase  # noqa: E402
from orchlib import plan as pl  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "plans")
S = "bd0dbcca-0000-4000-8000-000000000001"

SAMPLE_A = """## Decisions

- **D1 Shape: single plan** — one release. Status: decided
- **D2 Task inference** — only the subagent verbs; `-t` wins.
  Rejected: inference in `resolve_task`. Status: decided
- **D20 Other** — not D2. Status: decided
"""

SAMPLE_B = """## Decisions

### D1 — Shape
Single plan. Status: decided

### D2 — Task inference
Only the subagent verbs infer the task.
- Rejected: branch-name parsing.
- Status: decided

## Approach
D2 keeps it small (a mention, not an entry).

## Revision 1 — 2026-11-12
- trigger: review

### Decisions amended
- **D2 (amended) Task inference** — ORCH_TASK must agree. Status: decided
- **D5 (amended) Relative paths** — file lists only. Status: decided

### Step 2 — amended (supersedes T7)
- contract: x
"""


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read().replace("@@SHA@@", "0123456789abcdef0123456789abcdef01234567")


class PlanBase(core.Base):
    def task_with_plan(self, text, **kw):
        d = self.init(**kw)
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        return d

    def plan_cmd(self, *argv, **kw):
        return self.run_orch("plan", "-t", "myapp/add-login", *argv, **kw)


class TestPlanOutline(PlanBase):
    def test_outline_lists_every_section_and_step(self):
        text = fixture("standard.md")
        self.task_with_plan(text)
        rc, out, err = self.plan_cmd()
        self.assertEqual(rc, 0, err)
        p = pl.Plan(text)
        for sec in p.sections:
            self.assertIn("  %s  (" % p.lines[sec.start].rstrip(), out)
        for step in p.steps + [s for _, s in p.rev_steps]:
            self.assertIn("  %s  (" % p.lines[step.start].rstrip(), out)
        self.assertNotIn("Step 99", out)          # a heading inside a fence is not a step

    def test_outline_line_numbers_are_one_based(self):
        text = fixture("standard.md")
        self.task_with_plan(text)
        _, out, _ = self.plan_cmd()
        first = out.splitlines()[0]
        self.assertTrue(first.startswith("L11  ## Summary  ("), first)   # the Summary heading is line 11


class TestPlanBlocks(PlanBase):
    def test_step_2_is_the_binding_text(self):
        text = fixture("standard.md")
        self.task_with_plan(text)
        rc, out, err = self.plan_cmd("--step", "2")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, pl.Plan(text).step("2").text() + "\n")

    def test_superseding_revision_comes_first(self):
        text = "# Plan: add-login\n\n## Steps\n\n### Step 2 — Session store\n\n- size: M\n- do: old text.\n\n" \
               "## Revision 1 — 2026-03-02\n\n- step 2 is restated here: the old do is void.\n"
        self.task_with_plan(text)
        rc, out, err = self.plan_cmd("--step", "2")
        self.assertEqual(rc, 0, err)
        head, sep, rest = out.partition("\n\n")
        self.assertEqual(head, "From Revision 1 — 2026-03-02: this revision wins.")
        self.assertIn("- step 2 is restated here", rest)
        self.assertTrue(out.rstrip("\n").endswith(pl.Plan(text).step("2").text()), out)

    def test_section_by_name(self):
        text = fixture("standard.md")
        self.task_with_plan(text)
        rc, out, err = self.plan_cmd("--section", "decisions")
        self.assertEqual(rc, 0, err)
        p = pl.Plan(text)
        self.assertEqual(out, p.block(p.find("decisions").start, p.find("decisions").end) + "\n")

    def test_decision_d2_from_bullets(self):
        self.task_with_plan(fixture("standard.md"))
        rc, out, err = self.plan_cmd("--decision", "D2")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("## Decisions\n- **D2 Cookies** — signed, 1 hour. Status: decided"), out)
        self.assertNotIn("D1 Hashing", out)

    def test_decision_sample_a_has_no_d20(self):
        self.task_with_plan(SAMPLE_A)
        rc, out, err = self.plan_cmd("--decision", "D2")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "## Decisions\n- **D2 Task inference** — only the subagent verbs; `-t` wins.\n"
                              "  Rejected: inference in `resolve_task`. Status: decided\n")

    def test_decision_sample_b_heading_entries_and_revision_subsection(self):
        self.task_with_plan(SAMPLE_B)
        rc, out, err = self.plan_cmd("--decision", "D2")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "## Decisions\n### D2 — Task inference\nOnly the subagent verbs infer the task.\n"
                              "- Rejected: branch-name parsing.\n- Status: decided\n\n"
                              "## Revision 1 — 2026-11-12 / ### Decisions amended\n"
                              "- **D2 (amended) Task inference** — ORCH_TASK must agree. Status: decided\n")

    def test_unknown_decision_lists_the_ids(self):
        self.task_with_plan(SAMPLE_B)
        rc, out, err = self.plan_cmd("--decision", "D9")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no decision D9 in this plan (decisions: D1, D2, D5)", err)

    def test_unknown_step_and_section_exit_1_with_the_names(self):
        text = fixture("standard.md")
        self.task_with_plan(text)
        p = pl.Plan(text)
        rc, out, err = self.plan_cmd("--step", "9")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("plan steps: " + ", ".join(p.ids()), err)
        rc, out, err = self.plan_cmd("--section", "nosuch")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("sections: ", err)
        self.assertIn("Decisions", err)

    def test_options_are_mutually_exclusive(self):
        self.task_with_plan(fixture("standard.md"))
        rc, out, err = self.plan_cmd("--step", "2", "--section", "goal")
        self.assertEqual((rc, out), (1, ""))

    def test_no_t_with_a_driver_session_match_works(self):
        self.task_with_plan(fixture("standard.md"), env={"CLAUDE_CODE_SESSION_ID": S})
        rc, out, err = self.run_orch("plan", "--step", "1", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("### Step 1 — Auth module"), out)
        rc, out, err = self.run_orch("plan", "--step", "1", env={"CLAUDE_CODE_SESSION_ID": ""})
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("pass -t", err)

    def test_plan_writes_nothing(self):
        d = self.task_with_plan(fixture("standard.md"))
        before = core.tree_hash(self.root)
        for argv in ((), ("--step", "2"), ("--section", "decisions"), ("--decision", "D2")):
            self.assertEqual(self.plan_cmd(*argv)[0], 0)
        self.assertEqual(core.tree_hash(self.root), before)
        self.assertTrue(os.path.exists(os.path.join(d, "plan.md")))


class TestDiff(GitBase):
    """A repo with main, the task branch orch/add-login (feature.txt from setup_task, then a.py and b.py), and a plan
    whose steps name a.py (step 1), b.py (step 2), /etc/hosts (step 3) and an untouched c.py (step 4)."""

    def setUp(self):
        GitBase.setUp(self)
        self.setup_task()
        self.commit(self.wt, "a.py", "a\n")
        self.commit(self.wt, "b.py", "b\n")
        text = (
            "# Plan: add-login\n\n## Steps\n\n"
            "### Step 1 — A\n\n- size: S\n- files: {r}/a.py\n- do: a.\n- contract: a.\n- done when: a.\n\n"
            "### Step 2 — B\n\n- size: S\n- files: {r}/b.py\n- do: b.\n- contract: b.\n- done when: b.\n\n"
            "### Step 3 — Hosts\n\n- size: S\n- files: /etc/hosts\n- do: h.\n- contract: h.\n- done when: h.\n\n"
            "### Step 4 — Untouched\n\n- size: S\n- files: {r}/c.py\n- do: c.\n- contract: c.\n- done when: c.\n"
        ).replace("{r}", self.repo)
        with open(os.path.join(self.d, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        self.range = "main...orch/add-login"

    def diff(self, *argv, **kw):
        return self.run_orch("diff", "-t", "myapp/add-login", *argv, **kw)

    def test_default_equals_git_diff_of_base_and_branch(self):
        rc, out, err = self.diff()
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, self.git(self.repo, "diff", "--no-ext-diff", self.range) + "\n")

    def test_stat_and_name_only(self):
        rc, out, err = self.diff("--stat")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), self.git(self.repo, "diff", "--no-ext-diff", "--stat=200", self.range))
        rc, out, err = self.diff("--name-only")
        self.assertEqual((rc, out.split()), (0, ["a.py", "b.py", "feature.txt"]), err)
        rc, out, err = self.diff("--stat", "--name-only")
        self.assertEqual((rc, out), (1, ""))

    def test_step_limits_the_files(self):
        rc, out, err = self.diff("--step", "1", "--name-only")
        self.assertEqual((rc, out), (0, "a.py\n"), err)
        rc, out, err = self.diff("--step", "1", "--step", "2", "--name-only")
        self.assertEqual((rc, out.split()), (0, ["a.py", "b.py"]), err)

    def test_range_is_checked_like_brief_review(self):
        rc, out, err = self.diff("--range", "bad..ref", "--name-only")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("--range", err)
        rc, out, err = self.diff("--range", "nodots", "--name-only")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("--range must look like A..B or A...B", err)
        rc, out, err = self.diff("--range", self.range, "--name-only")
        self.assertEqual((rc, out.split()), (0, ["a.py", "b.py", "feature.txt"]), err)

    def test_step_outside_the_repo_is_refused_and_named(self):
        rc, out, err = self.diff("--step", "3")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("/etc/hosts", err)
        self.assertIn("names no file in the repo", err)

    def test_empty_diff_says_so(self):
        rc, out, err = self.diff("--step", "4")
        self.assertEqual((rc, out), (0, "(empty diff: %s)\n" % self.range), err)

    def test_pathspec_after_double_dash(self):
        rc, out, err = self.diff("--name-only", "--", "b.py")
        self.assertEqual((rc, out), (0, "b.py\n"), err)

    def test_git_failure_is_exit_1_with_its_first_line(self):
        self.hooks.append({"pred": lambda argv: "diff" in argv,
                           "action": lambda argv: (128, "", "fatal: bad object\nmore\n"), "fired": False})
        rc, out, err = self.diff("--name-only")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("git diff failed: fatal: bad object", err)

    def test_inferred_from_the_cwd_inside_the_worktree(self):
        old = os.getcwd()
        os.chdir(self.wt)
        try:
            rc, out, err = self.run_orch("diff", "--name-only")
        finally:
            os.chdir(old)
        self.assertEqual((rc, out.split()), (0, ["a.py", "b.py", "feature.txt"]), err)


if __name__ == "__main__":
    unittest.main()
