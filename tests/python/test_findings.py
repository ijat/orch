"""orch findings, orch write review, orch finding add|resolve: one parser for the review files. Neutral fixtures only."""
import io
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

import test_core as core  # noqa: E402
from orchlib import findings as fd, paths  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "reviews")
S = "bd0dbcca-0000-4000-8000-000000000003"
T = "myapp/add-login"
PLAN = "# Plan: add-login\n\n## Summary\n\nx\n"
EMPTY_COUNTS = "severity-unknown 0"


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def block(fid, severity="should-fix", origin="introduced", file="src/a.py:1", extra=""):
    lines = ["### %s — thing %s" % (fid, fid), "- severity: %s" % severity]
    if origin:
        lines.append("- origin: %s" % origin)
    if file:
        lines.append("- file: %s" % file)
    return "\n".join(lines) + "\n" + extra + "- resolution:\n"


class TestParse(unittest.TestCase):
    def states(self, text, security=False):
        return dict((f.id, f.state) for f in fd.parse_text(text, security).findings)

    def test_rounds_and_checks_give_the_latest_status(self):
        p = fd.parse_text(fixture("rounds.md"))
        self.assertEqual([(f.id, f.state) for f in p.findings], [("F1", "closed"), ("F2", "closed"), ("F3", "open"), ("F4", "open")])
        self.assertEqual(fd.counts_line(p),
                         "open: blocker 0, should-fix 1, nit 1, severity-unknown 0; "
                         "pre-existing: blocker 0, should-fix 0, nit 0, severity-unknown 0; "
                         "flags: disputed 0, status-unknown 0; not counted: parked 0, closed 2; unparsed 0")
        self.assertEqual(p.next_id("F"), "F5")

    def test_fields(self):
        f = fd.parse_text(fixture("rounds.md")).get("F2")
        self.assertEqual((f.severity, f.origin, f.file, f.summary), ("should-fix", "pre-existing", "src/log.py:3", "typo in a log line"))

    def test_status_phrases_are_compared_whole(self):
        text = (block("F1") + "\n" + block("F2") + "\n" + block("F3") + "\n" + block("F4") + "\n" + block("F5")
                + "\n## Round 2\n\n- F1: resolved in part — x\n- F2: Fixed — y\n- F3: fixed, but z\n- F4: wontfix\n- F5: DISPUTED\n")
        self.assertEqual(self.states(text), {"F1": "unknown", "F2": "closed", "F3": "unknown", "F4": "unknown", "F5": "disputed"})
        p = fd.parse_text(text)
        self.assertIn("flags: disputed 1, status-unknown 3;", fd.counts_line(p))
        self.assertIn("open: blocker 0, should-fix 4,", fd.counts_line(p))   # F2 closed; unknown and disputed count

    def test_phrase_separators(self):
        text = block("F1") + block("F2") + block("F3") + "\n## Round 2\n\n- F1: resolved - x\n- F2: fixed – y\n- F3: not fixed\n"
        self.assertEqual(self.states(text), {"F1": "closed", "F2": "closed", "F3": "open"})

    def test_no_status_line_means_open(self):
        self.assertEqual(self.states(block("F1")), {"F1": "open"})

    def test_a_status_before_the_block_does_not_apply(self):
        text = "## Round 1\n\n- F1: resolved\n\n## Findings\n\n" + block("F1")
        self.assertEqual(self.states(text), {"F1": "open"})
        self.assertEqual(len(fd.parse_text(text).unparsed), 0)

    def test_groups_are_disjoint_and_add_up(self):
        text = (block("F1", "blocker") + block("F2", "should-fix", "pre-existing — on base") + block("F3", "nit", None)
                + block("F4", "nit") + block("F5", "should-fix") + "\n## Round 2\n\n- F4: resolved\n")
        p = fd.parse_text(text)
        fd.mark_parked(p, [{"kind": "follow_up", "finding": "F5", "severity": "should-fix"}], None)
        line = fd.counts_line(p)
        self.assertEqual(line,
                         "open: blocker 1, should-fix 0, nit 1, severity-unknown 0; "
                         "pre-existing: blocker 0, should-fix 1, nit 0, severity-unknown 0; "
                         "flags: disputed 0, status-unknown 0; not counted: parked 1, closed 1; unparsed 0")
        self.assertEqual(sum(1 for f in p.findings if fd.counted(f)) + 2, len(p.findings))   # parked + closed are the rest
        self.assertEqual(p.get("F3").origin, None)      # a missing origin counts in the open group

    def test_block_without_severity_is_severity_unknown_not_dropped(self):
        text = "### F1 — no severity\n- file: a.py:1\n- origin: introduced\n"
        p = fd.parse_text(text)
        self.assertEqual(len(p.findings), 1)
        self.assertIn("open: blocker 0, should-fix 0, nit 0, severity-unknown 1;", fd.counts_line(p))
        self.assertIn("F1 | severity-unknown |", fd.finding_line(p.findings[0]))

    def test_severity_outside_the_set_is_unknown(self):
        p = fd.parse_text(block("F1", "major"))
        self.assertIn("severity-unknown 1", fd.counts_line(p))
        sec = fd.parse_text("### S1 — x\n- severity: blocker\n- file: a:1\n", security=True)
        self.assertIn("severity-unknown 1", fd.counts_line(sec))

    def test_lines_that_look_like_findings_are_unparsed(self):
        text = (block("F1") + "\n#### F7 — x\n\n### F8: y\n\n**F9** z\n\n## Round 2\n\n- F99: resolved\n")
        p = fd.parse_text(text)
        self.assertEqual([u.kind for u in p.unparsed], ["heading", "heading", "heading", "status"])
        self.assertTrue(fd.counts_line(p).endswith("unparsed 4"))
        self.assertEqual([f.id for f in p.findings], ["F1"])
        self.assertEqual(p.next_id("F"), "F10")

    def test_a_duplicate_heading_is_unparsed_and_the_first_block_wins(self):
        p = fd.parse_text(block("F1", "nit") + "\n" + block("F1", "blocker"))
        self.assertEqual([(f.id, f.severity) for f in p.findings], [("F1", "nit")])
        self.assertEqual([u.kind for u in p.unparsed], ["dup"])

    def test_heading_dash_forms(self):
        for head in ("### F1", "### F1 - x", "### F1 – x", "### F1 — x", "### F1 -- x"):
            p = fd.parse_text(head + "\n- severity: nit\n- file: a:1\n- origin: introduced\n")
            self.assertEqual(len(p.findings), 1, head)
            self.assertEqual(len(p.unparsed), 0, head)

    def test_the_rules_comment_of_a_template_is_not_parsed(self):
        for name, fid in (("review.md", "F1"), ("security-review.md", "S1")):
            with open(os.path.join(paths.TEMPLATES_DIR, name), encoding="utf-8") as fh:
                p = fd.parse_text(fh.read(), security=name.startswith("security"))
            # the unfilled placeholder block is a finding with no valid severity (counted); its comment adds nothing
            self.assertEqual(([f.id for f in p.findings], [f.severity for f in p.findings], p.unparsed, p.status_lines),
                             ([fid], [None], [], []), name)

    def test_an_unclosed_comment_hides_nothing(self):
        p = fd.parse_text("<!-- never closed\n" + block("F1"))
        self.assertEqual([f.id for f in p.findings], ["F1"])

    def test_a_fenced_block_is_ignored(self):
        p = fd.parse_text("```\n### F1 — x\n- severity: nit\n```\n")
        self.assertEqual((p.findings, p.unparsed), ([], []))

    def test_security_counts(self):
        p = fd.parse_text(fixture("security.md"), security=True)
        self.assertEqual(fd.counts_line(p),
                         "open: critical 0, high 0, medium 0, low 1, severity-unknown 0; "
                         "flags: disputed 0, status-unknown 0; not counted: closed 1; unparsed 0")

    def test_parking_needs_id_severity_and_milestone(self):
        text = block("F1", "blocker") + block("F2", "should-fix") + block("F3", "nit")

        def parked(records, ms=None):
            p = fd.parse_text(text)
            fd.mark_parked(p, records, ms)
            return [f.id for f in p.findings if f.state == "parked"]

        rec = lambda f, sev, **kw: dict({"kind": "follow_up", "finding": f, "severity": sev}, **kw)
        self.assertEqual(parked([rec("F3", "nit")]), ["F3"])
        self.assertEqual(parked([rec("F1", "should-fix")]), [])                 # a blocker never parks
        self.assertEqual(parked([rec("F2", "nit")]), [])                        # severity differs
        self.assertEqual(parked([rec("F3", "nit", milestone=1)], ms=2), [])      # another milestone's F3
        self.assertEqual(parked([rec("F3", "nit", milestone=2)], ms=2), ["F3"])
        self.assertEqual(parked([rec("F3", "nit", milestone=1)]), [])           # a program record on a single plan
        self.assertEqual(parked([{"kind": "other", "finding": "F3", "severity": "nit"}]), [])
        sec = fd.parse_text("### S1 — x\n- severity: low\n- file: a:1\n", security=True)
        fd.mark_parked(sec, [rec("S1", "low")], None)
        self.assertEqual(sec.findings[0].state, "open")                         # security findings never park


class ReviewBase(core.Base):
    def setUp(self):
        core.Base.setUp(self)
        self.env["ORCH_TASK"] = ""
        self.d = self.init()
        with open(os.path.join(self.d, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write(PLAN)
        self.review = os.path.join(self.d, "review.md")

    def put(self, text, name="review.md", d=None):
        with open(os.path.join(d or self.d, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def get(self, name="review.md", d=None):
        with open(os.path.join(d or self.d, name), "rb") as fh:
            return fh.read().decode("utf-8")

    def stdin(self, body, *argv, env=None):
        data = body.encode("utf-8") if body is not None else b""
        with mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(data))):
            return self.run_orch(*argv, env=env or {})

    def write_review(self, body, *more):
        return self.stdin(body, "write", "review", *(list(more) + ["-t", T]))


class TestFindingsVerb(ReviewBase):
    def test_lines_open_and_counts(self):
        self.put(fixture("rounds.md"))
        out, _ = self.ok("findings", "-t", T)
        self.assertEqual(out.splitlines(), [
            "F1 | blocker | introduced | src/auth.py:12 | closed | hashing is unsalted",
            "F2 | should-fix | pre-existing | src/log.py:3 | closed | typo in a log line",
            "F3 | nit | introduced | src/names.py:9 | open | naming",
            "F4 | should-fix | introduced | tests/test_auth.py:1 | open | missing test"])
        out, _ = self.ok("findings", "--open", "-t", T)
        self.assertEqual([l.split(" | ")[0] for l in out.splitlines()], ["F3", "F4"])
        out, _ = self.ok("findings", "--counts", "-t", T)
        self.assertEqual(out.count("\n"), 1)
        self.assertTrue(out.startswith("open: blocker 0, should-fix 1, nit 1, severity-unknown 0; pre-existing:"))
        out, _ = self.ok("findings", "--open", "--counts", "-t", T)
        self.assertEqual(len(out.splitlines()), 3)

    def test_unparsed_lines_are_listed(self):
        self.put(fixture("rounds.md") + "\n#### F7 — odd\n")
        out, _ = self.ok("findings", "--open", "-t", T)
        self.assertIn("unparsed | L", out.splitlines()[-1])

    def test_a_follow_up_record_parks_the_finding(self):
        self.put(fixture("rounds.md"))
        self.ok("follow-up", "name it later", "--finding", "F3", "--severity", "nit", "-t", T)
        out, _ = self.ok("findings", "-t", T)
        self.assertIn("F3 | nit | introduced | src/names.py:9 | parked | naming", out)
        out, _ = self.ok("findings", "--counts", "-t", T)
        self.assertIn("open: blocker 0, should-fix 1, nit 0,", out)
        self.assertIn("not counted: parked 1, closed 2;", out)

    def test_follow_up_for_a_wrong_severity_does_not_park(self):
        self.put(fixture("rounds.md"))
        self.ok("follow-up", "x", "--finding", "F3", "--severity", "should-fix", "-t", T)
        out, _ = self.ok("findings", "--counts", "-t", T)
        self.assertIn("nit 1,", out)
        self.assertIn("parked 0", out)

    def test_unknown_status_is_counted(self):
        self.put(fixture("rounds.md") + "\n## Round 3\n\n- F3: wontfix\n")
        out, _ = self.ok("findings", "--counts", "-t", T)
        self.assertIn("flags: disputed 0, status-unknown 1;", out)
        self.assertIn("nit 1,", out)

    def test_program_milestone_dir_and_milestone_matching(self):
        self.ok("set", "shape=roadmap", "milestone=M1 — building", "-t", T)
        self.ok("follow-up", "later", "--finding", "F3", "--severity", "nit", "-t", T)   # M1's record, by the real writer
        self.ok("set", "milestone=M2 — reviewing", "-t", T)
        m2 = os.path.join(self.d, "m02-sessions")
        os.makedirs(m2)
        self.put(PLAN, "plan.md", m2)
        self.put(fixture("rounds.md"), d=m2)
        out, _ = self.ok("findings", "-t", T)
        self.assertIn("F3 | nit | introduced | src/names.py:9 | open | naming", out)       # M1's F3 does not park M2's
        self.ok("follow-up", "later", "--finding", "F3", "--severity", "nit", "-t", T)   # M2's record
        out, _ = self.ok("findings", "-t", T)
        self.assertIn("F3 | nit | introduced | src/names.py:9 | parked | naming", out)

    def test_security_file_and_missing_file(self):
        self.put(fixture("security.md"), "security-review.md")
        out, _ = self.ok("findings", "--security", "--counts", "-t", T)
        self.assertEqual(out, "open: critical 0, high 0, medium 0, low 1, severity-unknown 0; flags: disputed 0, status-unknown 0; "
                              "not counted: closed 1; unparsed 0\n")
        self.assertIn("no review.md", self.refuse(1, "findings", "-t", T))

    def test_read_only(self):
        self.put(fixture("rounds.md"))
        before = self.get()
        self.ok("findings", "--counts", "-t", T)
        self.assertEqual(self.get(), before)


class TestWriteReview(ReviewBase):
    def test_creates_a_missing_file_with_the_title(self):
        rc, out, err = self.write_review(block("F1", "blocker", extra="- what: x\n"))
        self.assertEqual((rc, out), (0, "review.md: wrote F1; next id F2\n"), err)
        self.assertEqual(self.get(), "# Code review: add-login\n\n" + block("F1", "blocker", extra="- what: x\n"))

    def test_appends_after_a_blank_line_in_one_write(self):
        self.put(fixture("rounds.md"))
        before = self.get()
        text = "## Round 3\n\n- F3: resolved — renamed\n\n" + block("F5", "nit")
        rc, out, err = self.write_review(text)
        self.assertEqual((rc, out), (0, "review.md: wrote F5 (+1 status line); next id F6\n"), err)
        after = self.get()
        self.assertTrue(after.startswith(before))
        self.assertEqual(after[len(before):], "\n" + text)
        out, _ = self.ok("findings", "--counts", "-t", T)
        self.assertIn("nit 1,", out)               # F3 closed, F5 open

    def test_a_file_without_a_final_newline_gets_one(self):
        self.put("# Code review: x\n\n" + block("F1").rstrip("\n"))
        self.assertEqual(self.write_review(block("F2"))[0], 0)
        self.assertEqual(len(fd.parse_text(self.get()).findings), 2)

    def test_several_findings_and_statuses_in_the_summary_line(self):
        self.put(fixture("rounds.md"))
        text = "## Round 3\n\n- F3: resolved\n- F4: disputed — no\n\n" + block("F5") + "\n" + block("F6")
        rc, out, err = self.write_review(text)
        self.assertEqual(out, "review.md: wrote F5, F6 (+2 status lines); next id F7\n", err)

    def test_a_duplicate_id_is_refused_and_nothing_is_written(self):
        self.put(fixture("rounds.md"))
        before = self.get()
        rc, out, err = self.write_review(block("F2"))
        self.assertEqual(rc, 1)
        self.assertIn("F2: already in the file", err)
        self.assertTrue(err.rstrip().endswith("next id: F5"), err)
        self.assertEqual(self.get(), before)

    def test_an_id_repeated_in_the_text_is_refused(self):
        rc, _, err = self.write_review(block("F1") + "\n" + block("F1"))
        self.assertEqual(rc, 1)
        self.assertIn("F1: repeated in the text", err)
        self.assertFalse(os.path.exists(self.review))

    def test_bad_blocks_are_refused_together(self):
        text = block("F1", "major") + "\n" + block("F2", file=None) + "\n" + block("F3", origin=None)
        rc, _, err = self.write_review(text)
        self.assertEqual(rc, 1)
        for want in ("F1: `- severity:`", "F2: `- file:`", "F3: `- origin:`", "next id: F1"):
            self.assertIn(want, err)
        self.assertFalse(os.path.exists(self.review))

    def test_an_s_id_in_review_md_and_an_f_id_in_the_security_file_are_refused(self):
        self.assertIn("S1: this file takes F ids", self.write_review(block("S1", "high", None))[2])
        self.assertIn("F1: this file takes S ids", self.write_review(block("F1", "high", None), "--security")[2])
        self.assertFalse(os.path.exists(self.review))

    def test_a_status_line_for_an_unknown_id_is_refused(self):
        self.put(fixture("rounds.md"))
        before = self.get()
        rc, _, err = self.write_review("## Round 3\n\n- F99: resolved\n")
        self.assertEqual(rc, 1)
        self.assertIn("F99: a status line for an id that exists nowhere", err)
        self.assertEqual(self.get(), before)

    def test_a_status_phrase_outside_the_template_is_refused(self):
        self.put(fixture("rounds.md"))
        rc, _, err = self.write_review("## Round 3\n\n- F2: resolved in part\n")
        self.assertEqual(rc, 1)
        self.assertIn("is not one of: resolved, still open, disputed, fixed, not fixed", err)

    def test_a_status_line_outside_a_round_heading_is_refused(self):
        self.put(fixture("rounds.md"))
        rc, _, err = self.write_review("- F2: resolved\n")
        self.assertEqual(rc, 1)
        self.assertIn("## Round N", err)

    def test_a_malformed_heading_is_refused(self):
        rc, _, err = self.write_review("### F1: colon\n- severity: nit\n")
        self.assertEqual(rc, 1)
        self.assertIn("is not a finding heading", err)

    def test_empty_stdin_and_text_without_findings(self):
        self.assertEqual(self.write_review("")[0], 1)
        self.assertEqual(self.write_review("## Round 2\n\nprose only\n")[0], 1)

    def test_security_file(self):
        rc, out, err = self.write_review("### S1 — x\n- severity: high\n- file: a.py:2\n- resolution:\n", "--security")
        self.assertEqual((rc, out), (0, "security-review.md: wrote S1; next id S2\n"), err)
        self.assertTrue(self.get("security-review.md").startswith("# Security review: add-login\n\n### S1"))
        self.assertEqual(self.write_review("### S2 — y\n- severity: blocker\n- file: a:1\n", "--security")[0], 1)

    def test_a_given_title_is_not_doubled(self):
        text = "# Code review: add-login\n\n" + block("F1")
        self.assertEqual(self.write_review(text)[0], 0)
        self.assertEqual(self.get(), text)

    def test_program_writes_the_milestone_dir(self):
        self.ok("set", "shape=roadmap", "milestone=M2 — reviewing", "-t", T)
        m2 = os.path.join(self.d, "m02-sessions")
        os.makedirs(m2)
        self.put(PLAN, "plan.md", m2)
        self.assertEqual(self.write_review(block("F1"))[0], 0)
        self.assertTrue(os.path.exists(os.path.join(m2, "review.md")))
        self.assertFalse(os.path.exists(self.review))

    def test_the_task_is_inferred_and_tagged(self):
        self.env["CLAUDE_CODE_SESSION_ID"] = S
        self.ok("set", "driver_session=" + S, "--add", "-t", T)
        rc, out, err = self.stdin(block("F1"), "write", "review")
        self.assertEqual((rc, out), (0, "review.md: wrote F1; next id F2 [myapp/add-login]\n"), err)

    def test_refused_without_a_task(self):
        rc, _, err = self.stdin(block("F1"), "write", "review")
        self.assertEqual(rc, 1)
        self.assertIn("pass -t", err)


class TestFindingAdd(ReviewBase):
    def add(self, *more, body=""):
        return self.stdin(body, "finding", "add", *(list(more) + ["-t", T]))

    ARGS = ("--severity", "should-fix", "--origin", "introduced", "--file", "src/a.py:3", "--summary", "a thing")

    def test_ids_continue_and_the_block_has_the_template_shape(self):
        self.put(fixture("rounds.md"))
        rc, out, err = self.add(*self.ARGS)
        self.assertEqual((rc, out), (0, "F5\n"), err)
        rc, out, err = self.add(*self.ARGS)
        self.assertEqual(out, "F6\n")
        text = self.get()
        self.assertIn("\n### F5 — a thing\n- severity: should-fix\n- origin: introduced\n- file: src/a.py:3\n- resolution:\n\n### F6", text)
        self.assertEqual([f.id for f in fd.parse_text(text).findings][-2:], ["F5", "F6"])

    def test_creates_the_file_and_takes_details_from_stdin(self):
        rc, out, err = self.add(*self.ARGS, body="- what: it is wrong\n- suggested fix: change it\n")
        self.assertEqual((rc, out), (0, "F1\n"), err)
        self.assertEqual(self.get(), "# Code review: add-login\n\n### F1 — a thing\n- severity: should-fix\n- origin: introduced\n"
                                     "- file: src/a.py:3\n- what: it is wrong\n- suggested fix: change it\n- resolution:\n")

    def test_validation_is_the_write_validation(self):
        for bad, key in ((("--severity", "major"), "`- severity:`"), (("--origin", "maybe"), "`- origin:`")):
            args = dict(zip(self.ARGS[::2], self.ARGS[1::2]))
            args[bad[0]] = bad[1]
            argv = [x for kv in args.items() for x in kv]
            rc, _, err = self.add(*argv)
            self.assertEqual(rc, 1)
            self.assertIn(key, err)
        self.assertFalse(os.path.exists(self.review))
        self.assertEqual(self.add("--severity", "nit", "--file", "a:1", "--summary", "x")[0], 1)    # no origin
        self.assertEqual(self.add(*self.ARGS, body="### F9 — sneaky\n")[0], 1)                      # no second heading from stdin
        self.assertFalse(os.path.exists(self.review))

    def test_security(self):
        rc, out, err = self.add("--security", "--severity", "high", "--file", "a.py:1", "--summary", "leak")
        self.assertEqual((rc, out), (0, "S1\n"), err)
        self.assertIn("### S1 — leak\n- severity: high\n- file: a.py:1\n- resolution:\n", self.get("security-review.md"))
        self.assertEqual(self.add("--security", "--origin", "introduced", "--severity", "high", "--file", "a:1", "--summary", "x")[0], 1)

    def test_program_writes_the_milestone_dir(self):
        self.ok("set", "shape=roadmap", "milestone=M2 — reviewing", "-t", T)
        m2 = os.path.join(self.d, "m02-sessions")
        os.makedirs(m2)
        self.put(PLAN, "plan.md", m2)
        self.assertEqual(self.add(*self.ARGS)[1], "F1\n")
        self.assertTrue(os.path.exists(os.path.join(m2, "review.md")))


class TestFindingResolve(ReviewBase):
    def resolve(self, fid, text=None, body=None, *more):
        argv = ["finding", "resolve", fid] + (["--text", text] if text is not None else []) + list(more) + ["-t", T]
        return self.stdin(body, *argv)

    def test_sets_then_appends(self):
        self.put(fixture("rounds.md"))
        rc, out, err = self.resolve("F4", "added the test")
        self.assertEqual((rc, out), (0, "F4: resolution set\n"), err)
        text = self.get()
        self.assertIn("- file: tests/test_auth.py:1\n- what: no test for the salt.\n- resolution: added the test\n", text)
        rc, out, err = self.resolve("F4", "and a second one")
        self.assertEqual(out, "F4: resolution appended\n", err)
        self.assertIn("- resolution: added the test | 2026-03-01T10:00Z and a second one\n", self.get())
        self.assertEqual(self.get().count("### F"), 4)           # nothing else moved

    def test_a_template_placeholder_is_replaced(self):
        self.put("### F1 — x\n- severity: nit\n- origin: introduced\n- file: a:1\n- resolution: <left empty — the implementer fills this in>\n")
        self.resolve("F1", "done")
        self.assertIn("- resolution: done\n", self.get())
        self.assertNotIn("<left empty", self.get())

    def test_text_from_stdin_and_a_missing_resolution_line(self):
        self.put("### F1 — x\n- severity: nit\n- origin: introduced\n- file: a:1\n\n## Round 2\n\n- F1: still open\n")
        rc, out, err = self.resolve("F1", None, "two\nlines")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.get().split("\n")[:6],
                         ["### F1 — x", "- severity: nit", "- origin: introduced", "- file: a:1", "- resolution: two lines", ""])

    def test_the_prefix_picks_the_file(self):
        self.put(fixture("security.md"), "security-review.md")
        self.resolve("S2", "hidden")
        self.assertIn("- file: src/views.py:90\n- resolution: hidden\n", self.get("security-review.md"))
        self.assertFalse(os.path.exists(self.review))

    def test_unknown_ids_and_bad_input(self):
        self.put(fixture("rounds.md"))
        before = self.get()
        self.assertIn("F9 not found", self.resolve("F9", "x")[2])
        self.assertEqual(self.resolve("X1", "x")[0], 1)
        self.assertEqual(self.resolve("F1", "   ")[0], 1)
        self.assertEqual(self.resolve("S1", "x")[0], 1)       # no security-review.md
        self.assertEqual(self.get(), before)

    def test_program_milestone_dir(self):
        self.ok("set", "shape=roadmap", "milestone=M2 — reviewing", "-t", T)
        m2 = os.path.join(self.d, "m02-sessions")
        os.makedirs(m2)
        self.put(PLAN, "plan.md", m2)
        self.put(fixture("rounds.md"), d=m2)
        self.assertEqual(self.resolve("F3", "ok")[0], 0)
        self.assertIn("- resolution: ok\n", self.get(d=m2))


if __name__ == "__main__":
    unittest.main()
