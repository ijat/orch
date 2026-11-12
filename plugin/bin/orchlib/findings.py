"""orch findings, orch write review, orch finding add|resolve: the review files (review.md, security-review.md) as one
parser and a few validated writes. `orch brief fix` reads the same parser, so the brief and the counts agree.

Findings are described, never judged: a status is closed, open, disputed, unknown or parked, `unknown` counts as open,
and whatever the parser cannot place is counted or listed as `unparsed`. The loop rules (SKILL.md, Review loops) stay
with the orchestrator. Python 3.9 compatible, standard library only."""
import os
import re
import sys

from . import cli
from . import flow
from . import plan as pl
from .paths import OrchError, TaskLock, append_bytes, now, require_write_allowed, write_atomic

CODE_SEVERITIES = ("blocker", "should-fix", "nit")
SEC_SEVERITIES = ("critical", "high", "medium", "low")
ORIGINS = ("introduced", "pre-existing")
STATUS_PHRASES = ("resolved", "still open", "disputed", "fixed", "not fixed")    # templates/review.md:31, :41
CLOSED_PHRASES = ("resolved", "fixed")
OPEN_PHRASES = ("still open", "not fixed", "open")
WRITE_LIMIT = 1024 * 1024
SUMMARY_CHARS = 80

HEAD_RE = re.compile(r"^###[ \t]+([FS]\d+)(?:[ \t]+[—–-]+(?:[ \t]+(.*?))?|[ \t]*)[ \t]*$")
OLD_HEAD_RE = re.compile(r"^###[ \t]+([FS]\d+)\b[ \t]*[:\u2014\u2013-]*[ \t]*(.*?)[ \t]*$")     # what `brief fix` always accepted
LOOSE_HEAD_RE = re.compile(r"^#{1,6}\s*([FS]\d+)\b")
BOLD_HEAD_RE = re.compile(r"^\s*(?:[-*]\s+)?\*\*([FS]\d+)\b")
BLOCK_END_RE = re.compile(r"^#{1,3}\s")
LEVEL2_RE = re.compile(r"^(#{1,2})[ \t]+(.*?)[ \t]*$")
ROUND_RE = re.compile(r"(check(\s+\d+)? of )?round\s+\d+")
STATUS_RE = re.compile(r"^[-*]\s+([FS]\d+)\s*:(.*)$", re.S)     # a unit is a bullet plus its continuation lines; the phrase is the first line
STATUS_LOOSE_RE = re.compile(r"^[-*]\s+([FS]\d+)\s*:")
FIELD_RE = re.compile(r"^-[ \t]+([A-Za-z][A-Za-z ]*?):[ \t]*(.*)$")
PHRASE_SPLIT_RE = re.compile(r"\s[—–-](?:\s|$)")
PLACEHOLDER_RE = re.compile(r"^<.*>$")
ID_RE = re.compile(r"^[FS]\d+$")


# -- parsing -------------------------------------------------------------------------------------

class Finding(object):
    """One `### F<n> — summary` block. `state` is closed, open, disputed, unknown or parked."""

    def __init__(self, fid, summary, head, end, block):
        self.id, self.summary, self.head, self.end, self.block = fid, summary, head, end, block
        self.severity = None        # a value inside the file's set, else None (severity-unknown)
        self.sev_raw = ""
        self.origin = None          # introduced | pre-existing | the raw text | None
        self.file = ""
        self.resolution = ""
        self.phrase = None          # the latest status phrase after this block, or None
        self.state = "open"

    @property
    def number(self):
        return int(self.id[1:])


class Unparsed(object):
    """A line that looks like a finding but is not a parsed block: kind heading, dup (an id that already has a block) or status."""

    def __init__(self, kind, line, text, fid):
        self.kind, self.line, self.text, self.id = kind, line, text.strip(), fid


class Parsed(object):
    def __init__(self, lines, mask, security):
        self.lines, self.mask, self.security = lines, mask, security
        self.findings = []          # first block of each id, in file order
        self.unparsed = []
        self.heads = []             # (id, line index) of every heading-shaped line, parsed or not
        self.status_lines = []      # (id, line index, phrase, section heading, unit text)
        self.stray_status = []      # (id, line index) status-shaped lines outside a round/check section
        self.notes = {}             # id -> ["(<section heading>) <unit text>", …]

    def get(self, fid):
        return next((f for f in self.findings if f.id == fid), None)

    def ids(self):
        return set(i for i, _ in self.heads)

    def next_id(self, prefix):
        nums = [int(i[1:]) for i, _ in self.heads if i[0] == prefix]
        return "%s%d" % (prefix, max(nums + [0]) + 1)

    def block(self, a, b):
        """Lines [a, b) as text, trailing blank lines dropped."""
        while b > a and not self.lines[b - 1].strip():
            b -= 1
        return "\n".join(self.lines[a:b])


def comment_mask(lines, mask):
    """Lines inside an HTML comment that starts at the beginning of a line (the review templates keep their rules in one)."""
    out = list(mask)
    i = 0
    while i < len(lines):
        if not mask[i] and lines[i].lstrip().startswith("<!--"):
            j = i
            while j < len(lines) and "-->" not in lines[j][lines[j].find("<!--") + 4 if j == i else 0:]:
                j += 1
            if j >= len(lines):       # never closed: hiding the rest of the file could hide findings, so mask nothing
                i += 1
                continue
            for k in range(i, j + 1):
                out[k] = True
            i = j + 1
        else:
            i += 1
    return out


def status_phrase(rest):
    return PHRASE_SPLIT_RE.split(rest.strip(), 1)[0].strip().lower() if rest.strip() else ""


def phrase_state(phrase):
    if phrase in CLOSED_PHRASES:
        return "closed"
    if phrase in OPEN_PHRASES:
        return "open"
    if phrase == "disputed":
        return "disputed"
    return "unknown"


def parse_text(text, security=False, lenient_heads=False):
    """`lenient_heads` (for `brief fix`, which must still find `### F1: summary` blocks): any `### F<n>` heading is a block."""
    lines = text.split("\n")
    mask = comment_mask(lines, pl.fence_mask(lines))
    p = Parsed(lines, mask, security)
    sev_set = SEC_SEVERITIES if security else CODE_SEVERITIES
    ends = [i for i, l in enumerate(lines) if not mask[i] and BLOCK_END_RE.match(l)]
    seen = set()
    parsed_heads = set()
    for i, l in enumerate(lines):
        if mask[i]:
            continue
        m = (OLD_HEAD_RE if lenient_heads else HEAD_RE).match(l)
        if m:
            fid = m.group(1)
            p.heads.append((fid, i))
            if fid in seen:
                p.unparsed.append(Unparsed("dup", i, l, fid))
                continue
            seen.add(fid)
            parsed_heads.add(i)
            end = next((e for e in ends if e > i), len(lines))
            f = Finding(fid, (m.group(2) or "").strip(), i, end, p.block(i, end))
            fields = {}
            for k in range(i + 1, end):
                if mask[k]:
                    continue
                fm = FIELD_RE.match(lines[k])
                if fm and fm.group(1).strip().lower() not in fields:
                    fields[fm.group(1).strip().lower()] = fm.group(2).strip()
            f.sev_raw = fields.get("severity", "")
            f.severity = f.sev_raw.lower() if f.sev_raw.lower() in sev_set else None
            f.file = fields.get("file", "")
            f.resolution = fields.get("resolution", "")
            origin = fields.get("origin")
            if origin is not None:
                first = re.match(r"^(introduced|pre-existing)(?=$|\s)", origin.lower())
                f.origin = first.group(1) if first else origin
            p.findings.append(f)
            continue
        lm = LOOSE_HEAD_RE.match(l) or BOLD_HEAD_RE.match(l)
        if lm:
            p.heads.append((lm.group(1), i))
            p.unparsed.append(Unparsed("heading", i, l, lm.group(1)))
    sections = []
    heads2 = [(i, m.group(2)) for i, l in enumerate(lines) if not mask[i] for m in [LEVEL2_RE.match(l)] if m]
    for k, (i, title) in enumerate(heads2):
        end = heads2[k + 1][0] if k + 1 < len(heads2) else len(lines)
        sections.append((i, end, title))
    in_round = [False] * len(lines)
    for i, end, title in sections:
        if not ROUND_RE.match(pl.norm_title(title)):
            continue
        for k in range(i, end):
            in_round[k] = True
        for s, e in pl.split_units(lines, mask, i + 1, end):
            unit = p.block(s, e)
            sm = STATUS_RE.match(unit)
            if not sm:
                continue
            fid = sm.group(1)
            first_line = sm.group(2).split("\n", 1)[0]
            p.status_lines.append((fid, s, status_phrase(first_line), lines[i], unit))
            p.notes.setdefault(fid, []).append("(%s) %s" % (re.sub(r"^#+\s*", "", lines[i]), unit))
    for i, l in enumerate(lines):
        if not mask[i] and not in_round[i]:
            sm = STATUS_LOOSE_RE.match(l)
            if sm:
                p.stray_status.append((sm.group(1), i))
    for f in p.findings:
        later = [s for s in p.status_lines if s[0] == f.id and s[1] > f.head]
        if later:
            f.phrase = max(later, key=lambda s: s[1])[2]
            f.state = phrase_state(f.phrase)
    for fid, s, _, _, unit in p.status_lines:
        if fid not in seen:
            p.unparsed.append(Unparsed("status", s, unit.split("\n", 1)[0], fid))
    p.unparsed.sort(key=lambda u: u.line)
    return p


def parse(path, security=False, lenient_heads=False):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise OrchError("cannot read %s: %s" % (path, exc), 1)
    return parse_text(raw.decode("utf-8", "surrogateescape"), security, lenient_heads)


def mark_parked(parsed, records, milestone):
    """A `follow_up` journal record parks finding X only when its finding is X's id, its severity equals X's and its
    milestone equals the task's (absent on both sides for a single plan). Security findings never park."""
    if parsed.security:
        return
    for rec in records:
        if flow.kind_of(rec) != "follow_up":
            continue
        for f in parsed.findings:
            if (f.state != "closed" and rec.get("finding") == f.id and f.severity is not None
                    and rec.get("severity") == f.severity and rec.get("milestone") == milestone):
                f.state = "parked"


# -- counts and lines ----------------------------------------------------------------------------

def counted(f):
    return f.state in ("open", "disputed", "unknown")


def finding_line(f):
    summary = f.summary if len(f.summary) <= SUMMARY_CHARS else f.summary[:SUMMARY_CHARS - 1].rstrip() + "…"
    return " | ".join((f.id, f.severity or "severity-unknown", f.origin or "-", f.file or "-", f.state, summary))


def unparsed_line(u):
    text = u.text if len(u.text) <= SUMMARY_CHARS else u.text[:SUMMARY_CHARS - 1].rstrip() + "…"
    return "unparsed | L%d | %s" % (u.line + 1, text)


def counts_line(parsed):
    sevs = SEC_SEVERITIES if parsed.security else CODE_SEVERITIES
    groups = {"open": [], "pre-existing": []}
    for f in parsed.findings:
        if counted(f):
            groups["pre-existing" if f.origin == "pre-existing" and not parsed.security else "open"].append(f)

    def tally(fs):
        parts = ["%s %d" % (s, sum(1 for f in fs if f.severity == s)) for s in sevs]
        parts.append("severity-unknown %d" % sum(1 for f in fs if f.severity is None))
        return ", ".join(parts)

    allc = groups["open"] + groups["pre-existing"]
    flags = "disputed %d, status-unknown %d" % (sum(1 for f in allc if f.state == "disputed"),
                                                sum(1 for f in allc if f.state == "unknown"))
    closed = sum(1 for f in parsed.findings if f.state == "closed")
    parked = sum(1 for f in parsed.findings if f.state == "parked")
    parts = ["open: " + tally(groups["open"])]
    if not parsed.security:
        parts.append("pre-existing: " + tally(groups["pre-existing"]))
    parts.append("flags: " + flags)
    parts.append("not counted: " + ("closed %d" % closed if parsed.security else "parked %d, closed %d" % (parked, closed)))
    parts.append("unparsed %d" % len(parsed.unparsed))
    return "; ".join(parts)


# -- validation of new text ----------------------------------------------------------------------

def check_block_fields(f, security):
    """Problems of a new finding block: severity, file, and for code findings the origin."""
    problems = []
    sev_set = SEC_SEVERITIES if security else CODE_SEVERITIES
    if f.severity is None:
        problems.append("%s: `- severity:` must be one of %s (got %r)" % (f.id, " | ".join(sev_set), f.sev_raw))
    if not f.file or PLACEHOLDER_RE.match(f.file):
        problems.append("%s: `- file:` is missing (PATH:LINE)" % f.id)
    if not security and (f.origin not in ORIGINS):
        problems.append("%s: `- origin:` must be introduced or pre-existing (then ` — evidence`)" % f.id)
    return problems


def validate_text(text, existing, security):
    """(problems, [new finding ids], status line count) for text to append to a file whose parsed form is `existing`
    (None: the file does not exist yet). Nothing here writes."""
    prefix = "S" if security else "F"
    frag = parse_text(text, security)
    have = existing.ids() if existing is not None else set()
    problems = []
    new_ids = []
    for f in frag.findings:
        if f.id[0] != prefix:
            problems.append("%s: this file takes %s ids (%s)" % (f.id, prefix, "security-review.md" if security else "review.md"))
            continue
        if f.id in have:
            problems.append("%s: already in the file" % f.id)
        else:
            problems.extend(check_block_fields(f, security))
        new_ids.append(f.id)
    for u in frag.unparsed:
        if u.kind == "dup":
            problems.append("%s: repeated in the text" % u.id)
        elif u.kind == "heading":
            if u.id[0] != prefix:
                problems.append("%s: this file takes %s ids" % (u.id, prefix))
            else:
                problems.append("line %d is not a finding heading (`### %s1 — summary`): %s" % (u.line + 1, prefix, u.text[:60]))
        # status entries are checked below
    known = have | set(new_ids)
    statuses = 0
    for fid, line, phrase, _, _ in frag.status_lines:
        statuses += 1
        if fid[0] != prefix:
            problems.append("%s: a status line for an id this file does not take (%s ids)" % (fid, prefix))
        elif fid not in known:
            problems.append("%s: a status line for an id that exists nowhere" % fid)
        if phrase not in STATUS_PHRASES:
            problems.append("%s: status %r is not one of: %s" % (fid, phrase, ", ".join(STATUS_PHRASES)))
    for fid, line in frag.stray_status:
        problems.append("%s: status lines go under a `## Round N` or `## Check of round N` heading (line %d)" % (fid, line + 1))
    return problems, new_ids, statuses


# -- context -------------------------------------------------------------------------------------

def context(ns):
    from . import brief       # brief imports this module
    ns.plan = None
    return brief.load_ctx(ns, infer=True)


def review_path(c, security):
    return os.path.join(c.review_dir, "security-review.md" if security else "review.md")


def milestone_of(c):
    return c.ms if c.program else None


def read_stdin(limit=WRITE_LIMIT):
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    raw = sys.stdin.buffer.read(limit + 1)
    if len(raw) > limit:
        raise OrchError("the text is over %d bytes" % limit, 1)
    return raw.decode("utf-8", "surrogateescape")


def refuse(problems, next_id):
    raise OrchError("\n".join(problems + ["next id: %s" % next_id]), 1)


def title_for(c, security):
    return "# %s: %s\n" % ("Security review" if security else "Code review", c.slug)


def separator(path):
    """What must precede appended text so it starts after a blank line."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        n = fh.tell()
        if n == 0:
            return ""
        fh.seek(max(0, n - 2))
        tail = fh.read()
    if tail.endswith(b"\n\n"):
        return ""
    return "\n" if tail.endswith(b"\n") else "\n\n"


def append_text(c, path, security, text=None, build=None):
    """Validate the text against the file as it is under the lock, then append it in one write; all or nothing.
    `build(next id)` makes the text when the id is assigned here (`finding add`). Returns (new ids, status lines, next id)."""
    prefix = "S" if security else "F"
    with TaskLock(c.task_dir):
        exists = os.path.exists(path)
        existing = parse(path, security) if exists else None
        file_next = existing.next_id(prefix) if existing is not None else "%s1" % prefix
        if build is not None:
            text = build(file_next)
        problems, new_ids, statuses = validate_text(text, existing, security)
        if problems:
            refuse(problems, file_next)
        if not new_ids and not statuses:
            raise OrchError("nothing to write: the text has no finding block and no status line\nnext id: %s" % file_next, 1)
        body = text.rstrip("\n") + "\n"
        if exists:
            data = separator(path) + body
        else:
            data = ("" if re.match(r"^\s*#\s", text) else title_for(c, security) + "\n") + body
        append_bytes(path, data.encode("utf-8", "surrogateescape"))
        nums = [int(file_next[1:]) - 1] + [int(i[1:]) for i in new_ids]
    return new_ids, statuses, "%s%d" % (prefix, max(nums) + 1)


# -- verbs ---------------------------------------------------------------------------------------

def cmd_write(ns, extra):
    """`orch write review [--security]`: the text on stdin, validated whole, appended in one write."""
    require_write_allowed()
    c = context(ns)
    text = read_stdin()
    if not text.strip():
        raise OrchError("nothing to write (stdin is empty)", 1)
    path = review_path(c, ns.security)
    ids, statuses, nxt = append_text(c, path, ns.security, text=text)
    wrote = "wrote " + (", ".join(ids) if ids else "no new finding")
    if statuses:
        wrote += " (+%d status line%s)" % (statuses, "" if statuses == 1 else "s")
    print("%s: %s; next id %s%s" % (os.path.basename(path), wrote, nxt, cli.tag(ns)))
    return 0


def one_line_arg(text, name):
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value:
        raise OrchError("%s is empty" % name, 1)
    return value


def cmd_finding(ns, extra):
    return finding_add(ns) if ns.action == "add" else finding_resolve(ns)


def finding_add(ns):
    require_write_allowed()
    if ns.fid:
        raise OrchError("`finding add` takes no id (the next one is assigned)", 1)
    for name in ("severity", "file", "summary"):
        if not getattr(ns, name):
            raise OrchError("finding add needs --%s" % name, 1)
    security = ns.security
    if security and ns.origin:
        raise OrchError("--origin belongs to code findings, not --security", 1)
    c = context(ns)
    path = review_path(c, security)
    summary = one_line_arg(ns.summary, "--summary")
    fpath = one_line_arg(ns.file, "--file")
    details = read_stdin().strip("\n")

    def build(fid):
        block = ["### %s \u2014 %s" % (fid, summary), "- severity: %s" % ns.severity.strip()]
        if not security:
            block.append("- origin: %s" % (ns.origin or "").strip())
        block.append("- file: %s" % fpath)
        if details.strip():
            block.append(details)
        block.append("- resolution:")
        return "\n".join(block) + "\n"

    ids, _, _ = append_text(c, path, security, build=build)
    print("%s%s" % (ids[0], cli.tag(ns)))
    return 0


def locate_resolution(lines, f):
    """(index of the `- resolution:` line, index one past its last continuation line) inside finding f, or None."""
    for k in range(f.head + 1, f.end):
        if re.match(r"^-[ \t]+resolution:", lines[k], re.I):
            e = k + 1
            while e < f.end and lines[e].strip() and lines[e][:1] in (" ", "\t"):
                e += 1
            return k, e
    return None


def finding_resolve(ns):
    require_write_allowed()
    if not ns.fid or not ID_RE.match(ns.fid):
        raise OrchError("finding resolve needs an id (F<n> for review.md, S<n> for security-review.md)", 1)
    c = context(ns)
    security = ns.fid.startswith("S")
    path = review_path(c, security)
    if not os.path.isfile(path):
        raise OrchError("%s not found (needed for %s)" % (path, ns.fid), 1)
    text = ns.text if ns.text is not None else read_stdin()
    text = one_line_arg(text, "the resolution text")
    with TaskLock(c.task_dir):
        parsed = parse(path, security)
        f = parsed.get(ns.fid)
        if f is None:
            raise OrchError("finding %s not found in %s" % (ns.fid, path), 1)
        lines = list(parsed.lines)
        loc = locate_resolution(lines, f)
        if loc is None:
            end = f.end
            while end > f.head + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, "- resolution: %s" % text)
            how = "set"
        else:
            k, e = loc
            old = re.sub(r"^-[ \t]+resolution:[ \t]*", "", lines[k], flags=re.I).strip()
            if not old or PLACEHOLDER_RE.match(old):
                lines[k] = "- resolution: %s" % text
                how = "set"
            else:
                lines[e - 1] = "%s | %s %s" % (lines[e - 1].rstrip(), now(), text)
                how = "appended"
        write_atomic(path, "\n".join(lines).encode("utf-8", "surrogateescape"))
    print("%s: resolution %s%s" % (ns.fid, how, cli.tag(ns)))
    return 0


def cmd_findings(ns, extra):
    """`orch findings [--security] [--open] [--counts]`: read-only."""
    c = context(ns)
    path = review_path(c, ns.security)
    if not os.path.isfile(path):
        raise OrchError("no %s in %s" % (os.path.basename(path), c.review_dir), 1)
    parsed = parse(path, ns.security)
    mark_parked(parsed, flow.read_journal(c.task_dir), milestone_of(c))
    out = []
    if ns.open or not ns.counts:
        out.extend(finding_line(f) for f in parsed.findings if counted(f) or not ns.open)
        out.extend(unparsed_line(u) for u in parsed.unparsed)
    if ns.counts:
        out.append(counts_line(parsed))
    for line in out:
        print(line)
    return 0


def register(add):
    sp = add("write", cmd_write, "append validated text to review.md (`write review`) or security-review.md (`--security`); text on stdin")
    sp.add_argument("target", choices=["review"])
    sp.add_argument("--security", action="store_true")

    sp = add("finding", cmd_finding, "`finding add --severity S --file PATH:LINE --summary T [--origin O]` or `finding resolve ID --text T`")
    sp.add_argument("action", choices=["add", "resolve"])
    sp.add_argument("fid", nargs="?", default=None, metavar="ID")
    sp.add_argument("--security", action="store_true")
    sp.add_argument("--severity", default=None)
    sp.add_argument("--file", default=None)
    sp.add_argument("--summary", default=None)
    sp.add_argument("--origin", default=None)
    sp.add_argument("--text", default=None)

    sp = add("findings", cmd_findings, "list a review file's findings, their status and counts (read-only)")
    sp.add_argument("--security", action="store_true")
    sp.add_argument("--open", action="store_true", help="only the counted findings (open, disputed, unknown) and the unparsed lines")
    sp.add_argument("--counts", action="store_true", help="one counts line (alone: no finding lines)")
