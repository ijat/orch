"""orch current and orch install-instructions. Neutral fixtures only."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN)

from orchlib import current, instructions  # noqa: E402

LAUNCHER = os.path.join(BIN, "orch")


def run(args, cwd, orch_dir):
    env = dict(os.environ, ORCH_DIR=orch_dir)
    return subprocess.run([sys.executable, LAUNCHER] + args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", cwd] + list(args),
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.orch = os.path.join(self.tmp, "orch")
        os.makedirs(self.orch)


class Current(Base):
    def setUp(self):
        Base.setUp(self)
        self.repo = os.path.join(self.tmp, "proj")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "x")

    def task(self, repo_name, slug, repo_path, state="implementing", worktree="none", age_days=1):
        d = os.path.join(self.orch, repo_name, slug)
        os.makedirs(d)
        p = os.path.join(d, "status.md")
        with open(p, "w") as fh:
            fh.write("# Status: %s\n\n- task_state: %s\n- repo: %s\n- worktree: %s\n" % (slug, state, repo_path, worktree))
        then = time.time() - age_days * 86400
        os.utime(p, (then, then))

    def action(self, cwd):
        res = run(["current"], cwd, self.orch)
        self.assertEqual(res.returncode, 0, res.stderr)
        return next(l for l in res.stdout.splitlines() if l.startswith("action:")).split(" ", 1)[1], res.stdout

    def test_one_live_task_resumes(self):
        self.task("proj", "a", self.repo)
        self.assertEqual(self.action(self.repo)[0], "resume proj/a")

    def test_subdirectory_resumes(self):
        self.task("proj", "a", self.repo)
        sub = os.path.join(self.repo, "src")
        os.makedirs(sub)
        self.assertEqual(self.action(sub)[0], "resume proj/a")

    def test_linked_worktree_picks_its_own_task(self):
        wt = os.path.join(self.tmp, "wt")
        git(self.repo, "worktree", "add", "-q", "-b", "orch/b", wt)
        self.task("proj", "a", self.repo)
        self.task("proj", "b", self.repo, worktree=wt)
        self.assertEqual(self.action(wt)[0], "resume proj/b")
        self.assertEqual(self.action(self.repo)[0], "ask")

    def test_several_live_tasks_ask(self):
        self.task("proj", "a", self.repo)
        self.task("proj", "b", self.repo)
        self.assertEqual(self.action(self.repo)[0], "ask")

    def test_only_stale_task_asks(self):
        self.task("proj", "a", self.repo, age_days=8)
        self.assertEqual(self.action(self.repo)[0], "ask")

    def test_stale_leftover_does_not_block_the_live_task(self):
        self.task("proj", "a", self.repo)
        self.task("proj", "old", self.repo, age_days=8)
        self.assertEqual(self.action(self.repo)[0], "resume proj/a")

    def test_task_touched_minutes_ago_asks(self):
        self.task("proj", "a", self.repo, age_days=0)
        action, out = self.action(self.repo)
        self.assertEqual(action, "ask")
        self.assertIn("another session may be active", out)

    def test_finished_task_is_not_pending(self):
        self.task("proj", "a", self.repo, state="done")
        self.assertEqual(self.action(self.repo)[0], "none")

    def test_other_repo_is_listed_not_taken(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self.task("other", "z", other)
        action, out = self.action(self.repo)
        self.assertEqual(action, "none")
        self.assertIn("other/z", out)

    def test_same_folder_name_elsewhere_is_not_this_repo(self):
        twin = os.path.join(self.tmp, "elsewhere", "proj")
        os.makedirs(twin)
        self.task("proj", "a", twin)
        self.assertEqual(self.action(self.repo)[0], "none")

    def test_moved_repo_matches_by_name_and_asks(self):
        self.task("proj", "a", os.path.join(self.tmp, "gone", "proj"))
        action, out = self.action(self.repo)
        self.assertEqual(action, "ask")
        self.assertIn("[name]", out)

    def test_nested_repo_is_not_its_parent(self):
        inner = os.path.join(self.repo, "inner")
        os.makedirs(inner)
        git(inner, "init", "-q", "-b", "main")
        self.task("proj", "a", self.repo)
        self.assertEqual(self.action(inner)[0], "none")

    def test_outside_git_uses_the_repo_path(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(os.path.join(plain, "sub"))
        self.task("plain", "a", plain)
        self.assertEqual(self.action(os.path.join(plain, "sub"))[0], "resume plain/a")

    def test_writes_nothing(self):
        self.task("proj", "a", self.repo)
        before = sorted(os.path.join(d, f) for d, _, fs in os.walk(self.orch) for f in fs)
        self.action(self.repo)
        self.assertEqual(before, sorted(os.path.join(d, f) for d, _, fs in os.walk(self.orch) for f in fs))


class Instructions(Base):
    def setUp(self):
        Base.setUp(self)
        self.file = os.path.join(self.tmp, "CLAUDE.md")

    def write(self, text):
        with open(self.file, "w") as fh:
            fh.write(text)

    def read(self):
        with open(self.file) as fh:
            return fh.read()

    def cmd(self, *args):
        return run(["install-instructions", "--file", self.file] + list(args), self.tmp, self.orch)

    def backups(self):
        return [n for n in os.listdir(self.tmp) if ".orch-backup-" in n]

    def test_creates_a_missing_file(self):
        self.assertEqual(self.cmd().returncode, 0)
        self.assertTrue(self.read().startswith(instructions.BEGIN))
        self.assertEqual(self.backups(), [])

    def test_appends_keeps_text_and_backs_up(self):
        self.write("# Mine\n\nKeep this.\n")
        self.assertEqual(self.cmd().returncode, 0)
        text = self.read()
        self.assertTrue(text.startswith("# Mine\n\nKeep this.\n\n" + instructions.BEGIN))
        self.assertEqual(len(self.backups()), 1)

    def test_second_run_changes_nothing(self):
        self.write("# Mine\n")
        self.cmd()
        first = self.read()
        res = self.cmd()
        self.assertIn("nothing to do", res.stdout)
        self.assertEqual(self.read(), first)
        self.assertEqual(len(self.backups()), 1)

    def test_rerun_replaces_the_block_in_place(self):
        self.write("# Top\n\n<!-- orch:begin old -->\nold rule\n<!-- orch:end -->\n\n# Bottom\n")
        self.assertEqual(self.cmd().returncode, 0)
        text = self.read()
        self.assertNotIn("old rule", text)
        self.assertLess(text.index("# Top"), text.index(instructions.BEGIN))
        self.assertLess(text.index(instructions.END), text.index("# Bottom"))

    def test_dry_run_writes_nothing(self):
        self.write("# Mine\n")
        res = self.cmd("--dry-run")
        self.assertIn("+" + instructions.BEGIN, res.stdout)
        self.assertEqual(self.read(), "# Mine\n")
        self.assertEqual(self.backups(), [])

    def test_uninstall_restores_the_text(self):
        self.write("# Top\n\nText.\n\n# Bottom\n")
        before = self.read()
        self.cmd()
        self.assertEqual(self.cmd("--uninstall").returncode, 0)
        self.assertEqual(self.read(), before)

    def test_uninstall_without_a_block(self):
        self.write("# Top\n")
        self.assertIn("nothing to do", self.cmd("--uninstall").stdout)

    def test_damaged_markers_refuse(self):
        self.write("<!-- orch:begin x -->\nno end\n")
        res = self.cmd()
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self.read(), "<!-- orch:begin x -->\nno end\n")

    LEGACY = ("# Always\n\n## Git\n\n- Rule.\n\n## Orchestration mode — offer it\n\n- Offer it.\n- Skip it.\n\n"
              "# When\n\n- **Fonts** — use mine.\n- **Resuming** — read `~/.claude/orchestrations/ACTIVE.md` first\n"
              "- **Other** — thing.\n\n## Subagents\n\n- **No worktree.**\n"
              "- **`orch-explorer`** and **`orch-executor`** are general.\n")

    def test_handwritten_rules_refuse_without_the_flag(self):
        self.write(self.LEGACY)
        res = self.cmd()
        self.assertEqual(res.returncode, 2)
        self.assertIn("--replace-existing", res.stderr)
        self.assertEqual(self.read(), self.LEGACY)

    def test_replace_existing_swaps_only_orch_rules(self):
        self.write(self.LEGACY)
        self.assertEqual(self.cmd("--replace-existing").returncode, 0)
        text = self.read()
        for gone in ("Offer it.\n- Skip it.", "orchestrations/ACTIVE.md` first", "are general."):
            self.assertNotIn(gone, text)
        for kept in ("- Rule.", "- **Fonts** — use mine.", "- **Other** — thing.", "- **No worktree.**", "# When"):
            self.assertIn(kept, text)
        self.assertLess(text.index("- Rule."), text.index(instructions.BEGIN))
        self.assertLess(text.index(instructions.END), text.index("# When"))
        self.assertEqual(self.cmd().returncode, 0)  # the block's own lines do not count as hand-written

    def test_follows_a_symlink(self):
        real = os.path.join(self.tmp, "real.md")
        with open(real, "w") as fh:
            fh.write("# Mine\n")
        os.symlink(real, self.file)
        self.assertEqual(self.cmd().returncode, 0)
        self.assertTrue(os.path.islink(self.file))
        with open(real) as fh:
            self.assertIn(instructions.BEGIN, fh.read())

    def test_block_is_well_formed(self):
        with open(instructions.TEMPLATE) as fh:
            lines = fh.read().rstrip("\n").split("\n")
        self.assertEqual(instructions.block_span(lines), (0, len(lines)))


class Decide(unittest.TestCase):
    def test_unknown_state_is_not_certain(self):
        hit = {"task": "r/a", "match": "repo", "stale": False, "busy": False, "state": "unknown"}
        self.assertEqual(current.decide([hit]), ("ask", None))


if __name__ == "__main__":
    unittest.main()
