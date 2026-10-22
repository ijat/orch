"""Plan parser, mention matching, unit filtering and `orch plan-check`. Neutral synthetic fixtures only."""
import contextlib
import io
import os
import subprocess
import sys
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, BIN)

import test_core as core  # noqa: E402
from orchlib import plan as pl  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "plans")
REPO = "/home/u/Projects/myapp"


def fixture(name, sha="0123456789abcdef0123456789abcdef01234567"):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read().replace("@@SHA@@", sha)


def parse(name):
    return pl.parse_plan(fixture(name))


class TestParser(unittest.TestCase):
    def setUp(self):
        self.p = parse("standard.md")

    def test_steps_ids_titles_and_separators(self):
        self.assertFalse(self.p.legacy)
        self.assertEqual(self.p.ids(), ["1", "2", "3", "3a", "10"])
        self.assertEqual([s.title for s in self.p.steps],
                         ["Auth module", "Session store", "Views", "Views follow-up", "Release notes"])

    def test_step_one_never_matches_step_ten(self):
        self.assertEqual(self.p.step("1").title, "Auth module")
        self.assertEqual(self.p.step("10").title, "Release notes")
        self.assertIsNone(self.p.step("11"))

    def test_fenced_headings_and_fields_are_ignored(self):
        self.assertIsNone(self.p.step("99"))
        titles = [s.title for s in self.p.sections]
        self.assertNotIn("Not a section either", titles)
        s2 = self.p.step("2")
        self.assertEqual(s2.field_text("size"), "M")
        self.assertIn("### Step 99 — not a step (inside a fence)", s2.field_text("do"))
        self.assertIn("- size: XL", s2.field_text("do"))
        self.assertEqual(s2.field_text("contract"), "sessions expire after one hour.")

    def test_fields_and_continuation(self):
        s1 = self.p.step("1")
        self.assertEqual(s1.order, ["size", "files", "depends", "context", "do", "contract", "sketch", "done when"])
        self.assertEqual(s1.field_text("depends"), "none")
        ctx = s1.field_text("context")
        self.assertIn("/home/u/Projects/myapp/docs/design.md — the sign-in flow", ctx)
        self.assertIn("/home/u/Projects/myapp/src/config.py", ctx)
        self.assertEqual(s1.field_text("done when"), "`pytest tests/test_auth.py` passes.")
        self.assertIsNone(s1.field_text("pilot"))

    def test_files_and_context_paths(self):
        self.assertEqual(self.p.step("1").files(),
                         [REPO + "/src/auth.py", REPO + "/tests/test_auth.py", REPO + "/docs/design.md",
                          REPO + "/src/config.py"])

    def test_header_fields(self):
        self.assertEqual(self.p.header["repo"], REPO)
        self.assertEqual(self.p.planned_against, "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(pl.parse_plan("# P\n\n- planned against: <sha>\n\n## Summary\n").planned_against, "")

    def test_revisions(self):
        self.assertEqual([r.rev for r in self.p.revisions()], [1, 2])
        self.assertEqual([(n, s.id) for n, s in self.p.rev_steps], [(1, "2"), (1, "3")])
        # revision step blocks are not plan steps
        self.assertEqual(self.p.ids().count("2"), 1)

    def test_section_lookup(self):
        self.assertEqual(self.p.find("summary").title, "Summary")
        self.assertEqual(self.p.find("risks").title, "Risks and unknowns")
        self.assertEqual(self.p.find("files").title, "Files and areas touched")
        self.assertIsNone(self.p.find("nonsense"))
        self.assertEqual([s.title for s in self.p.decision_sections()], ["Decisions"])

    def test_legacy_plan_has_no_step_blocks(self):
        legacy = parse("legacy.md")
        self.assertTrue(legacy.legacy)
        self.assertEqual(legacy.steps, [])
        self.assertEqual([s.title for s in legacy.step_sections], ["Steps"])

    def test_lite_plan_omitted_sections(self):
        lite = parse("lite.md")
        self.assertFalse(lite.legacy)
        self.assertTrue(lite.is_omitted(lite.find("goal")))
        self.assertTrue(lite.is_omitted(lite.find("decisions")))
        self.assertFalse(lite.is_omitted(lite.find("summary")))

    def test_step_blocks_without_a_steps_section_are_tolerated(self):
        p = pl.parse_plan("# Plan\n\n### Step 1 — a\n\n### Step 2 — b\n\n## Revision 1\n\n### Step 2 — changes\n")
        self.assertEqual(p.ids(), ["1", "2"])
        self.assertFalse(p.legacy)

    def test_decision_like_sections_are_all_decisions(self):
        p = pl.parse_plan("# P\n\n## Decisions\n\nd\n\n## User decisions at the plan gate (2026-01-01)\n\nu\n\n"
                          "## Gate (2026-01-02, user)\n\ng\n\n## Goal\n\nx\n")
        self.assertEqual([s.norm for s in p.decision_sections()], ["decisions", "user decisions at the plan gate", "gate"])

    def test_never_raises_on_odd_input(self):
        for text in ("", "\n", "### Step 1", "## Steps\n### Step x — y\n", "```\n### Step 1 — a\n",
                     "## Steps — Phase 1\n### Step 1 — a\n- size: S\n- files:\n", "#\n##\n###\n",
                     b"\xff\xfe## Steps\n### Step 1 \xff\n".decode("utf-8", "surrogateescape")):
            p = pl.parse_plan(text)
            self.assertIsInstance(p.legacy, bool)
            pl.check_plan(p)

    def test_unclosed_fence_swallows_the_rest_without_failing(self):
        p = pl.parse_plan("# P\n\n## Summary\n\n## Steps\n\n```\n### Step 1 — a\n")
        self.assertTrue(p.legacy)

    def test_steps_phase_sections(self):
        p = pl.parse_plan("# P\n\n## Steps — Phase 1\n\n### Step 1 — a\n\n## Steps — Phase 2\n\n### Step 2 — b\n")
        self.assertEqual(p.ids(), ["1", "2"])

    def test_pre_written_blocks_are_byte_exact(self):
        s2 = self.p.step("2")
        text = s2.text()
        self.assertTrue(text.startswith("### Step 2 – Session store\n"))
        self.assertIn("  ```\n  ### Step 99 — not a step (inside a fence)\n", text)
        self.assertTrue(text.endswith("- done when: `pytest tests/test_session.py` passes."))


class TestRefs(unittest.TestCase):
    def hit(self, text, *wanted):
        return pl.refs_hit(text, set(wanted))

    def test_ids_do_not_prefix_match(self):
        self.assertTrue(self.hit("see Step 1 now", "1"))
        self.assertFalse(self.hit("see Step 10 now", "1"))
        self.assertTrue(self.hit("see Step 10 now", "10"))
        self.assertFalse(self.hit("step 3a only", "3"))
        self.assertTrue(self.hit("step 3a only", "3a"))
        self.assertFalse(self.hit("substep 6", "6"))
        self.assertFalse(self.hit("step 6th", "6"))

    def test_spellings(self):
        for text in ("Step 6", "step 6.", "STEP 6:", "step-6.md", "sketches/step_6.md", "(steps 6)"):
            self.assertTrue(self.hit(text, "6"), text)

    def test_ranges_and_lists(self):
        self.assertTrue(self.hit("steps 3–5", "4"))
        self.assertTrue(self.hit("steps 3-5", "5"))
        self.assertFalse(self.hit("steps 3-5", "6"))
        self.assertTrue(self.hit("steps 3, 5 and 6", "6"))
        self.assertTrue(self.hit("Steps 3 / 5", "5"))
        self.assertTrue(self.hit("steps 3–5", "4a"))
        self.assertFalse(self.hit("steps 5 and 7", "6"))

    def test_a_separator_before_a_word_is_not_a_second_id(self):
        self.assertTrue(self.hit("Step 1 — Stage, stagectl", "1"))
        self.assertFalse(self.hit("Step 1 — Stage, stagectl", "2"))

    def test_parse_depends(self):
        self.assertEqual(pl.parse_depends("none"), ([], []))
        self.assertEqual(pl.parse_depends(None), ([], []))
        self.assertEqual(pl.parse_depends("3"), (["3"], []))
        self.assertEqual(pl.parse_depends("1, 2"), (["1", "2"], []))
        self.assertEqual(pl.parse_depends("3 (CLI core)"), (["3"], []))
        self.assertEqual(pl.parse_depends("Steps 1 and 2"), (["1", "2"], []))
        ids, ranges = pl.parse_depends("1-3")
        self.assertEqual(ranges, [(1, 3)])
        self.assertEqual(pl.parse_depends("the CLI"), ([], []))


class TestKeys(unittest.TestCase):
    def keys(self, ids=(), paths=(), roots=(REPO,)):
        return pl.Keys(ids, paths, roots)

    def test_absolute_relative_and_basename(self):
        k = self.keys(paths=[REPO + "/src/auth_module.py"])
        self.assertTrue(k.matches("edits %s/src/auth_module.py today" % REPO))
        self.assertTrue(k.matches("the file src/auth_module.py"))
        self.assertTrue(k.matches("`auth_module.py` is odd"))
        self.assertTrue(k.matches("about auth_module.py."))
        self.assertFalse(k.matches("the file xauth_module.py"))
        self.assertFalse(k.matches("the file auth_module.pyc"))

    def test_short_basenames_do_not_count(self):
        k = self.keys(paths=[REPO + "/src/a.py"])
        self.assertFalse(k.matches("a.py is everywhere"))
        self.assertTrue(k.matches("src/a.py is named"))

    def test_six_character_basename_counts(self):
        k = self.keys(paths=[REPO + "/src/cli.py"])
        self.assertTrue(k.matches("cli.py"))

    def test_directories_match_files_below(self):
        k = self.keys(paths=[REPO + "/tests/fixtures/"])
        self.assertTrue(k.matches("%s/tests/fixtures/plans/x.md" % REPO))
        self.assertFalse(k.matches("tests/fixtures_old/x.md"))

    def test_case_insensitive_ids_and_paths(self):
        k = self.keys(ids=["3A"], paths=[REPO + "/README.md"])
        self.assertTrue(k.matches("STEP 3a"))
        self.assertTrue(k.matches("see readme.md"))

    def test_step_one_vs_ten_in_keys(self):
        k = self.keys(ids=["1"])
        self.assertTrue(k.matches("Step 1"))
        self.assertFalse(k.matches("Step 10"))
        self.assertFalse(k.matches("nothing"))


class TestUnits(unittest.TestCase):
    def units(self, text):
        p = pl.parse_plan(text)
        return [p.block(a, b) for a, b in pl.split_units(p.lines, p.mask, 0, len(p.lines))]

    def test_paragraphs_and_bullets(self):
        us = self.units("para one\nstill one\n\n- bullet a\n  continues\n\n  after a blank\n- bullet b\n\ntail\n")
        self.assertEqual(us, ["para one\nstill one", "- bullet a\n  continues\n\n  after a blank", "- bullet b", "tail"])

    def test_fence_stays_in_its_unit(self):
        us = self.units("- item\n  ```\n  code\n\n  more\n  ```\n- next\n")
        self.assertEqual(us, ["- item\n  ```\n  code\n\n  more\n  ```", "- next"])

    def test_numbered_items(self):
        self.assertEqual(self.units("1. one\n2. two\n"), ["1. one", "2. two"])

    def test_label_segments(self):
        segs = pl.label_segments("- Step 1: a. Step 2: b `x`. Step 10: c.")
        self.assertEqual(segs, ["Step 1: a.", "Step 2: b `x`.", "Step 10: c."])
        self.assertTrue(segs[1].startswith("Step 2:"))
        self.assertIsNone(pl.label_segments("- Step 1: only one label"))

    def test_filter_section_keeps_matching_units_and_each_heading_once(self):
        p = parse("standard.md")
        k = pl.Keys(["1"], [REPO + "/src/auth.py"], (REPO,))
        text = pl.filter_section(p, p.find("findings"), k)
        self.assertIn("### Verified", text)
        self.assertEqual(text.count("### Verified"), 1)
        self.assertIn("src/auth.py", text)
        self.assertNotIn("session cookie", text)
        self.assertNotIn("step 10", text)
        self.assertNotIn("Assumptions", text)

    def test_filter_section_matches_step_references(self):
        p = parse("standard.md")
        text = pl.filter_section(p, p.find("findings"), pl.Keys(["10"], [], (REPO,)))
        self.assertIn("step 10", text)
        self.assertNotIn("sha256", text)
        text = pl.filter_section(p, p.find("findings"), pl.Keys(["3"], [], (REPO,)))
        self.assertIn("Step 3 assumes", text)
        self.assertIn("### Assumptions (not verified)", text)

    def test_verification_labels_are_split(self):
        p = parse("standard.md")
        sec = p.find("verification")
        k = pl.Keys(["10"], [], (REPO,))
        text = pl.filter_section(p, sec, k, keep_paras=True, split_labels=True)
        self.assertIn("Run from the repo root.", text)
        self.assertIn("- Step 10: `grep -c entry CHANGELOG.md`.", text)
        self.assertNotIn("Step 3a", text)
        self.assertNotIn("test_auth", text)


class TestCheck(unittest.TestCase):
    def check(self, text, lite=False):
        return pl.check_plan(pl.parse_plan(text), lite)

    def fails(self, text, lite=False):
        return [m for l, m in self.check(text, lite) if l == "FAIL"]

    def warns(self, text, lite=False):
        return [m for l, m in self.check(text, lite) if l == "WARN"]

    def test_standard_fixture_has_no_fail(self):
        self.assertEqual(self.fails(fixture("standard.md")), [])

    def test_no_summary_and_no_steps(self):
        base = fixture("standard.md")
        no_summary = base.replace("## Summary", "## Overview")
        self.assertTrue(any("Summary" in m for m in self.fails(no_summary)))
        no_steps = base.replace("## Steps\n", "## Work\n")
        self.assertIn("no Steps section", self.fails(no_steps))
        self.assertTrue(any("no `### Step" in m or "no Steps" in m for m in self.fails("# P\n\n## Summary\n\nx\n\n## Steps\n\nprose\n")))

    def test_missing_step_fields(self):
        base = fixture("standard.md")
        s, e = base.index("### Step 1 "), base.index("### Step 2 ")
        block = base[s:e]
        for field in ("size", "files", "do", "contract", "done when"):
            lines = block.split("\n")
            kept = [l for l in lines if not l.startswith("- %s:" % field)]
            self.assertEqual(len(kept), len(lines) - 1, field)
            msgs = self.fails(base[:s] + "\n".join(kept) + base[e:])
            self.assertIn("step 1 lacks: %s" % field, msgs)

    def test_duplicate_ids(self):
        text = fixture("standard.md").replace("### Step 3a: Views follow-up", "### Step 3 — Views again")
        self.assertTrue(any("duplicate step id 3" in m for m in self.fails(text)))

    def test_depends_unknown_and_later(self):
        text = fixture("standard.md").replace("- depends: 3\n", "- depends: 7\n")
        self.assertIn("step 3a depends on unknown step 7", self.fails(text))
        text = fixture("standard.md").replace("- depends: none\n", "- depends: 2\n")
        self.assertIn("step 1 depends on step 2, which is not earlier", self.fails(text))
        text = fixture("standard.md").replace("- depends: none\n", "- depends: 1\n")
        self.assertIn("step 1 depends on step 1, which is not earlier", self.fails(text))
        text = fixture("standard.md").replace("- depends: 1-3\n", "- depends: 1-4\n")
        self.assertEqual([m for m in self.fails(text) if "depends" in m], ["step 10 depends on unknown step 4"])

    def test_depends_prose_is_not_checked(self):
        text = fixture("standard.md").replace("- depends: 1, 2\n", "- depends: the CLI core\n")
        self.assertEqual([m for m in self.fails(text) if "depends" in m], [])

    def test_warn_non_absolute_paths(self):
        text = fixture("standard.md").replace("/home/u/Projects/myapp/src/session.py\n- depends: 1\n- do: cookie", "src/session.py\n- depends: 1\n- do: cookie")
        self.assertTrue(any("step 2: non-absolute" in m and "src/session.py" in m for m in self.warns(text)))
        self.assertEqual([m for m in self.warns(fixture("standard.md")) if "non-absolute" in m], [])

    def test_warn_missing_planned_against(self):
        text = fixture("standard.md").replace("- planned against: 0123456789abcdef0123456789abcdef01234567\n", "")
        self.assertTrue(any("planned against" in m for m in self.warns(text)))
        self.assertEqual([m for m in self.warns(fixture("standard.md")) if "planned against" in m], [])

    def test_warn_summary_count(self):
        text = fixture("standard.md").replace("  5. Release notes (S)\n", "")
        self.assertIn("Summary lists 4 step(s), Steps has 5 block(s)", self.warns(text))
        self.assertEqual([m for m in self.warns(fixture("standard.md")) if m.startswith("Summary lists")], [])
        # inline index on one line
        inline = fixture("standard.md").replace("- steps:\n  1. Auth module — password check (S)\n  2. Session store — cookie sessions (M)\n  3. Views — login page and logout (M)\n  4. Docs — README section (S)\n  5. Release notes (S)\n",
                                               "- steps: 1. a (S); 2. b (S)\n")
        self.assertIn("Summary lists 2 step(s), Steps has 5 block(s)", self.warns(inline))

    def test_lite(self):
        text = fixture("lite.md")
        self.assertEqual(self.fails(text, lite=True), [])
        self.assertEqual([m for m in self.warns(text, lite=True) if "omitted" in m], [])
        self.assertTrue(any("Goal is `Lite: omitted.`" in m for m in self.warns(text, lite=False)))
        # a lite plan still needs its steps' contract
        broken = text.replace("- contract: one handler per element.\n", "")
        self.assertIn("step 1 lacks: contract", self.fails(broken, lite=True))

    def test_missing_standard_section_is_a_warning(self):
        text = fixture("standard.md").replace("## Approach", "## Notes")
        self.assertTrue(any("no Approach section" in m for m in self.warns(text)))

    def test_fenced_step_does_not_count(self):
        self.assertEqual([m for m in self.fails(fixture("standard.md")) if "99" in m], [])


class TestPlanCheckCommand(core.Base):
    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_exit_codes_and_output(self):
        good = self.write("good/plan.md", fixture("standard.md"))
        rc, out, err = self.run_orch("plan-check", good)
        self.assertEqual(rc, 0, out + err)
        last = out.strip().splitlines()[-1]
        self.assertEqual(last, "plan-check: OK (0 fail, 0 warn; 5 step(s))")
        bad = self.write("bad/plan.md", fixture("standard.md").replace("## Summary", "## Overview"))
        rc, out, err = self.run_orch("plan-check", bad)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL: no Summary section", out)
        self.assertIn("plan-check: FAIL (1 fail", out.splitlines()[-1])
        rc, out, err = self.run_orch("plan-check", os.path.join(self.tmp, "missing.md"))
        self.assertEqual(rc, 2)
        self.assertIn("ERROR", out)

    def test_lite_flag(self):
        path = self.write("lite/plan.md", fixture("lite.md"))
        rc, out, _ = self.run_orch("plan-check", path, "--lite")
        self.assertEqual(rc, 0)
        self.assertNotIn("omitted", out)
        rc, out, _ = self.run_orch("plan-check", path)
        self.assertEqual(rc, 0)
        self.assertIn("Lite: omitted", out)

    def test_task_plan_is_found_from_the_task(self):
        d = self.init()
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write(fixture("standard.md"))
        rc, out, err = self.run_orch("plan-check", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, out + err)
        rc, out, err = self.run_orch("plan-check")
        self.assertEqual(rc, 1)
        self.assertIn("plan-check needs", err)

    def test_plan_check_writes_nothing(self):
        d = self.init()
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write(fixture("standard.md"))
        before = core.tree_hash(self.home)
        self.run_orch("plan-check", "-t", "myapp/add-login")
        self.assertEqual(core.tree_hash(self.home), before)

    def test_roadmap_goes_to_the_program_checker(self):
        checker = os.path.join(BIN, "orch-program-check.py")
        for name, text in (("garbage/roadmap.md", "# Roadmap\n\nno table here\n"),
                           ("other/anything.md", "# Roadmap\n\n## Milestones\n\n| M | goal | depends on | mode | tiers | sensitive | review | merge | you | size | confidence |\n|---|---|---|---|---|---|---|---|---|---|---|\n| 1 | auth | - | standard | opus/high | none | skip | merge | merge | S | high |\n")):
            path = self.write(name, text)
            direct = subprocess.run([sys.executable, checker, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    universal_newlines=True)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                rc, out, err = self.run_orch("plan-check", path)
            self.assertEqual(rc, direct.returncode, (name, out, direct.stdout))
            self.assertEqual(out, direct.stdout, name)
        # a path named roadmap.md is a roadmap even without a table; a plan with steps is not
        rc, out, _ = self.run_orch("plan-check", self.write("x/plan.md", fixture("standard.md")))
        self.assertNotIn("milestone", out)

    def test_main_restores_argv(self):
        saved = list(sys.argv)
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            pl.program_check_main(self.write("r/roadmap.md", "# R\n"))
        self.assertEqual(sys.argv, saved)


if __name__ == "__main__":
    unittest.main()
