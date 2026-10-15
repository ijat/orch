#!/usr/bin/env python3
"""role-cost.py — transcript-based cost per orchestration role.

Reads Claude Code transcripts (default ~/.claude/projects). Each subagent run is
<session>/subagents/agent-<id>.jsonl beside agent-<id>.meta.json, which names its agentType.
API requests are de-duplicated by requestId (Claude Code logs one entry per content block).

Grouped by role and tier (default; --by type for the raw agent-type view). The tier is read from
the transcript itself: the run's most common model id and `effort` field, so it is right for both
the old per-tier agent files and the post-2026-10-08 model/effort spawn parameters. Old agent names
(orch-planner-max, orch-implementer-expert, ...) fold into their role. Effort shows `?` where none
is recorded (all Haiku runs so far).

Per group: runs, median requests per run, median peak context, median prompt volume per run
(sum over requests of input + cache writes + cache reads: what each request re-reads), and total
API-equivalent $ at list prices. Then, over sessions that spawned any orch-* agent: median
implementer prompt volume and median orch-* $ per session.

The journal's `return.tokens` is a run's final context size, not this cumulative volume.

usage: role-cost.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--root DIR] [--by tier|type]
Runs are dated by their first request. Exits 1 when no run matches: silence is not a pass.
"""
import argparse, glob, json, os, re, statistics, sys
from collections import Counter, defaultdict

# List prices, $/MTok: (input, output, cache-read multiplier). Cache writes: 1.25x (5 min), 2x (1 h).
# Source: Claude API reference (cached 2026-10-06). Update when prices change — audit step 5a.
# Haiku 5.5's cache-read price isn't in the reference: 0.1x is assumed, as for the other models.
PRICES = {
    'claude-fable-5-1': (10, 50, 0.025), 'claude-fable-5': (10, 50, 0.1),
    'claude-opus-5-5': (4, 20, 0.05), 'claude-opus-5': (5, 25, 0.1),
    'claude-opus-4-8': (5, 25, 0.1), 'claude-opus-4-7': (5, 25, 0.1),
    'claude-sonnet-5-5': (2, 10, 0.1), 'claude-sonnet-5': (2, 10, 0.1),
    'claude-haiku-5-5': (0.10, 0.50, 0.1), 'claude-sonnet-4-6': (3, 15, 0.1),
    'claude-haiku-4-5-20251001': (1, 5, 0.1), 'claude-haiku-4-5': (1, 5, 0.1),
}


def request_cost(usage, model):
    if model not in PRICES:
        return None
    pi, po, rm = PRICES[model]
    cc = usage.get('cache_creation') or {}
    w5 = cc.get('ephemeral_5m_input_tokens', 0) or 0
    w1 = cc.get('ephemeral_1h_input_tokens', 0) or 0
    if not cc:
        w5 = usage.get('cache_creation_input_tokens', 0) or 0
    return ((usage.get('input_tokens', 0) or 0) * pi + (w5 * 1.25 + w1 * 2) * pi
            + (usage.get('cache_read_input_tokens', 0) or 0) * pi * rm
            + (usage.get('output_tokens', 0) or 0) * po) / 1e6


def role_of(typ):
    m = re.match(r'(orch-planner|orch-implementer)\b', typ)
    return m.group(1) if m else typ


def tier_of(models, efforts):
    model = models.most_common(1)[0][0] if models else '?'
    model = re.sub(r'^claude-', '', model)
    effort = efforts.most_common(1)[0][0] if efforts else '?'
    return f"{model}/{effort}"


def read_run(path, stats):
    seen, reqs, first = set(), [], None
    models, efforts = Counter(), Counter()
    with open(path, errors='replace') as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                stats['bad_lines'] += 1
                continue
            if e.get('type') != 'assistant':
                continue
            m = e.get('message') or {}
            rid = e.get('requestId') or m.get('id')
            u = m.get('usage')
            if not rid or rid in seen or not u or m.get('model') == '<synthetic>':
                continue
            seen.add(rid)
            models[m.get('model')] += 1
            if e.get('effort'):
                efforts[e['effort']] += 1
            first = first or (e.get('timestamp') or '')[:10]
            prompt = sum((u.get(k, 0) or 0) for k in ('input_tokens', 'cache_creation_input_tokens',
                                                        'cache_read_input_tokens'))
            cost = request_cost(u, m.get('model'))
            if cost is None:
                stats['unpriced_requests'] += 1
            reqs.append((prompt, cost or 0.0))
    return first, reqs, tier_of(models, efforts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--since'); ap.add_argument('--until')
    ap.add_argument('--root', default=os.path.expanduser('~/.claude/projects'))
    ap.add_argument('--by', choices=('tier', 'type'), default='tier')
    a = ap.parse_args()
    stats = defaultdict(int)
    by_type = defaultdict(list)                      # type -> [(requests, peak, volume, usd)]
    by_session = defaultdict(lambda: defaultdict(float))
    for path in glob.glob(os.path.join(a.root, '**', 'subagents', 'agent-*.jsonl'), recursive=True):
        try:
            with open(path[:-len('.jsonl')] + '.meta.json') as fh:
                typ = json.load(fh).get('agentType') or 'unknown'
        except (OSError, ValueError):
            typ = 'unknown(no meta)'
        first, reqs, tier = read_run(path, stats)
        if not reqs or (a.since and first < a.since) or (a.until and first > a.until):
            continue
        vol = sum(p for p, _ in reqs); usd = sum(c for _, c in reqs)
        key = typ if a.by == 'type' else f"{role_of(typ)} {tier}"
        by_type[key].append((len(reqs), max(p for p, _ in reqs), vol, usd))
        if typ.startswith('orch-'):
            s = by_session[os.path.dirname(os.path.dirname(path))]
            s['orch_usd'] += usd
            if typ.startswith('orch-implementer'):
                s['impl_volume'] += vol
    if stats['bad_lines']:
        print(f"note: skipped {stats['bad_lines']} unparseable line(s)", file=sys.stderr)
    if stats['unpriced_requests']:
        print(f"note: {stats['unpriced_requests']} request(s) on unpriced models counted as $0", file=sys.stderr)
    if not by_type:
        print(f"no subagent runs found under {a.root} (since={a.since}, until={a.until}) - check --root and dates")
        return 1
    med = statistics.median
    print(f"{('agent type' if a.by == 'type' else 'role  model/effort'):40}{'runs':>6}{'med req':>9}{'med peak':>10}{'med volume':>12}{'total $':>10}")
    for typ, runs in sorted(by_type.items(), key=lambda kv: -sum(r[3] for r in kv[1])):
        print(f"{typ[:40]:40}{len(runs):>6}{med(r[0] for r in runs):>9.0f}{med(r[1] for r in runs):>10,.0f}"
              f"{med(r[2] for r in runs):>12,.0f}{sum(r[3] for r in runs):>10.2f}")
    if by_session:
        vols = [s['impl_volume'] for s in by_session.values()]
        print(f"\norchestration sessions: {len(by_session)}  median implementer volume/session: {med(vols):,.0f}"
              f"  median orch-* $/session: {med(s['orch_usd'] for s in by_session.values()):.2f}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
