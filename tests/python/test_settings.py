"""orch setting get|set: defaults, round trip, ORCH_DIR / ORCH_DATA scoping, malformed file, refusals."""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin"))

import test_core as core  # noqa: E402
from orchlib import settings  # noqa: E402


class TestSetting(core.Base):
    def file(self):
        return os.path.join(self.root, ".orch-settings.json")

    def test_default_is_off_and_nothing_is_written(self):
        out, err = self.ok("setting", "get", "mythos_review")
        self.assertEqual((out, err), ("off\n", ""))
        self.assertFalse(os.path.exists(self.file()))

    def test_set_get_round_trip_and_the_file_keeps_other_keys(self):
        self.ok("setting", "set", "mythos_review", "on")
        self.assertEqual(self.ok("setting", "get", "mythos_review")[0], "on\n")
        with open(self.file()) as fh:
            self.assertEqual(json.load(fh), {"mythos_review": "on"})
        with open(self.file(), "w") as fh:
            json.dump({"mythos_review": "on", "other": [1]}, fh)
        self.ok("setting", "set", "mythos_review", "off")
        with open(self.file()) as fh:
            self.assertEqual(json.load(fh), {"mythos_review": "off", "other": [1]})
        self.assertEqual(self.ok("setting", "get", "mythos_review")[0], "off\n")

    def test_orch_dir_scopes_the_file_and_the_real_one_is_never_touched(self):
        real = os.path.join(self.home, ".claude", "orch-data", "settings.json")
        self.ok("setting", "set", "mythos_review", "on")
        self.assertTrue(os.path.isfile(self.file()))
        self.assertFalse(os.path.exists(real))
        other = os.path.join(self.tmp, "other-tasks")
        self.assertEqual(self.ok("setting", "get", "mythos_review", env={"ORCH_DIR": other})[0], "off\n")

    def test_orch_data_moves_the_file_to_the_data_root(self):
        data = os.path.join(self.tmp, "data")
        env = {"ORCH_DATA": data}
        self.ok("setting", "set", "mythos_review", "on", env=env)
        self.assertTrue(os.path.isfile(os.path.join(data, "settings.json")))
        self.assertEqual(self.ok("setting", "get", "mythos_review", env=env)[0], "on\n")
        self.assertFalse(os.path.exists(self.file()))

    def test_no_orch_dir_reads_the_real_data_root(self):
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            for k in ("ORCH_DIR", "ORCH_DATA"):
                os.environ.pop(k, None)
            self.assertEqual(settings.settings_path(), os.path.join(self.home, ".claude", "orch-data", "settings.json"))

    def test_unknown_name_and_bad_values_exit_1(self):
        self.assertIn("unknown setting", self.refuse(1, "setting", "get", "nope"))
        self.assertIn("unknown setting", self.refuse(1, "setting", "set", "nope", "on"))
        self.assertIn("must be one of", self.refuse(1, "setting", "set", "mythos_review", "maybe"))
        self.assertIn("must be one of", self.refuse(1, "setting", "set", "mythos_review"))
        self.refuse(1, "setting", "get", "mythos_review", "on")
        self.assertFalse(os.path.exists(self.file()))

    def test_a_malformed_file_is_the_default_with_one_warning(self):
        for body in ("not json", "[1, 2]", '{"mythos_review": 3}', '{"mythos_review": "yes"}', ""):
            with open(self.file(), "w") as fh:
                fh.write(body)
            rc, out, err = self.run_orch("setting", "get", "mythos_review")
            self.assertEqual((rc, out), (0, "off\n"), body)
            self.assertLessEqual(len(err.strip().splitlines()), 1, body)
        with open(self.file(), "w") as fh:
            fh.write("not json")
        _, _, err = self.run_orch("setting", "get", "mythos_review")
        self.assertIn("using the defaults", err)
        # a set over a malformed file repairs it
        self.ok("setting", "set", "mythos_review", "on")
        self.assertEqual(self.ok("setting", "get", "mythos_review")[0], "on\n")

    def test_a_development_copy_without_orch_dir_refuses_to_set(self):
        env = {"ORCH_DIR": "", "ORCH_DATA": ""}
        self.assertIn("development copy", self.refuse(1, "setting", "set", "mythos_review", "on", env=env))


if __name__ == "__main__":
    unittest.main()
