"""status.md as a list of lines: edit only the target lines, write everything else back byte for byte."""
import os
import re
from collections import namedtuple

from .paths import OrchError, TEMPLATES_DIR

ROLE_STATES = ("pending", "in_progress", "waiting", "blocked", "failed", "done", "n/a", "not_offered",
               "declined", "skipped")

VALUE_RULE_FIELDS = ("repo", "task_dir", "worktree", "base_branch", "task_branch", "reviewed_through",
                     "merged_through")
PATH_FIELDS = ("repo", "task_dir", "worktree")
WORKTREE_LITERALS = ("not yet created", "none", "removed after merge")

FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
FIELD_RE = re.compile(r"^- ([A-Za-z_][A-Za-z0-9_]*): ?(.*)$", re.S)

COLUMN_NAMES = {
    "role": "role", "state": "state",
    "timestamp (utc)": "ts", "timestamp": "ts", "updated (utc)": "ts", "updated": "ts",
    "last completed step": "step",
    "notes": "notes", "note": "notes",
}

# orch-index.sh's classification (same rules, same quirks): see bin/orch-index.sh.
INDEX_ENUM = {"planning", "awaiting_approval", "implementing", "reviewing", "awaiting_merge",
              "paused_usage_limit", "done", "abandoned"}
INDEX_TERMINAL = {"done", "abandoned", "merged"}

Clean = namedtuple("Clean", "value missing")


def split_ending(line):
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def split_lines(text):
    pieces = text.split("\n")
    if pieces and pieces[-1] == "":
        pieces.pop()
        return [p + "\n" for p in pieces]
    return [p + "\n" for p in pieces[:-1]] + [pieces[-1]] if pieces else []


def single_line(value, what):
    if "\n" in value or "\r" in value:
        raise OrchError("%s must be a single line" % what, 1)
    return value.strip()


def clean_cell(value):
    """Table-cell value: '|' becomes '/', newlines become spaces."""
    value = re.sub(r"\r\n|\r|\n", " ", value).replace("|", "/")
    return value.strip()


def task_state_enum(templates_dir=None):
    """The enum on the template's `- task_state: a | b | …` line."""
    path = os.path.join(templates_dir or TEMPLATES_DIR, "status.md")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^- task_state:\s*(.*?)\s*$", line)
                if m:
                    vals = [v.strip() for v in m.group(1).split("|") if v.strip()]
                    if vals:
                        return vals
    except OSError as exc:
        raise OrchError("cannot read status template %s: %s" % (path, exc), 3)
    raise OrchError("status template %s has no task_state line" % path, 3)


def clean_value(name, raw):
    """Value rule for path-like fields (step 3, P8). Returns Clean(value, missing)."""
    if raw is None:
        return Clean("unknown", False)
    v = raw.strip()
    i = v.find(" (")
    if i != -1:
        v = v[:i]
    v = v.strip()
    if not v or "|" in v or "<" in v or v == "unknown":
        return Clean("unknown", False)   # a literal `unknown` (what `orch migrate` writes) reads like an absent field
    if name == "worktree" and v in WORKTREE_LITERALS:
        return Clean(v, False)
    if v == "~" or v.startswith("~/"):
        v = os.path.expanduser(v)
    missing = name in PATH_FIELDS and not os.path.exists(v)
    return Clean(v, missing)


def show_value(name, raw):
    if name in VALUE_RULE_FIELDS:
        c = clean_value(name, raw)
        return c.value + (" (missing)" if c.missing else "")
    return "unknown" if raw is None or not raw.strip() else raw.strip()


def classify(text, task_dir):
    """Task state exactly as orch-index.sh derives it (text read with errors=replace, universal newlines)."""
    def field(k):
        m = re.search(r"^- %s:\s*(.*)$" % k, text, re.M)
        return m.group(1).strip() if m else ""
    raw = field("task_state")
    first = re.sub(r"[^a-z_]", "", (raw.split() or [""])[0].lower()) if raw and "|" not in raw else ""
    if first in INDEX_ENUM or first in INDEX_TERMINAL:
        state = first
    elif first.startswith(("done", "merged")):
        state = "done"
    elif first:
        state = first
    elif os.path.exists(os.path.join(task_dir, "retro.md")):
        state = "done"
    else:
        m = re.search(r"^\|\s*orchestrator\s*\|\s*(\w+)", text, re.M)
        state = "done" if m and m.group(1) == "done" else "unknown"
    if state == "unknown" and re.match(r"removed", field("worktree")):
        state = "done"
    return state


def read_text_like_index(path):
    with open(path, errors="replace") as fh:
        return fh.read()


class Layout(object):
    def __init__(self, header, sep, last, cols, ncols):
        self.header, self.sep, self.last, self.cols, self.ncols = header, sep, last, cols, ncols


class Status(object):
    def __init__(self, data=b"", path=None):
        self.path = path
        self.lines = split_lines(data.decode("utf-8", "surrogateescape"))
        self.dirty = False

    @classmethod
    def load(cls, path):
        try:
            with open(path, "rb") as fh:
                return cls(fh.read(), path)
        except OSError as exc:
            raise OrchError("cannot read %s: %s" % (path, exc), 1)

    def render(self):
        return "".join(self.lines).encode("utf-8", "surrogateescape")

    # -- fixed fields -------------------------------------------------
    @staticmethod
    def _cells(line):
        body, ending = split_ending(line)
        s = body.strip()
        if len(s) < 2 or not (s.startswith("|") and s.endswith("|")):
            return None
        lead = body[:len(body) - len(body.lstrip())]
        trail = body[len(body.rstrip()):]
        return lead, s[1:-1].split("|"), trail, ending

    def _header_index(self):
        for i, line in enumerate(self.lines):
            c = self._cells(line)
            if c and c[1][0].strip().lower() == "role" and i + 1 < len(self.lines):
                nxt = self._cells(self.lines[i + 1])
                if nxt and all(re.match(r"^\s*:?-+:?\s*$", x) for x in nxt[1]):
                    return i
        return None

    def _head_end(self):
        h = self._header_index()
        return len(self.lines) if h is None else h

    def fields(self):
        """[(line index, name, raw value)] in the fixed-field block (before the role table)."""
        out = []
        for i in range(self._head_end()):
            body, _ = split_ending(self.lines[i])
            m = FIELD_RE.match(body)
            if m:
                out.append((i, m.group(1), m.group(2)))
        return out

    def get(self, name):
        for _, n, v in self.fields():
            if n == name:
                return v.strip()
        return None

    def set_field(self, name, value, add=False):
        """Returns True if a line changed. Validation of enum values is the caller's job."""
        if not FIELD_NAME_RE.match(name):
            raise OrchError("bad field name %r" % name, 1)
        value = single_line(value, "value of %s" % name)
        fields = self.fields()
        for i, n, v in fields:
            if n == name:
                if v.strip() == value:
                    return False
                _, ending = split_ending(self.lines[i])
                self.lines[i] = "- %s: %s%s" % (name, value, ending)
                self.dirty = True
                return True
        if not add:
            raise OrchError("unknown field %r (use --add to create it)" % name, 1)
        new = "- %s: %s" % (name, value)
        if fields:
            at = fields[-1][0] + 1
        else:
            at = 1
            while at < len(self.lines) and not self.lines[at].strip():
                at += 1
        if at > 0 and not self.lines[at - 1].endswith("\n"):
            self.lines[at - 1] += "\n"
        self.lines.insert(at, new + "\n")
        self.dirty = True
        return True

    # -- role table ---------------------------------------------------
    def layout(self):
        h = self._header_index()
        if h is None:
            raise OrchError("status.md has no `| role |` table (unknown layout); nothing written", 1)
        _, hcells, _, _ = self._cells(self.lines[h])
        cols = {}
        for idx, cell in enumerate(hcells):
            key = COLUMN_NAMES.get(cell.strip().lower())
            if key and key not in cols:
                cols[key] = idx
        if "role" not in cols or "state" not in cols:
            raise OrchError("role table has no role/state column (unknown layout); nothing written", 1)
        last = h + 1
        i = h + 2
        while i < len(self.lines) and self.lines[i].lstrip().startswith("|"):
            last = i
            i += 1
        return Layout(h, h + 1, last, cols, len(hcells))

    def find_role(self, role):
        lay = self.layout()
        for i in range(lay.sep + 1, lay.last + 1):
            c = self._cells(self.lines[i])
            if c and len(c[1]) == lay.ncols and c[1][lay.cols["role"]].strip() == role:
                return i
        return None

    def roles(self):
        """[(role, {state, ts, step, notes})] for rows whose cell count matches the header."""
        lay = self.layout()
        out = []
        for i in range(lay.sep + 1, lay.last + 1):
            c = self._cells(self.lines[i])
            if not c or len(c[1]) != lay.ncols:
                continue
            row = {k: c[1][idx].strip() for k, idx in lay.cols.items()}
            out.append((row["role"], row))
        return out

    def set_role(self, role, state=None, ts=None, step=None, note=None, validate=True):
        """Update (or insert) a role row. ts/step/note None = leave alone. Returns (changed, skipped_columns)."""
        if validate and state is not None and state not in ROLE_STATES:
            raise OrchError("bad state %r; one of: %s" % (state, " ".join(ROLE_STATES)), 1)
        lay = self.layout()
        want = {"state": state, "ts": ts, "step": step, "notes": note}
        want = {k: clean_cell(v) for k, v in want.items() if v is not None}
        skipped = sorted(k for k in want if k not in lay.cols)
        want = {k: v for k, v in want.items() if k in lay.cols}
        idx = self.find_role(role)
        if idx is None:
            # a row with that name but the wrong cell count is a layout problem, not a missing row
            for i in range(lay.sep + 1, lay.last + 1):
                c = self._cells(self.lines[i])
                if c and len(c[1]) > lay.cols["role"] and c[1][lay.cols["role"]].strip() == role:
                    raise OrchError("row %r has %d cells but the header has %d; nothing written"
                                    % (role, len(c[1]), lay.ncols), 1)
            if validate and not re.match(r"^(orchestrator|planner|implementer|director|plan-reviewer|code-reviewer|"
                                         r"security-reviewer)(:[A-Za-z0-9._-]+)?$", role):
                raise OrchError("cannot insert a row for unknown role %r" % role, 1)
            hl, _, ht, _ = self._cells(self.lines[lay.header])
            cells = [" "] * lay.ncols
            cells[lay.cols["role"]] = " %s " % clean_cell(role)
            for k, v in want.items():
                cells[lay.cols[k]] = " %s " % v if v else " "
            last = self.lines[lay.last]
            _, ending = split_ending(last)
            if not ending:
                # the table ends the file without a newline: give the old last row this file's line ending
                self.lines[lay.last] = last + (split_ending(self.lines[lay.header])[1] or "\n")
            self.lines.insert(lay.last + 1, "%s|%s|%s%s" % (hl, "|".join(cells), ht, ending))
            self.dirty = True
            return True, skipped
        lead, cells, trail, ending = self._cells(self.lines[idx])
        changed = False
        for k, v in want.items():
            col = lay.cols[k]
            if cells[col].strip() != v:
                cells[col] = " %s " % v if v else " "
                changed = True
        if changed:
            self.lines[idx] = "%s|%s|%s%s" % (lead, "|".join(cells), trail, ending)
            self.dirty = True
        return changed, skipped
