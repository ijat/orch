"""Tier facts and the escalation basis, for `orch hook tier` (tierguard.py).

Pure functions plus the readers they need: default tiers, tier comparison, the `basis:` line, the token table
(token -> roles -> highest tier allowed) and the lookup of a user's quote in the session transcript, in a CLAUDE.md,
in a lesson file or in a pane answer. Nothing here prints or decides; tierguard.py turns a `Verdict` into the hook's
answer. Python 3 standard library only.

The transcript is untrusted input: the spawn prompt is written by the model, and the transcript holds text of every
kind (tool results, skill bodies, agent hand-backs, memory). Only the sources listed in `user_texts` count as the
user's words."""
import json
import os
import re
import time
import unicodedata
from collections import namedtuple

from .paths import agent_key, orch_root

MODELS = ("haiku", "sonnet", "opus", "fable", "mythos")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# agent key -> default (model, effort): TIERS.md's tier table and the `model:`/`effort:` of plugin/agents/*.md
# (tests pin both). The mythos variant is not here: it is always above the security reviewer's default.
DEFAULTS = {
    "orch-planner": ("opus", "high"),
    "orch-implementer": ("sonnet", "high"),
    "orch-code-reviewer": ("opus", "high"),
    "orch-security-reviewer": ("opus", "high"),
    "orch-plan-reviewer": ("sonnet", "high"),
    "orch-director": ("opus", "high"),
    "orch-init": ("sonnet", "high"),
    "orch-explorer": ("haiku", "low"),
    "orch-executor": ("haiku", "low"),
    "orch-graph": ("haiku", "low"),
}
MYTHOS = "orch-security-reviewer-mythos"
MYTHOS_BASE = "orch-security-reviewer"       # its default is the security reviewer's

IMPL = "orch-implementer"
PLAN = "orch-planner"
CODE = "orch-code-reviewer"
SEC = "orch-security-reviewer"
PREV = "orch-plan-reviewer"
DIR = "orch-director"
INIT = "orch-init"
_FABLE_ROLES = (IMPL, CODE, SEC, PREV, DIR)

_O_MED, _O_HIGH, _O_XHIGH, _O_MAX = ("opus", "medium"), ("opus", "high"), ("opus", "xhigh"), ("opus", "max")
_F_HIGH = ("fable", "high")

# (kind, token) -> {role: highest tier the token allows}. One row per route TIERS.md allows. `user`, `lesson` and
# `claude-md` take any role and any tier and are not here.
TOKENS = {
    ("signal", "flag"): {IMPL: _O_MED},
    ("signal", "unknown_cause"): {IMPL: _O_MED},
    ("signal", "refusal"): {IMPL: _O_MED},
    ("signal", "lite"): {IMPL: _O_MED},
    ("signal", "boundary_declined"): {IMPL: _O_HIGH},
    ("signal", "lite_edge"): {IMPL: _O_HIGH},
    ("failure", "class_repeat"): {IMPL: _O_MED},
    ("failure", "stuck"): {IMPL: _O_MED},
    ("failure", "diligence"): {IMPL: _O_HIGH},
    ("failure", "ability"): {IMPL: _O_HIGH},
    ("failure", "fix_cycle"): {IMPL: _O_MAX},
    ("failure", "prior_tier_failed"): {IMPL: _O_MAX, PLAN: _F_HIGH},
    ("failure", "kept_rung"): {IMPL: _F_HIGH},
    ("failure", "opus_max_failed"): {r: _F_HIGH for r in _FABLE_ROLES},
    ("failure", "need_escalate"): {PLAN: _F_HIGH},
    ("failure", "premise_broke"): {PLAN: _F_HIGH},
    ("rule", "shape_roadmap"): {PLAN: _F_HIGH},
    ("rule", "large_diff"): {CODE: _O_XHIGH, SEC: _O_XHIGH},
    ("rule", "sensitive_diff"): {CODE: _O_XHIGH, SEC: _O_XHIGH},
    ("rule", "sensitive_milestone"): {PREV: _O_MED},
    ("rule", "no_reference"): {INIT: _O_MED},
    ("rule", "verdict_reversed"): {DIR: _O_MAX},
    ("rule", "capability"): {r: _F_HIGH for r in _FABLE_ROLES},
    ("rule", "measured"): {r: _F_HIGH for r in _FABLE_ROLES},
    ("rule", "urgency"): {r: _F_HIGH for r in _FABLE_ROLES},
}
KINDS = ("user", "signal", "rule", "failure", "lesson", "claude-md")
FREE_KINDS = ("user", "lesson", "claude-md")     # any role, any tier

QUOTE_MIN = 12
ASK_MIN = 12
RETRIES = 3
RETRY_WAIT = 0.5
SLEEP = time.sleep                               # tests replace it
BASIS_CLIP = 300
FILE_MAX = 4 * 1024 * 1024                       # a CLAUDE.md read stops here

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
BASIS_LINE_RE = re.compile(r"(?m)^basis:[ \t]*(.*?)[ \t]*$")
SESSION_TAIL_RE = re.compile(r"\|[ \t]*session ([0-9a-fA-F-]{36})[ \t]*$")
QREF_TAIL_RE = re.compile(r"\|[ \t]*Q(\d+) ([^/|]+)/([^/|]+?)[ \t]*$")
TOKEN_RE = re.compile(r"^[a-z][a-z_]*$")
IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
LESSON_RE = re.compile(r"^(global|[A-Za-z0-9][A-Za-z0-9._ -]*)/(\d{4}-\d{2}-\d{2}-[A-Za-z0-9][A-Za-z0-9._-]*)$")
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?(?:</system-reminder>|\Z)", re.S)
QUOTE_MAP = {ord(c): '"' for c in "“”„‟«»"}
QUOTE_MAP.update({ord(c): "'" for c in "‘’‚‛"})

Basis = namedtuple("Basis", "kind token ident quote qref session path name line")
Verdict = namedtuple("Verdict", "allow code reason found")


class BasisError(Exception):
    """A basis line that does not follow one of the six forms."""


def family(model):
    """haiku / sonnet / opus / fable / mythos from an alias or a full model ID, case-insensitively; the highest
    family named wins, so a mixed string is never read as a cheaper one. None for anything else."""
    if not isinstance(model, str):
        return None
    s = model.strip().lower()
    best = None
    for i, fam in enumerate(MODELS):
        if fam in s:
            best = fam
    return best


def effort_of(effort):
    if not isinstance(effort, str):
        return None
    e = effort.strip().lower()
    return e if e in EFFORTS else None


def rank(tier):
    """(model index, effort index): a tier compares by model first, then effort."""
    return (MODELS.index(tier[0]), EFFORTS.index(tier[1]))


def default_of(key):
    return DEFAULTS.get(MYTHOS_BASE if key == MYTHOS else key)


def checked(key):
    """True for an orch role the basis rule covers (every role with a default tier, the mythos variant included)."""
    return key == MYTHOS or key in DEFAULTS


def spawn_tier(key, model, effort):
    """(model family, effort) the spawn runs at, or None parts for an unknown model or effort string. A `model` or
    `effort` not given means the agent's own."""
    own_model, own_effort = default_of(key)
    if key == MYTHOS:
        own_model = "mythos"
    m = family(model) if model is not None else own_model
    e = effort_of(effort) if effort is not None else own_effort
    return m, e


def above_default(key, model, effort):
    """True when the spawn runs above the role's default (the mythos variant always does); an unknown model or
    effort string counts as above."""
    if key == MYTHOS:
        return True
    m, e = spawn_tier(key, model, effort)
    if m is None or e is None:
        return True
    return rank((m, e)) > rank(default_of(key))


def fits(kind, token, key, tier):
    """ok | unknown-token | wrong-role | above-ceiling, for a signal, failure or rule token."""
    roles = TOKENS.get((kind, token))
    if roles is None:
        return "unknown-token"
    if key not in roles:
        return "wrong-role"
    m, e = tier
    if m is None or e is None:
        return "above-ceiling"
    return "ok" if rank((m, e)) <= rank(roles[key]) else "above-ceiling"


# ---- the basis line -------------------------------------------------------------------------------------------

def basis_line(prompt):
    """The first line of the prompt that starts with `basis:`, or None."""
    if not isinstance(prompt, str):
        return None
    m = BASIS_LINE_RE.search(prompt)
    return None if m is None else m.group(0)


def _unquote(text):
    text = text.strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        raise BasisError('the quote must be wrapped in straight double quotes: "..."')
    inner = text[1:-1].strip()
    if not inner:
        raise BasisError("the quote is empty")
    return inner


def parse_basis(prompt):
    """Basis for the first `basis:` line of the prompt; None when there is none; BasisError when it is malformed."""
    m = BASIS_LINE_RE.search(prompt) if isinstance(prompt, str) else None
    if m is None:
        return None
    line = m.group(0)
    body = m.group(1)
    kind, bar, rest = body.partition("|")
    kind = kind.strip().lower()
    rest = rest.strip()
    if kind not in KINDS:
        raise BasisError("unknown kind %r (kinds: %s)" % (kind[:30], ", ".join(KINDS)))
    if not bar or not rest:
        raise BasisError("kind %r needs a value after `|`" % kind)
    token = ident = quote = qref = session = path = name = None
    if kind == "user":
        for _ in range(2):          # `| Q<n> <repo>/<slug>` and `| session <uuid>`, peeled from the end, either order
            sm = SESSION_TAIL_RE.search(rest)
            if sm and session is None:
                if not UUID_RE.match(sm.group(1)):
                    raise BasisError("session is not a UUID")
                session = sm.group(1).lower()
                rest = rest[:sm.start()].rstrip()
                continue
            qm = QREF_TAIL_RE.search(rest)
            if qm and qref is None:
                qref = (int(qm.group(1)), qm.group(2).strip(), qm.group(3).strip())
                rest = rest[:qm.start()].rstrip()
                continue
            break
        quote = _unquote(rest)
    elif kind == "claude-md":
        path, bar2, tail = rest.partition("|")
        path = path.strip()
        if not bar2 or not path:
            raise BasisError('claude-md needs `<path> | "<quote>"`')
        quote = _unquote(tail)
    elif kind == "lesson":
        lm = LESSON_RE.match(rest)
        if not lm:
            raise BasisError("lesson needs <global|repo>/<YYYY-MM-DD-slug>")
        name = (lm.group(1), lm.group(2))
    elif kind == "signal":
        parts = [p.strip() for p in rest.split("|")]
        if len(parts) > 2 or not TOKEN_RE.match(parts[0]):
            raise BasisError("signal needs `<token>` or `<token> | <step or finding id>`")
        token = parts[0]
        if len(parts) == 2:
            if not IDENT_RE.match(parts[1]):
                raise BasisError("the step or finding id is malformed")
            ident = parts[1]
    else:                           # rule, failure
        if not TOKEN_RE.match(rest):
            raise BasisError("%s needs a single `<token>`" % kind)
        token = rest
    return Basis(kind, token, ident, quote, qref, session, path, name, line)


# ---- finding the quote ----------------------------------------------------------------------------------------

def norm(text):
    """NFKC, straight quotes, single spaces, casefold."""
    s = unicodedata.normalize("NFKC", text).translate(QUOTE_MAP)
    return " ".join(s.split()).casefold()


def fragments(quote):
    """The quote split at `...` or an ellipsis character, each part normalized, empty parts dropped."""
    return [f for f in (norm(p) for p in re.split(r"\.\.\.|…", quote)) if f]


def in_order(frags, text):
    pos = 0
    for f in frags:
        i = text.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


def strip_reminders(text):
    return SYSTEM_REMINDER_RE.sub(" ", text)


def _text_of(content):
    """The string, or the joined `text` blocks of a list; a tool_result (or any other) block never counts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
        return "\n".join(parts) if parts else None
    return None


def user_texts(path):
    """Yield (source, text) for every entry of one transcript file that carries the user's own words:
    typed (human turn, or a `-p` prompt), queued (a human message typed while the agent worked) or ask (one
    AskUserQuestion answer per yield). Nothing else is yielded: not a tool result, a skill body, a hand-back, a
    plugin prompt, a peer or task-notification message, a compact summary or a memory attachment."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"user"' not in line and '"queued_command"' not in line:
                continue
            if '"tool_result"' in line and '"answers"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("isSidechain") or e.get("isMeta") or e.get("isCompactSummary"):
                continue
            kind = e.get("type")
            if kind == "attachment":
                a = e.get("attachment")
                if not isinstance(a, dict) or a.get("type") != "queued_command" or a.get("commandMode") != "prompt":
                    continue
                o = a.get("origin")
                if isinstance(o, dict) and o.get("kind") == "human":
                    text = _text_of(a.get("prompt"))
                    if text is not None:
                        yield "queued", strip_reminders(text)
                continue
            if kind != "user":
                continue
            tur = e.get("toolUseResult")
            if isinstance(tur, dict) and isinstance(tur.get("questions"), list) and isinstance(tur.get("answers"), dict):
                for v in tur["answers"].values():
                    if isinstance(v, str):
                        yield "ask", v
                continue
            if tur is not None:
                continue
            msg = e.get("message")
            text = _text_of(msg.get("content")) if isinstance(msg, dict) else None
            if text is None:
                continue
            o = e.get("origin")
            human = (isinstance(o, dict) and o.get("kind") == "human") or (o is None and e.get("promptSource") == "sdk")
            if human:
                yield "typed", strip_reminders(text)


def _sibling(transcript_path, session):
    """The transcript of another session in the same folder; the id has matched UUID_RE."""
    return os.path.join(os.path.dirname(transcript_path), session + ".jsonl")


def _scan(paths, quote):
    """The source name ('typed', 'queued', 'ask') of the first entry that holds the quote, or None."""
    frags = fragments(quote)
    if not frags:
        return None
    whole = norm(quote)
    long_enough = sum(len(f) for f in frags) >= QUOTE_MIN
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        for src, text in user_texts(p):
            t = norm(text)
            if src == "ask":
                if t == whole or (long_enough and in_order(frags, t)):
                    return "ask"
            elif long_enough and in_order(frags, t):
                return src
    return None


FIELD_RE = re.compile(r"^- ([a-z][a-z ]*?):[ \t]*(.*?)\s*$")
HEAD_RE = re.compile(r"^## Q(\d+)\b")
ANSWER_TS_RE = re.compile(r"\s*\(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z\)\s*$")


def _safe_name(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name and "\x00" not in name


def pane_answer(root, qref, quote):
    """True when `<root>/<repo>/<slug>/questions.md` holds block Q<n> with `status: answered` and `via: pane`
    whose answer equals the quote (or, for a multi question, one `|` part of it)."""
    n, repo, slug = qref
    if not (_safe_name(repo) and _safe_name(slug)):
        return False
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, repo, slug, "questions.md"))
    if os.path.commonpath([base, path]) != base or not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    block = None
    fields = {}
    for line in lines:
        hm = HEAD_RE.match(line)
        if hm:
            if block == n:
                break
            block = int(hm.group(1))
            fields = {}
            continue
        if block == n:
            fm = FIELD_RE.match(line)
            if fm and fm.group(1) not in fields:
                fields[fm.group(1)] = fm.group(2)
    if block != n:
        return False
    if fields.get("status") != "answered" or fields.get("via", "").strip().lower() != "pane":
        return False
    answer = ANSWER_TS_RE.sub("", fields.get("answer", ""))
    want = norm(quote)
    if norm(answer) == want:
        return True
    if fields.get("multi", "").strip().lower() == "yes":
        return any(norm(part) == want for part in answer.split("|"))
    return False


def find_user_quote(basis, transcript_path, root=None):
    """'typed' | 'queued' | 'ask' | 'pane' for where the user's quote was found, or None. The transcript is
    re-read up to RETRIES times, RETRY_WAIT apart (it is written asynchronously); a pane answer needs no wait."""
    root = orch_root() if root is None else root
    paths = [transcript_path if isinstance(transcript_path, str) and transcript_path else None]
    if basis.session and paths[0]:
        paths.append(_sibling(paths[0], basis.session))
    readable = any(p and os.path.isfile(p) for p in paths)
    found = _scan(paths, basis.quote)
    if found:
        return found
    if basis.qref and pane_answer(root, basis.qref, basis.quote):
        return "pane"
    if readable:
        for _ in range(RETRIES):
            SLEEP(RETRY_WAIT)
            found = _scan(paths, basis.quote)
            if found:
                return found
    return None


def claude_md_has(path, quote):
    """True when path is a CLAUDE.md or CLAUDE.local.md that exists and holds the quote (12+ characters)."""
    if os.path.basename(path) not in ("CLAUDE.md", "CLAUDE.local.md"):
        return False
    p = os.path.expanduser(path)
    if not os.path.isfile(p):
        return False
    frags = fragments(quote)
    if not frags or sum(len(f) for f in frags) < QUOTE_MIN:
        return False
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(FILE_MAX)
    return in_order(frags, norm(text))


def lesson_exists(name, root=None):
    """`<root>/lessons/<slug>.md` for `global`, `<root>/<repo>/lessons/<slug>.md` otherwise; the names matched
    LESSON_RE, which allows no path separator."""
    root = orch_root() if root is None else root
    scope, slug = name
    if not _safe_name(scope) or not _safe_name(slug):
        return False
    path = os.path.join(root, "lessons", slug + ".md") if scope == "global" \
        else os.path.join(root, scope, "lessons", slug + ".md")
    return os.path.isfile(path)


# ---- the verdict ----------------------------------------------------------------------------------------------

FORMS = (
    'basis: user | "<your own words, 12+ characters>" [| Q<n> <repo>/<slug>] [| session <uuid>]',
    "basis: signal | <token> [| <step or finding id>]",
    "basis: rule | <token>",
    "basis: failure | <token>",
    "basis: lesson | <global|repo>/<YYYY-MM-DD-slug>",
    'basis: claude-md | <path to CLAUDE.md or CLAUDE.local.md> | "<quote>"',
)


def _refuse(code, why, role, tier, default):
    shown = "%s/%s" % tuple(x or "?" for x in tier)
    reason = ("orch tier guard: %s at %s is above its default tier (%s/%s), and %s. "
              "The first line of the prompt must be one of:\n  %s\n"
              "Or spawn at the default tier (%s/%s)."
              % (role, shown, default[0], default[1], why, "\n  ".join(FORMS), default[0], default[1]))
    return Verdict(False, code, reason, None)


def check_spawn(tool_input, transcript_path=None, root=None):
    """Verdict for a spawn of a checked role. Allowed silently (code 'default') at or below the role's default."""
    sub = tool_input.get("subagent_type")
    key = agent_key(sub)
    model, effort = tool_input.get("model"), tool_input.get("effort")
    if (model is not None and not isinstance(model, str)) or (effort is not None and not isinstance(effort, str)):
        return Verdict(True, "default", None, None)
    if not above_default(key, model, effort):
        return Verdict(True, "default", None, None)
    role = "orch:" + key[len("orch-"):]
    tier = spawn_tier(key, model, effort)
    default = default_of(key)
    try:
        basis = parse_basis(tool_input.get("prompt"))
    except BasisError as exc:
        return _refuse("bad-basis", "the basis line is malformed: %s" % exc, role, tier, default)
    if basis is None:
        return _refuse("no-basis", "the prompt has no `basis:` line", role, tier, default)
    if key == MYTHOS and basis.kind != "user":
        return _refuse("mythos-basis", "%s takes a `user` basis only (the user accepted the Mythos offer or asked)"
                       % role, role, tier, default)
    if basis.kind in ("signal", "rule", "failure"):
        res = fits(basis.kind, basis.token, key, tier)
        if res == "unknown-token":
            return _refuse("token-unknown", "`%s | %s` is not a token in TIERS.md's table" % (basis.kind, basis.token),
                           role, tier, default)
        if res == "wrong-role":
            return _refuse("token-role", "`%s | %s` does not apply to %s" % (basis.kind, basis.token, role),
                           role, tier, default)
        if res == "above-ceiling":
            m, e = roles_ceiling(basis, key)
            return _refuse("token-ceiling", "`%s | %s` allows %s at most %s/%s, not %s/%s"
                           % (basis.kind, basis.token, role, m, e, tier[0] or "?", tier[1] or "?"),
                           role, tier, default)
        return Verdict(True, "ok", None, None)
    if basis.kind == "lesson":
        if lesson_exists(basis.name, root):
            return Verdict(True, "ok", None, None)
        return _refuse("lesson-missing", "no lesson file %s/%s exists" % basis.name, role, tier, default)
    if basis.kind == "claude-md":
        if claude_md_has(basis.path, basis.quote):
            return Verdict(True, "ok", None, None)
        return _refuse("claude-md", "that CLAUDE.md or CLAUDE.local.md does not exist or does not hold the quote",
                       role, tier, default)
    found = find_user_quote(basis, transcript_path, root)
    if found:
        return Verdict(True, "ok", None, found)
    return _refuse("quote-not-found", "the quote was not found among the user's own messages in this session "
                   "(typed, queued, an AskUserQuestion answer or a pane answer)", role, tier, default)


def roles_ceiling(basis, key):
    return TOKENS[(basis.kind, basis.token)][key]
