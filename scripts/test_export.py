#!/usr/bin/env python3
"""Tests for export.py (the walk, the denylist, the LICENSE exemption, FILES and EDITS) and for install.sh.

Each test builds a throwaway repo (a copy of export.py plus a fixture source tree) and a temp
denylist, so nothing reads the real ~/.claude or the real denylist.

Run: python3 scripts/test_export.py
"""
import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(HERE, "export.py")
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True  # a .pyc of export.py would hit the denylist on the next real export
import export  # noqa: E402

SECRET = "SECRETWORD"
# Stand-ins for the values of the private file; the real ones never appear in this repo.
PRIVATE = {"AUDIT": "fake-audit", "WHY_DOC": "FAKEDOC", "STATUSLINE": "fake-line.sh",
           "PRIVATE_REPO": "FakeRepo", "PRIVATE_TASK": "faketask"}


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ExportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="export-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        self.src = os.path.join(self.tmp, "src")
        self.deny = os.path.join(self.tmp, "denylist.txt")
        with open(EXPORT, encoding="utf-8") as f:
            write(os.path.join(self.repo, "scripts", "export.py"), f.read())
        self.priv = os.path.join(self.tmp, "private.json")
        write(self.deny, "# test denylist\n" + SECRET + "\n")
        write(self.priv, json.dumps(PRIVATE))
        self.build_src()

    def moved(self):
        return {export.fill(k, PRIVATE): v for k, v in export.MOVED.items()}

    def build_src(self):
        """A source tree with every FILES/MOVED entry; each edit's old text (or regex sample) is appended."""
        moved = self.moved()
        dest_to_src = {dst: src for src, dst in moved.items()}
        texts = {}
        for rel in export.FILES + list(moved):
            texts[rel] = "benign text for %s\n" % rel
        olds = [(rel, old) for rel, old, _new in export.EDITS]
        olds += [(rel, export.fill(old, PRIVATE)) for rel, old, _new in export.PRIVATE_EDITS]
        olds += [(rel, sample) for rel, _p, _f, _r, _c, sample in export.REGEX_EDITS]
        for rel, old in olds:
            src_rel = dest_to_src.get(rel, rel)
            texts.setdefault(src_rel, "benign text for %s\n" % src_rel)  # a file inside a TREES directory
            texts[src_rel] += old + "\n"
        for rel, text in texts.items():
            write(os.path.join(self.src, rel), text)

    def run_export(self, deny=True, private=True):
        cmd = [sys.executable, os.path.join(self.repo, "scripts", "export.py"), "--src", self.src,
               "--private", self.priv if private else os.path.join(self.tmp, "absent.json")]
        cmd += ["--denylist", self.deny if deny else os.path.join(self.tmp, "absent.txt")]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return p.returncode, p.stdout, p.stderr

    def test_clean_export_exits_0_and_copies_files(self):
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        n = len(export.FILES) + len(export.MOVED) + len(self.tree_rels())
        n_edits = len(export.EDITS) + len(export.PRIVATE_EDITS) + len(export.REGEX_EDITS)
        self.assertIn("copied %d files, %d edits, 0 failed" % (n, n_edits), out)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "skills/orchestrate/SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "bin/role-cost.py")))  # MOVED

    def tree_rels(self):
        """Every file under a TREES directory in the fixture source that a tree copy takes."""
        out = []
        for tree in export.TREES:
            out += [tree + "/" + r for r in export.tree_files(os.path.join(self.src, tree))]
        return out

    def test_trees_copy_by_suffix_and_fixtures_and_skip_caches(self):
        t = "bin/orchlib"
        for rel in ("a.py", "page.html", "tests/check.mjs", "tests/fixtures/old/x.jsonl", "tests/fixtures/deep/er/y.md"):
            write(os.path.join(self.src, t, rel), "keep %s\n" % rel)
        for rel in ("notes.txt", "tests/other.md", "__pycache__/a.cpython-39.pyc", "tests/__pycache__/z.py",
                    "tests/fixtures/__pycache__/q.json", ".DS_Store", "tests/fixtures/.DS_Store"):
            write(os.path.join(self.src, t, rel), "skip %s\n" % rel)
        write(os.path.join(self.repo, t, "gone.py"), "stale\n")  # removed in the source: must go
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        for rel in ("a.py", "page.html", "tests/check.mjs", "tests/fixtures/old/x.jsonl", "tests/fixtures/deep/er/y.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.repo, t, rel)), rel)
        for rel in ("notes.txt", "tests/other.md", "__pycache__/a.cpython-39.pyc", "tests/__pycache__/z.py",
                    "tests/fixtures/__pycache__/q.json", ".DS_Store", "tests/fixtures/.DS_Store", "gone.py"):
            self.assertFalse(os.path.exists(os.path.join(self.repo, t, rel)), rel)
        self.assertEqual(sorted(export.tree_files(os.path.join(self.repo, t))), sorted(export.tree_files(os.path.join(self.src, t))))

    def test_trees_do_not_follow_symlinks(self):
        outside = os.path.join(self.tmp, "outside.py")
        write(outside, "outside\n")
        os.symlink(outside, os.path.join(self.src, "bin/orchlib", "link.py"))
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "bin/orchlib", "link.py")))

    def test_denylisted_string_in_a_tree_file_fails(self):
        write(os.path.join(self.src, "bin/orchlib/tests/fixtures/f.md"), "leak %s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("DENYLIST bin/orchlib/tests/fixtures/f.md:1", err)

    def test_missing_tree_exits_2(self):
        shutil.rmtree(os.path.join(self.src, "bin/orchlib"))
        rc, out, err = self.run_export()
        self.assertEqual(rc, 2)
        self.assertIn("missing in source", err)

    def git(self, cwd, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=self.tmp, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false"] + list(args), env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_real_worktree_is_skipped(self):
        # a worktree: a directory holding a `.git` *file* that points at a real gitdir
        main = os.path.join(self.tmp, "main")
        os.makedirs(main)
        self.git(main, "init", "-q")
        write(os.path.join(main, "a.txt"), "x\n")
        self.git(main, "add", "a.txt")
        self.git(main, "commit", "-q", "-m", "x")
        wt = os.path.join(self.repo, ".claude", "worktrees", "task")
        self.git(main, "worktree", "add", "-q", "-b", "wt", wt)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")))
        write(os.path.join(wt, "LICENSE"), "copyright %s\n" % SECRET)
        write(os.path.join(wt, "src", "a.txt"), "%s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        self.assertIn("0 hits", out)

    def test_real_nested_checkout_is_skipped(self):
        clone = os.path.join(self.repo, "vendor", "clone")
        os.makedirs(clone)
        self.git(clone, "init", "-q")
        write(os.path.join(clone, "src", "a.txt"), "%s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)

    def test_nested_dir_with_invalid_git_file_is_still_scanned(self):
        # git does not treat this as a repo and would commit its files: the denylist must see them
        for i, text in enumerate(("gitdir: /nonexistent/path\n", "not a gitfile\n", "")):
            d = os.path.join(self.repo, "sub%d" % i)
            write(os.path.join(d, ".git"), text)
            write(os.path.join(d, "secret.txt"), "%s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        for i in range(3):
            self.assertIn("DENYLIST sub%d/secret.txt:1" % i, err)

    def test_nested_dir_with_empty_git_directory_is_still_scanned(self):
        os.makedirs(os.path.join(self.repo, "sub", ".git"))
        write(os.path.join(self.repo, "sub", "secret.txt"), "%s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("DENYLIST sub/secret.txt:1", err)

    def test_repo_root_git_file_is_skipped(self):
        write(os.path.join(self.repo, ".git"), "gitdir: /x/%s/.git/worktrees/w\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)

    def test_git_directory_is_still_skipped(self):
        write(os.path.join(self.repo, ".git", "config"), "%s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)

    def test_denylisted_string_in_normal_file_fails(self):
        write(os.path.join(self.repo, "docs", "note.md"), "ok\nhas %s inside\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("DENYLIST docs/note.md:2", err)

    def test_denylisted_string_in_exported_file_fails(self):
        with open(os.path.join(self.src, "skills/orchestrate/SKILL.md"), "a", encoding="utf-8") as f:
            f.write("leak %s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("DENYLIST skills/orchestrate/SKILL.md", err)

    def test_denylisted_word_in_export_py_itself_fails(self):
        script = os.path.join(self.repo, "scripts", "export.py")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)                      # the copy is clean to begin with
        with open(script, "a", encoding="utf-8") as f:
            f.write("# a private word: %s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("DENYLIST scripts/export.py:", err)

    def test_top_level_license_exemption_unchanged(self):
        write(os.path.join(self.repo, "LICENSE"), "copyright holder %s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        write(os.path.join(self.repo, "sub", "LICENSE"), "copyright holder %s\n" % SECRET)
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1, "only the top-level LICENSE is exempt")
        self.assertIn("DENYLIST sub/LICENSE", err)

    def test_missing_source_file_exits_2(self):
        os.remove(os.path.join(self.src, export.FILES[0]))
        rc, out, err = self.run_export()
        self.assertEqual(rc, 2)
        self.assertIn("missing in source", err)

    def test_edit_that_does_not_match_once_fails(self):
        rel, old, _new = export.EDITS[0]
        p = os.path.join(self.src, rel)
        with open(p, "w", encoding="utf-8") as f:
            f.write("nothing to edit here\n")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("EDIT FAIL", err)

    def test_edit_is_applied(self):
        rel, old, new = export.EDITS[0]
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.repo, rel), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(old, text)

    def test_missing_private_file_skips_its_edits_and_fails(self):
        rc, out, err = self.run_export(private=False)
        self.assertEqual(rc, 1)
        self.assertIn("SKIPPED (private key missing)", err)
        self.assertEqual(err.count("SKIPPED"), len(export.PRIVATE_EDITS) + len(export.MOVED))
        # the skipped edits left their text in place: nothing was quietly sanitised
        with open(os.path.join(self.repo, "skills/orchestrate/PROGRAM.md"), encoding="utf-8") as f:
            self.assertIn(PRIVATE["WHY_DOC"], f.read())

    def test_private_value_is_replaced_by_its_edit(self):
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.repo, "bin/usage-check.sh"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(PRIVATE["STATUSLINE"], text)
        self.assertIn("orch-ratelimit-cache.sh", text)

    def html_path(self):
        return os.path.join(self.src, "bin/orchlib/dashboard.html")

    def test_html_edits_are_structural(self):
        write(self.html_path(),
              '<head>\n<link rel="preconnect" href="https://any.host.test" crossorigin>\n'
              '<link rel="stylesheet" href="https://any.host.test/x/y.css">\n'
              '<link rel="icon" href="/local.png">\n<style>\n  --font: "Whatever Name", system-ui, sans-serif;\n</style>\n')
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.repo, "bin/orchlib/dashboard.html"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("any.host.test", text)
        self.assertNotIn("Whatever Name", text)
        self.assertIn('<link rel="icon" href="/local.png">', text)  # a local link is not an external origin
        self.assertIn("  --font: system-ui, sans-serif;", text)

    def test_html_edit_with_no_external_link_fails(self):
        write(self.html_path(), '<style>\n  --font: "X", sans-serif;\n</style>\n')
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("EDIT FAIL bin/orchlib/dashboard.html", err)

    def test_html_font_edit_matching_twice_fails(self):
        write(self.html_path(), '<link href="https://h.test/a.css">\n  --font: "X", a;\n  --font: "Y", b;\n')
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("regex expected 1 match(es), found 2", err)

    def test_html_font_edit_matching_zero_times_fails(self):
        write(self.html_path(), '<link href="https://h.test/a.css">\n  --font: sans-serif;\n')
        rc, out, err = self.run_export()
        self.assertEqual(rc, 1)
        self.assertIn("regex expected 1 match(es), found 0", err)

    def test_missing_denylist_is_not_a_pass(self):
        rc, out, err = self.run_export(deny=False)
        self.assertEqual(rc, 1)
        self.assertIn("nothing checked", err)


class InstallCase(unittest.TestCase):
    """install.sh against an empty temporary HOME; the real ~/.claude is never touched."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="install-test-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def run_install(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "ORCH_DIR"}
        env["HOME"] = self.home
        p = subprocess.run(["bash", os.path.join(os.path.dirname(HERE), "install.sh")] + list(args), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return p.returncode, p.stdout, p.stderr

    def test_dry_run_creates_nothing(self):
        rc, out, err = self.run_install("--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("(dry run)", out)
        self.assertIn("new   ", out)                      # it did list what it would install
        self.assertEqual(os.listdir(self.home), [])       # no ~/.claude, no orchestrations dir, no backup dir

    def test_install_creates_the_orchestrations_dir_and_a_second_run_changes_nothing(self):
        rc, out, err = self.run_install()
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".claude", "orchestrations")))
        self.assertTrue(os.access(os.path.join(self.home, ".claude", "bin", "orch"), os.X_OK))
        rc, out, err = self.run_install("--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("new 0, updated 0", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
