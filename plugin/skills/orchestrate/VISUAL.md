# Visual steps and pilot batches

Read this when a plan step has `visual:` or `pilot:`. Part of `SKILL.md` step 7.

**Visual steps** (plan step has `visual: <acceptance>`): `orch spawn` the director first (background,
default `opus/high`), then the implementer at the plan's tier, each given the other's agent ID (`Agent`
has no name parameter; in the `--extra` file of its `orch brief`, or by `SendMessage`, never added to the printed spawn prompt). They loop by `SendMessage` pointers to `visual-s<S>-<N>.md` (protocol in both
role files); you relay only if one returns `blocked` with `need: … unreachable`. Either ends its turn
while it waits for the other, so completion notices arrive mid-loop. **A return whose `status.md` row
reads `waiting` is not a finish:** no `orch return` (it refuses a non-final row), don't advance, keep the
wait armed. The pair is done when the implementer's row is `done` for the step; then read the batch
files' verdict lines, not the crops. **Whenever you replace the implementer** (after the pilot, three
strikes, a check-in stop, an escalation, a usage re-spawn), first `SendMessage` the director
`implementer is now <new id>` and give the new implementer the director's ID. A step not marked
`visual:` that needs visual judgement may get a director mid-step (journal an `override`). The
director's accepts are final; its escalations arrive as `blocked` — one about a recorded user decision
goes straight to the user (rungs 1–2 can't answer it), any other works the rung ladder first.

**Pilot batches:** a step with `pilot: <items>` runs those items first as their own spawn, at the
step's tier (`TIERS.md` pilots a gate-passing mechanical step with per-item checks at `haiku/medium`).
Measure it by the 5h usage it moved: `orch usage` right before the spawn and right after the
return (the completion notice's token count is the final context size, not spend; note anything else
that ran meanwhile). Take its redo or review rounds too, and extrapolate to the step's remaining items.
Park one line (`orch ask`, `--blocks` the rest of the step; options continue / continue at another tier
/ hold): "pilot: N items, +P% of the 5h window, R rounds → est. +P×M% for the rest at this tier". Journal
a `pilot` line; only after their answer spawn the rest, with the pilot's lessons (traps, a better batch
order) in its brief. On a visual step, the pilot is batch 1 of the director loop.
