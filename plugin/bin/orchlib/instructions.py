"""install-instructions: keep orch's marked block in the user's global CLAUDE.md."""
import datetime
import difflib
import os
import re
import shutil

from . import settings
from .paths import OrchError, agent_key, require_write_allowed, tilde, write_atomic

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "skills", "orchestrate", "templates", "claude-md-block.md")
BEGIN = "<!-- orch:begin"
END = "<!-- orch:end"
ENDIF = "<!-- orch:endif"
IF_RE = re.compile(r"^<!-- orch:if (\w+) -->$")
ENDIF_RE = re.compile(r"^<!-- orch:endif -->$")
HEADING_RE = re.compile(r"^(#{1,6})\s")
LEGACY_HEADING_RE = re.compile(r"^#{1,6}\s+Orchestration mode\b")
BULLET_RE = re.compile(r"^\s*[-*]\s")
AGENT_WORD_RE = re.compile(r"orch:[a-z][a-z-]*")


def agent_text(line):
    """The line with every agent name in today's spelling (agent_key on each `orch:<role>` word)."""
    return AGENT_WORD_RE.sub(lambda m: agent_key(m.group(0)), line)


def render_block(template_lines, overrides=None):
    """The template's lines for the settings as they are now: lines between `<!-- orch:if NAME -->` and
    `<!-- orch:endif -->` stay when the setting NAME is `on` and go when it is not; the marker lines always go.
    `overrides` ({name: value}) stands in for stored settings, to preview a switch before it is flipped."""
    out, keep, open_at = [], True, None
    for n, line in enumerate(template_lines, 1):
        m = IF_RE.match(line)
        if m:
            if open_at is not None:
                raise OrchError("the block template nests orch:if (line %d inside the one at line %d)" % (n, open_at), 2)
            if m.group(1) not in settings.SETTINGS:
                raise OrchError("the block template names an unknown setting %r (line %d)" % (m.group(1), n), 2)
            value = (overrides or {}).get(m.group(1)) or settings.get(m.group(1))
            open_at, keep = n, value == "on"
        elif ENDIF_RE.match(line):
            if open_at is None:
                raise OrchError("the block template has orch:endif without orch:if (line %d)" % n, 2)
            open_at, keep = None, True
        elif keep:
            out.append(line)
    if open_at is not None:
        raise OrchError("the block template has orch:if (line %d) without orch:endif" % open_at, 2)
    return out


def block_lines(overrides=None):
    """The managed block as it is written today (the template rendered for the current settings)."""
    with open(TEMPLATE, encoding="utf-8") as fh:
        return render_block(fh.read().rstrip("\n").split("\n"), overrides)


def default_file():
    return os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md")


def block_span(lines):
    """(start, end) line indexes of the marked block, end exclusive; None when there is none."""
    begins = [i for i, l in enumerate(lines) if l.startswith(BEGIN)]
    ends = [i for i, l in enumerate(lines) if l.startswith(END) and not l.startswith(ENDIF)]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise OrchError("the orch markers are damaged (%d begin, %d end): fix them by hand, then re-run"
                        % (len(begins), len(ends)), 2)
    return begins[0], ends[0] + 1


def section_end(lines, start):
    level = len(HEADING_RE.match(lines[start]).group(1))
    for i in range(start + 1, len(lines)):
        m = HEADING_RE.match(lines[i])
        if m and len(m.group(1)) <= level:
            return i
    return len(lines)


def bullet_end(lines, start):
    i = start + 1
    while i < len(lines) and lines[i].strip() and lines[i][0] in " \t" and not BULLET_RE.match(lines[i]):
        i += 1
    return i


def legacy_spans(lines, skip):
    """Hand-written orch rules outside the block: the section, and bullets the block now carries."""
    spans = []
    i = 0
    while i < len(lines):
        if skip and skip[0] <= i < skip[1]:
            i = skip[1]
            continue
        line = lines[i]
        if LEGACY_HEADING_RE.match(line):
            spans.append((i, section_end(lines, i)))
        elif BULLET_RE.match(line) and ("orchestrations/ACTIVE.md" in line or ("orch-explorer" in agent_text(line) and "orch-executor" in agent_text(line))):
            spans.append((i, bullet_end(lines, i)))
        i = spans[-1][1] if spans and spans[-1][0] == i else i + 1
    return spans


def rebuilt(lines, cut, insert_at, block):
    """lines without the cut spans, block at insert_at (an index into lines; None appends), one blank line around it."""
    gone = set()
    for start, end in cut:
        gone.update(range(start, end))
    before = [l for i, l in enumerate(lines) if i not in gone and (insert_at is None or i < insert_at)]
    after = [] if insert_at is None else [l for i, l in enumerate(lines) if i not in gone and i >= insert_at]
    while before and not before[-1].strip():
        before.pop()
    while after and not after[0].strip():
        after.pop(0)
    out = before
    if block:
        out = out + ([""] if out else []) + block
    if after:
        out = out + ([""] if out else []) + after
    return out


def plan_install(lines, block, replace_existing):
    span = block_span(lines)
    legacy = legacy_spans(lines, span)
    if legacy and not replace_existing:
        found = ", ".join("line %d" % (s + 1) for s, _ in legacy)
        raise OrchError("hand-written orch rules found (%s); installing beside them would duplicate rules. "
                        "Review with --dry-run --replace-existing, then re-run with --replace-existing" % found, 2)
    cut = list(legacy) + ([span] if span else [])
    if span:
        at = span[0]
    else:
        at = next((s for s, e in legacy if LEGACY_HEADING_RE.match(lines[s])), None)
    return rebuilt(lines, cut, at, block)


def plan_uninstall(lines):
    span = block_span(lines)
    return rebuilt(lines, [span], span[0], []) if span else lines


def cmd_install_instructions(ns, extra):
    if ns.file:
        path = os.path.realpath(os.path.expanduser(ns.file))
    else:
        require_write_allowed()
        path = os.path.realpath(default_file())
    block = None if ns.uninstall else block_lines(getattr(ns, "overrides", None))     # a broken template refuses before anything is read or written
    try:
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    except FileNotFoundError:
        old = None
    except (OSError, UnicodeDecodeError) as exc:
        raise OrchError("cannot read %s: %s" % (path, exc), 2)
    lines = (old or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if ns.uninstall:
        out = plan_uninstall(lines)
    else:
        out = plan_install(lines, block, ns.replace_existing)
    new = "\n".join(out) + "\n" if out else ""
    if new == (old or ""):
        print("%s: nothing to do" % tilde(path))
        return 0
    if ns.dry_run:
        diff = difflib.unified_diff((old or "").splitlines(True), new.splitlines(True), tilde(path), tilde(path) + " (new)")
        print("".join(diff), end="")
        return 0
    if old is not None:
        backup = "%s.orch-backup-%s" % (path, datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        shutil.copy2(path, backup)
        print("backup: %s" % tilde(backup))
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, new.encode("utf-8"))
    print("%s: %s" % (tilde(path), "orch block removed" if ns.uninstall else "orch block installed"))
    return 0


def register(add):
    sp = add("install-instructions", cmd_install_instructions, "write orch's rules into the global CLAUDE.md (marked block)")
    sp.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    sp.add_argument("--uninstall", action="store_true", help="remove the marked block")
    sp.add_argument("--replace-existing", action="store_true", help="also remove hand-written orch rules outside the block")
    sp.add_argument("--file", default=None, help="target file (default ~/.claude/CLAUDE.md)")
