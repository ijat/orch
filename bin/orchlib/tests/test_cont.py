"""orch continue-gate: reason codes, context measurement, journal and log lines. Synthetic fixtures; neutral names only."""
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import test_core as core  # noqa: E402
from orchlib import cont  # noqa: E402

T = "myapp/add-login"
AGENT = "a0742c49efc9b2653"
ROUTED = "1=opus/medium,2=opus/medium"


def step_block(sid, size="M", files="src/a.py", visual="none", pilot="none"):
    return ("### Step %s — Title %s\n\n- size: %s\n- depends: none\n- files: %s\n- do: change it\n"
            "- contract: it works\n- visual: %s\n- pilot: %s\n- done when: tests pass\n\n"
            % (sid, sid, size, files, visual, pilot))


def make_plan(cont="steps 1, 2", blocks=None):
    blocks = blocks if blocks is not None else [step_block("1"), step_block("2"), step_block("3")]
    line = "- continue: %s\n" % cont if cont is not None else ""
    return ("# Plan: demo\n\n- tier: opus/high\n\n## Summary\n\n- shape: single\n%s- steps: 3\n\n## Steps\n\n%s"
            "## Verification\n\nnone\n" % (line, "".join(blocks)))


def assistant(rid, t, i=0, cc=0, rd=0, o=0, model="claude-opus-5-5"):
    return json.dumps({"type": "assistant", "timestamp": t, "requestId": rid, "effort": "high",
                       "message": {"model": model, "id": "m" + rid,
                                   "usage": {"input_tokens": i, "cache_creation_input_tokens": cc,
                                             "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                                "ephemeral_1h_input_tokens": cc},
                                             "cache_read_input_tokens": rd, "output_tokens": o}}})


class GateBase(core.Base):
    def setUp(self):
        core.Base.setUp(self)
        self.d = self.init()
        self.uhome = os.path.join(self.tmp, "uhome")
        os.makedirs(os.path.join(self.uhome, "projects"))
        self.env["ORCH_USAGE_HOME"] = self.uhome

    def plan(self, text=None, **kw):
        with open(os.path.join(self.d, "plan.md"), "w") as fh:
            fh.write(text if text is not None else make_plan(**kw))

    def transcript(self, lines, agent=AGENT, proj="-home-u-proj-a", sess="sess-1"):
        d = os.path.join(self.uhome, "projects", proj, sess, "subagents")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "agent-%s.jsonl" % agent)
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

    def gate(self, *extra, **kw):
        argv = ["continue-gate", "-t", T, "--agent-id", kw.get("agent", AGENT),
                "--planner-tier", kw.get("planner", "opus/high"), "--routed", kw.get("routed", ROUTED)]
        if kw.get("tokens") is not None:
            argv += ["--tokens", str(kw["tokens"])]
        rc, out, err = self.run_orch(*(argv + list(extra)))
        self.assertEqual(rc, 0, "stdout=%s\nstderr=%s" % (out, err))
        return out.strip()

    def reasons(self, out):
        r = out.rsplit("reasons=", 1)[1]
        return [] if r == "-" else r.split(",")


class TestContext(GateBase):
    def test_notice_equals_last_request_prompt_plus_output(self):
        # 55,211 prompt (11 + 4,200 written + 51,000 read) + 2,870 output = 58,081, as measured on a known run
        self.transcript([assistant("r1", "2026-10-25T10:00:00.000Z", i=5, cc=100, rd=900, o=40),
                         "this line is not json {",
                         assistant("r2", "2026-10-25T10:01:00.000Z", i=11, cc=4200, rd=51000, o=2870),
                         json.dumps({"type": "user", "message": {"content": "x"}})])
        self.plan()
        out = self.gate()
        self.assertIn("ctx=58081 (transcript)", out)

    def test_last_entry_per_request_id_wins_and_synthetic_is_skipped(self):
        self.transcript([assistant("r1", "2026-10-25T10:00:00.000Z", i=1, cc=0, rd=1000, o=5),
                         assistant("r1", "2026-10-25T10:00:01.000Z", i=1, cc=0, rd=1000, o=50),
                         assistant("r2", "2026-10-25T10:05:00.000Z", i=0, o=0, model="<synthetic>")])
        self.plan()
        self.assertIn("ctx=1051 (transcript)", self.gate())

    def test_a_line_with_odd_shapes_is_skipped(self):
        bad = json.dumps({"type": "assistant", "timestamp": "2026-10-25T10:00:00.000Z", "requestId": "rx",
                          "message": {"model": "m", "usage": {"input_tokens": 1, "cache_creation": "oops"}}})
        self.transcript([bad, assistant("r1", "2026-10-25T10:01:00.000Z", i=7, o=3)])
        self.plan()
        self.assertIn("ctx=10 (transcript)", self.gate())

    def test_no_transcript_uses_tokens_then_unknown(self):
        self.plan()
        out = self.gate(tokens=58081)
        self.assertIn("ctx=58081 (notice)", out)
        out = self.gate()
        self.assertIn("ctx=unknown (none)", out)
        self.assertIn("ctx_unknown", self.reasons(out))

    def test_ambiguous_glob_is_not_trusted(self):
        self.transcript([assistant("r1", "2026-10-25T10:00:00.000Z", i=10, o=1)], proj="-home-u-proj-a")
        self.transcript([assistant("r1", "2026-10-25T10:00:00.000Z", i=10, o=1)], proj="-home-u-proj-b")
        self.plan()
        self.assertIn("ctx=unknown (none)", self.gate())
        self.assertIn("ctx=5000 (notice)", self.gate(tokens=5000))

    def test_a_transcript_with_no_usable_request_falls_back(self):
        self.transcript(["not json", json.dumps({"type": "user"})])
        self.plan()
        self.assertIn("ctx=77 (notice)", self.gate(tokens=77))

    def test_transcript_outside_projects_is_never_read(self):
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(os.path.join(outside, "x", "subagents"))
        with open(os.path.join(outside, "x", "subagents", "agent-%s.jsonl" % AGENT), "w") as fh:
            fh.write(assistant("r1", "2026-10-25T10:00:00.000Z", i=10, o=1) + "\n")
        os.symlink(outside, os.path.join(self.uhome, "projects", "linked"))
        self.plan()
        self.assertIn("ctx=unknown (none)", self.gate())

    def test_invalid_agent_ids(self):
        self.plan()
        for bad in ("abc", "../../etc/passwd", "a b c d e f", "abcdef*", "x" * 65):
            rc, _, err = self.run_orch("continue-gate", "-t", T, "--agent-id", bad, "--planner-tier", "opus/high",
                                       "--dry-run")
            self.assertEqual(rc, 1, bad)
            self.assertIn("bad --agent-id", err)


class TestBoundary(GateBase):
    def test_limit_is_the_constant(self):
        self.plan()
        below = self.gate(tokens=cont.CTX_MAX - 1)
        self.assertEqual(self.reasons(below), [])
        self.assertTrue(below.startswith("continue-gate: continue steps=1,2"))
        above = self.gate(tokens=cont.CTX_MAX)
        self.assertEqual(self.reasons(above), ["ctx_high"])
        self.assertTrue(above.startswith("continue-gate: split"))

    def test_literal_limits(self):
        # a guard against the constant drifting: these are the values the user set (Revision 1)
        self.assertEqual(cont.CTX_MAX, 200000)
        self.plan()
        self.assertEqual(self.reasons(self.gate(tokens=199999)), [])
        self.assertEqual(self.reasons(self.gate(tokens=200000)), ["ctx_high"])
        self.assertEqual(self.reasons(self.gate(tokens=250000)), ["ctx_high"])

    def test_help_names_the_limit(self):
        rc, out, _ = self.run_orch("continue-gate", "--help")
        self.assertEqual(rc, 0)
        self.assertIn(format(cont.CTX_MAX, ","), out.replace("\n", " ").replace("  ", " "))

    def test_commands_lists_it(self):
        rc, out, _ = self.run_orch("commands")
        self.assertIn("continue-gate", out.split())


class TestReasons(GateBase):
    def check(self, want, tokens=100000, planner="opus/high", routed=ROUTED, signals=None, **plan_kw):
        self.plan(**plan_kw)
        extra = ["--signals", signals] if signals else []
        out = self.gate(*extra, tokens=tokens, planner=planner, routed=routed)
        self.assertEqual(self.reasons(out), want, out)
        return out

    def test_all_clear(self):
        out = self.check([])
        self.assertEqual(out, "continue-gate: continue steps=1,2 ctx=100000 (notice) reasons=-")

    def test_plan_no_missing_no_and_unparsable(self):
        for says in (None, "no — every step routes to sonnet/high", "No", "steps <ids> | no — <why>", "maybe", "steps"):
            self.check(["plan_no"], cont=says)

    def test_plan_says_forms(self):
        for says in ("steps 1, 2", "steps 1–2", "steps 1-2", "steps 1 and 2", "Steps 1,2", "steps 1, 2 (both opus)"):
            self.check([], cont=says)
        self.check([], cont="steps 1")

    def test_ids_with_letters_and_dots(self):
        blocks = [step_block("1a"), step_block("1b"), step_block("2")]
        self.check([], cont="steps 1a, 1b", blocks=blocks, routed="1a=opus/high,1b=opus/high")
        self.check([], cont="steps 1a–1b", blocks=blocks, routed="1a=opus/high,1b=opus/high")
        self.check(["not_first"], cont="steps 1b, 2", blocks=blocks, routed="1b=opus/high,2=opus/high")

    def test_not_first_and_not_contiguous(self):
        self.check(["not_first"], cont="steps 2, 3", routed="2=opus/medium,3=opus/medium")
        self.check(["not_first"], cont="steps 1, 3", routed="1=opus/medium,3=opus/medium")
        self.check(["not_first"], cont="steps 9", routed="9=opus/medium")

    def test_done_steps_come_from_the_implementer_row(self):
        self.ok("role", "implementer", "in_progress", "--step", "1", "-t", T)
        self.check([], cont="steps 2, 3", routed="2=opus/medium,3=opus/medium")
        self.check(["not_first"], cont="steps 1, 2", routed="1=opus/medium,2=opus/medium")

    def test_too_many(self):
        blocks = [step_block(str(i)) for i in range(1, 6)]
        r = ",".join("%d=opus/medium" % i for i in range(1, 6))
        self.check(["too_many"], cont="steps 1-4", blocks=blocks, routed=r)
        self.check([], cont="steps 1-3", blocks=blocks, routed=r)

    def test_size_L(self):
        blocks = [step_block("1"), step_block("2", size="L"), step_block("3")]
        self.check(["size_L"], blocks=blocks)

    def test_tier_model(self):
        self.check(["tier_model"], routed="1=sonnet/high,2=sonnet/high")
        self.check(["tier_model"], routed="1=opus/medium")   # step 2 has no routed tier
        self.check(["tier_model"], routed="")

    def test_tier_effort_ladder(self):
        self.check([], planner="opus/high", routed="1=opus/high,2=opus/medium")
        self.check(["tier_effort"], planner="opus/high", routed="1=opus/low,2=opus/medium")
        self.check(["tier_effort"], planner="opus/medium", routed="1=opus/high,2=opus/high")   # routed above the planner
        self.check(["tier_effort"], planner="opus/max", routed="1=opus/medium,2=opus/medium")
        self.check([], planner="opus/max", routed="1=opus/xhigh,2=opus/xhigh")
        self.check(["tier_effort"], planner="opus/high", routed="1=opus/wat,2=opus/medium")

    def test_visual_and_pilot(self):
        self.check(["visual"], blocks=[step_block("1", visual="yes"), step_block("2")])
        self.check(["pilot"], blocks=[step_block("1"), step_block("2", pilot="batch 1")])
        self.check([], blocks=[step_block("1", visual="none — nothing to draw"), step_block("2")])

    def test_visual_on_a_step_outside_the_run_is_ignored(self):
        self.check([], blocks=[step_block("1"), step_block("2"), step_block("3", visual="yes")])

    def test_notebook(self):
        self.check(["notebook"], blocks=[step_block("1", files="a.py, nb/x.ipynb (edit)"), step_block("2")])

    def test_signals(self):
        self.check(["signal:boundary"], signals="2=boundary")
        self.check(["signal:sensitive"], signals="1=sensitive")
        self.check(["signal:boundary", "signal:parallel"], signals="1=parallel,2=boundary")
        self.check([], signals="3=boundary")   # not in the run

    def test_unknown_signal_name_is_an_error(self):
        self.plan()
        rc, _, err = self.run_orch("continue-gate", "-t", T, "--agent-id", AGENT, "--planner-tier", "opus/high",
                                   "--signals", "1=bogus", "--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("bad --signals item", err)

    def test_bad_routed_and_planner_tier(self):
        self.plan()
        for extra in (["--routed", "1opus/medium"], ["--routed", "1=Opus"], ["--routed", "1=opus/high,1=opus/high"]):
            rc, _, _ = self.run_orch("continue-gate", "-t", T, "--agent-id", AGENT, "--planner-tier", "opus/high",
                                     "--dry-run", *extra)
            self.assertEqual(rc, 1, extra)
        rc, _, err = self.run_orch("continue-gate", "-t", T, "--agent-id", AGENT, "--planner-tier", "opus", "--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("--planner-tier", err)

    def test_mode(self):
        self.ok("set", "mode=lite", "-t", T)
        self.check(["mode"])

    def test_ctx_unknown_with_no_tokens(self):
        self.plan()
        out = self.gate()
        self.assertEqual(self.reasons(out), ["ctx_unknown"])

    def test_several_reasons_come_in_a_fixed_order(self):
        self.ok("set", "mode=lite", "-t", T)
        out = self.check(["plan_no", "ctx_high", "mode"], tokens=cont.CTX_MAX, cont=None)
        self.assertIn("steps=-", out)


class TestWrites(GateBase):
    def test_dry_run_writes_nothing(self):
        self.plan()
        before = core.tree_hash(self.d)
        out = self.gate("--dry-run", tokens=1000)
        self.assertTrue(out.startswith("continue-gate: continue"))
        self.assertEqual(core.tree_hash(self.d), before)

    def test_journal_and_log_lines(self):
        self.plan()
        self.transcript([assistant("r2", "2026-10-25T10:01:00.000Z", i=11, cc=4200, rd=51000, o=2870)])
        out = self.gate()
        rec = self.journal_lines(self.d)[-1]
        self.assertEqual(rec["kind"], "continue_gate")
        self.assertEqual(rec["decision"], "continue")
        self.assertEqual(rec["agent_id"], AGENT)
        self.assertEqual(rec["ctx"], 58081)
        self.assertEqual(rec["ctx_src"], "transcript")
        self.assertEqual(rec["planner_tier"], "opus/high")
        self.assertEqual(rec["routed"], ["1=opus/medium", "2=opus/medium"])
        self.assertEqual(rec["steps"], [1, 2])
        self.assertEqual(rec["reasons"], [])
        self.assertEqual(rec["plan_says"], "steps 1, 2")
        log = self.read(self.d, "log.md").decode().splitlines()[-1]
        self.assertIn("orchestrator: " + out, log)

    def test_split_line_records_unknowns_as_null(self):
        self.plan(cont="no — everything routes to sonnet/high")
        self.gate(routed="1=sonnet/high,2=sonnet/high")
        rec = self.journal_lines(self.d)[-1]
        self.assertEqual((rec["decision"], rec["ctx"], rec["ctx_src"], rec["steps"]), ("split", None, "none", []))
        self.assertEqual(rec["reasons"], ["plan_no", "ctx_unknown"])
        self.assertEqual(rec["routed"], [])
        self.assertTrue(rec["plan_says"].startswith("no"))

    def test_missing_continue_line_records_null(self):
        self.plan(cont=None)
        self.gate(tokens=1000)
        self.assertIsNone(self.journal_lines(self.d)[-1]["plan_says"])

    def test_show_and_dashboard_survive_the_new_kind(self):
        self.plan()
        self.gate(tokens=1000)
        rc, out, err = self.run_orch("show", "-t", T)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_orch("dashboard", "--json")
        self.assertEqual(rc, 0, err)
        json.loads(out)

    def test_write_needs_orch_dir_but_dry_run_does_not(self):
        self.plan()
        import subprocess
        e = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1",
             "ORCH_USAGE_HOME": self.uhome}
        base = [sys.executable, core.LAUNCHER, "continue-gate", "-t", self.d, "--agent-id", AGENT,
                "--planner-tier", "opus/high", "--tokens", "10", "--routed", ROUTED]
        r = subprocess.run(base, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("development copy", r.stderr)
        r = subprocess.run(base + ["--dry-run"], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)


BIN = os.path.dirname(os.path.dirname(HERE))
ROLE_COST = os.path.join(BIN, "role-cost.py")
STATS = os.path.join(BIN, "orch-stats.sh")


def user(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


class TestRoleCostSplit(core.Base):
    def run_cost(self, *argv):
        r = subprocess.run([sys.executable, ROLE_COST, "--root", os.path.join(self.tmp, "projects")] + list(argv),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def agent(self, name, lines, typ="orch-planner"):
        d = os.path.join(self.tmp, "projects", "p", "s", "subagents")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "agent-%s.jsonl" % name), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        with open(os.path.join(d, "agent-%s.meta.json" % name), "w") as fh:
            json.dump({"agentType": typ}, fh)

    def rows(self, out):
        return dict((l[:40].strip(), l[40:].split()) for l in out.splitlines()[1:]
                    if len(l) > 40 and l[40:].split()[0].isdigit())

    def test_marker_splits_a_planner_run(self):
        self.agent("aaa111", [user("plan it"), assistant("r1", "2026-10-25T10:00:00.000Z", i=1000, o=100),
                              assistant("r2", "2026-10-25T10:01:00.000Z", i=2000, o=200),
                              user("orch: phase 2 — implement steps 1, 2; worktree /w"),
                              assistant("r3", "2026-10-25T10:10:00.000Z", i=3000, o=300),
                              assistant("r4", "2026-10-25T10:11:00.000Z", i=4000, o=400)])
        out = self.run_cost()
        rows = self.rows(out)
        # runs, median requests, median peak, median volume
        self.assertEqual(rows["planner-continued opus-5-5/high"][:4], ["1", "2", "4,000", "7,000"])
        self.assertEqual(rows["orch-planner opus-5-5/high"][:4], ["1", "2", "2,000", "3,000"])
        self.assertIn("planner-continued", self.run_cost("--by", "type"))

    def test_a_transcript_without_the_marker_is_one_planner_row(self):
        self.agent("bbb222", [user("plan it"), assistant("q1", "2026-10-25T10:00:00.000Z", i=1000, o=100),
                              user("please revise"), assistant("q2", "2026-10-25T10:01:00.000Z", i=2000, o=200)])
        out = self.run_cost()
        self.assertNotIn("planner-continued", out)
        self.assertEqual(self.rows(out)["orch-planner opus-5-5/high"][:4], ["1", "2", "2,000", "3,000"])

    def test_the_marker_only_counts_in_a_planner_run(self):
        self.agent("ccc333", [user("orch: phase 2"), assistant("q1", "2026-10-25T10:00:00.000Z", i=1000, o=100)],
                   typ="orch-implementer")
        self.assertNotIn("planner-continued", self.run_cost())


@unittest.skipUnless(shutil.which("jq"), "jq is needed by orch-stats.sh")
class TestStatsBlock(core.Base):
    def stats(self, lines):
        d = os.path.join(self.root, "r", "t1")
        os.makedirs(d)
        with open(os.path.join(d, "journal.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
        r = subprocess.run(["bash", STATS], env={"PATH": os.environ.get("PATH", ""), "HOME": self.home,
                                                 "ORCH_DIR": self.root},
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def line(self, **kw):
        base = {"ts": "2026-10-25T10:00Z", "repo": "r", "task": "t1"}
        base.update(kw)
        return base

    def test_block_with_and_without_the_kind(self):
        start = self.line(kind="task_start", mode="standard", request="x")
        out = self.stats([start])
        self.assertIn("planner continuation:\n  none\n", out)
        d = os.path.join(self.root, "r", "t1")
        shutil.rmtree(d)
        gate = lambda dec, ctx, reasons: self.line(kind="continue_gate", decision=dec, agent_id="abcdef1", ctx=ctx,
                                                   ctx_src="transcript", planner_tier="opus/high", routed=[],
                                                   steps=[1], reasons=reasons, plan_says="steps 1")
        out = self.stats([start, gate("continue", 150000, []), gate("split", 250000, ["ctx_high", "tier_model"]),
                          gate("split", None, ["plan_no", "ctx_unknown"]),
                          self.line(kind="spawn", role="implementer", agent="orch-planner", tier="opus/high",
                                    steps=[1], usage_5h=1, usage_7d=1)])
        block = out.split("planner continuation:\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("gates: 3 (continue 1, split 2)", block)
        self.assertIn("ctx_high: 1", block)
        self.assertIn("median ctx: continue 150000  split 250000", block)
        self.assertIn("continued spawns: 1", block)


if __name__ == "__main__":
    unittest.main()
