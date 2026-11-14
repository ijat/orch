"""scripts/build-agents.py --check: what it refuses (stale or missing variant, orphan agent file, malformed `variants:`)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(REPO, "scripts", "build-agents.py")
ROLES = os.path.join(REPO, "plugin", "skills", "orchestrate", "roles")
AGENTS = os.path.join(REPO, "plugin", "agents")
VARIANT = "security-reviewer-mythos.md"


class TestRenderChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-build-agents-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.roles = os.path.join(self.tmp, "roles")
        self.agents = os.path.join(self.tmp, "agents")
        shutil.copytree(ROLES, self.roles)
        shutil.copytree(AGENTS, self.agents)

    def check(self):
        p = subprocess.run([sys.executable, SCRIPT, self.roles, self.agents, "--check"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True)
        return p.returncode, p.stdout

    def test_the_repo_as_committed_passes(self):
        rc, out = self.check()
        self.assertEqual(rc, 0, out)
        with open(os.path.join(AGENTS, VARIANT), encoding="utf-8") as fh:
            head = fh.read().split("\n---", 1)[0]
        self.assertIn("name: security-reviewer-mythos", head)
        self.assertIn("model: claude-mythos-5-2", head)
        self.assertIn("effort: high", head)
        self.assertNotIn("variants:", head)

    def test_a_stale_variant_fails(self):
        with open(os.path.join(self.agents, VARIANT), "a", encoding="utf-8") as fh:
            fh.write("extra\n")
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFFERS " + VARIANT, out)

    def test_a_missing_variant_fails(self):
        os.remove(os.path.join(self.agents, VARIANT))
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFFERS " + VARIANT, out)

    def test_a_stray_agent_file_fails(self):
        shutil.copy(os.path.join(self.agents, "explorer.md"), os.path.join(self.agents, "ghost.md"))
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("ORPHAN ghost.md", out)

    def test_a_malformed_variants_line_is_a_build_error_naming_the_role(self):
        path = os.path.join(self.roles, "security-reviewer.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("mythos=claude-mythos-5-2", "mythos=/high", "=m/high", "mythos=m/high,", "a=m/high, a=n/low", "Mythos=m/high"):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace("variants: mythos=claude-mythos-5-2/high", "variants: " + bad))
            rc, out = self.check()
            self.assertEqual(rc, 1, (bad, out))
            self.assertIn("security-reviewer: malformed variants", out, bad)

    def test_a_role_without_variants_renders_as_before(self):
        path = os.path.join(self.roles, "security-reviewer.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("variants: mythos=claude-mythos-5-2/high\n", ""))
        rc, out = self.check()
        self.assertEqual(rc, 1, out)                       # the variant file is now an orphan, the role itself still matches
        self.assertIn("ORPHAN " + VARIANT, out)
        self.assertNotIn("DIFFERS security-reviewer.md", out)


if __name__ == "__main__":
    unittest.main()
