"""orch append notes: one notes.md entry from stdin or --text, path-free. Neutral fixtures only."""
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

import test_core as core  # noqa: E402
from orchlib import flow, paths  # noqa: E402

S = "bd0dbcca-0000-4000-8000-000000000002"
HEAD = "## implementer — 2026-03-01T10:00Z — "


class NotesBase(core.Base):
    def setUp(self):
        core.Base.setUp(self)
        self.d = self.tdir()
        self.notes = os.path.join(self.d, "notes.md")

    def append(self, body, *argv, env=None):
        """Run `orch append notes …` with `body` on stdin (None: no stdin is read)."""
        data = body.encode("utf-8") if body is not None else b""
        with mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(data))):
            return self.run_orch("append", "notes", *argv, env=env or {})

    def read_notes(self):
        with open(self.notes, "rb") as fh:
            return fh.read()


class TestAppendNotes(NotesBase):
    def test_first_append_creates_the_file_from_the_template(self):
        self.init()
        rc, out, err = self.append("Did the thing.\nMore.", "--as", "implementer", "-t", "myapp/add-login")
        self.assertEqual((rc, out), (0, "notes.md: +5 lines (implementer — Did the thing.)\n"), err)
        with open(os.path.join(paths.TEMPLATES_DIR, "notes.md"), encoding="utf-8") as fh:
            template = fh.read()
        want = template.replace("<task-slug>", "add-login").rstrip("\n") + "\n" + \
            "\n" + HEAD + "Did the thing.\n\nDid the thing.\nMore.\n"
        self.assertEqual(self.read_notes().decode("utf-8"), want)

    def test_second_append_leaves_the_first_bytes_identical(self):
        self.init()
        self.append("first", "--as", "implementer", "-t", "myapp/add-login")
        before = self.read_notes()
        rc, out, err = self.append("second", "--as", "reviewer", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, err)
        after = self.read_notes()
        self.assertTrue(after.startswith(before))
        self.assertIn("\n## reviewer — 2026-03-01T10:00Z — second\n\nsecond\n".encode("utf-8"), after)

    def test_contract_check_block_round_trips_through_contract_blocks(self):
        self.init()
        body = "contract-check: step 3\n- [x] item — evidence here\n- [ ] other — why not"
        rc, out, err = self.append(body, "--as", "implementer", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, err)
        blocks, seen = flow.contract_blocks(self.notes, {"3"})
        self.assertEqual(blocks, [body])
        self.assertEqual(seen, {"3"})

    def test_text_option_and_trailing_newlines_are_stripped(self):
        self.init()
        rc, out, err = self.append(None, "--as", "implementer", "--text", "one line\n\n", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, err)
        self.assertIn(HEAD + "one line\n\none line\n", self.read_notes().decode("utf-8"))

    def test_explicit_topic_wins_and_is_one_line(self):
        self.init()
        rc, out, err = self.append("body", "--as", "implementer", "--topic", "a\nb", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, err)
        self.assertIn("## implementer — 2026-03-01T10:00Z — a b\n", self.read_notes().decode("utf-8"))

    def test_default_topic_is_the_first_line_clipped_to_60(self):
        self.init()
        long_line = "x" * 80
        rc, out, err = self.append("\n\n" + long_line + "\nmore", "--as", "implementer", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, err)
        heading = [l for l in self.read_notes().decode("utf-8").split("\n") if l.startswith("## implementer")][0]
        self.assertEqual(heading, HEAD + "x" * 60)

    def test_empty_body_refused_and_nothing_written(self):
        self.init()
        for body in ("", "  \n\n"):
            rc, out, err = self.append(body, "--as", "implementer", "-t", "myapp/add-login")
            self.assertEqual((rc, out), (1, ""))
            self.assertIn("nothing to append", err)
        self.assertFalse(os.path.exists(self.notes))

    def test_body_over_64_kib_refused_and_names_artifacts(self):
        self.init()
        rc, out, err = self.append("x" * (70 * 1024), "--as", "implementer", "-t", "myapp/add-login")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("artifacts", err)
        self.assertFalse(os.path.exists(self.notes))

    def test_body_at_the_limit_is_accepted(self):
        self.init()
        rc, out, err = self.append("x" * 65536, "--as", "implementer", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, err)

    def test_bad_role_refused_and_nothing_written(self):
        self.init()
        rc, out, err = self.append("hi", "--as", "bad role!", "-t", "myapp/add-login")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("bad role name", err)
        self.assertFalse(os.path.exists(self.notes))

    def test_missing_as_is_a_usage_error(self):
        self.init()
        rc, out, err = self.append("hi", "-t", "myapp/add-login")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("--as", err)

    def test_existing_file_without_a_final_newline_gets_one(self):
        self.init()
        with open(self.notes, "w", encoding="utf-8") as fh:
            fh.write("# Notes: add-login\nlast line, no newline")
        rc, out, err = self.append("more", "--as", "implementer", "-t", "myapp/add-login")
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.read_notes().decode("utf-8").startswith("# Notes: add-login\nlast line, no newline\n\n## "))

    def test_no_t_with_a_driver_session_match_works_and_is_tagged(self):
        self.init(env={"CLAUDE_CODE_SESSION_ID": S})
        rc, out, err = self.append("hi", "--as", "implementer", env={"CLAUDE_CODE_SESSION_ID": S})
        self.assertEqual((rc, out), (0, "notes.md: +4 lines (implementer — hi) [myapp/add-login]\n"), err)

    def test_no_t_without_a_match_is_refused(self):
        self.init(env={"CLAUDE_CODE_SESSION_ID": S})
        rc, out, err = self.append("hi", "--as", "implementer", env={"CLAUDE_CODE_SESSION_ID": ""})
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("pass -t", err)
        self.assertFalse(os.path.exists(self.notes))

    def test_heading_carries_orch_now(self):
        self.init()
        rc, out, err = self.append("hi", "--as", "implementer", "-t", "myapp/add-login",
                                   env={"ORCH_NOW": "2026-07-04T08:30Z"})
        self.assertEqual(rc, 0, err)
        self.assertIn("## implementer — 2026-07-04T08:30Z — hi\n", self.read_notes().decode("utf-8"))

    def test_writes_need_orch_dir(self):
        self.init()
        before = core.tree_hash(self.d)
        rc, out, err = self.append("hi", "--as", "implementer", "-t", "myapp/add-login", env={"ORCH_DIR": ""})
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("development copy: set ORCH_DIR", err)
        self.assertEqual(core.tree_hash(self.d), before)


if __name__ == "__main__":
    unittest.main()
