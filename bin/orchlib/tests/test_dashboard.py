"""Dashboard data and local server (step 10). Neutral fixtures only; the server tests use real sockets on 127.0.0.1."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN)

from orchlib import cli, dashboard as db  # noqa: E402
from orchlib.paths import OrchError  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "dash")
NOW = 1772370000   # 2026-03-01T13:00:00Z
NOW_ENV = "2026-03-01T13:00Z"
# minutes before NOW at which each fixture task was last written
AGES = {"myapp/add-login": 30, "My App/fix-menu": 60, "myapp/paused-job": 360, "myapp/needs-ok": 420,
        "myapp/mystery": 60 * 24 * 28, "myapp/old-merged": 60 * 24 * 9, "myapp/second-merged": 60 * 24 * 8,
        "myapp/dropped": 60 * 24 * 7, "myapp/declined-job": 60 * 24 * 6}


def page_hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]


def snapshot(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            info = os.lstat(p)
            out.append((os.path.relpath(p, root), info.st_size, info.st_mtime_ns, info.st_ino))
        for sub in sorted(dirs):
            out.append((os.path.relpath(os.path.join(d, sub), root) + "/",))
    return out


def set_mtimes(root):
    for rel, minutes in AGES.items():
        t = (NOW - minutes * 60) * 10 ** 9
        for d, _, files in os.walk(os.path.join(root, rel)):
            for f in files:
                os.utime(os.path.join(d, f), ns=(t, t))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orch-dash-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, {"HOME": "/home/u"})   # task_dir shows as ~/… under the home directory only
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = os.path.join(self.tmp, "orch")
        shutil.copytree(os.path.join(FIX, "orch"), self.root)
        set_mtimes(self.root)
        self.html = os.path.join(self.tmp, "dashboard.html")
        with open(self.html, "w") as fh:
            fh.write("<!doctype html><title>t</title><p>page</p><script>var x = 1;</script>\n")

    def dash(self):
        return db.Dashboard(self.root, self.html)

    def task(self, data, tid):
        return next(t for t in data["tasks"] if t["id"] == tid)


class TestData(Base):
    def test_golden_json(self):
        data = self.dash().data(NOW)
        text = json.dumps(data, indent=1, sort_keys=False).replace(self.root, "<ROOT>")
        with open(os.path.join(FIX, "expected.json")) as fh:
            want = fh.read()
        self.assertEqual(json.loads(text), json.loads(want))
        self.assertEqual(list(json.loads(want)), ["generated", "page_version", "tasks", "events", "finished"])

    def test_keys_are_exact_and_in_order(self):
        data = self.dash().data(NOW)
        keys = ["id", "repo", "slug", "state", "state_words", "mode", "shape", "milestone", "milestones_done",
                "milestones_total", "steps_done", "steps_total", "percent", "current", "roles", "questions_open",
                "questions", "last_log", "updated", "updated_age_min", "tokens_reported", "usage_first",
                "usage_last", "paused", "worktree", "task_dir"]
        for t in data["tasks"]:
            self.assertEqual(list(t), keys)
        self.assertEqual(data["generated"], "2026-03-01T13:00:00Z")
        self.assertEqual(data["page_version"], page_hash(self.html))
        for e in data["events"]:
            self.assertEqual(list(e), ["ts", "id", "kind", "words"])
        self.assertEqual(list(data["finished"]), ["merged", "abandoned"])

    def test_single_task_numbers(self):
        t = self.task(self.dash().data(NOW), "myapp/add-login")
        self.assertEqual((t["state"], t["state_words"], t["mode"], t["shape"]), ("implementing", "Building", "standard", "single"))
        self.assertEqual((t["steps_done"], t["steps_total"], t["percent"]), (2, 4, 50))   # "step 2 (2026-03-01)": the date is not a step
        self.assertEqual((t["milestones_done"], t["milestones_total"], t["milestone"]), (None, None, None))
        self.assertEqual(t["tokens_reported"], 100000)
        self.assertEqual(t["usage_first"], {"5h": 12, "7d": 40})
        self.assertEqual(t["usage_last"], {"5h": 31, "7d": 44})
        self.assertEqual((t["questions_open"], t["questions"]), (1, [{"id": "Q1", "text": "Use a session cookie or a token?", "blocks": "3"}]))
        self.assertEqual((t["updated"], t["updated_age_min"], t["paused"]), ("2026-03-01T12:30Z", 30, False))
        self.assertEqual(t["current"], "implementer: working on step 3")
        self.assertEqual(t["last_log"], "2026-03-01T11:00Z implementer: step 2 done")
        self.assertEqual(t["worktree"], "/home/u/Projects/myapp-wt")

    def test_program(self):
        t = self.task(self.dash().data(NOW), "My App/fix-menu")
        # roadmap rows: the table under "## Milestones" only (a commented example and a later `| 9 |` row do not count)
        self.assertEqual((t["shape"], t["milestone"], t["milestones_total"]), ("roadmap", "M2 (menu)", 3))
        self.assertEqual(t["milestones_done"], 1)   # two milestone_end lines for M1 count once; M2's outcome is blocked
        self.assertEqual((t["steps_total"], t["steps_done"], t["percent"]), (3, 1, 33))   # the milestone's plan, not roadmap.md

    def test_program_without_a_unique_milestone_plan(self):
        d = os.path.join(self.root, "My App", "fix-menu")
        shutil.copytree(os.path.join(d, "m02-menu"), os.path.join(d, "m02-other"))
        t = self.task(self.dash().data(NOW), "My App/fix-menu")
        self.assertEqual((t["steps_total"], t["steps_done"], t["percent"]), (None, None, None))

    def test_paused_and_waiting(self):
        data = self.dash().data(NOW)
        p = self.task(data, "myapp/paused-job")
        self.assertEqual((p["state_words"], p["paused"], p["current"]), ("Paused: usage limit", True, "implementer: paused"))
        self.assertEqual((p["steps_total"], p["steps_done"]), (8, 7))
        n = self.task(data, "myapp/needs-ok")
        self.assertEqual(n["state_words"], "Waiting for your approval")
        self.assertIn("<script>alert(1)</script>", n["roles"][1]["note"])   # raw in the data, escaped by the page

    def test_unknown_state_and_wrong_types(self):
        t = self.task(self.dash().data(NOW), "myapp/mystery")
        self.assertEqual((t["state"], t["state_words"]), ("unknown", "Unknown state: unknown"))
        self.assertEqual(t["tokens_reported"], None)    # "7" is a string
        self.assertEqual(t["usage_first"], {"5h": None, "7d": 8})   # `event` key read; "12" is a string
        self.assertEqual((t["steps_total"], t["steps_done"], t["percent"], t["questions_open"]), (None, None, None, 0))
        self.assertEqual(t["milestone"], None)

    def test_state_words_table(self):
        want = {"planning": "Planning", "awaiting_approval": "Waiting for your approval", "implementing": "Building",
                "reviewing": "Reviewing", "awaiting_merge": "Ready to merge", "paused_usage_limit": "Paused: usage limit",
                "done": "Done", "abandoned": "Abandoned"}
        self.assertEqual(db.STATE_WORDS, want)
        d = os.path.join(self.root, "myapp", "odd-state")
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write("# s\n\n- task_state: m3_merged_stopped\n\n| role | state | timestamp (UTC) | last completed step | notes |\n|---|---|---|---|---|\n")
        t = self.task(self.dash().data(NOW), "myapp/odd-state")
        self.assertEqual(t["state_words"], "Unknown state: m3_merged_stopped")

    def test_finished_lists(self):
        f = self.dash().data(NOW)["finished"]
        self.assertEqual([r["id"] for r in f["merged"]], ["myapp/second-merged", "myapp/old-merged"])
        self.assertEqual([r["id"] for r in f["abandoned"]], ["myapp/declined-job", "myapp/dropped"])   # declined counts as abandoned
        self.assertEqual(f["merged"][0]["ended"], "2026-02-21T10:00Z")

    def test_finished_without_task_end_uses_the_file_time(self):
        d = os.path.join(self.root, "myapp", "legacy-done")
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write("# s\n\n- task_state: done\n")
        t = (NOW - 3600) * 10 ** 9
        os.utime(os.path.join(d, "status.md"), ns=(t, t))
        f = self.dash().data(NOW)["finished"]
        self.assertEqual(f["merged"][0], {"id": "myapp/legacy-done", "ended": "2026-03-01T12:00Z"})

    def test_events_words_and_order(self):
        ev = self.dash().data(NOW)["events"]
        self.assertLessEqual(len(ev), 30)
        self.assertEqual([e["ts"] for e in ev], sorted((e["ts"] for e in ev), reverse=True))
        words = [(e["id"], e["words"]) for e in ev]
        for want in [("myapp/add-login", "Started implementer (sonnet/high)"),
                     ("myapp/add-login", "implementer finished: done"),
                     ("myapp/add-login", "code review round 1: pass"),
                     ("myapp/add-login", "Asked you 2 questions"),
                     ("My App/fix-menu", "Merged 0123456 into main"),
                     ("My App/fix-menu", "plan gate: approved"),
                     ("myapp/paused-job", "Paused for usage (5h 96%)"),
                     ("myapp/old-merged", "Task ended: merged")]:
            self.assertIn(want, words)
        self.assertNotIn(("myapp/add-login", "Asked you 1 question"), words)   # asked: false is not shown
        for e in ev:
            self.assertNotEqual(e["kind"], "judgement")
            self.assertNotEqual(e["kind"], "task_start")

    def test_each_event_sentence(self):
        w = db.event_words
        self.assertEqual(w("spawn", {"role": "planner", "tier": "opus/high"}), "Started planner (opus/high)")
        self.assertEqual(w("spawn", {}), "Started unknown (unknown)")
        self.assertEqual(w("return", {"role": "planner", "outcome": "blocked"}), "planner finished: blocked")
        self.assertEqual(w("review_round", {"reviewer": "security", "round": 2, "verdict": "fail"}), "security review round 2: fail")
        self.assertEqual(w("auto_merge", {"sha": "abcdef0123", "base": "main"}), "Merged abcdef0 into main")
        self.assertEqual(w("auto_merge", {"sha": "not hex!", "base": "main"}), "Merged unknown into main")
        self.assertEqual(w("gate", {"gate": "merge", "outcome": "declined"}), "merge gate: declined")
        self.assertEqual(w("pause", {"usage_5h": 95}), "Paused for usage (5h 95%)")
        self.assertEqual(w("pause", {"usage_5h": "95"}), "Paused for usage")
        self.assertEqual(w("resume", {}), "Resumed")
        self.assertEqual(w("checkpoint", {"asked": True, "questions": 1}), "Asked you 1 question")
        self.assertEqual(w("checkpoint", {"asked": True, "questions": 3}), "Asked you 3 questions")
        self.assertIsNone(w("checkpoint", {"asked": False, "questions": 3}))
        self.assertEqual(w("task_end", {"outcome": "abandoned"}), "Task ended: abandoned")
        for kind in ("tier_choice", "judgement", "override", "plan_revision", "lesson", "sketch", "blocker", "task_start", "x"):
            self.assertIsNone(w(kind, {"role": "r"}))

    def test_event_values_are_clipped_and_stripped_of_control_characters(self):
        w = db.event_words("return", {"role": "a\nb\x00c d", "outcome": "x" * 500})
        self.assertNotIn("\n", w)
        self.assertNotIn("\x00", w)
        self.assertNotIn(" ", w)
        self.assertLess(len(w), 200)

    def test_percent_and_step_parsing(self):
        rows = lambda *cells: [("implementer", {"step": c}) for c in cells]
        self.assertEqual(db.steps_done_from(rows("step 9"), 13), 9)
        self.assertEqual(db.steps_done_from(rows("step 3", "step 7"), 13), 7)
        self.assertEqual(db.steps_done_from(rows("step 12a"), 13), None)         # a lettered id is not an integer
        self.assertEqual(db.steps_done_from(rows("steps 1-3"), 13), 3)
        self.assertEqual(db.steps_done_from(rows("step 99"), 13), None)          # above the total
        self.assertEqual(db.steps_done_from(rows("2026-10-22T07:55Z step 2"), 13), 2)
        self.assertEqual(db.steps_done_from([("planner", {"step": "step 5"})], 13), None)
        self.assertEqual(db.steps_done_from([("implementer:b", {"step": "step 5"})], 13), 5)
        self.assertEqual(db.steps_done_from(rows("step 5"), None), None)

    def test_dot_dirs_and_stray_files_are_not_tasks(self):
        data = self.dash().data(NOW)
        ids = [t["id"] for t in data["tasks"]] + [r["id"] for k in data["finished"].values() for r in k]
        self.assertFalse([i for i in ids if "/." in i or i.startswith(".")], ids)
        with open(os.path.join(self.root, "ACTIVE.md"), "w") as fh:
            fh.write("x")
        os.makedirs(os.path.join(self.root, "myapp", "no-status"))
        self.assertEqual(len(self.dash().data(NOW)["tasks"]), len(data["tasks"]))

    def test_same_count_as_the_index_rules(self):
        """Active = what orch-index.sh lists (every state outside done/abandoned/merged, dot dirs aside)."""
        data = self.dash().data(NOW)
        self.assertEqual(sorted(t["id"] for t in data["tasks"]),
                         sorted(["myapp/add-login", "My App/fix-menu", "myapp/paused-job", "myapp/needs-ok", "myapp/mystery"]))
        self.assertEqual(sum(len(v) for v in data["finished"].values()), 4)

    def test_json_bytes_are_ascii_and_have_no_raw_markup(self):
        body = self.dash().json_bytes(NOW)
        body.decode("ascii")
        for bad in (b"<", b">", b"&"):
            self.assertNotIn(bad, body)
        self.assertEqual(json.loads(body)["tasks"][0]["id"], self.dash().data(NOW)["tasks"][0]["id"])
        self.assertIn("<script>alert(1)</script>", json.dumps(json.loads(body)))   # decodes back to the same text

    def test_data_never_leaves_the_task_tree(self):
        """Symlinks, fifos and huge files are not read."""
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        for name, text in (("status.md", "- task_state: implementing\n"), ("journal.jsonl", '{"ts":"2026-03-01T00:00Z","kind":"return","role":"leak","outcome":"done","tokens":777}\n')):
            with open(os.path.join(outside, name), "w") as fh:
                fh.write(text)
        os.symlink(outside, os.path.join(self.root, "linked-repo"))                       # symlinked repo dir
        os.makedirs(os.path.join(self.root, "myapp", "t-link"))
        os.symlink(os.path.join(outside, "status.md"), os.path.join(self.root, "myapp", "t-link", "status.md"))
        os.symlink(outside, os.path.join(self.root, "myapp", "t-linkdir"))                # symlinked task dir
        d = os.path.join(self.root, "myapp", "add-login")
        os.rename(os.path.join(d, "journal.jsonl"), os.path.join(d, "journal.real"))
        os.symlink(os.path.join(outside, "journal.jsonl"), os.path.join(d, "journal.jsonl"))
        os.makedirs(os.path.join(self.root, "myapp", "t-fifo"))
        os.mkfifo(os.path.join(self.root, "myapp", "t-fifo", "status.md"))
        data = self.dash().data(NOW)
        ids = [t["id"] for t in data["tasks"]]
        self.assertFalse([i for i in ids if i.startswith("linked-repo") or "t-link" in i or "t-fifo" in i], ids)
        self.assertIsNone(self.task(data, "myapp/add-login")["tokens_reported"])           # the symlinked journal was ignored
        self.assertNotIn("leak", json.dumps(data))
        with mock.patch.object(db, "MAX_FILE", 100):
            data = self.dash().data(NOW)    # status.md files above the cap: no tasks, no crash
        self.assertEqual(data["tasks"], [])

    def test_read_file_only_reads_regular_files_within_the_cap(self):
        d = os.path.join(self.tmp, "rf")
        os.makedirs(d)
        reg = os.path.join(d, "reg")
        with open(reg, "wb") as fh:
            fh.write(b"12345")
        os.symlink(reg, os.path.join(d, "link"))
        os.mkfifo(os.path.join(d, "fifo"))
        os.makedirs(os.path.join(d, "dir"))
        self.assertEqual(db.read_file(reg), b"12345")
        self.assertEqual(db.read_file(reg, 5), b"12345")
        self.assertIsNone(db.read_file(reg, 4))
        for name in ("link", "fifo", "dir", "missing"):
            self.assertIsNone(db.read_file(os.path.join(d, name)), name)
            self.assertIsNone(db.stat_regular(os.path.join(d, name)), name)
        self.assertIsNotNone(db.stat_regular(reg))

    def test_events_are_capped_at_30_across_tasks_newest_first(self):
        d = os.path.join(self.root, "myapp", "busy")
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "w") as fh:
            fh.write("# s\n\n- task_state: implementing\n")
        with open(os.path.join(d, "journal.jsonl"), "w") as fh:
            for i in range(40):
                fh.write(json.dumps({"ts": "2026-03-02T%02d:%02dZ" % (i // 60, i % 60), "kind": "resume"}) + "\n")
        ev = self.dash().data(NOW)["events"]
        self.assertEqual(len(ev), 30)
        self.assertEqual(ev[0]["ts"], "2026-03-02T00:39Z")
        self.assertEqual(ev[-1]["ts"], "2026-03-02T00:10Z")

    def test_milestones_done_counts_done_and_old_merged_outcomes(self):
        d = os.path.join(self.root, "My App", "fix-menu")
        with open(os.path.join(d, "journal.jsonl"), "a") as fh:
            fh.write(json.dumps({"ts": "2026-03-01T09:45Z", "kind": "milestone_end", "milestone": 3, "outcome": "merged"}) + "\n")
            fh.write(json.dumps({"ts": "2026-03-01T09:46Z", "kind": "milestone_end", "milestone": 4, "outcome": "abandoned"}) + "\n")
        self.assertEqual(self.task(self.dash().data(NOW), "My App/fix-menu")["milestones_done"], 2)

    def test_broken_inputs_do_not_crash(self):
        d = os.path.join(self.root, "myapp", "broken")
        os.makedirs(d)
        with open(os.path.join(d, "status.md"), "wb") as fh:
            fh.write(b"- task_state: implementing\n\xff\xfe\n| role | state |\n|---|---|\n| implementer | in_progress |\n")
        with open(os.path.join(d, "journal.jsonl"), "wb") as fh:
            fh.write(b'\xff\xfe\n{"kind": 1}\n{"kind":"spawn","usage_5h":true,"ts":3}\n{"kind":"return","tokens":true,"ts":"2026-03-01T00:00Z"}\n')
        with open(os.path.join(d, "plan.md"), "wb") as fh:
            fh.write(b"\xff### Step 1 \xff\n### Step 2\n")
        t = self.task(self.dash().data(NOW), "myapp/broken")
        self.assertEqual((t["tokens_reported"], t["usage_first"]), (None, None))   # booleans are not integers


class TestCache(Base):
    def test_hit_without_change_and_recompute_after_a_touch(self):
        d = self.dash()
        d.data(NOW)
        d.data(NOW + 60)
        d.json_bytes(NOW + 120)
        self.assertEqual(d.computes, 1)
        p = os.path.join(self.root, "myapp", "add-login", "log.md")
        t = os.stat(p).st_mtime_ns + 5 * 10 ** 9
        os.utime(p, ns=(t, t))
        d.data(NOW)
        self.assertEqual(d.computes, 2)

    def test_ages_move_without_a_recompute(self):
        d = self.dash()
        a = self.task(d.data(NOW), "myapp/add-login")["updated_age_min"]
        b = self.task(d.data(NOW + 600), "myapp/add-login")["updated_age_min"]
        self.assertEqual((a, b, d.computes), (30, 40, 1))

    def test_migrate_style_write_that_restores_the_mtime_recomputes(self):
        d = self.dash()
        before = self.task(d.data(NOW), "myapp/add-login")
        p = os.path.join(self.root, "myapp", "add-login", "status.md")
        info = os.stat(p)
        with open(p, "rb") as fh:
            text = fh.read()
        new = text.replace(b"- mode: standard", b"- mode: lite    ")
        self.assertEqual(len(new), len(text))
        tmp = p + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(new)
        os.utime(tmp, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(tmp, p)                                   # new inode, same size, same mtime_ns
        self.assertEqual(os.stat(p).st_mtime_ns, info.st_mtime_ns)
        after = self.task(d.data(NOW), "myapp/add-login")
        self.assertEqual((d.computes, before["mode"], after["mode"]), (2, "standard", "lite"))

    def test_size_change_alone_recomputes(self):
        d = self.dash()
        d.data(NOW)
        p = os.path.join(self.root, "myapp", "add-login", "log.md")
        info = os.stat(p)
        with open(p, "ab") as fh:                     # same inode, same mtime_ns, one byte more
            fh.write(b"x")
        os.utime(p, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(os.stat(p).st_mtime_ns, info.st_mtime_ns)
        d.data(NOW)
        self.assertEqual(d.computes, 2)

    def test_same_size_edit_recomputes(self):
        d = self.dash()
        d.data(NOW)
        p = os.path.join(self.root, "myapp", "add-login", "status.md")
        with open(p, "rb") as fh:
            text = fh.read()
        with open(p, "wb") as fh:
            fh.write(text.replace(b"- mode: standard", b"- mode: lite    "))
        t = os.stat(p).st_mtime_ns + 10 ** 9
        os.utime(p, ns=(t, t))
        d.data(NOW)
        self.assertEqual(d.computes, 2)

    def test_new_and_removed_tasks_recompute(self):
        d = self.dash()
        d.data(NOW)
        shutil.copytree(os.path.join(self.root, "myapp", "paused-job"), os.path.join(self.root, "myapp", "copy-job"))
        self.assertIn("myapp/copy-job", [t["id"] for t in d.data(NOW)["tasks"]])
        shutil.rmtree(os.path.join(self.root, "myapp", "copy-job"))
        self.assertNotIn("myapp/copy-job", [t["id"] for t in d.data(NOW)["tasks"]])
        self.assertEqual(d.computes, 3)

    def test_watched_set_is_the_named_files_only(self):
        d = self.dash()
        d.data(NOW)
        task = os.path.join(self.root, "myapp", "add-login")
        for rel in ("artifacts/x/status.md", "stage/bin/plan.md", "notes.md", "m02-menu/deeper/plan.md", "artifacts/journal.jsonl"):
            p = os.path.join(task, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("x")
        d.data(NOW)
        self.assertEqual(d.computes, 1, "files outside the watched set must not trigger a recompute")
        p = os.path.join(self.root, "My App", "fix-menu", "m02-menu", "plan.md")
        t = os.stat(p).st_mtime_ns + 10 ** 9
        os.utime(p, ns=(t, t))
        d.data(NOW)
        self.assertEqual(d.computes, 2)
        for name in db.WATCH:
            self.assertIn(os.path.join(task, name), db.watched(task))

    def test_concurrent_requests_compute_once(self):
        d = self.dash()
        threads = [threading.Thread(target=d.data, args=(NOW,)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(d.computes, 1)

    def test_page_version_follows_the_html(self):
        d = self.dash()
        a = d.data(NOW)["page_version"]
        with open(self.html, "a") as fh:
            fh.write("<!-- more -->\n")
        b = d.data(NOW)["page_version"]
        self.assertNotEqual(a, b)
        self.assertEqual(b, page_hash(self.html))
        self.assertEqual(d.computes, 1)
        os.unlink(self.html)
        self.assertIsNone(d.data(NOW)["page_version"])


class TestServer(Base):
    def setUp(self):
        Base.setUp(self)
        self.srv = db.make_server(self.root, 0, self.html)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(5)

    def raw(self, data, port=None, timeout=5):
        s = socket.create_connection(("127.0.0.1", port or self.port), timeout=timeout)
        try:
            s.sendall(data)
            chunks = []
            while True:
                try:
                    c = s.recv(65536)
                except ConnectionResetError:
                    break
                if not c:
                    break
                chunks.append(c)
        finally:
            s.close()
        blob = b"".join(chunks)
        head, _, body = blob.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split()[1]) if lines[0].startswith("HTTP/") else None
        headers = {}
        for l in lines[1:]:
            k, _, v = l.partition(":")
            headers.setdefault(k.strip().lower(), []).append(v.strip())
        return status, headers, body

    def get(self, target, host=None, method="GET", extra=b"", version=b"HTTP/1.1"):
        host = "127.0.0.1:%d" % self.port if host is None else host
        req = method.encode() + b" " + target + b" " + version + b"\r\nHost: " + host.encode() + b"\r\n" + extra + b"Connection: close\r\n\r\n"
        return self.raw(req)

    def all_files(self):
        out = []
        for d, _, files in os.walk(self.root):
            for f in files:
                with open(os.path.join(d, f), "rb") as fh:
                    out.append(fh.read())
        return [b for b in out if b]

    # -- binding and handler surface -------------------------------------------------------------

    def test_bound_to_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertEqual(self.srv.socket.getsockname()[0], "127.0.0.1")
        self.assertEqual(self.srv.socket.family, socket.AF_INET)

    def test_non_loopback_address_is_refused(self):
        ips = set()
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("192.0.2.1", 9))     # no packet is sent; this only picks the outbound interface address
            ips.add(probe.getsockname()[0])
            probe.close()
        except OSError:
            pass
        try:
            ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
        ips = [i for i in ips if not i.startswith("127.") and i != "0.0.0.0"]
        if not ips:
            self.skipTest("no non-loopback address on this machine")
        for ip in ips:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            try:
                self.assertNotEqual(s.connect_ex((ip, self.port)), 0, "server answered on %s" % ip)
            finally:
                s.close()

    def test_handler_defines_only_get_and_head(self):
        self.assertEqual(sorted(n for n in dir(db.Handler) if n.startswith("do_")), ["do_GET", "do_HEAD"])

    def test_make_server_never_binds_a_wildcard(self):
        with mock.patch.object(db.http.server.HTTPServer, "__init__", return_value=None) as init:
            srv = db.Server.__new__(db.Server)
            db.Server.__init__(srv, 0, None)
        self.assertEqual(init.call_args[0][1][0], "127.0.0.1")
        self.assertEqual(db.HOST, "127.0.0.1")

    # -- the two routes --------------------------------------------------------------------------

    def test_root_and_json(self):
        status, h, body = self.get(b"/")
        self.assertEqual(status, 200)
        with open(self.html, "rb") as fh:
            self.assertEqual(body, fh.read())
        self.assertEqual(h["content-type"], ["text/html; charset=utf-8"])
        status, h, body = self.get(b"/dashboard.json")
        self.assertEqual(status, 200)
        self.assertEqual(h["content-type"], ["application/json; charset=utf-8"])
        data = json.loads(body)
        self.assertEqual(sorted(data), ["events", "finished", "generated", "page_version", "tasks"])
        self.assertEqual(len(data["tasks"]), 5)
        self.assertEqual(h["content-length"], [str(len(body))])

    def test_headers(self):
        for target in (b"/", b"/dashboard.json", b"/nope"):
            _, h, _ = self.get(target)
            self.assertEqual(h["cache-control"], ["no-store"], target)
            self.assertEqual(h["x-content-type-options"], ["nosniff"], target)
            self.assertFalse([k for k in h if k.startswith("access-control-")], (target, h))
        _, h, _ = self.get(b"/")
        csp = h["content-security-policy"][0]
        self.assertIn("default-src 'none'", csp)
        digest = hashlib.sha256(b"var x = 1;").digest()
        import base64
        self.assertIn("script-src 'sha256-%s'" % base64.b64encode(digest).decode(), csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("script-src 'unsafe-inline'", csp)
        self.assertIn("connect-src 'self'", csp)

    def test_head_matches_get_without_a_body(self):
        for target in (b"/", b"/dashboard.json", b"/missing"):
            gs, gh, gb = self.get(target)
            hs, hh, hb = self.get(target, method="HEAD")
            self.assertEqual((hs, hb), (gs, b""), target)
            self.assertEqual(hh["content-length"], gh["content-length"], target)
            self.assertEqual(hh["content-type"], gh["content-type"], target)

    def test_query_string_is_ignored_for_routing_only(self):
        for target in (b"/?a=b", b"/dashboard.json?_=1", b"/dashboard.json?../../etc/passwd", b"/?"):
            self.assertEqual(self.get(target)[0], 200, target)
        for target in (b"/x?/", b"/?/../x", b"/dashboard.json/?a"):
            if target.startswith(b"/?"):
                self.assertEqual(self.get(target)[0], 200, target)
            else:
                self.assertEqual(self.get(target)[0], 404, target)

    def test_page_missing_is_a_fixed_500(self):
        os.unlink(self.html)
        status, _, body = self.get(b"/")
        self.assertEqual((status, body), (500, b"Server error\n"))
        status, _, body = self.get(b"/dashboard.json")
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["page_version"])

    # -- Host header and request target -----------------------------------------------------------

    def test_host_header(self):
        p = self.port
        good = ["127.0.0.1:%d" % p, "localhost:%d" % p, "LOCALHOST:%d" % p, "127.0.0.1:%d " % p]
        for host in good:
            self.assertEqual(self.get(b"/", host=host)[0], 200, host)
        bad = ["evil.com", "127.0.0.1:%d.evil.com" % p, "127.0.0.1:%d" % (p + 1), "127.0.0.1", "localhost",
               "evil.com:%d" % p, "localhost.evil.com:%d" % p, "a@127.0.0.1:%d" % p, "[::1]:%d" % p,
               "0.0.0.0:%d" % p, "127.0.0.1:%d:%d" % (p, p), "", "127.1:%d" % p, "2130706433:%d" % p,
               "127.0.0.1:%d,evil.com" % p, "127.0.0.1:%d evil.com" % p]
        for host in bad:
            status, _, body = self.get(b"/", host=host)
            self.assertEqual((status, body), (400, b"Bad request\n"), repr(host))
            self.assertEqual(self.get(b"/dashboard.json", host=host)[0], 400, repr(host))

    def test_missing_and_duplicate_host(self):
        h = "127.0.0.1:%d" % self.port
        self.assertEqual(self.raw(b"GET / HTTP/1.1\r\nConnection: close\r\n\r\n")[0], 400)
        self.assertEqual(self.raw(b"GET / HTTP/1.0\r\n\r\n")[0], 400)
        two = ("GET / HTTP/1.1\r\nHost: %s\r\nHost: %s\r\n\r\n" % (h, h)).encode()
        self.assertEqual(self.raw(two)[0], 400)
        evil = ("GET / HTTP/1.1\r\nHost: %s\r\nHost: evil.com\r\n\r\n" % h).encode()
        self.assertEqual(self.raw(evil)[0], 400)
        evil = ("GET / HTTP/1.1\r\nHost: evil.com\r\nHost: %s\r\n\r\n" % h).encode()
        self.assertEqual(self.raw(evil)[0], 400)
        folded = ("GET / HTTP/1.1\r\nHost: %s\r\n .evil.com\r\n\r\n" % h).encode()
        self.assertEqual(self.raw(folded)[0], 400)
        self.assertEqual(self.raw(("GET / HTTP/1.1\r\nX-Forwarded-Host: %s\r\n\r\n" % h).encode())[0], 400)

    def test_absolute_form_and_odd_targets_are_400(self):
        for target in (b"http://evil/", b"http://127.0.0.1:%d/" % self.port, b"HTTP://evil/dashboard.json",
                       b"*", b"evil.com:80", b"dashboard.json", b"\\x", b"ftp://x/", b"x/y"):
            status, _, body = self.get(target)
            self.assertEqual((status, body), (400, b"Bad request\n"), target)
        status, _, body = self.raw(("GET http://evil/ HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port).encode())
        self.assertEqual((status, body), (400, b"Bad request\n"))

    def test_path_traversal_and_other_paths_are_a_fixed_404(self):
        targets = [b"/../", b"/..", b"/..%2f..%2fetc/passwd", b"/..%2F..%2Fetc%2Fpasswd", b"//etc/passwd", b"//",
                   b"/%2e%2e/", b"/%2e%2e%2f", b"/dashboard.json/../x", b"/dashboard.json/..", b"/./", b"/dashboard.json/.",
                   b"/\\..\\..\\etc\\passwd", b"/%5c..%5cx", b"/%00", b"/dashboard.json%00.png", b"/dashboard.json\x00",
                   b"/\x00", b"/" + b"a" * 9000, b"/" + b"../" * 3000, b"/%2e%2e/" * 1500,
                   b"/index.html", b"/dashboard.html", b"/dashboard.json/", b"/Dashboard.json", b"/DASHBOARD.JSON",
                   b"/dashboard%2ejson", b"/status.md", b"/myapp/add-login/status.md", b"/myapp/", b"/ACTIVE.md",
                   b"/etc/passwd", b"/favicon.ico", b"/%", b"/%zz", b"/%ff", b"/;/", b"/#x", b"/.git/config",
                   b"/orchlib/dashboard.py", b"/../../../../../../../../etc/hosts"]
        before = snapshot(self.root)
        files = self.all_files()
        for target in targets:
            status, _, body = self.get(target)
            self.assertIn(status, (404, 400), target[:60])
            if status == 404:
                self.assertEqual(body, b"Not found\n", target[:60])
            self.assertNotIn(body, files)
            self.assertLess(len(body), 40)
        self.assertEqual(snapshot(self.root), before)

    def test_encoded_path_is_not_decoded_to_a_route(self):
        self.assertEqual(self.get(b"/%64ashboard.json")[0], 404)
        self.assertEqual(self.get(b"/dashboard.js%6fn")[0], 404)

    def test_over_long_request_line_and_headers(self):
        status, _, body = self.raw(b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port)
        self.assertIn(status, (414, None))
        if status:
            self.assertEqual(body, b"Request too long\n")
        many = b"".join(b"X-%d: y\r\n" % i for i in range(150))
        status, _, body = self.raw(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % self.port + many + b"\r\n")
        self.assertEqual((status, body), (431, b"Headers too large\n"))
        status, _, body = self.raw(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX: " % self.port + b"y" * 70000 + b"\r\n\r\n")
        self.assertEqual((status, body), (431, b"Headers too large\n"))
        self.assertEqual(self.get(b"/")[0], 200)

    def test_garbage_and_old_protocols(self):
        for blob in (b"\x00\x01\x02\r\n\r\n", b"\r\n", b"GARBAGE\r\n\r\n", b"GET\r\n\r\n", b"GET / HTTP/9.9\r\nHost: x\r\n\r\n",
                     b"GET / HTTP/1.1\r\n", b"", b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nBad header line\r\n\r\n" % self.port):
            try:
                status, _, body = self.raw(blob, timeout=1)    # a half request just waits for the server's own timeout
            except OSError:
                continue
            if status is not None:
                self.assertIn(status, (200, 400, 505), blob)   # a malformed header line is ignored; a bad Host is not
            self.assertNotIn(b"<html", body.lower())
        status, _, body = self.raw(b"GET /\r\n\r\n")      # HTTP/0.9 style: no Host header, so it is refused
        self.assertNotIn(b"<p>page</p>", body)
        self.assertEqual(self.get(b"/")[0], 200)       # and the server is still alive

    # -- methods ---------------------------------------------------------------------------------

    def test_other_methods_are_405(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CONNECT", "PROPFIND", "FOO", "get", "Get"):
            for target in (b"/", b"/dashboard.json", b"/other"):
                status, h, body = self.get(target, method=method)
                self.assertEqual((status, body), (405, b"Method not allowed\n"), (method, target))
                self.assertEqual(h["allow"], ["GET, HEAD"])
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, _, body = self.raw(("%s /dashboard.json HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: 11\r\n\r\nhello world" % (method, self.port)).encode())
            self.assertEqual((status, body), (405, b"Method not allowed\n"), method)
            status, _, body = self.raw(("%s / HTTP/1.1\r\nHost: evil.com\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}" % method).encode())
            self.assertEqual(status, 405, method)        # no Host rule is needed for a method that is refused anyway

    def test_request_storm_leaves_the_task_tree_unchanged(self):
        before = snapshot(self.root)
        methods = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"]
        targets = [b"/", b"/dashboard.json", b"/x", b"/../etc/passwd", b"/status.md", b"/dashboard.json?x=1"]
        for _ in range(3):
            for m in methods:
                for t in targets:
                    self.get(t, method=m)
                    self.raw(("%s %s HTTP/1.1\r\nHost: evil.com\r\nContent-Length: 4\r\n\r\ndata" % (m, t.decode())).encode())
        self.assertEqual(snapshot(self.root), before)
        # nothing was created beside the tree either
        self.assertEqual(sorted(os.listdir(self.tmp)), sorted(["orch", "dashboard.html"]))

    def test_a_stalled_client_does_not_block_others(self):
        s = socket.create_connection(("127.0.0.1", self.port))
        try:
            s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Slow: " % self.port)
            start = time.time()
            self.assertEqual(self.get(b"/dashboard.json")[0], 200)
            self.assertLess(time.time() - start, 3)
        finally:
            s.close()

    def test_http_0_9_request_line_is_refused_with_the_full_header_set(self):
        for target in (b"/", b"/dashboard.json"):
            blob = b"GET " + target + b"\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port
            status, h, body = self.raw(blob)
            self.assertEqual((status, body), (400, b"Bad request\n"), target)
            self.assertEqual(h["cache-control"], ["no-store"])
            self.assertEqual(h["x-content-type-options"], ["nosniff"])
            self.assertNotIn(b"<p>page</p>", body)
            self.assertNotIn(b"page_version", body)
        status, h, body = self.raw(b"GET /\r\n\r\n")        # no Host either: the same refusal
        self.assertEqual((status, body), (400, b"Bad request\n"))
        self.assertEqual(h["x-content-type-options"], ["nosniff"])

    def test_connection_cap_closes_the_extra_connection_and_frees_slots(self):
        with mock.patch.object(db, "MAX_CONNS", 3):
            srv = db.make_server(self.root, 0, self.html)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        port = srv.server_address[1]
        held = []

        def cleanup():
            for c in held:
                c.close()
            srv.shutdown()
            srv.server_close()
            thread.join(5)
        self.addCleanup(cleanup)
        for _ in range(3):
            c = socket.create_connection(("127.0.0.1", port), timeout=5)
            c.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Slow: " % port)   # holds a slot: no end of headers
            held.append(c)
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", port), timeout=5)
        extra.settimeout(2)
        start = time.time()
        try:
            extra.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % port)
            try:
                data = extra.recv(65536)
            except ConnectionResetError:
                data = b""
        finally:
            extra.close()
        self.assertEqual(data, b"")                      # closed with no reply
        self.assertLess(time.time() - start, 1.5)
        self.assertLessEqual(len(held), 3)
        for c in held:
            c.close()
        held[:] = []
        req = b"GET /dashboard.json HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n" % port
        status = None
        for _ in range(40):                              # the slots come back once the server sees the closes
            status, _, _ = self.raw(req, port=port)
            if status == 200:
                break
            time.sleep(0.1)
        self.assertEqual(status, 200)
        for _ in range(40):                              # the last request's slot comes back after its reply
            if srv.slots._value == 3:
                break
            time.sleep(0.1)
        self.assertEqual(srv.slots._value, 3)            # all released; a double release would have raised

    def test_a_slow_drip_is_cut_off_at_the_total_deadline(self):
        with mock.patch.object(db, "REQUEST_DEADLINE", 1.0):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Slow: " % self.port)
                start, got = time.time(), b""
                closed = False
                while time.time() - start < 6:
                    try:
                        s.sendall(b"a")                  # a byte every 0.3 s: each read is well inside the 5 s read timeout
                        s.settimeout(0.3)
                        chunk = s.recv(65536)
                        if not chunk:
                            closed = True
                            break
                        got += chunk
                    except socket.timeout:
                        continue
                    except OSError:
                        closed = True
                        break
                elapsed = time.time() - start
            finally:
                s.close()
        self.assertTrue(closed, "the server never closed the connection")
        self.assertLess(elapsed, 3.5)
        self.assertNotIn(b"200 OK", got)
        self.assertEqual(self.get(b"/dashboard.json")[0], 200)

    def test_json_never_includes_file_text_from_outside_the_tree(self):
        outside = os.path.join(self.tmp, "secret.txt")
        with open(outside, "w") as fh:
            fh.write("TOP-SECRET-VALUE")
        os.symlink(outside, os.path.join(self.root, "myapp", "add-login", "questions.md.bak"))
        os.rename(os.path.join(self.root, "myapp", "add-login", "questions.md"), os.path.join(self.root, "myapp", "add-login", "q.real"))
        os.symlink(outside, os.path.join(self.root, "myapp", "add-login", "questions.md"))
        status, _, body = self.get(b"/dashboard.json")
        self.assertEqual(status, 200)
        self.assertNotIn(b"TOP-SECRET", body)

    # -- startup ---------------------------------------------------------------------------------

    def test_port_in_use_is_exit_1(self):
        with self.assertRaises(OrchError) as cm:
            db.make_server(self.root, self.port, self.html)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("already in use", str(cm.exception))
        rc, out, err = run_cli(self.root, "dashboard", "--serve", "--port", str(self.port))
        self.assertEqual(rc, 1)
        self.assertIn("already in use", err)
        self.assertEqual(out, "")

    def test_a_wildcard_listener_on_the_port_counts_as_in_use(self):
        wild = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        wild.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        wild.bind(("", 0))
        wild.listen(1)
        self.addCleanup(wild.close)
        with self.assertRaises(OrchError) as cm:     # macOS lets a specific bind sit beside a wildcard one
            db.make_server(self.root, wild.getsockname()[1], self.html)
        self.assertEqual(cm.exception.code, 1)

    def test_bad_port_values(self):
        for bad in ("70000", "-1"):
            rc, _, err = run_cli(self.root, "dashboard", "--serve", "--port", bad)
            self.assertEqual(rc, 1, bad)
        rc, _, err = run_cli(self.root, "dashboard", "--serve", "--port", "abc")
        self.assertEqual(rc, 1)


def run_cli(root, *argv, **env):
    e = {"ORCH_DIR": root, "ORCH_NO_INDEX": "1", "ORCH_NOW": NOW_ENV}
    e.update(env)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, e, clear=False), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class TestCommand(Base):
    def test_json_output(self):
        rc, out, err = run_cli(self.root, "dashboard", "--json")
        self.assertEqual((rc, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(data["generated"], "2026-03-01T13:00:00Z")
        self.assertEqual(len(data["tasks"]), 5)
        self.assertEqual(data["page_version"], page_hash(db.HTML_PATH) if os.path.exists(db.HTML_PATH) else None)

    def test_json_is_read_only(self):
        before = snapshot(self.root)
        run_cli(self.root, "dashboard", "--json")
        self.assertEqual(snapshot(self.root), before)

    def test_one_of_json_or_serve(self):
        for argv in (["dashboard"], ["dashboard", "--json", "--serve"]):
            rc, out, err = run_cli(self.root, *argv)
            self.assertEqual((rc, out), (1, ""), argv)
            self.assertIn("exactly one of --json or --serve", err)

    def test_listed_among_the_commands(self):
        rc, out, _ = run_cli(self.root, "commands")
        self.assertIn("dashboard", out.split())

    def test_open_uses_the_default_browser(self):
        with mock.patch.object(db.Server, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch.object(db.webbrowser, "open", return_value=True) as wb:
            rc, out, err = run_cli(self.root, "dashboard", "--serve", "--port", "0", "--open")
        self.assertEqual(rc, 0)
        self.assertEqual(wb.call_count, 1)
        url = wb.call_args[0][0]
        self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/$")
        self.assertIn("serving " + url, out)
        self.assertNotIn("warning", err)
        with mock.patch.object(db.Server, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch.object(db.webbrowser, "open", return_value=False):
            rc, out, err = run_cli(self.root, "dashboard", "--serve", "--port", "0", "--open")
        self.assertEqual(rc, 0)
        self.assertIn("could not open the browser; open http://127.0.0.1:", err)

    def test_serve_without_open_never_runs_a_program(self):
        with mock.patch.object(db.Server, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch.object(db.webbrowser, "open") as wb:
            rc, out, _ = run_cli(self.root, "dashboard", "--serve", "--port", "0")
        self.assertEqual((rc, wb.call_count), (0, 0))


class TestRealPage(Base):
    """The page that ships, served by the real server."""

    def test_the_shipped_page_is_served_with_a_matching_script_hash(self):
        import base64
        import re
        srv = db.make_server(self.root, 0)          # default html: bin/orchlib/dashboard.html
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("GET", "/")
        res = conn.getresponse()
        body = res.read()
        with open(db.HTML_PATH, "rb") as fh:
            self.assertEqual(body, fh.read())
        scripts = re.findall(rb"<script>(.*?)</script>", body, re.S)
        self.assertEqual(len(scripts), 1)
        want = "'sha256-%s'" % base64.b64encode(hashlib.sha256(scripts[0]).digest()).decode()
        self.assertIn("script-src %s;" % want, res.getheader("Content-Security-Policy"))
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("GET", "/dashboard.json")
        data = json.loads(conn.getresponse().read())
        self.assertEqual(data["page_version"], page_hash(db.HTML_PATH))
        conn.close()

    def test_csp_allows_only_the_origins_the_page_links(self):
        link = b'<link rel="stylesheet" href="%s">'
        for html, style, font in (
                (b"<html></html>", "style-src 'unsafe-inline';", None),
                (link % b"https://a.example/x.css" + b"\n" + link % b"https://a.example:8443/y.css" + b"<link href='https://b.example'>",
                 "style-src 'unsafe-inline' https://a.example https://a.example:8443 https://b.example;",
                 "font-src https://a.example https://a.example:8443 https://b.example;"),
                (link % b"http://plain.example/x.css" + link % b"/local.css" + link % b"https://user@evil.example/x.css", "style-src 'unsafe-inline';", None)):
            csp = db.csp_for(html)
            self.assertIn(style, csp, html)
            if font is None:
                self.assertNotIn("font-src", csp)
            else:
                self.assertIn(font, csp)
            self.assertIn("default-src 'none'", csp)

    def test_the_shipped_pages_csp_names_no_origin_its_links_do_not(self):
        import re
        with open(db.HTML_PATH, "rb") as fh:
            html = fh.read()
        origins = set(re.findall(rb'<link[^>]*href="(https://[^/"]+)', html))
        csp = db.csp_for(html)
        named = set(re.findall(r"https://[^\s;]+", csp))
        self.assertEqual(named, set(o.decode() for o in origins))

    def test_font_lines_and_no_other_external_resource(self):
        with open(db.HTML_PATH, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        self.assertEqual(len([l for l in lines if "<link" in l]), 0)
        self.assertEqual(len([l for l in lines if l == '  --font: system-ui, -apple-system, "Segoe UI", sans-serif;']), 1)


class TestPageViaNode(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed: dashboard page tests (test_dashboard_page.mjs) skipped")
    def test_node_tests_pass(self):
        res = subprocess.run(["node", "--test", os.path.join(HERE, "test_dashboard_page.mjs")], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout[-3000:])
        self.assertIn("fail 0", res.stdout)


if __name__ == "__main__":
    unittest.main()
