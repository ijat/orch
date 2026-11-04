"""orch continue-gate: retired 2026-11-04. Planners no longer implement; every plan goes to fresh implementers.

The command stays so that a session started under the old rules can still call it: it answers `split`, which the
old rules treat as the ordinary path, and writes nothing. Old `continue_gate` journal lines and `continue:` plan
lines stay valid. Python 3.9 compatible, standard library only."""


def cmd_continue_gate(ns, extra):
    print("continue-gate: split steps=- ctx=unknown (retired) reasons=retired")
    return 0


def register(add):
    sp = add("continue-gate", cmd_continue_gate, "retired: always answers split, writes nothing")
    sp.add_argument("--agent-id", dest="agent_id", default=None)
    sp.add_argument("--planner-tier", dest="planner_tier", default=None)
    sp.add_argument("--routed", default="")
    sp.add_argument("--signals", default="")
    sp.add_argument("--tokens", type=int, default=None)
    sp.add_argument("--plan", default=None)
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")
