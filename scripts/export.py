#!/usr/bin/env python3
"""Sync this repo from a live ~/.claude install.

The live install is the source of truth. This script copies the orchestration files into the repo,
applies the few edits listed in EDITS (dropping pointers to maintainer-only files, making examples
generic), and then checks the result.

Usage: scripts/export.py [--src ~/.claude] [--denylist FILE]

- Every edit must match exactly once. When upstream text changes, the script fails instead of
  shipping something half-sanitised.
- --denylist (default ~/.claude/orch-export-denylist.txt, never committed) holds case-insensitive
  regexes, one per line. Any match in the exported tree fails the export.

Exit 0 = clean; 1 = an edit didn't apply or the denylist matched; 2 = bad input.
"""
import argparse, os, re, shutil, sys

FILES = [
    "skills/orchestrate/SKILL.md", "skills/orchestrate/PROGRAM.md", "skills/orchestrate/TIERS.md",
    *[f"skills/orchestrate/templates/{n}.md" for n in (
        "journal", "log", "notes", "plan", "retro", "review", "review-visual", "roadmap",
        "security-ledger", "security-review", "sketch", "status")],
    *[f"agents/{n}.md" for n in (
        "orch-planner", "orch-implementer", "orch-code-reviewer", "orch-security-reviewer",
        "orch-plan-reviewer", "orch-director", "explorer", "executor")],
    "bin/usage-check.sh", "bin/usage-watch.sh", "bin/orch-stats.sh", "bin/orch-index.sh",
    "bin/orch-index-hook.sh", "bin/orch-program-check.py", "bin/orch-build-agents.py",
    *[f"skills/orchestrate/roles/{n}.md" for n in (
        "orch-planner", "orch-implementer", "orch-code-reviewer", "orch-security-reviewer",
        "orch-plan-reviewer", "orch-director")],
    *[f"skills/orchestrate/roles/_shared/{n}.md" for n in (
        "when-to-delegate", "trust-instrument", "be-brief", "every-role")],
]
# Copied from elsewhere in the live install, landing at a different repo path.
MOVED = {"skills/orchestrate-audit/scripts/role-cost.py": "bin/role-cost.py"}

# (repo path, old, new): each old must occur exactly once in the copied file.
EDITS = [
    ("skills/orchestrate/SKILL.md",
     "Why each rule exists: `~/.claude/skills/orchestrate/RATIONALE.md`. Read it before changing a rule.\n\n", ""),
    ("skills/orchestrate/SKILL.md", "verified 2026-10-08, see RATIONALE)", "verified 2026-10-08)"),
    ("skills/orchestrate/SKILL.md",
     " For analysing or changing the workflow itself, use the `orchestrate-audit`\n"
     "skill, which wraps this with further checks and the change procedure.", ""),
    ("skills/orchestrate/PROGRAM.md", " Why each rule exists: `RATIONALE.md`, \"Program mode\".", ""),
    ("skills/orchestrate/PROGRAM.md", "`~/.claude/skills/orchestrate-audit/scripts/role-cost.py --by tier`",
     "`~/.claude/bin/role-cost.py --by tier`"),
    ("skills/orchestrate/TIERS.md", "(`role-cost.py`)", "(`~/.claude/bin/role-cost.py`)"),
    ("skills/orchestrate/templates/journal.md",
     '"repo":"Debugger","task":"p2-ext-ux"', '"repo":"myapp","task":"add-login"'),
    ("bin/orch-stats.sh", "(e.g. whipspot's \"orch-planner\" ran on sonnet)",
     "(e.g. an \"orch-planner\" spawn that ran on sonnet)"),
    ("bin/role-cost.py", " (for the orchestrate-audit skill)", ""),
    ("bin/usage-check.sh", "# Reads ONLY local files written by ~/.claude/statusline-command.sh.",
     "# Reads ONLY local files written by orch-ratelimit-cache.sh (from the statusline)."),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--denylist", default=os.path.expanduser("~/.claude/orch-export-denylist.txt"))
    a = ap.parse_args()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    pairs = [(f, f) for f in FILES] + list(MOVED.items())
    for src_rel, dst_rel in pairs:
        src = os.path.join(a.src, src_rel)
        if not os.path.isfile(src):
            print(f"missing in source: {src}", file=sys.stderr); return 2
        dst = os.path.join(repo, dst_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    bad = 0
    for rel, old, new in EDITS:
        p = os.path.join(repo, rel)
        s = open(p, encoding="utf-8").read()
        n = s.count(old)
        if n != 1:
            print(f"EDIT FAIL {rel}: expected 1 match, found {n}: {old[:60]!r}", file=sys.stderr)
            bad += 1; continue
        open(p, "w", encoding="utf-8").write(s.replace(old, new))
    print(f"copied {len(pairs)} files, {len(EDITS)} edits, {bad} failed")

    if not os.path.isfile(a.denylist):
        print(f"no denylist at {a.denylist}: nothing checked (not a pass)", file=sys.stderr); return 1
    pats = [l.strip() for l in open(a.denylist, encoding="utf-8") if l.strip() and not l.startswith("#")]
    rx = re.compile("|".join(f"(?:{p})" for p in pats), re.I)
    hits = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in files:
            p = os.path.join(root, fn)
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue
            for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                if rx.search(line):
                    print(f"DENYLIST {os.path.relpath(p, repo)}:{i}: {line.strip()[:120]}", file=sys.stderr)
                    hits += 1
    print(f"denylist: {len(pats)} patterns, {hits} hits")
    return 1 if (bad or hits) else 0


if __name__ == "__main__":
    sys.exit(main())
