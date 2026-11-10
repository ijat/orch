"""Plugin text layout (C9, T1-T6): how the skills, roles, templates and agents name commands, files, data paths,
agents and mod tools, plus the agent render check. Reads the working tree; writes nothing."""
import glob
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PLUGIN = os.path.join(REPO, "plugin")
SKILLS = os.path.join(PLUGIN, "skills")
ROLES_DIR = os.path.join(SKILLS, "orchestrate", "roles")
AGENTS_DIR = os.path.join(PLUGIN, "agents")
ROLES = ("code-reviewer", "director", "executor", "explorer", "graph", "implementer", "init", "plan-reviewer",
         "planner", "security-reviewer")
NO_INIT = "|".join(sorted((r for r in ROLES if r != "init"), key=len, reverse=True))

LEGACY_AGENT = re.compile(r"(?<![\w.:-])orch-(%s)(?![\w-])" % NO_INIT)
AGENT_WORD = re.compile(r"orch:(\*|[A-Za-z][\w-]*)")
MARKERS = {"begin", "end"}  # `<!-- orch:begin` / `<!-- orch:end` in the CLAUDE.md block template
PLUGIN_ROOT = "${CLAUDE_PLUGIN_ROOT}"
ROOT_PATH = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}(/[^\s`'\")<{…,;]*)?")
REMOVED_TOOLS = re.compile(r"orch-build-agents|sync-live|export\.py|install\.sh|orch-build-hook")
DATA_PATH = re.compile(r"~/\.claude/orch-data(/[\w.-]*)?")
DATA_TOP = {"tasks", "logs", "cache", "bin", "private", "backups", "plugin", "README.md"}
BARE_CMD = re.compile(r"(?<![\w./-])((?:orch-[\w-]+|role-cost)\.(?:py|sh))(?![\w.-])")

# History lines (D2: old names stay in history): they describe records or agent files from before the rename.
HISTORY = (
    ("skills/orchestrate/SKILL.md", "Records before 2026-10-08 name tiers by agent file"),
    ("skills/orchestrate/SKILL.md", "`…-max` / `…-opus-max` → `opus/max`, `…-fable[-max]` → `fable/max`;"),
)


def instruction_files():
    """Every text file of the skills and every agent: (path relative to plugin/, text)."""
    out = []
    for path in sorted(glob.glob(os.path.join(SKILLS, "**", "*"), recursive=True)) + sorted(
            glob.glob(os.path.join(AGENTS_DIR, "*.md"))):
        if os.path.isfile(path) and "__pycache__" not in path and not path.endswith(".DS_Store"):
            with open(path, encoding="utf-8") as fh:
                out.append((os.path.relpath(path, PLUGIN), fh.read()))
    return out


def full_path_file(rel):
    """Files that name skill files by `${CLAUDE_PLUGIN_ROOT}` paths: each SKILL.md and the role/agent bodies."""
    return (re.match(r"skills/[^/]+/SKILL\.md$", rel) is not None or rel.startswith("skills/orchestrate/roles/")
            or rel.startswith("agents/"))


def hits(files, rx, keep=lambda rel, line: False):
    out = []
    for rel, text in files:
        for n, line in enumerate(text.split("\n"), 1):
            if rx.search(line) and not keep(rel, line):
                out.append("%s:%d: %s" % (rel, n, line.strip()[:160]))
    return out


def history(rel, line):
    return any(rel == f and frag in line for f, frag in HISTORY)


class Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = instruction_files()
        cls.assertTrue(cls, len(cls.files) > 50)  # instrument check: the walk found the tree

    def test_t1_commands_are_bare_names_on_path(self):
        self.assertEqual(hits(self.files, re.compile(r"~/\.claude/bin/")), [])
        self.assertEqual(hits(self.files, re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/bin/")), [])
        named = set()
        for _, text in self.files:
            named.update(m.group(1) for m in BARE_CMD.finditer(text))
        self.assertIn("orch-program-check.py", named)  # instrument check: the pattern sees the commands
        missing = sorted(c for c in named if not os.access(os.path.join(PLUGIN, "bin", c), os.X_OK))
        self.assertEqual(missing, [], "commands named in the text that plugin/bin does not provide")

    def test_t2_plugin_root_paths_only_in_skill_md_and_role_bodies_and_only_for_existing_files(self):
        wrong_place, not_a_file = [], []
        for rel, text in self.files:
            for m in ROOT_PATH.finditer(text):
                where = "%s: %s" % (rel, m.group(0))
                if not full_path_file(rel):
                    wrong_place.append(where)
                    continue
                tail = m.group(1) or ""
                if not tail.startswith("/skills/") or not os.path.exists(PLUGIN + tail):
                    not_a_file.append(where)
        self.assertEqual(wrong_place, [])
        self.assertEqual(not_a_file, [])

    def test_t3_other_skill_files_use_paths_relative_to_the_skill_dir(self):
        rel_ref = re.compile(r"(?<![\w./{}-])((?:\.\./\.\./agents|templates|references)/[\w.-]+\.md|\.\./\.\./agents/[\w.-]+\.md)")
        broken = []
        for rel, text in self.files:
            if full_path_file(rel):
                continue
            self.assertNotIn(PLUGIN_ROOT, text, rel)
            skill_dir = os.path.join(PLUGIN, *rel.split("/")[:2])
            for m in rel_ref.finditer(text):
                if not os.path.exists(os.path.join(skill_dir, m.group(1))):
                    broken.append("%s: %s" % (rel, m.group(1)))
        self.assertEqual(broken, [])
        skill = dict(self.files)["skills/orchestrate/SKILL.md"]
        self.assertEqual(skill.count("a relative path is relative to this skill's directory"), 1)

    def test_t4_no_old_install_paths_and_data_paths_under_orch_data(self):
        self.assertEqual(hits(self.files, re.compile(r"~/\.claude/(bin|skills|agents)/|~/\.claude/orchestrations")), [])
        odd = []
        for rel, text in self.files:
            for m in DATA_PATH.finditer(text):
                top = (m.group(1) or "/").split("/")[1]
                if top not in DATA_TOP:
                    odd.append("%s: %s" % (rel, m.group(0)))
        self.assertEqual(odd, [])

    def test_t5_agent_files_names_and_tokens(self):
        for d in (ROLES_DIR, AGENTS_DIR):
            stems = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(d, "*.md")))
            self.assertEqual(stems, sorted(ROLES), d)
            for r in ROLES:
                with open(os.path.join(d, r + ".md"), encoding="utf-8") as fh:
                    head = fh.read().split("\n---", 1)[0]
                self.assertRegex(head, r"(?m)^name: %s$" % re.escape(r), "%s/%s.md" % (d, r))
        unknown = []
        for rel, text in self.files:
            for m in AGENT_WORD.finditer(text):
                w = m.group(1)
                if w not in ROLES and w != "*" and w not in MARKERS:
                    unknown.append("%s: %s" % (rel, m.group(0)))
        self.assertEqual(unknown, [], "orch:<x> where x is no role")
        self.assertEqual(hits(self.files, re.compile(r"mcp__orch-(pane|ui)__")), [])
        skill = dict(self.files)["skills/orchestrate/SKILL.md"]
        for tool in ("mcp__orch__compact_when_idle", "mcp__orch__set_message_format"):
            self.assertIn(tool, skill)

    def test_t5_init_agent_never_where_the_skill_or_a_path_is_meant(self):
        bad = re.compile(r"orch:orch-|/orch:init|orch:init(/|\.md)|`?orch:init`?\s+skill|skills/orch:init")
        self.assertEqual(hits(self.files, bad), [])
        self.assertIn("name: orch-init\n", dict(self.files)["skills/orch-init/SKILL.md"])
        self.assertTrue(hits(self.files, re.compile(r"`orch:init` agent")))  # instrument check: the agent is named

    def test_t6_no_legacy_agent_names_role_words_kept_removed_tools_gone(self):
        self.assertEqual(hits(self.files, LEGACY_AGENT, history), [])
        self.assertTrue(hits(self.files, LEGACY_AGENT))  # instrument check: the history lines are seen and kept
        self.assertEqual(hits(self.files, re.compile(r"\brole orch[:-]")), [], "orch role takes role words")
        self.assertTrue(hits(self.files, re.compile(r"\brole planner\b")))
        self.assertEqual(hits(self.files, REMOVED_TOOLS), [])

    def test_agents_are_rendered_from_roles(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "build-agents.py"), ROLES_DIR, AGENTS_DIR,
                            "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
