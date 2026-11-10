#!/usr/bin/env python3
"""Text parity of the plugin's instructions against a base commit (the pre-plugin skill install).

Usage: text-parity.py --base REV [--verbose]

Pairs every file in plugin/skills/** and plugin/agents/*.md with its twin at REV (skills/**, agents/*.md; role and
agent files were orch-<role>.md there). Both sides go through the same normalizer N, whose table is this file's own
(it never imports the codemod that made the change): agent names, `name:` lines, command and skill-path prefixes,
the data paths and the mod tool prefixes. What differs after N must be an entry of ALLOWLIST (file, old line, new
line, reason), and every entry must be used exactly once. It also fails on a file present on one side only and on an
agent whose frontmatter `model`, `effort` or `tools` changed.

`init` is outside the agent collapse: `orch-init` is also the skill's name, so each orch-init -> orch:init change is
an allowlist entry, and every orch-init line left unchanged is printed under "kept as skill".

Prints counts per mapping kind and per allowlisted hunk. Exit 0 on parity, 1 on any difference, 2 on bad input.
Legacy agent names are allowed here by design (T6).
"""
import difflib
import os
import re
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES = ("code-reviewer", "director", "executor", "explorer", "graph", "implementer", "init", "plan-reviewer",
         "planner", "security-reviewer")
COLLAPSED = "|".join(sorted((r for r in ROLES if r != "init"), key=len, reverse=True))
HOME = r"~/\.claude"
PLUG = r"\$\{CLAUDE_PLUGIN_ROOT\}"

# (kind, pattern, replacement), applied in order to each line of both sides. Data paths map the new form to the
# old one; the other rules map both spellings to one placeholder.
TABLE = (
    ("data:logs", re.compile(HOME + r"/orch-data/logs/"), "~/.claude/orchestrations/"),
    ("data:tasks", re.compile(HOME + r"/orch-data/tasks(?![\w-])"), "~/.claude/orchestrations"),
    ("data:orch-index", re.compile(HOME + r"/orch-data/bin/orch-index(?![\w.-])"), "~/.claude/bin/orch-index"),
    ("data:orch-index-env", re.compile(r"\$\{ORCH_DATA:-\$HOME/\.claude/orch-data\}/bin/orch-index(?![\w.-])"),
     "$HOME/.claude/bin/orch-index"),
    ("data:usage", re.compile(HOME + r"/orch-data/cache/usage(?![\w.-])"), "~/.claude/.orch-usage"),
    ("data:private", re.compile(HOME + r"/orch-data/private/"), "~/.claude/"),
    ("agent-file", re.compile(r"(?:%s|%s|\.\./\.\.)/agents/(?:orch-)?(%s)\.md" % (HOME, PLUG, COLLAPSED)),
     r"«agents»/\1.md"),
    # a command: the orch-index binary is a data path (above), never a bare command
    ("command", re.compile(r"(?:%s|%s)/bin/(?!orch-index(?![\w.-]))([\w.-]+)" % (HOME, PLUG)), r"\1"),
    ("skill-path", re.compile(r"(?:%s|%s)/skills/(?:orchestrate|orch-init)/" % (HOME, PLUG)), ""),
    ("mod-tool", re.compile(r"mcp__orch(?:-pane|-ui)?__"), "mcp__«mod»__"),
    ("name", re.compile(r"^name: (?:orch-)?(%s)$" % COLLAPSED), r"name: «\1»"),
    ("agent", re.compile(r"(?<![\w.:-])orch[-:](%s)(?![\w-])" % COLLAPSED), r"«\1»"),
    ("agent-glob", re.compile(r"`orch[-:]\*`"), "`«agents»*`"),
)

# Every line that still differs after N. old/new are the raw lines (None: the line exists on one side only).
ALLOWLIST = (
    {"file": "plugin/skills/orchestrate/SKILL.md", "old": None,
     "new": "Paths: this file and the role files name skill files by their full path under `${CLAUDE_PLUGIN_ROOT}/skills/`; in this skill's other files (`PROGRAM.md`, `TIERS.md`, `LITE.md`, `INTERACTIVE.md`, `VISUAL.md`, `templates/`) a relative path is relative to this skill's directory, `${CLAUDE_PLUGIN_ROOT}/skills/orchestrate/`.",
     "reason": "T3: SKILL.md says once how relative paths resolve (new sentence)"},
    {"file": "plugin/skills/orchestrate/SKILL.md",
     "old": "| Init | `orch-init` | `sonnet/high` | → `opus/medium` for an ecosystem with no `skills/orch-init/references/<ecosystem>.md`; sets up or audits a project's agent files for the `orch-init` skill (step 3a) |",
     "new": "| Init | `orch:init` | `sonnet/high` | → `opus/medium` for an ecosystem with no `skills/orch-init/references/<ecosystem>.md`; sets up or audits a project's agent files for the `orch-init` skill (step 3a) |",
     "reason": "orch-init -> orch:init: the roles table's agent column (the agent type to spawn); the path and the skill mention on the same line stay"},
    {"file": "plugin/skills/orchestrate/SKILL.md",
     "old": "role's source, `~/.claude/skills/orchestrate/roles/<role>.md` (or this skill, for orchestrator behaviour), and run",
     "new": "role's source, `plugin/skills/orchestrate/roles/<role>.md` in the orch repository (or this skill, for orchestrator behaviour), and run",
     "reason": "lesson promotion edits the role source in the orch repository, not the installed plugin copy (a plugin update would overwrite it)"},
    {"file": "plugin/skills/orchestrate/SKILL.md",
     "old": "`~/.claude/bin/orch-build-agents.py` in the same change — never `agents/orch-*.md`, which are generated and",
     "new": "`scripts/build-agents.py plugin/skills/orchestrate/roles plugin/agents` in the same change — never `plugin/agents/*.md`, which are generated and",
     "reason": "T6: orch-build-agents.py was removed (step 1 moved it to scripts/build-agents.py, a dev script that needs both dirs); agent files are <role>.md"},
    {"file": "plugin/skills/orchestrate/SKILL.md",
     "old": "shows the proposal, and has the `orch-init` agent write the files in the repo's main checkout on the",
     "new": "shows the proposal, and has the `orch:init` agent write the files in the repo's main checkout on the",
     "reason": "orch-init -> orch:init: names the agent that writes the files"},
    {"file": "plugin/skills/orch-init/SKILL.md",
     "old": "You are the **session** that runs project initialization. A Sonnet `orch-init` agent researches",
     "new": "You are the **session** that runs project initialization. A Sonnet `orch:init` agent researches",
     "reason": "orch-init -> orch:init: names the agent (vs. the session running the skill)"},
    {"file": "plugin/skills/orch-init/SKILL.md",
     "old": "4. **Phase 1 — proposal.** Spawn the `orch-init` agent (model `sonnet`, effort `high`; `opus`/`medium`",
     "new": "4. **Phase 1 — proposal.** Spawn the `orch:init` agent (model `sonnet`, effort `high`; `opus`/`medium`",
     "reason": "orch-init -> orch:init: a spawn of the agent"},
    {"file": "plugin/skills/orch-init/SKILL.md",
     "old": "- Run `install.sh`, deploy, or touch anything outside the repo and `~/.claude/orchestrations/<repo-name>/`.",
     "new": "- Deploy, or touch anything outside the repo and `~/.claude/orch-data/tasks/<repo-name>/`.",
     "reason": "intended removal: install.sh retired in step 4 (orchestrator decision); the deploy and outside-the-repo rules stay"},
    {"file": "plugin/skills/orch-init/SKILL.md",
     "old": "`agents/orch-init.md` (`skills/orchestrate/roles/orch-init.md`).",
     "new": "`agents/init.md` (`skills/orchestrate/roles/init.md`).",
     "reason": "the init agent's and role's files are renamed to the role word (D2, T5); these are file paths"},
    {"file": "plugin/skills/orch-init/references/architecture.md",
     "old": "Language-neutral. Used by the `orch-init` agent to name boundaries for a project and to write the",
     "new": "Language-neutral. Used by the `orch:init` agent to name boundaries for a project and to write the",
     "reason": "orch-init -> orch:init: names the agent that reads this reference"},
    {"file": "plugin/skills/orch-init/templates/audit.md",
     "old": "<!-- Output of the orch-init agent in audit mode: ~/.claude/orchestrations/<repo-name>/audit.md.",
     "new": "<!-- Output of the orch:init agent in audit mode: ~/.claude/orch-data/tasks/<repo-name>/audit.md.",
     "reason": "orch-init -> orch:init: names the agent that writes this file"},
    {"file": "plugin/skills/orch-init/templates/init-record.md",
     "old": "<!-- Written by the orch-init agent at ~/.claude/orchestrations/<repo-name>/init.md. Orchestrate reads",
     "new": "<!-- Written by the orch:init agent at ~/.claude/orch-data/tasks/<repo-name>/init.md. Orchestrate reads",
     "reason": "orch-init -> orch:init: names the agent that writes this file"},
    {"file": "plugin/skills/orch-init/templates/proposal.md",
     "old": "<!-- Phase-1 output of the orch-init agent: ~/.claude/orchestrations/<repo-name>/init-proposal.md.",
     "new": "<!-- Phase-1 output of the orch:init agent: ~/.claude/orch-data/tasks/<repo-name>/init-proposal.md.",
     "reason": "orch-init -> orch:init: names the agent that writes this file"},
)
for _role_file in ("plugin/skills/orchestrate/roles/init.md", "plugin/agents/init.md"):
    ALLOWLIST += (
        {"file": _role_file, "old": "name: orch-init", "new": "name: init",
         "reason": "the init agent's name is the role word (D2: agent orch:init)"},
        {"file": _role_file,
         "old": "You are **orch-init**. A session running the `orch-init` skill (`~/.claude/skills/orch-init/SKILL.md`)",
         "new": "You are **orch:init**. A session running the `orch-init` skill (`${CLAUDE_PLUGIN_ROOT}/skills/orch-init/SKILL.md`)",
         "reason": "orch-init -> orch:init: the agent's own identity; the skill mention on the same line stays"},
        {"file": _role_file,
         "old": "  proposal instead. Never run `install.sh`, deploy, or touch anything outside the repo and",
         "new": "  proposal instead. Never deploy or touch anything outside the repo and",
         "reason": "intended removal: install.sh retired in step 4 (orchestrator decision); the deploy and outside-the-repo rules stay"},
    )

INIT = re.compile(r"orch-init(?![\w-])")
FM_KEYS = ("model", "effort", "tools")


def git(*args):
    r = subprocess.run(("git", "-C", ROOT) + args, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write("text-parity: git %s failed: %s\n" % (" ".join(args), r.stderr.strip()))
        sys.exit(2)
    return r.stdout


def old_path(new):
    """The base twin of a plugin path."""
    rel = new[len("plugin/"):]
    m = re.match(r"(skills/orchestrate/roles|agents)/([\w-]+)\.md$", rel)
    if m and m.group(2) in ROLES:
        return "%s/orch-%s.md" % (m.group(1), m.group(2))
    return rel


def normalize(line, counts, side):
    for kind, rx, rep in TABLE:
        line, n = rx.subn(rep, line)
        if n:
            counts[(kind, side)] += n
    return line


def frontmatter(text):
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    out = {}
    for l in text[4:end if end > 0 else len(text)].split("\n"):
        m = re.match(r"^([\w-]+):\s*(.*?)\s*$", l)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main():
    argv = sys.argv[1:]
    if "--base" not in argv or argv.index("--base") + 1 >= len(argv):
        sys.stderr.write(__doc__)
        return 2
    base = argv[argv.index("--base") + 1]
    verbose = "--verbose" in argv
    git("rev-parse", "--verify", "--quiet", base + "^{commit}")
    new_files = sorted(f for f in git("ls-files", "-co", "--exclude-standard", "plugin/skills", "plugin/agents").split("\n")
                       if f and (f.startswith("plugin/skills/") or re.match(r"plugin/agents/[^/]+\.md$", f))
                       and os.path.isfile(os.path.join(ROOT, f)))  # a tracked file deleted on disk is absent
    old_files = set(f for f in git("ls-tree", "-r", "--name-only", base, "skills", "agents").split("\n")
                    if f and (f.startswith("skills/") or re.match(r"agents/[^/]+\.md$", f)))
    fails = []
    counts = Counter()
    used = Counter()
    kept = []
    allow = {}
    for i, e in enumerate(ALLOWLIST):
        allow.setdefault((e["file"], e["old"], e["new"]), []).append(i)
    paired = set()
    for nf in new_files:
        of = old_path(nf)
        if of not in old_files:
            fails.append("only in the plugin: %s" % nf)
            continue
        paired.add(of)
        with open(os.path.join(ROOT, nf), encoding="utf-8", errors="replace") as fh:
            new_text = fh.read()
        old_text = git("show", "%s:%s" % (base, of))
        if nf.startswith("plugin/agents/"):
            fo, fn = frontmatter(old_text), frontmatter(new_text)
            for k in FM_KEYS:
                if fo.get(k) != fn.get(k):
                    fails.append("%s: frontmatter %s changed (%r -> %r)" % (nf, k, fo.get(k), fn.get(k)))
        a_raw, b_raw = old_text.split("\n"), new_text.split("\n")
        a = [normalize(l, counts, "old") for l in a_raw]
        b = [normalize(l, counts, "new") for l in b_raw]
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                for k in range(i2 - i1):
                    ol, nl = a_raw[i1 + k], b_raw[j1 + k]
                    if INIT.search(ol) and len(INIT.findall(ol)) == len(INIT.findall(nl)):
                        kept.append("%s:%d: %s" % (nf, j1 + k + 1, nl.strip()))
                continue
            for k in range(max(i2 - i1, j2 - j1)):
                ol = a_raw[i1 + k] if k < i2 - i1 else None
                nl = b_raw[j1 + k] if k < j2 - j1 else None
                key = (nf, ol, nl)
                idx = allow.get(key, [])
                if len(idx) > used[key]:
                    used[key] += 1
                else:
                    fails.append("%s:%s: differs after normalizing\n  old: %s\n  new: %s"
                                 % (nf, (j1 + k + 1) if nl is not None else "-", ol, nl))
    for of in sorted(old_files - paired):
        fails.append("only at %s: %s" % (base, of))
    for key, idx in allow.items():
        if used[key] < len(idx):
            fails.append("allowlist entry not used: %s: %r -> %r" % key)

    print("mapping counts (old side / new side):")
    for kind, _, _ in TABLE:
        print("  %-16s %5d / %d" % (kind, counts[(kind, "old")], counts[(kind, "new")]))
    print("allowlisted hunks (%d):" % len(ALLOWLIST))
    for e in ALLOWLIST:
        print("  %s: %s" % (e["file"], e["reason"]))
        if verbose:
            print("    old: %s\n    new: %s" % (e["old"], e["new"]))
    print("kept as skill (%d orch-init lines unchanged on both sides):" % len(kept))
    for k in kept:
        print("  " + k)
    print("files paired: %d" % len(paired))
    for f in fails:
        print("FAIL " + f)
    print("text-parity: %s (%d failures)" % ("ok" if not fails else "DIFFERS", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
