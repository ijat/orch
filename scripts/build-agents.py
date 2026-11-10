#!/usr/bin/env python3
"""Render agent files from role sources plus shared blocks.

Agent files are subagent system prompts, and Claude Code does not expand imports in them
(probed 2026-10-15), so shared rules are inlined at build time. A source line of the exact form
`<!-- @include _shared/<name>.md -->` is replaced by that file's contents.

Usage: build-agents.py <roles-dir> <agents-dir> [--check]
  --check: write nothing. Exit 1 if any rendered file differs from the one in agents-dir,
           or if an agent's `name:` differs from its file stem.
  No arguments: print this usage and exit 2 (never writes anywhere).
"""
import glob, os, re, sys

RX = re.compile(r'^<!-- @include (_shared/[\w.-]+\.md) -->\n$')
NAME_RX = re.compile(r'^name:[ \t]*(.+?)[ \t]*$', re.M)

def name_of(text):
    """The `name:` of the frontmatter (the block between the first two --- lines), or None."""
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---', 4)
    m = NAME_RX.search(text[4:end if end > 0 else len(text)])
    return m.group(1).strip('"\'') if m else None

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
    unknown = [a for a in sys.argv[1:] if a.startswith('-') and a != '--check']
    if unknown:
        sys.exit(__doc__)  # an unknown flag (e.g. --help) must never fall through to a write
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    check = '--check' in sys.argv
    if len(args) != 2:
        sys.stderr.write(__doc__)
        sys.exit(2)
    roles, agents = args
    srcs = sorted(glob.glob(os.path.join(roles, '*.md')))
    if not srcs:
        sys.exit(f"no *.md sources in {roles}")
    differ = 0
    for s in srcs:
        text = render(s, roles)
        dst = os.path.join(agents, os.path.basename(s))
        same = os.path.isfile(dst) and open(dst, encoding='utf-8').read() == text
        if check:
            if not same:
                print(f"DIFFERS {os.path.basename(s)}"); differ += 1
            stem = os.path.splitext(os.path.basename(s))[0]
            nm = name_of(text)
            if nm != stem:
                print(f"NAME {os.path.basename(s)}: name is {nm!r}, file stem is {stem!r}"); differ += 1
        elif not same:
            open(dst, 'w', encoding='utf-8').write(text); print(f"wrote {os.path.basename(s)}")
    print(f"{len(srcs)} roles, {differ if check else 0} differing")
    return 1 if differ else 0

if __name__ == '__main__':
    sys.exit(main())
