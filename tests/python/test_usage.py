"""orch usage: estimator, line, loaders, CLI. Synthetic fixtures under a temp ORCH_USAGE_HOME; neutral names only."""
import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin")
sys.path.insert(0, BIN)

from orchlib import cli, flow, usage  # noqa: E402

PROJ = '-home-u-proj-a'
SID = 'sess-aaaa'
OPUS = 'claude-opus-5-5'   # $4 in, $20 out per MTok
T0 = 1800000000.0          # fixed clock for pure tests


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def req(t, dollars=0.0, model=OPUS, key='main', i=None, w5=0, w1=0, rd=0, o=0):
    """A Req costing `dollars` of opus input (unless token parts are given)."""
    if i is None:
        i = int(round(dollars * 1e6 / 4))
    return usage.Req(t, model, i, w5, w1, rd, o, key)


def keys(**kw):
    out = {'main': usage.KeyInfo(False, SID, True)}
    out.update(kw)
    return out


def spawn_child(argv):
    """A child process, killed by the Home cleanup."""
    c = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _CHILDREN.append(c)
    return c


def ps_failures():
    """Each way `ps` can be unusable: missing from PATH, timing out, failing to fork, listing nothing."""
    empty = subprocess.CompletedProcess(['ps'], 1, stdout='')
    return (('missing', mock.patch.dict(os.environ, {'PATH': '/nonexistent'})),
            ('timeout', mock.patch.object(usage.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ps', 5))),
            ('oserror', mock.patch.object(usage.subprocess, 'run', side_effect=OSError(11, 'fork failed'))),
            ('no-rows', mock.patch.object(usage.subprocess, 'run', return_value=empty)))


_CHILDREN = []


def _reap():
    while _CHILDREN:
        c = _CHILDREN.pop()
        if c.poll() is None:
            c.kill()
        c.wait()


class Home(unittest.TestCase):
    """A temp ~/.claude-like home with writers for the log, legacy caches, transcripts and the registry."""

    def setUp(self):
        base = tempfile.mkdtemp(prefix='usage-home-')      # base/.claude = H; base/.claude.json is the account file
        self.home = os.path.join(base, '.claude')
        os.mkdir(self.home)
        self.state = tempfile.mkdtemp(prefix='usage-state-')
        self.addCleanup(shutil.rmtree, base, True)
        self.addCleanup(shutil.rmtree, self.state, True)
        self.addCleanup(_reap)
        self.now = time.time()
        self.r5 = int(self.now) + 3 * 3600

    def log(self, *lines):
        with open(os.path.join(self.home, '.statusline-usage-log'), 'a') as fh:
            for l in lines:
                fh.write(l + '\n')

    def sample(self, t, p5, r5=None, p7='-', r7='-', sid=SID):
        self.log('%d %s %s %s %s %s' % (t, p5, r5 or self.r5, p7, r7, sid))

    def legacy(self, kind, reset, pairs):
        with open(os.path.join(self.home, '.statusline-reset-' + kind), 'w') as fh:
            fh.write('%d\n' % reset)
        with open(os.path.join(self.home, '.statusline-ratelimit-' + kind), 'w') as fh:
            for t, p in pairs:
                fh.write('%d %s\n' % (t, p))

    def transcript(self, rows, sid=SID, agent=None, meta=None):
        d = os.path.join(self.home, 'projects', PROJ)
        if agent:
            d = os.path.join(d, sid, 'subagents')
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, (agent or sid) + '.jsonl')
        with open(path, 'a') as fh:
            fh.write(json.dumps({'type': 'user', 'timestamp': iso(self.now - 7200)}) + '\n')
            for r in rows:
                rid, t, model, i, o = r[:5]
                u = {'input_tokens': i, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0,
                     'output_tokens': o, 'cache_creation': {'ephemeral_5m_input_tokens': 0,
                                                            'ephemeral_1h_input_tokens': 0}}
                fh.write(json.dumps({'type': 'assistant', 'timestamp': iso(t), 'requestId': rid,
                                     'message': {'id': 'msg-' + rid, 'model': model, 'usage': u}}) + '\n')
        if meta is not None:
            with open(path[:-len('.jsonl')] + '.meta.json', 'w') as fh:
                json.dump(meta, fh)
        return path

    def registry(self, sid, pid, started=None):
        d = os.path.join(self.home, 'sessions')
        os.makedirs(d, exist_ok=True)
        rec = {'pid': pid, 'sessionId': sid, 'status': 'busy'}
        if started is not None:
            rec['startedAt'] = int(started * 1000)
        with open(os.path.join(d, '%d.json' % pid), 'w') as fh:
            json.dump(rec, fh)

    def est(self, **kw):
        kw.setdefault('session', '-none-')
        return usage.estimate(home=self.home, state=self.state, now=self.now, **kw)

    def cli(self, *argv, **env):
        e = {'ORCH_USAGE_HOME': self.home, 'ORCH_USAGE_STATE': self.state, 'CLAUDE_CODE_SESSION_ID': ''}
        e.update(env)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, e), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(['usage'] + list(argv))
        return rc, out.getvalue().strip(), err.getvalue()


class TestAnchor(Home):
    def test_stale_view_same_window_takes_high_water(self):
        self.sample(self.now - 120, 53, sid='s1')
        self.sample(self.now - 60, 44, sid='s2')
        e = self.est()
        self.assertEqual(int(e.p5 + 0.5), 53)
        self.assertIn(' 5h=53 ', e.text())

    def test_stale_old_window_view_after_fresh_sample(self):
        self.sample(self.now - 120, 3)
        self.sample(self.now - 60, 97, r5=int(self.now) - 600, sid='old')
        e = self.est()
        self.assertEqual(int(e.p5), 3)

    def test_reset_epochs_within_120s_are_one_window(self):
        self.sample(self.now - 120, 30, r5=self.r5)
        self.sample(self.now - 60, 20, r5=self.r5 + 90)
        self.assertEqual(self.est().p5, 30)

    def test_log_and_legacy_union(self):
        self.sample(self.now - 60, 40)
        self.legacy('5h', self.r5, [(self.now - 120, 62)])
        e = self.est()
        self.assertEqual(int(e.p5), 62)
        self.assertEqual(int(e.age5), 120)

    def test_legacy_stale_old_window_view_does_not_set_high_water(self):
        # F2: B's render is a stale view of the window that ended (its log line carries the old reset); the legacy
        # cache has the same 97 but is stamped with the reset file's (new) value -> it must not become this window's hw
        r_old = int(self.now) - 600
        self.sample(self.now - 90, 3, sid='sidA')
        self.sample(self.now - 75, 97, r5=r_old, sid='sidB')   # B's last log line (dedup: <= 60 s heartbeat)
        self.legacy('5h', self.r5, [(self.now - 90, 3), (self.now - 70, 97), (self.now - 20, 97)])
        e = self.est()
        self.assertEqual((int(e.p5), e.state), (3, 'OK'))
        rc, out, _ = self.cli()
        self.assertEqual(rc, 0)
        self.assertIn(' 5h=3 ', out)

    def test_legacy_kept_when_log_line_is_same_window_or_absent(self):
        self.sample(self.now - 60, 40)
        self.legacy('5h', self.r5, [(self.now - 50, 40), (self.now - 30, 62)])   # same window / no log line
        self.assertEqual(int(self.est().p5), 62)
        os.remove(os.path.join(self.home, '.statusline-usage-log'))
        self.assertEqual(int(self.est().p5), 62)   # no log at all: legacy only (as before the log existed)

    def test_legacy_sample_older_than_window_ignored(self):
        self.sample(self.now - 60, 40)
        self.legacy('5h', self.r5, [(self.r5 - 18000 - 10, 90)])
        self.assertEqual(int(self.est().p5), 40)

    def test_malformed_log_lines_skipped(self):
        self.log('garbage', '', '12 x 1 2 3 s', '%d 50 %d - - s aa bb' % (self.now, self.r5),
                 '%d -5 %d - - s' % (self.now, self.r5), '%d nan %d - - s' % (self.now, self.r5),
                 '%d 99 %d' % (self.now, self.r5))
        self.sample(self.now - 30, 21)
        e = self.est()
        self.assertEqual(e.p5, 21)
        self.assertEqual(e.code, 0)

    def test_empty_home_is_unknown_exit_4(self):
        e = self.est()
        self.assertEqual((e.state, e.code), ('UNKNOWN', 4))
        rc, out, _ = self.cli()
        self.assertEqual(rc, 4)
        self.assertTrue(out.startswith('state=UNKNOWN 5h=? 5h_age=?'), out)

    def test_seven_day_fields_and_projection(self):
        r7 = int(self.now) + 2 * 86400
        self.sample(self.now - 23 * 3600, 10, p7=40, r7=r7, sid='s0')
        self.sample(self.now - 60, 12, p7=52, r7=r7)
        e = self.est()
        self.assertEqual(int(e.p7), 52)
        slope = 12.0 / (23 * 3600 - 60)
        self.assertAlmostEqual(e.proj7, 52 + slope * (r7 - self.now), places=6)
        os.remove(os.path.join(self.home, '.statusline-usage-log'))
        # a 6 h busy-period slope (the legacy 7d cache's whole retention) is not projected: < 20 h of samples
        self.sample(self.now - 6 * 3600, 48, p7=48, r7=r7, sid='s0')
        self.sample(self.now - 30, 12, p7=59, r7=r7)
        e = self.est()
        self.assertEqual((int(e.p7), e.proj7), (59, None))
        self.assertIn(' 7d_proj=?', e.text())
        self.sample(self.now - 20 * 3600 - 30, 40, p7=40, r7=r7, sid='s1')   # span exactly 20 h -> projected
        self.assertIsNotNone(self.est().proj7)


class TestAccount(Home):
    """The account rule: one account's samples only (usage.py docstring). Accounts are fake keys '<acct8>.<org8>'."""
    A, B = 'aaaaaaaa.a0a0a0a0', 'bbbbbbbb.b0b0b0b0'
    R7 = 1793890800            # the 2026-11-01 log shape: both accounts share this weekly reset (fixed-timestamp tests only)

    def setUp(self):
        Home.setUp(self)
        # tests that run on the current clock need a weekly reset that is still ahead of it (`_seven` ignores a past
        # reset), so it follows the clock; R7 above belongs to the tests whose timestamps are fixed
        self.r7 = int(self.now) + 4 * 86400

    def line(self, t, p5, r5, p7, r7, acct, sid=SID):
        self.log('%d %s %s %s %s %s%s' % (t, p5, r5, p7, r7, sid, '' if acct is None else ' ' + acct))

    def at(self, now, **kw):
        kw.setdefault('session', '-none-')
        return usage.estimate(home=self.home, state=self.state, now=now, **kw)

    def replay(self, a_tag):
        """A at 7d 97-99 (old 5h window), then B at 14-21 (new window); B newest."""
        self.line(1793451472, 69, 1793467200, 99, self.R7, a_tag)
        self.line(1793453670, 49, 1793467200, 97, self.R7, a_tag)
        if a_tag:                                                     # A in B's 5h window: must not set B's high-water
            self.line(1793516000, 97, 1793526000, 98, self.R7, a_tag)
        self.line(1793510000, 20, 1793526000, 14, self.R7, self.B, 'sid-b')
        self.line(1793515000, 35, 1793526000, 18, self.R7, self.B, 'sid-b')
        self.line(1793520000, 48, 1793526000, 21, self.R7, self.B, 'sid-b')
        return self.at(1793520400)

    def test_replay_of_2026_11_01_untagged_history(self):
        e = self.replay(None)
        self.assertEqual((e.p7, e.age7), (21, 400))
        self.assertEqual((e.p5, e.age5), (48, 400))

    def test_replay_of_2026_11_01_tagged_history(self):
        e = self.replay(self.A)
        self.assertEqual((e.p7, e.age7), (21, 400))
        self.assertEqual((e.p5, e.age5), (48, 400))

    def replay_login(self, stale_tag, legacy=True):
        """The real 2026-11-01 login shape: A (sid-c) at 7d 97-99, B's first render at 1793453662, then sid-c renders its stale
        A payload (49/97 again) at 1793453670, before its own switch to B. stale_tag is what the writer gave that line:
        A when it inherits from sid-c's identical previous line, B (the file's account) when there is none."""
        self.line(1793451472, 69, 1793467200, 99, self.R7, self.A, 'sid-c')
        if stale_tag == self.A:
            self.line(1793452715, 49, 1793467200, 97, self.R7, self.A, 'sid-c')   # the prior identical line it inherits from
        self.line(1793453662, 0, 1793471400, 0, self.R7, self.B, 'sid-b')
        self.line(1793453670, 49, 1793467200, 97, self.R7, stale_tag, 'sid-c')
        self.line(1793453672, 0, 1793471400, 0, self.R7, self.B, 'sid-c')
        self.line(1793510000, 20, 1793526000, 14, self.R7, self.B, 'sid-b')
        self.line(1793520000, 48, 1793526000, 21, self.R7, self.B, 'sid-b')
        if legacy:                       # the legacy 7d cache holds the same stale sample, written after sid-c's switch to B
            self.legacy('7d', self.R7, [(1793453675, 97), (1793510001, 14), (1793520001, 21)])
        return self.at(1793520400)

    def test_replay_login_stale_render_inherits_old_account(self):
        e = self.replay_login(self.A)
        self.assertLessEqual(e.p7, 22)
        self.assertEqual((e.p7, e.age7), (21, 399))

    def test_replay_login_stale_render_without_prior_line_is_the_residual(self):
        # no identical earlier line of that sid (e.g. its first line after a restart): the writer can only use the
        # file's account, so the stale 97 counts for B. Documented limit, not a goal.
        e = self.replay_login(self.B, legacy=False)
        self.assertEqual(e.p7, 97)

    def test_legacy_sample_matching_other_account_log_line_is_dropped(self):
        A, B = self.A, self.B
        log5 = [(100, 90, 1, 's', A), (200, 10, 2, 's', B)]          # B's run starts at T = 200
        log7 = [(100, 97, 9, A), (200, 5, 9, B)]
        leg5 = [(201, 90, 2, None), (202, 90.5, 2, None), (203, 11, 2, None)]
        leg7 = [(205, 97, 9), (206, 6, 9), (260, 97, 9)]
        # a legacy sample after T is dropped when A's log line within LEGACY_MATCH has the same value (205 is 105 s after A's 97)
        acct, l5, l7, g5, g7 = usage.by_account(log5, log7, leg5, leg7)
        self.assertEqual(acct, B)
        self.assertEqual(g5, [(202, 90.5, 2, None), (203, 11, 2, None)])      # 201 matches A's 90; 202 is a different value
        self.assertEqual(g7, [(206, 6, 9), (260, 97, 9)])                     # 260 is 160 s away: out of range, kept
        # with A as the current account, A's own line never drops its samples
        acct, l5, l7, g5, g7 = usage.by_account([(100, 90, 1, 's', A)], [(100, 97, 9, A)], [(105, 90, 1, None)], [(105, 97, 9)])
        self.assertEqual((g5, g7), ([(105, 90, 1, None)], [(105, 97, 9)]))

    def test_replay_without_account_rule_would_give_99(self):
        with mock.patch.object(usage, 'by_account', lambda a5, a7, b5, b7, current=None: (None, [s[:-1] for s in a5], [s[:-1] for s in a7], b5, b7)):
            e = self.replay(None)
        self.assertEqual(e.p7, 99)       # the instrument sees the bug when the rule is off

    def set_acct(self, body):
        """A fake .claude.json beside H: a key ('<acct8>.<org8>') is written as accountUuid and organizationUuid, an
        (account uuid, organization uuid) tuple as given, anything else verbatim (bytes)."""
        if isinstance(body, str):
            body = (body[:8] + '-1111', body[9:] + '-2222')
        with open(os.path.join(os.path.dirname(self.home), '.claude.json'), 'wb') as fh:
            fh.write(json.dumps({'oauthAccount': {'accountUuid': body[0], 'organizationUuid': body[1]}}).encode()
                     if isinstance(body, tuple) else body)

    def stale_newest(self):
        """The review's F4 probe: idle sid-y renders its stale A payload (inherited tag A) after B's latest line."""
        self.line(1793452715, 49, 1793467200, 97, self.R7, self.A, 'sid-y')
        self.line(1793510000, 20, 1793526000, 14, self.R7, self.B, 'sid-x')
        self.line(1793520000, 48, 1793526000, 21, self.R7, self.B, 'sid-x')
        self.line(1793520100, 49, 1793467200, 97, self.R7, self.A, 'sid-y')

    def test_logged_in_account_beats_a_stale_newest_line(self):
        self.stale_newest()
        self.set_acct(self.B)
        for now in (1793520150, 1793520400):
            e = self.at(now)
            self.assertEqual((e.p7, e.p5), (21, 48))

    def test_no_account_file_falls_back_to_the_newest_line(self):
        self.stale_newest()
        self.assertEqual(self.at(1793520150).p7, 97)       # the old rule, and the instrument sees F4 without the file
        for bad in (b'', b'not json', b'[1]', b'{"oauthAccount": "x"}', b'{"oauthAccount": {"accountUuid": 12345678}}',
                    b'{"oauthAccount": {"accountUuid": "BBBBBBBB-1"}}', b'{"oauthAccount": {"accountUuid": "bbbb"}}'):
            self.set_acct(bad)
            self.assertEqual(self.at(1793520150).p7, 97, bad)

    def test_load_account(self):
        self.assertIsNone(usage.load_account(self.home))
        self.set_acct(self.B)
        self.assertEqual(usage.load_account(self.home), self.B)
        self.assertEqual(usage.load_account(self.home + os.sep), self.B)
        self.set_acct(b'{"oauthAccount": null}')
        self.assertIsNone(usage.load_account(self.home))

    def test_logged_in_account_with_untagged_log_changes_nothing(self):
        r5, now = self.r5, self.now
        self.line(now - 900, 60, r5, 50, self.r7, None)
        self.line(now - 60, 30, r5, 5, self.r7, None)
        self.legacy('5h', r5, [(now - 30, 31)])
        before = self.est()
        self.set_acct(self.B)
        after = self.est()
        self.assertEqual((after.p5, after.p7, after.age5), (before.p5, before.p7, before.age5))
        self.assertEqual((after.p5, after.p7), (60, 50))

    def test_logged_in_account_without_its_own_line_counts_nothing_else(self):
        r5, now = self.r5, self.now
        self.line(now - 900, 60, r5, 50, self.r7, self.A)
        self.line(now - 60, 30, r5, 52, self.r7, None)                 # untagged: not known to be B's
        self.legacy('5h', r5, [(now - 30, 31)])
        self.set_acct(self.B)
        e = self.est()
        self.assertEqual((e.p5, e.p7), (None, None))
        self.line(now - 20, 5, r5, 3, self.r7, self.B)                # B's first line: its run starts, T = now - 20
        e = self.est()
        self.assertEqual((e.p5, e.p7), (5, 3))

    def test_by_account_current_and_t_after_stale_lines(self):
        A, B = self.A, self.B
        log7 = [(100, 97, 9, A), (200, 5, 9, B), (210, 97, 9, A), (220, 6, 9, B), (230, 7, 9, None), (300, 97, 9, A)]
        leg7 = [(205, 8, 9), (225, 9, 9), (305, 97, 9)]
        acct, _, l7, _, g7 = usage.by_account([], log7, [], leg7, current=B)
        self.assertEqual(acct, B)
        self.assertEqual(l7, [(200, 5, 9), (220, 6, 9), (230, 7, 9)])  # B's run back from 220 stops at A's 210: T = 220
        self.assertEqual(g7, [(225, 9, 9)])                             # 205 before T; 305 matches A's stale 97
        self.assertEqual(usage.by_account([], log7, [], leg7)[0], A)   # no current: newest line (the old rule)

    def test_two_organizations_of_one_account_do_not_mix(self):
        # the 2026-11-14 bug: one accountUuid, two organizations, the same weekly reset; the other one's 98 won the max
        a_o1, a_o2 = 'aaaaaaaa.11111111', 'aaaaaaaa.22222222'
        r5, now = self.r5, self.now
        self.line(now - 7200, 80, r5, 98, self.r7, a_o1)
        self.line(now - 3600, 90, r5, 97, self.r7, a_o1)
        self.line(now - 300, 12, r5, 41, self.r7, a_o2)
        self.line(now - 60, 14, r5, 42, self.r7, a_o2)
        self.set_acct(a_o2)
        e = self.est()
        self.assertEqual((e.p5, e.p7), (14, 42))
        self.assertEqual(int(e.age7), 60)                 # the chosen key's newest sample
        self.set_acct(a_o1)
        e = self.est()
        self.assertEqual((e.p5, e.p7), (90, 98))
        self.assertEqual(int(e.age7), 7200)               # age is the newest 98, the key's own high-water sample
        self.set_acct(('aaaaaaaa-1', None))               # unreadable organization: key 'aaaaaaaa.-', no line of it yet
        self.assertEqual(usage.load_account(self.home), 'aaaaaaaa.-')
        e = self.est()
        self.assertEqual((e.p5, e.p7), (None, None))
        self.line(now - 30, 5, r5, 3, self.r7, 'aaaaaaaa.-')
        e = self.est()
        self.assertEqual((e.p5, e.p7), (5, 3))

    def test_an_old_8_char_tag_is_another_subscription_after_the_upgrade(self):
        r5, now = self.r5, self.now
        self.line(now - 900, 60, r5, 98, self.r7, 'aaaaaaaa')              # the old writer's tag
        self.set_acct('aaaaaaaa.22222222')
        self.assertEqual((self.est().p5, self.est().p7), (None, None))      # no sample of the new key yet
        self.line(now - 20, 5, r5, 41, self.r7, 'aaaaaaaa.22222222')
        e = self.est()
        self.assertEqual((e.p5, e.p7), (5, 41))
        self.assertEqual(usage.by_account([(1, 9, 9, 's', 'aaaaaaaa')], [(1, 9, 9, 'aaaaaaaa')], [], [],
                                          current='aaaaaaaa.22222222')[0], 'aaaaaaaa.22222222')

    def test_key_forms(self):
        self.assertEqual(usage._acct('aaaaaaaa.11111111'), 'aaaaaaaa.11111111')
        self.assertEqual(usage._acct('aaaaaaaa.-'), 'aaaaaaaa.-')
        self.assertEqual(usage._acct('aaaaaaaa'), 'aaaaaaaa')
        for bad in (None, '', '-', 'aaaaaaaa.', 'aaaaaaaa.1111', '.11111111', 'aaaaaaaa.ZZZZZZZZ', 'aaaaaaaa.11111111.1', '--------.-'):
            self.assertIsNone(usage._acct(bad), bad)
        self.assertEqual(usage.account_key('aaaaaaaa-1111', 'bbbbbbbb-2222'), 'aaaaaaaa.bbbbbbbb')
        for org in (None, 5, 'short', 'ZZZZZZZZ-1'):
            self.assertEqual(usage.account_key('aaaaaaaa-1111', org), 'aaaaaaaa.-')
        for acct in (None, 5, 'short', 'AAAAAAAA-1'):
            self.assertIsNone(usage.account_key(acct, 'bbbbbbbb-2222'))

    def test_a_b_a_keeps_earlier_a_window(self):
        r5, now = self.r5, self.now
        self.line(now - 900, 60, r5, 50, self.r7, self.A)
        self.line(now - 600, 90, r5, 5, self.r7, self.B)          # B in the same window: dropped when A is newest
        self.line(now - 60, 30, r5, 52, self.r7, self.A)
        e = self.est()
        self.assertEqual((e.p5, e.p7), (60, 52))
        self.assertEqual(int(e.age5), 900)

    def test_newest_b_drops_a_but_keeps_b(self):
        r5, now = self.r5, self.now
        self.line(now - 900, 60, r5, 50, self.r7, self.A)
        self.line(now - 60, 30, r5, 5, self.r7, self.B)
        e = self.est()
        self.assertEqual((e.p5, e.p7), (30, 5))

    def test_untagged_newest_filters_nothing(self):
        r5, now = self.r5, self.now
        self.line(now - 900, 60, r5, 50, self.r7, self.A)
        self.line(now - 60, 30, r5, 5, self.r7, None)
        self.assertEqual((self.est().p5, self.est().p7), (60, 50))

    def test_malformed_seventh_field_is_untagged(self):
        r5, now = self.r5, self.now
        self.line(now - 900, 60, r5, 50, self.r7, self.A)
        self.line(now - 60, 30, r5, 5, self.r7, 'zz')
        self.line(now - 50, 31, r5, 5, self.r7, 'AAAAAAAA')      # upper case is not an id either
        self.line(now - 40, 32, r5, 5, self.r7, '--------')
        s5, s7 = usage.load_log(self.home, with_acct=True)
        self.assertEqual([s[-1] for s in s5], [self.A, None, None, None])
        self.assertEqual(self.est().p5, 60)
        s5, _ = usage.load_log(self.home)
        self.assertEqual([len(s) for s in s5], [4, 4, 4, 4])     # default shape unchanged

    def test_load_log_accepts_6_and_7_fields(self):
        self.log('%d 10 %d 20 %d s1' % (self.now - 20, self.r5, self.r7),
                 '%d 11 %d 21 %d s2 %s' % (self.now - 10, self.r5, self.r7, self.A),
                 '%d 12 %d 22 %d s3 %s extra' % (self.now - 5, self.r5, self.r7, self.A))
        s5, s7 = usage.load_log(self.home)
        self.assertEqual([round(s[1]) for s in s5], [10, 11])
        s5, s7 = usage.load_log(self.home, with_acct=True)
        self.assertEqual([(s[3], s[4]) for s in s5], [('s1', None), ('s2', self.A)])
        self.assertEqual([s[-1] for s in s7], [None, self.A])

    def test_by_account_untagged_and_legacy_count_only_from_t(self):
        A, B = self.A, self.B
        log5 = [(100, 90, 1, None, None), (150, 91, 1, None, A), (200, 10, 2, None, B), (230, 70, 2, None, None),
                (240, 12, 2, None, B)]
        log7 = [(100, 95, 9, None), (200, 5, 9, B)]
        leg5 = [(150, 99, 1, None), (200, 11, 2, None), (250, 13, 2, None)]
        leg7 = [(150, 96, 9), (260, 6, 9)]
        acct, l5, l7, g5, g7 = usage.by_account(log5, log7, leg5, leg7)
        # newest line is B's; B's latest run starts at 200 (150 is A's; the untagged 230 does not break it): T = 200
        self.assertEqual(acct, B)
        self.assertEqual(l5, [(200, 10, 2, None), (230, 70, 2, None), (240, 12, 2, None)])
        self.assertEqual(l7, [(200, 5, 9)])
        self.assertEqual(g5, [(200, 11, 2, None), (250, 13, 2, None)])
        self.assertEqual(g7, [(260, 6, 9)])
        # newest untagged: nothing filtered, shapes stripped
        acct, l5, l7, g5, g7 = usage.by_account(log5[:2] + [(300, 1, 2, None, None)], log7, leg5, leg7)
        self.assertEqual((acct, len(l5), len(l7), g5, g7), (None, 3, 2, leg5, leg7))
        self.assertEqual(usage.by_account([], [], [], [])[0], None)

    def test_untagged_and_legacy_before_t_dropped_in_estimate(self):
        r5, now = self.r5, self.now
        self.line(now - 1200, 70, r5, 50, self.r7, None)          # before T: A or whatever, dropped
        self.line(now - 600, 20, r5, 10, self.r7, self.B)         # T
        self.line(now - 500, 25, r5, 10, self.r7, None)           # after T: counted
        self.line(now - 30, 30, r5, 10, self.r7, self.B)
        self.legacy('5h', r5, [(now - 1100, 88), (now - 400, 33)])
        e = self.est()
        self.assertEqual(int(e.p5), 33)
        self.legacy('5h', r5, [(now - 1100, 88)])
        self.assertEqual(int(self.est().p5), 30)

    def kjson(self):
        with open(os.path.join(self.state, 'k.json')) as fh:
            return json.load(fh)

    def persisting_samples(self, acct):
        for j, p in enumerate((5, 15, 25)):
            self.line(self.now - 900 + 60 * j, p, self.r5, 10, self.r7, acct)
        self.transcript([('m1', self.r5 - 18000 + 60, OPUS, 2500000, 0)])

    def test_k_is_saved_per_account_and_leaves_others_alone(self):
        os.makedirs(self.state, exist_ok=True)
        seed = {'k': 3.0, 'windows': [[1, 3.0]], 'by_acct': {self.A: {'k': 9.0, 'windows': [[2, 9.0]]}}}
        with open(os.path.join(self.state, 'k.json'), 'w') as fh:
            json.dump(seed, fh)
        self.persisting_samples(self.B)
        self.est()
        d = self.kjson()
        self.assertEqual((d['k'], d['windows']), (3.0, [[1, 3.0]]))
        self.assertEqual(d['by_acct'][self.A], seed['by_acct'][self.A])
        self.assertAlmostEqual(d['by_acct'][self.B]['k'], 2.5)
        self.assertEqual(d['by_acct'][self.B]['windows'], [[self.r5, 2.5]])
        self.assertEqual([n for n in os.listdir(self.state) if n != 'k.json'], [])

    def test_new_account_does_not_inherit_other_k(self):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, 'k.json'), 'w') as fh:
            json.dump({'k': 3.0, 'windows': [[1, 3.0]], 'by_acct': {self.A: {'k': 9.0, 'windows': [[2, 9.0]]}}}, fh)
        self.line(self.now - 30, 20, self.r5, 10, self.r7, self.B)     # no window cost: k would come from history
        e = self.est()
        self.assertEqual((e.k, e.k_src), (None, 'none'))
        self.line(self.now - 20, 21, self.r5, 10, self.r7, self.A)
        self.assertEqual((self.est().k, self.est().k_src), (9.0, 'prev'))

    def test_untagged_run_keeps_by_acct_and_old_k_json_loads(self):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, 'k.json'), 'w') as fh:       # a k.json from before: no by_acct
            json.dump({'k': 3.0, 'windows': [[1, 3.0]]}, fh)
        self.persisting_samples(None)
        self.est()
        d = self.kjson()
        self.assertAlmostEqual(d['k'], 2.5)
        self.assertNotIn('by_acct', d)
        d['by_acct'] = {self.A: {'k': 9.0, 'windows': []}}
        with open(os.path.join(self.state, 'k.json'), 'w') as fh:
            json.dump(d, fh)
        self.line(self.now - 5, 40, self.r5, 10, self.r7, None)
        self.transcript([('m2', self.r5 - 18000 + 120, OPUS, 2500000, 0)])
        self.est()
        self.assertEqual(self.kjson()['by_acct'], {self.A: {'k': 9.0, 'windows': []}})
        with open(os.path.join(self.state, 'k.json'), 'w') as fh:
            fh.write('[1]')                                              # not a dict: fresh start, no crash
        self.est()

    def test_by_acct_garbage_is_ignored(self):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, 'k.json'), 'w') as fh:
            json.dump({'k': 3.0, 'by_acct': [1, 2]}, fh)
        self.persisting_samples(self.B)
        self.est()
        self.assertAlmostEqual(self.kjson()['by_acct'][self.B]['k'], 2.5)


class TestCompute(unittest.TestCase):
    """Pure compute() with an injected clock."""
    r5 = T0 + 3 * 3600
    ws = r5 - 18000

    def base(self, hw_pts=(10, 30, 50), ta=T0 - 120, window_cost=10.0):
        s5 = [(ta - 60 * (len(hw_pts) - 1 - j), p, self.r5, SID) for j, p in enumerate(hw_pts)]
        reqs = [req(self.ws + 60, window_cost)]
        return s5, reqs

    def test_extrapolation_exact(self):
        s5, reqs = self.base()
        reqs.append(req(T0 - 60, 2.0, key='other'))
        e = usage.compute(s5, [], reqs, keys(), T0)
        k = 50 / 10.0
        self.assertEqual(e.k_src, 'window')
        self.assertAlmostEqual(e.k, k)
        self.assertAlmostEqual(e.est, 50 + k * 2.0)
        self.assertAlmostEqual(e.ext, 10.0)

    def test_mythos_has_its_own_price_row(self):
        self.assertEqual(usage.price('claude-mythos-5-2'), (8, 40, 0.025))      # not the top row ($10 / $50)
        r = usage.Req(0, 'claude-mythos-5-2', 1000000, 0, 0, 0, 1000000, 'x')
        self.assertAlmostEqual(usage.cost(r), 8 + 40)

    def test_unknown_model_priced_at_top_row(self):
        r = usage.Req(0, 'claude-newmodel-9', 1000000, 0, 0, 0, 1000000, 'x')
        top = usage.TOP_PRICE
        self.assertEqual(top, max(usage.PRICES.values()))
        self.assertAlmostEqual(usage.cost(r), top[0] + top[1])
        self.assertAlmostEqual(usage.cost(r._replace(model=None)), top[0] + top[1])
        self.assertEqual(usage.price('claude-opus-5-5-20261001'), (4, 20, 0.05))
        self.assertEqual(usage.price('claude-opus-5-20250101'), (5, 25, 0.1))

    def test_fable_5_2_priced_at_its_own_row(self):
        # $8 in / $40 out, cache read $0.20 (platform pricing page, 2026-11-12); not the 5.1 or the bare `claude-fable-5` row
        self.assertEqual(usage.price('claude-fable-5-2'), (8, 40, 0.025))
        self.assertEqual(usage.price('claude-fable-5-1'), (10, 50, 0.025))
        r = usage.Req(0, 'claude-fable-5-2', 1000000, 0, 0, 1000000, 1000000, 'x')
        self.assertAlmostEqual(usage.cost(r), 8 + 0.20 + 40)

    def test_cost_formula(self):
        r = usage.Req(0, OPUS, 1000, 2000, 3000, 4000, 500, 'x')
        want = (1000 * 4 + (2000 * 1.25 + 3000 * 2) * 4 + 4000 * 4 * 0.05 + 500 * 20) / 1e6
        self.assertAlmostEqual(usage.cost(r), want)

    def test_k_none_legacy_line(self):
        s5 = [(T0 - 60, 3, self.r5, SID)]
        e = usage.compute(s5, [], [], keys(), T0)
        self.assertEqual((e.k_src, e.line, e.est), ('none', 95.0, 3))
        self.assertIn(' k=? k_src=none ', e.text())
        e = usage.compute([(T0 - 60, 91, self.r5, SID)], [], [], keys(), T0)
        self.assertEqual(e.state, 'WARN')   # legacy warn 90

    def test_k_prev_when_window_base_tiny(self):
        s5 = [(T0 - 60, 3, self.r5, SID)]
        e = usage.compute(s5, [], [req(T0 - 30, 1.0)], keys(), T0, {'k': 2.0, 'windows': []})
        self.assertEqual(e.k_src, 'prev')
        self.assertAlmostEqual(e.est, 3 + 2.0 * 1.0)

    def test_clamp_80_and_99(self):
        s5, reqs = self.base()
        e = usage.compute(s5, [], reqs, keys(), T0)
        self.assertEqual(e.line, 98.0)   # 100 - S 1 - R 0 - U 1
        with mock.patch.object(usage, 'S_PTS', 0), mock.patch.object(usage, 'U0', 0):
            self.assertEqual(usage.compute(s5, [], reqs, keys(), T0).line, 99.0)
        # a live subagent with a 5 M-token context: rd$ = 5e6*4*0.05/1e6 = $1 -> R = k*1 = 5 pts
        big = req(T0 - 30, key='big', i=0, rd=5000000)
        e = usage.compute(s5, [], reqs + [big], keys(big=usage.KeyInfo(True, SID, True)), T0)
        self.assertAlmostEqual(e.R, 5.0 * 1.0, places=6)
        huge = req(T0 - 30, key='big', i=0, rd=500000000)
        e = usage.compute(s5, [], reqs + [huge], keys(big=usage.KeyInfo(True, SID, True)), T0)
        self.assertEqual(e.line, 80.0)

    def test_line_formula_unclamped_region(self):
        s5, reqs = self.base()
        big = req(T0 - 30, key='big', i=0, rd=5000000)
        reqs.append(req(T0 - 10, 1.0, key='other'))
        e = usage.compute(s5, [], reqs + [big], keys(big=usage.KeyInfo(True, SID, True)), T0)
        # ext = k * (1 + $0.5 of the big request's cache read) ; R = k * $1 ; U = 1 + 0.5 ext
        ext = 5.0 * (1.0 + 5000000 * 4 * 0.05 / 1e6)
        self.assertAlmostEqual(e.ext, ext)
        self.assertAlmostEqual(e.line, 100 - 1 - 5.0 - (1 + 0.5 * ext))

    def test_due_rewrite_adds_wr(self):
        s5, reqs = self.base()
        ctx = 100000
        sub_recent = req(T0 - 100, key='sub', i=0, rd=ctx)
        e1 = usage.compute(s5, [], reqs + [sub_recent], keys(sub=usage.KeyInfo(True, SID, True)), T0)
        sub_due = req(T0 - 250, key='sub', i=0, rd=ctx)     # 5m TTL: idle > 300 - 60
        e2 = usage.compute(s5, [], reqs + [sub_due], keys(sub=usage.KeyInfo(True, SID, True)), T0)
        rd_cost = ctx * 4 * 0.05 / 1e6
        wr_cost = ctx * 4 * 1.25 / 1e6
        self.assertAlmostEqual(e1.R, e1.k * rd_cost)
        self.assertAlmostEqual(e2.R, e2.k * (rd_cost + wr_cost))
        # main session: TURNS read turns; 1h TTL (no writes seen) -> re-write due only after 3540 s idle
        m = req(T0 - 3550, key='m2', i=0, rd=ctx, o=0)
        e3 = usage.compute(s5, [], reqs + [m], keys(m2=usage.KeyInfo(False, 'x', True)), T0)
        self.assertAlmostEqual(e3.R, e3.k * (4 * rd_cost + ctx * 4 * 2 / 1e6))

    def test_liveness_idle_and_alive_flags(self):
        s5, reqs = self.base()
        r = [req(T0 - 100, key='a', i=10), req(T0 - 100, key='b', i=10), req(T0 - 4000, key='c', i=10),
             req(T0 - 100, key='d', i=10)]
        k = keys(a=usage.KeyInfo(True, SID, True), b=usage.KeyInfo(True, SID, False),
                 c=usage.KeyInfo(True, SID, True), d=usage.KeyInfo(False, 'x', True))
        e = usage.compute(s5, [], reqs + r, k, T0)
        self.assertEqual((e.agents, e.sessions), (1, 1))

    def test_confirmed_main_never_drops_out_of_r_when_idle(self):
        # F3: a live (registry-confirmed) main session idle past its 1 h TTL keeps TURNS x read turns + the full
        # re-write in R; the line must not rise when its cache expires (idle 3550 s -> 3700 s -> 3 h)
        r5 = T0 + 3600                     # window started 4 h ago, so a 3 h idle request is in this window
        s5 = [(T0 - 240, 10, r5, SID), (T0 - 180, 30, r5, SID), (T0 - 120, 50, r5, SID)]
        base = [req(r5 - 18000 + 60, 10.0)]
        ctx = 150000
        rd_cost = ctx * 4 * 0.05 / 1e6
        wr_cost = ctx * 4 * 2 / 1e6
        live = keys(m=usage.KeyInfo(False, 'orch', True, True))
        out = {}
        for idle in (3500, 3550, 3700, 3 * 3600):
            out[idle] = usage.compute(s5, [], base + [req(T0 - idle, key='m', i=0, rd=ctx)], live, T0)
        self.assertAlmostEqual(out[3500].R, out[3500].k * 4 * rd_cost)
        for idle in (3550, 3700, 3 * 3600):
            e = out[idle]
            self.assertAlmostEqual(e.R, e.k * (4 * rd_cost + wr_cost), msg=idle)
            self.assertEqual(e.sessions, 1, idle)
            self.assertLessEqual(e.line, out[3550].line + 1e-9, idle)
        # registry-less main (alive, not confirmed): the IDLE_MAX cut stays
        e = usage.compute(s5, [], base + [req(T0 - 3700, key='m', i=0, rd=ctx)],
                          keys(m=usage.KeyInfo(False, 'orch', True)), T0)
        self.assertEqual((e.sessions, e.R), (0, 0.0))
        # confirmed, but no request in this window: not counted (bounds R for sessions left open for days)
        e = usage.compute(s5, [], base + [req(r5 - 18000 - 10, key='m', i=0, rd=ctx)], live, T0)
        self.assertEqual(e.sessions, 0)
        # subagents: confirmed (unfinished, parent live) -> kept with its re-write; unconfirmed -> the cut stays
        e = usage.compute(s5, [], base + [req(T0 - 3700, key='a', i=0, rd=ctx)],
                          keys(a=usage.KeyInfo(True, SID, True, True)), T0)
        self.assertEqual(e.agents, 1)
        self.assertAlmostEqual(e.R, e.k * (rd_cost + ctx * 4 * 1.25 / 1e6))   # no writes seen: 5 m TTL assumed
        e = usage.compute(s5, [], base + [req(T0 - 3700, key='a', i=0, rd=ctx)],
                          keys(a=usage.KeyInfo(True, SID, True)), T0)
        self.assertEqual(e.agents, 0)

    def test_states(self):
        s5, reqs = self.base()
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=50)
        self.assertEqual((e.state, e.line), ('STOP', 50.0))
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=54)
        self.assertEqual(e.state, 'WARN')
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=54, warn_override=52)
        self.assertEqual(e.state, 'OK')
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=56, warn_override=50)
        self.assertEqual(e.state, 'WARN')
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=1)
        self.assertEqual((e.state, e.line), ('STOP', 1.0))   # explicit override: not clamped

    def test_stale_only_near_the_line(self):
        s5 = [(T0 - 1000, 10, self.r5, SID), (T0 - 900, 30, self.r5, SID), (T0 - 400, 50, self.r5, SID)]
        reqs = [req(self.ws + 60, 10.0)]
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=59)
        self.assertEqual(e.state, 'STALE')   # anchor 400 s > 300, est 50 >= 59 - 10
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=61, warn_override=60)
        self.assertEqual(e.state, 'OK')      # 50 < 61 - 10
        s5[-1] = (T0 - 200, 50, self.r5, SID)
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=59, warn_override=58)
        self.assertEqual(e.state, 'OK')      # fresh anchor

    def test_eta_warn(self):
        s5, reqs = self.base()
        reqs.append(req(T0 - 60, 1.0, key='other'))   # burn = 5 * $1 / 10 min = 0.5 pts/min
        e = usage.compute(s5, [], reqs, keys(), T0, line_override=65, warn_override=None)
        self.assertAlmostEqual(e.burn, 0.5)
        self.assertEqual(e.eta_line, '0h20m')   # (65 - 55) / 0.5 = 20 min
        self.assertEqual(e.state, 'WARN')

    def test_burn_others_excludes_own_session(self):
        s5, reqs = self.base()
        reqs += [req(T0 - 60, 1.0, key='main'), req(T0 - 60, 2.0, key='o')]
        k = keys(o=usage.KeyInfo(False, 'other-sid', True))
        e = usage.compute(s5, [], reqs, k, T0, session=SID)
        self.assertAlmostEqual(e.burn, 5.0 * 3.0 / 10)
        self.assertAlmostEqual(e.burn_others, 5.0 * 2.0 / 10)

    def test_reset_passed_window(self):
        old = T0 - 600
        s5 = [(old - 1200, 97, old, SID)]
        reqs = [req(old + 60, 1.0)]
        e = usage.compute(s5, [], reqs, keys(), T0, {'k': 3.0, 'windows': []})
        self.assertEqual((e.p5, e.k_src, e.reset5, e.reset_in), (0.0, 'prev', None, None))
        self.assertAlmostEqual(e.est, 3.0)
        self.assertIn(' 5h=0 ', e.text())
        self.assertIn(' reset_5h=? reset_in=? ', e.text())
        e = usage.compute(s5, [], reqs, keys(), T0)
        self.assertEqual((e.k_src, e.est, e.line), ('none', 0.0, 95.0))
        # long gone reset: the anchor is at most one window back
        e = usage.compute([(T0 - 90000, 97, T0 - 80000, SID)], [], [req(T0 - 30000, 50.0), req(T0 - 60, 1.0)],
                          keys(), T0, {'k': 3.0, 'windows': []})
        self.assertAlmostEqual(e.est, 3.0)

    def test_deterministic(self):
        s5, reqs = self.base()
        reqs.append(req(T0 - 200, 0.7, key='o', o=5000))
        k = keys(o=usage.KeyInfo(True, SID, True))
        a = usage.compute(s5, [], reqs, k, T0).text()
        b = usage.compute(list(reversed(s5)), [], list(reversed(reqs)), k, T0).text()
        self.assertEqual(a, b)
        self.assertNotEqual(a, usage.compute(s5, [], reqs, k, T0 + 400).text())


class TestKGuard(unittest.TestCase):
    r5 = T0 + 3 * 3600
    ws = r5 - 18000

    def test_weak_evidence_clamped_to_trailing_p90(self):
        # 1 -> 24 points on $2 of window cost: k 12 from a contaminated base
        s5 = [(T0 - 1200, 1, self.r5, SID), (T0 - 600, 24, self.r5, SID)]
        reqs = [req(self.ws + 60, 2.0)]
        hist = {'k': 1.2, 'windows': [[self.r5 - 18000 * i, k] for i, k in
                                      enumerate((0.8, 0.7, 0.9, 1.4, 0.6, 0.8, 1.1, 0.75, 0.85, 0.65), 1)]}
        e = usage.compute(s5, [], reqs, keys(), T0, hist)
        self.assertEqual(e.k, 1.4)
        self.assertIsNone(e.persist)
        e = usage.compute(s5, [], reqs, keys(), T0, {'k': 1.2, 'windows': hist['windows'][:2]})
        self.assertEqual(e.k, 1.2)   # < 3 trailing windows: the previous window's k
        e = usage.compute(s5, [], reqs, keys(), T0)
        self.assertAlmostEqual(e.k, 12.0)   # nothing to clamp to

    def test_enough_evidence_unclamped_and_persisted(self):
        s5 = [(T0 - 1800, 5, self.r5, SID), (T0 - 1200, 15, self.r5, SID), (T0 - 600, 24, self.r5, SID)]
        reqs = [req(self.ws + 60, 6.0)]
        hist = {'k': 1.0, 'windows': [[self.r5 - 18000 * i, 1.0] for i in range(1, 6)]}
        e = usage.compute(s5, [], reqs, keys(), T0, hist)
        self.assertAlmostEqual(e.k, 4.0)
        self.assertEqual(e.persist, (int(self.r5), 4.0))
        self.assertEqual(usage.k_guard(9.0, 2, 30, 9.0, [1.0] * 5, None), (1.0, True))
        self.assertEqual(usage.k_guard(9.0, 3, 9.9, 9.0, [1.0] * 5, None), (1.0, True))
        self.assertEqual(usage.k_guard(9.0, 3, 30, 4.99, [1.0] * 5, None), (1.0, True))
        self.assertEqual(usage.k_guard(0.5, 1, 0, 0.1, [1.0] * 5, None), (0.5, False))  # clamp only from above

    def test_no_persist_below_hw_20(self):
        s5 = [(T0 - 1800, 1, self.r5, SID), (T0 - 1200, 8, self.r5, SID), (T0 - 600, 19, self.r5, SID)]
        e = usage.compute(s5, [], [req(self.ws + 60, 6.0)], keys(), T0)
        self.assertIsNone(e.persist)


class TestScan(Home):
    def test_duplicate_request_ids_counted_once(self):
        t = self.now - 300
        self.transcript([('r1', t, OPUS, 250000, 0), ('r1', t, OPUS, 250000, 0), ('r2', t, OPUS, 250000, 0)])
        reqs, _ = usage.scan(self.home, self.now - 3600)
        self.assertEqual(len(reqs), 2)
        self.assertAlmostEqual(sum(usage.cost(r) for r in reqs), 2.0)

    def test_subagent_transcript_counted(self):
        self.sample(self.now - 600, 10)
        self.sample(self.now - 500, 20)
        self.sample(self.now - 400, 30)
        self.transcript([('m1', self.r5 - 18000 + 60, OPUS, 2500000, 0)])           # $10 window base
        self.transcript([('s1', self.now - 100, OPUS, 250000, 0)], agent='agent-x')  # $1 after the anchor
        e = self.est()
        self.assertEqual(e.agents, 1)
        self.assertAlmostEqual(e.est, 30 + 3.0 * 1.0)

    def test_subagent_liveness_live_finished_killed(self):
        t = self.now - 100
        self.transcript([('a', t, OPUS, 10, 0)], agent='agent-live')
        self.transcript([('b', t, OPUS, 10, 0)], agent='agent-done', meta={'agentType': 'x', 'finished': True})
        self.transcript([('c', t, OPUS, 10, 0)], agent='agent-killed', meta={'agentType': 'x'})
        _, k = usage.scan(self.home, self.now - 3600)
        alive = sorted(os.path.basename(p) for p, v in k.items() if v.sub and v.alive)
        self.assertEqual(alive, ['agent-killed.jsonl', 'agent-live.jsonl'])

    def test_main_liveness_registry(self):
        t = self.now - 100
        self.transcript([('a', t, OPUS, 10, 0)], sid='sess-live')
        self.transcript([('b', t, OPUS, 10, 0)], sid='sess-gone')
        _, k = usage.scan(self.home, self.now - 3600)
        self.assertTrue(all(v.alive for v in k.values()))   # no registry: idle rule only
        self.registry('sess-live', os.getpid())
        self.registry('sess-gone', 2 ** 22 + 12345)
        _, k = usage.scan(self.home, self.now - 3600)
        self.assertEqual(sorted(v.sid for v in k.values() if v.alive), ['sess-live'])
        self.assertEqual(sorted(v.sid for v in k.values() if v.confirmed), ['sess-live'])
        self.transcript([('c', t, OPUS, 10, 0)], sid='sess-live', agent='agent-run')
        self.transcript([('d', t, OPUS, 10, 0)], sid='sess-live', agent='agent-end', meta={'finished': True})
        self.transcript([('e', t, OPUS, 10, 0)], sid='sess-gone', agent='agent-orphan')
        _, k = usage.scan(self.home, self.now - 3600)
        self.assertEqual(sorted(os.path.basename(p) for p, v in k.items() if v.sub and v.confirmed), ['agent-run.jsonl'])
        _, k2 = usage.Scanner().scan(self.home, self.now - 3600)
        self.assertEqual(k2, k)

    def test_own_session_confirmed_by_claude_pid(self):
        self.sample(self.now - 60, 30)
        self.transcript([('w', self.r5 - 18000 + 60, OPUS, 2500000, 0)], sid='sess-other')
        self.transcript([('a', self.now - 3700, OPUS, 10, 0)], sid='sess-own')   # no registry at all
        with mock.patch.dict(os.environ, {'CLAUDE_PID': ''}):
            self.assertEqual(self.est(session='sess-own').sessions, 0)
        with mock.patch.dict(os.environ, {'CLAUDE_PID': str(os.getpid())}):
            self.assertEqual(self.est(session='sess-own').sessions, 1)
        with mock.patch.dict(os.environ, {'CLAUDE_PID': str(2 ** 22 + 12345)}):
            self.assertEqual(self.est(session='sess-own').sessions, 0)
        # pid reuse: a live CLAUDE_PID that started after us is not our session (a child starts within the 1 s ps
        # resolution of a young test process, so its start is reported a minute later)
        child = spawn_child(['sleep', '30'])
        real_all = usage.proc_starts
        later = lambda pids: dict((p, t + (60 if p == child.pid else 0)) for p, t in (real_all(pids) or {}).items())
        with mock.patch.dict(os.environ, {'CLAUDE_PID': str(child.pid)}), \
                mock.patch.object(usage, 'proc_starts', side_effect=later):
            self.assertEqual(self.est(session='sess-own').sessions, 0)
        # ... nor one whose registry entry was recorded before the process started
        self.registry('sess-own', os.getpid(), started=usage.proc_start(os.getpid()) - 3600)
        with mock.patch.dict(os.environ, {'CLAUDE_PID': str(os.getpid())}):
            self.assertEqual(self.est(session='sess-own').sessions, 0)

    def test_registry_pid_reuse(self):
        t = self.now - 100
        self.transcript([('a', t, OPUS, 10, 0)], sid='sess-live')
        self.transcript([('b', t, OPUS, 10, 0)], sid='sess-reused')
        me = usage.proc_start(os.getpid())
        self.assertIsNotNone(me)
        self.registry('sess-live', os.getpid(), started=me + 1)       # written just after start: live
        child = spawn_child(['sleep', '30'])
        self.registry('sess-reused', child.pid, started=me - 3600)    # pid now held by a later process
        self.assertEqual(usage.live_sessions(self.home), {'sess-live'})
        _, k = usage.scan(self.home, self.now - 3600)
        self.assertEqual(sorted(v.sid for v in k.values() if v.confirmed), ['sess-live'])

    def test_ps_unusable_falls_back_to_pid_alive(self):
        # F7: a `ps` failure is "unknown", not "dead": every start-time check degrades to pid liveness, so a degraded
        # reader keeps live sessions in R (more conservative), never drops them
        t = self.now - 100
        self.transcript([('a', t, OPUS, 10, 0)], sid='sess-live')
        self.transcript([('b', t, OPUS, 10, 0)], sid='sess-reused')
        self.transcript([('c', t, OPUS, 10, 0)], sid='sess-dead')
        me = usage.proc_start(os.getpid())
        child = spawn_child(['sleep', '30'])
        wait = spawn_child([sys.executable, '-c', 'import time; time.sleep(30)', 'usage', 'wait'])
        dead = spawn_child(['true'])
        dead.wait()
        self.registry('sess-live', os.getpid(), started=me + 1)
        self.registry('sess-reused', child.pid, started=me - 3600)
        self.registry('sess-dead', dead.pid, started=me)
        self.assertEqual(usage.live_sessions(self.home), {'sess-live'})   # ps works: start times apply
        confirmed = lambda: sorted(v.sid for v in usage.scan(self.home, self.now - 3600)[1].values() if v.confirmed)
        self.assertEqual(confirmed(), ['sess-live'])
        for name, broken in ps_failures():
            with self.subTest(name), broken:
                self.assertIsNone(usage.proc_starts([os.getpid()]))
                self.assertEqual(usage.proc_starts([dead.pid]), {})
                self.assertIs(usage.proc_start(os.getpid()), usage.PS_UNKNOWN)
                self.assertIsNone(usage.proc_start(dead.pid))
                self.assertEqual(usage.live_sessions(self.home), {'sess-live', 'sess-reused'})   # the pid-only set
                self.assertTrue(usage.claude_pid_live(self.home, os.getpid()))
                self.assertFalse(usage.claude_pid_live(self.home, dead.pid))
                self.assertEqual(confirmed(), ['sess-live', 'sess-reused'])   # stays in R; the dead one doesn't
                self.assertTrue(usage.is_wait_proc(wait.pid))
                self.assertTrue(usage.is_wait_proc(wait.pid, '%d' % (me - 600)))
                self.assertFalse(usage.is_wait_proc(dead.pid))

    def test_k_persisted_atomically(self):
        for j, p in enumerate((5, 15, 25)):
            self.sample(self.now - 900 + 60 * j, p)
        self.transcript([('m1', self.r5 - 18000 + 60, OPUS, 2500000, 0)])
        self.est()
        with open(os.path.join(self.state, 'k.json')) as fh:
            d = json.load(fh)
        self.assertAlmostEqual(d['k'], 2.5)
        self.assertEqual(d['windows'], [[self.r5, 2.5]])
        self.assertEqual([n for n in os.listdir(self.state) if n != 'k.json'], [])


class TestCli(Home):
    def setUp(self):
        Home.setUp(self)
        for j, p in enumerate((10, 30, 50)):
            self.sample(self.now - 300 + 60 * j, p, p7=40, r7=int(self.now) + 86400)
        self.transcript([('m1', self.r5 - 18000 + 60, OPUS, 2500000, 0)])

    def test_line_format(self):
        rc, out, _ = self.cli()
        self.assertEqual(rc, 0, out)
        names = [kv.split('=', 1)[0] for kv in out.split()]
        self.assertEqual(names, ['state', '5h', '5h_age', '7d', '7d_age', 'reset_5h', 'reset_in', 'est', 'line',
                                 'eta_line', 'burn', 'k', 'k_src', 'sessions', 'agents', 'burn_others', '7d_proj'])
        self.assertIn('state=OK 5h=50 ', out)
        self.assertRegex(out, r' est=50\.0 line=\d+\.\d eta_line=\? burn=0\.00/min k=5\.00 k_src=window ')

    def test_json_same_fields(self):
        rc, out, _ = self.cli('--json')
        d = json.loads(out)
        rc2, line, _ = self.cli()
        self.assertEqual(list(d), [kv.split('=', 1)[0] for kv in line.split()])
        self.assertEqual((d['state'], d['5h'], d['k_src']), ('OK', 50, 'window'))

    def test_line_and_warn_overrides(self):
        rc, out, _ = self.cli('--line', '50')
        self.assertEqual(rc, 2)
        self.assertIn(' line=50.0 ', out)
        rc, out, _ = self.cli('--line', '60', '--warn', '40')
        self.assertEqual(rc, 1)
        rc, out, _ = self.cli('--line', '0')
        self.assertEqual(rc, 4)
        self.assertIn('reason=ValueError', out)

    def test_never_raises(self):
        with mock.patch.object(usage, 'compute', side_effect=ZeroDivisionError('x')):
            rc, out, _ = self.cli()
        self.assertEqual(rc, 4)
        self.assertTrue(out.startswith('state=UNKNOWN 5h=? '), out)
        self.assertTrue(out.endswith('reason=ZeroDivisionError'), out)

    def test_wait_field_with_task(self):
        rc, out, _ = self.cli('-t', os.path.join(self.home, 'no-such', 'task'))
        self.assertTrue(out.endswith(' wait=none'), out)
        os.makedirs(self.state, exist_ok=True)
        child = spawn_child(['sleep', '30'])   # alive, but not a `usage wait` (not our own pid: the runner's argv varies)
        for launch in ('-', '%d' % time.time()):
            with open(os.path.join(self.state, 'wait-no-such__task.pid'), 'w') as fh:
                fh.write('%d - - %s\n' % (child.pid, launch))
            self.assertEqual(usage.wait_armed(self.state, 'no-such__task'), None)

    def test_is_wait_proc_argv_tokens_and_pid_reuse(self):
        def py(*tail):
            return spawn_child([sys.executable, '-c', 'import time; time.sleep(30)'] + list(tail))
        a = py('usage', 'wait', '--refresh')
        for c in (py('wait', 'usage'), py('usage', 'x', 'wait'), py('usage-wait'), py('usage_check', 'waiting')):
            self.assertFalse(usage.is_wait_proc(c.pid), c.args)
        deadline = time.time() + 5
        while usage.proc_start(a.pid) is None and time.time() < deadline:
            time.sleep(0.02)
        start = usage.proc_start(a.pid)
        self.assertTrue(usage.is_wait_proc(a.pid))                        # bare-pid file: argv only
        self.assertTrue(usage.is_wait_proc(a.pid, '%d' % (start + 1)))    # launched just after start
        self.assertFalse(usage.is_wait_proc(a.pid, '%d' % (start - 600)))  # pidfile older than the process: reused
        self.assertFalse(usage.is_wait_proc(a.pid, 'junk'))
        os.makedirs(self.state, exist_ok=True)
        path = usage.pidfile_path(self.state, 'k')
        for launch, want in (('%d' % (start + 1), a.pid), ('%d' % (start - 600), None)):
            with open(path, 'w') as fh:
                fh.write('%d 1 - %s\n' % (a.pid, launch))
            self.assertEqual(usage.wait_armed(self.state, 'k'), want)

    def test_etime_parse(self):
        for v, want in (('00:05', 5), ('12:34', 754), ('01:00:00', 3600), ('2-03:04:05', 2 * 86400 + 11045),
                        ('', None), ('x', None), ('1:2:3:4', None)):
            self.assertEqual(usage._etime_sec(v), want, v)

    def test_5h_round_trip_through_run_usage(self):
        # reset_5h= must never be read as 5h=
        cmd = '%s %s usage' % (sys.executable, os.path.join(BIN, 'orch'))
        env = {'ORCH_USAGE_HOME': self.home, 'ORCH_USAGE_STATE': self.state, 'ORCH_USAGE_CMD': cmd,
               'CLAUDE_CODE_SESSION_ID': ''}
        with mock.patch.dict(os.environ, env):
            code, u5, u7, line = flow.run_usage()
        self.assertEqual((code, u5, u7), (0, 50, 40), line)
        self.assertIn('reset_5h=%d' % self.r5, line)


if __name__ == '__main__':
    unittest.main()
