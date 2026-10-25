"""orch usage wait: exit table, paused mode, check-ins, REFRESH, pidfile, incremental scan, CLI wiring.
Injected clock/sleep/estimates; real sleeping only in the CLI-level tests (well under 2 s). Neutral names only."""
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN)

from orchlib import cli, flow, usage  # noqa: E402

ORCH = os.path.join(BIN, 'orch')
BASE = flow.ts_epoch('2027-01-15T10:00Z')   # minute-aligned fixed clock
R5 = BASE + 3 * 3600
SID = 'sess-aaaa'
KEY = 'proj-a__task-x'


def ts(t):
    return time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(t))


def est(state='OK', p5=50.0, e=None, line=95.0, window=R5, rolled=False, k_src='window', age5=30.0, ext=0.0,
        own_last=None, t_sample=None):
    return usage.Estimate(state=state, p5=p5, age5=age5, p7=None, age7=None, reset5=None if rolled or window is None else int(window),
                          reset_in='1h0m', est=p5 + ext if e is None else e, line=line, eta_line=None, burn=0.0,
                          k=3.0, k_src=k_src, sessions=1, agents=0, burn_others=0.0, proj7=None, ext=ext,
                          window=window, rolled=rolled, own_last=own_last, t_sample=t_sample, persist=None)


def ps_failures():
    """Each way `ps` can be unusable: missing from PATH, timing out, failing to fork, listing nothing."""
    empty = subprocess.CompletedProcess(['ps'], 1, stdout='')
    return (('missing', mock.patch.dict(os.environ, {'PATH': '/nonexistent'})),
            ('timeout', mock.patch.object(usage.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ps', 5))),
            ('oserror', mock.patch.object(usage.subprocess, 'run', side_effect=OSError(11, 'fork failed'))),
            ('no-rows', mock.patch.object(usage.subprocess, 'run', return_value=empty)))


class Clock(object):
    def __init__(self, t):
        self.t = float(t)
        self.hooks = []   # [(at, fn)] run once when the clock passes `at`

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s
        for h in list(self.hooks):
            if self.t >= h[0]:
                self.hooks.remove(h)
                h[1]()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='usage-wait-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = os.path.join(self.tmp, 'state')
        self.clock = Clock(BASE)

    def wait(self, est_fn, **kw):
        kw.setdefault('interval', 30)
        kw.setdefault('max_sec', 6 * 3600)
        kw.setdefault('is_wait', lambda pid, launch='-': True)
        return usage.Wait(self.state, kw.pop('key', KEY), est_fn, clock=self.clock, sleep=self.clock.sleep, **kw)

    def run_wait(self, est_fn, **kw):
        w = self.wait(est_fn, **kw)
        code, line = w.run()
        self.assertEqual(len(line.splitlines()), 1)
        return code, line

    def pidfile(self, key=KEY):
        return usage.pidfile_path(self.state, key)

    # a task dir: status.md with an implementer row, journal, log
    def task(self, state='implementing', rows=(('orchestrator', 'in_progress'), ('implementer', 'in_progress')),
             journal=(), log=(), row_ts=None):
        d = os.path.join(self.tmp, 'proj-a', 'task-x')
        os.makedirs(d, exist_ok=True)
        lines = ['# Status: task-x', '', '- task_state: %s' % state, '- mode: standard', '',
                 '| role | state | timestamp (UTC) | last completed step | notes |', '|---|---|---|---|---|']
        for role, rs in rows:
            lines.append('| %s | %s | %s | | |' % (role, rs, row_ts or ts(BASE)))
        with open(os.path.join(d, 'status.md'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        with open(os.path.join(d, 'journal.jsonl'), 'w') as fh:
            for rec in journal:
                fh.write(json.dumps(rec) + '\n')
        with open(os.path.join(d, 'log.md'), 'w') as fh:
            fh.write('# Log: task-x\n\n')
            for line in log:
                fh.write(line + '\n')
        return d

    def set_state(self, d, state):
        p = os.path.join(d, 'status.md')
        with open(p) as fh:
            text = fh.read()
        import re
        with open(p, 'w') as fh:
            fh.write(re.sub(r'- task_state: \S+', '- task_state: ' + state, text))


class TestExitTable(Base):
    def test_stop_at_launch_is_immediate(self):
        code, line = self.run_wait(lambda: est('STOP', 97, line=96))
        self.assertEqual(code, 2)
        self.assertTrue(line.startswith('STOP state=STOP 5h=97 '))
        self.assertEqual(self.clock.t, BASE)
        self.assertFalse(os.path.exists(self.pidfile()))

    def test_unknown_at_launch_is_4(self):
        code, line = self.run_wait(lambda: usage.unknown('no-sample'))
        self.assertEqual(code, 4)
        self.assertTrue(line.startswith('UNKNOWN state=UNKNOWN '))
        self.assertIn('reason=no-sample', line)
        self.assertFalse(os.path.exists(self.pidfile()))

    def test_stop_mid_wait(self):
        code, line = self.run_wait(lambda: est('STOP' if self.clock.t >= BASE + 300 else 'OK', 90))
        self.assertEqual((code, self.clock.t), (2, BASE + 300))

    def test_max_ends_13_and_stays_silent_before(self):
        code, line = self.run_wait(lambda: est(), max_sec=600)
        self.assertEqual(code, 13)
        self.assertTrue(line.startswith('ENDED reason=max state=OK '))
        self.assertEqual(line.count('reason='), 1)
        self.assertEqual(self.clock.t, BASE + 600)

    def test_parent_dead_at_launch_is_4_mid_wait_is_13(self):
        code, line = self.run_wait(lambda: est(), owner=4242, alive=lambda p: False)
        self.assertEqual(code, 4)
        self.assertIn('reason=parent-dead', line)
        self.assertFalse(os.path.exists(self.pidfile()))
        code, line = self.run_wait(lambda: est(), owner=4242, alive=lambda p: self.clock.t < BASE + 120)
        self.assertEqual((code, self.clock.t), (13, BASE + 120))
        self.assertIn('ENDED reason=parent-dead', line)
        self.assertFalse(os.path.exists(self.pidfile()))

    def test_owner_liveness_survives_pid_reuse(self):
        # the real check (no injected alive): an owner that started before us is the live parent
        code, line = self.run_wait(lambda: est(), owner=os.getppid(), max_sec=60)
        self.assertEqual(code, 13)
        self.assertIn('reason=max', line)
        # a live pid that started after us is not our parent (its pid was reused): dead at launch. A child starts
        # within the 1 s ps resolution of a young test process, so its start is reported a minute later.
        c = subprocess.Popen(['sleep', '30'])
        self.addCleanup(c.wait)
        self.addCleanup(c.kill)
        real_all = usage.proc_starts
        later = lambda pids: dict((p, t + (60 if p == c.pid else 0)) for p, t in (real_all(pids) or {}).items())
        with mock.patch.object(usage, 'proc_starts', side_effect=later):
            code, line = self.run_wait(lambda: est(), owner=c.pid, max_sec=60)
        self.assertEqual(code, 4)
        self.assertIn('reason=parent-dead', line)
        # the owner's pid taken by a new process mid-wait: its start time changes -> ended
        real = usage.proc_start
        self.clock = Clock(BASE)
        with mock.patch.object(usage, 'proc_start',
                               side_effect=lambda pid: real(pid) + (3600 if self.clock.t >= BASE + 90 else 0)):
            code, line = self.run_wait(lambda: est(), owner=os.getppid(), max_sec=600)
        self.assertEqual((code, self.clock.t), (13, BASE + 90))
        self.assertIn('ENDED reason=parent-dead', line)
        self.assertFalse(os.path.exists(self.pidfile()))

    def test_owner_alive_when_ps_unusable(self):
        # F7: `ps` failing at launch or mid-wait leaves a live owner alive (pid liveness), never parent-dead
        for name, broken in ps_failures():
            with self.subTest(name):
                self.clock = Clock(BASE)
                with broken:
                    code, line = self.run_wait(lambda: est(), owner=os.getppid(), max_sec=60)
                self.assertEqual(code, 13)
                self.assertIn('reason=max', line)
                # unknown at launch: the start stays unrecorded, the next good read records it
                w = self.wait(lambda: est(), owner=os.getppid())
                with broken:
                    self.assertTrue(w._owner_alive(os.getppid()))
                self.assertIsNone(w._owner_start)
                self.assertTrue(w._owner_alive(os.getppid()))
                self.assertIsNotNone(w._owner_start)
                # recorded, then ps fails mid-wait: still alive
                with broken:
                    self.assertTrue(w._owner_alive(os.getppid()))
                    self.assertFalse(w._owner_alive(2 ** 22 + 12345))

    def test_task_terminal_ends_13(self):
        d = self.task()
        self.clock.hooks.append((BASE + 90, lambda: self.set_state(d, 'done')))
        code, line = self.run_wait(lambda: est(), task_dir=d)
        self.assertEqual((code, self.clock.t), (13, BASE + 90))
        self.assertIn('ENDED reason=task-done', line)

    def test_degraded_600s_is_4_and_590_then_ok_keeps_waiting(self):
        code, line = self.run_wait(lambda: usage.unknown('no-sample') if self.clock.t > BASE else est(), interval=10)
        self.assertEqual((code, self.clock.t), (4, BASE + 610))
        self.assertTrue(line.startswith('UNKNOWN reason=degraded state=UNKNOWN'))
        self.assertEqual(line.count('reason='), 1)
        self.clock = Clock(BASE)
        code, line = self.run_wait(lambda: usage.unknown('x') if BASE < self.clock.t <= BASE + 590 else est(),
                                   interval=10, max_sec=1800)
        self.assertEqual(code, 13)

    def test_k_none_with_stale_anchor_is_degraded(self):
        code, _ = self.run_wait(lambda: est(k_src='none', age5=700 + self.clock.t - BASE), interval=60)
        self.assertEqual((code, self.clock.t), (4, BASE + 600))


class TestPaused(Base):
    def test_until_reset_never_stops_and_resets_on_clock(self):
        code, line = self.run_wait(lambda: est('STOP', 99, line=96), until_reset=True, interval=60)
        self.assertEqual(code, 10)
        self.assertGreaterEqual(self.clock.t, R5 + 120)
        self.assertLess(self.clock.t, R5 + 180)
        self.assertIn('RESET reason=clock', line)

    def test_new_window_sample_resets(self):
        code, line = self.run_wait(lambda: est('STOP', 99) if self.clock.t < BASE + 600 else est('OK', 2, window=R5 + 18000),
                                   until_reset=True)
        self.assertEqual((code, self.clock.t), (10, BASE + 600))
        self.assertIn('reason=new-window', line)

    def test_no_reset5_hw_drop_resets(self):
        code, line = self.run_wait(lambda: est('STOP', 99 if self.clock.t < BASE + 300 else 90, window=None),
                                   until_reset=True)
        self.assertEqual((code, self.clock.t), (10, BASE + 300))
        self.assertIn('reason=hw-drop', line)

    def test_cap_5h10m(self):
        code, line = self.run_wait(lambda: est('STOP', 99, window=None), until_reset=True, interval=600, max_sec=10 ** 6)
        self.assertEqual((code, self.clock.t), (10, BASE + 5 * 3600 + 600))
        self.assertIn('reason=cap', line)

    def test_task_paused_at_launch_or_mid_wait_switches_to_reset_mode(self):
        d = self.task(state='paused_usage_limit')
        code, _ = self.run_wait(lambda: est('STOP', 99), task_dir=d, interval=300)
        self.assertEqual(code, 10)
        self.clock = Clock(BASE)
        d = self.task(state='implementing')
        self.clock.hooks.append((BASE + 60, lambda: self.set_state(d, 'paused_usage_limit')))
        code, line = self.run_wait(lambda: est('OK' if self.clock.t < BASE + 60 else 'STOP', 90), task_dir=d,
                                   interval=60)
        self.assertEqual(code, 10)
        self.assertGreaterEqual(self.clock.t, R5 + 120)
        self.assertIn('RESET reason=clock', line)

    def test_paused_never_checks_in_or_refreshes(self):
        d = self.task(state='paused_usage_limit',
                      journal=[{'ts': ts(BASE - 7200), 'kind': 'spawn', 'role': 'implementer'}])
        code, _ = self.run_wait(lambda: est('WARN', 93, line=95, age5=900, ext=3, own_last=self.clock.t - 10),
                                task_dir=d, refresh=True, sid=SID, max_sec=3600)
        self.assertEqual(code, 13)


class TestCheckin(Base):
    def spawned(self, **kw):
        return self.task(journal=[{'ts': ts(BASE), 'kind': 'spawn', 'role': 'implementer'}] + kw.pop('more', []), **kw)

    def test_ladder_30_60_120_120_then_silent(self):
        d = self.spawned()
        fired = []
        for _ in range(5):
            code, line = self.run_wait(lambda: est(), task_dir=d, interval=60, max_sec=4 * 3600)
            fired.append((code, self.clock.t - BASE))
            if code == 11:
                self.assertIn('CHECKIN role=implementer ', line)
        first = BASE + 59 + 1800
        self.assertEqual(fired[0], (11, first - BASE + 1))       # first 60 s poll at/after base+30 min
        gaps = [fired[i + 1][1] - fired[i][1] for i in range(3)]
        self.assertEqual(gaps, [3600, 7200, 7200])
        self.assertEqual(fired[4][0], 13)                         # a 5th interval: none

    def test_stamp_guard_rearm_right_after_checkin_does_not_refire(self):
        d = self.spawned()
        code, _ = self.run_wait(lambda: est(), task_dir=d, interval=60)
        self.assertEqual(code, 11)
        t = self.clock.t
        code, _ = self.run_wait(lambda: est(), task_dir=d, interval=60, max_sec=3540)
        self.assertEqual(code, 13)
        code, _ = self.run_wait(lambda: est(), task_dir=d, interval=60)
        self.assertEqual((code, self.clock.t - t), (11, 3600))

    def test_activity_resets_the_ladder(self):
        d = self.spawned(log=['- %s implementer: step 1 done' % ts(BASE + 1200)])
        code, line = self.run_wait(lambda: est(), task_dir=d, interval=60)
        self.assertEqual((code, self.clock.t), (11, BASE + 1200 + 59 + 1800 + 1))
        self.assertIn('silent_min=30 interval=30', line)

    def test_return_journaled_no_journal_garbled_files_never_due(self):
        d = self.spawned(more=[{'ts': ts(BASE + 60), 'kind': 'return', 'role': 'implementer'}])
        self.assertEqual(self.run_wait(lambda: est(), task_dir=d, max_sec=4 * 3600)[0], 13)
        d = self.task()
        os.unlink(os.path.join(d, 'journal.jsonl'))
        self.assertEqual(self.run_wait(lambda: est(), task_dir=d, max_sec=4 * 3600)[0], 13)
        d = self.spawned()
        with open(os.path.join(d, 'status.md'), 'wb') as fh:
            fh.write(b'\xff\xfe garbage | no table\n')
        with open(os.path.join(d, 'log.md'), 'wb') as fh:
            fh.write(b'\xff- not a line\n')
        self.assertEqual(self.run_wait(lambda: est(), task_dir=d, max_sec=4 * 3600)[0], 13)
        os.unlink(os.path.join(d, 'status.md'))
        self.assertEqual(self.run_wait(lambda: est(), task_dir=d, max_sec=4 * 3600)[0], 13)

    def test_unspawned_in_progress_row_is_never_due(self):
        d = self.task(journal=[{'ts': ts(BASE), 'kind': 'spawn', 'role': 'planner'}])
        self.assertEqual(self.run_wait(lambda: est(), task_dir=d, max_sec=4 * 3600)[0], 13)


class TestRefresh(Base):
    def near(self, **kw):
        kw.setdefault('own_last', self.clock.t - 100)
        return est('WARN', 88, e=91, line=95, age5=300, ext=3, **kw)

    def refresh_log(self):
        with open(os.path.join(self.state, 'refresh-log.jsonl')) as fh:
            return [json.loads(l) for l in fh]

    def test_off_without_flag_cold_cache_no_refresh(self):
        self.assertEqual(self.run_wait(lambda: self.near(), sid=SID, max_sec=600)[0], 13)
        self.clock = Clock(BASE)
        self.assertEqual(self.run_wait(lambda: self.near(own_last=self.clock.t - 3000), refresh=True, sid=SID,
                                       max_sec=600)[0], 13)
        self.assertFalse(os.path.exists(os.path.join(self.state, 'refresh-log.jsonl')))

    def test_warm_refresh_logs_and_600s_guard(self):
        code, line = self.run_wait(lambda: self.near(t_sample=BASE + 30), refresh=True, sid=SID)
        self.assertEqual((code, self.clock.t), (12, BASE))
        self.assertTrue(line.startswith('REFRESH state=WARN'))
        rec = self.refresh_log()[-1]
        self.assertEqual((rec['ts'], rec['sid'], rec['est'], rec['line'], rec['fresh_sample_within_120s']),
                         (BASE, SID, 91.0, 95.0, None))
        code, _ = self.run_wait(lambda: self.near(t_sample=BASE + 30), refresh=True, sid=SID, interval=60)
        self.assertEqual((code, self.clock.t), (12, BASE + 600))
        self.assertTrue(self.refresh_log()[1]['fresh_sample_within_120s'])

    def test_ineffective_disables_for_24h(self):
        self.assertEqual(self.run_wait(lambda: self.near(t_sample=BASE - 50), refresh=True, sid=SID)[0], 12)
        code, _ = self.run_wait(lambda: self.near(t_sample=BASE - 50), refresh=True, sid=SID, interval=300,
                                max_sec=86000)
        self.assertEqual(code, 13)
        self.assertIs(self.refresh_log()[1]['fresh_sample_within_120s'], False)
        code, _ = self.run_wait(lambda: self.near(t_sample=BASE - 50), refresh=True, sid=SID, interval=300)
        self.assertEqual(code, 12)
        self.assertGreaterEqual(self.clock.t, BASE + 120 + 86400)


class TestPidfile(Base):
    def read_pid(self):
        with open(self.pidfile()) as fh:
            return fh.read()

    def write_pid(self, pid, owner='-', sid='-'):
        os.makedirs(self.state, exist_ok=True)
        with open(self.pidfile(), 'w') as fh:
            fh.write('%d %s %s %d\n' % (pid, owner, sid, BASE))

    def test_same_owner_is_armed_and_file_untouched(self):
        self.write_pid(777, owner='4242')
        before = self.read_pid()
        code, line = self.run_wait(lambda: est(), owner=4242, alive=lambda p: True)
        self.assertEqual((code, line), (0, 'ARMED pid=777'))
        self.assertEqual(self.read_pid(), before)

    def test_same_sid_is_armed(self):
        self.write_pid(777, sid=SID)
        self.assertEqual(self.run_wait(lambda: est(), sid=SID), (0, 'ARMED pid=777'))

    def test_stale_or_foreign_pidfile_is_replaced(self):
        for is_wait, owner, sid in ((False, '4242', '-'), (True, '9999', '-'), (True, '-', '-')):
            self.write_pid(777, owner=owner, sid=sid)
            self.clock = Clock(BASE)
            code, _ = self.run_wait(lambda: est(), owner=4242 if owner != '-' else None, alive=lambda p: True,
                                    is_wait=lambda p, launch='-': is_wait, max_sec=60)
            self.assertEqual(code, 13)
            self.assertFalse(os.path.exists(self.pidfile()))   # replaced by ours, then ours removed

    def test_superseded_exits_13_within_one_poll_and_leaves_the_file(self):
        self.clock.hooks.append((BASE + 45, lambda: self.write_pid(888, owner='9999')))
        code, line = self.run_wait(lambda: est(), owner=4242, alive=lambda p: True)
        self.assertEqual((code, self.clock.t), (13, BASE + 60))
        self.assertIn('ENDED reason=superseded', line)
        self.assertTrue(self.read_pid().startswith('888 '))

    def test_pidfile_content_while_running(self):
        seen = []
        self.clock.hooks.append((BASE + 1, lambda: seen.append(self.read_pid().split())))
        self.run_wait(lambda: est(), owner=4242, sid=SID, alive=lambda p: True, max_sec=60)
        self.assertEqual(seen[0], [str(os.getpid()), '4242', SID, str(int(BASE))])


class TestScanner(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='usage-scan-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.dir = os.path.join(self.home, 'projects', '-home-u-proj-a')
        os.makedirs(self.dir)
        self.path = os.path.join(self.dir, 'sess-aaaa.jsonl')

    def line(self, n, tok):
        t = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(time.time() - 600 + n))
        return json.dumps({'type': 'assistant', 'timestamp': t, 'requestId': 'req-%d' % n,
                           'message': {'id': 'm%d' % n, 'model': 'claude-opus-5-5',
                                       'usage': {'input_tokens': tok, 'output_tokens': 10}}}) + '\n'

    def write(self, lines, mode='w'):
        with open(self.path, mode) as fh:
            fh.write(''.join(lines))

    def cost(self, reqs):
        return round(sum(usage.cost(r) for r in reqs), 9)

    def check(self, sc):
        since = time.time() - 3600
        a, _ = sc.scan(self.home, since)
        b, _ = usage.scan(self.home, since)
        self.assertEqual(self.cost(a), self.cost(b))
        return a

    def test_incremental_append_partial_line_truncate_rewrite(self):
        sc = usage.Scanner()
        self.write([self.line(i, 1000) for i in range(5)])
        self.assertEqual(len(self.check(sc)), 5)
        part = self.line(5, 1000)
        self.write([part[:20]], 'a')
        self.assertEqual(len(self.check(sc)), 5)
        self.write([part[20:], self.line(6, 1000)], 'a')
        self.assertEqual(len(self.check(sc)), 7)
        self.write([self.line(i, 5000) for i in range(3)])                     # truncated + rewritten, shorter
        self.assertEqual(len(self.check(sc)), 3)
        self.write([self.line(i, 7000 + i) for i in range(9)])                 # rewritten, longer, same inode
        self.assertEqual(len(self.check(sc)), 9)
        os.unlink(self.path)
        self.write([self.line(i, 3) for i in range(2)])                       # new inode
        self.assertEqual(len(self.check(sc)), 2)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='usage-wait-cli-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.state = os.path.join(self.tmp, 'state')
        os.makedirs(self.home)
        now = int(time.time())
        with open(os.path.join(self.home, '.statusline-usage-log'), 'w') as fh:
            fh.write('%d 10 %d - - %s\n' % (now - 5, now + 3 * 3600, SID))
        self.env = dict(os.environ, ORCH_USAGE_HOME=self.home, ORCH_USAGE_STATE=self.state,
                        ORCH_DIR=os.path.join(self.tmp, 'orch'))
        for k in ('CLAUDE_PID', 'CLAUDE_CODE_SESSION_ID', 'ORCH_USAGE_CMD'):
            self.env.pop(k, None)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_interval_max_runs_to_ended(self):
        t = time.time()
        rc, out, _ = self.main('usage', 'wait', '--interval', '0.05', '--max', '1')
        self.assertEqual(rc, 13)
        self.assertTrue(out.startswith('ENDED reason=max state=OK 5h=10 '))
        self.assertEqual(len(out.splitlines()), 1)
        self.assertLess(time.time() - t, 1.8)
        self.assertEqual(os.listdir(self.state) if os.path.isdir(self.state) else [], [])

    def test_unresolvable_task_bad_args_and_launch_errors_are_4(self):
        rc, out, _ = self.main('-t', 'no-such-task', 'usage', 'wait', '--max', '1')
        self.assertEqual(rc, 4)
        self.assertIn('reason=task-unresolved', out)
        rc, out, _ = self.main('usage', 'wait', '--line', '0')
        self.assertEqual((rc, out.startswith('UNKNOWN ')), (4, True))
        with mock.patch.object(usage, 'safe_estimate', side_effect=RuntimeError('boom')):
            rc, out, _ = self.main('usage', 'wait', '--max', '1')
        self.assertEqual(rc, 4)
        self.assertIn('reason=RuntimeError', out)
        rc, out, _ = self.main('usage', 'wait', '--parent-pid', '999999', '--max', '1')
        self.assertEqual(rc, 4)
        self.assertIn('reason=parent-dead', out)
        self.assertEqual(os.listdir(self.state) if os.path.isdir(self.state) else [], [])

    def test_argparse_error_is_1(self):
        rc, _, err = self.main('usage', 'wait', '--interval')
        self.assertEqual(rc, 1)

    def test_armed_second_wait_and_sigterm_removes_pidfile(self):
        argv = [sys.executable, ORCH, 'usage', 'wait', '--interval', '0.05', '--max', '20',
                '--parent-pid', str(os.getpid()), '--session', SID]
        a = subprocess.Popen(argv, env=self.env, stdout=subprocess.PIPE, universal_newlines=True)
        try:
            pidfile = usage.pidfile_path(self.state, 'session-' + SID)
            deadline = time.time() + 5
            while not os.path.exists(pidfile) and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(os.path.exists(pidfile))
            b = subprocess.run(argv, env=self.env, stdout=subprocess.PIPE, universal_newlines=True, timeout=10)
            self.assertEqual((b.returncode, b.stdout.strip()), (0, 'ARMED pid=%d' % a.pid))
            a.send_signal(signal.SIGTERM)
            self.assertEqual(a.wait(timeout=5), 143)
            self.assertFalse(os.path.exists(pidfile))
        finally:
            if a.poll() is None:
                a.kill()
                a.wait()
            a.stdout.close()


if __name__ == '__main__':
    unittest.main()
