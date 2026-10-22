"""LESSONS.md reading for briefs: dated entries, the `- role:` line, and which entries a brief carries."""
import os
import re

from .plan import fence_mask

ENTRY_RE = re.compile(r"^##[ \t]+(\d{4}-\d\d-\d\d)\b.*$")
ROLE_LINE_RE = re.compile(r"^- role:[ \t]*(.*)$", re.I)
ROLE_WORD_RE = re.compile(r"[a-z][a-z-]*")


class Entry(object):
    def __init__(self, title, roles, text):
        self.title, self.roles, self.text = title, roles, text


def read_text(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "surrogateescape")
    except OSError:
        return None


def parse_entries(text):
    """Dated `## YYYY-MM-DD — title` entries (outside code fences) with the role words of their `- role:` line."""
    lines = text.split("\n")
    mask = fence_mask(lines)
    starts = [i for i, l in enumerate(lines) if not mask[i] and ENTRY_RE.match(l)]
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        # an entry ends at the next level-1/2 heading too (the "Open" / "Promoted" sections)
        for j in range(s + 1, e):
            if not mask[j] and re.match(r"^#{1,2}[ \t]+\S", lines[j]):
                e = j
                break
        while e > s and not lines[e - 1].strip():
            e -= 1
        roles = set()
        for l in lines[s + 1:e]:
            m = ROLE_LINE_RE.match(l)
            if m:
                roles.update(ROLE_WORD_RE.findall(m.group(1).lower()))
                break
        out.append(Entry(lines[s].strip(), roles, "\n".join(lines[s:e])))
    return out


def role_words(role):
    """Role words a lesson's `- role:` line may use for this brief's role."""
    base = role.split(":")[0].lower()
    words = {base}
    if base in ("code-reviewer", "security-reviewer", "plan-reviewer"):
        words.add(base.replace("-reviewer", "") + "-reviewer")
    return words


def global_entries(path, role, mention):
    """Global entries that name `role` on their role line, or that `mention(text)` says mention a step file
    (path or basename) or the repo."""
    text = read_text(path)
    if text is None:
        return []
    words = role_words(role)
    return [e for e in parse_entries(text) if (e.roles & words) or mention(e.text)]


def repo_file(path):
    """The repo LESSONS.md in full (stripped of trailing blank lines), or None."""
    text = read_text(path)
    if text is None or not text.strip():
        return None
    return text.rstrip("\n")


def repo_mention_re(repo_name):
    """Word-bounded, case-insensitive match on the repo name."""
    if not repo_name:
        return None
    return re.compile(r"(?<![\w])" + re.escape(repo_name) + r"(?![\w])", re.I)


def lessons_section(task_dir, orch_root_dir, repo_name, role, keys, repo_full=True):
    """Text of `## Lessons`: the repo file in full, then the matching global entries."""
    parts = []
    rname = repo_mention_re(repo_name)

    def mention(text):
        return bool((keys.re and keys.re.search(text)) or (rname and rname.search(text)))

    repo_path = os.path.join(os.path.dirname(task_dir), "LESSONS.md")
    glob_path = os.path.join(orch_root_dir, "LESSONS.md")
    if repo_full:
        body = repo_file(repo_path)
        if body:
            parts.append("### Repo lessons (%s, in full)\n\n%s" % (repo_path, body))
    entries = []
    if os.path.realpath(glob_path) != os.path.realpath(repo_path):
        entries = global_entries(glob_path, role, mention)
    if entries:
        parts.append("### Global lessons that apply (%s)\n\n%s" % (glob_path, "\n\n".join(e.text for e in entries)))
    return "\n\n".join(parts)
