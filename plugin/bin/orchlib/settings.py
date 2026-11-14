"""orch setting get|set NAME [VALUE]: orch's own switches, in one small JSON file (settings.json).

The file is `<data root>/settings.json` (~/.claude/orch-data/settings.json). Under ORCH_DATA it moves with the data root;
under ORCH_DIR alone (a development copy told where to write) it is `<ORCH_DIR>/.orch-settings.json`, so a scratch run
never reads or writes the real one. A missing file, a missing key or a malformed file means the default (a malformed file
also prints one warning line to stderr); no command here fails because of the file's content.
"""
import json
import os

from .paths import OrchError, data_root, require_write_allowed, warn_stderr, write_atomic

# name -> (default, allowed values)
SETTINGS = {
    "mythos_review": ("off", ("on", "off")),
}


def settings_path():
    if os.environ.get("ORCH_DATA"):
        return os.path.join(data_root(), "settings.json")
    if os.environ.get("ORCH_DIR"):
        return os.path.join(os.environ["ORCH_DIR"], ".orch-settings.json")
    return os.path.join(data_root(), "settings.json")


def load(warn=True):
    """The file's object, {} when it is missing or malformed (one warning line for a malformed one)."""
    path = settings_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if warn:
            warn_stderr("%s is not readable (%s); using the defaults" % (path, str(exc).splitlines()[0] if str(exc) else type(exc).__name__))
        return {}
    if not isinstance(data, dict):
        if warn:
            warn_stderr("%s is not a JSON object; using the defaults" % path)
        return {}
    return data


def get(name):
    """The setting's value: the stored one when it is allowed, else the default."""
    default, allowed = SETTINGS[name]
    value = load().get(name)
    return value if value in allowed else default


def _known(name):
    if name not in SETTINGS:
        raise OrchError("unknown setting %r (known: %s)" % (name, ", ".join(sorted(SETTINGS))), 1)


def cmd_setting(ns, extra):
    _known(ns.name)
    if ns.action == "get":
        if ns.value is not None:
            raise OrchError("setting get takes only a name", 1)
        print(get(ns.name))
        return 0
    default, allowed = SETTINGS[ns.name]
    if ns.value not in allowed:
        raise OrchError("setting %s must be one of: %s (got %r)" % (ns.name, " | ".join(allowed), ns.value), 1)
    require_write_allowed()
    data = load()
    data[ns.name] = ns.value
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(ns.value)
    return 0


def register(add):
    sp = add("setting", cmd_setting, "read or change one of orch's own switches (settings.json): get NAME | set NAME VALUE")
    sp.add_argument("action", choices=["get", "set"])
    sp.add_argument("name")
    sp.add_argument("value", nargs="?", default=None)
