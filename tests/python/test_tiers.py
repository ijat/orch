"""orchlib/tiers.py: tier facts, the basis line, quote matching; pinned to TIERS.md and plugin/agents/*.md.

The hook itself (decide, logging, transcripts on disk) is in test_tierguard.py."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BIN = os.path.join(ROOT, "plugin", "bin")
sys.path.insert(0, BIN)

from orchlib import tiers  # noqa: E402

TIERS_MD = os.path.join(ROOT, "plugin", "skills", "orchestrate", "TIERS.md")
AGENTS = os.path.join(ROOT, "plugin", "agents")
TIER_RE = re.compile(r"`(haiku|sonnet|opus|fable|mythos)/(low|medium|high|xhigh|max)`")
ROLE_KEYS = {"Planner": "orch-planner", "Implementer": "orch-implementer", "Code reviewer": "orch-code-reviewer",
             "Security reviewer": "orch-security-reviewer", "Plan reviewer": "orch-plan-reviewer",
             "Director": "orch-director", "Explorer": "orch-explorer", "Executor": "orch-executor",
             "Graph": "orch-graph", "Init": "orch-init"}
USER_WORDS = re.compile(r"user(?:'s)? (?:asks|accepts|requests?)|a user request|user's request", re.I)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def table_rows(text, header_start):
    """The rows of the markdown table whose header line starts with header_start, as lists of stripped cells."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(header_start):
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                rows.append([c.strip() for c in row.strip().strip("|").split("|")])
            return rows
    raise AssertionError("no table starting %r in TIERS.md" % header_start)


def tier_cells(cell):
    return [(m.group(1), m.group(2)) for m in TIER_RE.finditer(cell)]


def frontmatter(path):
    out = {}
    text = read(path)
    block = text.split("---", 2)[1]
    for line in block.splitlines():
        m = re.match(r"^(model|effort):\s*(\S+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


class Order(unittest.TestCase):
    def test_model_and_effort_order(self):
        self.assertEqual(tiers.MODELS, ("haiku", "sonnet", "opus", "fable", "mythos"))
        self.assertEqual(tiers.EFFORTS, ("low", "medium", "high", "xhigh", "max"))
        self.assertLess(tiers.rank(("sonnet", "max")), tiers.rank(("opus", "low")))   # model first, then effort
        self.assertLess(tiers.rank(("opus", "max")), tiers.rank(("fable", "low")))
        self.assertGreater(tiers.rank(("opus", "xhigh")), tiers.rank(("opus", "high")))

    def test_family(self):
        for model, fam in (("opus", "opus"), ("Opus", "opus"), ("claude-opus-4-1-20250805", "opus"),
                           ("claude-mythos-5-2", "mythos"), ("FABLE", "fable"), ("claude-haiku-4-5", "haiku"),
                           ("sonnet", "sonnet"), ("inherit", None), ("gpt-9", None), (5, None)):
            self.assertEqual(tiers.family(model), fam, model)
        self.assertEqual(tiers.family("haiku-opus"), "opus")    # a mixed string is read as the higher family

    def test_above_default(self):
        a = tiers.above_default
        self.assertFalse(a("orch-planner", None, None))
        self.assertFalse(a("orch-planner", "opus", "high"))
        self.assertFalse(a("orch-planner", "sonnet", "max"))      # below by model
        self.assertFalse(a("orch-planner", None, "medium"))
        self.assertTrue(a("orch-planner", None, "xhigh"))
        self.assertTrue(a("orch-planner", "fable", None))
        self.assertTrue(a("orch-implementer", None, "xhigh"))
        self.assertFalse(a("orch-implementer", "haiku", "max"))
        self.assertTrue(a("orch-planner", "gpt-9", "low"))        # unknown model string
        self.assertTrue(a("orch-planner", "opus", "ultra"))       # unknown effort string
        self.assertTrue(a("orch-security-reviewer-mythos", None, None))
        self.assertTrue(a("orch-security-reviewer-mythos", "opus", "low"))


class Defaults(unittest.TestCase):
    def test_equal_tiers_md_tier_table_and_agent_frontmatter(self):
        rows = table_rows(read(TIERS_MD), "| Role | Default | Allowed, and when")
        from_md = {}
        for cells in rows:
            self.assertEqual(len(cells), 5, cells)
            key = ROLE_KEYS[cells[0]]
            found = tier_cells(cells[1])
            self.assertEqual(len(found), 1, cells[1])
            from_md[key] = found[0]
        self.assertEqual(set(from_md), set(ROLE_KEYS.values()))
        self.assertEqual(tiers.DEFAULTS, from_md)
        seen = set()
        for name in sorted(os.listdir(AGENTS)):
            if not name.endswith(".md") or name == "security-reviewer-mythos.md":
                continue
            fm = frontmatter(os.path.join(AGENTS, name))
            key = "orch-" + name[:-3]
            seen.add(key)
            self.assertEqual((fm["model"], fm["effort"]), tiers.DEFAULTS[key], name)
        self.assertEqual(seen, set(tiers.DEFAULTS))

    def test_mythos_variant(self):
        fm = frontmatter(os.path.join(AGENTS, "security-reviewer-mythos.md"))
        self.assertIn("mythos", fm["model"])
        self.assertNotIn(tiers.MYTHOS, tiers.DEFAULTS)
        self.assertEqual(tiers.default_of(tiers.MYTHOS), tiers.DEFAULTS["orch-security-reviewer"])
        self.assertEqual(tiers.spawn_tier(tiers.MYTHOS, None, None), ("mythos", "high"))
        self.assertTrue(tiers.checked(tiers.MYTHOS))
        self.assertFalse(tiers.checked("orch-unknown"))


class Coverage(unittest.TestCase):
    """A tier TIERS.md allows for a role has a token that reaches it, or the text names the user's request."""

    def reached(self, key, tier):
        return any(key in roles and tiers.rank(tier) <= tiers.rank(roles[key]) for roles in tiers.TOKENS.values())

    def test_tier_table_allowed_column(self):
        rows = table_rows(read(TIERS_MD), "| Role | Default | Allowed, and when")
        checked = 0
        for cells in rows:
            key = ROLE_KEYS[cells[0]]
            if key in ("orch-explorer", "orch-executor", "orch-graph"):
                continue
            default = tiers.DEFAULTS[key]
            for tier in tier_cells(cells[2]):
                if tiers.rank(tier) <= tiers.rank(default):
                    continue
                checked += 1
                self.assertTrue(self.reached(key, tier) or USER_WORDS.search(cells[2]),
                                "%s %s/%s is allowed by TIERS.md but no signal, failure or rule token reaches it "
                                "and its text does not name the user's request" % (cells[0], tier[0], tier[1]))
        self.assertGreaterEqual(checked, 8)      # the instrument ran: planner, reviewers, director, init, fable rows

    def test_implementer_cells(self):
        rows = table_rows(read(TIERS_MD), "| Cell | Verdict | Use when")
        checked = 0
        for cells in rows:
            use = cells[2]
            if use == "—":
                continue
            for tier in tier_cells(cells[0]):
                if tiers.rank(tier) <= tiers.rank(("sonnet", "high")):
                    continue
                checked += 1
                self.assertTrue(self.reached("orch-implementer", tier) or USER_WORDS.search(use + " " + cells[1]),
                                "implementer cell %s/%s has no token and does not name the user's request"
                                % (tier[0], tier[1]))
        self.assertGreaterEqual(checked, 8)

    def test_instrument_catches_a_missing_token(self):
        self.assertFalse(self.reached("orch-planner", ("fable", "xhigh")))
        self.assertTrue(self.reached("orch-planner", ("fable", "high")))
        self.assertFalse(self.reached("orch-init", ("fable", "high")))


class BasisTable(unittest.TestCase):
    """TIERS.md § Record it, Basis: one row per (token, roles, highest tier); the guard's TOKENS must equal it."""

    def md_tokens(self):
        rows = table_rows(read(TIERS_MD), "| Token | Kind | Roles | Highest tier")
        out = {}
        for cells in rows:
            self.assertEqual(len(cells), 6, cells)
            token, kind = cells[0].strip("`"), cells[1]
            top = tier_cells(cells[3])
            self.assertEqual(len(top), 1, cells)
            for role in re.findall(r"`([a-z-]+)`", cells[2]):
                key = (kind, token)
                self.assertNotIn("orch-" + role, out.get(key, {}), "duplicate row %s %s" % (kind, role))
                out.setdefault(key, {})["orch-" + role] = top[0]
        return out

    def test_the_table_equals_the_guard(self):
        self.assertEqual(self.md_tokens(), tiers.TOKENS)

    def test_every_row_names_a_rule_and_a_trigger(self):
        for cells in table_rows(read(TIERS_MD), "| Token | Kind | Roles | Highest tier"):
            self.assertTrue(cells[4], cells)
            self.assertTrue(cells[5] == "\u2014" or re.match(r"^`[a-z_]+`$", cells[5]), cells)

    def test_the_six_forms_are_shown(self):
        text = read(TIERS_MD)
        for form in tiers.FORMS:
            self.assertIn(form.replace("<your own words, 12+ characters>", "<the user's own words, 12+ characters>"), text)

    def test_skill_md_states_the_rule(self):
        text = read(os.path.join(os.path.dirname(TIERS_MD), "SKILL.md"))
        for phrase in ("Auto-memory decides nothing", "orch memory audit", "--basis", "Never pass `--via pane` yourself"):
            self.assertIn(phrase, text)


class BasisForms(unittest.TestCase):
    P = staticmethod(tiers.parse_basis)

    def test_none_without_a_line(self):
        self.assertIsNone(self.P("no basis here"))
        self.assertIsNone(self.P(""))
        self.assertIsNone(self.P(None))
        self.assertIsNone(self.P("  basis: signal | flag"))       # must start the line

    def test_first_line_wins(self):
        b = self.P("intro\nbasis: signal | flag\nbasis: rule | large_diff\n")
        self.assertEqual((b.kind, b.token), ("signal", "flag"))

    def test_signal_rule_failure(self):
        b = self.P("basis: signal | flag")
        self.assertEqual((b.kind, b.token, b.ident), ("signal", "flag", None))
        self.assertEqual(self.P("basis: signal | flag | s3").ident, "s3")
        self.assertEqual(self.P("basis: rule | large_diff").token, "large_diff")
        self.assertEqual(self.P("basis: Failure | prior_tier_failed").kind, "failure")

    def test_user_forms(self):
        b = self.P('basis: user | "use fable for the planner"')
        self.assertEqual((b.kind, b.quote, b.qref), ("user", "use fable for the planner", None))
        b = self.P('basis: user | "use fable | for the planner" | Q4 orch-skill/memory-lessons')
        self.assertEqual(b.quote, "use fable | for the planner")
        self.assertEqual(b.qref, (4, "orch-skill", "memory-lessons"))
        b = self.P('basis: user | "a quote that says | session in the middle"')
        self.assertEqual(b.quote, "a quote that says | session in the middle")
        b = self.P('basis: user | "a quote with "inner" marks"')
        self.assertEqual(b.quote, 'a quote with "inner" marks')

    def test_claude_md(self):
        b = self.P('basis: claude-md | /w/CLAUDE.md | "never ask twice"')
        self.assertEqual((b.path, b.quote), ("/w/CLAUDE.md", "never ask twice"))

    def test_lesson(self):
        self.assertEqual(self.P("basis: lesson | global/2026-11-01-known-answer").name,
                         ("global", "2026-11-01-known-answer"))
        self.assertEqual(self.P("basis: lesson | orch-skill/2026-11-02-x").name, ("orch-skill", "2026-11-02-x"))

    def test_malformed(self):
        for line in ("basis: plan | step 1 | flag", "basis: user", "basis: user |", "basis: user | no quotes",
                     'basis: user | ""', 'basis: user | "q" | session not-a-uuid',
                     'basis: user | "use fable for the planner" | session 11111111-2222-3333-4444-555555555555',
                     'basis: user | "use fable for the planner" | Q4 a/b | session 11111111-2222-3333-4444-555555555555',
                     "basis: signal | Flag", "basis: signal | a | b | c", "basis: signal | flag | step 3", "basis: rule | two words",
                     "basis: lesson | ../2026-11-01-x", "basis: lesson | global/x", "basis: lesson | global/2026-11-01-../x",
                     'basis: claude-md | "only a quote"', 'basis: claude-md | | "q"', "basis: | flag"):
            with self.assertRaises(tiers.BasisError, msg=line):
                self.P(line)


class Quotes(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(tiers.norm("  Use “Fable”  HIGH\n now’s "), 'use "fable" high now\'s')
        self.assertEqual(tiers.norm("Ｕse"), "use")      # NFKC

    def test_fragments_and_order(self):
        frags = tiers.fragments("use fable ... planner now … ok")
        self.assertEqual(frags, ["use fable", "planner now", "ok"])
        text = tiers.norm("Yes, use Fable high for the planner now. ok")
        self.assertTrue(tiers.in_order(frags, text))
        self.assertFalse(tiers.in_order(list(reversed(frags)), text))
        self.assertEqual(tiers.fragments(" ... "), [])

    def test_system_reminder_spans_are_dropped(self):
        self.assertEqual(tiers.norm(tiers.strip_reminders("a <system-reminder>secret\nplan</system-reminder> b")), "a b")
        self.assertEqual(tiers.norm(tiers.strip_reminders("a <system-reminder>never closed")), "a")

    def test_fits(self):
        f = tiers.fits
        self.assertEqual(f("signal", "flag", "orch-implementer", ("opus", "medium")), "ok")
        self.assertEqual(f("signal", "flag", "orch-implementer", ("opus", "high")), "above-ceiling")
        self.assertEqual(f("signal", "flag", "orch-planner", ("opus", "xhigh")), "wrong-role")
        self.assertEqual(f("signal", "nope", "orch-implementer", ("opus", "medium")), "unknown-token")
        self.assertEqual(f("rule", "flag", "orch-implementer", ("opus", "medium")), "unknown-token")   # kind matters
        self.assertEqual(f("signal", "flag", "orch-implementer", (None, "high")), "above-ceiling")


if __name__ == "__main__":
    unittest.main()
