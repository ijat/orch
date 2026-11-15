"""orch memory: audit, clear with a verified backup, restore, the auto-memory switch and its status.

Every test runs with ORCH_CLAUDE_HOME, ORCH_DATA, ORCH_DIR and --project in temp dirs and HOME pointed at a third temp
dir that must stay empty. The transcript lines are real Claude Code 2.1.325 lines, scrubbed the way step 1's fixtures
are (strings made up, ids zeroed); {CLAUDE_HOME}, {PROJECT}, {FOLDER} and {TS} are filled in per test.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

from orchlib import cli, memory  # noqa: E402

FIRST_LINE = '{"type": "mode", "mode": "made-up mode", "sessionId": "00000000-0000-4000-8000-000000000058"}'
INSTRUCTIONS_LINE = (
    '{"parentUuid": "00000000-0000-4000-8000-000000000086", "isSidechain": false, "attachment": {"type": "instructions", '
    '"files": [{"path": "{CLAUDE_HOME}/CLAUDE.md", "type": "User", "content": "made-up content"}, '
    '{"path": "{PROJECT}/CLAUDE.local.md", "type": "Local", "content": "made-up content"}, '
    '{"path": "{CLAUDE_HOME}/projects/{FOLDER}/memory/MEMORY.md", "type": "AutoMem", "content": "made-up content"}]}, '
    '"type": "attachment", "uuid": "00000000-0000-4000-8000-000000000061", "timestamp": "{TS}", '
    '"rendered": [{"content": "made-up content"}], "renderedRole": "user", '
    '"session_id": "00000000-0000-4000-8000-000000000050", "userType": "external", "entrypoint": "cli", '
    '"cwd": "{PROJECT}", "sessionId": "00000000-0000-4000-8000-000000000058", "version": "0.0.0", '
    '"gitBranch": "made-up gitBranch"}')
SECRET = "sk-fake-0123456789abcdef"

FEEDBACK = """---
name: tier-advice
description: "Use opus/high for the planner and escalate after a failure"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 11111111-1111-4111-8111-111111111111
  modified: 2026-11-13T10:25:29.595Z
---

See `plugin/bin/orch` and `no/such/file.py`; run `orch nosuchverb`.
"""
COMPLETE = """---
name: complete
description: plain note
type: user
quote: "keep it short"
scope: project
review-by: 2027-01-01
exempt: no
---

Nothing about models here.
"""


def tree(top):
    out = {}
    for d, _, files in os.walk(top):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, top)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class MemoryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="orch-memory-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.ch, self.proj, self.data, self.odir = (
            os.path.join(self.tmp, n) for n in ("home", "claude", "proj", "data", "tasks"))
        for d in (self.home, self.ch, self.proj, self.odir):
            os.makedirs(d)
        self.folder_name = "-tmp-proj"
        self.folder = os.path.join(self.ch, "projects", self.folder_name)
        self.mem = os.path.join(self.folder, "memory")
        os.makedirs(self.mem)
        self.env = {"HOME": self.home, "ORCH_CLAUDE_HOME": self.ch, "ORCH_DATA": self.data, "ORCH_DIR": self.odir,
                    "ORCH_NOW": "2026-11-15T03:04Z", "ORCH_NO_INDEX": "1", "CLAUDE_CODE_SESSION_ID": ""}
        self.addCleanup(self.assert_home_empty)
        self.transcript("11111111-1111-4111-8111-111111111111", "2026-11-14T10:00:00.000Z", memory_loaded=True)
        self.write(os.path.join(self.mem, "MEMORY.md"), "# Memory index\n\n- [tier advice](tier-advice.md)\n")
        self.write(os.path.join(self.mem, "tier-advice.md"), FEEDBACK)
        self.write(os.path.join(self.mem, "complete.md"), COMPLETE)
        os.makedirs(os.path.join(self.mem, "sub"))
        self.write(os.path.join(self.mem, "sub", "nested.md"), "no frontmatter\n")
        os.makedirs(os.path.join(self.proj, "plugin", "bin"))
        self.write(os.path.join(self.proj, "plugin", "bin", "orch"), "")

    def load(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def assert_home_empty(self):
        self.assertEqual(os.listdir(self.home), [], "HOME was written")

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def transcript(self, sid, ts, memory_loaded, folder=None, cwd=None):
        line = INSTRUCTIONS_LINE.replace("{CLAUDE_HOME}", self.ch).replace("{PROJECT}", cwd or self.proj) \
            .replace("{FOLDER}", os.path.basename(folder or self.folder)).replace("{TS}", ts)
        o = json.loads(line)
        if not memory_loaded:
            o["attachment"]["files"] = [f for f in o["attachment"]["files"] if f["type"] != "AutoMem"]
        path = os.path.join(folder or self.folder, sid + ".jsonl")
        self.write(path, FIRST_LINE + "\n" + json.dumps(o) + "\n")
        return path

    def run_orch(self, *argv, **env):
        e = dict(self.env)
        e.update(env)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, e, clear=False), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            if memory.ENV not in env:
                os.environ.pop(memory.ENV, None)
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def ok(self, *argv, **env):
        rc, out, err = self.run_orch(*argv, **env)
        self.assertEqual(rc, 0, "argv=%r\nstdout=%s\nstderr=%s" % (argv, out, err))
        return out, err

    def refuse(self, code, *argv, **env):
        rc, out, err = self.run_orch(*argv, **env)
        self.assertEqual(rc, code, "argv=%r\nstdout=%s\nstderr=%s" % (argv, out, err))
        return out, err


class TestRoots(MemoryBase):
    def test_module_never_expands_home_itself(self):
        with open(memory.__file__, encoding="utf-8") as fh:
            self.assertNotIn("expanduser", fh.read())

    def test_claude_home_default_and_override(self):
        from orchlib import paths
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            os.environ.pop("ORCH_CLAUDE_HOME", None)
            self.assertEqual(paths.claude_home(), os.path.join(self.home, ".claude"))
        with mock.patch.dict(os.environ, {"ORCH_CLAUDE_HOME": self.ch}):
            self.assertEqual(paths.claude_home(), self.ch)


class TestAudit(MemoryBase):
    def test_no_matching_transcript_means_no_memory(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        out, _ = self.ok("memory", "audit", "--project", other)
        self.assertIn("no Claude memory for this project", out)

    def test_lists_every_file_flags_and_changes_nothing(self):
        before, mtimes = tree(self.ch), {p: os.path.getmtime(os.path.join(self.mem, p)) for p in tree(self.mem)}
        rep = json.loads(self.ok("memory", "audit", "--project", self.proj, "--json")[0])
        self.assertEqual(tree(self.ch), before)
        self.assertEqual({p: os.path.getmtime(os.path.join(self.mem, p)) for p in tree(self.mem)}, mtimes)
        rows = {r["file"]: r for r in rep["files"]}
        self.assertEqual(sorted(rows), ["MEMORY.md", "complete.md", os.path.join("sub", "nested.md"), "tier-advice.md"])
        fb = rows["tier-advice.md"]
        self.assertEqual((fb["name"], fb["type"], fb["modified"]), ("tier-advice", "feedback", "2026-11-13"))
        self.assertEqual(fb["description"], "Use opus/high for the planner and escalate after a failure")
        self.assertEqual(fb["origin_transcript"], "exists")
        self.assertEqual(fb["missing"], ["quote", "scope", "review-by", "exempt"])
        kinds = {f["kind"]: f["detail"] for f in fb["flags"] if f["kind"] == "orch-policy"}
        for term in ("opus/high", "planner", "escalate"):
            self.assertIn(term, kinds["orch-policy"])
        stale = [f["detail"] for f in fb["flags"] if f["kind"] == "stale-name"]
        self.assertTrue(any("no/such/file.py" in s for s in stale), stale)
        self.assertTrue(any("nosuchverb" in s for s in stale), stale)
        self.assertFalse(any("plugin/bin/orch`" in s for s in stale), stale)
        self.assertEqual(rows["complete.md"]["missing"], [])
        self.assertEqual(rows["complete.md"]["flags"], [])
        self.assertEqual(rows["MEMORY.md"]["type"], "index")
        self.assertEqual(rows["MEMORY.md"]["missing"], [])
        self.assertEqual(rows[os.path.join("sub", "nested.md")]["missing"], list(memory.REQUIRED))
        text, _ = self.ok("memory", "audit", "--project", self.proj)
        self.assertIn("4 files", text.splitlines()[0])
        self.assertEqual(tree(self.ch), before)

    def test_origin_transcript_gone(self):
        os.remove(os.path.join(self.folder, "11111111-1111-4111-8111-111111111111.jsonl"))
        self.transcript("22222222-2222-4222-8222-222222222222", "2026-11-14T10:00:00.000Z", True)
        rep = json.loads(self.ok("memory", "audit", "--project", self.proj, "--json")[0])
        self.assertEqual({r["file"]: r["origin_transcript"] for r in rep["files"]}["tier-advice.md"], "gone")

    def test_all_counts_projects_first(self):
        f2 = os.path.join(self.ch, "projects", "-tmp-two")
        p2 = os.path.join(self.tmp, "two")
        os.makedirs(p2)
        self.transcript("33333333-3333-4333-8333-333333333333", "2026-11-14T10:00:00.000Z", True, folder=f2, cwd=p2)
        self.write(os.path.join(f2, "memory", "MEMORY.md"), "# index\n")
        os.makedirs(os.path.join(self.ch, "projects", "-tmp-empty", "memory"))
        out, _ = self.ok("memory", "audit", "--all")
        self.assertEqual(out.splitlines()[0], "2 projects with Claude memory")
        self.assertIn(p2, out)
        self.assertIn(self.proj, out)


class TestClearRestore(MemoryBase):
    def backups(self):
        return os.path.join(self.data, "backups", "memory")

    def test_clear_needs_a_reason(self):
        before = tree(self.mem)
        self.refuse(1, "memory", "clear", "--project", self.proj)
        self.refuse(1, "memory", "clear", "--project", self.proj, "--reason", "  ")
        self.assertEqual(tree(self.mem), before)
        self.assertFalse(os.path.exists(self.backups()))

    def test_clear_backs_up_verifies_then_empties(self):
        before = tree(self.mem)
        out, _ = self.ok("memory", "clear", "--project", self.proj, "--reason", "moving it into lessons")
        name = "20261115T0304Z-%s" % self.folder_name
        bdir = os.path.join(self.backups(), name)
        self.assertIn("orch memory restore %s" % name, out)
        self.assertEqual(os.listdir(self.mem), [])
        self.assertTrue(os.path.isdir(self.mem))
        copy = tree(bdir)
        manifest = self.load(os.path.join(bdir, "MANIFEST.json"))
        del copy["MANIFEST.json"]
        self.assertEqual(copy, before)
        self.assertEqual(manifest["files"], before)
        self.assertEqual(manifest["project"], self.proj)
        self.assertEqual(manifest["folder"], self.mem)
        self.assertEqual(manifest["reason"], "moving it into lessons")
        self.assertEqual(manifest["date"], "2026-11-15T03:04Z")

    def test_clear_refuses_empty_or_missing_folder(self):
        shutil.rmtree(self.mem)
        os.makedirs(self.mem)
        self.refuse(2, "memory", "clear", "--project", self.proj, "--reason", "x")
        os.rmdir(self.mem)
        self.refuse(2, "memory", "clear", "--project", self.proj, "--reason", "x")
        self.assertFalse(os.path.exists(self.backups()))

    def test_corrupted_copy_removes_nothing(self):
        before = tree(self.mem)

        def bad_copy(src, dst):
            shutil.copy2(src, dst)
            if src.endswith("complete.md"):
                with open(dst, "ab") as fh:
                    fh.write(b"corrupt")

        with mock.patch.object(memory, "copy_file", bad_copy):
            _, err = self.refuse(2, "memory", "clear", "--project", self.proj, "--reason", "x")
        self.assertIn("nothing removed", err)
        self.assertEqual(tree(self.mem), before)

    def test_restore_into_empty_then_refuse_then_merge(self):
        before = tree(self.mem)
        self.ok("memory", "clear", "--project", self.proj, "--reason", "x")
        name = os.listdir(self.backups())[0]
        self.ok("memory", "restore", name)
        self.assertEqual(tree(self.mem), before)
        self.refuse(2, "memory", "restore", name)
        self.write(os.path.join(self.mem, "complete.md"), "edited since\n")
        os.remove(os.path.join(self.mem, "MEMORY.md"))
        out, _ = self.ok("memory", "restore", name, "--merge")
        self.assertIn("restored 1 file", out)
        with open(os.path.join(self.mem, "complete.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "edited since\n")          # --merge never overwrites
        self.assertEqual(tree(self.mem)["MEMORY.md"], before["MEMORY.md"])

    def test_restore_refuses_bad_names_and_a_tampered_backup(self):
        self.refuse(1, "memory", "restore", "../x")
        self.refuse(1, "memory", "restore", "nope")
        self.ok("memory", "clear", "--project", self.proj, "--reason", "x")
        name = os.listdir(self.backups())[0]
        with open(os.path.join(self.backups(), name, "complete.md"), "a") as fh:
            fh.write("tampered")
        self.refuse(2, "memory", "restore", name)
        self.assertEqual(os.listdir(self.mem), [])


class TestSwitch(MemoryBase):
    def settings(self, path, data, mode=0o600):
        self.write(path, json.dumps(data, indent=4) + "\n")
        os.chmod(path, mode)

    def test_auto_off_keeps_other_keys_order_mode_and_prints_no_secret(self):
        path = os.path.join(self.proj, ".claude", "settings.local.json")
        self.settings(path, {"env": {"API_TOKEN": SECRET}, "model": "opus", "permissions": {"allow": ["Bash(ls)"]}})
        out, err = self.ok("memory", "auto-off", "--project", self.proj)
        self.assertNotIn(SECRET, out + err)
        self.assertEqual(out.strip(), "%s: autoMemoryEnabled = false" % path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        data = json.loads(text)
        self.assertEqual(list(data), ["env", "model", "permissions", "autoMemoryEnabled"])
        self.assertEqual(data["env"]["API_TOKEN"], SECRET)
        self.assertIs(data["autoMemoryEnabled"], False)
        self.assertIn('\n    "env"', text)                       # the file's own indent is kept
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        out, err = self.ok("memory", "auto-on", "--project", self.proj)
        self.assertNotIn(SECRET, out + err)
        data = self.load(path)
        self.assertEqual(list(data), ["env", "model", "permissions"])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_auto_off_creates_the_local_file_and_global_writes_claude_home(self):
        self.ok("memory", "auto-off", "--project", self.proj)
        with open(os.path.join(self.proj, ".claude", "settings.local.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"autoMemoryEnabled": False})
        gpath = os.path.join(self.ch, "settings.json")
        self.settings(gpath, {"apiKeyHelper": SECRET, "autoMemoryEnabled": True, "theme": "dark"})
        out, err = self.ok("memory", "auto-off", "--global")
        self.assertNotIn(SECRET, out + err)
        with open(gpath, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(list(data), ["apiKeyHelper", "autoMemoryEnabled", "theme"])   # an existing key keeps its place
        self.assertIs(data["autoMemoryEnabled"], False)
        self.refuse(1, "memory", "auto-off")
        self.refuse(1, "memory", "auto-off", "--global", "--project", self.proj)

    def test_malformed_file_is_refused_untouched_and_not_printed(self):
        path = os.path.join(self.proj, ".claude", "settings.local.json")
        bad = '{"env": {"API_TOKEN": "%s"},, }\n' % SECRET
        self.write(path, bad)
        for verb in ("auto-off", "auto-on"):
            out, err = self.refuse(2, "memory", verb, "--project", self.proj)
            self.assertNotIn(SECRET, out + err)
            self.assertIn("not valid JSON", err)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), bad)
        out, err = self.ok("memory", "status", "--project", self.proj)
        self.assertNotIn(SECRET, out + err)


class TestStatus(MemoryBase):
    def status(self, **env):
        out, err = self.ok("memory", "status", "--project", self.proj, "--json", **env)
        self.assertNotIn(SECRET, out + err)
        return json.loads(out)

    def set_off(self, when):
        path = os.path.join(self.proj, ".claude", "settings.local.json")
        self.write(path, json.dumps({"env": {"API_TOKEN": SECRET}, "autoMemoryEnabled": False}))
        os.utime(path, (when, when))

    def test_values_and_still_loading(self):
        self.set_off(memory.parse_ts("2026-11-14T09:00:00Z"))
        self.write(os.path.join(self.ch, "settings.json"), json.dumps({"apiKeyHelper": SECRET}))
        os.utime(os.path.join(self.ch, "settings.json"), (1, 1))
        rep = self.status(**{memory.ENV: "1"})
        self.assertEqual([s["value"] for s in rep["settings"]], ["false", "no file", "unset"])
        self.assertEqual(rep["env"]["value"], "1")
        self.assertEqual(rep["live"]["state"], "still loading")

    def test_verified_off(self):
        self.set_off(memory.parse_ts("2026-11-14T11:00:00Z"))
        self.transcript("44444444-4444-4444-8444-444444444444", "2026-11-14T12:00:00.000Z", memory_loaded=False)
        rep = self.status()
        self.assertEqual(rep["live"]["state"], "verified off")
        self.assertEqual(rep["live"]["transcript"], "44444444-4444-4444-8444-444444444444.jsonl")
        self.assertEqual(rep["env"]["value"], "unset")

    def test_never_switched_off_is_not_verified_off(self):
        """No source says off: a session without MEMORY.md in it proves nothing about a switch."""
        self.transcript("44444444-4444-4444-8444-444444444444", "2026-11-14T12:00:00.000Z", memory_loaded=False)
        rep = self.status()
        self.assertEqual(rep["live"]["state"], "not switched off")
        self.assertEqual([s["value"] for s in rep["settings"]], ["no file", "no file", "no file"])
        for env in ("0", "false", ""):
            self.assertEqual(self.status(**{memory.ENV: env})["live"]["state"], "not switched off", env)
        self.write(os.path.join(self.proj, ".claude", "settings.local.json"), json.dumps({"autoMemoryEnabled": True}))
        self.assertEqual(self.status()["live"]["state"], "not switched off")
        out, _ = self.ok("memory", "status", "--project", self.proj)
        self.assertIn("live check: not switched off", out)

    def test_the_environment_variable_alone_counts_as_off(self):
        self.transcript("44444444-4444-4444-8444-444444444444", "2026-11-14T12:00:00.000Z", memory_loaded=False)
        self.assertEqual(self.status(**{memory.ENV: "1"})["live"]["state"], "verified off")

    def test_no_memory_md_cannot_verify_anything(self):
        """Right after `clear` no session can load MEMORY.md, whether or not the switch worked."""
        self.ok("memory", "clear", "--project", self.proj, "--reason", "test")
        self.assertFalse(os.path.exists(os.path.join(self.mem, "MEMORY.md")))
        self.transcript("44444444-4444-4444-8444-444444444444", "2026-11-16T12:00:00.000Z", memory_loaded=False)
        self.ok("memory", "auto-off", "--project", self.proj)
        rep = self.status()
        self.assertEqual(rep["live"]["state"], "not verifiable (no MEMORY.md)")
        self.assertEqual(rep["settings"][0]["value"], "false")
        out, _ = self.ok("memory", "status", "--project", self.proj)
        self.assertIn("live check: not verifiable (no MEMORY.md)", out)

    def test_a_moved_memory_folder_is_reported_not_called_off(self):
        self.set_off(memory.parse_ts("2026-11-14T11:00:00Z"))
        self.transcript("44444444-4444-4444-8444-444444444444", "2026-11-14T12:00:00.000Z", memory_loaded=False)
        self.write(os.path.join(self.ch, "settings.json"), json.dumps({"autoMemoryDirectory": "/elsewhere/" + SECRET}))
        rep = self.status()
        self.assertEqual(rep["live"]["state"], "not verifiable (autoMemoryDirectory)")
        self.assertNotIn("/elsewhere", json.dumps(rep))        # the key is named, its value never read out
        self.assertIn("autoMemoryDirectory", rep["live"]["why"])

    def test_not_yet_verified(self):
        self.set_off(memory.parse_ts("2026-11-14T13:00:00Z"))
        self.assertEqual(self.status()["live"]["state"], "not yet verified")
        out, _ = self.ok("memory", "status", "--project", self.proj)
        self.assertIn("live check: not yet verified", out)


if __name__ == "__main__":
    unittest.main()
