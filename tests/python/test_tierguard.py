"""orch hook tier: the PreToolUse hook that keeps orch:explorer, orch:executor and orch:graph on haiku/low.

decide() is called with explicit env dicts; the log goes to a per-test ORCH_DATA; the CLI path runs the real
launcher with a scrubbed env."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin")
sys.path.insert(0, BIN)

from orchlib import tierguard, tiers  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "transcripts")


def payload(sub="orch:explorer", tool="Agent", **tool_input):
    tool_input.setdefault("prompt", "SECRET_PROMPT")   # where the real payload carries it
    if sub is not None:
        tool_input["subagent_type"] = sub
    return {"tool_name": tool, "tool_input": tool_input, "session_id": "s1"}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-tier-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = os.environ.get("ORCH_DATA")
        os.environ["ORCH_DATA"] = self.tmp

        def restore():
            if saved is None:
                os.environ.pop("ORCH_DATA", None)
            else:
                os.environ["ORCH_DATA"] = saved
        self.addCleanup(restore)

    def reason(self, p, env=None):
        out = tierguard.decide(p, env or {})
        if out is None:
            return None
        h = out["hookSpecificOutput"]
        self.assertEqual(h["hookEventName"], "PreToolUse")
        self.assertEqual(h["permissionDecision"], "deny")
        return h["permissionDecisionReason"]

    def allowed(self, p, env=None):
        self.assertIsNone(tierguard.decide(p, env or {}))

    def denied(self, p, env=None):
        r = self.reason(p, env)
        self.assertIsNotNone(r)
        return r


class Allowed(Case):
    def test_rows(self):
        self.allowed(payload())
        self.allowed(payload(model="haiku"))
        self.allowed(payload(model="haiku", effort="low"))
        self.allowed(payload(model="Haiku", effort="LOW"))
        self.allowed(payload(model="claude-haiku-4-5", effort="low"))
        self.allowed(payload(model="sonnet", effort="low"))
        self.allowed(payload(effort="low"))

    def test_every_guarded_role(self):
        for role in ("orch:explorer", "orch:executor", "orch:graph"):
            self.allowed(payload(role, model="haiku", effort="low"))
            self.denied(payload(role, model="opus"))

    def test_task_tool_name(self):
        self.allowed(payload(tool="Task", model="haiku"))
        self.denied(payload(tool="Task", model="opus"))

    def test_env_haiku_is_fine(self):
        self.allowed(payload(), {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"})
        self.allowed(payload(), {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-4-5"})


class Denied(Case):
    def test_models(self):
        for m in ("opus", "Opus", "fable", "claude-opus-4-1", "claude-opus-4-1-20250805", "inherit", "gpt-9"):
            r = self.denied(payload(model=m, effort="low"))
            self.assertIn("orch:explorer", r)
            self.assertIn(m, r)
            self.assertIn('model: "haiku", effort: "low"', r)

    def test_sonnet_needs_low(self):
        r = self.denied(payload(model="sonnet"))
        self.assertIn('effort: "low"', r)
        self.assertNotIn('Respawn with model: "haiku"', r)   # a Haiku-ban caller must not be steered to haiku
        self.denied(payload(model="sonnet", effort="max"))
        self.denied(payload(model="sonnet", effort="high"))
        self.denied(payload(model="claude-sonnet-4-5", effort="medium"))

    def test_effort_above_low(self):
        for e in ("medium", "high", "xhigh", "max", "HIGH"):
            r = self.denied(payload(model="haiku", effort=e))
            self.assertIn(e, r)
        self.denied(payload(effort="high"))

    def test_env_model_when_spawn_omits_model(self):
        r = self.denied(payload(), {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"})
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL=sonnet", r)
        self.denied(payload(effort="low"), {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-4-1"})

    def test_env_model_ignored_when_spawn_sets_model(self):
        self.allowed(payload(model="haiku"), {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"})

    def test_env_force(self):
        for v in ("1", "true", "yes"):
            r = self.denied(payload(model="haiku", effort="low"), {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": v})
            self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", r)
        for v in ("", "0", "false"):
            self.allowed(payload(model="haiku"), {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": v})

    def test_denial_is_logged_without_the_prompt(self):
        self.denied(payload(model="opus"))
        with open(os.path.join(self.tmp, "logs", "tier-guard.jsonl")) as f:
            text = f.read()
        rec = json.loads(text.splitlines()[0])
        self.assertEqual((rec["decision"], rec["role"], rec["model"], rec["code"]),
                         ("deny", "orch:explorer", "opus", "model"))
        self.assertNotIn("SECRET_PROMPT", text)

    def test_allow_is_not_logged(self):
        self.allowed(payload(model="haiku"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "logs", "tier-guard.jsonl")))


class OptOut(Case):
    def test_off_allows_everything(self):
        env = {"ORCH_TIER_GUARD": "0", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "CLAUDE_CODE_SUBAGENT_MODEL": "opus"}
        self.allowed(payload(model="opus", effort="max"), env)

    def test_other_values_do_not_disable(self):
        self.denied(payload(model="opus"), {"ORCH_TIER_GUARD": "1"})
        self.denied(payload(model="opus"), {"ORCH_TIER_GUARD": ""})


class NotGuarded(Case):
    def test_other_subagents(self):
        self.allowed(payload("general-purpose", model="opus", effort="max"))
        self.allowed(payload("orch:implementer", model="sonnet", effort="high"))   # at default: the escalation check
        self.allowed(payload("orch:planner", model="opus", effort="high"))         # lets it through untouched
        self.allowed(payload("foo:explorer", model="opus"))
        self.allowed(payload(None, model="opus"))

    def test_other_tools(self):
        self.allowed(payload(tool="Bash", model="opus"))
        self.allowed({"tool_name": "Read", "tool_input": {"file_path": "/x"}})

    def test_old_role_names(self):
        self.denied(payload("orch-explorer", model="opus"))
        self.denied(payload("orch-executor", effort="high"))
        self.allowed(payload("orch-graph", model="haiku", effort="low"))

    def test_nested_call_payload(self):
        p = payload(model="opus")
        p.update({"agent_id": "a1", "agent_type": "orch:implementer"})
        self.denied(p)


class FailOpen(Case):
    def run_hook(self, text):
        out = io.StringIO()
        rc = tierguard.run(io.StringIO(text), out)
        return rc, out.getvalue()

    def test_malformed(self):
        for text in ("", "not json", "[]", "null", '{"tool_name": "Agent"}',
                     '{"tool_name": "Agent", "tool_input": "x"}',
                     '{"tool_name": "Agent", "tool_input": {"subagent_type": "orch:explorer", "model": 5}}',
                     '{"tool_name": "Agent", "tool_input": {"subagent_type": ["a"]}}'):
            self.assertEqual(self.run_hook(text), (0, ""), text)

    def test_deny_through_run(self):
        saved = {k: os.environ.pop(k, None) for k in ("ORCH_TIER_GUARD", "CLAUDE_CODE_SUBAGENT_MODEL",
                                                       "CLAUDE_CODE_SUBAGENT_MODEL_FORCE")}
        self.addCleanup(lambda: os.environ.update({k: v for k, v in saved.items() if v is not None}))
        rc, out = self.run_hook(json.dumps(payload(model="opus")))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")


class Launcher(Case):
    def run_cli(self, text, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("ORCH_", "CLAUDE_CODE_SUBAGENT"))}
        base.update(ORCH_DATA=self.tmp, **env)
        return subprocess.run([sys.executable, os.path.join(BIN, "orch"), "hook", "tier"], input=text,
                              capture_output=True, text=True, env=base)

    def test_deny_and_allow(self):
        res = self.run_cli(json.dumps(payload(model="opus")))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        res = self.run_cli(json.dumps(payload(model="haiku", effort="low")))
        self.assertEqual((res.returncode, res.stdout), (0, ""))
        res = self.run_cli(json.dumps(payload(model="opus")), ORCH_TIER_GUARD="0")
        self.assertEqual((res.returncode, res.stdout), (0, ""))

    def test_escalation_through_the_launcher(self):
        p = payload("orch:planner", model="fable", effort="high", prompt="no basis here")
        res = self.run_cli(json.dumps(p))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        p["tool_input"]["prompt"] = "basis: failure | need_escalate\nplan it"
        res = self.run_cli(json.dumps(p))
        self.assertEqual((res.returncode, res.stdout), (0, ""))
        res = self.run_cli(json.dumps(payload("orch:planner", model="fable", effort="high")), ORCH_TIER_GUARD="0")
        self.assertEqual((res.returncode, res.stdout), (0, ""))

    def test_malformed_stdin(self):
        res = self.run_cli("garbage")
        self.assertEqual((res.returncode, res.stdout), (0, ""))

    def test_hooks_json_wires_it(self):
        with open(os.path.join(BIN, "..", "hooks", "hooks.json")) as f:
            hooks = json.load(f)["hooks"]["PreToolUse"]
        self.assertEqual([h["matcher"] for h in hooks], ["Agent|Task", "AskUserQuestion", "Bash"])
        self.assertEqual(hooks[0]["hooks"][0]["command"], '"${CLAUDE_PLUGIN_ROOT}/bin/orch" hook tier')


# ---- the escalation check ---------------------------------------------------------------------------------------

TEXT = "yes, use fable high for the planner now"            # what the fixtures' main text fields hold
TEXT2 = "also keep the reviewer at opus xhigh please"
QUOTE = "use fable high for the planner"
ASK = "Fable high for the planner"
UUID = "11111111-2222-3333-4444-555555555555"
ROLES = ("planner", "implementer", "code-reviewer", "security-reviewer", "plan-reviewer", "director", "init")
DEFAULT = {"planner": "opus/high", "implementer": "sonnet/high", "code-reviewer": "opus/high",
           "security-reviewer": "opus/high", "plan-reviewer": "sonnet/high", "director": "opus/high",
           "init": "sonnet/high"}
S_IMPL = ("implementer",)
S_FABLE = ("implementer", "code-reviewer", "security-reviewer", "plan-reviewer", "director")
# (kind, token, roles, highest tier TIERS.md allows) -- written out here on purpose, not read from tiers.TOKENS
ROUTES = [
    ("signal", "flag", S_IMPL, "opus/medium"), ("signal", "unknown_cause", S_IMPL, "opus/medium"),
    ("signal", "refusal", S_IMPL, "opus/medium"), ("signal", "lite", S_IMPL, "opus/medium"),
    ("signal", "boundary_declined", S_IMPL, "opus/high"), ("signal", "lite_edge", S_IMPL, "opus/high"),
    ("failure", "class_repeat", S_IMPL, "opus/medium"), ("failure", "stuck", S_IMPL, "opus/medium"),
    ("failure", "diligence", S_IMPL, "opus/high"), ("failure", "ability", S_IMPL, "opus/high"),
    ("failure", "fix_cycle", S_IMPL, "opus/max"),
    ("failure", "prior_tier_failed", S_IMPL, "opus/max"), ("failure", "prior_tier_failed", ("planner",), "fable/high"),
    ("failure", "kept_rung", S_IMPL, "fable/high"),
    ("failure", "opus_max_failed", S_FABLE, "fable/high"),
    ("failure", "need_escalate", ("planner",), "fable/high"), ("failure", "premise_broke", ("planner",), "fable/high"),
    ("rule", "shape_roadmap", ("planner",), "fable/high"),
    ("rule", "large_diff", ("code-reviewer", "security-reviewer"), "opus/xhigh"),
    ("rule", "sensitive_diff", ("code-reviewer", "security-reviewer"), "opus/xhigh"),
    ("rule", "sensitive_milestone", ("plan-reviewer",), "opus/medium"),
    ("rule", "no_reference", ("init",), "opus/medium"),
    ("rule", "verdict_reversed", ("director",), "opus/max"),
    ("rule", "capability", S_FABLE, "fable/high"), ("rule", "measured", S_FABLE, "fable/high"),
    ("rule", "urgency", S_FABLE, "fable/high"),
]
# the no-opus cap cells (TIERS.md § Caps and pressure): the same tokens at a lower tier
CAP_ROUTES = [("signal", "flag", "sonnet/xhigh"), ("signal", "unknown_cause", "sonnet/xhigh"),
              ("signal", "refusal", "sonnet/xhigh"), ("failure", "class_repeat", "sonnet/xhigh"),
              ("failure", "stuck", "sonnet/xhigh"), ("failure", "prior_tier_failed", "sonnet/max")]


def split_tier(tier):
    m, e = tier.split("/")
    return m, e


def step_above(tier):
    """The next tier in the guard's order (model first, then effort)."""
    m, e = split_tier(tier)
    if e != "max":
        return "%s/%s" % (m, tiers.EFFORTS[tiers.EFFORTS.index(e) + 1])
    return "%s/low" % tiers.MODELS[tiers.MODELS.index(m) + 1]


def lowest_above(role):
    return step_above(DEFAULT[role])


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".jsonl"), encoding="utf-8") as fh:
        return json.loads(fh.readline())


class Escalation(Case):
    def setUp(self):
        Case.setUp(self)
        self.tasks = os.path.join(self.tmp, "tasks")
        os.makedirs(self.tasks)
        for var, val in (("ORCH_DIR", self.tasks), ("ORCH_TIER_GUARD", None)):
            saved = os.environ.get(var)
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
            self.addCleanup(lambda v=var, s=saved: os.environ.pop(v, None) if s is None else os.environ.__setitem__(v, s))
        self.sleeps = []
        saved_sleep = tiers.SLEEP
        tiers.SLEEP = self.sleeps.append
        self.addCleanup(setattr, tiers, "SLEEP", saved_sleep)

    # helpers
    def transcript(self, *entries, name="main.jsonl"):
        path = os.path.join(self.tmp, "sessions", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e if isinstance(e, dict) else fixture(e)) + "\n")
        return path

    def instructions_entry(self, *paths):
        """The real `instructions` attachment line, with the listed files replaced by these paths."""
        e = fixture("instructions")
        e["attachment"]["files"] = [dict(e["attachment"]["files"][0], path=p, type="Project") for p in paths]
        return e

    def spawn(self, role, tier, basis, transcript=None):
        model, effort = split_tier(tier)
        prompt = ("%s\n" % basis if basis is not None else "") + "tier: opus/high\nSECRET_PROMPT"
        p = payload("orch:" + role, model=model, effort=effort, prompt=prompt)
        if transcript:
            p["transcript_path"] = transcript
        return p

    def user(self, quote=QUOTE, extra=""):
        return 'basis: user | "%s"%s' % (quote, extra)

    def planner_user(self, entries, quote=QUOTE, extra=""):
        return self.spawn("planner", "fable/high", self.user(quote, extra), self.transcript(*entries))

    def with_transcript(self, p, path):
        p["transcript_path"] = path
        return p

    def log_records(self):
        path = os.path.join(self.tmp, "logs", "tier-guard.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [json.loads(line) for line in fh]


class AtOrBelowDefault(Escalation):
    def test_never_refused_nor_logged_nor_delayed(self):
        for role in ROLES:
            d = DEFAULT[role]
            m, e = split_tier(d)
            self.allowed(payload("orch:" + role))
            self.allowed(payload("orch:" + role, model=m, effort=e))
            self.allowed(payload("orch:" + role, model="claude-%s-4-1" % m, effort=e.upper()))
            self.allowed(payload("orch:" + role, effort="low"))
            self.allowed(payload("orch:" + role, model="haiku", effort="max"))
        self.allowed(payload("orch:planner", model="sonnet", effort="max"))
        self.assertEqual(self.log_records(), [])
        self.assertEqual(self.sleeps, [])

    def test_old_role_names(self):
        self.denied(payload("orch-planner", model="fable", effort="high"))
        self.allowed(payload("orch-planner", model="opus", effort="high"))

    def test_the_tier_comes_from_the_call_not_the_prompt(self):
        self.allowed(payload("orch:planner", model="opus", effort="high", prompt="tier: fable/high"))
        self.denied(payload("orch:planner", model="fable", effort="high", prompt="tier: opus/high"))

    def test_unknown_strings_count_as_above(self):
        self.denied(payload("orch:planner", model="gpt-9"))
        self.denied(payload("orch:planner", model="opus", effort="ultra"))
        self.denied(payload("orch:planner", model="inherit"))

    def test_non_string_values_fail_open(self):
        self.allowed({"tool_name": "Agent", "tool_input": {"subagent_type": "orch:planner", "model": 5}})

    def test_guard_off(self):
        self.allowed(payload("orch:planner", model="fable", effort="max"), {"ORCH_TIER_GUARD": "0"})
        self.assertEqual(self.log_records(), [])


class RouteWalk(Escalation):
    def test_every_route_at_its_ceiling_and_lowest_above(self):
        count = 0
        for kind, token, roles, ceiling in ROUTES:
            for role in roles:
                for tier in {ceiling, lowest_above(role)}:
                    basis = "basis: %s | %s" % (kind, token)
                    self.allowed(self.spawn(role, tier, basis))
                    count += 1
        self.assertGreater(count, 50)

    def test_the_cap_cells(self):
        for kind, token, tier in CAP_ROUTES:
            self.allowed(self.spawn("implementer", tier, "basis: %s | %s" % (kind, token)))

    def test_signal_with_an_id(self):
        self.allowed(self.spawn("implementer", "opus/medium", "basis: signal | flag | s3"))
        self.allowed(self.spawn("implementer", "opus/medium", "basis: signal | unknown_cause | F2"))

    def test_one_step_above_the_ceiling_is_denied(self):
        for kind, token, roles, ceiling in ROUTES:
            for role in roles:
                r = self.denied(self.spawn(role, step_above(ceiling), "basis: %s | %s" % (kind, token)))
                self.assertIn("allows orch:%s at most %s" % (role, ceiling.replace("/", "/")), r)

    def test_a_token_on_another_role_is_denied(self):
        for kind, token, roles, ceiling in ROUTES:
            for role in ROLES:
                if role in roles:
                    continue
                if any(k == kind and t == token and role in rs for k, t, rs, _ in ROUTES):
                    continue
                r = self.denied(self.spawn(role, lowest_above(role), "basis: %s | %s" % (kind, token)))
                self.assertIn("does not apply to orch:%s" % role, r)

    def test_unknown_token_and_the_wrong_kind(self):
        self.assertIn("not a token", self.denied(self.spawn("implementer", "opus/medium", "basis: signal | because")))
        self.assertIn("not a token", self.denied(self.spawn("implementer", "opus/medium", "basis: rule | flag")))
        self.assertIn("not a token", self.denied(self.spawn("implementer", "opus/medium", "basis: failure | flag")))

    def test_the_lowest_above_default_is_in_the_walk_for_every_role(self):
        self.assertEqual({r: lowest_above(r) for r in ROLES}, {
            "planner": "opus/xhigh", "implementer": "sonnet/xhigh", "code-reviewer": "opus/xhigh",
            "security-reviewer": "opus/xhigh", "plan-reviewer": "sonnet/xhigh", "director": "opus/xhigh",
            "init": "sonnet/xhigh"})

    def test_user_for_the_tiers_no_token_reaches(self):
        entries = ("typed",)
        for tier in ("opus/xhigh", "fable/xhigh", "fable/high", "opus/max"):
            self.allowed(self.spawn("planner", tier, self.user(), self.transcript(*entries)))
        self.allowed(self.spawn("implementer", "fable/xhigh", self.user(), self.transcript(*entries)))
        self.allowed(self.spawn("security-reviewer", "mythos/high", self.user(), self.transcript(*entries)))
        self.allowed(self.with_transcript(payload("orch:security-reviewer-mythos", prompt=self.user()),
                                          self.transcript(*entries)))

    def test_lesson_and_claude_md_are_checked_against_the_role_and_capped(self):
        os.makedirs(os.path.join(self.tasks, "lessons"))
        for role in ROLES:
            with open(os.path.join(self.tasks, "lessons", "2026-11-01-%s.md" % role), "w") as fh:
                fh.write("# t\n\n- status: open\n- role: orchestrator, %s\n- scope: global\n" % role)
        md = os.path.join(self.tmp, "CLAUDE.md")
        with open(md, "w") as fh:
            fh.write("# Rules\n\nSpawn the planner on fable high when the user asks for a roadmap.\n")
        tr = self.transcript(self.instructions_entry(md))
        for role in ROLES:
            self.allowed(self.spawn(role, "fable/high", "basis: lesson | global/2026-11-01-" + role))
            p = self.spawn(role, "fable/high", 'basis: claude-md | %s | "planner on fable high when the user asks"' % md)
            self.allowed(self.with_transcript(p, tr))
            # above the ceiling only a user basis counts
            r = self.denied(self.spawn(role, "fable/xhigh", "basis: lesson | global/2026-11-01-" + role))
            self.assertIn("at most fable/high", r)
            p = self.spawn(role, "fable/max", 'basis: claude-md | %s | "planner on fable high when the user asks"' % md)
            self.denied(self.with_transcript(p, tr))
        self.allowed(self.spawn("security-reviewer", "mythos/high", self.user(), self.transcript("typed")))


class Refusal(Escalation):
    def test_no_basis(self):
        r = self.denied(self.spawn("planner", "fable/high", None))
        self.assertIn("orch:planner", r)
        self.assertIn("fable/high", r)
        self.assertIn("no `basis:` line", r)

    def test_message_shows_the_six_forms_and_the_default(self):
        r = self.denied(self.spawn("planner", "fable/high", None))
        for form in ("basis: user |", "basis: signal |", "basis: rule |", "basis: failure |", "basis: lesson |",
                     "basis: claude-md |"):
            self.assertEqual(len([ln for ln in r.splitlines() if ln.strip().startswith(form)]), 1, form)
        self.assertIn("Or spawn at the default tier (opus/high).", r)
        self.assertNotIn("ORCH_TIER_GUARD", r)
        self.assertNotIn("basis: plan", r)
        r = self.denied(self.spawn("implementer", "opus/high", None))
        self.assertIn("(sonnet/high)", r)

    def test_malformed_basis(self):
        for basis in ("basis: plan | step 1 | flag", "basis: user | unquoted words here", "basis: ",
                      "basis: signal", "basis: rule | two words", "basis: lesson | ../etc"):
            r = self.denied(self.spawn("planner", "fable/high", basis))
            self.assertIn("malformed", r, basis)

    def test_basis_must_start_a_line_and_the_first_one_counts(self):
        self.denied(self.spawn("planner", "fable/high", "   basis: failure | need_escalate"))
        p = self.spawn("planner", "fable/high", "basis: signal | flag")
        p["tool_input"]["prompt"] += "\nbasis: failure | need_escalate\n"
        r = self.denied(p)
        self.assertIn("does not apply to orch:planner", r)

    def test_denials_and_allows_are_logged_without_the_prompt(self):
        self.denied(self.spawn("planner", "fable/high", None))
        self.allowed(self.spawn("planner", "fable/high", "basis: failure | need_escalate"))
        with open(os.path.join(self.tmp, "logs", "tier-guard.jsonl")) as fh:
            text = fh.read()
        self.assertNotIn("SECRET_PROMPT", text)
        deny, allow = [json.loads(line) for line in text.splitlines()]
        self.assertEqual((deny["decision"], deny["code"], deny["role"], deny["model"], deny["effort"], deny["basis"]),
                         ("deny", "no-basis", "orch:planner", "fable", "high", None))
        self.assertEqual((allow["decision"], allow["code"], allow["basis_kind"], allow["basis"], allow["session_id"]),
                         ("allow", "ok", "failure", "basis: failure | need_escalate", "s1"))
        self.assertIn("ts", allow)
        self.assertIn("caller", allow)

    def test_the_basis_line_is_clipped_to_300_characters(self):
        self.denied(self.spawn("planner", "fable/high", 'basis: user | "%s"' % ("x" * 500)))
        rec = self.log_records()[0]
        self.assertEqual(len(rec["basis"]), 300)

    def test_the_cheap_role_log_line_is_unchanged(self):
        self.denied(payload(model="opus"))
        rec = self.log_records()[0]
        self.assertEqual(sorted(rec), ["caller", "code", "decision", "effort", "model", "role", "session_id", "ts"])


class MythosVariant(Escalation):
    def test_always_above_and_user_only(self):
        sub = "orch:security-reviewer-mythos"
        self.denied(payload(sub, prompt="no basis"))
        for basis in ("basis: rule | large_diff", "basis: rule | sensitive_diff", "basis: signal | flag",
                      "basis: failure | opus_max_failed", "basis: lesson | global/2026-11-01-x",
                      'basis: claude-md | /w/CLAUDE.md | "quote that is long enough"'):
            r = self.denied(payload(sub, prompt=basis, model="opus", effort="high"))
            self.assertIn("`user` basis only", r, basis)
        self.allowed(self.with_transcript(payload(sub, prompt=self.user()), self.transcript("typed")))
        self.allowed(self.with_transcript(payload(sub, prompt=self.user(), model="opus", effort="high"),
                                          self.transcript("typed")))

    def test_off_switch(self):
        self.allowed(payload("orch:security-reviewer-mythos", prompt="x"), {"ORCH_TIER_GUARD": "0"})


class UserQuoteSources(Escalation):
    def check(self, entries, quote=QUOTE, extra=""):
        return tierguard.decide(self.planner_user(entries, quote, extra), {})

    def test_allowed_sources(self):
        cases = [("typed", QUOTE, "typed"), ("sdk_prompt", QUOTE, "typed"), ("typed_queued", QUOTE, "typed"),
                 ("queued_human", QUOTE, "queued"), ("typed_list", "reviewer at opus xhigh", "typed")]
        for name, quote, source in cases:
            self.assertIsNone(self.check([name], quote), name)
            self.assertEqual(self.log_records()[-1]["found"], source, name)

    def test_the_same_words_elsewhere_are_not_the_users(self):
        for name in ("queued_peer", "queued_task_notification", "handback_peer", "handback_task", "plugin_prompt",
                     "skill_body", "tool_result", "instructions", "compact_summary"):
            out = self.check([name])
            self.assertIsNotNone(out, name)
            self.assertIn("not found", out["hookSpecificOutput"]["permissionDecisionReason"], name)

    def test_the_fixtures_hold_the_words(self):
        """Without this a refusal above could be an empty fixture."""
        for name in ("typed", "sdk_prompt", "typed_queued", "queued_human", "queued_peer", "queued_task_notification",
                     "handback_peer", "handback_task", "plugin_prompt", "skill_body", "tool_result", "instructions",
                     "compact_summary"):
            self.assertIn(QUOTE, json.dumps(fixture(name)), name)
        self.assertIn("reviewer at opus xhigh", json.dumps(fixture("typed_list")))

    def test_a_system_reminder_span_is_dropped(self):
        e = fixture("typed")
        e["message"]["content"] = "<system-reminder>%s</system-reminder> go ahead and plan" % TEXT
        self.assertIsNotNone(self.check([e]))
        e["message"]["content"] = "<system-reminder>memory</system-reminder> %s" % TEXT
        self.assertIsNone(self.check([e]))

    def test_a_typed_turn_before_a_compact_summary_still_counts(self):
        self.assertIsNone(self.check(["typed", "compact_summary"]))
        self.assertIsNotNone(self.check(["compact_summary"]))

    def test_a_queued_command_needs_a_human_origin_and_prompt_mode(self):
        for field, value in (("commandMode", "task-notification"), ("origin", None), ("origin", {"kind": "peer"})):
            e = fixture("queued_human")
            e["attachment"][field] = value
            self.assertIsNotNone(self.check([e]), field)
        e = fixture("queued_human")
        del e["attachment"]["origin"]
        self.assertIsNotNone(self.check([e]))
        e = fixture("queued_human")
        e["attachment"]["type"] = "instructions"
        self.assertIsNotNone(self.check([e]))

    def test_a_user_entry_needs_a_human_or_sdk_origin(self):
        e = fixture("typed")
        e["origin"] = {"kind": "peer"}
        self.assertIsNotNone(self.check([e]))
        del e["origin"]
        self.assertIsNotNone(self.check([e]))           # no origin and not a -p prompt: a slash-command echo
        e["promptSource"] = "sdk"
        self.assertIsNone(self.check([e]))

    def test_sidechain_meta_and_summary_flags_exclude(self):
        for flag in ("isSidechain", "isMeta", "isCompactSummary"):
            e = fixture("typed")
            e[flag] = True
            self.assertIsNotNone(self.check([e]), flag)

    def test_short_quotes(self):
        self.assertIsNotNone(self.check(["typed"], "yes"))                  # typed text needs 12+ characters
        self.assertIsNotNone(self.check(["typed"], "use fable"))
        self.assertIsNone(self.check(["ask_answer"], "yes"))                # an exact answer does not
        self.assertIsNone(self.check(["ask_answer"], ASK))
        self.assertIsNone(self.check(["ask_answer"], "high for the planner"))   # 12+ characters contained
        self.assertIsNotNone(self.check(["ask_answer"], "high for"))
        self.assertIsNotNone(self.check(["ask_answer"], "no"))

    def test_an_ask_answer_only_counts_as_an_answer(self):
        e = fixture("ask_answer")
        del e["toolUseResult"]["questions"]
        self.assertIsNotNone(self.check([e], ASK))
        e = fixture("ask_answer")
        e["toolUseResult"]["answers"] = ["yes"]
        self.assertIsNotNone(self.check([e], "yes"))

    def test_elision(self):
        self.assertIsNone(self.check(["typed"], "use fable ... planner now"))
        self.assertIsNone(self.check(["typed"], "use fable high … the planner now"))
        self.assertIsNotNone(self.check(["typed"], "planner now ... use fable"))          # out of order
        self.assertIsNotNone(self.check(["typed", "typed_list"], "use fable high ... reviewer at opus xhigh"))
        self.assertIsNotNone(self.check(["typed"], "use ... now"))                        # fragments under 12 characters

    def test_normalization(self):
        self.assertIsNone(self.check(["typed"], "USE  Fable   HIGH for the planner"))
        self.assertIsNone(self.check(["typed"], "yes, use fable high for the planner now"))

    def test_a_sibling_session_never_backs_the_quote(self):
        """A memory can name another session of the project (`originSessionId`); that session's words are not this
        session's. There is no `| session <uuid>` form: it is refused, whether or not the sibling holds the quote."""
        self.transcript("typed", name=UUID + ".jsonl")
        main = self.transcript("tool_result", name="main.jsonl")
        for tail in (" | session " + UUID, " | Q1 a/b | session " + UUID, " | session x"):
            r = self.denied(self.spawn("planner", "fable/high", self.user(extra=tail), main))
            self.assertIn("no `| session <uuid>` form", r, tail)
        self.assertEqual(self.log_records()[-1]["decision"], "deny")
        self.assertNotIn("session <uuid>]", self.denied(self.spawn("planner", "fable/high", self.user(), main)))

    def test_a_resumed_or_compacted_transcript_needs_no_session_form(self):
        """`--resume` and `/compact` keep the transcript, so the typed turn from before is still in it."""
        self.allowed(self.planner_user(["typed", "compact_summary", "tool_result"]))

    def test_retries_then_refuses(self):
        self.denied(self.planner_user(["tool_result"]))
        self.assertEqual(self.sleeps, [0.5, 0.5, 0.5])
        self.sleeps[:] = []
        self.allowed(self.planner_user(["typed"]))
        self.assertEqual(self.sleeps, [])

    def test_a_missing_transcript_refuses_without_waiting(self):
        p = self.spawn("planner", "fable/high", self.user(), os.path.join(self.tmp, "gone.jsonl"))
        self.denied(p)
        self.denied(self.spawn("planner", "fable/high", self.user()))
        self.assertEqual(self.sleeps, [])

    def test_a_late_write_is_found_on_a_re_read(self):
        path = self.transcript("tool_result")
        calls = []

        def sleep(sec):
            calls.append(sec)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(fixture("typed")) + "\n")
        tiers.SLEEP = sleep
        self.allowed(self.spawn("planner", "fable/high", self.user(), path))
        self.assertEqual(calls, [0.5])

    def test_a_garbled_transcript_line_is_skipped(self):
        path = self.transcript("typed")
        with open(path, "a") as fh:
            fh.write('{"type": "user", broken\n')
        self.allowed(self.spawn("planner", "fable/high", self.user(), path))


class PaneAnswer(Escalation):
    def make(self, repo="repo", slug="task", via="- via: pane\n", status="answered", multi="",
             answer="Fable planner subagent (2026-11-15T10:00Z)"):
        d = os.path.join(self.tasks, repo, slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "questions.md"), "w") as fh:
            fh.write("# Questions: task\n\n## Q1 — first\n- status: answered\n- answer: no (2026-11-15T09:00Z)\n\n"
                     "## Q3 — Which planner?\n- status: %s\n- blocks: all\n%s%s- answer: %s\n\n## Q4 — later\n- status: open\n"
                     % (status, via, multi, answer))

    def spawn_pane(self, quote="Fable planner subagent", ref="Q3 repo/task", transcript=None):
        return self.spawn("planner", "fable/high", self.user(quote, " | " + ref), transcript or self.transcript("tool_result"))

    def test_a_pane_answer_backs_the_basis(self):
        self.make()
        self.allowed(self.spawn_pane())
        self.assertEqual(self.log_records()[-1]["found"], "pane")
        self.assertEqual(self.sleeps, [])

    def test_without_via_pane_it_does_not(self):
        self.make(via="")
        self.denied(self.spawn_pane())
        self.make(via="- via: terminal\n")
        self.denied(self.spawn_pane())

    def test_only_an_answered_question_counts(self):
        self.make(status="open")
        self.denied(self.spawn_pane())
        self.make()
        self.denied(self.spawn_pane(ref="Q4 repo/task"))
        self.denied(self.spawn_pane(ref="Q9 repo/task"))

    def test_the_answer_must_equal_the_quote(self):
        self.make()
        self.denied(self.spawn_pane(quote="Fable planner"))
        self.denied(self.spawn_pane(quote="no"))

    def test_a_multi_answer_matches_one_part(self):
        self.make(multi="- multi: yes\n", answer="Plan here | Restart on Fable (2026-11-15T10:00Z)")
        self.allowed(self.spawn_pane(quote="Restart on Fable"))
        self.make(answer="Plan here | Restart on Fable (2026-11-15T10:00Z)")      # not a multi question
        self.denied(self.spawn_pane(quote="Restart on Fable"))

    def test_path_traversal_is_refused(self):
        self.make(repo="repo")
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "questions.md"), "w") as fh:
            fh.write("## Q3 — x\n- status: answered\n- via: pane\n- answer: Fable planner subagent (2026-11-15T10:00Z)\n")
        self.denied(self.spawn_pane(ref="Q3 ../outside"))
        self.denied(self.spawn_pane(ref="Q3 repo/.."))
        self.denied(self.spawn_pane(ref="Q3 . /task"))

    def test_a_link_out_of_the_root_is_refused(self):
        self.make(repo="elsewhere")
        target = os.path.join(self.tmp, "elsewhere-real")
        os.rename(os.path.join(self.tasks, "elsewhere"), target)
        os.symlink(target, os.path.join(self.tasks, "linked"))
        self.denied(self.spawn_pane(ref="Q3 linked/task"))

    def test_the_transcript_is_still_searched_first(self):
        self.allowed(self.spawn_pane(quote=QUOTE, transcript=self.transcript("typed")))

    def test_an_answer_older_than_this_session_does_not_count(self):
        """The fixtures' first entry is at 2026-01-01T00:00; an answer given before it is another session's."""
        self.make(answer="Fable planner subagent (2025-12-31T23:59Z)")
        self.denied(self.spawn_pane())
        self.make(answer="Fable planner subagent (2026-01-01T00:00Z)")      # the same minute counts
        self.allowed(self.spawn_pane())

    def test_an_answer_with_no_time_or_a_transcript_with_no_time_does_not_count(self):
        self.make(answer="Fable planner subagent")
        self.denied(self.spawn_pane())
        self.make()
        e = fixture("tool_result")
        e.pop("timestamp")
        self.denied(self.spawn_pane(transcript=self.transcript(e)))
        self.denied(self.spawn("planner", "fable/high", self.user("Fable planner subagent", " | Q3 repo/task")))


class FileBases(Escalation):
    def setUp(self):
        Escalation.setUp(self)
        self.md = os.path.join(self.tmp, "CLAUDE.md")
        with open(self.md, "w") as fh:
            fh.write("# Rules\n\nSpawn the planner on fable high when the user asks.\n")

    def md_spawn(self, quote, path=None, loaded=None):
        """`loaded`: the paths the transcript's instructions attachment lists (default: the CLAUDE.md written above)."""
        tr = self.transcript(self.instructions_entry(*(self.md,) if loaded is None else loaded))
        p = self.spawn("planner", "fable/high", 'basis: claude-md | %s | "%s"' % (path or self.md, quote))
        return self.with_transcript(p, tr)

    def test_claude_md(self):
        self.allowed(self.md_spawn("planner on fable high"))
        self.allowed(self.md_spawn("PLANNER  on fable   high when"))
        self.denied(self.md_spawn("a sentence that is not in the file"))
        self.denied(self.md_spawn("short"))
        missing = os.path.join(self.tmp, "missing", "CLAUDE.md")
        self.denied(self.md_spawn("planner on fable high", missing, loaded=[missing]))
        other = os.path.join(self.tmp, "NOTES.md")
        with open(other, "w") as fh:
            fh.write("planner on fable high\n")
        self.denied(self.md_spawn("planner on fable high", other, loaded=[other]))
        local = os.path.join(self.tmp, "CLAUDE.local.md")
        with open(local, "w") as fh:
            fh.write("keep the planner on fable high\n")
        self.allowed(self.md_spawn("planner on fable high", local, loaded=[local]))

    def test_claude_md_must_be_one_this_session_loaded(self):
        """Any CLAUDE.md on disk (a vendored checkout, one the session wrote) is not a rule the user has set."""
        r = self.denied(self.md_spawn("planner on fable high", loaded=[os.path.join(self.tmp, "elsewhere", "CLAUDE.md")]))
        self.assertIn("not one this session loaded", r)
        self.denied(self.md_spawn("planner on fable high", loaded=[]))
        no_attachment = self.with_transcript(self.md_spawn("planner on fable high"), self.transcript("typed"))
        self.denied(no_attachment)
        self.denied(self.spawn("planner", "fable/high", 'basis: claude-md | %s | "planner on fable high"' % self.md))
        # a link is read as the file it points to
        link = os.path.join(self.tmp, "link", "CLAUDE.md")
        os.makedirs(os.path.dirname(link))
        os.symlink(self.md, link)
        self.allowed(self.md_spawn("planner on fable high", link, loaded=[self.md]))

    def test_lesson(self):
        d = os.path.join(self.tasks, "orch-skill", "lessons")
        os.makedirs(d)
        with open(os.path.join(d, "2026-11-02-x.md"), "w") as fh:
            fh.write("# x\n\n- status: open\n- role: planner, orchestrator\n- scope: project\n")
        self.allowed(self.spawn("planner", "fable/high", "basis: lesson | orch-skill/2026-11-02-x"))
        r = self.denied(self.spawn("planner", "fable/high", "basis: lesson | global/2026-11-02-x"))
        self.assertIn("no lesson file", r)
        self.denied(self.spawn("planner", "fable/high", "basis: lesson | other/2026-11-02-x"))

    def test_lesson_must_be_open_and_name_the_role(self):
        d = os.path.join(self.tasks, "lessons")
        os.makedirs(d)

        def write(name, status, role):
            with open(os.path.join(d, name + ".md"), "w") as fh:
                fh.write("# t\n\n- status: %s\n- role: %s\n- scope: global\n" % (status, role))
        write("2026-11-02-open", "open", "implementer")
        write("2026-11-02-archived", "archived", "code-reviewer")
        write("2026-11-02-promoted", "promoted", "planner")
        write("2026-11-02-reviewer", "open", "code-reviewer")
        write("2026-11-02-nearly", "open", "implementers, planner-notes")
        self.allowed(self.spawn("implementer", "opus/medium", "basis: lesson | global/2026-11-02-open"))
        r = self.denied(self.spawn("implementer", "opus/medium", "basis: lesson | global/2026-11-02-reviewer"))
        self.assertIn("no `role:` line naming orch:implementer", r)
        self.denied(self.spawn("plan-reviewer", "opus/medium", "basis: lesson | global/2026-11-02-reviewer"))
        self.allowed(self.spawn("code-reviewer", "opus/xhigh", "basis: lesson | global/2026-11-02-reviewer"))
        r = self.denied(self.spawn("code-reviewer", "opus/xhigh", "basis: lesson | global/2026-11-02-archived"))
        self.assertIn("not `status: open`", r)
        self.denied(self.spawn("planner", "fable/high", "basis: lesson | global/2026-11-02-promoted"))
        self.denied(self.spawn("planner", "fable/high", "basis: lesson | global/2026-11-02-nearly"))     # words, not prefixes


class LooseNames(Escalation):
    """Claude Code resolves a `subagent_type` that matches no agent exactly by NFKC, lowercase and dropping white
    space, dashes and `_`, and then runs the one agent that matches: so the guard must read those spellings as the
    role they run."""
    PLANNER = ("orch:Planner", "Orch:planner", "ORCH:PLANNER", "orch: planner", "orch:planner ", " orch:planner",
               "orch:plan_ner", "orch:plan\u2013ner", "orch:pla nner", "\uff4f\uff52\uff43\uff48:planner")

    def test_a_loose_spelling_gets_the_role_s_check(self):
        for name in self.PLANNER:
            r = self.denied(payload(name, model="fable", effort="high", prompt="no basis here"))
            self.assertIn("orch:planner at fable/high", r, repr(name))
            self.allowed(payload(name, model="fable", effort="high", prompt="basis: failure | need_escalate\nplan it"))
            self.allowed(payload(name, model="opus", effort="high", prompt="no basis needed"))      # the default
            self.allowed(payload(name, prompt="no basis needed"))
        self.assertEqual({r["subagent_type"] for r in self.log_records() if "subagent_type" in r},
                         {n[:60] for n in self.PLANNER})

    def test_a_loose_spelling_of_a_role_with_dashes(self):
        for name in ("orch:security_reviewer", "orch:Security-Reviewer", "orch:securityreviewer", "orch:code_reviewer"):
            self.denied(payload(name, model="fable", effort="high", prompt="no basis here"))
        self.denied(payload("orch:security_reviewer_mythos", prompt="no basis here"))     # always above its default
        self.allowed(payload("orch:code_reviewer", model="opus", effort="xhigh", prompt="basis: rule | large_diff"))
        self.denied(payload("orch:code_reviewer", model="opus", effort="max", prompt="basis: rule | large_diff"))

    def test_a_loose_spelling_of_a_cheap_role(self):
        for name in ("orch:Explorer", "orch: executor", "orch:Gra_ph", "orch:explorer "):
            self.denied(payload(name, model="opus"))
            self.denied(payload(name, effort="high"))
            self.allowed(payload(name, model="haiku", effort="low"))
            self.allowed(payload(name))
        self.assertEqual(self.log_records()[-1]["code"], "effort")

    def test_a_user_basis_is_still_looked_up_for_a_loose_name(self):
        p = self.spawn("planner", "fable/high", self.user(), self.transcript("tool_result"))
        p["tool_input"]["subagent_type"] = "orch:Planner"
        self.denied(p)
        p = self.spawn("planner", "fable/high", self.user(), self.transcript("typed"))
        p["tool_input"]["subagent_type"] = "orch:Planner"
        self.allowed(p)

    def test_an_orch_name_no_role_has_is_refused(self):
        for name in ("orch:orch-planner", "orch:plannerx", "orch:", "orch:foo", "orch:implementer2"):
            r = self.denied(payload(name, model="haiku", effort="low"))
            self.assertIn("is not an orch role", r, name)
            self.assertIn("exact name orch:<role>", r)
        rec = self.log_records()[-1]
        self.assertEqual((rec["decision"], rec["code"]), ("deny", "orch-name"))
        self.assertNotIn("SECRET_PROMPT", json.dumps(rec))

    def test_other_names_are_untouched(self):
        for name in ("foo:planner", "xorch:planner", "planner", "general-purpose", "orchestrator", "orch-planner-x"):
            self.allowed(payload(name, model="opus", effort="max"))

    def test_the_off_switch_covers_loose_names(self):
        self.allowed(payload("orch:Planner", model="fable", effort="high", prompt="x"), {"ORCH_TIER_GUARD": "0"})
        self.allowed(payload("orch:nonsense", model="opus"), {"ORCH_TIER_GUARD": "0"})

    def test_tiers_resolve_role(self):
        self.assertEqual(tiers.resolve_role("orch:planner"), "orch-planner")
        self.assertEqual(tiers.resolve_role("orch-planner"), "orch-planner")
        self.assertEqual(tiers.resolve_role("orch:Security_Reviewer-Mythos"), tiers.MYTHOS)
        self.assertIsNone(tiers.resolve_role("orch:foo"))
        self.assertIsNone(tiers.resolve_role(None))
        self.assertIsNone(tiers.resolve_role(["orch:planner"]))


class GuardBugs(Escalation):
    def test_an_exception_in_the_quote_reader_allows_and_logs(self):
        def boom(*a, **k):
            raise RuntimeError("reader broke")
        saved = tiers.user_texts
        tiers.user_texts = boom
        self.addCleanup(setattr, tiers, "user_texts", saved)
        self.allowed(self.planner_user(["typed"]))
        rec = self.log_records()[-1]
        self.assertEqual((rec["decision"], rec["code"], rec["role"]), ("error", "error", "orch:planner"))
        self.assertIn("RuntimeError", rec["error"])
        self.assertNotIn("SECRET_PROMPT", json.dumps(rec))

    def test_an_exception_in_the_pane_reader_allows_and_logs(self):
        saved = tiers.pane_answer
        tiers.pane_answer = lambda *a: 1 / 0
        self.addCleanup(setattr, tiers, "pane_answer", saved)
        self.allowed(self.spawn("planner", "fable/high", self.user("zzz not there zzz", " | Q1 r/s"),
                                self.transcript("typed")))
        self.assertEqual(self.log_records()[-1]["decision"], "error")

    def test_a_verdict_is_a_returned_dict_not_an_exception(self):
        out = tierguard.decide(self.planner_user(["tool_result"]), {})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.log_records()[-1]["decision"], "deny")

    def test_through_run_a_deny_prints_and_an_error_stays_silent(self):
        out = io.StringIO()
        self.assertEqual(tierguard.run(io.StringIO(json.dumps(self.spawn("planner", "fable/high", None))), out), 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
