"""Windows code paths, driven on any OS by patching os.name (no Windows-only module is imported)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from orchlib import dashboard, paths, usage  # noqa: E402


class WindowsPids(unittest.TestCase):
    def test_pid_alive_never_calls_os_kill(self):
        # os.kill(pid, 0) terminates the process on Windows.
        with mock.patch.object(usage.os, "name", "nt"), mock.patch.object(usage.os, "kill") as kill, \
                mock.patch.object(usage, "_pid_alive_nt", return_value=True) as probe:
            self.assertTrue(usage.pid_alive(1234))
            self.assertFalse(usage.pid_alive(0))
            self.assertFalse(usage.pid_alive("x"))
        self.assertEqual(kill.call_count, 0)
        self.assertEqual(probe.call_count, 1)

    def test_probe_error_reads_as_dead(self):
        with mock.patch.object(usage.os, "name", "nt"), \
                mock.patch.object(usage, "_pid_alive_nt", side_effect=AttributeError):
            self.assertFalse(usage.pid_alive(1234))

    def test_ps_is_not_run(self):
        with mock.patch.object(usage.os, "name", "nt"), mock.patch.object(usage, "pid_alive", return_value=True), \
                mock.patch.object(usage.subprocess, "run") as run:
            self.assertIsNone(usage.proc_starts([1234]))
            self.assertIs(usage.proc_start(1234), usage.PS_UNKNOWN)
            self.assertTrue(usage.is_wait_proc(1234))
        self.assertEqual(run.call_count, 0)


class WindowsRead(unittest.TestCase):
    def test_read_file_without_o_nofollow_still_refuses_symlinks(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        real, link = os.path.join(d, "a.md"), os.path.join(d, "b.md")
        with open(real, "wb") as fh:
            fh.write(b"x\r\ny")
        os.symlink(real, link)
        import types
        fake = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__") and k != "O_NOFOLLOW"})
        with mock.patch.object(dashboard, "os", fake):
            self.assertEqual(dashboard.read_file(real), b"x\r\ny")
            self.assertIsNone(dashboard.read_file(link))


class WindowsIndex(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"ORCH_NO_INDEX": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_index_script_runs_through_bash(self):
        warns = []
        with mock.patch.object(paths.os, "name", "nt"), mock.patch.object(paths.shutil, "which", return_value="B"), \
                mock.patch.object(paths.subprocess, "run") as run:
            run.return_value.returncode = 0
            paths.refresh_index(warns.append)
        self.assertEqual(run.call_args[0][0], ["B", os.path.join(paths.BIN_DIR, "orch-index.sh")])
        self.assertEqual(warns, [])

    def test_no_bash_is_a_warning(self):
        warns = []
        with mock.patch.object(paths.os, "name", "nt"), mock.patch.object(paths.shutil, "which", return_value=None), \
                mock.patch.object(paths.subprocess, "run") as run:
            paths.refresh_index(warns.append)
        self.assertEqual(run.call_count, 0)
        self.assertEqual(warns, ["index not refreshed: no bash on PATH"])


if __name__ == "__main__":
    unittest.main()
