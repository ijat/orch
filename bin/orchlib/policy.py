"""Per-repo policy: ~/.claude/orchestrations/<repo>/policy.md (the parent dir of the task dir)."""
import os
import re
from collections import namedtuple

MERGE_VALUES = ("auto", "ask")
SECURITY_VALUES = ("rule", "always", "ask")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
LINE_RE = re.compile(r"^\s*[-*]\s*(merge|security)\s*:[ \t]*(.*?)\s*$", re.I | re.M)

Policy = namedtuple("Policy", "merge security path exists")


def policy_path(task_dir):
    return os.path.join(os.path.dirname(os.path.normpath(task_dir)), "policy.md")


def read_policy(task_dir):
    """Policy(merge, security, path, exists). A missing file or field means merge=auto, security=rule.

    Fail closed: a `merge` value other than `auto` (including template text such as `auto | ask`, a
    second `- merge:` line that disagrees, or a file that cannot be read) is returned as that value
    (`ask` for an unreadable file), never as `auto`. An unknown `security` value reads as `ask`."""
    path = policy_path(task_dir)
    if not os.path.exists(path):
        return Policy("auto", "rule", path, False)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return Policy("ask", "ask", path, True)
    text = COMMENT_RE.sub("", text)
    merge, security = [], []
    for m in LINE_RE.finditer(text):
        (merge if m.group(1).lower() == "merge" else security).append(m.group(2).strip())
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
    return Policy(merge_v, sec_v, path, True)


def merge_allowed(policy):
    return policy.merge == "auto"
