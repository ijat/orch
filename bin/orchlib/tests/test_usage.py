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
BIN = os.path.dirname(os.path.dirname(HERE))
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
        self.home = tempfile.mkdtemp(prefix='usage-home-')
        self.state = tempfile.mkdtemp(prefix='usage-state-')
        self.addCleanup(shutil.rmtree, self.home, True)
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
        self.log('garbage', '', '12 x 1 2 3 s', '%d 50 %d - - s 7' % (self.now, self.r5),
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

    def test_unknown_model_priced_at_top_row(self):
        r = usage.Req(0, 'claude-newmodel-9', 1000000, 0, 0, 0, 1000000, 'x')
        top = usage.TOP_PRICE
        self.assertEqual(top, max(usage.PRICES.values()))
        self.assertAlmostEqual(usage.cost(r), top[0] + top[1])
        self.assertAlmostEqual(usage.cost(r._replace(model=None)), top[0] + top[1])
        self.assertEqual(usage.price('claude-opus-5-5-20261001'), (4, 20, 0.05))
        self.assertEqual(usage.price('claude-opus-5-20250101'), (5, 25, 0.1))

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
        later = lambda pids: dict((p, t + (60 if p == child.pid else 0)) for p, t in real_all(pids).items())
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
