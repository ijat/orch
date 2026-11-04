"""The retired continue gate: old sessions may still call it."""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from orchlib import cont, plan as pl  # noqa: E402


class TestRetiredGate(unittest.TestCase):
    def test_always_splits_and_succeeds(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cont.cmd_continue_gate(None, []), 0)
        self.assertTrue(out.getvalue().startswith("continue-gate: split "))

    def test_old_plans_with_a_continue_line_still_parse(self):
        p = pl.parse_plan("# Plan\n\n## Summary\n\n- continue: steps 1\n\n## Steps\n\n### Step 1 — a\n\n- do: a\n")
        self.assertEqual(p.ids(), ["1"])


if __name__ == "__main__":
    unittest.main()
