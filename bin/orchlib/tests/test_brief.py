"""orch brief: implement, director, review, security, fix. Real git repos in a temp dir, synthetic plans,
neutral values only. The review-brief header is checked against the reader `orch journal` really uses."""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import test_core as core  # noqa: E402
from test_merge import GitBase  # noqa: E402
from orchlib import merge, plan as pl  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "plans")
T = "myapp/add-login"
SHEADS = ("Where you work", "Checkpoint commands", "Summary", "Decisions", "Assumed decisions", "Your steps",
          "Steps yours depend on", "Revisions that touch your steps", "Findings about your files",
          "Risks about your steps", "Verification for your steps", "Context files", "Lessons", "Prior work",
          "From the orchestrator", "If this brief is not enough")


def rd(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def wr(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def heads(text, level="##"):
    return [l[len(level) + 1:] for l in text.split("\n") if l.startswith(level + " ")]


BRIEF_HEADS = ("Where you work|Checkpoint commands|Summary|Decisions|Assumed decisions|Your steps|Steps yours depend on|"
               "Revisions|Findings|Risks|Verification|Context files|Lessons|Prior work|From the orchestrator|"
               "If this brief is not enough|What you judge|Steps \\(files|Steps whose files|Files and areas touched|"
               "Diff stat")


def sec(text, title):
    """Body of a `## title` section of a brief; it ends at the next heading the brief itself writes."""
    m = re.search(r"^## %s[^\n]*\n\n(.*?)(?=^## (?:%s)|\Z)" % (re.escape(title), BRIEF_HEADS), text, re.M | re.S)
    assert m, "no section %r in:\n%s" % (title, text[:400])
    return m.group(1).rstrip("\n")


class BriefBase(GitBase):
    def setUp(self):
        GitBase.setUp(self)
        self.setup_task()
        self.base_sha = self.git(self.repo, "rev-parse", "main")
        self.plan_text = self.fixture_plan("standard.md")
        wr(os.path.join(self.d, "plan.md"), self.plan_text)
        wr(os.path.join(self.d, "sketches", "step-1.md"), "# sketch\n")
        self.plan_sketch = self.plan_text.replace("- sketch: none", "- sketch: sketches/step-1.md")

    def fixture_plan(self, name):
        text = rd(os.path.join(FIX, name))
        return text.replace("@@SHA@@", self.base_sha).replace("/home/u/Projects/myapp", self.repo)

    def brief(self, *args, **kw):
        out, err = self.cmd_ok("brief", *args)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2, out)
        self.assertTrue(re.match(r"^brief \d+\.\d KB vs plan \d+\.\d KB$", lines[1]), lines[1])
        return lines[0], rd(lines[0])

    def refused(self, code, *args):
        rc, out, err = self.cmd("brief", *args)
        self.assertEqual(rc, code, "out=%s err=%s" % (out, err))
        return err

    def put_plan(self, text):
        wr(os.path.join(self.d, "plan.md"), text)


class TestImplementBrief(BriefBase):
    def test_sections_in_order_and_header(self):
        path, text = self.brief("implement", "1")
        self.assertEqual(os.path.basename(path), "implement-s1.md")
        self.assertEqual(os.path.dirname(path), os.path.join(self.d, "briefs"))
        self.assertEqual(text.split("\n")[0], "# Brief: implement step(s) 1 — myapp/add-login")
        self.assertEqual(heads(text), list(SHEADS))
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))

    def test_your_steps_and_decisions_are_byte_identical(self):
        p = pl.parse_plan(self.plan_text)
        _, text = self.brief("implement", "1")
        self.assertEqual(sec(text, "Your steps"), p.step("1").text())
        self.assertEqual(sec(text, "Decisions"), p.body(p.find("decisions")))
        self.assertEqual(sec(text, "Summary"), p.body(p.find("summary")))

    def test_fenced_block_survives_inside_a_step(self):
        p = pl.parse_plan(self.plan_text)
        _, text = self.brief("implement", "2")
        self.assertIn(p.step("2").text(), sec(text, "Your steps"))
        self.assertIn("  ### Step 99 — not a step (inside a fence)", text)

    def test_names_never_overwrite(self):
        a, _ = self.brief("implement", "1")
        b, _ = self.brief("implement", "1")
        c, _ = self.brief("implement", "1")
        self.assertEqual([os.path.basename(x) for x in (a, b, c)],
                         ["implement-s1.md", "implement-s1-2.md", "implement-s1-3.md"])
        d, _ = self.brief("implement", "2,3")
        e, _ = self.brief("implement", "1-3")
        self.assertEqual(os.path.basename(d), "implement-s2-3.md")
        self.assertEqual(os.path.basename(e), "implement-s1-2-3.md")

    def test_same_inputs_give_identical_bytes(self):
        a, ta = self.brief("implement", "2,3", )
        b, tb = self.brief("implement", "2,3")
        self.assertNotEqual(a, b)
        self.assertEqual(ta, tb)
        with open(a, "rb") as fa, open(b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read())

    def test_unknown_and_bad_step_ids(self):
        self.assertIn("unknown step(s) 7", self.refused(1, "implement", "7"))
        self.assertIn("bad step id", self.refused(1, "implement", "x"))
        self.assertIn("bad step range", self.refused(1, "implement", "10-1"))
        self.assertIn("needs the step id", self.refused(1, "implement"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "briefs")))

    def test_step_ids_3a_and_10(self):
        _, text = self.brief("implement", "3a")
        self.assertIn("### Step 3a: Views follow-up", sec(text, "Your steps"))
        _, text = self.brief("implement", "10")
        self.assertIn("### Step 10 — Release notes", sec(text, "Your steps"))
        self.assertNotIn("### Step 1 — Auth module", sec(text, "Your steps"))

    def test_where_you_work(self):
        self.put_plan(self.plan_sketch)
        _, text = self.brief("implement", "1", "--tier", "sonnet/high", "--escalation", "proactive")
        w = sec(text, "Where you work")
        self.assertIn("- worktree: %s  <- this is the worktree, not the main repo." % self.wt, w)
        self.assertIn("- main repo (do not edit): %s" % self.repo, w)
        self.assertIn("- branch: orch/add-login", w)
        self.assertIn("- base: main", w)
        self.assertIn("- plan: %s" % os.path.join(self.d, "plan.md"), w)
        self.assertIn("- notes: %s" % os.path.join(self.d, "notes.md"), w)
        self.assertIn("- sketch for step 1 (non-binding): %s" % os.path.join(self.d, "sketches", "step-1.md"), w)
        self.assertIn("- tier: sonnet/high", w)
        self.assertIn("- escalation: proactive", w)
        self.assertIn("main-repo paths; the same file in your worktree is under %s" % self.wt, w)
        self.assertNotIn("(missing)", w)

    def test_sketch_none_and_missing_sketch(self):
        _, text = self.brief("implement", "2")
        self.assertNotIn("sketch for", sec(text, "Where you work"))
        os.unlink(os.path.join(self.d, "sketches", "step-1.md"))
        _, text = self.brief("implement", "1")
        self.assertNotIn("sketch for", sec(text, "Where you work"))   # step 1 says `sketch: none`
        self.put_plan(self.plan_sketch)
        _, text = self.brief("implement", "1")
        self.assertIn("step-1.md (missing)", sec(text, "Where you work"))

    def test_no_worktree_variants(self):
        self.set_field(worktree="not yet created")
        _, text = self.brief("implement", "1")
        self.assertIn("none exists yet", sec(text, "Where you work"))
        self.assertNotIn("this is the worktree", text)
        self.set_field(worktree="none (user: no worktree)")
        _, text = self.brief("implement", "1")
        self.assertIn("no worktree (the user chose to work in the repo itself): %s" % self.repo, sec(text, "Where you work"))

    def test_checkpoint_commands_are_quoted(self):
        _, text = self.brief("implement", "1,2")
        c = sec(text, "Checkpoint commands")
        q = "'%s'" % self.d
        self.assertIn("~/.claude/bin/orch -t %s role 'implementer' in_progress --note 'working on step(s) 1, 2' --log 'started step(s) 1, 2'" % q, c)
        self.assertIn("role 'implementer' done --step '2' --note '<one line>' --log 'done: <one line>'", c)
        self.assertIn("role 'implementer' blocked --note 'need: <what>' --log 'blocked: <what>'", c)
        _, text = self.brief("implement", "1", "--role", "implementer:part-a")
        self.assertIn("role 'implementer:part-a' in_progress", sec(text, "Checkpoint commands"))
        self.refused(1, "implement", "1", "--role", "bad role; rm")

    def test_task_dir_with_spaces_is_quoted(self):
        self.ok("init", "My App", "add-menu", "--repo-path", self.repo, "--base", "main", "--request", "x")
        d = self.tdir("My App", "add-menu")
        self.ok("set", "worktree=%s" % self.wt, "-t", "My App/add-menu")
        wr(os.path.join(d, "plan.md"), self.plan_text)
        rc, out, err = self.run_orch("brief", "implement", "1", "-t", "My App/add-menu")
        self.assertEqual(rc, 0, err)
        text = rd(out.splitlines()[0])
        self.assertIn("-t '%s' role" % d, text)

    def test_depends_contract_and_done_when_only(self):
        _, text = self.brief("implement", "3")
        dep = sec(text, "Steps yours depend on")
        self.assertIn("### Step 1 — Auth module", dep)
        self.assertIn("- contract: `check(user, password) -> bool`; constant time.", dep)
        self.assertIn("- done when: `pytest tests/test_auth.py` passes.", dep)
        self.assertNotIn("- do:", dep)
        self.assertIn("### Step 2 — Session store", dep)
        self.assertIn("sessions expire after one hour.", dep)
        self.assertNotIn("Step 3a", dep)

    def test_depends_range_and_no_depends(self):
        _, text = self.brief("implement", "10")
        dep = sec(text, "Steps yours depend on")
        for sid in ("1", "2", "3"):
            self.assertIn("### Step %s — " % sid, dep)
        _, text = self.brief("implement", "1")
        self.assertEqual(sec(text, "Steps yours depend on"), "None.")
        _, text = self.brief("implement", "1-3")
        self.assertEqual(sec(text, "Steps yours depend on"), "None.")   # all inside the brief already

    def test_revision_that_restates_a_step_is_printed_first(self):
        _, text = self.brief("implement", "2")
        steps = sec(text, "Your steps")
        i_rev = steps.index("supersedes this step or part of it: this revision wins.")
        i_orig = steps.index("### Step 2 – Session store")
        self.assertLess(i_rev, i_orig)
        self.assertIn("### Step 2 — Session store (restated; replaces the original block)", steps)
        self.assertIn("sessions expire after one hour and are signed.", steps)
        self.assertNotIn("Step 2 — Session store (restated", sec(text, "Revisions that touch your steps"))

    def test_revision_amending_a_step_goes_to_revisions(self):
        _, text = self.brief("implement", "3")
        rev = sec(text, "Revisions that touch your steps")
        self.assertIn("### Revision 1 — 2026-03-02", rev)
        self.assertIn("#### Step 3 — changes (amends)", rev)
        self.assertIn("Add a test for a wrong password.", rev)
        self.assertNotIn("Unrelated notes", rev)
        self.assertNotIn("CI badge", rev)
        self.assertIn("- trigger: user feedback at the gate", rev)   # the revision's own preamble

    def test_a_bullet_revision_that_replaces_a_step_wins_in_your_steps(self):
        _, text = self.brief("implement", "3a")
        steps = sec(text, "Your steps")
        self.assertIn("From Revision 2 — 2026-03-03: this revision wins.", steps)
        self.assertIn("- Step 3a is replaced by a redirect in step 3.", steps)
        self.assertLess(steps.index("this revision wins"), steps.index("### Step 3a: Views follow-up"))
        self.assertNotIn("The README is rewritten", text)

    def test_revision_untouched_steps_get_no_revision_text(self):
        _, text = self.brief("implement", "10")
        self.assertEqual(sec(text, "Revisions that touch your steps"), "None.")

    def test_findings_and_risks_are_filtered(self):
        _, text = self.brief("implement", "1")
        f = sec(text, "Findings about your files")
        self.assertIn("`src/auth.py` hashes with sha256", f)
        self.assertNotIn("session cookie", f)
        self.assertNotIn("step 10", f)
        self.assertNotIn("CI config", f)
        self.assertEqual(f.count("### Verified"), 1)
        self.assertEqual(sec(text, "Risks about your steps"), "None.")
        _, text = self.brief("implement", "10")
        self.assertIn("The build in step 10 needs node 20.", sec(text, "Findings about your files"))
        self.assertNotIn("sha256", sec(text, "Findings about your files"))
        self.assertIn("Step 10 may need a version bump.", sec(text, "Risks about your steps"))
        _, text = self.brief("implement", "2")
        self.assertIn("(affects `src/session.py`)", sec(text, "Risks about your steps"))
        self.assertIn("The session cookie in `src/session.py`", sec(text, "Findings about your files"))
        self.assertNotIn("logo", sec(text, "Risks about your steps"))

    def test_findings_match_a_file_basename(self):
        _, text = self.brief("implement", "3")
        # views.py is a step 3 file; step 3 is also named in the assumptions bullet
        self.assertIn("Step 3 assumes", sec(text, "Findings about your files"))
        self.assertIn("### Assumptions (not verified)", sec(text, "Findings about your files"))

    def test_verification_lines_name_the_steps(self):
        _, text = self.brief("implement", "1")
        v = sec(text, "Verification for your steps")
        self.assertIn("Run from the repo root.", v)
        self.assertIn("- Step 1: `pytest tests/test_auth.py`", v)
        self.assertNotIn("Step 2:", v)
        self.assertNotIn("Final gate", v)
        _, text = self.brief("implement", "3a")
        v = sec(text, "Verification for your steps")
        self.assertIn("- Step 3a: `pytest tests/test_redirect.py`.", v)
        self.assertNotIn("Step 10:", v)
        self.assertNotIn("Step 3: `pytest tests/test_views.py`", v)

    def test_final_gate_comes_with_the_last_step(self):
        _, text = self.brief("implement", "10")
        v = sec(text, "Verification for your steps")
        self.assertIn("### Final gate (once, before review)", v)
        self.assertIn("`pytest -q` and `ruff check src`.", v)
        self.assertIn("- Step 10: `grep -c entry CHANGELOG.md`", v)

    def test_assumed_decisions_are_included(self):
        self.cmd_ok("ask", "Use a cookie or a header?", "--options", "cookie|header", "--assume", "cookie", "--reversible", "yes")
        self.cmd_ok("ask", "Open question?", "--options", "a|b")
        _, text = self.brief("implement", "1")
        a = sec(text, "Assumed decisions")
        self.assertIn("## Q1 — Use a cookie or a header?", a)
        self.assertIn("- status: assumed", a)
        self.assertIn("- answer: cookie", a)
        self.assertNotIn("Open question", a)

    def test_context_files_freshness(self):
        os.makedirs(os.path.join(self.wt, "docs"), exist_ok=True)
        self.commit(self.wt, "docs/design.md", "v1\n")
        self.commit(self.wt, "src_config.txt", "x\n")
        # planned against the commit before design.md existed -> design.md is "changed"; config.py never existed
        _, text = self.brief("implement", "1")
        ctx = sec(text, "Context files")
        self.assertIn("%s/docs/design.md — the sign-in flow [changed since planning — re-read]" % self.wt, ctx)
        # a path outside the repo has no freshness
        self.put_plan(self.plan_text.replace("src/config.py", "/etc/hosts"))
        _, text = self.brief("implement", "1")
        self.assertIn("/etc/hosts [freshness unknown]", sec(text, "Context files"))
        # planned against the tip -> unchanged
        tip = self.git(self.wt, "rev-parse", "HEAD")
        self.put_plan(self.plan_text.replace(self.base_sha, tip))
        _, text = self.brief("implement", "1")
        self.assertIn("docs/design.md — the sign-in flow [unchanged since planning]", sec(text, "Context files"))

    def test_freshness_unknown_without_planned_against_or_a_bad_sha(self):
        self.put_plan(self.plan_text.replace("- planned against: %s\n" % self.base_sha, ""))
        _, text = self.brief("implement", "1")
        self.assertEqual(sec(text, "Context files").count("[freshness unknown]"), 2)
        self.put_plan(self.plan_text.replace(self.base_sha, "not-a-sha; rm -rf"))
        _, text = self.brief("implement", "1")
        self.assertEqual(sec(text, "Context files").count("[freshness unknown]"), 2)

    def test_prior_work(self):
        _, text = self.brief("implement", "1")
        self.assertRegex(sec(text, "Prior work"), r"^[0-9a-f]+ edit feature\.txt$")
        for i in range(21):
            self.commit(self.wt, "f%d.txt" % i, "x\n", "commit number %d" % i)
        _, text = self.brief("implement", "1")
        rows = sec(text, "Prior work").splitlines()
        self.assertEqual(len(rows), 21)
        self.assertEqual(rows[-1], "... (more commits on the branch)")
        self.assertIn("commit number 20", rows[0])

    def test_prior_work_without_a_worktree_reads_the_branch(self):
        self.set_field(worktree="none (user: no worktree)")
        _, text = self.brief("implement", "1")
        self.assertRegex(sec(text, "Prior work"), r"^[0-9a-f]+ edit feature\.txt$")

    def test_extra_is_verbatim(self):
        extra = os.path.join(self.tmp, "extra.md")
        wr(extra, "Name the class: state reset.\n\n```\n## not a heading\n```\n")
        _, text = self.brief("implement", "1", "--extra", extra)
        self.assertEqual(sec(text, "From the orchestrator"), "Name the class: state reset.\n\n```\n## not a heading\n```")
        self.assertIn("If this brief is not enough", heads(text))
        self.refused(1, "implement", "1", "--extra", os.path.join(self.tmp, "nope.md"))

    def test_fallback_text(self):
        _, text = self.brief("implement", "1")
        f = sec(text, "If this brief is not enough")
        self.assertIn("grep -n '^#'", f)
        self.assertIn("brief-gap: <what was missing>", f)
        self.assertIn("notes.md", f)

    def test_size_is_a_fraction_of_the_plan(self):
        out, _ = self.cmd_ok("brief", "implement", "10")
        self.assertRegex(out.splitlines()[1], r"^brief \d+\.\d KB vs plan %.1f KB$" % (os.path.getsize(os.path.join(self.d, "plan.md")) / 1024.0))

    def test_only_briefs_and_the_lock_are_written(self):
        before = set(self.listing())
        self.brief("implement", "1")
        after = set(self.listing())
        self.assertEqual(sorted(after - before), ["briefs", "briefs/implement-s1.md"])

    def listing(self):
        out = []
        for d, dirs, files in os.walk(self.d):
            for n in dirs + files:
                p = os.path.relpath(os.path.join(d, n), self.d)
                if n != ".orch.lock":
                    out.append(p)
        return out

    def test_development_copy_refuses_without_orch_dir(self):
        import contextlib
        import io
        from unittest import mock
        from orchlib import cli
        env = dict(self.env)
        env.pop("ORCH_DIR")
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stderr(err):
            os.environ.pop("ORCH_DIR", None)
            rc = cli.main(["brief", "implement", "1", "-t", self.d])
        self.assertEqual(rc, 1)
        self.assertIn("development copy", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.d, "briefs")))


class TestLessons(BriefBase):
    def setUp(self):
        BriefBase.setUp(self)
        self.repo_lessons = os.path.join(self.root, "myapp", "LESSONS.md")
        self.global_lessons = os.path.join(self.root, "LESSONS.md")
        wr(self.repo_lessons, "# Lessons — myapp\n\n## 2026-01-05 — unrelated\n- role: orchestrator\n- lesson: always ask first.\n")
        wr(self.global_lessons,
           "# Lessons — global\n\n## Promoted\n\n| a | b |\n|---|---|\n| row | row |\n\n## Open\n\n"
           "## 2026-02-01 — implementer rule\n- role: implementer\n- lesson: run the tests you touched.\n\n"
           "## 2026-02-02 — planner only\n- role: planner\n- lesson: write contracts.\n\n"
           "## 2026-02-03 — file mention\n- role: orchestrator\n- lesson: auth.py parsing had a trap.\n\n"
           "## 2026-02-04 — repo mention\n- role: orchestrator\n- lesson: myapp deploys on friday.\n\n"
           "## 2026-02-05 — both roles\n- role: planner + implementer (+ orchestrator)\n- lesson: name the class.\n\n"
           "## 2026-02-06 — code reviewer\n- role: code-reviewer\n- lesson: read the diff.\n")

    def lessons(self, *args):
        _, text = self.brief(*args)
        return sec(text, "Lessons")

    def test_repo_file_in_full_even_when_it_names_nothing(self):
        les = self.lessons("implement", "2")
        self.assertIn("## 2026-01-05 — unrelated", les)
        self.assertIn("always ask first.", les)
        self.assertIn("# Lessons — myapp", les)
        self.assertIn(rd(self.repo_lessons).rstrip("\n"), les)

    def test_global_entries_by_role_file_or_repo(self):
        les = self.lessons("implement", "2")
        self.assertIn("implementer rule", les)           # role names implementer
        self.assertIn("both roles", les)                  # one of several roles
        self.assertIn("repo mention", les)                # names myapp
        self.assertNotIn("planner only", les)             # another role, mentions nothing
        self.assertNotIn("code reviewer", les)
        self.assertNotIn("file mention", les)             # auth.py is a step 1 file, not step 2's
        self.assertNotIn("| row | row |", les)            # the Promoted table is not an entry
        les = self.lessons("implement", "1")
        self.assertIn("file mention", les)                # basename of a step 1 file

    def test_no_lessons_files(self):
        os.unlink(self.repo_lessons)
        os.unlink(self.global_lessons)
        self.assertEqual(self.lessons("implement", "1"), "None.")

    def test_director_and_fix_briefs_carry_the_repo_file_too(self):
        self.put_plan(self.plan_text.replace("- sketch: none", "- sketch: none\n- visual: the page looks right"))
        _, text = self.brief("director", "1")
        les = sec(text, "Lessons")
        self.assertIn("always ask first.", les)
        self.assertNotIn("implementer rule", les)      # a director gets entries naming director, not implementer
        wr(os.path.join(self.d, "review.md"), "# Code review\n\n## Findings\n\n### F1 — x\n- severity: nit\n- file: %s/src/auth.py:3\n- what: y\n" % self.repo)
        _, text = self.brief("fix", "1", "--findings", "F1")
        les = sec(text, "Lessons")
        self.assertIn("always ask first.", les)
        self.assertIn("implementer rule", les)

    def test_review_briefs_carry_the_repo_file_and_reviewer_entries(self):
        _, text = self.brief("review")
        les = sec(text, "Lessons")
        self.assertIn("always ask first.", les)
        self.assertIn("code reviewer", les)
        self.assertNotIn("implementer rule", les)
        self.assertIn("repo mention", les)


class TestDirectorAndLegacyAndLite(BriefBase):
    def test_director_brief(self):
        self.put_plan(self.plan_text.replace("- sketch: none", "- sketch: none\n- visual: the login page matches the mock"))
        path, text = self.brief("director", "1", "--tier", "opus/high")
        self.assertEqual(os.path.basename(path), "director-s1.md")
        self.assertEqual(text.split("\n")[0], "# Brief: director step(s) 1 — myapp/add-login")
        self.assertEqual(heads(text)[:6], ["Where you work", "Checkpoint commands", "Summary", "Decisions", "Assumed decisions", "Your steps"])
        self.assertIn("What you judge (visual)", heads(text))
        self.assertEqual(sec(text, "What you judge"), "Step 1: the login page matches the mock")
        self.assertIn("role 'director' in_progress", sec(text, "Checkpoint commands"))
        self.assertIn("- tier: opus/high", sec(text, "Where you work"))
        self.refused(1, "director", "1,2")
        self.refused(1, "director", "1-3")

    def test_director_without_a_visual_line(self):
        _, text = self.brief("director", "2")
        self.assertIn("(no `visual:` line)", sec(text, "What you judge"))

    def test_legacy_plan_never_fails(self):
        self.put_plan(rd(os.path.join(FIX, "legacy.md")).replace("/home/u/Projects/myapp", self.repo))
        path, text = self.brief("implement", "1")
        self.assertTrue(text.split("\n")[2].startswith("WARNING: "))
        self.assertIn("no `### Step <id> — <title>` blocks (legacy format)", text)
        self.assertIn("1. Do the first thing in `src/a_module.py`.", sec(text, "Your steps"))
        self.assertIn("2. Do the second thing.", sec(text, "Your steps"))
        self.assertIn("- goal: an old style plan.", sec(text, "Summary"))
        self.assertIn("- D1 keep it simple.", sec(text, "Decisions"))
        self.assertEqual(heads(text), list(SHEADS))
        _, text = self.brief("implement", "7")      # ids are not validated without blocks
        self.assertIn("step(s) 7", text.split("\n")[0])

    def test_legacy_plan_without_a_steps_section_prints_the_whole_plan(self):
        self.put_plan("# Plan: odd\n\nJust prose about the work.\n")
        _, text = self.brief("implement", "1")
        self.assertIn("(no Steps section found; the whole plan follows)", sec(text, "Your steps"))
        self.assertIn("Just prose about the work.", sec(text, "Your steps"))
        self.assertIn("(this plan has no Summary section)", sec(text, "Summary"))

    def test_lite_plan_omitted_sections_are_empty_not_content(self):
        self.put_plan(rd(os.path.join(FIX, "lite.md")).replace("/home/u/Projects/myapp", self.repo))
        _, text = self.brief("implement", "1")
        self.assertEqual(sec(text, "Decisions"), "(omitted in this lite plan)")
        self.assertIn("`src/menu.py` registers the handler twice.", sec(text, "Findings about your files"))
        self.assertEqual(sec(text, "Risks about your steps"), "(omitted in this lite plan)")
        self.assertNotIn("Lite: omitted", sec(text, "Your steps"))

    def test_no_plan_is_an_error(self):
        os.unlink(os.path.join(self.d, "plan.md"))
        self.assertIn("no plan at", self.refused(1, "implement", "1"))

    def test_plan_option(self):
        other = os.path.join(self.tmp, "other-plan.md")
        wr(other, self.plan_text.replace("Auth module", "Auth module v2"))
        _, text = self.brief("implement", "1", "--plan", other)
        self.assertIn("### Step 1 — Auth module v2", text)
        self.assertIn("- plan: %s" % other, text)


class TestRevisionIntroducedStep(BriefBase):
    NEW = ("\n### Step 11 — Post-release check (new; runs after step 10)\n\n- size: S\n- files: @R@/CHANGELOG.md\n"
           "- depends: 10\n- do: look at the release.\n- contract: one line in the log.\n- done when: it is in the log.\n")

    def test_a_step_a_revision_adds_can_be_briefed(self):
        self.put_plan(self.plan_text.rstrip("\n") + "\n" + self.NEW.replace("@R@", self.repo))
        p = pl.parse_plan(self.plan_text.rstrip("\n") + "\n" + self.NEW)
        self.assertEqual(p.ids(), ["1", "2", "3", "3a", "10", "11"])
        self.assertEqual(p.step_count(), 6)
        self.assertEqual(p.last_step(), "10")
        _, text = self.brief("implement", "11")
        steps = sec(text, "Your steps")
        self.assertEqual(steps.count("### Step 11 — Post-release check"), 1)
        self.assertNotIn("this revision wins", steps)
        self.assertNotIn("Post-release check", sec(text, "Revisions that touch your steps"))
        self.assertIn("### Step 10 — Release notes", sec(text, "Steps yours depend on"))

    def test_resume_counts_it(self):
        self.put_plan(self.plan_text.rstrip("\n") + "\n" + self.NEW)
        out, _ = self.cmd_ok("resume", T)
        self.assertIn("(6 steps)", out)


class TestResumeUsesTheParser(BriefBase):
    def plan_line(self, text):
        self.put_plan(text)
        out, _ = self.cmd_ok("resume", T)
        return [l for l in out.splitlines() if l.startswith("plan: ")][0]

    def test_counts_blocks_not_fenced_headings(self):
        self.assertEqual(self.plan_line(self.plan_text), "plan: %s (5 steps)" % os.path.join(self.d, "plan.md"))
        fenced = "# P\n\n## Steps\n\n### Step 1 — a\n\n```\n### Step 2 — b\n```\n"
        self.assertEqual(self.plan_line(fenced), "plan: %s (1 steps)" % os.path.join(self.d, "plan.md"))

    def test_legacy_plan(self):
        self.assertEqual(self.plan_line("# P\n\n## Steps\n\n1. a\n2. b\n"),
                         "plan: %s (legacy format, no step blocks)" % os.path.join(self.d, "plan.md"))


class TestProgramPlanSelection(BriefBase):
    def program(self, ms=2):
        self.set_field(shape="roadmap", milestone="M%d — implementing" % ms)
        self.mdir = os.path.join(self.d, "m%02d-sessions" % ms)
        wr(os.path.join(self.mdir, "plan.md"), self.fixture_plan("program_m02.md"))

    def test_milestone_plan_is_chosen_from_status(self):
        self.program()
        _, text = self.brief("implement", "1")
        self.assertIn("- plan: %s" % os.path.join(self.mdir, "plan.md"), text)
        self.assertIn("### Step 1 — Session store", sec(text, "Your steps"))
        self.assertNotIn("Auth module", text)

    def test_missing_or_ambiguous_milestone_dir(self):
        self.set_field(shape="roadmap", milestone="M2 — implementing")
        self.assertIn("no milestone dir m02-*", self.refused(1, "implement", "1"))
        wr(os.path.join(self.d, "m02-a", "plan.md"), "x")
        wr(os.path.join(self.d, "m02-b", "plan.md"), "x")
        self.assertIn("more than one milestone dir", self.refused(1, "implement", "1"))
        self.set_field(milestone="n/a")
        self.assertIn("no resolvable milestone", self.refused(1, "implement", "1"))

    def test_a_single_plan_task_ignores_milestone_dirs(self):
        wr(os.path.join(self.d, "m02-sessions", "plan.md"), self.fixture_plan("program_m02.md"))
        _, text = self.brief("implement", "1")
        self.assertIn("### Step 1 — Auth module", text)


class TestReviewBrief(BriefBase):
    def tip_sha(self):
        return self.tip()

    def head_lines(self, text, n=60):
        return text.split("\n")[:n]

    def test_header_and_reader_accepts_it(self):
        path, text = self.brief("review")
        sha = self.tip_sha()
        self.assertEqual(os.path.basename(path), "review-code-r1.md")
        head = self.head_lines(text)
        self.assertEqual(head[0], "# Brief: code review, round 1 — myapp/add-login")
        self.assertEqual(head[1:4], ["- reviewed sha: %s" % sha, "- round: 1", "- range: main...%s" % sha])
        self.assertEqual(len([l for l in head if l.startswith("- reviewed sha:")]), 1)
        self.assertIn("Review exactly this range. If the branch has moved past %s, say so in your review file and don't review the newer commits." % sha, text)
        # the reader step 5 wrote accepts what this wrote, and stores the sha
        self.assertEqual(merge.brief_header(path, 1, False, None), sha)
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        rec = self.journal_lines(self.d)[-1]
        self.assertEqual((rec["kind"], rec["sha"], rec["round"]), ("review_round", sha, 1))

    def test_security_brief_name_and_reader(self):
        path, text = self.brief("security")
        self.assertEqual(os.path.basename(path), "review-security-r1.md")
        self.assertEqual(text.split("\n")[0], "# Brief: security review, round 1 — myapp/add-login")
        self.assertIn("- write your findings to: %s" % os.path.join(self.d, "security-review.md"), text)
        self.cmd_ok("journal", "review_round", "reviewer=security", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(self.journal_lines(self.d)[-1]["sha"], self.tip_sha())

    def test_round_defaults_from_status_and_can_be_set(self):
        self.set_field(review_round="1/3")
        path, text = self.brief("review")
        self.assertEqual(os.path.basename(path), "review-code-r2.md")
        self.assertIn("- round: 2", text)
        path, _ = self.brief("review", "--round", "3")
        self.assertEqual(os.path.basename(path), "review-code-r3.md")
        self.assertIn("at least 1", self.refused(1, "review", "--round", "0"))
        self.assertIn("takes no positional", self.refused(1, "review", "2"))

    def test_range_option_resolves_the_right_side(self):
        sha = self.tip_sha()
        _, text = self.brief("review", "--range", "main..orch/add-login")
        self.assertIn("- reviewed sha: %s" % sha, text)
        self.assertIn("- range: main..%s" % sha, text)
        self.refused(1, "review", "--range", "main")
        self.assertIn("is not a safe branch name", self.refused(1, "review", "--range", "main..-x"))
        self.assertIn("does not resolve", self.refused(1, "review", "--range", "main..nonesuch"))

    def test_the_sha_is_the_tip_at_brief_time(self):
        path, text = self.brief("review")
        old = self.tip_sha()
        self.commit(self.wt, "later.txt", "x\n")
        self.assertNotEqual(old, self.tip_sha())
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(self.journal_lines(self.d)[-1]["sha"], old)

    def test_rebriefing_a_round_after_a_commit_is_refused_and_leaves_the_brief(self):
        a, text_a = self.brief("review")
        old = self.tip_sha()
        self.commit(self.wt, "later.txt", "x\n")
        err = self.refused(2, "review", "--round", "1")
        self.assertIn("round 1 of the code review is already written at %s" % old[:9], err)
        self.assertIn("--round 2", err)
        self.assertEqual(rd(a), text_a)
        self.assertIn("- reviewed sha: %s" % old, rd(a))
        # the pin stays with the brief: journaling round 1 binds the old sha, not the new tip
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(self.journal_lines(self.d)[-1]["sha"], old)
        self.assertIn("already journaled", self.refused(2, "review", "--round", "1"))
        # a new round is the way to review the new commit
        b, text_b = self.brief("review", "--round", "2")
        self.assertIn("- reviewed sha: %s" % self.tip_sha(), text_b)
        self.assertNotEqual(a, b)
        # the security review of the same round is independent
        self.brief("security", "--round", "1")

    def test_rebriefing_a_round_at_the_same_sha_is_allowed(self):
        a, _ = self.brief("review", "--round", "1")
        extra = os.path.join(self.tmp, "more.md")
        wr(extra, "Look at the lock.\n")
        b, text = self.brief("review", "--round", "1", "--extra", extra)
        self.assertEqual(a, b)
        self.assertIn("Look at the lock.", text)
        self.assertIn("- reviewed sha: %s" % self.tip_sha(), text)

    def test_rebriefing_over_an_unreadable_brief_is_refused(self):
        a, _ = self.brief("review", "--round", "1")
        wr(a, "# Brief\n\nno header here\n")
        err = self.refused(2, "review", "--round", "1")
        self.assertIn("cannot be read", err)
        self.assertEqual(rd(a), "# Brief\n\nno header here\n")

    def test_checkpoint_commands_and_task_line(self):
        _, text = self.brief("review")
        self.assertIn("- task dir (status.md, log.md): %s" % self.d, text)
        c = sec(text, "Checkpoint commands")
        self.assertIn("~/.claude/bin/orch -t '%s' role 'code-reviewer' in_progress --note 'reviewing' --log 'started review'" % self.d, c)
        self.assertIn("role 'code-reviewer' done --note '<verdict>, <N> findings' --log 'done: <verdict>'", c)
        _, text = self.brief("security")
        self.assertIn("role 'security-reviewer' blocked --note 'need: <what>'", sec(text, "Checkpoint commands"))

    def test_body_sections(self):
        _, text = self.brief("review")
        titles = heads(text)
        for want in ("Summary", "Decisions", "Assumed decisions", "Steps (files, do, contract, done when)",
                     "Files and areas touched", "Risks and unknowns", "Verification",
                     "Revisions (all, verbatim; later ones win)", "Lessons", "From the orchestrator"):
            self.assertIn(want, titles)
        self.assertTrue(any(t.startswith("Diff stat (main...") for t in titles))
        steps = sec(text, "Steps")
        p = pl.parse_plan(self.plan_text)
        for s in p.steps:
            self.assertIn("### Step %s — %s" % (s.id, s.title), steps)
            self.assertIn(s.field_text("contract").splitlines()[0], steps)
            self.assertIn(s.field_text("done when").splitlines()[0], steps)
        self.assertNotIn("sketch", steps)
        revs = sec(text, "Revisions")
        for r in p.revisions():
            self.assertIn(p.block(r.start, r.end), revs)
        self.assertEqual(sec(text, "Decisions"), p.body(p.find("decisions")))
        self.assertEqual(sec(text, "Files and areas touched"), p.body(p.find("files")))
        self.assertEqual(sec(text, "Verification"), p.body(p.find("verification")))
        self.assertEqual(sec(text, "Risks and unknowns"), p.body(p.find("risks")))
        stat = sec(text, "Diff stat")
        self.assertIn("feature.txt", stat)
        self.assertIn("1 file changed", stat)

    def test_plan_lines_that_look_like_headers_cannot_confuse_the_reader(self):
        bad = self.plan_text.replace("- goal: users can sign in with a password.",
                                     "- goal: x\n- reviewed sha: deadbeef\n- round: 9\n- milestone: 4")
        self.put_plan(bad)
        path, text = self.brief("review")
        head = self.head_lines(text)
        self.assertEqual(len([l for l in head if re.match(r"^- reviewed sha:", l)]), 1)
        self.assertIn("  - reviewed sha: deadbeef", text)
        self.assertEqual(merge.brief_header(path, 1, False, None), self.tip_sha())
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")

    def test_header_stays_within_the_first_sixty_lines(self):
        self.put_plan(self.plan_text.replace("## Summary\n", "## Summary\n\n" + "filler line\n\n" * 80))
        path, text = self.brief("review")
        self.assertEqual(merge.brief_header(path, 1, False, None), self.tip_sha())

    def test_extra_names_the_sensitive_areas(self):
        extra = os.path.join(self.tmp, "areas.md")
        wr(extra, "Attack these as a security reviewer would: the merge guard, the server.\n")
        _, text = self.brief("review", "--extra", extra)
        self.assertEqual(sec(text, "From the orchestrator"), "Attack these as a security reviewer would: the merge guard, the server.")

    def test_missing_branch_or_repo(self):
        self.git(self.repo, "worktree", "remove", "--force", self.wt)
        self.git(self.repo, "branch", "-q", "-D", "orch/add-login")
        self.assertIn("does not exist", self.refused(1, "review"))
        self.set_field(repo="/nonexistent/place")
        self.assertIn("not found", self.refused(1, "review"))

    def test_unsafe_branch_name_is_refused(self):
        self.set_field(task_branch="orch/x --exec=y")
        self.assertIn("not a safe branch name", self.refused(1, "review"))

    def test_legacy_plan_review_brief(self):
        self.put_plan(rd(os.path.join(FIX, "legacy.md")))
        path, text = self.brief("review")
        self.assertIn("WARNING:", text)
        self.assertIn("1. Do the first thing", sec(text, "Steps"))
        self.assertEqual(merge.brief_header(path, 1, False, None), self.tip_sha())


class TestProgramReviewBrief(BriefBase):
    def program(self, ms, reviewed_through="none"):
        self.set_field(shape="roadmap", milestone="M%d — reviewing" % ms, reviewed_through=reviewed_through)
        self.mdir = os.path.join(self.d, "m%02d-sessions" % ms)
        wr(os.path.join(self.mdir, "plan.md"), self.fixture_plan("program_m02.md"))

    def test_names_and_headers_for_a_program(self):
        self.program(2)
        sha = self.tip()
        path, text = self.brief("review")
        self.assertEqual(os.path.basename(path), "review-code-m02-r1.md")
        head = text.split("\n")[:60]
        self.assertEqual(head[1:5], ["- reviewed sha: %s" % sha, "- round: 1", "- milestone: 2", "- range: main...%s" % sha])
        self.assertEqual(merge.brief_header(path, 1, True, 2), sha)
        self.assertIn("- write your findings to: %s" % os.path.join(self.mdir, "review.md"), text)
        path, _ = self.brief("security")
        self.assertEqual(os.path.basename(path), "review-security-m02-r1.md")

    def run_brief_checkpoints(self, text, role):
        """Run every command in the brief's Checkpoint commands block as written; return the commands."""
        import shlex
        block = re.search(r"```\n(.*?)\n```", sec(text, "Checkpoint commands"), re.S).group(1)
        cmds = [l for l in block.split("\n") if l and not l.startswith("#")]
        self.assertEqual(len(cmds), 4)
        for line in cmds:
            argv = shlex.split(line.replace("<verdict>", "pass").replace("<N>", "0").replace("<file group>", "all")
                               .replace("<what>", "x"))
            self.assertEqual(argv[0], "~/.claude/bin/orch")
            self.assertEqual(argv[1:3], ["-t", self.d])      # the program dir, never the milestone dir
            self.assertEqual(argv[3:5], ["role", role])
            self.ok(*argv[1:])
        return cmds

    def test_review_brief_checkpoint_commands_work_for_a_program(self):
        self.program(2)
        for reviewer in ("review", "security"):
            role = "code-reviewer" if reviewer == "review" else "security-reviewer"
            _, text = self.brief(reviewer)
            self.assertIn("- task dir (status.md, log.md): %s" % self.d, text)
            self.assertNotIn("-t '%s'" % self.mdir, text)
            self.run_brief_checkpoints(text, role)
            row = [l for l in rd(os.path.join(self.d, "status.md")).split("\n") if l.startswith("| %s |" % role)]
            self.assertEqual(len(row), 1, role)
            self.assertIn("| blocked |", row[0])         # the four commands ran in order; the last one wins
        log = rd(os.path.join(self.d, "log.md"))
        self.assertIn("done: pass", log)
        self.assertFalse(os.path.exists(os.path.join(self.mdir, "status.md")))   # nothing was written beside the plan
        # the milestone dir is not a task dir: the command with it fails, which is why the brief never names it
        self.assertNotEqual(self.run_orch("-t", self.mdir, "role", "code-reviewer", "done")[0], 0)

    def test_range_after_a_review_point_starts_at_reviewed_through(self):
        first = self.git(self.repo, "rev-parse", "orch/add-login")
        self.commit(self.wt, "second.txt", "x\n")
        sha = self.tip()
        self.program(5, reviewed_through=first)
        _, text = self.brief("review")
        self.assertIn("- range: %s..%s" % (first, sha), text)
        self.assertIn("- milestone: 5", text)

    def test_reader_binds_each_milestone_to_its_own_brief(self):
        self.program(2)
        a, _ = self.brief("review", "--round", "2")
        sha_m2 = self.tip()
        self.commit(self.wt, "more.txt", "x\n")
        self.program(5)
        b, _ = self.brief("review", "--round", "1")
        sha_m5 = self.tip()
        self.assertNotEqual(sha_m2, sha_m5)
        self.assertEqual([os.path.basename(a), os.path.basename(b)], ["review-code-m02-r2.md", "review-code-m05-r1.md"])
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        rec = self.journal_lines(self.d)[-1]
        self.assertEqual((rec["milestone"], rec["round"], rec["sha"]), (5, 1, sha_m5))
        self.assertIn("is already journaled", self.refused(2, "review", "--round", "1"))
        self.brief("review", "--round", "2")      # M5 round 2 is free; M2's round 2 brief is another file

    def test_single_plan_rounds_bind_their_own_brief(self):
        # a plan task: r1 then r2 exist; journaling round 1 binds r1's sha, not r2's
        _, _ = self.brief("review")
        sha1 = self.tip()
        self.commit(self.wt, "r2.txt", "x\n")
        self.brief("review", "--round", "2")
        sha2 = self.tip()
        self.cmd_ok("journal", "review_round", "reviewer=code", "round:=1", "blockers:=0", "should_fix:=0", "nits:=0", "verdict=pass")
        self.assertEqual(self.journal_lines(self.d)[-1]["sha"], sha1)
        self.assertNotEqual(sha1, sha2)


class TestFixBrief(BriefBase):
    REVIEW = ("# Code review: add-login\n\n- round: 1\n- verdict: findings\n\n## Findings\n\n"
              "### F1 — hashing is unsalted\n- severity: should-fix\n- origin: introduced\n- file: @R@/src/auth.py:12\n"
              "- what: sha256 without a salt.\n- why it matters: rainbow tables.\n- suggested fix: scrypt.\n- resolution:\n\n"
              "### F2 — typo in a log line\n- severity: nit\n- file: @R@/CHANGELOG.md:3\n- what: spelling.\n\n"
              "### F3 — redirect loop\n- severity: blocker\n- file: @R@/src/views.py:40\n- what: loops forever.\n\n"
              "## Round 2\n\n- reviewed at: 2026-03-04T10:00Z\n- F1: still open — the salt is a constant.\n- F2: resolved\n")
    SECURITY = ("# Security review: add-login\n\n- round: 1\n\n## Findings\n\n"
                "### S1 — open redirect\n- severity: high\n- file: @R@/src/views.py:77\n- attack path: next= parameter.\n- resolution:\n")

    def setUp(self):
        BriefBase.setUp(self)
        wr(os.path.join(self.d, "review.md"), self.REVIEW.replace("@R@", self.repo))
        wr(os.path.join(self.d, "security-review.md"), self.SECURITY.replace("@R@", self.repo))

    def test_named_findings_verbatim_and_their_steps(self):
        path, text = self.brief("fix", "1", "--findings", "F1,S1")
        self.assertEqual(os.path.basename(path), "fix-r1.md")
        self.assertEqual(text.split("\n")[0], "# Brief: fix round 1 (F1, S1) — myapp/add-login")
        findings = sec(text, "Findings to fix")
        self.assertIn("### F1 — hashing is unsalted\n- severity: should-fix\n- origin: introduced\n- file: %s/src/auth.py:12\n- what: sha256 without a salt.\n- why it matters: rainbow tables.\n- suggested fix: scrypt.\n- resolution:" % self.repo, findings)
        self.assertIn("### S1 — open redirect", findings)
        self.assertIn("(Round 2) - F1: still open — the salt is a constant.", findings)
        self.assertNotIn("F2", findings)
        self.assertNotIn("F3", findings)
        steps = sec(text, "Steps whose files")
        self.assertIn("### Step 1 — Auth module", steps)      # F1 cites src/auth.py
        self.assertIn("### Step 3 - Views", steps)            # S1 cites src/views.py
        self.assertIn("### Step 3a: Views follow-up", steps)
        self.assertNotIn("### Step 2", steps)
        self.assertNotIn("### Step 10", steps)
        self.assertIn("role 'implementer' in_progress --note 'working on step(s) fix round 1'", sec(text, "Checkpoint commands"))
        self.assertIn("role 'implementer' done --step 'fix round 1'", sec(text, "Checkpoint commands"))

    def test_names_never_overwrite_and_extra(self):
        extra = os.path.join(self.tmp, "class.md")
        wr(extra, "Class: every hash call. Invariant: salted.\n")
        a, text = self.brief("fix", "1", "--findings", "F3", "--extra", extra)
        b, _ = self.brief("fix", "1", "--findings", "F3")
        c, _ = self.brief("fix", "2", "--findings", "F3")
        self.assertEqual([os.path.basename(x) for x in (a, b, c)], ["fix-r1.md", "fix-r1-2.md", "fix-r2.md"])
        self.assertEqual(sec(text, "From the orchestrator"), "Class: every hash call. Invariant: salted.")

    def test_errors(self):
        self.assertIn("needs --findings", self.refused(1, "fix", "1"))
        self.assertIn("needs the round number", self.refused(1, "fix", "--findings", "F1"))
        self.assertIn("needs the round number", self.refused(1, "fix", "x", "--findings", "F1"))
        self.assertIn("finding(s) not found: F9", self.refused(1, "fix", "1", "--findings", "F1,F9"))
        self.assertIn("bad finding id", self.refused(1, "fix", "1", "--findings", "X1"))
        os.unlink(os.path.join(self.d, "security-review.md"))
        self.assertIn("security-review.md not found", self.refused(1, "fix", "1", "--findings", "S1"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "briefs")))

    def test_program_reads_the_milestone_dir(self):
        self.set_field(shape="roadmap", milestone="M2 — reviewing")
        mdir = os.path.join(self.d, "m02-sessions")
        wr(os.path.join(mdir, "plan.md"), self.fixture_plan("program_m02.md"))
        wr(os.path.join(mdir, "review.md"), self.REVIEW.replace("@R@", self.repo).replace("hashing is unsalted", "session is unsigned").replace("src/auth.py", "src/session.py"))
        _, text = self.brief("fix", "1", "--findings", "F1")
        self.assertIn("### F1 — session is unsigned", text)
        self.assertIn("### Step 1 — Session store", sec(text, "Steps whose files"))


class TestMentionMatching(BriefBase):
    def test_step_one_brief_never_pulls_step_ten_text(self):
        _, text = self.brief("implement", "1")
        for part in ("Findings about your files", "Risks about your steps", "Verification for your steps",
                     "Revisions that touch your steps"):
            self.assertNotIn("step 10", sec(text, part), part)
            self.assertNotIn("Step 10", sec(text, part), part)

    def test_ranges_in_text_cover_the_step(self):
        self.put_plan(self.plan_text.replace("- The CI config is unrelated to this task.", "- Steps 2–3 share the cookie jar."))
        _, text = self.brief("implement", "3")
        self.assertIn("Steps 2–3 share the cookie jar.", sec(text, "Findings about your files"))
        _, text = self.brief("implement", "1")
        self.assertNotIn("cookie jar", sec(text, "Findings about your files"))


if __name__ == "__main__":
    unittest.main()
