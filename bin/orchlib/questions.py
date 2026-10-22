"""questions.md: ask, answer, checkpoint. One `## Q<n> — <text>` block per question, edited through the CLI."""
import json
import os
import re

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
MAX_ASKED = 4
MAX_OPTIONS = 3


class Question(object):
    def __init__(self, n, text, start, end, fields):
        self.n, self.text, self.start, self.end, self.fields = n, text, start, end, fields

    @property
    def status(self):
        return self.fields.get("status", "")

    def options(self):
        """[(label, recommended)] from the `- options:` line."""
        raw = self.fields.get("options", "")
        out = []
        for part in raw.split("|"):
            part = part.strip()
            if not part:
                continue
            rec = part.endswith(RECOMMENDED)
            out.append((part[:-len(RECOMMENDED)].rstrip() if rec else part, rec))
        return out


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


def split_options(raw, recommended):
    opts = [o.strip() for o in (raw or "").split("|")]
    if any(not o for o in opts) and raw:
        raise OrchError("--options has an empty option", 1)
    opts = [o for o in opts if o]
    if len(set(opts)) != len(opts):
        raise OrchError("--options has a duplicate option", 1)
    if recommended is not None:
        rec = recommended.strip()
        if not opts:
            raise OrchError("--recommended needs --options", 1)
        if rec not in opts:
            raise OrchError("--recommended %r is not one of --options" % rec, 1)
    return opts, (recommended or "").strip()


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
    opts, rec = split_options(ns.options, ns.recommended)
    blocks = clean_blocks(ns.blocks)
    reversible = ns.reversible or "unknown"
    if reversible not in REVERSIBLE:
        raise OrchError("--reversible must be yes, no or unknown", 1)
    assume = None
    if ns.assume is not None:
        assume = cli.one_line(ns.assume)
        if not assume:
            raise OrchError("--assume is empty", 1)
        if reversible != "yes":
            raise OrchError("--assume needs --reversible yes (only a reversible decision may be assumed)", 1)
        if opts and assume not in opts:
            raise OrchError("--assume %r is not one of --options" % assume, 1)
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
        shown = [(o + RECOMMENDED) if o == rec else o for o in opts]
        block = ["## Q%d — %s\n" % (n, text),
                 "- status: %s\n" % ("assumed" if assume is not None else "open"),
                 "- asked by: %s at %s\n" % (role, ts),
                 "- blocks: %s\n" % blocks,
                 "- reversible: %s\n" % reversible]
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


def ask_payload(q):
    """One AskUserQuestion entry: recommended option first, at most MAX_OPTIONS of them, plus `Hold on`."""
    opts = q.options()
    opts.sort(key=lambda o: 0 if o[1] else 1)  # stable: the recommended one first
    meta = "Blocks: %s. Reversible: %s." % (q.fields.get("blocks", "all") or "all", q.fields.get("reversible", "unknown") or "unknown")
    entries = []
    for label, rec in opts[:MAX_OPTIONS]:
        entries.append({"label": (label + RECOMMENDED) if rec else label,
                        "description": ("Recommended. " if rec else "") + meta})
    if not entries:
        entries.append({"label": "Answer in free text", "description": "Pick Other and type the answer. " + meta})
    entries.append({"label": "Hold on", "description": "Stop and wait until I resume."})
    return {"question": "Q%d: %s" % (q.n, q.text), "header": "Q%d" % q.n, "multiSelect": False, "options": entries}


def cmd_checkpoint(ns, extra):
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    repo, slug = task_names(task_dir)
    schema = jr.load_schema()
    ts = now()
    with TaskLock(task_dir):
        opens = sorted(open_ones(task_dir), key=sort_key)
        shown = opens[:MAX_ASKED] if ns.ask else []
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
    print("checkpoint: %d open, asking %d (AskUserQuestion input below)" % (len(opens), len(shown)))
    print(json.dumps({"questions": [ask_payload(q) for q in shown]}, ensure_ascii=False))
    if len(opens) > len(shown):
        print("%d more stay parked" % (len(opens) - len(shown)))
    return 0


def register(add):
    sp = add("ask", cmd_ask, "park a question (or record an assumed decision)")
    sp.add_argument("text")
    sp.add_argument("--options", default=None, help='"A|B|C"')
    sp.add_argument("--recommended", default=None)
    sp.add_argument("--blocks", default=None, help="step ids, all (default) or none")
    sp.add_argument("--reversible", default=None, help="yes | no | unknown (default)")
    sp.add_argument("--assume", default=None, help="take this option now (needs --reversible yes)")
    sp.add_argument("--as", dest="as_role", default=None)

    sp = add("answer", cmd_answer, "resolve a question")
    sp.add_argument("qid")
    sp.add_argument("text", nargs="?", default=None)
    sp.add_argument("--withdraw", action="store_true")

    sp = add("checkpoint", cmd_checkpoint, "journal a checkpoint; --ask prints the AskUserQuestion batch")
    sp.add_argument("--ask", action="store_true")
