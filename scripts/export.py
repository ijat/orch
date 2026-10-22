#!/usr/bin/env python3
"""Sync this repo from a live ~/.claude install.

The live install is the source of truth. This script copies the orchestration files into the repo,
applies the few edits listed in EDITS (dropping pointers to maintainer-only files, making examples
generic), and then checks the result.

Usage: scripts/export.py [--src ~/.claude] [--denylist FILE]

- Every edit must match exactly once. When upstream text changes, the script fails instead of
  shipping something half-sanitised.
- --denylist (default ~/.claude/orch-export-denylist.txt, never committed) holds case-insensitive
  regexes, one per line. Any match in the exported tree fails the export, this file included.
- This file names no private word. The few PRIVATE_EDITS that must match one carry an @@KEY@@ token; the
  value comes from --private (default ~/.claude/orch-export-private.json, never committed, JSON object).
  An entry whose key is missing is skipped with a warning and counts as a failure (exit 1).

Exit 0 = clean; 1 = an edit didn't apply or the denylist matched; 2 = bad input.
"""
import argparse, json, os, re, shutil, subprocess, sys


FILES = [
    "skills/orchestrate/SKILL.md", "skills/orchestrate/PROGRAM.md", "skills/orchestrate/TIERS.md",
    *[f"skills/orchestrate/templates/{n}.md" for n in (
        "journal", "log", "notes", "plan", "policy", "questions", "retro", "review", "review-visual",
        "roadmap", "security-ledger", "security-review", "sketch", "status")],
    *[f"agents/{n}.md" for n in (
        "orch-planner", "orch-implementer", "orch-code-reviewer", "orch-security-reviewer",
        "orch-plan-reviewer", "orch-director", "orch-explorer", "orch-executor")],
    "bin/usage-check.sh", "bin/usage-watch.sh", "bin/orch-stats.sh", "bin/orch-index.sh",
    "bin/orch-index-hook.sh", "bin/orch-program-check.py", "bin/orch-build-agents.py",
    "bin/orch-build-hook.sh", "bin/orch",
    *[f"skills/orchestrate/roles/{n}.md" for n in (
        "orch-planner", "orch-implementer", "orch-code-reviewer", "orch-security-reviewer",
        "orch-plan-reviewer", "orch-director", "orch-explorer", "orch-executor")],
    *[f"skills/orchestrate/roles/_shared/{n}.md" for n in (
        "when-to-delegate", "trust-instrument", "be-brief", "every-role")],
]
# Whole directories, copied at the same path. A file is copied when its suffix is in TREE_SUFFIXES or
# it sits under a TREE_ALL directory (relative to the tree root; the fixtures are synthetic data).
# `__pycache__`, `*.pyc` and `.DS_Store` are skipped. The destination tree is emptied first, so a
# file removed in the source disappears from the repo too.
TREES = ["bin/orchlib"]
TREE_SUFFIXES = (".py", ".html", ".mjs")
TREE_ALL = ("tests/fixtures",)
# Copied from elsewhere in the live install, landing at a different repo path.
# (the source key has an @@KEY@@ token, filled from the private file)
MOVED = {"skills/@@AUDIT@@/scripts/role-cost.py": "bin/role-cost.py"}

# (repo path, old, new): each old must occur exactly once in the copied file. `@@KEY@@` in old or new is
# filled from the private file; these are the only entries that need a private word.
PRIVATE_EDITS = [
    ("skills/orchestrate/SKILL.md",
     "Why each rule exists: `~/.claude/skills/orchestrate/@@WHY_DOC@@.md`. Read it before changing a rule.\n\n", ""),
    ("skills/orchestrate/SKILL.md", "defaults, see @@WHY_DOC@@). Write a tier", "defaults). Write a tier"),
    ("skills/orchestrate/SKILL.md",
     " `orch-stats.sh` aggregates every journal; to change the workflow\n"
     "itself, use the `@@AUDIT@@` skill.", " `orch-stats.sh` aggregates every journal."),
    ("skills/orchestrate/PROGRAM.md", " Why each rule exists: `@@WHY_DOC@@.md`, \"Program mode\".", ""),
    ("skills/orchestrate/PROGRAM.md", "`~/.claude/skills/@@AUDIT@@/scripts/role-cost.py --by tier`",
     "`~/.claude/bin/role-cost.py --by tier`"),
    ("skills/orchestrate/templates/journal.md",
     '"repo":"@@PRIVATE_REPO@@","task":"p2-ext-ux"', '"repo":"myapp","task":"add-login"'),
    ("bin/orch-stats.sh", "(e.g. @@PRIVATE_TASK@@'s \"orch-planner\" ran on sonnet)",
     "(e.g. an \"orch-planner\" spawn that ran on sonnet)"),
    ("bin/role-cost.py", " (for the @@AUDIT@@ skill)", ""),
    ("bin/usage-check.sh", "# Reads ONLY local files written by ~/.claude/@@STATUSLINE@@.",
     "# Reads ONLY local files written by orch-ratelimit-cache.sh (from the statusline)."),
]

# Edits that need no private word.
EDITS = [
    ("skills/orchestrate/TIERS.md", "(`role-cost.py`)", "(`~/.claude/bin/role-cost.py`)"),
    ("bin/orchlib/tests/test_dashboard_page.mjs",
     "// The font name is built from parts so this file carries no word the public export removes.\n", ""),
    ("bin/orchlib/tests/test_dashboard_page.mjs", '  assert.equal(count(html, "<link"), 2);\n',
     '  assert.equal(count(html, "<link"), 0);\n'),
    ("bin/orchlib/tests/test_dashboard_page.mjs",
     '  assert.equal(urls.length, 2, urls.join(" "));\n'
     '  for (const u of urls) { assert.ok(u.startsWith("https://" + HOST), u); }\n',
     '  assert.equal(urls.length, 0, urls.join(" "));\n'),
]

# (repo path, regex, flags, replacement, expected matches or None for "at least one", sample the tests append).
# The dashboard page loads a web font from an external https origin; the public copy keeps the system fonts.
# Every external `<link>` goes (the server derives its CSP from what is left) and the first quoted family
# goes from `--font:`, whatever their names, so no host or font name is needed here.
REGEX_EDITS = [
    ("bin/orchlib/dashboard.html", r'^<link [^>]*href="https://[^"]*"[^>]*>\n', re.M, "", None,
     '<link rel="stylesheet" href="https://host.example/a.css">\n'),
    ("bin/orchlib/dashboard.html", r'^(  --font: )"[^"]*", ', re.M, r"\1", 1,
     '  --font: "Some Family", system-ui, sans-serif;\n'),
    # The page tests assert those lines; the public copy asserts the page without them.
    ("bin/orchlib/tests/test_dashboard.py",
     r'        self\.assertEqual\(len\(\[l for l in lines if "[^"\n]*" in l\]\), 2\)\n'
     r'        word = "[^"\n]*" \+ "[^"\n]*"\n'
     r'        self\.assertEqual\(len\(\[l for l in lines if word in l\.lower\(\)\]\), 2\)\n', 0,
     '        self.assertEqual(len([l for l in lines if "<link" in l]), 0)\n'
     '        self.assertEqual(len([l for l in lines if l == \'  --font: system-ui, -apple-system, "Segoe UI", sans-serif;\']), 1)\n',
     1,
     '        self.assertEqual(len([l for l in lines if "h.example" in l]), 2)\n'
     '        word = "ab" + "cd"\n'
     '        self.assertEqual(len([l for l in lines if word in l.lower()]), 2)\n'),
    ("bin/orchlib/tests/test_dashboard_page.mjs",
     r'  const preconnect = .*?\n  assert\.equal\(lines\.filter\(\(l\) => l\.toLowerCase\(\)\.includes\(lower\)\)\.length, 2\);\n',
     re.S,
     '  const family = `  --font: system-ui, -apple-system, "Segoe UI", sans-serif;`;\n'
     '  assert.equal(lines.filter((l) => l === family).length, 1, family);\n'
     '  assert.equal(lines.filter((l) => l.includes("<link")).length, 0);\n', 1,
     '  const preconnect = `x`;\n  assert.equal(lines.filter((l) => l.toLowerCase().includes(lower)).length, 2);\n'),
    # the name/host constants are only used by the assertions above
    ("bin/orchlib/tests/test_dashboard_page.mjs",
     r'const Name = \[[^\]\n]*\]\.join\(""\);\nconst lower = Name\.toLowerCase\(\);\nconst HOST = "[^"\n]*";\n', 0, "", 1,
     'const Name = ["a", "b"].join("");\nconst lower = Name.toLowerCase();\nconst HOST = "h.example";\n'),
]


def fill(text, private):
    """`text` with each @@KEY@@ replaced; None if a key is missing from `private`."""
    for key in re.findall(r"@@(\w+)@@", text):
        if key not in private:
            return None
    return re.sub(r"@@(\w+)@@", lambda m: private[m.group(1)], text)


def load_private(path):
    """The private values, or {} (with a warning) if the file is missing or not a JSON object of strings."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in d.items()):
            return d
        print(f"private file {path}: not a JSON object of strings", file=sys.stderr)
    except (OSError, ValueError) as e:
        print(f"no usable private file at {path} ({e.__class__.__name__})", file=sys.stderr)
    return {}


def tree_files(root):
    """Relative paths of the files under `root` that a tree copy takes (see TREES)."""
    out = []
    for cur, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            if n == ".DS_Store" or n.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(cur, n), root).replace(os.sep, "/")
            if n.endswith(TREE_SUFFIXES) or any(rel.startswith(t + "/") for t in TREE_ALL):
                if os.path.isfile(os.path.join(root, rel)) and not os.path.islink(os.path.join(root, rel)):
                    out.append(rel)
    return out


def nested_checkout(path):
    """True only if `path` holds a `.git` entry and git itself says `path` is the top of a work tree
    (a nested clone, a submodule or a worktree). An invalid `.git` file or an empty `.git` directory
    does not make it one, and a failed or missing git counts as 'not a checkout' (it gets scanned)."""
    if not os.path.lexists(os.path.join(path, ".git")):
        return False
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, env=env, universal_newlines=True)
    except OSError:
        return False
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--denylist", default=os.path.expanduser("~/.claude/orch-export-denylist.txt"))
    ap.add_argument("--private", default=os.path.expanduser("~/.claude/orch-export-private.json"))
    a = ap.parse_args()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    private = load_private(a.private)
    skipped = 0
    pairs = [(f, f) for f in FILES]
    for src_rel, dst_rel in MOVED.items():
        filled = fill(src_rel, private)
        if filled is None:
            print(f"SKIPPED (private key missing): copy to {dst_rel}", file=sys.stderr); skipped += 1; continue
        pairs.append((filled, dst_rel))
    for src_rel, dst_rel in pairs:
        src = os.path.join(a.src, src_rel)
        if not os.path.isfile(src):
            print(f"missing in source: {src}", file=sys.stderr); return 2
        dst = os.path.join(repo, dst_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    ntree = 0
    for tree in TREES:
        src_root = os.path.join(a.src, tree)
        if not os.path.isdir(src_root):
            print(f"missing in source: {src_root}", file=sys.stderr); return 2
        dst_root = os.path.join(repo, tree)
        if os.path.isdir(dst_root):
            shutil.rmtree(dst_root)
        for rel in tree_files(src_root):
            dst = os.path.join(dst_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(src_root, rel), dst)
            ntree += 1

    bad = 0
    edits = list(EDITS)
    for rel, old, new in PRIVATE_EDITS:
        fo, fn = fill(old, private), fill(new, private)
        if fo is None or fn is None:
            print(f"SKIPPED (private key missing) {rel}: {old[:30]!r}", file=sys.stderr); skipped += 1; continue
        edits.append((rel, fo, fn))
    for rel, old, new in edits:
        p = os.path.join(repo, rel)
        s = open(p, encoding="utf-8").read()
        n = s.count(old)
        if n != 1:
            print(f"EDIT FAIL {rel}: expected 1 match, found {n}: {old[:60]!r}", file=sys.stderr)
            bad += 1; continue
        open(p, "w", encoding="utf-8").write(s.replace(old, new))
    for rel, pat, flags, repl, count, _sample in REGEX_EDITS:
        p = os.path.join(repo, rel)
        s = open(p, encoding="utf-8").read()
        s2, n = re.subn(pat, repl, s, flags=flags)
        if (n != count) if count is not None else (n < 1):
            print(f"EDIT FAIL {rel}: regex expected {count if count is not None else '>=1'} match(es), found {n}: {pat[:50]!r}",
                  file=sys.stderr)
            bad += 1; continue
        open(p, "w", encoding="utf-8").write(s2)
    bad += skipped
    print(f"copied {len(pairs) + ntree} files, {len(edits) + len(REGEX_EDITS)} edits, {bad} failed")

    if not os.path.isfile(a.denylist):
        print(f"no denylist at {a.denylist}: nothing checked (not a pass)", file=sys.stderr); return 1
    pats = [l.strip() for l in open(a.denylist, encoding="utf-8") if l.strip() and not l.startswith("#")]
    rx = re.compile("|".join(f"(?:{p})" for p in pats), re.I)
    hits = 0
    for root, dirs, files in os.walk(repo):
        # Skip every `.git` (a directory, or the file a worktree has) and every directory below the
        # repo root that is a real nested checkout or worktree. A directory with a `.git` entry that
        # git does not accept is a plain directory: git would commit its files, so they are scanned.
        dirs[:] = [d for d in dirs if d != ".git" and not nested_checkout(os.path.join(root, d))]
        for fn in files:
            if fn == ".git":
                continue
            p = os.path.join(root, fn)
            if os.path.relpath(p, repo) == "LICENSE":  # the copyright holder's name belongs there
                continue
            for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                if rx.search(line):
                    print(f"DENYLIST {os.path.relpath(p, repo)}:{i}: {line.strip()[:120]}", file=sys.stderr)
                    hits += 1
    print(f"denylist: {len(pats)} patterns, {hits} hits")
    return 1 if (bad or hits) else 0


if __name__ == "__main__":
    sys.exit(main())
