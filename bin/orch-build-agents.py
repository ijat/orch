#!/usr/bin/env python3
"""Render the orch-* agent files, plus explorer and executor, from role sources plus shared blocks.

Agent files are subagent system prompts, and Claude Code does not expand imports in them
(probed 2026-10-15), so shared rules are inlined at build time. A source line of the exact form
`<!-- @include _shared/<name>.md -->` is replaced by that file's contents.

Usage: orch-build-agents.py [<roles-dir> <agents-dir>] [--check]
  defaults: ~/.claude/skills/orchestrate/roles  ~/.claude/agents
  --check: write nothing. Exit 1 if any rendered file differs from the one in agents-dir.
"""
import glob, os, re, sys

RX = re.compile(r'^<!-- @include (_shared/[\w.-]+\.md) -->\n$')

def render(path, roles):
    out = []
    for line in open(path, encoding='utf-8'):
        m = RX.match(line)
        if m:
            inc = os.path.join(roles, m.group(1))
            if not os.path.isfile(inc):
                sys.exit(f"{os.path.basename(path)}: missing include {m.group(1)}")
            out.append(open(inc, encoding='utf-8').read())
        else:
            out.append(line)
    return ''.join(out)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    check = '--check' in sys.argv
    if len(args) not in (0, 2):
        sys.exit(__doc__)
    home = os.path.expanduser('~/.claude')
    roles, agents = args or (f'{home}/skills/orchestrate/roles', f'{home}/agents')
    srcs = sorted(glob.glob(os.path.join(roles, 'orch-*.md'))
                  + [p for n in ('explorer', 'executor') if os.path.isfile(p := os.path.join(roles, f'{n}.md'))])
    if not srcs:
        sys.exit(f"no role sources in {roles}")
    differ = 0
    for s in srcs:
        text = render(s, roles)
        dst = os.path.join(agents, os.path.basename(s))
        same = os.path.isfile(dst) and open(dst, encoding='utf-8').read() == text
        if check:
            if not same:
                print(f"DIFFERS {os.path.basename(s)}"); differ += 1
        elif not same:
            open(dst, 'w', encoding='utf-8').write(text); print(f"wrote {os.path.basename(s)}")
    print(f"{len(srcs)} roles, {differ if check else 0} differing")
    return 1 if differ else 0

if __name__ == '__main__':
    sys.exit(main())
