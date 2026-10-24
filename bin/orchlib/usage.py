"""orch usage: estimate the account's rolling 5h usage between statusline samples, and the dynamic stop line.

est = window high-water sample + k x API-equivalent cost of every transcript (all sessions, all subagents)
since that sample; k (5h points per $) is calibrated per window from the samples themselves.
line = clamp(100 - S - R - U, LINE_MIN, LINE_MAX): R = what stopping everything still costs, U = uncertainty.

Reads only local files under H = $ORCH_USAGE_HOME or ~/.claude; writes only its state dir
($ORCH_USAGE_STATE or H/.orch-usage/, atomic writes, never required). No network.
I/O loaders are separate from compute(), which is pure (no files, no clock) so replays can drive it.

Known limit: usage made outside this machine's transcripts (claude.ai, other devices) is invisible
until the next sample shows it; U and the window high-water bound it, nothing more.
"""
import bisect
import collections
import datetime
import glob
import json
import os
import subprocess
import sys
import tempfile

# -- constants (D3 defaults kept by the Step 0 backtest; Q1 amendments: k early-window guard, STALE near the line)
S_PTS = 1.0            # safety slack, points
U0 = 1.0               # fixed uncertainty, points
U1 = 0.5               # uncertainty per extrapolated point
TURNS = 4              # orchestrator pause turns in the reserve
LINE_MIN, LINE_MAX = 80.0, 99.0
WARN_MARGIN = 5.0      # WARN at est >= line - 5 ...
WARN_ETA = 1800        # ... or when the line is < 30 min away at the current burn
STALE_AGE = 300        # STALE: anchor older than this ...
STALE_NEAR = 10.0      # ... while est >= line - 10
LEGACY_LINE, LEGACY_WARN = 95.0, 90.0   # no k at all
WINDOW = 18000
WEEK = 7 * 86400
GROUP = 120            # reset epochs within 120 s are one window
IDLE_MAX = 3600        # a transcript idle longer than this is not live
K_MIN_HW, K_MIN_COST = 5.0, 0.05        # below either: k from this window is meaningless
K_PERSIST_HW = 20.0    # persist a window's k only from hw >= 20 (and the guard satisfied)
GUARD_VALUES = 3       # k guard: >= 3 distinct sample values in the window ...
GUARD_SPAN = 10.0      # ... spanning >= 10 points ...
GUARD_COST = 5.0       # ... and >= $5 of window cost up to the anchor; else k <= trailing p90
K_TRAIL = 10           # windows kept for the trailing k distribution
BURN_SPAN = 600

# $/MTok input, output, cache-read multiplier (TIERS.md rows; older ids for history). Longest id prefix wins.
PRICES = {
    'claude-fable-5-1': (10, 50, 0.025), 'claude-fable-5': (10, 50, 0.1),
    'claude-opus-5-5': (4, 20, 0.05), 'claude-opus-5': (5, 25, 0.1),
    'claude-opus-4-8': (5, 25, 0.1), 'claude-opus-4-7': (5, 25, 0.1),
    'claude-sonnet-5-5': (2, 10, 0.1), 'claude-sonnet-5': (2, 10, 0.1),
    'claude-haiku-5-5': (0.10, 0.50, 0.1), 'claude-sonnet-4-6': (3, 15, 0.1),
    'claude-haiku-4-5': (1, 5, 0.1),
}
TOP_PRICE = max(PRICES.values())   # unknown model: never $0
STATES = {'OK': 0, 'WARN': 1, 'STOP': 2, 'STALE': 3, 'UNKNOWN': 4}

Req = collections.namedtuple('Req', 't model i w5 w1 rd o key')
KeyInfo = collections.namedtuple('KeyInfo', 'sub sid alive')   # alive: registry / meta.json says so (idle not applied)


# -- pure helpers --------------------------------------------------------------------------------

def price(model):
    best = None
    for pre, row in PRICES.items():
        if model and model.startswith(pre) and (best is None or len(pre) > len(best)):
            best = pre
    return PRICES[best] if best else TOP_PRICE


def cost(r):
    pi, po, rm = price(r.model)
    return (r.i * pi + (r.w5 * 1.25 + r.w1 * 2) * pi + r.rd * pi * rm + r.o * po) / 1e6


def q90(vals):
    v = sorted(vals)
    return v[min(len(v) - 1, int(0.9 * len(v)))]


def k_guard(k_raw, n_values, span, cost_base, trailing, k_prev):
    """-> (k, capped). Until the window shows enough evidence, k is clamped to the trailing p90
    (fewer than 3 trailing windows: the previous window's k; none: unclamped)."""
    if n_values >= GUARD_VALUES and span >= GUARD_SPAN and cost_base >= GUARD_COST:
        return k_raw, False
    cap = q90(trailing) if len(trailing) >= 3 else k_prev
    if cap is not None and k_raw > cap:
        return cap, True
    return k_raw, False


def fmt_dur(secs):
    if secs is None:
        return '?'
    secs = max(0, int(secs))
    return '%dh%dm' % (secs // 3600, secs % 3600 // 60)


class Cum:
    """Cumulative cost over time-sorted requests; C(a, b) = cost of requests with a < t <= b."""

    def __init__(self, reqs):
        rs = sorted(reqs, key=lambda r: r.t)
        self.t = [r.t for r in rs]
        self.c = []
        acc = 0.0
        for r in rs:
            acc += cost(r)
            self.c.append(acc)

    def at(self, t):
        i = bisect.bisect_right(self.t, t)
        return self.c[i - 1] if i else 0.0

    def between(self, a, b):
        return max(0.0, self.at(b) - self.at(a))


def _window(samples, now):
    """-> (r5, rolled). Newest future reset anyone has seen; else the latest past one (rolled)."""
    fut = [s[2] for s in samples if s[2] is not None and s[2] > now]
    if fut:
        return max(fut), False
    past = [s[2] for s in samples if s[2] is not None]
    return (max(past), True) if past else (None, False)


def _anchor(samples, r5):
    win = [s for s in samples if s[2] is not None and abs(s[2] - r5) <= GROUP and s[1] is not None]
    if not win:
        return None
    hw = max(s[1] for s in win)
    ta = max(s[0] for s in win if s[1] >= hw - 0.5)
    return hw, ta, win


def _seven(s7, now):
    """-> (p7 hw, t_anchor7, r7, proj) or Nones."""
    fut = [s[2] for s in s7 if s[2] is not None and s[2] > now]
    if not fut:
        return None, None, None, None
    r7 = max(fut)
    win = [s for s in s7 if abs(s[2] - r7) <= GROUP and s[1] is not None]
    if not win:
        return None, None, r7, None
    hw = max(s[1] for s in win)
    ta = max(s[0] for s in win if s[1] >= hw - 0.5)
    recent = sorted((s for s in win if s[0] >= now - 86400), key=lambda s: s[0])
    proj = None
    if recent and recent[-1][0] - recent[0][0] >= 3600:
        slope = max(0.0, (hw - recent[0][1]) / max(1.0, recent[-1][0] - recent[0][0]))
        proj = hw + slope * (r7 - now)
    return hw, ta, r7, proj


class Estimate:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def code(self):
        return STATES[self.state]

    def fields(self, wait=None):
        """Ordered (name, value) pairs; value None prints as '?'."""
        f = [('state', self.state), ('5h', self.p5), ('5h_age', self.age5), ('7d', self.p7), ('7d_age', self.age7),
             ('reset_5h', self.reset5), ('reset_in', self.reset_in)]
        if self.state == 'UNKNOWN' and getattr(self, 'reason', None):
            f.append(('reason', self.reason))
        else:
            f += [('est', self.est), ('line', self.line), ('eta_line', self.eta_line), ('burn', self.burn),
                  ('k', self.k), ('k_src', self.k_src), ('sessions', self.sessions), ('agents', self.agents),
                  ('burn_others', self.burn_others), ('7d_proj', self.proj7)]
        if wait is not None:
            f.append(('wait', wait))
        return f

    def text(self, wait=None):
        out = []
        for name, v in self.fields(wait):
            if v is None:
                s = '?'
            elif name in ('est', 'line'):
                s = '%.1f' % v
            elif name in ('burn', 'burn_others'):
                s = '%.2f/min' % v
            elif name == 'k':
                s = '%.2f' % v
            elif name in ('5h', '7d', '7d_proj'):
                s = '%d' % int(v + 0.5)
            elif isinstance(v, float):
                s = '%d' % int(v)
            else:
                s = str(v)
            out.append('%s=%s' % (name, s))
        return ' '.join(out)

    def as_dict(self, wait=None):
        d = {}
        for name, v in self.fields(wait):
            if name in ('5h', '7d', '7d_proj') and v is not None:
                v = int(v + 0.5)
            elif name in ('est', 'line') and v is not None:
                v = round(v, 1)
            elif name in ('burn', 'burn_others', 'k') and v is not None:
                v = round(v, 2)
            elif name in ('5h_age', '7d_age') and v is not None:
                v = int(v)
            d[name] = v
        return d


def unknown(reason, p5=None, age5=None, p7=None, age7=None, reset5=None, reset_in=None):
    return Estimate(state='UNKNOWN', p5=p5, age5=age5, p7=p7, age7=age7, reset5=reset5, reset_in=reset_in,
                    reason=reason, persist=None)


def compute(s5, s7, reqs, keys, now, k_hist=None, line_override=None, warn_override=None, session=None):
    """Pure. s5: [(t, p5, r5, sid)]; s7: [(t, p7, r7)]; reqs: [Req]; keys: {key: KeyInfo};
    k_hist: {'k': float|None, 'windows': [[r5, k], ...]}. -> Estimate (persist = (r5, k) to store, or None)."""
    k_hist = k_hist or {}
    r5, rolled = _window(s5, now)
    p7, ta7, r7, proj7 = _seven(s7, now)
    age7 = (now - ta7) if ta7 is not None else None
    if r5 is None:
        return unknown('no-sample', p7=p7, age7=age7)
    hist = [w for w in (k_hist.get('windows') or []) if isinstance(w, (list, tuple)) and len(w) == 2]
    trailing = [float(k) for wr5, k in hist if abs(wr5 - r5) > GROUP and k and k > 0]
    k_prev = k_hist.get('k')
    k_prev = float(k_prev) if isinstance(k_prev, (int, float)) and k_prev > 0 else None
    C = Cum(reqs)
    persist = None
    if rolled:
        # reset passed and no sample of the new window yet: hw = 0 from the old reset (at most one window back)
        hw = 0.0
        ta = ws = max(float(r5), now - WINDOW)
        reset5, reset_in = None, None
        k, k_src = (k_prev, 'prev') if k_prev else (None, 'none')
    else:
        hw, ta, win = _anchor(s5, r5)
        ws = r5 - WINDOW
        reset5, reset_in = int(r5), fmt_dur(r5 - now)
        base = C.between(ws, ta)
        if hw >= K_MIN_HW and base >= K_MIN_COST:
            vals = set(int(s[1] + 0.5) for s in win)
            span = hw - min(s[1] for s in win)
            k, capped = k_guard(hw / base, len(vals), span, base, trailing, k_prev)
            k_src = 'window'
            if not capped and hw >= K_PERSIST_HW and len(vals) >= GUARD_VALUES and span >= GUARD_SPAN \
                    and base >= GUARD_COST:
                persist = (int(r5), k)
        elif k_prev:
            k, k_src = k_prev, 'prev'
        else:
            k, k_src = None, 'none'
    ext = k * C.between(ta, now) if k else 0.0
    est = hw + ext

    # reserve: what stopping everything still costs
    bykey = collections.defaultdict(list)
    for r in reqs:
        if r.t <= now:
            bykey[r.key].append(r)
    reserve = 0.0
    n_sess = n_agents = 0
    for key, rs in bykey.items():
        info = keys.get(key)
        if info is None or not info.alive:
            continue
        rs.sort(key=lambda r: r.t)
        last = rs[-1]
        idle = now - last.t
        if idle > IDLE_MAX:
            continue
        if info.sub:
            n_agents += 1
        else:
            n_sess += 1
        pi, po, rm = price(last.model)
        ctx = last.i + last.w5 + last.w1 + last.rd
        outs = sorted(r.o for r in rs[-10:])
        out = outs[len(outs) // 2]
        ttl = 3600 if (last.w1 > last.w5 or (last.w5 == 0 and last.w1 == 0 and not info.sub)) else 300
        rd_cost = (ctx * pi * rm + out * po) / 1e6
        wr_cost = ctx * pi * (2 if ttl == 3600 else 1.25) / 1e6
        pend = wr_cost if idle > ttl - 60 else 0.0
        reserve += (rd_cost if info.sub else TURNS * rd_cost) + pend
    R = (k or 0.0) * reserve
    U = U0 + U1 * ext
    if line_override is not None:
        line = float(line_override)
    elif k is None:
        line = LEGACY_LINE
    else:
        line = min(LINE_MAX, max(LINE_MIN, 100 - S_PTS - R - U))
    if warn_override is not None:
        warn_at = float(warn_override)
    elif k is None and line_override is None:
        warn_at = LEGACY_WARN
    else:
        warn_at = line - WARN_MARGIN

    burn = (k or 0.0) * C.between(now - BURN_SPAN, now) / (BURN_SPAN / 60.0)
    other = [r for r in reqs if session is None or keys.get(r.key) is None or keys[r.key].sid != session]
    burn_others = (k or 0.0) * Cum(other).between(now - BURN_SPAN, now) / (BURN_SPAN / 60.0) if other else 0.0
    eta = (line - est) / burn * 60 if burn > 0.01 and est < line else None
    age5 = now - ta
    if est >= line:
        state = 'STOP'
    elif age5 > STALE_AGE and est >= line - STALE_NEAR:
        state = 'STALE'
    elif est >= warn_at or (warn_override is None and eta is not None and eta < WARN_ETA):
        state = 'WARN'
    else:
        state = 'OK'
    own = [r.t for r in reqs if session is not None and keys.get(r.key) is not None
           and keys[r.key].sid == session and not keys[r.key].sub and r.t <= now]
    return Estimate(state=state, p5=hw, age5=age5, p7=p7, age7=age7, reset5=reset5, reset_in=reset_in,
                    est=est, line=line, eta_line=fmt_dur(eta) if eta is not None else None, burn=burn, k=k,
                    k_src=k_src, sessions=n_sess, agents=n_agents, burn_others=burn_others, proj7=proj7,
                    ext=ext, R=R, U=U, persist=persist, window=r5, rolled=rolled,
                    own_last=max(own) if own else None, t_sample=max(s[0] for s in s5))


# -- I/O -----------------------------------------------------------------------------------------

def home_dir():
    return os.environ.get('ORCH_USAGE_HOME') or os.path.join(os.path.expanduser('~'), '.claude')


def state_dir(home=None):
    return os.environ.get('ORCH_USAGE_STATE') or os.path.join(home or home_dir(), '.orch-usage')


def _num(s):
    if s == '-':
        return None
    v = float(s)
    if v != v or v < 0:
        raise ValueError(s)
    return v


def load_log(home):
    """The sample log: '<epoch> <p5> <reset5> <p7> <reset7> <sid>', '-' = unknown; malformed lines skipped."""
    s5, s7 = [], []
    try:
        fh = open(os.path.join(home, '.statusline-usage-log'), encoding='utf-8', errors='replace')
    except OSError:
        return s5, s7
    with fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 6 or not parts[0].isdigit():
                continue
            try:
                t = float(parts[0])
                p5, r5, p7, r7 = (_num(x) for x in parts[1:5])
            except ValueError:
                continue
            sid = None if parts[5] == '-' else parts[5]
            if p5 is not None and r5 is not None:
                s5.append((t, p5, r5, sid))
            if p7 is not None and r7 is not None:
                s7.append((t, p7, r7))
    return s5, s7


def _read_reset(path):
    try:
        with open(path, encoding='utf-8') as fh:
            v = fh.read().strip()
        return float(v) if v.isdigit() else None
    except OSError:
        return None


def _read_pairs(path):
    out = []
    try:
        fh = open(path, encoding='utf-8', errors='replace')
    except OSError:
        return out
    with fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            try:
                out.append((float(parts[0]), _num(parts[1])))
            except ValueError:
                continue
    return [(t, p) for t, p in out if p is not None]


def load_legacy(home, now):
    """The legacy statusline caches. A sample gets the reset file's value only when that reset is in the future
    and the sample is inside its window; a past reset is kept only to detect a rolled window."""
    s5, s7 = [], []
    for kind, span, dst in (('5h', WINDOW, s5), ('7d', WEEK, s7)):
        reset = _read_reset(os.path.join(home, '.statusline-reset-' + kind))
        if reset is None:
            continue
        for t, p in _read_pairs(os.path.join(home, '.statusline-ratelimit-' + kind)):
            if t < reset - span:
                continue
            if kind == '5h':
                dst.append((t, p, reset, None))
            elif reset > now:
                dst.append((t, p, reset))
    return s5, s7


def _ep(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        return None


def transcripts(home):
    root = os.path.join(home, 'projects')
    return glob.glob(os.path.join(root, '*', '*.jsonl')) + \
        glob.glob(os.path.join(root, '*', '*', 'subagents', '*.jsonl'))


def _parse_line(line, path, since, key, out):
    """One transcript line -> out[rid] = Req when it is an assistant request with t >= since."""
    if '"usage"' not in line or '"assistant"' not in line:
        return
    try:
        d = json.loads(line)
    except ValueError:
        return
    if not isinstance(d, dict) or d.get('type') != 'assistant':
        return
    m = d.get('message') or {}
    u = m.get('usage') if isinstance(m, dict) else None
    if not isinstance(u, dict):
        return
    t = _ep(d.get('timestamp'))
    if t is None or t < since:
        return
    cc = u.get('cache_creation') or {}
    w5 = cc.get('ephemeral_5m_input_tokens', 0) or 0
    w1 = cc.get('ephemeral_1h_input_tokens', 0) or 0
    if not cc:
        w5 = u.get('cache_creation_input_tokens', 0) or 0
    rid = d.get('requestId') or m.get('id') or '%s@%s' % (path, t)
    out[rid] = Req(t, m.get('model'), u.get('input_tokens', 0) or 0, w5, w1,
                   u.get('cache_read_input_tokens', 0) or 0, u.get('output_tokens', 0) or 0, key)


def parse_transcript(path, since, key):
    """-> {rid: Req} of assistant requests with t >= since (last entry per requestId wins)."""
    out = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            _parse_line(line, path, since, key, out)
    return out


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError, TypeError):
        return False
    return True


def live_sessions(home):
    """Session ids with a live pid in H/sessions/*.json; None when the registry is missing or unparsable."""
    ids, parsed = set(), False
    for p in glob.glob(os.path.join(home, 'sessions', '*.json')):
        try:
            with open(p, encoding='utf-8') as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or 'sessionId' not in d:
            continue
        parsed = True
        if pid_alive(d.get('pid')):
            ids.add(d['sessionId'])
    return ids if parsed else None


def _meta_finished(path):
    try:
        with open(path[:-len('.jsonl')] + '.meta.json', encoding='utf-8') as fh:
            return json.load(fh).get('finished') is True
    except (OSError, ValueError, AttributeError):
        return False


def scan(home, since):
    """-> (reqs, keys). Transcripts touched since `since`; requests deduped by requestId across files."""
    reg = live_sessions(home)
    reqs, keys = {}, {}
    for p in transcripts(home):
        try:
            if os.path.getmtime(p) < since:
                continue
        except OSError:
            continue
        sub = os.sep + 'subagents' + os.sep in p
        sid = p.split(os.sep)[-3] if sub else os.path.basename(p)[:-len('.jsonl')]
        alive = (not _meta_finished(p)) if sub else (reg is None or sid in reg)
        keys[p] = KeyInfo(sub, sid, alive)
        try:
            reqs.update(parse_transcript(p, since, p))
        except OSError:
            continue
    return list(reqs.values()), keys


class Scanner:
    """Incremental scan() for a long-running wait: each transcript is read once and then only its new complete
    lines. Keyed by (path, inode); an inode change, size < offset, an mtime older than recorded, changed bytes
    just before the offset, or a `since` earlier than the file's first read -> that file is rescanned from 0."""
    TAIL = 64

    def __init__(self):
        self.files = {}

    def _read(self, p, f):
        with open(p, 'rb') as fh:
            if f['off']:
                back = min(self.TAIL, f['off'])
                fh.seek(f['off'] - back)
                if fh.read(back) != f['tail']:
                    return False
            data = fh.read()
        end = data.rfind(b'\n') + 1   # complete lines only; a half-written last line waits for the next poll
        if end:
            for line in data[:end].decode('utf-8', 'replace').splitlines(True):
                _parse_line(line, p, f['floor'], p, f['reqs'])
            f['off'] += end
            f['tail'] = (f['tail'] + data[:end])[-self.TAIL:]
        return True

    def scan(self, home, since):
        reg = live_sessions(home)
        reqs, keys, seen = {}, {}, set()
        for p in transcripts(home):
            try:
                stt = os.stat(p)
            except OSError:
                continue
            if stt.st_mtime < since:
                continue
            sub = os.sep + 'subagents' + os.sep in p
            sid = p.split(os.sep)[-3] if sub else os.path.basename(p)[:-len('.jsonl')]
            alive = (not _meta_finished(p)) if sub else (reg is None or sid in reg)
            keys[p] = KeyInfo(sub, sid, alive)
            f = self.files.get(p)
            for attempt in (0, 1):
                if f is None or attempt or f['ino'] != stt.st_ino or stt.st_size < f['off'] \
                        or stt.st_mtime < f['mtime'] or since < f['floor']:
                    f = {'ino': stt.st_ino, 'off': 0, 'tail': b'', 'mtime': stt.st_mtime, 'floor': since, 'reqs': {}}
                try:
                    ok = self._read(p, f)
                except OSError:
                    f = None
                    break
                if ok:
                    break
            if f is None:
                self.files.pop(p, None)
                continue
            f['mtime'] = stt.st_mtime
            self.files[p] = f
            seen.add(p)
            for rid, r in f['reqs'].items():
                if r.t >= since:
                    reqs[rid] = r
        for p in list(self.files):
            if p not in seen:
                del self.files[p]
        return list(reqs.values()), keys


def load_k(state):
    try:
        with open(os.path.join(state, 'k.json'), encoding='utf-8') as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_k(state, k_hist, persist):
    """Record (r5, k) for the trailing distribution; atomic; failures are silent (state is never required)."""
    r5, k = persist
    wins = [w for w in (k_hist.get('windows') or []) if isinstance(w, list) and len(w) == 2 and abs(w[0] - r5) > GROUP]
    wins.append([r5, round(k, 4)])
    wins = sorted(wins)[-K_TRAIL:]
    data = {'k': round(k, 4), 'windows': wins}
    if data == k_hist:
        return
    try:
        os.makedirs(state, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.k.json.tmp-', dir=state)
        with os.fdopen(fd, 'w') as fh:
            json.dump(data, fh)
        os.replace(tmp, os.path.join(state, 'k.json'))
    except OSError:
        pass


def estimate(home=None, state=None, now=None, line=None, warn=None, session=None, scanner=None):
    """Load everything, compute, persist k. May raise; callers that must not raise use safe_estimate()."""
    import time
    home = home or home_dir()
    state = state or state_dir(home)
    now = time.time() if now is None else now
    a5, a7 = load_log(home)
    b5, b7 = load_legacy(home, now)
    s5, s7 = a5 + b5, a7 + b7
    r5, rolled = _window(s5, now)
    ws = (max(r5, now - WINDOW) if rolled else r5 - WINDOW) if r5 else now
    since = min(ws, now - IDLE_MAX) - 60
    reqs, keys = scanner.scan(home, since) if scanner else scan(home, since)
    k_hist = load_k(state)
    if session is None:
        session = os.environ.get('CLAUDE_CODE_SESSION_ID') or None
    e = compute(s5, s7, reqs, keys, now, k_hist, line, warn, session)
    if e.persist:
        save_k(state, k_hist, e.persist)
    return e


def safe_estimate(**kw):
    try:
        return estimate(**kw)
    except Exception as exc:  # never raises: a crash must not read as WARN/STALE
        return unknown(type(exc).__name__)


# -- wait: pidfile, check-ins, refresh, the loop ----------------------------------------------------

DEGRADED_SEC = 600     # UNKNOWN (or k_src=none with a stale anchor) this long mid-wait -> exit 4
PAUSE_CAP = 5 * 3600 + 600
CHECKIN_LADDER = (30, 60, 120, 120)   # minutes; at most len() CHECKINs per open spawn
REFRESH_EVERY = 600
REFRESH_VERDICT = 120
REFRESH_OFF = 86400
REFRESH_WARM = 3000
EXIT = {'ARMED': 0, 'STOP': 2, 'UNKNOWN': 4, 'RESET': 10, 'CHECKIN': 11, 'REFRESH': 12, 'ENDED': 13}


def wait_key(repo=None, slug=None, sid=None):
    return '%s__%s' % (repo, slug) if repo else 'session-%s' % (sid or '-')


def pidfile_path(state, key):
    return os.path.join(state, 'wait-%s.pid' % key)


def read_pidfile(path):
    """-> (pid, owner, sid, launch) or None; owner/sid '-' when unknown (also for a bare-pid file)."""
    try:
        with open(path, encoding='utf-8') as fh:
            parts = fh.read().split()
        pid = int(parts[0])
    except (OSError, ValueError, IndexError):
        return None
    parts += ['-'] * (4 - len(parts))
    return pid, parts[1], parts[2], parts[3]


def is_wait_proc(pid):
    """pid is alive and its command line is a `usage wait`."""
    if not pid_alive(pid):
        return False
    try:
        cmd = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return 'usage' in cmd and 'wait' in cmd


def wait_armed(state, key):
    """pid of a live `usage wait` holding STATE/wait-<key>.pid, else None (owner not checked)."""
    rec = read_pidfile(pidfile_path(state, key))
    return rec[0] if rec and is_wait_proc(rec[0]) else None


def _write_atomic(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.tmp-', dir=d)
    try:
        with os.fdopen(fd, 'w') as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path):
    try:
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _append_jsonl(path, rec):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, sort_keys=True) + '\n')
    except OSError:
        pass


def _min_ts(ts):
    """A minute-resolution task-file timestamp -> epoch at the END of that minute (so check-ins are never early)."""
    from . import flow
    t = flow.ts_epoch(ts)
    return None if t is None else t + 59


def task_state(task_dir):
    """task_state of status.md, or None when missing/unreadable."""
    if not task_dir:
        return None
    try:
        from . import status as st
        v = st.Status.load(os.path.join(task_dir, 'status.md')).get('task_state')
        return v.split()[0] if v else None
    except Exception:
        return None


LOG_RE = None


def _log_activity(task_dir):
    """{role: epoch of its latest log.md line}."""
    import re
    global LOG_RE
    LOG_RE = LOG_RE or re.compile(r'^- (\d{4}-\d\d-\d\dT\d\d:\d\dZ) ([^:\s]+):')
    out = {}
    try:
        with open(os.path.join(task_dir, 'log.md'), encoding='utf-8', errors='replace') as fh:
            for line in fh:
                m = LOG_RE.match(line)
                if m:
                    t = _min_ts(m.group(1))
                    if t is not None and t > out.get(m.group(2), 0):
                        out[m.group(2)] = t
    except OSError:
        pass
    return out


def due_checkin(task_dir, now, stamps):
    """-> (role, silent_min, interval_min, spawn_id) for the earliest overdue open spawn, else None.
    A row is due only if `in_progress` AND its role's latest journal `spawn` comes after its latest `return`.
    Activity = spawn, the role's log.md lines, its row timestamp, journal `checkin`s since the spawn;
    the ladder base also counts this key's CHECKIN stamp, so a re-armed wait can't re-fire at once."""
    from . import flow
    from . import status as st
    try:
        rows = [(r, row) for r, row in st.Status.load(os.path.join(task_dir, 'status.md')).roles()
                if row.get('state') == 'in_progress']
    except Exception:
        return None
    journal = flow.read_journal(task_dir)
    if not rows or not journal:
        return None
    logs = _log_activity(task_dir)
    best = None
    for role, row in rows:
        i_s = i_r = None
        for i, rec in enumerate(journal):
            if rec.get('role') != role:
                continue
            k = flow.kind_of(rec)
            if k == 'spawn':
                i_s = i
            elif k == 'return':
                i_r = i
        if i_s is None or (i_r is not None and i_r > i_s):
            continue
        t_spawn = _min_ts(journal[i_s].get('ts'))
        if t_spawn is None:
            continue
        spawn_id = '%s#%d' % (journal[i_s].get('ts'), i_s)
        jci = [_min_ts(r.get('ts')) for r in journal[i_s + 1:] if flow.kind_of(r) == 'checkin' and r.get('role') == role]
        jci = [t for t in jci if t is not None]
        stamp = stamps.get(role) if isinstance(stamps.get(role), dict) else {}
        if stamp.get('spawn') != spawn_id:
            stamp = {}
        n = max(len(jci), int(stamp.get('n') or 0))
        if n >= len(CHECKIN_LADDER):
            continue
        acts = [t_spawn] + jci
        if logs.get(role, 0) >= t_spawn:
            acts.append(logs[role])
        t_row = _min_ts(row.get('ts', ''))
        if t_row is not None and t_row >= t_spawn:
            acts.append(t_row)
        last_act = max(acts)
        base = max([last_act] + ([float(stamp['last'])] if isinstance(stamp.get('last'), (int, float)) else []))
        interval = CHECKIN_LADDER[n] * 60
        if now >= base + interval and (best is None or base + interval < best[0]):
            best = (base + interval, role, int((now - last_act) // 60), CHECKIN_LADDER[n], spawn_id, n + 1)
    return best[1:] if best else None


class Wait:
    """One `usage wait`. Every collaborator is injectable (tests drive it with a fake clock and estimates)."""

    def __init__(self, state, key, est_fn, task_dir=None, owner=None, sid=None, interval=30.0, max_sec=21600.0,
                 until_reset=False, refresh=False, clock=None, sleep=None, pid=None, alive=None, is_wait=None):
        import time
        self.state, self.key, self.est_fn, self.task_dir = state, key, est_fn, task_dir
        self.owner, self.sid = owner, sid
        self.interval, self.max_sec, self.until_reset, self.refresh = interval, max_sec, until_reset, refresh
        self.clock = clock or time.time
        self.sleep = sleep or time.sleep
        self.pid = os.getpid() if pid is None else pid
        self.alive = alive or pid_alive
        self.is_wait = is_wait or is_wait_proc
        self.pidfile = pidfile_path(state, key)
        self.held = False

    # -- output
    @staticmethod
    def line(event, e=None, **detail):
        parts = [event] + ['%s=%s' % kv for kv in detail.items()]
        if e is not None:
            parts += [t for t in e.text().split(' ') if not ('reason' in detail and t.startswith('reason='))]
        return ' '.join(parts)

    # -- pidfile
    def _armed_other(self):
        rec = read_pidfile(self.pidfile)
        if not rec or rec[0] == self.pid or not self.is_wait(rec[0]):
            return None
        pid, owner, sid = rec[0], rec[1], rec[2]
        same_owner = owner != '-' and self.owner is not None and owner == str(self.owner)
        same_sid = sid != '-' and self.sid is not None and sid == self.sid
        return pid if (same_owner or same_sid) else None

    def _still_ours(self):
        rec = read_pidfile(self.pidfile)
        return rec is not None and rec[0] == self.pid

    def release(self):
        if self.held and self._still_ours():
            try:
                os.unlink(self.pidfile)
            except OSError:
                pass
        self.held = False

    # -- check-in / refresh stamps
    def _checkin(self, now):
        path = os.path.join(self.state, 'checkin-%s.json' % self.key)
        stamps = _load_json(path)
        c = due_checkin(self.task_dir, now, stamps)
        if not c:
            return None
        role, silent, interval, spawn_id, n = c
        stamps[role] = {'spawn': spawn_id, 'n': n, 'last': now}
        _write_atomic(path, json.dumps(stamps, sort_keys=True))
        return role, silent, interval

    def _refresh(self, e, now):
        if not self.refresh or not self.sid:
            return False
        path = os.path.join(self.state, 'refresh-%s.json' % self.sid)
        logp = os.path.join(self.state, 'refresh-log.jsonl')
        s = _load_json(path)
        at = s.get('at') if isinstance(s.get('at'), (int, float)) else None
        if at is not None and s.get('verdict') is None and now - at >= REFRESH_VERDICT:
            fresh = (getattr(e, 't_sample', None) or 0) > at
            s.update(verdict='effective' if fresh else 'ineffective', verdict_at=now)
            _write_atomic(path, json.dumps(s, sort_keys=True))
            _append_jsonl(logp, {'ts': at, 'sid': self.sid, 'est': s.get('est'), 'line': s.get('line'),
                                 'fresh_sample_within_120s': fresh})
        if s.get('verdict') == 'ineffective' and now - (s.get('verdict_at') or 0) < REFRESH_OFF:
            return False
        if at is not None and now - at < REFRESH_EVERY:
            return False
        est, line, ext = getattr(e, 'est', None), getattr(e, 'line', None), getattr(e, 'ext', None)
        own = getattr(e, 'own_last', None)
        if est is None or line is None or est < line - STALE_NEAR or (e.age5 or 0) < 120 or (ext or 0) < 1 \
                or own is None or now - own >= REFRESH_WARM:
            return False
        _write_atomic(path, json.dumps({'at': now, 'est': round(est, 1), 'line': round(line, 1)}, sort_keys=True))
        _append_jsonl(logp, {'ts': now, 'sid': self.sid, 'est': round(est, 1), 'line': round(line, 1),
                             'fresh_sample_within_120s': None})
        return True

    # -- the loop
    def run(self):
        """-> (code, line). Removes its own pidfile on every exit path except superseded (not its file then)."""
        try:
            return self._run()
        finally:
            self.release()

    def _run(self):
        if self.owner is not None and not self.alive(self.owner):
            return 4, self.line('UNKNOWN', unknown('parent-dead'))
        other = self._armed_other()
        if other:
            return 0, 'ARMED pid=%d' % other
        _write_atomic(self.pidfile, '%d %s %s %d\n' % (self.pid, self.owner if self.owner is not None else '-',
                                                       self.sid or '-', int(self.clock())))
        self.held = True
        start = self.clock()
        e = self.est_fn()
        if e.state == 'UNKNOWN':
            return 4, self.line('UNKNOWN', e)
        paused = self.until_reset or task_state(self.task_dir) == 'paused_usage_limit'
        if not paused and e.state == 'STOP':
            return 2, self.line('STOP', e)
        r5_0, p_0, t_pause = getattr(e, 'window', None), e.p5, start
        degraded = None
        first = True
        while True:
            now = self.clock()
            if self.owner is not None and not self.alive(self.owner):
                return 13, self.line('ENDED', e, reason='parent-dead')
            if not self._still_ours():
                self.held = False
                return 13, self.line('ENDED', e, reason='superseded')
            if now - start >= self.max_sec:
                return 13, self.line('ENDED', e, reason='max')
            ts = task_state(self.task_dir)
            if ts in ('done', 'abandoned', 'merged'):
                return 13, self.line('ENDED', e, reason='task-' + ts)
            if not first:
                e = self.est_fn()
            first = False
            if not paused and ts == 'paused_usage_limit':
                paused, t_pause = True, now
                if e.state != 'UNKNOWN':
                    r5_0, p_0 = getattr(e, 'window', None), e.p5
            if paused:
                w = getattr(e, 'window', None)
                if r5_0 is not None and now >= r5_0 + 120:
                    return 10, self.line('RESET', e, reason='clock')
                if r5_0 is not None and w is not None and not getattr(e, 'rolled', False) and w > r5_0 + GROUP:
                    return 10, self.line('RESET', e, reason='new-window')
                if r5_0 is None and p_0 is not None and e.p5 is not None and e.p5 < p_0 - 5:
                    return 10, self.line('RESET', e, reason='hw-drop')
                if now - t_pause >= PAUSE_CAP:
                    return 10, self.line('RESET', e, reason='cap')
            elif e.state == 'STOP':
                return 2, self.line('STOP', e)
            bad = e.state == 'UNKNOWN' or (getattr(e, 'k_src', None) == 'none' and (e.age5 or 0) > DEGRADED_SEC)
            if bad and not (paused and r5_0 is not None):
                degraded = now if degraded is None else degraded
                if now - degraded >= DEGRADED_SEC:
                    return 4, self.line('UNKNOWN', e, reason='degraded')
            else:
                degraded = None
            if not paused:
                if self.task_dir:
                    c = self._checkin(now)
                    if c:
                        return 11, self.line('CHECKIN', e, role=c[0], silent_min=c[1], interval=c[2])
                if self._refresh(e, now):
                    return 12, self.line('REFRESH', e)
            self.sleep(self.interval)


# -- CLI -----------------------------------------------------------------------------------------

def _bound(v, name):
    x = float(v)
    if not 1 <= x <= 100:
        raise ValueError('%s must be 1-100' % name)
    return x


def _signal_exit(signum, frame):
    raise SystemExit(128 + signum)   # unwinds through Wait.run's finally: the pidfile goes


def cmd_wait(ns):
    """Exit code only from the wait table (0/2/4/10/11/12/13); every error in here -> 4. Signals -> 128+N."""
    import signal
    w = None
    try:
        line = _bound(ns.line, '--line') if ns.line is not None else None
        interval = float(ns.interval)
        max_sec = float(ns.max)
        if not (interval > 0 and max_sec > 0):
            raise ValueError('--interval and --max must be > 0')
        owner = ns.parent_pid if ns.parent_pid is not None else os.environ.get('CLAUDE_PID')
        owner = int(owner) if owner not in (None, '') else None
        sid = ns.session or os.environ.get('CLAUDE_CODE_SESSION_ID') or None
        task = getattr(ns, 'task_sub', None) or getattr(ns, 'task', None)
        task_dir, key = None, wait_key(sid=sid)
        if task:
            from . import cli
            from .paths import task_names
            try:
                task_dir = cli.get_task(ns)
            except Exception:
                print(Wait.line('UNKNOWN', unknown('task-unresolved')))
                return 4
            key = wait_key(*task_names(task_dir))
        home = home_dir()
        state = state_dir(home)
        sc = Scanner()
        w = Wait(state, key, lambda: safe_estimate(home=home, state=state, line=line, session=sid, scanner=sc),
                 task_dir=task_dir, owner=owner, sid=sid, interval=interval, max_sec=max_sec,
                 until_reset=ns.until_reset, refresh=ns.refresh)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, _signal_exit)
        code, text = w.run()
    except Exception as exc:
        if w is not None:
            w.release()
        code, text = 4, Wait.line('UNKNOWN', unknown(type(exc).__name__))
    print(text)
    sys.stdout.flush()
    return code


def cmd_usage(ns, extra):
    """Exit code only from OK/WARN/STOP/STALE/UNKNOWN; every error -> UNKNOWN (4)."""
    if ns.mode == 'wait':
        return cmd_wait(ns)
    try:
        line = _bound(ns.line, '--line') if ns.line is not None else None
        warn = _bound(ns.warn, '--warn') if ns.warn is not None else None
        e = safe_estimate(line=line, warn=warn, session=ns.session)
    except Exception as exc:
        e = unknown(type(exc).__name__)
    wait = None
    task = getattr(ns, 'task_sub', None) or getattr(ns, 'task', None)
    if task:
        wait = 'none'
        try:
            from . import cli
            from .paths import task_names
            repo, slug = task_names(cli.get_task(ns))
            if wait_armed(state_dir(), wait_key(repo, slug)):
                wait = 'armed'
        except Exception:
            pass
    if ns.json:
        print(json.dumps(e.as_dict(wait), sort_keys=False))
    else:
        print(e.text(wait))
    return e.code


def register(add):
    sp = add('usage', cmd_usage, '5h usage estimate and dynamic stop line (exit 0 OK 1 WARN 2 STOP 3 STALE 4 UNKNOWN);'
             ' `usage wait`: silent until an event (0 ARMED 2 STOP 4 UNKNOWN 10 RESET 11 CHECKIN 12 REFRESH 13 ENDED)')
    sp.add_argument('mode', nargs='?', default='check', choices=['check', 'wait'])
    sp.add_argument('--interval', default='30', help='wait: seconds between polls')
    sp.add_argument('--max', default='21600', help='wait: give up (ENDED) after this many seconds')
    sp.add_argument('--until-reset', action='store_true', help='wait: paused mode, exit only on RESET')
    sp.add_argument('--refresh', action='store_true', help='wait: allow the REFRESH wake (off by default)')
    sp.add_argument('--parent-pid', default=None, help='wait: end when this pid dies (default $CLAUDE_PID)')
    sp.add_argument('--json', action='store_true')
    sp.add_argument('--line', default=None, help='fixed STOP threshold 1-100 (not clamped)')
    sp.add_argument('--warn', default=None, help='fixed WARN threshold 1-100')
    sp.add_argument('--session', default=None, help='this session id (default $CLAUDE_CODE_SESSION_ID)')
