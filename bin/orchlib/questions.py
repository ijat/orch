"""questions.md: ask, answer, checkpoint. One `## Q<n> — <text>` block per question, edited through the CLI."""
import os
import re
import sys
from collections import namedtuple

from . import cli
from . import journal as jr
from . import status as st
from .paths import OrchError, TEMPLATES_DIR, TaskLock, now, task_names, write_atomic

HEAD_RE = re.compile(r"^## Q(\d+)\b[ \t]*(?:[—–-]+[ \t]*)?(.*?)\s*$")
FIELD_RE = re.compile(r"^- ([a-z][a-z ]*?):[ \t]*(.*?)\s*$")
QID_RE = re.compile(r"^[Qq]?(\d+)$")
BLOCKS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REVERSIBLE = ("yes", "no", "unknown")
RECOMMENDED = " (recommended)"
RECOMMENDED_END_RE = re.compile(r"\(recommended\)\s*$", re.I)
OPTION_SEP_RE = re.compile(r"::|!!")             # `Label :: explanation` or `Label !! reason` (D2)
STORED_SEP_RE = re.compile(r"\s(::|!!)\s")      # the stored form, always spaced; a bare `std::vector` label is untouched
REASON_FILLER = frozenset(("n/a", "na", "none", "obvious", "self-explanatory", "not", "needed"))
MIN_REASON_WORDS = 3
OPEN_WARN = 20                                   # D8: warn at this many open questions, still park
TOPIC_MAX = 60
UNGROUPED = "(ungrouped)"                         # the name `orch status --json --question-group` gives questions with no group
SHOW_MAX = 10                                     # `orch checkpoint --ask` writes the first 10 by sort_key, then the count left
Option = namedtuple("Option", "label text kind recommended")   # kind: explanation | reason | None (a bare, old option)


class Question(object):
    def __init__(self, n, text, start, end, fields):
        self.n, self.text, self.start, self.end, self.fields = n, text, start, end, fields

    @property
    def status(self):
        return self.fields.get("status", "")

    def option_details(self):
        """[Option] from the `- options:` line: `Label (recommended) :: explanation | Label !! reason | Bare`."""
        raw = self.fields.get("options", "")
        out = []
        for part in raw.split("|"):
            part = part.strip()
            if not part:
                continue
            text, kind = None, None
            m = STORED_SEP_RE.search(part)
            if m:
                text, kind = part[m.end():].strip(), ("explanation" if m.group(1) == "::" else "reason")
                part = part[:m.start()].strip()
            rec = part.endswith(RECOMMENDED)
            out.append(Option(part[:-len(RECOMMENDED)].rstrip() if rec else part, text, kind, rec))
        return out

    def options(self):
        """[(label, recommended)]: labels only, whatever explanation or reason is stored."""
        return [(o.label, o.recommended) for o in self.option_details()]


def questions_path(task_dir):
    return os.path.join(task_dir, "questions.md")


def read_lines(task_dir):
    path = questions_path(task_dir)
    if not os.path.exists(path):
        return []
    with open(path, "rb") as fh:
        return st.split_lines(fh.read().decode("utf-8", "surrogateescape"))


def parse(lines):
    heads = []
    for i, line in enumerate(lines):
        m = HEAD_RE.match(line.rstrip("\n"))
        if m:
            heads.append((i, int(m.group(1)), m.group(2)))
    out = []
    for k, (i, n, text) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        fields = {}
        for line in lines[i + 1:end]:
            m = FIELD_RE.match(line.rstrip("\n"))
            if m and m.group(1) not in fields:
                fields[m.group(1)] = m.group(2)
        out.append(Question(n, text, i, end, fields))
    return out


def load_questions(task_dir):
    return parse(read_lines(task_dir))


def open_ones(task_dir):
    return [q for q in load_questions(task_dir) if q.status == "open"]


def sort_key(q):
    b = q.fields.get("blocks", "all").strip().lower() or "all"
    if b == "all":
        return (0, 0, 0, "", q.n)
    if b == "none":
        return (2, 0, 0, "", q.n)
    first = b.split(",")[0].strip()
    if re.match(r"^\d+$", first):
        return (1, 0, int(first), "", q.n)
    return (1, 1, 0, first, q.n)


def render_file(task_dir, lines):
    return "".join(lines).encode("utf-8", "surrogateescape")


def new_file_lines(task_dir):
    try:
        with open(os.path.join(TEMPLATES_DIR, "questions.md"), encoding="utf-8") as fh:
            head = fh.read()
    except OSError as exc:
        raise OrchError("cannot read questions template: %s" % exc, 3)
    head = head.replace("<task-slug>", task_names(task_dir)[1]).rstrip("\n") + "\n"
    return st.split_lines(head)


def set_field(lines, q, key, value):
    """Replace `- key:` inside the block, or add it after the block's last field line."""
    for i in range(q.start + 1, q.end):
        m = FIELD_RE.match(lines[i].rstrip("\n"))
        if m and m.group(1) == key:
            _, ending = st.split_ending(lines[i])
            lines[i] = "- %s: %s%s" % (key, value, ending or "\n")
            return
    at = q.start + 1
    for i in range(q.start + 1, q.end):
        if FIELD_RE.match(lines[i].rstrip("\n")):
            at = i + 1
    if at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines.insert(at, "- %s: %s\n" % (key, value))


def parse_qid(text):
    m = QID_RE.match((text or "").strip())
    if not m:
        raise OrchError("bad question id %r (Q3 or 3)" % text, 1)
    return int(m.group(1))


def check_reason(label, reason):
    words = reason.split()
    if len(words) < MIN_REASON_WORDS:
        raise OrchError("option %r: a `!!` reason needs at least %d words, got %r" % (label, MIN_REASON_WORDS, reason), 1)
    if all(w.strip(".,;:!?()\"'").lower() in REASON_FILLER for w in words):
        raise OrchError("option %r: the `!!` reason %r is filler; say why it cannot be explained" % (label, reason), 1)


def split_options(raw, recommended, reversible="unknown"):
    """([Option], recommended label) from `--options "Label :: explanation|Label !! reason"` (D2). Each option has
    exactly one separator; a `!!` reason never goes on the recommended option or on a `--reversible no` question."""
    parts = [cli.one_line(o) for o in raw.split("|")] if raw else []
    if any(not o for o in parts):
        raise OrchError("--options has an empty option", 1)
    rec = (recommended or "").strip()
    opts = []
    for part in parts:
        if len(OPTION_SEP_RE.findall(part)) != 1:
            raise OrchError("option %r needs exactly one of `::` (explanation) or `!!` (reason), once; "
                            "`|`, `::` and `!!` cannot appear inside a label or text" % part, 1)
        mark = OPTION_SEP_RE.search(part).group(0)
        label, text = (x.strip() for x in OPTION_SEP_RE.split(part, 1))
        if not label or not text:
            raise OrchError("option %r needs a label before and text after `%s`" % (part, mark), 1)
        if RECOMMENDED_END_RE.search(label):
            raise OrchError("option label %r must not end with (recommended); use --recommended" % label, 1)
        kind = "explanation" if mark == "::" else "reason"
        if kind == "reason":
            if reversible == "no":
                raise OrchError("option %r: a `--reversible no` question explains every option (`::`), none takes `!!`" % label, 1)
            check_reason(label, text)
        opts.append(Option(label, text, kind, label == rec))
    labels = [o.label for o in opts]
    if len(set(labels)) != len(labels):
        raise OrchError("--options has a duplicate option label", 1)
    if recommended is not None:
        if not opts:
            raise OrchError("--recommended needs --options", 1)
        if rec not in labels:
            raise OrchError("--recommended %r is not one of the option labels" % rec, 1)
        if opts[labels.index(rec)].kind == "reason":
            raise OrchError("the recommended option %r must be explained (`::`), not given a `!!` reason" % rec, 1)
    return opts, rec


def clean_blocks(raw):
    if raw is None:
        return "all"
    v = raw.strip()
    if v.lower() in ("all", "none"):
        return v.lower()
    items = [x.strip() for x in v.split(",")]
    if not items or any(not BLOCKS_RE.match(x) for x in items):
        raise OrchError("--blocks must be all, none or a comma list of step ids, got %r" % raw, 1)
    return ", ".join(items)


def cmd_ask(ns, extra):
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    text = cli.one_line(ns.text)
    if not text:
        raise OrchError("question text is empty", 1)
    role = cli.one_line(ns.as_role or "orchestrator")
    reversible = ns.reversible or "unknown"
    if reversible not in REVERSIBLE:
        raise OrchError("--reversible must be yes, no or unknown", 1)
    opts, rec = split_options(ns.options, ns.recommended, reversible)
    labels = [o.label for o in opts]
    blocks = clean_blocks(ns.blocks)
    topic = cli.clip(text, TOPIC_MAX)
    if ns.topic is not None:
        topic = cli.one_line(ns.topic)
        if not topic:
            raise OrchError("--topic is empty", 1)
    group = None
    if ns.group is not None:
        group = cli.one_line(ns.group)
        if not group:
            raise OrchError("--group is empty", 1)
        if group == UNGROUPED:
            raise OrchError("--group %s is reserved" % UNGROUPED, 1)
    assume = None
    if ns.assume is not None:
        assume = cli.one_line(ns.assume)
        if not assume:
            raise OrchError("--assume is empty", 1)
        if reversible != "yes":
            raise OrchError("--assume needs --reversible yes (only a reversible decision may be assumed)", 1)
        if opts and assume not in labels:
            raise OrchError("--assume %r is not one of the option labels" % assume, 1)
    ts = now()
    log_role = role
    cli.log_line(ts, log_role, "x")  # validates the role name before anything is written
    repo, slug = task_names(task_dir)
    schema = jr.load_schema() if assume is not None else None
    with TaskLock(task_dir):
        lines = read_lines(task_dir) if os.path.exists(questions_path(task_dir)) else new_file_lines(task_dir)
        n = max([q.n for q in parse(lines)] or [0]) + 1
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        shown = ["%s%s %s %s" % (o.label, RECOMMENDED if o.recommended else "", "::" if o.kind == "explanation" else "!!",
                                 o.text) for o in opts]
        open_now = sum(1 for q in parse(lines) if q.status == "open") + (0 if assume is not None else 1)
        block = ["## Q%d — %s\n" % (n, text),
                 "- status: %s\n" % ("assumed" if assume is not None else "open"),
                 "- asked by: %s at %s\n" % (role, ts),
                 "- blocks: %s\n" % blocks,
                 "- reversible: %s\n" % reversible,
                 "- topic: %s\n" % topic]
        if group is not None:
            block.append("- group: %s\n" % group)
        if shown:
            block.append("- options: %s\n" % " | ".join(shown))
        if assume is not None:
            block.append("- answer: %s (%s)\n" % (assume, ts))
        data = None
        if assume is not None:
            status = None
            spath = cli.status_path(task_dir)
            if os.path.exists(spath):
                status = st.Status.load(spath)
            program, auto_ms = cli.program_info(status) if status else (False, None)
            rec_j, _ = jr.build_record(
                schema, "assumed_decision",
                [("decision", "=", assume), ("reversible", ":=", "true"), ("role", "=", role),
                 ("question", "=", "Q%d: %s" % (n, text))], ts, repo, slug, program, auto_ms)
            data = jr.encode(rec_j)
        what = ("Q%d assumed %s" % (n, assume)) if assume is not None else ("Q%d parked (blocks %s)" % (n, blocks))
        log = cli.log_line(ts, log_role, "question %s: %s" % (what, cli.clip(text, 100)))
        write_atomic(questions_path(task_dir), render_file(task_dir, lines + block), 0o644)
        if data is not None:
            jr.append(os.path.join(task_dir, "journal.jsonl"), data)
        cli.append_log(task_dir, log)
    print("Q%d %s%s" % (n, "assumed " + assume if assume is not None else "open", "" if assume is not None else " (blocks %s)" % blocks))
    if open_now >= OPEN_WARN:
        sys.stderr.write("warning: %d open questions are parked; group them (--group), merge duplicates or ask in "
                         "text before parking more\n" % open_now)
    return 0


def cmd_answer(ns, extra):
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    n = parse_qid(ns.qid)
    if ns.withdraw:
        if ns.text:
            raise OrchError("--withdraw takes no answer text", 1)
        text = None
    else:
        text = cli.one_line(ns.text or "")
        if not text:
            raise OrchError("answer needs the answer text (or --withdraw)", 1)
    ts = now()
    log = cli.log_line(ts, "orchestrator", "x")
    with TaskLock(task_dir):
        lines = read_lines(task_dir)
        match = [q for q in parse(lines) if q.n == n]
        if not match:
            raise OrchError("no question Q%d in %s" % (n, questions_path(task_dir)), 1)
        q = match[0]
        if q.status != "open":
            raise OrchError("Q%d is %s, not open; nothing written" % (n, q.status or "unknown"), 1)
        if text is None:
            set_field(lines, q, "status", "withdrawn")
            log = cli.log_line(ts, "orchestrator", "question Q%d withdrawn" % n)
        else:
            set_field(lines, q, "status", "answered")
            q = [x for x in parse(lines) if x.n == n][0]
            set_field(lines, q, "answer", "%s (%s)" % (text, ts))
            log = cli.log_line(ts, "orchestrator", "question Q%d answered: %s" % (n, cli.clip(text, 100)))
        write_atomic(questions_path(task_dir), render_file(task_dir, lines), 0o644)
        cli.append_log(task_dir, log)
    print("Q%d %s" % (n, "withdrawn" if text is None else "answered"))
    return 0


def cmd_answers(ns, extra):
    """Several answers, all or nothing: every id and text is checked before the file is touched (D9). With `--auto`
    each one is an auto-answer (the pane's Auto-answer option): the question must be open, `--reversible yes`, and the
    text must be its recommended option; it becomes `assumed` and is journaled as `orch ask --assume` journals."""
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    auto = bool(ns.auto)
    role = cli.one_line(ns.as_role or "orchestrator")
    args = ns.pairs
    if len(args) % 2:
        raise OrchError("answers takes <qid> <text> pairs; got an odd number of arguments", 1)
    pairs, seen = [], set()
    for qid, raw in zip(args[::2], args[1::2]):
        n = parse_qid(qid)
        if n in seen:
            raise OrchError("Q%d is answered twice in this call; nothing written" % n, 1)
        seen.add(n)
        text = cli.one_line(raw)
        if not text:
            raise OrchError("the answer to Q%d is empty; nothing written" % n, 1)
        pairs.append((n, text))
    ts = now()
    cli.log_line(ts, role, "x")
    repo, slug = task_names(task_dir)
    schema = jr.load_schema() if auto else None
    with TaskLock(task_dir):
        lines = read_lines(task_dir)
        by_n = dict((q.n, q) for q in parse(lines))
        for n, text in pairs:
            if n not in by_n:
                raise OrchError("no question Q%d in %s; nothing written" % (n, questions_path(task_dir)), 1)
            if by_n[n].status != "open":
                raise OrchError("Q%d is %s, not open; nothing written" % (n, by_n[n].status or "unknown"), 1)
            if auto:
                if by_n[n].fields.get("reversible", "unknown") != "yes":
                    raise OrchError("Q%d is not --reversible yes; it is never auto-answered; nothing written" % n, 1)
                if not any(o.recommended and o.label == text for o in by_n[n].option_details()):
                    raise OrchError("%r is not Q%d's recommended option; nothing written" % (text, n), 1)
        status = None
        spath = cli.status_path(task_dir)
        if auto and os.path.exists(spath):
            status = st.Status.load(spath)
        program, auto_ms = cli.program_info(status) if status else (False, None)
        log, journal = "", []
        for n, text in pairs:
            q = [x for x in parse(lines) if x.n == n][0]
            set_field(lines, q, "status", "assumed" if auto else "answered")
            q = [x for x in parse(lines) if x.n == n][0]
            set_field(lines, q, "answer", "%s (%s)" % (text, ts))
            if auto:
                log += cli.log_line(ts, role, "question Q%d assumed %s: %s" % (n, text, cli.clip(q.text, 100)))
                rec, _ = jr.build_record(
                    schema, "assumed_decision",
                    [("decision", "=", text), ("reversible", ":=", "true"), ("role", "=", role),
                     ("question", "=", "Q%d: %s" % (n, q.text))], ts, repo, slug, program, auto_ms)
                journal.append(jr.encode(rec))
            else:
                log += cli.log_line(ts, "orchestrator", "question Q%d answered: %s" % (n, cli.clip(text, 100)))
        write_atomic(questions_path(task_dir), render_file(task_dir, lines), 0o644)
        for data in journal:
            jr.append(os.path.join(task_dir, "journal.jsonl"), data)
        cli.append_log(task_dir, log)
    for n, text in pairs:
        print("Q%d %s" % (n, "assumed " + text if auto else "answered"))
    return 0


def letter(i):
    """a, b, … z, aa, ab, …"""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(97 + r) + out
    return out


def ask_text(shown, remaining):
    """The questions as text for the user (D7): numbered, lettered options with the recommended one first and every
    option explained, a reply hint, and always Hold on."""
    out = []
    for i, q in enumerate(shown, 1):
        group = q.fields.get("group")
        out.append("%d. %s  (Q%d, blocks %s, reversible %s%s)" % (
            i, q.text, q.n, q.fields.get("blocks", "all") or "all", q.fields.get("reversible", "unknown") or "unknown",
            ", group %s" % group if group else ""))
        opts = sorted(q.option_details(), key=lambda o: 0 if o.recommended else 1)   # stable
        for j, o in enumerate(opts):
            line = "   %s) %s%s" % (letter(j), o.label, RECOMMENDED if o.recommended else "")
            if o.kind == "explanation":
                line += " \u2014 " + o.text
            elif o.kind == "reason":
                line += " \u2014 not explained: " + o.text
            out.append(line)
        if not opts:
            out.append("   Answer in your own words.")
    hints = ["`1a 2b` (question 1 option a, question 2 option b)"]
    if any(o.recommended for q in shown if q.fields.get("reversible") != "no" for o in q.option_details()):
        hints.append("`ok` to take every recommended option except on questions marked `reversible no`, "
                     "which you answer yourself")
    hints.append("`hold`")
    out.append("Hold on: stop and wait until you resume.")
    out.append("Reply with %s." % ", ".join(hints))
    if remaining:
        out.append("%d more stay parked." % remaining)
    return out


def cmd_checkpoint(ns, extra):
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    repo, slug = task_names(task_dir)
    schema = jr.load_schema()
    ts = now()
    with TaskLock(task_dir):
        opens = sorted(open_ones(task_dir), key=sort_key)
        shown = opens[:SHOW_MAX] if ns.ask else []
        spath = cli.status_path(task_dir)
        status = st.Status.load(spath) if os.path.exists(spath) else None
        program, auto_ms = cli.program_info(status) if status else (False, None)
        rec, _ = jr.build_record(schema, "checkpoint",
                                 [("questions", ":=", str(len(opens))), ("asked", ":=", "true" if shown else "false")],
                                 ts, repo, slug, program, auto_ms)
        jr.append(os.path.join(task_dir, "journal.jsonl"), jr.encode(rec))
    if not opens:
        print("checkpoint: 0 open questions")
        return 0
    if not ns.ask:
        print("checkpoint: %d open question%s" % (len(opens), "" if len(opens) == 1 else "s"))
        for q in opens[:8]:
            print("  Q%d (blocks %s): %s" % (q.n, q.fields.get("blocks", "all"), cli.clip(q.text, 100)))
        return 0
    print("checkpoint: %d open, asking %d. Ask these in text and end the turn; do not open a dialog." % (len(opens), len(shown)))
    for line in ask_text(shown, len(opens) - len(shown)):
        print(line)
    return 0


def register(add):
    sp = add("ask", cmd_ask, "park a question (or record an assumed decision)")
    sp.add_argument("text")
    sp.add_argument("--options", default=None,
                    help='"Label :: explanation|Label !! reason": every option has exactly one; a `!!` reason '
                         '(3+ words, never filler) is for a non-recommended option on a question that is not --reversible no')
    sp.add_argument("--topic", default=None, help="a short title (default: the first %d characters of the text)" % TOPIC_MAX)
    sp.add_argument("--group", default=None, help="name that batches related questions")
    sp.add_argument("--recommended", default=None)
    sp.add_argument("--blocks", default=None, help="step ids, all (default) or none")
    sp.add_argument("--reversible", default=None, help="yes | no | unknown (default)")
    sp.add_argument("--assume", default=None, help="take this option now (needs --reversible yes)")
    sp.add_argument("--as", dest="as_role", default=None)

    sp = add("answer", cmd_answer, "resolve a question")
    sp.add_argument("qid")
    sp.add_argument("text", nargs="?", default=None)
    sp.add_argument("--withdraw", action="store_true")

    sp = add("answers", cmd_answers, "resolve several questions at once (all or nothing): <qid> <text> [<qid> <text> ...]")
    sp.add_argument("pairs", nargs="+", metavar="qid_or_text")
    sp.add_argument("--auto", action="store_true",
                    help="auto-answer: each question must be open and --reversible yes, the text its recommended option; "
                         "recorded as assumed")
    sp.add_argument("--as", dest="as_role", default=None, help="role named in the log line (default orchestrator)")

    sp = add("checkpoint", cmd_checkpoint, "journal a checkpoint; --ask prints the first 10 open questions as text to ask the user")
    sp.add_argument("--ask", action="store_true")
