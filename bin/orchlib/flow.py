"""spawn, return, end: the orchestrator's per-subagent transitions, one call each."""
import calendar
import datetime
import json
import os
import re
import shlex
import subprocess
import time

from . import cli
from . import journal as jr
from . import questions as qs
from . import status as st
from .paths import OrchError, TEMPLATES_DIR, TaskLock, now, refresh_index, task_names, warn_stderr, write_atomic

ROLE_RE = re.compile(r"^[a-z][a-z0-9:_.-]*$")
AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]*$")
TIER_RE = re.compile(r"^[a-z0-9.-]+/[a-z0-9]+$")
USAGE_STATES = {0: "OK", 1: "WARN", 2: "STOP", 3: "STALE", 4: "UNKNOWN"}
OUTCOMES = ("done", "blocked", "failed", "stopped")
END_OUTCOMES = ("merged", "declined", "failed_merge", "abandoned", "handed_to_user")
QUIET_FILES = ("status.md", "log.md", "journal.jsonl")
SKIP_DIRS = (".git", "node_modules", "__pycache__")
MAX_CHANGED = 15


def read_journal(task_dir):
    """[dict] of every parseable journal line (damaged lines are skipped, never fatal)."""
    path = os.path.join(task_dir, "journal.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        pass
    return out


def kind_of(rec):
    return rec.get("kind") or rec.get("event")


def clock():
    """The current time as epoch seconds; ORCH_NOW (tests) fixes it."""
    return ts_epoch(now()) if os.environ.get("ORCH_NOW") else time.time()


def ts_epoch(ts):
    try:
        return calendar.timegm(datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%MZ").timetuple())
    except (TypeError, ValueError):
        return None


def status_or_none(task_dir):
    path = cli.status_path(task_dir)
    return st.Status.load(path) if os.path.exists(path) else None


def program_of(status):
    return cli.program_info(status) if status else (False, None)


# -- spawn ---------------------------------------------------------------------------------------

def run_usage():
    """-> (code, 5h, 7d, line). Code 0 OK, 1 WARN, 2 STOP, 3 STALE, 4 UNKNOWN (also: the check could not run).
    Default: the in-process estimator (`orch usage`); ORCH_USAGE_CMD replaces it with a command."""
    override = os.environ.get("ORCH_USAGE_CMD")
    if override is None:
        from . import usage
        try:
            e = usage.safe_estimate()
            line, code = e.text(), e.code
        except Exception as exc:
            line, code = "state=UNKNOWN reason=%s" % type(exc).__name__, 4
        return (code,) + usage_values(line) + (line,)
    try:
        argv = shlex.split(override)
        if not argv:
            raise OSError("ORCH_USAGE_CMD is empty")
        res = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                             universal_newlines=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return 4, None, None, "state=UNKNOWN reason=usage check could not run (%s)" % exc
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    line = lines[-1] if lines else "state=UNKNOWN reason=no output"
    code = res.returncode if res.returncode in USAGE_STATES else 4
    return (code,) + usage_values(line) + (line,)


def usage_values(line):
    """-> (5h, 7d) ints from a usage line (None when absent or not an integer)."""
    values = {}
    for key in ("5h", "7d"):
        m = re.search(r"(?:^|\s)%s=(\S+)" % key, line)
        values[key] = int(m.group(1)) if m and re.match(r"^\d+$", m.group(1)) else None
    return values["5h"], values["7d"]


def usage_field(line, key):
    m = re.search(r"(?:^|\s)%s=(\S+)" % re.escape(key), line)
    return m.group(1) if m else "?"


def wait_status(task_dir):
    """The spawn's `usage wait:` line."""
    from . import usage
    try:
        pid = usage.wait_armed(usage.state_dir(), usage.wait_key(*task_names(task_dir)))
    except Exception:
        pid = None
    if pid:
        return "usage wait: armed (pid %d)" % pid
    return "usage wait: not armed — run in background: orch -t %s/%s usage wait" % task_names(task_dir)


def cmd_spawn(ns, extra):
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    role = ns.role
    if not ROLE_RE.match(role):
        raise OrchError("bad role %r" % role, 1)
    if not AGENT_RE.match(ns.agent):
        raise OrchError("bad --agent %r" % ns.agent, 1)
    if not TIER_RE.match(ns.tier):
        raise OrchError("--tier must look like sonnet/high, got %r" % ns.tier, 1)
    esc = ns.escalation if ns.escalation not in (None, "none") else None
    if ns.escalation not in (None, "none", "proactive", "post-failure"):
        raise OrchError("--escalation must be none, proactive or post-failure", 1)
    if ns.reason is not None and not ns.tradeoff:
        raise OrchError("--reason needs --tradeoff (the tier_choice line records both)", 1)
    if esc:
        for flag, val in (("--from", ns.from_tier), ("--trigger", ns.trigger), ("--tradeoff", ns.tradeoff)):
            if not val:
                raise OrchError("--escalation %s needs --from, --trigger and --tradeoff (%s missing)" % (esc, flag), 1)
        if not TIER_RE.match(ns.from_tier):
            raise OrchError("--from must look like sonnet/high, got %r" % ns.from_tier, 1)
    alts = [a.strip() for a in (ns.alt or "").split(",") if a.strip()]
    steps = None
    if ns.steps is not None and ns.steps.strip():
        items = [x.strip() for x in ns.steps.split(",") if x.strip()]
        steps = [int(x) for x in items] if items and all(re.match(r"^-?\d+$", x) for x in items) else items
    code, u5, u7, usage_line = run_usage()
    ts = now()
    repo, slug = task_names(task_dir)
    note = cli.one_line(ns.note) if ns.note else ""
    schema = jr.load_schema()
    with TaskLock(task_dir):
        status = status_or_none(task_dir)
        program, auto_ms = program_of(status)
        if code == 2:
            refused = "spawn refused: usage STOP (5h %s%%) est=%s line=%s" % (
                "?" if u5 is None else u5, usage_field(usage_line, "est"), usage_field(usage_line, "line"))
            cli.append_log(task_dir, cli.log_line(ts, "orchestrator", refused))
            raise OrchError("%s — %s" % (refused, usage_line), 2)

        def rec(kind, pairs):
            r, _ = jr.build_record(schema, kind, pairs, ts, repo, slug, program, auto_ms)
            return kind, jr.encode(r)

        out = []
        if ns.reason is not None:
            out.append(rec("tier_choice", [("role", "=", role), ("tier", "=", ns.tier), ("tradeoff", "=", ns.tradeoff),
                                          ("reason", "=", ns.reason), ("alternatives", ":=", json.dumps(alts))]))
        if esc:
            out.append(rec("escalation", [("role", "=", role), ("from", "=", ns.from_tier), ("to", "=", ns.tier),
                                         ("trigger", "=", ns.trigger), ("tradeoff", "=", ns.tradeoff),
                                         ("reason", "=", ns.reason or "%s escalation: %s" % (esc, ns.trigger)),
                                         ("alternatives", ":=", json.dumps(alts))]))
        out.append(rec("spawn", [("role", "=", role), ("agent", "=", ns.agent), ("tier", "=", ns.tier),
                                 ("steps", ":=", json.dumps(steps)), ("usage_5h", ":=", json.dumps(u5)),
                                 ("usage_7d", ":=", json.dumps(u7))]))
        text = "spawned %s (%s, %s)%s%s" % (role, ns.agent, ns.tier, " steps %s" % ",".join(str(s) for s in steps) if steps else "",
                                          ": " + note if note else "")
        log = cli.log_line(ts, "orchestrator", text)
        for _, data in out:
            jr.append(os.path.join(task_dir, "journal.jsonl"), data)
        cli.append_log(task_dir, log)
    if code != 0:
        warn_stderr("usage %s: %s" % (USAGE_STATES[code], usage_line))
    print("usage: %s" % usage_line)
    print(wait_status(task_dir))
    print("journal %s" % ", ".join(k for k, _ in out))
    return 0


# -- return --------------------------------------------------------------------------------------

def last_spawn(journal, role):
    found = None
    for rec in journal:
        if kind_of(rec) == "spawn" and rec.get("role") == role:
            found = rec
    return found


def changed_files(task_dir, since):
    out = []
    for d, dirs, files in os.walk(task_dir):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(d, f), task_dir)
            if f.startswith(".") or rel in QUIET_FILES:
                continue
            try:
                if os.path.getmtime(os.path.join(d, f)) >= since:
                    out.append(rel)
            except OSError:
                continue
    return out


def contract_blocks(notes_path, wanted):
    """Every `contract-check: step <N>` block (the line and the list lines after it) for the wanted steps
    (None = all), verbatim. Returns (blocks, steps seen)."""
    try:
        with open(notes_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return [], set()
    head = re.compile(r"^\s*(?:#+\s*|[-*]\s+)?contract-check:\s*step\s+([A-Za-z0-9._-]*[A-Za-z0-9])")
    blocks, seen, i = [], set(), 0
    while i < len(lines):
        m = head.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() and not lines[j].startswith("#") and not head.match(lines[j]):
            j += 1
        seen.add(m.group(1))
        if wanted is None or m.group(1) in wanted:
            blocks.append("\n".join(lines[i:j]))
        i = j
    return blocks, seen


def cmd_return(ns, extra):
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    role = ns.role
    if not ROLE_RE.match(role):
        raise OrchError("bad role %r" % role, 1)
    if ns.outcome is not None and ns.outcome not in OUTCOMES:
        raise OrchError("--outcome must be one of: %s" % " ".join(OUTCOMES), 1)
    for flag, val in (("--tokens", ns.tokens), ("--tool-uses", ns.tool_uses), ("--duration-ms", ns.duration_ms)):
        if val is not None and val < 0:
            raise OrchError("%s must be >= 0" % flag, 1)
    row = None
    status = status_or_none(task_dir)
    if status:
        try:
            row = dict(status.roles()).get(role)
        except OrchError:
            row = None
    outcome = ns.outcome
    if outcome is None:
        state = row["state"] if row else None
        if state in OUTCOMES[:3]:
            outcome = state
        elif row is None:
            raise OrchError("no %s row in status.md to read the outcome from; pass --outcome" % role, 1)
        else:
            raise OrchError("row not final — did it checkpoint? (%s is %s; pass --outcome to override)" % (role, state or "empty"), 1)
    ts = now()
    repo, slug = task_names(task_dir)
    schema = jr.load_schema()
    journal = read_journal(task_dir)
    spawn = last_spawn(journal, role)
    with TaskLock(task_dir):
        program, auto_ms = program_of(status_or_none(task_dir))
        pairs = [("role", "=", role), ("agent", ":=", json.dumps(spawn.get("agent") if spawn else None)),
                 ("outcome", "=", outcome),
                 ("tokens", ":=", json.dumps(ns.tokens)), ("tool_uses", ":=", json.dumps(ns.tool_uses)),
                 ("duration_ms", ":=", json.dumps(ns.duration_ms))]
        r, _ = jr.build_record(schema, "return", pairs, ts, repo, slug, program, auto_ms)
        jr.append(os.path.join(task_dir, "journal.jsonl"), jr.encode(r))
    if spawn is None:
        warn_stderr("no earlier spawn line for %s (agent recorded as null)" % role)
    print("return %s: %s" % (role, outcome))
    if row:
        print("row: %s | step: %s | note: %s" % (row.get("state", ""), row.get("step", "") or "-", row.get("notes", "") or "-"))
        m = re.search(r"\bneed:\s*(.*)$", row.get("notes", ""))
        if outcome == "blocked" and m:
            print("need: %s" % m.group(1).strip())
    since = ts_epoch(spawn.get("ts")) if spawn else None
    if since is not None:
        files = changed_files(task_dir, since)
        if files:
            shown = files[:MAX_CHANGED]
            print("changed since spawn (%s): %s%s" % (spawn["ts"], ", ".join(shown),
                                                       " (+%d more)" % (len(files) - len(shown)) if len(files) > len(shown) else ""))
        else:
            print("changed since spawn (%s): nothing in the task dir" % spawn["ts"])
    if spawn and str(spawn.get("tier", "")).startswith("haiku/"):
        steps = spawn.get("steps")
        wanted = set(str(s) for s in steps) if isinstance(steps, list) and steps else None
        blocks, seen = contract_blocks(os.path.join(task_dir, "notes.md"), wanted)
        for b in blocks:
            print(b)
        if wanted:
            for s in sorted(wanted - seen):
                print("no contract-check block in notes.md for step %s" % s)
        elif not blocks:
            print("no contract-check block in notes.md")
    return 0


# -- end -----------------------------------------------------------------------------------------

def norm_text(text):
    return re.sub(r"\s+", " ", text).strip()


def retro_is_template(task_dir):
    path = os.path.join(task_dir, "retro.md")
    if not os.path.exists(path):
        return "retro.md is missing"
    with open(os.path.join(TEMPLATES_DIR, "retro.md"), encoding="utf-8") as fh:
        tpl = fh.read().replace("<task-slug>", task_names(task_dir)[1])
    with open(path, encoding="utf-8", errors="replace") as fh:
        if norm_text(fh.read()) == norm_text(tpl):
            return "retro.md is still the template"
    return None


def rounds_of(status):
    m = re.match(r"^\s*(\d+)\s*/", (status.get("review_round") or "") if status else "")
    return int(m.group(1)) if m else None


def digest(task_dir, outcome, rounds, final):
    """The `orch end` output: at most 25 lines in all."""
    journal = read_journal(task_dir)
    out = ["ended: %s (task_state %s); review rounds %s" % (outcome, final, "?" if rounds is None else "%d/3" % rounds)]
    merges = [r for r in journal if kind_of(r) in ("auto_merge", "merge_point")]
    for r in merges[-3:]:
        if kind_of(r) == "auto_merge":
            out.append("merge (auto): %s into %s; revert: %s" % (str(r.get("sha"))[:12], r.get("base"), r.get("revert")))
        else:
            line = "merge point%s: %s" % (" M%s" % r["milestone"] if r.get("milestone") is not None else "", str(r.get("sha"))[:12])
            if "backmerge" in r and r.get("backmerge") is None:
                line += " — backmerge: null: task branch not caught up — next merge point will ask"
            elif r.get("tree_equal") is False:
                line += " — back-merge changed the tree — next review covers the difference"
            out.append(line)
    if not merges:
        out.append("merges: none journaled")
    assumed = [r for r in journal if kind_of(r) == "assumed_decision"]
    if assumed:
        out.append("assumed decisions: %d" % len(assumed))
        for r in assumed[:3]:
            out.append("  %s (%s, reversible %s)" % (cli.clip(str(r.get("decision")), 70), r.get("role"), str(r.get("reversible")).lower()))
    else:
        out.append("assumed decisions: none")
    opens = qs.open_ones(task_dir)
    out.append("open questions: %d" % len(opens))
    for q in opens[:3]:
        out.append("  Q%d (blocks %s): %s" % (q.n, q.fields.get("blocks", "all"), cli.clip(q.text, 80)))
    sec = [r for r in journal if kind_of(r) == "auto_security"]
    if sec:
        for r in sec[-3:]:
            areas = r.get("areas") if isinstance(r.get("areas"), list) else []
            out.append("security: %s (rule %s; areas %s)" % ("ran" if r.get("ran") else "skipped", r.get("rule"), ", ".join(map(str, areas)) or "-"))
    else:
        out.append("security: no auto_security line")
    returns = [r for r in journal if kind_of(r) == "return"]
    toks = [r["tokens"] for r in returns if isinstance(r.get("tokens"), int) and not isinstance(r.get("tokens"), bool)]
    out.append("agent tokens reported: %d over %d returns (reported context sizes, not spend)" % (sum(toks), len(returns)))
    spawns = [r for r in journal if kind_of(r) == "spawn"]
    if spawns:
        def pair(key):
            a, b = spawns[0].get(key), spawns[-1].get(key)
            return "%s%%→%s%%" % ("?" if a is None else a, "?" if b is None else b)
        out.append("usage at first→last spawn: 5h %s, 7d %s" % (pair("usage_5h"), pair("usage_7d")))
    else:
        out.append("usage: no spawn lines")
    return out[:25]


def cmd_end(ns, extra):
    cli.require_write_allowed()
    task_dir = cli.get_task(ns)
    outcome = ns.outcome
    if outcome not in END_OUTCOMES:
        raise OrchError("OUTCOME must be one of: %s" % " | ".join(END_OUTCOMES), 1)
    problem = retro_is_template(task_dir)
    if problem and not ns.force:
        raise OrchError("%s — write the retro first (or --force)" % problem, 1)
    ts = now()
    repo, slug = task_names(task_dir)
    schema = jr.load_schema()
    enum = st.task_state_enum()
    final = "abandoned" if outcome == "abandoned" else "done"
    with TaskLock(task_dir):
        if not ns.force and any(kind_of(r) == "task_end" for r in read_journal(task_dir)):
            raise OrchError("task already ended (task_end is in the journal); --force to write another", 2)
        status = status_or_none(task_dir)
        program, auto_ms = program_of(status)
        rounds = rounds_of(status)
        r, _ = jr.build_record(schema, "task_end", [("outcome", "=", outcome), ("rounds", ":=", json.dumps(rounds))],
                               ts, repo, slug, program, auto_ms)
        data = jr.encode(r)
        changed = []
        if status is not None:
            changed = cli.apply_sets(status, ["task_state=%s" % final], False, enum)
        jr.append(os.path.join(task_dir, "journal.jsonl"), data)
        if changed:
            write_atomic(cli.status_path(task_dir), status.render())
        cli.append_log(task_dir, cli.log_line(ts, "orchestrator", "task ended: %s" % outcome))
    if changed:
        refresh_index(warn_stderr)
    for line in digest(task_dir, outcome, rounds, final if status is not None else "no status.md"):
        print(line)
    return 0


def register(add):
    sp = add("spawn", cmd_spawn, "usage pre-flight + tier_choice/escalation/spawn journal lines")
    sp.add_argument("role")
    sp.add_argument("--agent", required=True)
    sp.add_argument("--tier", required=True, help="<model>/<effort>")
    sp.add_argument("--steps", default=None, help="comma list of step ids")
    sp.add_argument("--reason", default=None)
    sp.add_argument("--alt", default=None, help="comma list of alternatives considered")
    sp.add_argument("--tradeoff", default=None)
    sp.add_argument("--escalation", default=None, help="none | proactive | post-failure")
    sp.add_argument("--from", dest="from_tier", default=None)
    sp.add_argument("--trigger", default=None)
    sp.add_argument("--note", default=None)

    sp = add("return", cmd_return, "journal a subagent return and print what it left behind")
    sp.add_argument("role")
    sp.add_argument("--outcome", default=None, help="done | blocked | failed | stopped (default: the role's row)")
    sp.add_argument("--tokens", type=int, default=None)
    sp.add_argument("--tool-uses", dest="tool_uses", type=int, default=None)
    sp.add_argument("--duration-ms", dest="duration_ms", type=int, default=None)

    sp = add("end", cmd_end, "task_end line, final state, digest")
    sp.add_argument("outcome")
    sp.add_argument("--force", action="store_true")

    from . import usage  # here, not at the top: usage's handler imports cli lazily too
    usage.register(add)
