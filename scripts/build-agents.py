#!/usr/bin/env python3
"""Render agent files from role sources plus shared blocks.

Agent files are subagent system prompts, and Claude Code does not expand imports in them
(probed 2026-10-15), so shared rules are inlined at build time. A source line of the exact form
`<!-- @include _shared/<name>.md -->` is replaced by that file's contents.

A role's frontmatter may carry `variants: <suffix>=<model-id>/<effort>[, ...]`. The renderer then writes
`<role>.md` without that line, plus one `<role>-<suffix>.md` per variant: same body, `name:` suffixed,
`model:`/`effort:` replaced, `description:` prefixed to say it is the variant. Roles without `variants:`
render exactly as before.

Usage: build-agents.py <roles-dir> <agents-dir> [--check]
  --check: write nothing. Exit 1 if any rendered file differs from the one in agents-dir,
           if an agent's `name:` differs from its file stem, or if agents-dir holds a file no role renders.
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

VARIANTS_RX = re.compile(r'^variants:[ \t]*(.*?)[ \t]*\n', re.M)
SUFFIX_RX = re.compile(r'^[a-z][a-z0-9]*$')
MODEL_RX = re.compile(r'^[\w.-]+$')

def frontmatter_end(text):
    """Index of the newline that starts the closing `---`, or -1."""
    return text.find('\n---', 4) if text.startswith('---\n') else -1

def split_variants(role, text):
    """(base text without its `variants:` line, [(suffix, model, effort)]). A bad value exits naming the role."""
    end = frontmatter_end(text)
    if end < 0:
        return text, []
    fm = text[:end + 1]
    m = VARIANTS_RX.search(fm)
    if not m:
        return text, []
    found = []
    for part in m.group(1).split(','):
        part = part.strip()
        sm, _, rest = part.partition('=')
        model, _, effort = rest.rpartition('/')
        if not (SUFFIX_RX.match(sm) and MODEL_RX.match(model) and MODEL_RX.match(effort)):
            sys.exit(f"{role}: malformed variants: {m.group(1)!r} (want <suffix>=<model-id>/<effort>[, ...]; bad part {part!r})")
        found.append((sm, model, effort))
    if len({f[0] for f in found}) != len(found):
        sys.exit(f"{role}: malformed variants: {m.group(1)!r} (duplicate suffix)")
    return fm[:m.start()] + fm[m.end():] + text[end + 1:], found

def make_variant(role, base, suffix, model, effort):
    end = frontmatter_end(base)
    fm, body = base[:end + 1], base[end + 1:]
    label = suffix.capitalize()
    prefix = (f"{label}-model variant of the {role.replace('-', ' ')}, spawned only after the user accepts a "
              f"{label} review offer (orchestrate SKILL.md step 5). ")
    def sub(key, fn):
        nonlocal fm
        fm, n = re.subn(rf'^{key}:[ \t]*(.*?)[ \t]*$', lambda mm: f"{key}: {fn(mm.group(1))}", fm, count=1, flags=re.M)
        if n != 1:
            sys.exit(f"{role}: variants need a `{key}:` line in the frontmatter")
    sub('name', lambda v: f"{v}-{suffix}")
    sub('description', lambda v: prefix + v)
    sub('model', lambda v: model)
    sub('effort', lambda v: effort)
    return fm + body

def outputs(roles):
    """[(file name, rendered text)] for every role and variant, in a stable order."""
    srcs = sorted(glob.glob(os.path.join(roles, '*.md')))
    if not srcs:
        sys.exit(f"no *.md sources in {roles}")
    outs = []
    for s in srcs:
        fname = os.path.basename(s)
        role = os.path.splitext(fname)[0]
        base, variants = split_variants(role, render(s, roles))
        outs.append((fname, base))
        for suffix, model, effort in variants:
            outs.append((f"{role}-{suffix}.md", make_variant(role, base, suffix, model, effort)))
    return outs

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
    outs = outputs(roles)
    differ = 0
    for fname, text in outs:
        dst = os.path.join(agents, fname)
        same = os.path.isfile(dst) and open(dst, encoding='utf-8').read() == text
        if check:
            if not same:
                print(f"DIFFERS {fname}"); differ += 1
            stem = os.path.splitext(fname)[0]
            nm = name_of(text)
            if nm != stem:
                print(f"NAME {fname}: name is {nm!r}, file stem is {stem!r}"); differ += 1
        elif not same:
            open(dst, 'w', encoding='utf-8').write(text); print(f"wrote {fname}")
    expected = {f for f, _ in outs}
    for f in sorted(glob.glob(os.path.join(agents, '*.md'))):
        if os.path.basename(f) not in expected:
            print(f"ORPHAN {os.path.basename(f)}: no role or variant renders it")
            if check:
                differ += 1
    print(f"{len(outs)} agent files, {differ if check else 0} differing")
    return 1 if differ else 0

if __name__ == '__main__':
    sys.exit(main())
