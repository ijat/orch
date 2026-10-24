"""Shell side of the usage monitor: the sample-log writers (the statusline script and the repo's cache
script), usage-check.sh delegating to `orch usage` with its legacy fallback, and usage-watch.sh reading
the new log line. Neutral names only; every run uses a throwaway HOME, never the real one.

The writer scripts are found via ORCH_TEST_STATUSLINE (the statusline script) and ORCH_TEST_CACHE_SCRIPT
(default: orch-ratelimit-cache.sh next to bin/); a writer that cannot be found is skipped."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN)

from orchlib import usage  # noqa: E402

CHECK = os.path.join(BIN, 'usage-check.sh')
WATCH = os.path.join(BIN, 'usage-watch.sh')
ORCH = os.path.join(BIN, 'orch')
WRITERS = {
    'statusline': os.environ.get('ORCH_TEST_STATUSLINE'),
    'cache': os.environ.get('ORCH_TEST_CACHE_SCRIPT') or os.path.join(BIN, 'orch-ratelimit-cache.sh'),
}
HAVE_JQ = shutil.which('jq') is not None


def sh(cmd, env, inp=None, timeout=20):
    return subprocess.run(cmd, input=inp, env=env, capture_output=True, text=True, timeout=timeout)


def clean_env(home, **extra):
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': home, 'ORCH_USAGE_STATE': os.path.join(home, 'state')}
    env.update(extra)
    return env


def sample_json(sid='sid-a', p5=53.0, r5=0, p7=57, r7=0):
    return ('{"session_id":"%s","workspace":{"current_dir":"/nonexistent-dir"},'
            '"rate_limits":{"five_hour":{"used_percentage":%s,"resets_at":%d},'
            '"seven_day":{"used_percentage":%s,"resets_at":%d}}}' % (sid, p5, r5, p7, r7))


def make_writer_tests(label):
    @unittest.skipUnless(HAVE_JQ, 'jq not installed')
    class WriterTests(unittest.TestCase):
        def setUp(self):
            self.script = WRITERS[label]
            if not self.script or not os.path.isfile(self.script):
                self.skipTest('%s writer script not available' % label)
            self.home = tempfile.mkdtemp(prefix='usage-shell-')
            self.addCleanup(shutil.rmtree, self.home, True)
            os.makedirs(os.path.join(self.home, '.claude'))
            self.log = os.path.join(self.home, '.claude', '.statusline-usage-log')
            self.r5 = int(time.time()) + 3 * 3600
            self.r7 = int(time.time()) + 3 * 86400

        def run_writer(self, **kw):
            kw.setdefault('r5', self.r5)
            kw.setdefault('r7', self.r7)
            p = sh(['bash', self.script], clean_env(self.home), sample_json(**kw))
            self.assertEqual(p.returncode, 0, p.stderr)
            return p

        def lines(self):
            try:
                with open(self.log) as fh:
                    return fh.read().splitlines()
            except OSError:
                return []

        def test_line_format_and_reader(self):
            self.run_writer()
            ls = self.lines()
            self.assertEqual(len(ls), 1, ls)
            f = ls[0].split()
            self.assertEqual(len(f), 6)
            self.assertAlmostEqual(float(f[1]), 53.0)
            self.assertEqual((f[2], f[3], f[4], f[5]), (str(self.r5), '57', str(self.r7), 'sid-a'))
            s5, s7 = usage.load_log(os.path.join(self.home, '.claude'))
            self.assertEqual([(round(p), r, s) for _, p, r, s in s5], [(53, self.r5, 'sid-a')])
            self.assertEqual([(p, r) for _, p, r in s7], [(57.0, self.r7)])

        def test_same_json_twice_within_60s_is_one_line(self):
            self.run_writer()
            self.run_writer()
            self.assertEqual(len(self.lines()), 1)

        def test_two_sids_alternating_one_line_per_change(self):
            for sid in ('sid-a', 'sid-b', 'sid-a', 'sid-b'):
                self.run_writer(sid=sid)
            ls = self.lines()
            self.assertEqual([l.split()[5] for l in ls], ['sid-a', 'sid-b'])
            self.run_writer(sid='sid-a', p5=54.0)      # a changed field appends at once
            self.assertEqual([l.split()[5] for l in self.lines()], ['sid-a', 'sid-b', 'sid-a'])

        def test_heartbeat_after_60s(self):
            old = int(time.time()) - 61
            with open(self.log, 'w') as fh:
                fh.write('%d 53.0 %d 57 %d sid-a\n' % (old, self.r5, self.r7))
            self.run_writer()
            self.assertEqual(len(self.lines()), 2)

        def test_oversize_log_pruned_to_26h(self):
            now = int(time.time())
            with open(self.log, 'w') as fh:
                for i in range(6000):                      # ~ 30 h old, then fresh ones
                    fh.write('%d 40.0 %d 50 %d sid-old\n' % (now - 30 * 3600 + i, self.r5, self.r7))
                for i in range(100):
                    fh.write('%d 41.0 %d 50 %d sid-new\n' % (now - 3600 + i, self.r5, self.r7))
            self.assertGreater(os.path.getsize(self.log), 256 * 1024)
            self.run_writer()
            ls = self.lines()
            self.assertTrue(ls)
            self.assertGreaterEqual(min(int(l.split()[0]) for l in ls), now - 26 * 3600 - 5)
            self.assertEqual(sum(1 for l in ls if l.endswith('sid-old')), 0)
            self.assertEqual(ls[-1].split()[5], 'sid-a')
            self.assertLess(os.path.getsize(self.log), 256 * 1024)
            left = [n for n in os.listdir(os.path.join(self.home, '.claude')) if '.tmp.' in n]
            self.assertEqual(left, [])

        def test_unwritable_log_never_fails_and_legacy_still_written(self):
            os.mkdir(self.log)                              # a directory where the file should be
            self.run_writer()                               # asserts exit 0
            legacy = os.path.join(self.home, '.claude', '.statusline-ratelimit-5h')
            with open(legacy) as fh:
                self.assertTrue(fh.read().split()[-1].startswith('53'))

        def test_no_rate_limits_writes_nothing(self):
            p = sh(['bash', self.script], clean_env(self.home), '{"session_id":"sid-a","workspace":{"current_dir":"/nonexistent-dir"}}')
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(self.lines(), [])

    WriterTests.__name__ = 'WriterTests_' + label
    WriterTests.__qualname__ = WriterTests.__name__
    return WriterTests


WriterTests_statusline = make_writer_tests('statusline')
WriterTests_cache = make_writer_tests('cache')


class ShellBase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='usage-shell-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.claude = os.path.join(self.home, '.claude')
        self.bindir = os.path.join(self.claude, 'bin')
        os.makedirs(self.bindir)
        self.check = os.path.join(self.bindir, 'usage-check.sh')
        shutil.copy(CHECK, self.check)
        os.chmod(self.check, 0o755)
        self.now = int(time.time())
        self.r5 = self.now + 3 * 3600 + 1800
        self.r7 = self.now + 3 * 86400

    def link_orch(self):
        os.symlink(ORCH, os.path.join(self.bindir, 'orch'))

    def stub_orch(self, body):
        p = os.path.join(self.bindir, 'orch')
        with open(p, 'w') as fh:
            fh.write('#!/usr/bin/env bash\n' + body + '\n')
        os.chmod(p, 0o755)

    def write_log(self, p5):
        with open(os.path.join(self.claude, '.statusline-usage-log'), 'w') as fh:
            fh.write('%d %s %d 57 %d sid-a\n' % (self.now - 20, p5, self.r5, self.r7))

    def write_legacy(self, p5):
        for name, body in (('.statusline-ratelimit-5h', '%d %s\n' % (self.now - 20, p5)),
                           ('.statusline-ratelimit-7d', '%d 57\n' % (self.now - 20)),
                           ('.statusline-reset-5h', '%d\n' % self.r5)):
            with open(os.path.join(self.claude, name), 'w') as fh:
                fh.write(body)

    def env(self, **extra):
        return clean_env(self.home, **extra)

    @staticmethod
    def kv(line):
        return dict(t.split('=', 1) for t in line.split() if '=' in t)

    def run_check(self, **extra):
        p = sh([self.check], self.env(**extra))
        return p.stdout.strip(), p.returncode


class CheckTests(ShellBase):
    def test_delegates_same_prefix_and_code(self):
        self.link_orch()
        self.write_log(53.0)
        direct = sh([ORCH, 'usage'], self.env())
        out, code = self.run_check()
        self.assertEqual(code, direct.returncode)
        a, b = self.kv(out), self.kv(direct.stdout.strip())
        for k in ('state', '5h', '7d', 'reset_5h'):
            self.assertEqual(a[k], b[k], k)
        self.assertTrue(out.startswith('state=OK 5h=53 5h_age='), out)
        self.assertIn(' est=', out)

    def test_caller_thresholds_become_fixed(self):
        self.link_orch()
        self.write_log(53.0)
        out, code = self.run_check(STOP_PCT='40')
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith('state=STOP'), out)
        self.assertIn(' line=40.0 ', out)
        out, code = self.run_check(WARN_PCT='50', STOP_PCT='99')
        self.assertEqual((code, self.kv(out)['state']), (1, 'WARN'), out)

    def test_orch_missing_falls_back_to_legacy(self):
        self.write_legacy(53)
        out, code = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertTrue(re.match(r'state=OK 5h=53 5h_age=\d+ 7d=57 7d_age=\d+ reset_5h=%d reset_in=3h\d+m$' % self.r5, out), out)
        self.write_legacy(96)
        out, code = self.run_check()
        self.assertEqual((code, self.kv(out)['state']), (2, 'STOP'), out)       # legacy fixed 95

    def test_orch_crashing_falls_back_to_legacy(self):
        self.write_legacy(53)
        for body in ('echo "Traceback (most recent call last)" >&2; exit 3',
                     'echo "something else"; exit 7',
                     'exit 0'):
            self.stub_orch(body)
            out, code = self.run_check()
            self.assertEqual(code, 0, (body, out))
            self.assertTrue(out.startswith('state=OK 5h=53 5h_age='), (body, out))
            self.assertNotIn(' est=', out)

    def test_orch_unknown_is_passed_through(self):
        self.link_orch()                     # no samples anywhere: the estimator itself says UNKNOWN
        out, code = self.run_check()
        self.assertEqual(code, 4, out)
        self.assertTrue(out.startswith('state=UNKNOWN'), out)

    def test_stale_old_sample_matches_orch(self):
        self.link_orch()
        with open(os.path.join(self.claude, '.statusline-usage-log'), 'w') as fh:
            fh.write('%d 53.0 %d 57 %d sid-a\n' % (self.now - 7200, self.r5, self.r7))
        direct = sh([ORCH, 'usage'], self.env())
        out, code = self.run_check()
        self.assertEqual(code, direct.returncode)
        self.assertEqual(self.kv(out)['state'], self.kv(direct.stdout.strip())['state'])


class WatchTests(ShellBase):
    def test_one_loop_parses_new_log_line(self):
        self.link_orch()
        self.write_log(97.0)
        shutil.copy(WATCH, os.path.join(self.bindir, 'usage-watch.sh'))
        p = sh(['bash', os.path.join(self.bindir, 'usage-watch.sh'), '--interval', '0', '--exit-on-stop'], self.env(), timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(p.stdout.startswith('STOP 5h=97%'), p.stdout)


class SyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for f in (CHECK, WATCH):
            p = subprocess.run(['bash', '-n', f], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == '__main__':
    unittest.main()
