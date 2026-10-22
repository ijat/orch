# Plan: fix-menu

- repo: /home/u/Projects/myapp
- base branch: main

## Summary

- shape: single — one fix
- goal: the menu opens on tap.
- steps:
  1. Fix the tap handler (S)

## Goal

Lite: omitted.

## Findings

### Verified

- `src/menu.py` registers the handler twice.

### Assumptions (not verified)

None.

## Decisions

Lite: omitted.

## Approach

Lite: omitted.

## Steps

### Step 1 — Fix the tap handler

- size: S
- files: /home/u/Projects/myapp/src/menu.py
- do: register once.
- contract: one handler per element.
- done when: the menu test passes.

## Files and areas touched

- src/menu.py

## Parallelization

Lite: omitted.

## Verification

### Per step (targeted)

- Step 1: `pytest tests/test_menu.py`

### Final gate (once, before review)

`pytest -q`

## Risks and unknowns

Lite: omitted.
