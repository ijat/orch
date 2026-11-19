"""Per-repo policy: <tasks root>/<repo>/policy.md (the parent dir of the task dir)."""
import os
import re
from collections import namedtuple

MERGE_VALUES = ("auto", "ask")
SECURITY_VALUES = ("rule", "always", "ask")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
LINE_RE = re.compile(r"^\s*[-*]\s*(merge|security|docs-only)\s*:[ \t]*(.*?)\s*$", re.I | re.M)
# Paths whose change alone needs no code review round (a check instead) and no new gate or security pass.
# Root names match only at the repo root; `**` matches nested directories.
DOCS_ONLY_DEFAULT = ("docs/**/*.md", "STATE.md", "DECISIONS.md", "CHANGELOG.md", "README.md", "AGENTS.md")
GLOB_RE = re.compile(r"^[A-Za-z0-9_.*?/+@ -]+$")

Policy = namedtuple("Policy", "merge security path exists docs_only", defaults=(DOCS_ONLY_DEFAULT,))


def policy_path(task_dir):
    return os.path.join(os.path.dirname(os.path.normpath(task_dir)), "policy.md")


def parse_docs_only(values):
    """Globs (a tuple; empty = off) from the values of the `- docs-only:` lines. No line: the default globs. A value is
    `default`, `none`, or comma-separated globs; `none`, a bad glob (empty, absolute, a `..` segment, a character
    outside letters, digits and `_.*?/+@ -`) or a bad value on any line turns the feature off. Fail closed."""
    if not values:
        return DOCS_ONLY_DEFAULT
    out = []
    for v in values:
        if v.lower() == "default":
            out.extend(DOCS_ONLY_DEFAULT)
            continue
        if v.lower() == "none":
            return ()
        for g in v.split(","):
            g = g.strip()
            if (not g or not GLOB_RE.match(g) or g.startswith("/") or ".." in g.split("/")
                    or g.lower() in ("default", "none")):
                return ()
            out.append(g)
    return tuple(dict.fromkeys(out))


def read_policy(task_dir):
    """Policy(merge, security, path, exists, docs_only). A missing file or field means merge=auto, security=rule,
    docs_only=the default globs (`- docs-only: none` or an unreadable or unparseable value: empty = off).

    Fail closed: a `merge` value other than `auto` (including template text such as `auto | ask`, a
    second `- merge:` line that disagrees, or a file that cannot be read) is returned as that value
    (`ask` for an unreadable file), never as `auto`. An unknown `security` value reads as `ask`."""
    path = policy_path(task_dir)
    if not os.path.exists(path):
        return Policy("auto", "rule", path, False, DOCS_ONLY_DEFAULT)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return Policy("ask", "ask", path, True, ())
    text = COMMENT_RE.sub("", text)
    merge, security, docs = [], [], []
    for m in LINE_RE.finditer(text):
        {"merge": merge, "security": security, "docs-only": docs}[m.group(1).lower()].append(m.group(2).strip())
    merge_v = "auto"
    for v in merge:                      # any non-auto line wins; the first one is reported
        if v != "auto":
            merge_v = v if v else "(empty)"
            break
    sec_v = "rule"
    for v in security:
        if v not in SECURITY_VALUES:
            sec_v = "ask"
            break
        if v != "rule":
            sec_v = v
    return Policy(merge_v, sec_v, path, True, parse_docs_only(docs))


def merge_allowed(policy):
    return policy.merge == "auto"
