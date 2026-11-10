"""The data root and every path derived from it, and the agent-name mapping (C3/D16). Neutral fixtures only."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin")
sys.path.insert(0, BIN)

from orchlib import hook, instructions, paths, usage  # noqa: E402

ROLE_COST = os.path.join(BIN, "role-cost.py")


def load_role_cost():
    spec = importlib.util.spec_from_file_location("role_cost", ROLE_COST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TmpHome(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-paths-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def env(self, **kw):
        """os.environ with HOME=<scratch> and the orch variables unset, then kw."""
        e = {k: v for k, v in os.environ.items() if k not in ("ORCH_DATA", "ORCH_DIR", "ORCH_USAGE_STATE")}
        e["HOME"] = self.home
        e.update(kw)
        return mock.patch.dict(os.environ, e, clear=True)


class DataRoot(TmpHome):
    def test_default_root_and_derived_paths(self):
        with self.env():
            data = os.path.join(self.home, ".claude", "orch-data")
            self.assertEqual(paths.data_root(), data)
            self.assertEqual(paths.orch_root(), os.path.join(data, "tasks"))
            self.assertEqual(paths.logs_dir(), os.path.join(data, "logs"))
            self.assertEqual(paths.cache_dir(), os.path.join(data, "cache"))
            self.assertEqual(paths.data_bin(), os.path.join(data, "bin"))
            self.assertEqual(paths.pointer_path(), os.path.join(data, "plugin"))
            self.assertEqual(paths.private_dir(), os.path.join(data, "private"))
            self.assertEqual(paths.backups_dir(), os.path.join(data, "backups"))
            self.assertEqual(paths.denylist_path(), os.path.join(data, "private", "orch-export-denylist.txt"))

    def test_orch_data_moves_everything(self):
        alt = os.path.join(self.tmp, "alt")
        with self.env(ORCH_DATA=alt):
            self.assertEqual(paths.orch_root(), os.path.join(alt, "tasks"))
            for fn, leaf in ((paths.logs_dir, "logs"), (paths.cache_dir, "cache"), (paths.data_bin, "bin"),
                             (paths.pointer_path, "plugin"), (paths.private_dir, "private")):
                self.assertEqual(fn(), os.path.join(alt, leaf))

    def test_orch_dir_wins_for_the_tasks_root_only(self):
        alt, tasks = os.path.join(self.tmp, "alt"), os.path.join(self.tmp, "tasks")
        with self.env(ORCH_DATA=alt, ORCH_DIR=tasks):
            self.assertEqual(paths.orch_root(), tasks)
            self.assertEqual(paths.logs_dir(), os.path.join(alt, "logs"))
        with self.env(ORCH_DIR=tasks):
            self.assertEqual(paths.orch_root(), tasks)
            self.assertEqual(paths.cache_dir(), os.path.join(self.home, ".claude", "orch-data", "cache"))

    def test_usage_state_is_in_the_cache(self):
        with self.env():
            self.assertEqual(usage.state_dir(), os.path.join(self.home, ".claude", "orch-data", "cache", "usage"))
            self.assertEqual(usage.state_dir("/elsewhere/.claude"),
                             os.path.join(self.home, ".claude", "orch-data", "cache", "usage"))
        with self.env(ORCH_USAGE_STATE=os.path.join(self.tmp, "st")):
            self.assertEqual(usage.state_dir(), os.path.join(self.tmp, "st"))

    def test_statusline_files_stay_where_the_statusline_writes_them(self):
        with self.env():
            self.assertEqual(usage.home_dir(), os.path.join(self.home, ".claude"))

    def test_index_binary_default_is_in_the_data_bin(self):
        binary = os.path.join(self.home, ".claude", "orch-data", "bin", "orch-index")
        os.makedirs(os.path.dirname(binary))
        with open(binary, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(binary, 0o755)
        with self.env(PATH=os.path.join(self.tmp, "empty")):
            os.environ.pop("ORCH_INDEX_BIN", None)
            self.assertEqual(hook._binary(), binary)

    def test_orch_index_sh_writes_under_the_data_root(self):
        tasks = os.path.join(self.home, ".claude", "orch-data", "tasks")
        os.makedirs(tasks)
        with self.env():
            r = subprocess.run([os.path.join(BIN, "orch-index.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(tasks, "ACTIVE.md")))
        alt = os.path.join(self.tmp, "alt")
        os.makedirs(os.path.join(alt, "tasks"))
        with self.env(ORCH_DATA=alt):
            r = subprocess.run([os.path.join(BIN, "orch-index.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(alt, "tasks", "ACTIVE.md")))

    def test_installer_default_dest_is_the_data_bin(self):
        with self.env(ORCH_INDEX_AUTOINSTALL="1"):
            r = subprocess.run([os.path.join(BIN, "orch-install-code-index.sh"), "--print-target"],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(os.path.join(".claude", "bin"), r.stdout)
        with open(os.path.join(BIN, "orch-install-code-index.sh")) as fh:
            self.assertIn('dest="${ORCH_DATA:-$HOME/.claude/orch-data}/bin"', fh.read())


class AgentNames(unittest.TestCase):
    CASES = (("orch:planner", "orch-planner"), ("orch-planner", "orch-planner"),
             ("foo:planner", "foo:planner"), ("orch:orch-planner", "orch-orch-planner"),
             ("orch:security-reviewer", "orch-security-reviewer"), ("general-purpose", "general-purpose"),
             ("", ""), (None, None), (3, 3))

    def test_agent_key(self):
        for given, want in self.CASES:
            with self.subTest(given=given):
                self.assertEqual(paths.agent_key(given), want)

    def test_role_cost_copy_matches_the_helper(self):
        rc = load_role_cost()
        for given, _ in self.CASES:
            with self.subTest(given=given):
                self.assertEqual(rc.agent_key(given), paths.agent_key(given))

    def test_no_role_for_other_prefixes(self):
        rc = load_role_cost()
        roles = ("orch-planner", "orch-implementer")
        for typ in ("foo:planner", "orch:orch-planner"):
            self.assertNotIn(rc.role_of(rc.agent_key(typ)), roles)
        self.assertEqual(rc.role_of(rc.agent_key("orch:planner")), "orch-planner")

    def test_instructions_reads_either_spelling(self):
        self.assertEqual(instructions.agent_text("use `orch:explorer` and `orch:executor`"),
                         "use `orch-explorer` and `orch-executor`")
        lines = ["- **Agents** — `orch:explorer` and `orch:executor` are general.\n"]
        self.assertEqual(instructions.legacy_spans(lines, None), [(0, 1)])


class RoleCostLabels(unittest.TestCase):
    """role-cost labels are identical for a transcript set in either spelling."""

    def write_set(self, root, spelling):
        types = ("planner", "implementer", "code-reviewer", "explorer")
        for i, role in enumerate(types):
            d = os.path.join(root, "proj", "sess%d" % i, "subagents")
            os.makedirs(d)
            name = ("orch:%s" if spelling == "new" else "orch-%s") % role
            with open(os.path.join(d, "agent-a%d.meta.json" % i), "w") as fh:
                json.dump({"agentType": name}, fh)
            with open(os.path.join(d, "agent-a%d.jsonl" % i), "w") as fh:
                for n in range(2):
                    fh.write(json.dumps({"type": "assistant", "requestId": "r%d-%d" % (i, n), "effort": "high",
                                         "timestamp": "2026-11-01T00:00:00Z",
                                         "message": {"model": "claude-opus-5-5",
                                                     "usage": {"input_tokens": 100 * (i + 1), "output_tokens": 5}}})
                             + "\n")

    def test_labels_identical(self):
        tmp = tempfile.mkdtemp(prefix="orch-rolecost-")
        self.addCleanup(shutil.rmtree, tmp, True)
        out = {}
        for spelling in ("old", "new"):
            root = os.path.join(tmp, spelling)
            self.write_set(root, spelling)
            for by in ("tier", "type"):
                r = subprocess.run([sys.executable, ROLE_COST, "--root", root, "--by", by],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                out[spelling, by] = r.stdout
        for by in ("tier", "type"):
            self.assertEqual(out["old", by], out["new", by])
        self.assertIn("orch-planner opus-5-5/high", out["new", "tier"])
        self.assertIn("orch-code-reviewer", out["new", "type"])
        self.assertNotIn("orch:", out["new", "tier"] + out["new", "type"])


@unittest.skipUnless(shutil.which("jq"), "jq not installed")
class StatsNames(TmpHome):
    def test_orch_stats_maps_plugin_names(self):
        root = os.path.join(self.tmp, "tasks")
        d = os.path.join(root, "myapp", "t1")
        os.makedirs(d)
        with open(os.path.join(d, "journal.jsonl"), "w") as fh:
            for agent in ("orch:planner", "orch-planner"):
                fh.write(json.dumps({"ts": "2026-11-01T00:00Z", "kind": "return", "agent": agent, "tokens": 10,
                                     "role": "planner"}) + "\n")
        with self.env(ORCH_DIR=root):
            r = subprocess.run([os.path.join(BIN, "orch-stats.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("  orch-planner: n=2", r.stdout)
        self.assertNotIn("orch:planner", r.stdout)


if __name__ == "__main__":
    unittest.main()
