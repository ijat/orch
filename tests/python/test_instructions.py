"""The managed CLAUDE.md block: conditional lines (orch:if / orch:endif) rendered from the agent_docs setting."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

from orchlib import cli, instructions, paths  # noqa: E402


class TestRender(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-instr-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.orch = os.path.join(self.tmp, "orch")
        os.makedirs(self.orch)
        self.file = os.path.join(self.tmp, "CLAUDE.md")
        with open(self.file, "w") as fh:
            fh.write("# Mine\n")
        with open(instructions.TEMPLATE, encoding="utf-8") as fh:
            self.raw = fh.read().rstrip("\n").split("\n")

    def run_orch(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"ORCH_DIR": self.orch, "ORCH_DATA": ""}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def install(self, *extra):
        return self.run_orch("install-instructions", "--file", self.file, *extra)

    def block(self):
        with open(self.file) as fh:
            lines = fh.read().split("\n")
        start = next(i for i, l in enumerate(lines) if l.startswith(instructions.BEGIN))
        end = next(i for i, l in enumerate(lines) if l.startswith("<!-- orch:end -->"))
        return lines[start:end + 1]

    def test_off_is_the_block_plus_the_task_bullet_only(self):
        self.assertEqual(self.install()[0], 0)
        if_at = self.raw.index("<!-- orch:if agent_docs -->")
        self.assertEqual(self.block(), self.raw[:if_at] + [self.raw[-1]])
        text = "\n".join(self.block())
        self.assertIn("run `orch init <repo> <slug> ...`", text)
        self.assertNotIn("Agent docs stay out of repos", text)
        self.assertNotIn("orch:if", text)
        self.assertNotIn("orch:endif", text)

    def test_on_adds_the_section_and_no_marker_line_reaches_the_file(self):
        self.assertEqual(self.run_orch("setting", "set", "agent_docs", "on")[0], 0)
        self.assertEqual(self.install()[0], 0)
        expected = [l for l in self.raw if l not in ("<!-- orch:if agent_docs -->", "<!-- orch:endif -->")]
        self.assertEqual(self.block(), expected)
        with open(self.file) as fh:
            whole = fh.read()
        self.assertNotIn("orch:if", whole)
        self.assertNotIn("orch:endif", whole)
        self.assertIn("## Agent docs stay out of repos", whole)
        self.assertIn("orch docs adopt <path>", whole)
        self.assertIn("orch docs commit", whole)
        self.assertLessEqual(sum(1 for l in self.block()[self.block().index("## Agent docs stay out of repos"):] if l.startswith("- ")), 6)

    def test_rerun_is_idempotent_and_the_setting_moves_the_block_both_ways(self):
        self.install()
        self.assertIn("nothing to do", self.install()[1])
        self.run_orch("setting", "set", "agent_docs", "on")
        rc, out, err = self.install("--dry-run")
        self.assertIn("+## Agent docs stay out of repos", out)
        self.assertEqual(self.install()[0], 0)
        self.assertIn("nothing to do", self.install()[1])
        self.run_orch("setting", "set", "agent_docs", "off")
        self.assertEqual(self.install()[0], 0)
        self.assertNotIn("Agent docs stay out of repos", "\n".join(self.block()))
        with open(self.file) as fh:
            self.assertTrue(fh.read().startswith("# Mine\n"))

    def test_unbalanced_or_unknown_markers_refuse_and_write_nothing(self):
        for bad in (["a", "<!-- orch:if agent_docs -->", "b"],
                    ["a", "<!-- orch:endif -->"],
                    ["<!-- orch:if agent_docs -->", "<!-- orch:if agent_docs -->", "<!-- orch:endif -->", "<!-- orch:endif -->"],
                    ["<!-- orch:if nonesuch -->", "<!-- orch:endif -->"]):
            with self.assertRaises(paths.OrchError) as cm:
                instructions.render_block(bad)
            self.assertEqual(cm.exception.code, 2, bad)
        broken = os.path.join(self.tmp, "broken.md")
        with open(broken, "w") as fh:
            fh.write("<!-- orch:begin x -->\n<!-- orch:if agent_docs -->\nline\n<!-- orch:end -->\n")
        with mock.patch.object(instructions, "TEMPLATE", broken):
            rc, out, err = self.install()
        self.assertEqual(rc, 2)
        self.assertIn("without orch:endif", err)
        with open(self.file) as fh:
            self.assertEqual(fh.read(), "# Mine\n")
        self.assertEqual([n for n in os.listdir(self.tmp) if ".orch-backup-" in n], [])

    def test_the_raw_template_is_one_well_formed_block(self):
        self.assertEqual(instructions.block_span(self.raw), (0, len(self.raw)))


if __name__ == "__main__":
    unittest.main()
