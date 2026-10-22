# Plan: add-login (milestone M2)

- repo: /home/u/Projects/myapp
- base branch: main

## Summary

- shape: single (milestone)
- goal: sessions.
- steps:
  1. Session store (S)

## Decisions

- **D1** — cookies are signed.

## Steps

### Step 1 — Session store

- size: S
- files: /home/u/Projects/myapp/src/session.py
- do: sessions.
- contract: signed.
- done when: tests pass.

## Files and areas touched

- src/session.py

## Verification

### Per step (targeted)

- Step 1: `pytest tests/test_session.py`

## Risks and unknowns

- None.
