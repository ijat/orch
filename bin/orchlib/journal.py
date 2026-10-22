"""journal.jsonl: schema read at run time from templates/journal.md, typed values, one-write appends."""
import json
import os
import re

from .paths import OrchError, TEMPLATES_DIR, append_bytes, ends_with_newline

RESERVED = ("ts", "repo", "task", "kind")
KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
PAIR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(:?=)(.*)$", re.S)
INT_RE = re.compile(r"^-?\d+$")


class Schema(object):
    def __init__(self):
        self.kinds = {}   # kind -> {"fields": [...], "choice": bool}
        self.types = {}   # (kind or "*", field) -> "integer" | "boolean" | "array"

    def type_of(self, kind, field):
        return self.types.get((kind, field)) or self.types.get(("*", field))


def _split_top(text):
    """Split a fields cell on ',' and ';' outside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _parse_fields(cell, kinds):
    """-> {kind: [fields]}. Only the first backticked name of a segment counts (backticked values inside
    parentheses are not fields); `end adds `a`, `b`` limits the following fields to the kind ending `_end`."""
    out = dict((k, []) for k in kinds)
    scope = None
    for seg in _split_top(cell):
        m = re.search(r"`([a-z_][a-z0-9_]*)`", seg)
        if not m:
            continue
        adds = re.search(r"([a-z]+)\s+adds\b", seg[:m.start()])
        if adds:
            scope = adds.group(1)
        name = m.group(1)
        for k in kinds:
            if scope is None or k == scope or k.endswith("_" + scope):
                if name not in out[k]:
                    out[k].append(name)
    return out


def _expand_type_token(tok):
    m = re.match(r"^([a-z_][a-z0-9_]*|\*)\.\{([^}]*)\}$", tok)
    if m:
        return [(m.group(1), f.strip()) for f in m.group(2).split(",") if f.strip()]
    m = re.match(r"^([a-z_][a-z0-9_]*|\*)\.([a-z_][a-z0-9_]*)$", tok)
    if m:
        return [(m.group(1), m.group(2))]
    raise OrchError("journal.md Types line: cannot read %r" % tok, 3)


def load_schema(templates_dir=None):
    path = os.path.join(templates_dir or TEMPLATES_DIR, "journal.md")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError as exc:
        raise OrchError("cannot read journal schema %s: %s" % (path, exc), 3)
    schema = Schema()
    in_table = False
    for line in lines:
        if re.match(r"^\|\s*kind\s*\|\s*when\s*\|\s*extra fields\s*\|", line):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
            elif re.match(r"^\|\s*-+", line):
                continue
            else:
                parts = line.strip().strip("|").split("|")
                if len(parts) < 3:
                    continue
                kinds = re.findall(r"`([a-z_][a-z0-9_]*)`", parts[0])
                choice = "**choice**" in parts[0]
                fields = _parse_fields("|".join(parts[2:]), kinds)
                for k in kinds:
                    schema.kinds[k] = {"fields": fields[k], "choice": choice}
            continue
        m = re.match(r"^\s*[-*]\s*(integer|boolean|array)\s*:\s*(.*)$", line)
        if m:
            for tok in re.findall(r"`([^`]+)`", m.group(2)):
                for key in _expand_type_token(tok):
                    schema.types[key] = m.group(1)
    if not schema.kinds:
        raise OrchError("journal schema %s has no kind table" % path, 3)
    return schema


def _check_type(kind, key, value, t):
    ok = ((t == "integer" and isinstance(value, int) and not isinstance(value, bool))
          or (t == "boolean" and isinstance(value, bool))
          or (t == "array" and isinstance(value, list)))
    if not ok:
        raise OrchError("%s.%s must be %s (or null with :=null), got %s" % (kind, key, t, json.dumps(value)), 1)


def _no_constant(name):
    raise ValueError("%s is not valid JSON" % name)


def coerce(schema, kind, key, mode, raw):
    t = schema.type_of(kind, key)
    if mode == ":=":
        try:
            value = json.loads(raw, parse_constant=_no_constant)
        except ValueError as exc:
            raise OrchError("%s:=%s is not valid JSON (%s)" % (key, raw, exc), 1)
        if t and value is not None:
            _check_type(kind, key, value, t)
        return value
    if t == "integer":
        if not INT_RE.match(raw):
            raise OrchError("%s.%s must be an integer, got %r (write null as %s:=null)" % (kind, key, raw, key), 1)
        return int(raw)
    if t == "boolean":
        if raw not in ("true", "false"):
            raise OrchError("%s.%s must be true or false, got %r" % (kind, key, raw), 1)
        return raw == "true"
    if t == "array":
        items = [x.strip() for x in raw.split(",")] if raw.strip() else []
        if items and all(INT_RE.match(x) for x in items):
            return [int(x) for x in items]
        return items
    return raw


def parse_pairs(items):
    """['k=v', 'k:=json'] -> [(key, mode, raw)]."""
    out, seen = [], set()
    for it in items:
        m = PAIR_RE.match(it)
        if not m:
            raise OrchError("expected k=v or k:=json, got %r" % it, 1)
        key, mode, raw = m.group(1), m.group(2), m.group(3)
        if not KEY_RE.match(key):
            raise OrchError("bad journal key %r (lowercase letters, digits, underscore)" % key, 1)
        if key in RESERVED:
            raise OrchError("%r is set by orch (use --ts for the timestamp)" % key, 1)
        if key in seen:
            raise OrchError("duplicate key %r" % key, 1)
        seen.add(key)
        out.append((key, mode, raw))
    return out


def build_record(schema, kind, pairs, ts, repo, task, program, auto_milestone):
    """-> (dict in line order, [fields written as null]). Order: ts, repo, task, kind, [milestone],
    schema fields in table order (choice kinds: then reason, alternatives), then extras as given."""
    if kind not in schema.kinds:
        raise OrchError("unknown journal kind %r; known: %s" % (kind, ", ".join(sorted(schema.kinds))), 1)
    spec = schema.kinds[kind]
    given = {}
    order = []
    for key, mode, raw in pairs:
        given[key] = coerce(schema, kind, key, mode, raw)
        order.append(key)
    fields = list(spec["fields"])
    if spec["choice"]:
        for extra in ("reason", "alternatives"):
            if extra not in fields:
                fields.append(extra)
        for need in ("reason", "alternatives"):
            if given.get(need) is None:
                raise OrchError("%s needs %s (the real reason; alternatives may be [])" % (kind, need), 1)
    rec = {"ts": ts, "repo": repo, "task": task, "kind": kind}
    used = set()
    missing = []
    if program and "milestone" not in fields:
        rec["milestone"] = given["milestone"] if "milestone" in given else auto_milestone
        used.add("milestone")
    for f in fields:
        if f in given:
            rec[f] = given[f]
        elif f == "milestone" and program:
            rec[f] = auto_milestone
        else:
            rec[f] = None
            missing.append(f)
        used.add(f)
    for key in order:
        if key not in used:
            rec[key] = given[key]
    return rec, missing


def encode(rec):
    try:
        text = json.dumps(rec, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        data = text.encode("utf-8")
    except (ValueError, UnicodeEncodeError) as exc:
        raise OrchError("journal line is not encodable: %s" % exc, 1)
    if json.loads(text) != rec:
        raise OrchError("journal line does not round-trip through json.loads", 3)
    return data


def append(path, data):
    prefix = b"" if ends_with_newline(path) else b"\n"
    append_bytes(path, prefix + data + b"\n")
