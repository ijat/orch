"""Composite commands: spawn, return, end, ask, answer, checkpoint, resume. Neutral fixtures only."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import test_core as core  # noqa: E402
from orchlib import flow, questions as qs, status as st  # noqa: E402

T = "myapp/add-login"


class FlowBase(core.Base):
    def setUp(self):
        core.Base.setUp(self)
        self.usage_n = 0
        self.d = self.init()

    def run_orch(self, *argv, **kw):
        """Task commands default to -t T."""
        argv = list(argv)
        if "-t" not in argv and argv and argv[0] in ("spawn", "return", "end", "ask", "answer", "checkpoint"):
            argv += ["-t", T]
        return core.Base.run_orch(self, *argv, **kw)

    def fake_usage(self, code, line):
        self.usage_n += 1
        path = os.path.join(self.tmp, "usage%d.sh" % self.usage_n)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\necho '%s'\nexit %d\n" % (line, code))
        os.chmod(path, 0o755)
        return {"ORCH_USAGE_CMD": path}

    @property
    def ok_usage(self):
        return self.fake_usage(0, "state=OK 5h=31 5h_age=4 7d=60 7d_age=4 reset_in=1h")

    def spawn(self, *args, **kw):
        kw.setdefault("env", self.ok_usage)
        return self.ok("spawn", *args, **kw)

    def journal(self):
        return self.journal_lines(self.d)

    def kinds(self):
        return [r["kind"] for r in self.journal()]

    def log_lines(self):
        return self.read(self.d, "log.md").decode().splitlines()

    def snapshot(self):
        return core.tree_hash(self.d)


class TestSpawn(FlowBase):
    def test_each_usage_exit_code(self):
        cases = [(0, "state=OK 5h=31 5h_age=4 7d=60 7d_age=4", False),
                 (1, "state=WARN 5h=91 5h_age=4 7d=60 7d_age=4", True),
                 (3, "state=STALE 5h=40 5h_age=900 7d=60 7d_age=900", True),
                 (4, "state=UNKNOWN 5h=? 5h_age=? reason=no-cache-sample", True)]
        for code, line, warns in cases:
            before = len(self.journal())
            out, err = self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high",
                                  env=self.fake_usage(code, line))
            self.assertIn("usage: " + line, out)
            self.assertEqual(err.count("warning:") if warns else err, 1 if warns else "", (code, err))
            if warns:
                self.assertIn(line, err)
            rec = self.journal()[-1]
            self.assertEqual((rec["kind"], len(self.journal()) - before), ("spawn", 1))
            want5 = int(re.search(r"5h=(\d+)", line).group(1)) if re.search(r"5h=(\d+)", line) else None
            self.assertEqual(rec["usage_5h"], want5)
        self.assertIsNone(self.journal()[-1]["usage_5h"])
        self.assertEqual(self.journal()[-1]["usage_7d"], None)

    def test_stop_refuses_and_writes_only_a_log_line(self):
        journal_before = self.read(self.d, "journal.jsonl")
        status_before = self.read(self.d, "status.md")
        err = self.refuse(2, "spawn", "implementer", "--agent", "orch-implementer", "--tier", "sonnet/high",
                          "--reason", "r", "--tradeoff", "t",
                          env=self.fake_usage(2, "state=STOP 5h=96 5h_age=2 7d=70 7d_age=2"))
        self.assertIn("spawn refused: usage STOP (5h 96%)", err)
        self.assertEqual(self.read(self.d, "journal.jsonl"), journal_before)
        self.assertEqual(self.read(self.d, "status.md"), status_before)
        self.assertTrue(self.log_lines()[-1].endswith("orchestrator: spawn refused: usage STOP (5h 96%)"))

    def test_usage_command_that_cannot_run_is_unknown_not_fatal(self):
        out, err = self.spawn("planner", "--agent", "orch-planner", "--tier", "opus/high",
                              env={"ORCH_USAGE_CMD": os.path.join(self.tmp, "no-such-script")})
        self.assertIn("state=UNKNOWN", out)
        self.assertIn("warning: usage UNKNOWN", err)
        self.assertIsNone(self.journal()[-1]["usage_5h"])

    def test_default_usage_check_is_the_launcher_sibling(self):
        fake = os.path.join(self.tmp, "fakebin")
        os.makedirs(fake)
        with open(os.path.join(fake, "usage-check.sh"), "w") as fh:
            fh.write("#!/bin/sh\necho 'state=OK 5h=12 5h_age=1 7d=34 7d_age=1'\nexit 0\n")
        os.chmod(os.path.join(fake, "usage-check.sh"), 0o755)
        with mock.patch.object(flow, "BIN_DIR", fake):
            self.ok("spawn", "planner", "--agent", "orch-planner", "--tier", "opus/high")
        rec = self.journal()[-1]
        self.assertEqual((rec["usage_5h"], rec["usage_7d"]), (12, 34))

    def test_tier_choice_escalation_spawn_in_that_order(self):
        out, _ = self.spawn("implementer", "--agent", "orch-implementer", "--tier", "opus/high", "--steps", "3,4",
                            "--reason", "concurrency in step 3", "--alt", "sonnet/high, opus/medium",
                            "--tradeoff", "slower, fewer reviews", "--escalation", "post-failure",
                            "--from", "sonnet/high", "--trigger", "prior_tier_failed", "--note", "round 2")
        self.assertEqual(self.kinds()[-3:], ["tier_choice", "escalation", "spawn"])
        tc, esc, sp = self.journal()[-3:]
        self.assertEqual((tc["role"], tc["tier"], tc["tradeoff"], tc["reason"], tc["alternatives"]),
                         ("implementer", "opus/high", "slower, fewer reviews", "concurrency in step 3",
                          ["sonnet/high", "opus/medium"]))
        self.assertEqual((esc["from"], esc["to"], esc["trigger"], esc["tradeoff"]),
                         ("sonnet/high", "opus/high", "prior_tier_failed", "slower, fewer reviews"))
        self.assertEqual(esc["alternatives"], ["sonnet/high", "opus/medium"])
        self.assertTrue(esc["reason"])
        self.assertEqual((sp["agent"], sp["steps"], sp["usage_5h"], sp["usage_7d"]), ("orch-implementer", [3, 4], 31, 60))
        self.assertIn("journal tier_choice, escalation, spawn", out)
        self.assertLessEqual(len(out.splitlines()), 3)
        self.assertIn("round 2", self.log_lines()[-1])

    def test_no_reason_means_no_tier_choice_and_none_means_no_escalation(self):
        self.spawn("director", "--agent", "orch-director", "--tier", "opus/high", "--escalation", "none")
        self.assertEqual(self.kinds(), ["task_start", "spawn"])
        self.assertEqual(self.journal()[-1]["steps"], None)
        self.assertEqual(self.journal()[-1]["alternatives"] if "alternatives" in self.journal()[-1] else [], [])

    def test_alt_may_be_empty_and_steps_may_be_strings(self):
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high", "--steps", "M1 phase 1",
                   "--reason", "default", "--tradeoff", "none")
        self.assertEqual(self.journal()[-2]["alternatives"], [])
        self.assertEqual(self.journal()[-1]["steps"], ["M1 phase 1"])

    def test_validation_errors_write_nothing(self):
        before = self.snapshot()
        base = ["spawn", "implementer", "--agent", "orch-implementer", "--tier", "sonnet/high"]
        env = self.ok_usage
        for extra in (["--reason", "r"],                                         # reason needs tradeoff
                      ["--escalation", "proactive", "--trigger", "plan_flag", "--tradeoff", "t"],   # no --from
                      ["--escalation", "proactive", "--from", "sonnet/high", "--tradeoff", "t"],    # no --trigger
                      ["--escalation", "proactive", "--from", "sonnet/high", "--trigger", "x"],     # no --tradeoff
                      ["--escalation", "sometimes"], ["--tier", "sonnet-high"]):
            argv = list(base)
            if "--tier" in extra:
                argv[argv.index("--tier") + 1] = extra[1]
            else:
                argv += extra
            self.refuse(1, *argv, env=env)
        self.refuse(1, "spawn", "Bad Role", "--agent", "a", "--tier", "sonnet/high", env=env)
        self.refuse(1, "spawn", "planner", "--tier", "sonnet/high", env=env)
        self.assertEqual(self.snapshot(), before)

    def test_program_spawn_carries_the_milestone(self):
        self.ok("set", "shape=roadmap", "milestone=M2 — implementing", "-t", T)
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high")
        self.assertEqual(self.journal()[-1]["milestone"], 2)


class TestReturn(FlowBase):
    def test_outcome_defaults_to_the_row_state(self):
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high", "--steps", "1")
        for state in ("done", "blocked", "failed"):
            self.ok("role", "implementer", state, "--step", "1", "--note", "need: a decision" if state == "blocked" else "n", "-t", T)
            out, _ = self.ok("return", "implementer", "--tokens", "5000", "--tool-uses", "40", "--duration-ms", "90000", "-t", T)
            rec = self.journal()[-1]
            self.assertEqual((rec["kind"], rec["outcome"], rec["agent"], rec["tokens"], rec["tool_uses"], rec["duration_ms"]),
                             ("return", state, "orch-implementer", 5000, 40, 90000))
            self.assertIn("row: %s | step: 1" % state, out)
            self.assertEqual("need: a decision" in out, state == "blocked")

    def test_a_row_still_working_is_refused_unless_outcome_is_given(self):
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high")
        for state in ("in_progress", "waiting", "pending"):
            self.ok("role", "implementer", state, "-t", T)
            before = self.snapshot()
            err = self.refuse(1, "return", "implementer", "-t", T)
            self.assertIn("row not final", err)
            self.assertEqual(self.snapshot(), before)
        self.ok("return", "implementer", "--outcome", "stopped", "-t", T)
        self.assertEqual(self.journal()[-1]["outcome"], "stopped")
        self.assertIsNone(self.journal()[-1]["tokens"])
        self.refuse(1, "return", "implementer", "--outcome", "great", "-t", T)

    def test_unknown_row_needs_an_outcome(self):
        self.refuse(1, "return", "implementer:ui", "-t", T)
        _, err = self.ok("return", "implementer:ui", "--outcome", "done", "-t", T)
        self.assertIn("no earlier spawn", err)
        self.assertIsNone(self.journal()[-1]["agent"])

    def test_matches_the_last_spawn_of_that_role(self):
        self.spawn("implementer", "--agent", "agent-one", "--tier", "sonnet/high")
        self.spawn("implementer", "--agent", "agent-two", "--tier", "sonnet/high")
        self.spawn("planner", "--agent", "agent-three", "--tier", "opus/high")
        self.ok("return", "implementer", "--outcome", "done", "-t", T)
        self.assertEqual(self.journal()[-1]["agent"], "agent-two")

    def test_lists_files_changed_since_the_spawn_minute(self):
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high", now="2026-03-01T10:30Z")
        old = os.path.join(self.d, "plan.md")
        new = os.path.join(self.d, "notes.md")
        deep = os.path.join(self.d, "artifacts", "out.txt")
        os.makedirs(os.path.dirname(deep))
        for p in (old, new, deep):
            with open(p, "w") as fh:
                fh.write("x")
        t0 = flow.ts_epoch("2026-03-01T10:30Z")
        os.utime(old, (t0 - 120, t0 - 120))
        os.utime(new, (t0 + 600, t0 + 600))
        os.utime(deep, (t0 + 30, t0 + 30))
        self.ok("role", "implementer", "done", "-t", T)
        out, _ = self.ok("return", "implementer", "-t", T)
        line = [l for l in out.splitlines() if l.startswith("changed since spawn")][0]
        self.assertIn("(2026-03-01T10:30Z)", line)
        self.assertIn("notes.md", line)
        self.assertIn(os.path.join("artifacts", "out.txt"), line)
        self.assertNotIn("plan.md", line)
        self.assertNotIn("status.md", line)
        self.assertNotIn(".orch.lock", line)

    NOTES = ("# Notes\n\n## implementer — step 3\n\ncontract-check: step 3\n- [x] item one — evidence one\n"
             "- [ ] item two — not done\n\ncontract-check: step 4\n- [x] other step\n\nTrailing prose.\n")

    def test_haiku_return_prints_the_contract_check_blocks_verbatim(self):
        with open(os.path.join(self.d, "notes.md"), "w") as fh:
            fh.write(self.NOTES)
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "haiku/medium", "--steps", "3")
        self.ok("role", "implementer", "done", "-t", T)
        out, _ = self.ok("return", "implementer", "-t", T)
        self.assertIn("contract-check: step 3\n- [x] item one — evidence one\n- [ ] item two — not done\n", out)
        self.assertNotIn("step 4", out)
        self.assertNotIn("Trailing prose", out)

    def test_haiku_return_says_when_a_block_is_missing_and_other_tiers_print_none(self):
        with open(os.path.join(self.d, "notes.md"), "w") as fh:
            fh.write(self.NOTES)
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "haiku/medium", "--steps", "3,9")
        self.ok("role", "implementer", "done", "-t", T)
        out, _ = self.ok("return", "implementer", "-t", T)
        self.assertIn("contract-check: step 3", out)
        self.assertIn("no contract-check block in notes.md for step 9", out)
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high", "--steps", "3")
        out, _ = self.ok("return", "implementer", "-t", T)
        self.assertNotIn("contract-check", out)


class TestEnd(FlowBase):
    def write_retro(self, body="# Retro: add-login\n\n- outcome: merged\n"):
        with open(os.path.join(self.d, "retro.md"), "w") as fh:
            fh.write(body)

    def test_retro_must_exist_and_differ_from_the_template(self):
        before = self.snapshot()
        self.assertIn("missing", self.refuse(1, "end", "merged", "-t", T))
        with open(os.path.join(core.BIN, "..", "skills", "orchestrate", "templates", "retro.md")) as fh:
            self.write_retro(fh.read().replace("<task-slug>", "add-login"))
        before = self.snapshot()
        self.assertIn("template", self.refuse(1, "end", "merged", "-t", T))
        self.assertEqual(self.snapshot(), before)
        self.write_retro()
        self.ok("end", "merged", "-t", T)

    def test_force_skips_the_retro_check(self):
        self.ok("end", "declined", "--force", "-t", T)
        self.assertEqual(self.journal()[-1]["outcome"], "declined")

    def test_end_writes_task_end_and_final_state(self):
        self.write_retro()
        self.ok("set", "review_round=2/3", "-t", T)
        out, _ = self.ok("end", "merged", "-t", T)
        rec = self.journal()[-1]
        self.assertEqual((rec["kind"], rec["outcome"], rec["rounds"]), ("task_end", "merged", 2))
        self.assertEqual(st.Status.load(os.path.join(self.d, "status.md")).get("task_state"), "done")
        self.assertIn("ended: merged (task_state done); review rounds 2/3", out)

    def test_abandoned_sets_abandoned_and_every_outcome_is_accepted(self):
        self.write_retro()
        self.ok("end", "abandoned", "-t", T)
        self.assertEqual(st.Status.load(os.path.join(self.d, "status.md")).get("task_state"), "abandoned")
        for i, outcome in enumerate(("declined", "failed_merge", "handed_to_user")):
            self.init(slug="t%d" % i)
            with open(os.path.join(self.tdir(slug="t%d" % i), "retro.md"), "w") as fh:
                fh.write("# Retro\n- outcome: %s\n" % outcome)
            self.ok("end", outcome, "-t", "myapp/t%d" % i)
        self.refuse(1, "end", "finished", "-t", T)

    def test_ending_twice_is_refused_unless_forced(self):
        self.write_retro()
        self.ok("end", "merged", "-t", T)
        before = self.snapshot()
        self.assertIn("already ended", self.refuse(2, "end", "merged", "-t", T))
        self.assertEqual(self.snapshot(), before)
        self.ok("end", "merged", "--force", "-t", T)
        self.assertEqual(self.kinds().count("task_end"), 2)

    def test_digest_content_and_size(self):
        self.write_retro()
        self.spawn("implementer", "--agent", "orch-implementer", "--tier", "sonnet/high",
                   env=self.fake_usage(0, "state=OK 5h=20 5h_age=1 7d=50 7d_age=1"))
        self.ok("return", "implementer", "--outcome", "done", "--tokens", "1000", "-t", T)
        self.spawn("code-reviewer", "--agent", "orch-code-reviewer", "--tier", "sonnet/high",
                   env=self.fake_usage(1, "state=WARN 5h=92 5h_age=1 7d=61 7d_age=1"))
        self.ok("return", "code-reviewer", "--outcome", "done", "--tokens", "2500", "-t", T)
        self.ok("journal", "auto_merge", "sha=abc1234567890", "base=main", "branch=orch/add-login",
                "revert=git revert -m 1 abc1234567890", "-t", T)
        self.ok("journal", "merge_point", "milestone:=1", "sha=def4567890123", "backmerge:=null", "tree_equal:=null", "-t", T)
        self.ok("journal", "auto_security", "areas=auth,parser", "ran:=true", "rule=step5", "-t", T)
        self.ok("journal", "auto_security", "areas=", "ran:=false", "rule=late", "-t", T)
        self.ok("ask", "Pick a colour", "--options", "red|blue", "--recommended", "blue", "--reversible", "yes",
                "--assume", "blue", "-t", T)
        self.ok("ask", "Drop the old API?", "--blocks", "2", "-t", T)
        out, _ = self.ok("end", "merged", "-t", T)
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 25)
        self.assertIn("merge (auto): abc123456789 into main; revert: git revert -m 1 abc1234567890", out)
        self.assertIn("backmerge: null: task branch not caught up — next merge point will ask", out)
        self.assertIn("assumed decisions: 1", out)
        self.assertIn("blue (orchestrator, reversible true)", out)
        self.assertIn("open questions: 1", out)
        self.assertIn("Q2 (blocks 2): Drop the old API?", out)
        self.assertIn("security: ran (rule step5; areas auth, parser)", out)
        self.assertIn("security: skipped (rule late", out)
        self.assertIn("agent tokens reported: 3500 over 2 returns (reported context sizes, not spend)", out)
        self.assertIn("5h 20%→92%, 7d 50%→61%", out)

    def test_digest_is_capped_at_25_lines(self):
        self.write_retro()
        for i in range(8):
            self.ok("ask", "question %d" % i, "--options", "a|b", "--reversible", "yes", "--assume", "a", "-t", T)
            self.ok("ask", "open %d" % i, "-t", T)
            self.ok("journal", "auto_merge", "sha=s%d" % i, "base=main", "branch=b", "revert=r", "-t", T)
            self.ok("journal", "auto_security", "areas=x", "ran:=true", "rule=step5", "-t", T)
        out, _ = self.ok("end", "merged", "-t", T)
        self.assertLessEqual(len(out.splitlines()), 25)
        self.assertIn("agent tokens reported", out)  # the closing lines survive a busy task
        self.assertIn("usage: no spawn lines", out)


class TestQuestions(FlowBase):
    def qpath(self):
        return os.path.join(self.d, "questions.md")

    def test_ask_creates_the_file_from_the_template_and_numbers_questions(self):
        out, _ = self.ok("ask", "Use SQLite?", "--options", "yes|no", "--recommended", "no", "--blocks", "3,4",
                         "--reversible", "no", "--as", "implementer", "-t", T)
        self.assertIn("Q1 open (blocks 3, 4)", out)
        self.ok("ask", "Second?", "-t", T)
        text = self.read(self.qpath()).decode()
        self.assertTrue(text.startswith("# Questions: add-login\n"))
        self.assertIn("\n## Q1 — Use SQLite?\n- status: open\n- asked by: implementer at 2026-03-01T10:00Z\n"
                      "- blocks: 3, 4\n- reversible: no\n- options: yes | no (recommended)\n\n## Q2 — Second?\n"
                      "- status: open\n", text)
        self.assertIn("- blocks: all\n- reversible: unknown\n", text.split("## Q2")[1])
        self.assertNotIn("options:", text.split("## Q2")[1])
        self.assertEqual(len(re.findall(r"^## Q\d+ ", text, re.M)), 2)

    def test_ask_logs_exactly_one_line(self):
        n = len(self.log_lines())
        self.ok("ask", "Use SQLite?", "-t", T)
        self.assertEqual(len(self.log_lines()), n + 1)
        self.assertIn("question Q1 parked", self.log_lines()[-1])

    def test_show_and_resume_count_the_questions_ask_writes(self):
        self.ok("ask", "one", "-t", T)
        self.ok("ask", "two", "-t", T)
        self.ok("answer", "1", "done", "-t", T)
        self.assertIn("open questions: 1", self.ok("show", "-t", T)[0])
        self.assertIn("open questions: 1", self.ok("resume", T)[0])

    def test_assume_needs_reversible_yes_and_an_option(self):
        before = self.snapshot()
        self.assertIn("--reversible yes", self.refuse(1, "ask", "Colour?", "--options", "red|blue", "--assume", "red", "-t", T))
        self.refuse(1, "ask", "Colour?", "--options", "red|blue", "--reversible", "unknown", "--assume", "red", "-t", T)
        self.refuse(1, "ask", "Colour?", "--options", "red|blue", "--reversible", "yes", "--assume", "pink", "-t", T)
        self.assertEqual(self.snapshot(), before)

    def test_assume_records_status_answer_and_journal_line(self):
        self.ok("ask", "Colour?", "--options", "red|blue", "--recommended", "blue", "--reversible", "yes", "--assume",
                "blue", "--as", "orchestrator", "-t", T)
        text = self.read(self.qpath()).decode()
        self.assertIn("- status: assumed\n", text)
        self.assertIn("- answer: blue (2026-03-01T10:00Z)\n", text)
        rec = self.journal()[-1]
        self.assertEqual((rec["kind"], rec["decision"], rec["reversible"], rec["role"], rec["question"]),
                         ("assumed_decision", "blue", True, "orchestrator", "Q1: Colour?"))
        self.assertIn("assumed blue", self.log_lines()[-1])
        self.assertEqual(qs.open_ones(self.d), [])

    def test_ask_validation(self):
        before = self.snapshot()
        for argv in (["ask", "", "-t", T], ["ask", "x", "--recommended", "a", "-t", T],
                     ["ask", "x", "--options", "a|b", "--recommended", "c", "-t", T],
                     ["ask", "x", "--options", "a||b", "-t", T], ["ask", "x", "--options", "a|a", "-t", T],
                     ["ask", "x", "--blocks", "3;4", "-t", T], ["ask", "x", "--reversible", "maybe", "-t", T],
                     ["ask", "x", "--as", "bad|role", "-t", T]):
            self.refuse(1, *argv)
        self.assertEqual(self.snapshot(), before)

    def test_newlines_in_text_become_spaces(self):
        self.ok("ask", "line one\nline two", "-t", T)
        self.assertIn("## Q1 — line one line two\n", self.read(self.qpath()).decode())

    def test_answer_resolves_only_open_questions(self):
        self.ok("ask", "first", "-t", T)
        self.ok("ask", "second", "-t", T)
        self.ok("ask", "third", "-t", T)
        out, _ = self.ok("answer", "Q2", "go with B\nplease", "-t", T)
        self.assertEqual(out.strip(), "Q2 answered")
        text = self.read(self.qpath()).decode()
        self.assertIn("## Q2 — second\n- status: answered\n", text)
        self.assertIn("- answer: go with B please (2026-03-01T10:00Z)\n", text.split("## Q2")[1].split("## Q3")[0])
        self.assertIn("- status: open", text.split("## Q3")[1])
        self.ok("answer", "3", "--withdraw", "-t", T)
        self.assertIn("- status: withdrawn", self.read(self.qpath()).decode().split("## Q3")[1])
        before = self.snapshot()
        for argv in (["answer", "Q2", "again"], ["answer", "3", "x"], ["answer", "Q9", "x"], ["answer", "Q1"],
                     ["answer", "Q1", "x", "--withdraw"], ["answer", "two", "x"]):
            self.refuse(1, *(argv + ["-t", T]))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([q.n for q in qs.open_ones(self.d)], [1])

    def test_answer_keeps_every_other_byte_of_the_file(self):
        self.ok("ask", "first", "--options", "a|b", "-t", T)
        self.ok("ask", "second", "-t", T)
        before = self.read(self.qpath()).decode()
        self.ok("answer", "1", "a", "-t", T)
        after = self.read(self.qpath()).decode()
        expect = before.replace("## Q1 \u2014 first\n- status: open\n", "## Q1 \u2014 first\n- status: answered\n")
        expect = expect.replace("- options: a | b\n", "- options: a | b\n- answer: a (2026-03-01T10:00Z)\n")
        self.assertEqual(after, expect)


class TestCheckpoint(FlowBase):
    def payload(self, out):
        return json.loads([l for l in out.splitlines() if l.startswith("{")][0])["questions"]

    def test_no_open_questions(self):
        out, _ = self.ok("checkpoint", "--ask", "-t", T)
        self.assertIn("0 open", out)
        rec = self.journal()[-1]
        self.assertEqual((rec["kind"], rec["questions"], rec["asked"]), ("checkpoint", 0, False))

    def test_without_ask_it_lists_and_journals_asked_false(self):
        self.ok("ask", "one", "-t", T)
        out, _ = self.ok("checkpoint", "-t", T)
        self.assertIn("1 open question", out)
        self.assertNotIn("{", out)
        rec = self.journal()[-1]
        self.assertEqual((rec["questions"], rec["asked"]), (1, False))

    def test_order_all_first_then_first_blocked_step_then_none(self):
        self.ok("ask", "none-q", "--blocks", "none", "-t", T)
        self.ok("ask", "ten", "--blocks", "10,11", "-t", T)
        self.ok("ask", "two", "--blocks", "2", "-t", T)
        self.ok("ask", "all-q", "--blocks", "all", "-t", T)
        self.ok("ask", "named", "--blocks", "M1", "-t", T)
        self.ok("ask", "all-too", "--blocks", "all", "-t", T)
        out, _ = self.ok("checkpoint", "--ask", "-t", T)
        got = self.payload(out)
        self.assertEqual([q["question"].split(": ", 1)[1] for q in got], ["all-q", "all-too", "two", "ten"])
        self.assertIn("2 more stay parked", out)
        rec = self.journal()[-1]
        self.assertEqual((rec["questions"], rec["asked"]), (6, True))

    def test_options_recommended_first_at_most_three_plus_hold_on(self):
        self.ok("ask", "colour", "--options", "red|green|blue|pink", "--recommended", "blue", "-t", T)
        self.ok("ask", "free", "-t", T)
        out, _ = self.ok("checkpoint", "--ask", "-t", T)
        q1, q2 = self.payload(out)
        self.assertEqual([o["label"] for o in q1["options"]], ["blue (recommended)", "red", "green", "Hold on"])
        self.assertEqual((q1["header"], q1["multiSelect"]), ("Q1", False))
        self.assertTrue(all(o["description"] for o in q1["options"]))
        self.assertEqual([o["label"] for o in q2["options"]], ["Answer in free text", "Hold on"])
        self.assertNotIn("stay parked", out)

    def test_only_open_questions_are_asked(self):
        self.ok("ask", "a", "-t", T)
        self.ok("ask", "b", "-t", T)
        self.ok("ask", "c", "--options", "x|y", "--reversible", "yes", "--assume", "x", "-t", T)
        self.ok("answer", "1", "ok", "-t", T)
        out, _ = self.ok("checkpoint", "--ask", "-t", T)
        self.assertEqual([q["header"] for q in self.payload(out)], ["Q2"])


class TestResume(FlowBase):
    def put(self, name, text):
        with open(os.path.join(self.d, name), "w") as fh:
            fh.write(text)

    def test_resume_is_read_only(self):
        self.ok("ask", "q", "-t", T)
        self.put("plan.md", "# Plan\n\n### Step 1 — a\n")
        # no lock file is created by a read: remove the one the setup commands left
        os.unlink(os.path.join(self.d, ".orch.lock"))
        before = core.tree_hash(self.root)
        self.ok("resume", T)
        self.ok("resume", "add-login")
        self.ok("resume", self.d)
        self.ok("-t", T, "resume")
        self.assertEqual(core.tree_hash(self.root), before)

    def test_every_state_has_a_suggestion_and_unknown_falls_back(self):
        want = {"planning": "spawn planner", "awaiting_approval": "plan gate", "implementing": "orch brief implement",
                "reviewing": "review", "awaiting_merge": "orch merge", "paused_usage_limit": "usage-check.sh",
                "done": "nothing to resume", "abandoned": "nothing to resume"}
        for state, text in want.items():
            self.ok("set", "task_state=%s" % state, "-t", T)
            out, _ = self.ok("resume", T)
            self.assertIn("state: %s" % state, out)
            line = [l for l in out.splitlines() if l.startswith("suggested next (verify):")]
            self.assertEqual(len(line), 1)
            self.assertIn(text, line[0], state)
        with open(os.path.join(self.d, "status.md"), "w") as fh:
            fh.write("# Status\n\n- task_state: holding_at_gate\n\n| role | state |\n|---|---|\n| orchestrator | in_progress |\n")
        out, _ = self.ok("resume", T)
        self.assertIn("state: holding_at_gate", out)
        self.assertIn("suggested next (verify): read status.md and log.md", out)

    def test_packet_content(self):
        self.ok("role", "implementer", "in_progress", "--step", "2", "--note", "working", "-t", T)
        self.ok("ask", "Use SQLite?", "--blocks", "3", "-t", T)
        self.put("plan.md", "# Plan\n\n### Step 1 — a\n\n### Step 2 — b\n\n## Revision 1\n\n### Step 2 — changes\n\n### Step 3 — c\n")
        out, _ = self.ok("resume", T)
        self.assertLessEqual(len(out.splitlines()), 40)
        self.assertIn("task: myapp/add-login", out)
        self.assertIn("state: planning  mode: standard  shape: tbd (planner decides)  milestone: n/a", out)
        self.assertIn("plan: %s (3 steps)" % os.path.join(self.d, "plan.md"), out)
        self.assertIn("  implementer | in_progress | 2 | working", out)
        self.assertIn("open questions: 1", out)
        self.assertIn("  Q1 (blocks 3): Use SQLite?", out)
        self.assertIn("log (last 8):", out)
        self.assertIn("orchestrator: question Q1 parked", out)

    def test_the_packet_stays_within_40_lines(self):
        for i in range(14):
            self.ok("role", "implementer:p%d" % i, "pending", "-t", T)
            self.ok("log", "event %d" % i, "-t", T)
            self.ok("ask", "question %d" % i, "-t", T)
        out, _ = self.ok("resume", T)
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 40)
        self.assertIn("suggested next (verify):", lines[-1])
        self.assertIn("event 13", out)
        self.assertNotIn("event 5\n", out)

    def test_value_rule_patterns(self):
        real = os.path.join(self.tmp, "Projects", "My App")
        os.makedirs(real)
        cases = [
            ({"repo": "/home/u/Projects/myapp (main, no worktree)", "worktree": "none (user: no worktree)",
              "base_branch": "main (direct, no worktree)"},
             ["repo: /home/u/Projects/myapp (missing)", "worktree: none", "base: main"]),
            ({"repo": real, "worktree": "<pending>|not yet created", "base_branch": "<branch>"},
             ["repo: %s" % real, "worktree: unknown", "base: unknown"]),
            ({"repo": real + " (main)", "worktree": "not yet created", "base_branch": "main"},
             ["repo: %s" % real, "worktree: not yet created", "base: main"]),
            ({"repo": "/abs/path/to/repo", "worktree": "/home/u/Projects/My App/.claude/worktrees/orch-x",
              "base_branch": "main"},
             ["repo: /abs/path/to/repo (missing)", "worktree: /home/u/Projects/My App/.claude/worktrees/orch-x (missing)"]),
        ]
        for fields, expect in cases:
            self.ok("set", *["%s=%s" % kv for kv in fields.items()] + ["-t", T])
            out, _ = self.ok("resume", T)
            for line in expect:
                self.assertIn(line + "\n", out)
            self.assertNotIn("(main", out)

    def git(self, repo, *args):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        subprocess.run(["git", "-C", repo] + list(args), env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_branch_tip_and_commits_ahead(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        repo = os.path.join(self.tmp, "Projects", "My App")
        os.makedirs(repo)
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "one")
        self.ok("set", "repo=%s (main)" % repo, "base_branch=main", "-t", T)
        out, _ = self.ok("resume", T)
        self.assertIn("branch: orch/add-login does not exist in the repo yet", out)
        self.git(repo, "checkout", "-q", "-b", "orch/add-login")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "two")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "three")
        out, _ = self.ok("resume", T)
        tip = subprocess.run(["git", "-C", repo, "rev-parse", "--short=10", "HEAD"], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout.strip()
        self.assertIn("branch: orch/add-login tip %s, 2 commit(s) ahead of main" % tip, out)
        self.ok("set", "base_branch=nope", "-t", T)
        self.assertIn("base nope not found", self.ok("resume", T)[0])

    def test_task_branch_none_is_reported_as_not_recorded(self):
        self.ok("set", "task_branch=none (no branch)", "-t", T)
        self.assertIn("branch: no task branch recorded (task_branch: none)", self.ok("resume", T)[0])

    def test_hostile_branch_names_are_not_passed_to_git(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        self.ok("set", "repo=%s" % repo, "task_branch=--output=x", "base_branch=main", "-t", T)
        out, _ = self.ok("resume", T)
        self.assertIn("branch: --output=x does not exist", out)
        self.assertFalse(os.path.exists(os.path.join(repo, "x")))

    def test_age_and_the_other_session_warning(self):
        t0 = flow.ts_epoch("2026-03-01T10:00Z")
        for secs, expect_warning, text in ((5 * 60, True, "5 min ago"), (3 * 3600 + 120, False, "3 h 2 min ago"),
                                           (3 * 86400, False, "4320 min")):
            for name in ("status.md", "log.md", "journal.jsonl"):
                os.utime(os.path.join(self.d, name), (t0 - secs, t0 - secs))
            out, _ = self.ok("resume", T)
            line = [l for l in out.splitlines() if l.startswith("last activity:")][0]
            self.assertEqual("another session may be active" in line, expect_warning, line)
            if secs < 86400:
                self.assertIn(text, line)
            else:
                self.assertIn("3 days ago", line)

    def test_bare_slug_resolution(self):
        self.init(repo="other", slug="add-login")
        err = self.refuse(1, "resume", "add-login")
        self.assertIn("myapp/add-login", err)
        self.assertIn("other/add-login", err)
        self.ok("resume", "other/add-login")
        self.init(repo="other", slug="fix-menu")
        out, _ = self.ok("resume", "fix-menu")
        self.assertIn("task: other/fix-menu", out)
        self.assertIn("no task with slug", self.refuse(1, "resume", "nothing-here"))

    def test_task_without_status_md_is_an_error(self):
        os.unlink(os.path.join(self.d, "status.md"))
        self.assertIn("no status.md", self.refuse(1, "resume", self.d))
        self.refuse(1, "resume")


class TestRegistered(FlowBase):
    def test_commands_list_has_every_composite_and_the_dev_guard_applies(self):
        out, _ = self.ok("commands")
        for name in ("spawn", "return", "end", "ask", "answer", "checkpoint", "resume"):
            self.assertIn(name, out.split())
        before = core.tree_hash(self.home)
        e = {"PATH": os.environ.get("PATH", ""), "HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1"}
        for argv in (["spawn", "planner", "--agent", "a", "--tier", "opus/high", "-t", T], ["return", "planner", "-t", T],
                     ["end", "merged", "-t", T], ["ask", "q", "-t", T], ["answer", "1", "x", "-t", T],
                     ["checkpoint", "-t", T]):
            r = subprocess.run([sys.executable, core.LAUNCHER] + argv, env=e, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(r.returncode, 1, argv)
            self.assertIn("development copy: set ORCH_DIR", r.stderr)
        self.assertEqual(core.tree_hash(self.home), before)


if __name__ == "__main__":
    unittest.main()
