import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine, MockClock } from 'claude-code/testing'

import {
  agentPalette, agentPct, agentPctTail, askContext, barCells, barLayout, countTail, fold, goalLine, hhmm, idleMinutes, isStale, lastActivity, lineText, mdLite,
  bytesText, indexDetails, indexHeadline, indexLook, overallTail, parseIndex, placeText, plainInput, scopeLabel, shimmer,
  sortByAttention, splitScope, isMine, spawnOf, sparkline, stepsTail, textKey,
  WAKE_TEXT, acceptable, answerOf, answersArgv, autoPairs, historyRows, isCritical, mergeOrder, metaText, nextInQueue, optionNote,
  pageCount, pickCurrent, pickedLabel, staleQuestion, togglePick, isChosen, isMulti, needsHoldRow, openListOf, skipFor,
  COMPACT_TOOL_FULL, COMPACT_WAKE_TEXT, busyReason,
} from '../hooks/pane/lib'
import type { ItemState, MdLine } from '../hooks/pane/lib'
import type { Draft, IndexStatus, StatusQuestion, StatusRole, StatusTask } from '../types'

const NOW = Date.parse('2026-10-29T10:30Z')

const PANE = {
  plugin: 'orch',
  component: 'Pane',
  requestId: 'orch-pane',
  props: {
    title: 'Orchestration',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 60 },
    view: {},
  },
} as const

const PROGRAM = {
  id: 'app/build', repo: 'app', slug: 'build', task_dir: '~/x', repo_path: '/proj', worktree: null,
  in_cwd: true, state: 'implementing', state_words: 'Building', mode: 'standard', shape: 'roadmap', program: true,
  milestone: 'M2 (two)', milestone_no: 2, summary: '- goal: build the app', updated: null, last_log: null,
  questions_open: 0,
  running: {
    roles: [{ role: 'implementer', state: 'in_progress', step: '1', note: 'working on step(s) 2' }],
    last_spawn: { ts: '2026-10-29T10:18Z', role: 'implementer', agent: 'orch-implementer', tier: 'sonnet/high',
                  steps: ['2'], milestone: 2, returned: false },
  },
  steps: {
    plan: '~/x/m02-two/plan.md', total: 2, done: 1, percent: 50, current: ['2'],
    items: [
      { id: '1', title: 'first step', done: true, current: false, summary: 'change thing 1' },
      { id: '2', title: 'second step', done: false, current: true, summary: 'change thing 2\nand more' },
    ],
  },
  milestones: {
    done: 1, total: 3,
    items: [
      { n: 1, goal: 'first', size: 'S', state: 'done', summary: 'first' },
      { n: 2, goal: 'second', size: 'L', state: 'current', summary: 'second\nreview: code · size: L' },
      { n: 3, goal: 'third', size: 'M', state: 'pending', summary: 'third' },
    ],
  },
  overall: { percent: 43, estimate: true, basis: 'milestone size (S=1 M=2 L=4)' },
  error: null,
}

const OTHER = {
  ...PROGRAM, id: 'other/fix', repo: 'other', slug: 'fix', in_cwd: false, program: false, shape: 'single',
  milestone: null, milestone_no: null, milestones: null, summary: 'fix other',
  running: { roles: [], last_spawn: null },
  steps: { plan: null, total: null, done: null, percent: null, current: null, items: [] },
  overall: { percent: null, estimate: false, basis: 'steps' },
}

const OUTPUT = JSON.stringify({ schema: 1, generated: '2026-10-29T10:30Z', cwd: '/proj', tasks: [PROGRAM, OTHER] })

type Ran = { exitCode: number; stdout: string; stderr: string }
type World = {
  // `refuseSession`: an orch that predates `--session` (exit 1, its real stderr) for any run that carries the flag.
  refuseSession?: boolean
  // Where installed_plugins.json says `orch@orch` is installed; the plugin's own root when left out.
  installedAt?: string
  runs: string[][]; indexRuns: string[][]; opens: number; paneOpen: boolean; placed: boolean; result: Ran; index: Ran
  // `orch answers` runs (its argv), its answer, and what the orch would then print for `status`.
  answers: string[][]; answered: Ran; onAnswers: (argv: string[]) => void
  // `orch status --question-group <label>`: the stdout to give by label, and the runs.
  groups: Record<string, string | ((skip: number) => string)>; groupRuns: string[][]
  toasts: string[]
  wakes: string[]; notes: string[]
  // The tools registered, `session.compact` calls (their instructions), how many of them fail first, and the order of effects.
  tools: string[]; compacts: (string | undefined)[]; compactFails: number; order: string[]
  // `orch setting` runs, the stored value of `mythos_review` (the CLI's file, default off) and whether a `set` fails.
  settingRuns: string[][]; mythos: 'on' | 'off'; settingRefuses?: boolean
}

const INDEX: IndexStatus = {
  schema: 1, state: 'stale', indexing: false, indexed_commit: '2a465daf7c7c8793', head_commit: 'd5eb5b06fbd77be7',
  commits_behind: 3, indexed_at: '2026-10-29T10:27:00Z', db_bytes: 1503238554,
  counts: { files: 485942, symbols: 1200000, refs: 9000000, includes: 800000, bases: 5000, names: 70000 },
  parse_error_files: 12, parse_error_structural: 2, languages: { cpp: 400000, rust: 85942 },
  last_index: { kind: 'incremental', seconds: 0.9, extracted: 200, reused: 485742, files_per_sec: 222.2 },
  history: [
    { at: '2026-10-29T10:27:00Z', kind: 'incremental', seconds: 0.9, extracted: 200 },
    { at: '2026-10-29T09:00:00Z', kind: 'full', seconds: 9, extracted: 1000 },
  ],
  usage: {
    window_days: 7, queries: 412, by_command: { def: { n: 200, p50_ms: 4, p95_ms: 31 } }, zero_hit_pct: 8,
    stale_answer_pct: 12.5, label_mix: { resolved: 0.31, syntactic: 0.69 }, last_query_at: '2026-10-29T10:20:00Z',
  },
}
const INDEX_RAN: Ran = { exitCode: 0, stdout: JSON.stringify(INDEX), stderr: '' }

// The engine beneath the plugin: env, cwd, the command, panes, and `orch status` itself.
// `env` replaces the environment.
// Tests that move the mock clock by tens of seconds (animation ticks, polls) are CPU-bound: they take ~1.5 s alone and
// more than the 5 s default when scripts/check.py runs the Python suite beside them, so they carry `timeoutMs: 30000`.
// The CLI runs from the plugin's own bin: the mod reads its root from `$.plugin.root`, which in these tests is the plugin
// dir above this file (nothing is written down, so a checkout anywhere passes).
const PLUGIN_ROOT = import.meta.dir.replace(/\\/g, '/').replace(/\/tests$/, '')
const ORCH = `${PLUGIN_ROOT}/bin/orch`
function world(on: On, result: Ran = { exitCode: 0, stdout: OUTPUT, stderr: '' },
               env: Record<string, string> = { HOME: '/home/u' }): World {
  const w: World = {
    runs: [], indexRuns: [], opens: 0, paneOpen: true, placed: true, result, index: INDEX_RAN,
    answers: [], answered: { exitCode: 0, stdout: 'Q1 answered', stderr: '' }, onAnswers: () => {}, groups: {}, groupRuns: [], toasts: [], wakes: [], notes: [], tools: [], compacts: [], compactFails: 0, order: [], settingRuns: [], mythos: 'off',
  }
  mock.env(on, env)
  on('session.cwd', () => ({ value: '/proj' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('ui.open', () => {
    w.opens += 1
    return { value: { isPlaced: true } }
  })
  on('ui.panes', () => ({
    value: w.paneOpen
      ? [{ id: 'orch-pane', title: 'Orchestration', isShown: w.placed, isFocused: false, isPlaced: w.placed }]
      : [],
  }))
  on('process.run', (_, e) => {
    // The About probes: `--version` of orch and orch-index, and a python one-liner for installed_plugins.json
    // (it prints the installPath and the gitCommitSha of `orch@orch`; `w.installedAt` is where it says it is installed).
    if (e.argv.includes('--version') || e.argv.includes('-c')) {
      const out = e.argv.includes('-c')
        ? `${w.installedAt ?? PLUGIN_ROOT} abc1234def56789`
        : e.argv[0]?.includes('orch-index') ? 'orch-index 0.2.0' : 'orch 0.1.0'
      return { value: { exitCode: 0, stdout: out, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    }
    if (e.argv.includes('setting')) {
      // `orch setting get|set mythos_review`: the CLI's own file, here a field of the world.
      const [verb, name, value] = e.argv.slice(e.argv.indexOf('setting') + 1)
      w.settingRuns.push([...e.argv])
      if (verb === 'set' && name === 'mythos_review' && !w.settingRefuses) w.mythos = value === 'on' ? 'on' : 'off'
      const out = verb === 'get' ? `${w.mythos}\n` : verb === 'set' && !w.settingRefuses ? `${value}\n` : ''
      return { value: { exitCode: verb === 'set' && w.settingRefuses ? 1 : 0, stdout: out, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    }
    if (e.argv.includes('answers')) {
      w.answers.push([...e.argv])
      w.order.push('answers')
      if (w.answered.exitCode === 0) w.onAnswers([...e.argv])
      return { value: { ...w.answered, isStdoutTruncated: false, isStderrTruncated: false } }
    }
    const group = e.argv.indexOf('--question-group')
    if (group >= 0) {
      w.groupRuns.push([...e.argv])
      const given = w.groups[e.argv[group + 1] ?? '']
      const skip = e.argv.indexOf('--question-skip')
      const out = typeof given === 'function' ? given(skip >= 0 ? Number(e.argv[skip + 1]) : 0) : given
      return { value: { exitCode: out === undefined ? 1 : 0, stdout: out ?? '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    }
    const isIndex = e.argv.some(a => /orch-index(\.exe)?$/.test(a))
    ;(isIndex ? w.indexRuns : w.runs).push([...e.argv])
    const ran = isIndex ? w.index
      : w.refuseSession && e.argv.includes('--session') ? { exitCode: 1, stdout: '', stderr: "orch: unrecognized argument '--session'" }
      : w.result
    return { value: { ...ran, isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('turn.complete', () => ({ text: '' }))
  on('prompt.submit', (_, e) => {
    w.wakes.push(e.text)
    w.order.push('wake')
    return { text: e.text }
  })
  on('ui.notify', (_, e) => {
    w.notes.push(e.text)
    return { value: { isSent: true as const, channel: 'kitty' as const } }
  })
  on('ui.toast', (_, e) => { w.toasts.push(JSON.stringify(e)); return { value: undefined } })
  on('tool.register', (_, e) => {
    w.tools.push(e.name)
    return { value: { tool: `mcp__orch__${e.name}` } }
  })
  on('session.compact', (_, e) => {
    if (w.compactFails > 0) {
      w.compactFails -= 1
      throw new Error('a turn is running')
    }
    w.compacts.push(e.instructions)
    w.order.push('compact')
    return { messages: [{ role: 'user' as const, text: 'summary', toolUses: [] }] }
  })
  return w
}

async function start($: Engine, clock: MockClock) {
  await $.session.start({ cwd: '/proj', surface: 'desktop', isInteractive: true })
  await clock.settle()
}

test('shows the repo task with bars, markers and the running line, on every surface', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  await start($, clock)
  expect(w.runs).toEqual([[ORCH, 'status', '--json', '--cwd', '/proj']])
  expect(w.indexRuns).toEqual([['/home/u/.claude/orch-data/bin/orch-index', 'status', '--json', '--repo', '/proj']])
  expect(w.opens).toBe(1)

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /^Steps/ }))?.text).toBe('Steps    ████████████░░░░░░░░░░░░  1/2 · 50%')
    expect((await ui.find({ type: 'Text', text: /^Overall/ }))?.text).toBe('Overall  ██████████░░░░░░░░░░░░░░   43%  estimate')
    expect((await ui.find({ type: 'Text', text: /implementer/ }))?.text).toMatch(/^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] implementer$/)
    expect((await ui.find({ type: 'Text', text: /^sonnet/ }))?.text).toBe('sonnet/high')
    // The spawn lists its steps, so the status file's older `last: 1` is left out.
    expect((await ui.find({ type: 'Text', text: /^ {2}steps/ }))?.text).toBe('  steps 2 · running 12m')
    expect((await ui.find({ type: 'Button', key: 'ms:app/build:1' }))?.text).toMatch(/^✓ M1 first/)
    expect((await ui.find({ type: 'Button', key: 'ms:app/build:2' }))?.text).toMatch(/^▶ M2 second.*L/)
    expect((await ui.find({ type: 'Button', key: 'ms:app/build:3' }))?.text).toMatch(/^· M3 third/)
    expect((await ui.find({ type: 'Button', key: 'step:app/build:2' }))?.text).toMatch(/^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] 2 second step/)
    expect(await ui.find({ type: 'Button', key: 'task:other/fix' })).toBeUndefined()
    await ui.unmount()
  }
})

test('on Windows it runs orch through python from USERPROFILE, not HOME', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, undefined, { OS: 'Windows_NT', USERPROFILE: 'C:\\home\\u', HOME: '/c/home/u' })
  await start($, clock)
  expect(w.runs).toEqual([['python', ORCH, 'status', '--json', '--cwd', '/proj']])
  expect(w.indexRuns).toEqual([['C:/home/u/.claude/orch-data/bin/orch-index.exe', 'status', '--json', '--repo', '/proj']])
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /could not run/ })).toBeUndefined()
  await ui.unmount()
})

test('the scope button shows and hides the other tasks', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'scope' })
    expect((await ui.find({ type: 'Button', key: 'task:other/fix' }))?.text).toMatch(/other\/fix/)
    await ui.press({ key: 'scope' })
    expect(await ui.find({ type: 'Button', key: 'task:other/fix' })).toBeUndefined()
    await ui.unmount()
  }
})

test('a step row expands to its summary and folds again', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: /change thing 2/ })).toBeUndefined()
    await ui.press({ key: 'step:app/build:2' })
    expect((await ui.find({ type: 'Text', text: /change thing 2/ }))?.text).toBe('change thing 2')
    expect((await ui.find({ type: 'Text', text: /and more/ }))?.text).toBe('and more')
    await ui.press({ key: 'step:app/build:2' })
    expect(await ui.find({ type: 'Text', text: /change thing 2/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('a failing orch status shows its error line', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { exitCode: 1, stdout: '', stderr: "orch: invalid choice: 'status'\nusage: …" })
  await start($, clock)
  expect(w.opens).toBe(0)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /orch status failed/ }))?.text).toBe(
      "orch status failed: orch: invalid choice: 'status'",
    )
    await ui.unmount()
  }
})

test('polls every 8 s only while the pane is open', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  await start($, clock)
  expect(w.runs.length).toBe(1)
  await clock.advance(8000)
  expect(w.runs.length).toBe(2)
  w.paneOpen = false
  await clock.advance(8000)
  expect(w.runs.length).toBe(2)
})

test('the running spinner turns while open and a role runs, and stops when idle or closed', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const glyph = async () => (await ui.find({ type: 'Text', text: /implementer/ }))?.text?.[0]
  const first = await glyph()
  await clock.advance(120)
  const second = await glyph()
  expect(second).not.toBe(first)
  w.paneOpen = false
  await clock.advance(360)
  expect(await glyph()).toBe(second)
})

test('an idle task does not spin', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const idle = JSON.stringify({ schema: 1, generated: '2026-10-29T10:30Z', cwd: '/proj', tasks: [{ ...OTHER, in_cwd: true }] })
  world(on, { exitCode: 0, stdout: idle, stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const before = (await ui.find({ type: 'Button', key: 'task:other/fix' }))?.text
  await clock.advance(1200)
  expect((await ui.find({ type: 'Button', key: 'task:other/fix' }))?.text).toBe(before)
})

test('an open pane that waits undrawn is not polled', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  w.placed = false
  await start($, clock)
  expect(w.runs.length).toBe(1)
  await clock.advance(16000)
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })
  await clock.settle()
  expect(w.runs.length).toBe(1)
  w.placed = true
  await clock.advance(8000)
  expect(w.runs.length).toBe(2)
})

test('a finished turn refreshes the open pane', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  await start($, clock)
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })
  await clock.settle()
  expect(w.runs.length).toBe(2)
  w.paneOpen = false
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 't2', reason: 'answer' })
  await clock.settle()
  expect(w.runs.length).toBe(2)
})

test('a snapshot an earlier version stored is declined', async ($, on) => {
  on('state.get', () => ({ value: { value: { tasks: [] }, version: 1 } }))
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /Loading/ }))?.text).toBe('Loading…')
  await ui.unmount()
})

// -- pure logic (hooks/lib.ts) --------------------------------------------------------------------

const REAL_STEP = 'workspace (edition 2024, `resolver = "3"`, `members = ["crates/*", "services/*"]`, `[workspace.dependencies]` with exact versions, `[workspace.lints]` with `unsafe_code = "forbid"`, clippy pedantic off, `unwrap_used = "deny"` outside tests); toolchain `1.99.1` with rustfmt and clippy; `.gitignore` (`/target`, `/.testkit/`, `.DS_Store`); justfile recipes `ci`, `fmt`, `lint`, `test`, `test-unit`,…'
const REAL_GOAL = "- goal: Chrome extensions run in Luneium on a complete chrome.* API that Luneium builds itself, and LastPass, uBlock\n  Origin and uBO Lite work end to end. Page context menus (ROADMAP Next) come first, with room for extension items.\n- changed:\n  - rev 1: the user's D1 (own layer, complete API) and the acceptance tests;\n  - rev 2: the gate answers and the plan review, including the `LUNEIUM_EXTENSIONS` gate (D28);"

const widest = (lines: MdLine[]) => Math.max(...lines.map(l => lineText(l).length))

test('mdLite: bullets, keys, code and bold marks, nesting', () => {
  const [line, ...rest] = mdLite('- goal: build the app', 40)
  expect(rest).toHaveLength(0)
  expect(line?.lead).toBe('• ')
  expect(line?.runs).toEqual([{ text: 'goal:', key: true }, { text: ' build the app' }])

  const nested = mdLite('- a\n  - b\n    - c', 40)
  expect(nested.map(l => l.lead)).toEqual(['• ', '  • ', '    • '])

  const code = mdLite('use `orch status --json` now', 40)
  expect(code[0]?.runs).toEqual([{ text: 'use ' }, { text: 'orch status --json', code: true }, { text: ' now' }])
  expect(code.map(lineText).join('')).not.toContain('`')

  const bold = mdLite('**bold** text', 40)
  expect(bold[0]?.runs).toEqual([{ text: 'bold', bold: true }, { text: ' text' }])

  expect(mdLite('- **goal:** do it', 40)[0]?.runs[0]).toEqual({ text: 'goal:', key: true })
  expect(lineText(mdLite('an `unclosed tick and **open', 40)[0]!)).toBe('an `unclosed tick and **open')
})

test('mdLite: wraps with hanging indents, hard-breaks long tokens, folds blank runs', () => {
  const long = mdLite(`- ${'word '.repeat(40).trim()}`, 40)
  expect(long.length).toBeGreaterThan(1)
  expect(widest(long)).toBeLessThanOrEqual(40)
  expect(long.slice(1).every(l => l.lead === '  ')).toBe(true)

  const path = mdLite(`x ${'/a'.repeat(35)} y`, 30)
  expect(widest(path)).toBeLessThanOrEqual(30)
  expect(path.map(l => l.runs.map(r => r.text).join('')).join('')).toContain('/a/a')

  const blanks = mdLite('one\n\n\n\ntwo\n\n', 40)
  expect(blanks.map(lineText)).toEqual(['one', '', 'two'])

  const real = mdLite(REAL_STEP, 56)
  expect(widest(real)).toBeLessThanOrEqual(56)
  expect(real.some(l => l.runs.some(r => r.text.includes('`')))).toBe(false)
  expect(real.map(lineText).join(' ')).toContain('test-unit,…')

  for (const width of [12, 20, 33, 56, 80]) expect(widest(mdLite(REAL_GOAL, width))).toBeLessThanOrEqual(width)
  // A source line that continues a bullet joins it.
  expect(lineText(mdLite(REAL_GOAL, 400)[0]!)).toContain('uBlock Origin and uBO Lite')
})

test('goalLine takes the goal bullet, else the first line', () => {
  expect(goalLine('- goal: build the app')).toBe('build the app')
  expect(goalLine('- **goal:** turn `x` into y\n- approach: z')).toBe('turn x into y')
  expect(goalLine(REAL_GOAL)).toMatch(/^Chrome extensions .* uBlock Origin and uBO Lite work end to end\./)
  expect(goalLine('Fix the **thing**\nmore')).toBe('Fix the thing')
  expect(goalLine(null)).toBeNull()
  expect(goalLine('  \n')).toBeNull()
})

const st = (spec: string) => [...spec].map(c => (c === 'd' ? 'done' : c === 'c' ? 'current' : 'pending') as ItemState)
const foldOf = (spec: string) => fold(st(spec), s => s)

test('fold: done runs and later items fold separately, the current and the next item always show', () => {
  expect(foldOf('dddddcpppppp')).toEqual([
    { kind: 'done', from: 0, to: 4 }, { kind: 'item', index: 5 }, { kind: 'item', index: 6 },
    { kind: 'later', from: 7, to: 11 },
  ])
  // Under three later items nothing folds; one done item does not fold either.
  expect(foldOf('ddcpp').map(r => r.kind)).toEqual(['done', 'item', 'item', 'item'])
  expect(foldOf('dcpppp').map(r => r.kind)).toEqual(['item', 'item', 'item', 'later'])
  expect(foldOf('dcppp')).toEqual([
    { kind: 'item', index: 0 }, { kind: 'item', index: 1 }, { kind: 'item', index: 2 }, { kind: 'later', from: 3, to: 4 },
  ])
  expect(foldOf('dddddd')).toEqual([{ kind: 'done', from: 0, to: 5 }])
  expect(foldOf('dcp')).toEqual([{ kind: 'item', index: 0 }, { kind: 'item', index: 1 }, { kind: 'item', index: 2 }])
  expect(foldOf('')).toEqual([])
  // Not-done items before the current one stay in view; two done runs fold one by one.
  const two = foldOf('ddpddcpppp')
  expect(two.map(r => r.kind)).toEqual(['done', 'item', 'done', 'item', 'item', 'later'])
  // Every index exactly once, in order.
  for (const spec of ['dddddcpppppp', 'ddpdpppp', 'pdpdcdpdpp', 'ccc', 'pppppppp', 'ddpddcpppp']) {
    const covered = foldOf(spec).flatMap(r => (r.kind === 'item' ? [r.index] : Array.from({ length: r.to - r.from + 1 }, (_, i) => r.from + i)))
    expect(covered).toEqual([...spec].map((_, i) => i))
  }
})

test('countTail writes done of total and the share', () => {
  expect(countTail(1, 3)).toBe('1/3 · 33%')
  expect(countTail(3, 7)).toBe('3/7 · 43%')
  expect(countTail(6, 12)).toBe('6/12 · 50%')
  expect(countTail(0, 0)).toBe('0/0 · 0%')
})

test('bar layout aligns labels and tails, with no tilde', () => {
  const bars = [{ label: 'Steps', tail: stepsTail(50, 1, 2) }, { label: 'Overall', tail: overallTail(43, true) }]
  expect(bars.map(b => b.tail)).toEqual(['1/2 · 50%', ' 43%  estimate'])
  expect(barLayout(56, bars)).toEqual({ labelW: 9, barW: 24, tailW: 14 })
  expect(barLayout(30, bars).barW).toBe(10)
  expect(barCells(43, 24)).toBe(10)
  expect(barCells(100, 24)).toBe(24)
  expect(barCells(0, 24)).toBe(0)
  expect(overallTail(100, false)).toBe('100%')
})

test('shimmer moves one cell per frame and repeats', () => {
  expect(shimmer(0, 5)).toEqual([])
  const a = shimmer(12, 7)
  expect(a).toHaveLength(12)
  expect(a.every(c => /^#[0-9a-f]{6}$/.test(c))).toBe(true)
  expect(shimmer(12, 7)).toEqual(a)
  expect(shimmer(12, 7 + 18)).toEqual(a)
  for (const filled of [3, 5, 12, 24]) {
    for (let f = 0; f < filled + 7; f++) expect(shimmer(filled, f + 1)).not.toEqual(shimmer(filled, f))
  }
})

test('agentPct accepts only integers 0-99', () => {
  const r = (pct: unknown): StatusRole => ({ role: 'implementer', state: 'in_progress', step: null, note: null, pct } as StatusRole)
  expect(agentPct(r(40))).toBe(40)
  expect(agentPct(r(0))).toBe(0)
  expect(agentPct(r(99))).toBe(99)
  for (const bad of [undefined, null, -1, 100, 12.5, NaN, Infinity, '40']) expect(agentPct(r(bad))).toBeNull()
  expect(agentPct({ role: null, state: null, step: null, note: null })).toBeNull()
  expect(agentPctTail(40)).toBe(' 40%')
  expect(agentPctTail(0)).toBe('  0%')
  expect(agentPctTail(99)).toBe(' 99%')
})

test('agent palettes follow the role base name, and shimmer defaults to green', () => {
  const green = agentPalette('implementer')
  expect(agentPalette('implementer:ui')).toBe(green)
  expect(agentPalette(null)).toBe(green)
  expect(agentPalette('orchestrator')).toBe(green)
  expect(agentPalette('planner')).not.toBe(green)
  expect(agentPalette('code-reviewer:a')).toBe(agentPalette('code-reviewer'))
  expect(shimmer(12, 7)).toEqual(shimmer(12, 7, green))
  // Frame 12 puts the highlight at cell 9 of 12: cell 0 is the plain dark end.
  expect(shimmer(12, 12, agentPalette('code-reviewer'))[0]).toBe('#0891b2')
  expect(shimmer(12, 12)[0]).toBe('#16a34a')
  expect(shimmer(12, 7, agentPalette('planner'))).not.toEqual(shimmer(12, 7))
  expect(shimmer(12, 12, agentPalette('toString'))[0]).toBe('#16a34a')
})

const T0 = Date.parse('2026-10-29T10:00Z')
const task = (over: Partial<StatusTask>): StatusTask => ({
  id: 'r/t', repo: 'r', slug: 't', task_dir: null, repo_path: null, worktree: null, in_cwd: true,
  state: 'implementing', state_words: 'Building', mode: null, shape: null, program: false, milestone: null,
  milestone_no: null, summary: null, updated: '2026-10-29T10:00Z', last_log: null, questions_open: 0,
  running: { roles: [], last_spawn: null },
  steps: { total: null, done: null, percent: null, current: null, items: [] },
  milestones: null, overall: { percent: null, estimate: false, basis: '' }, error: null, ...over,
})

test('stale means an active task with nothing running for over 30 minutes', () => {
  expect(isStale(task({}), T0 + 31 * 60000)).toBe(true)
  expect(isStale(task({}), T0 + 29 * 60000)).toBe(false)
  expect(isStale(task({ state: 'awaiting_approval' }), T0 + 300 * 60000)).toBe(false)
  const running = { roles: [{ role: 'implementer', state: 'in_progress', step: null, note: null }], last_spawn: null }
  expect(isStale(task({ running }), T0 + 300 * 60000)).toBe(false)
  expect(idleMinutes(task({ last_log: '2026-10-29T10:20:30Z x: y' }), T0 + 30 * 60000)).toBe(9)
  expect(idleMinutes(task({ updated: null }), T0)).toBeNull()
})

test('each running role finds its own spawn, not just the newest', () => {
  const spawn = (role: string, tier: string) =>
    ({ ts: '2026-10-29T10:18Z', role, agent: 'orch-implementer', tier, steps: ['1'], milestone: 2, returned: false })
  const [a, b] = [spawn('implementer:a', 'opus/medium'), spawn('implementer:b', 'sonnet/high')]
  const both = task({ running: { roles: [], last_spawn: b, spawns: [a, b] } })
  expect(spawnOf(both, 'implementer:a')?.tier).toBe('opus/medium')
  expect(spawnOf(both, 'implementer:b')?.tier).toBe('sonnet/high')
  expect(spawnOf(both, 'implementer:c')).toBeNull()
  const old = task({ running: { roles: [], last_spawn: b } })   // an orch without `spawns`
  expect(spawnOf(old, 'implementer:b')?.tier).toBe('sonnet/high')
  expect(spawnOf(old, 'implementer:a')).toBeNull()
})

test('tasks needing attention sort first, the rest keep their order', () => {
  const a = task({ id: 'a', state: 'awaiting_approval' })
  const b = task({ id: 'b', state: 'awaiting_approval' })
  const q = task({ id: 'q', state: 'awaiting_approval', questions_open: 2 })
  const e = task({ id: 'e', state: 'awaiting_approval', error: 'bad' })
  expect(sortByAttention([a, b, q, e], T0).map(t => t.id)).toEqual(['q', 'e', 'a', 'b'])
  expect(sortByAttention([a, task({ id: 's' })], T0 + 60 * 60000).map(t => t.id)).toEqual(['s', 'a'])
})

test('lastActivity parses the log line', () => {
  expect(lastActivity(task({ last_log: '2026-10-29T15:17Z implementer: started step(s) 6' }))).toEqual({
    who: 'implementer', text: 'started step(s) 6', at: '2026-10-29T15:17Z',
  })
  expect(lastActivity(task({ last_log: '2026-10-27T07:47Z orchestrator: orchestrator: M4 passed' }))?.text).toBe('M4 passed')
  expect(lastActivity(task({ last_log: '2026-09-28T17:23:19Z orchestrator: CI green' }))?.at).toBe('2026-09-28T17:23:19Z')
  expect(lastActivity(task({}))).toEqual({ who: '', text: 'updated', at: '2026-10-29T10:00Z' })
  expect(lastActivity(task({ updated: null }))).toBeNull()
})

test('hhmm is local wall-clock time', () => {
  const d = new Date(T0)
  expect(hhmm(T0)).toBe(`${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`)
})

test('textKey is a stable, versioned hash', () => {
  expect(textKey('abc')).toBe(textKey('abc'))
  expect(textKey('abc')).toMatch(/^p1-[0-9a-f]{14}$/)
  expect(textKey('abd')).not.toBe(textKey('abc'))
  expect(textKey('')).toMatch(/^p1-[0-9a-f]{14}$/)
})

test('askContext drops paths and stays within its limit', () => {
  const items = Array.from({ length: 30 }, (_, i) => ({ id: String(i + 1), title: `s${i}`, done: false, current: false, summary: 'x'.repeat(400) }))
  const big = task({ task_dir: '~/secret/dir', repo_path: '/p', steps: { total: 30, done: 0, percent: 0, current: null, items } })
  const full = askContext([big])
  expect(full).not.toContain('task_dir')
  expect(full).toContain('x'.repeat(400))
  const small = askContext([big], 6000)
  expect(small).not.toContain('x'.repeat(400))
  expect(small.length <= 6000 || !small.includes('xxx')).toBe(true)
  expect(JSON.parse(small)[0].steps.items).toHaveLength(30)
})

test('scope label counts the tasks elsewhere', () => {
  expect(scopeLabel(false, 3)).toBe('[this repo · 3 elsewhere]')
  expect(scopeLabel(false, 0)).toBe('[this repo]')
  expect(scopeLabel(true, 3)).toBe('[all tasks]')
})

// -- the redesigned pane ---------------------------------------------------------------------------

const output = (...tasks: unknown[]) => JSON.stringify({ schema: 1, generated: '2026-10-29T10:30Z', cwd: '/proj', tasks })
const PANE60 = (surface: 'terminal' | 'desktop') => ({ ...PANE, surface })

test('the bars line up and carry no tilde', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    const steps = (await ui.find({ type: 'Text', text: /^Steps/ }))?.text ?? ''
    const overall = (await ui.find({ type: 'Text', text: /^Overall/ }))?.text ?? ''
    expect(steps.search(/[█░]/)).toBe(overall.search(/[█░]/))
    expect(steps.search(/[█░]/)).toBeGreaterThan(0)
    const tailAt = (row: string) => row.lastIndexOf('░') + 1
    expect(tailAt(steps)).toBe(tailAt(overall))
    expect(steps + overall).not.toContain('~')
    expect((await ui.find({ type: 'Button', key: 'task:app/build' }))?.text).not.toContain('~')
    await ui.unmount()
  }
})

test('the header shows state words, slug or id, the questions badge, and the scope count', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on, { exitCode: 0, stdout: output(PROGRAM, { ...OTHER, questions_open: 2, state: 'awaiting_approval', state_words: 'Waiting for your approval' }), stderr: '' })
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    expect((await ui.find({ type: 'Button', key: 'scope' }))?.text).toBe('[this repo · 1 elsewhere]')
    // An open task is a card: its name on the left, its state on the right.
    expect((await ui.find({ type: 'Button', key: 'task:app/build' }))?.text).toBe('▾ build')
    expect((await ui.find({ type: 'Text', text: 'Building' }))).toBeDefined()
    await ui.press({ key: 'scope' })
    expect((await ui.find({ type: 'Button', key: 'scope' }))?.text).toBe('[all tasks]')
    expect((await ui.find({ type: 'Button', key: 'task:app/build' }))?.text).toBe('▾ app/build')
    const heads = (await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('task:'))
    expect(heads.map(b => b.key)).toEqual(['task:other/fix', 'task:app/build'])
    // A folded task keeps its one line: name, state, the questions badge and the overall percent.
    expect(heads[0]?.text).toMatch(/^▸ other\/fix {2}Waiting for your approval {2}\? 2 questions$/)
    await ui.press({ key: 'scope' })
    await ui.unmount()
  }
})

test('the Now section shows the last activity and warns when a task stalls', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const stalled = { ...OTHER, in_cwd: true, state: 'implementing', updated: '2026-10-29T09:00Z',
    last_log: '2026-10-29T09:00Z orchestrator: orchestrator: round 2 pass' }
  const busy = { ...PROGRAM, last_log: '2026-10-29T10:18Z implementer: started step(s) 2' }
  world(on, { exitCode: 0, stdout: output(busy, stalled), stderr: '' })
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    expect((await ui.find({ type: 'Text', text: /started step/ }))?.text).toBe('implementer started step(s) 2 · 12m ago')
    expect((await ui.find({ type: 'Text', text: /round 2 pass/ }))?.text).toBe('orchestrator round 2 pass · 1h 30m ago')
    expect((await ui.find({ type: 'Text', text: /^idle/ }))?.text).toBe('idle 1h 30m · nothing running')
    // The stalled task needs attention, so it comes first.
    const heads = (await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('task:'))
    expect(heads.map(b => b.key)).toEqual(['task:other/fix', 'task:app/build'])
    await ui.unmount()
  }
})

test('a failed fetch after a good one keeps the data and says since when', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  await start($, clock)
  w.result = { exitCode: 1, stdout: '', stderr: 'boom' }
  await clock.advance(8000)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    const d = new Date(NOW)
    const at = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    expect((await ui.find({ type: 'Text', text: /stale since/ }))?.text).toBe(`stale since ${at}`)
    expect((await ui.find({ type: 'Text', text: /orch status failed/ }))?.text).toBe('orch status failed: boom')
    expect(await ui.find({ type: 'Button', key: 'task:app/build' })).toBeDefined()
    await ui.unmount()
  }
})

test('the card and panels have headings and the goal is folded to one line until pressed', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const long = { ...PROGRAM, summary: '- goal: build the app with `cargo`\n- approach:\n  - first a **thing**\n  - then another' }
  world(on, { exitCode: 0, stdout: output(long), stderr: '' })
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    for (const h of ['Agents', 'Milestones', 'Steps (M2)', '1/3 · 33%', '1/2 · 50%']) {
      expect(await ui.find({ type: 'Text', text: h })).toBeDefined()
    }
    expect(await ui.find({ type: 'Text', text: 'Goal' })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'goal:app/build' }))?.text).toBe('▸ build the app with cargo')
    expect(await ui.find({ type: 'Text', text: /then another/ })).toBeUndefined()
    await ui.press({ key: 'goal:app/build' })
    expect((await ui.find({ type: 'Button', key: 'goal:app/build' }))?.text).toBe('▾ build the app with cargo')
    expect((await ui.find({ type: 'Text', text: /• goal:/ }))?.text).toBe('• goal: build the app with cargo')
    expect((await ui.find({ type: 'Text', text: /first a thing/ }))?.text).toBe('  • first a thing')
    expect((await ui.find({ type: 'Text', text: /then another/ }))?.text).toBe('  • then another')
    await ui.press({ key: 'goal:app/build' })
    await ui.unmount()
  }
})

test('a long step list folds around the current step and opens on press', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const items = Array.from({ length: 12 }, (_, i) => ({ id: String(i + 1), title: `step ${i + 1}`, done: i < 5, current: i === 5, summary: null }))
  const many = { ...PROGRAM, steps: { ...PROGRAM.steps, total: 12, done: 5, percent: 42, current: ['6'], items } }
  world(on, { exitCode: 0, stdout: output(many), stderr: '' })
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    const steps = async () => (await ui.findAll({ type: 'Button' }))
      .filter(b => b.key?.startsWith('step:') || b.key?.startsWith('fold:app/build:steps')).map(b => b.text)
    expect(await steps()).toEqual(['✓ 1–5 done', expect.stringMatching(/^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] 6 step 6$/), '· 7 step 7', '· 5 more'])
    // Later items open and fold on their own: `▴ fold` sits below its run.
    await ui.press({ key: 'fold:app/build:steps:later' })
    const open = await steps()
    expect(open).toHaveLength(9)
    expect(open[0]).toBe('✓ 1–5 done')
    expect(open[8]).toBe('▴ fold')
    // Done items open on their own: `▴ fold done` sits above its run.
    await ui.press({ key: 'fold:app/build:steps:done' })
    const both = await steps()
    expect(both).toHaveLength(14)
    expect(both[0]).toBe('▴ fold done')
    expect(both[5]).toMatch(/^✓ 5 step 5$/)
    expect(both[13]).toBe('▴ fold')
    await ui.press({ key: 'fold:app/build:steps:done' })
    await ui.press({ key: 'fold:app/build:steps:later' })
    expect(await steps()).toHaveLength(4)
    await ui.unmount()
  }
})

const stepColours = async (ui: { findAll: (q: { type: string; text: RegExp }) => Promise<{ props: Record<string, unknown> }[]> }) =>
  (await ui.findAll({ type: 'Text', text: /^█+$/ })).map(t => String(t.props.color)).filter(c => c === 'success')

test('the Steps bar stays one plain colour while a role runs', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  expect(await stepColours(ui)).toEqual(['success'])
  await clock.advance(120)
  expect(await stepColours(ui)).toEqual(['success'])
  // The Overall bar stays one theme colour.
  expect((await ui.findAll({ type: 'Text', text: /^█+$/ })).filter(t => t.props.color === 'claude')).toHaveLength(1)
  await ui.unmount()
})

test('an idle task draws a plain Steps bar', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const idle = { ...PROGRAM, running: { roles: [], last_spawn: null } }
  world(on, { exitCode: 0, stdout: output(idle), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  expect(await stepColours(ui)).toEqual(['success'])
  await clock.advance(360)
  expect(await stepColours(ui)).toEqual(['success'])
  await ui.unmount()
})

// -- per-agent progress bar -------------------------------------------------------------------------

const withRoles = (...roles: { role: string; pct?: unknown }[]) => ({
  ...PROGRAM,
  running: {
    ...PROGRAM.running,
    roles: roles.map(r => ({ role: r.role, state: 'in_progress', step: null, note: null, ...('pct' in r ? { pct: r.pct } : {}) })),
  },
})
const barLines = async (ui: { findAll: (q: { type: string; text: RegExp }) => Promise<{ text: string }[]> }) =>
  (await ui.findAll({ type: 'Text', text: /^ {2}[█░]+ {2} *\d+%$/ })).map(t => t.text)

test('a running agent that reports pct gets a bar under its name, as wide as the Steps bar', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on, { exitCode: 0, stdout: output(withRoles({ role: 'implementer', pct: 40 })), stderr: '' })
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    const lines = await barLines(ui)
    expect(lines).toHaveLength(1)
    expect(lines[0]).toMatch(/ {2} 40%$/)
    const steps = (await ui.find({ type: 'Text', text: /^Steps/ }))?.text ?? ''
    const cells = (t: string) => [...t].filter(c => c === '█' || c === '░').length
    expect(cells(lines[0] ?? '')).toBe(cells(steps))
    // Order: name line, bar line, then the tail line.
    const texts = (await ui.findAll({ type: 'Text', text: /./ })).map(t => t.text)
    const name = texts.findIndex(t => /implementer/.test(t))
    const bar = texts.indexOf(lines[0] ?? '')
    const tail = texts.findIndex(t => /running /.test(t))
    expect(name).toBeGreaterThanOrEqual(0)
    expect(bar).toBeGreaterThan(name)
    expect(tail).toBeGreaterThan(bar)
    await ui.unmount()
  }
})

test('an agent without a valid pct has no bar line', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on, { exitCode: 0, stdout: output(withRoles({ role: 'implementer' }, { role: 'planner', pct: 100 }, { role: 'code-reviewer', pct: null })), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  expect(await barLines(ui)).toEqual([])
  await ui.unmount()
})

test('two running agents each get a bar, in role order, in their own colour and animated', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on, { exitCode: 0, stdout: output(withRoles({ role: 'implementer', pct: 40 }, { role: 'code-reviewer:a', pct: 15 })), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  const lines = await barLines(ui)
  expect(lines).toHaveLength(2)
  expect(lines[0]).toMatch(/ 40%$/)
  expect(lines[1]).toMatch(/ 15%$/)
  const filled = (l: string) => [...l].filter(c => c === '█').length
  expect(filled(lines[0] ?? '')).toBeGreaterThan(filled(lines[1] ?? ''))
  const colours = async () => (await ui.findAll({ type: 'Text', text: /^█+$/ })).map(t => String(t.props.color)).filter(c => c.startsWith('#'))
  const before = await colours()
  expect(before.length).toBeGreaterThan(0)
  await clock.advance(120)
  expect(await colours()).not.toEqual(before)
  await ui.unmount()
})

// -- Haiku: plain summaries and the Ask box ---------------------------------------------------------

const USAGE = { input_tokens: 1, output_tokens: 1, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 }
type Call = { model: string; effort?: string; prompt: string; system?: string }

// The model beneath the plugin: records each call and answers `reply` (a text, or a failure reason).
function haiku(on: On, reply: (prompt: string) => string | { reason: 'api-error' } = () => 'In plain words.') {
  const calls: Call[] = []
  on('model.complete', (_, e) => {
    calls.push({ model: e.model, effort: e.effort, prompt: e.prompt, system: e.system })
    const r = reply(e.prompt)
    return typeof r === 'string'
      ? { value: { isAnswered: true as const, text: r, usage: USAGE } }
      : { value: { isAnswered: false as const, reason: 'api-error' as const, status: 500, error: 'server_error' as const, usage: USAGE } }
  })
  return calls
}

// $.store in memory, in insertion order, as the engine keeps it.
function memStore(on: On, seed: Record<string, unknown> = {}) {
  const map = new Map<string, unknown>(Object.entries(seed))
  on('store.get', (_, e) => ({ value: map.get(e.key) }))
  on('store.set', (_, e) => {
    map.set(e.key, e.value)
    return { value: undefined }
  })
  on('store.delete', (_, e) => {
    map.delete(e.key)
    return { value: undefined }
  })
  on('store.keys', () => ({ value: [...map.keys()] }))
  return map
}

const STEP2_INPUT = plainInput('step', 'second step', 'change thing 2\nand more')

test('expanding a step asks Haiku once for a plain summary and caches it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  const calls = haiku(on)
  const store = memStore(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  expect(calls).toHaveLength(0)
  await ui.press({ key: 'step:app/build:2' })
  expect(calls).toHaveLength(1)
  expect(calls[0]).toMatchObject({ model: 'haiku', effort: 'low' })
  expect(calls[0]?.prompt).toContain('change thing 2')
  expect((await ui.find({ type: 'Text', text: /In plain words/ }))?.text).toBe('✦ In plain words.')
  expect(await ui.find({ type: 'Text', text: /change thing 2/ })).toBeUndefined()
  expect(store.get(`plain:${textKey(STEP2_INPUT)}`)).toEqual({ text: 'In plain words.' })
  await ui.press({ key: 'step:app/build:2' })
  await ui.press({ key: 'step:app/build:2' })
  expect(calls).toHaveLength(1)
  // Raw stays reachable.
  await ui.press({ key: 'raw:step:app/build:2' })
  expect((await ui.find({ type: 'Text', text: /change thing 2/ }))?.text).toBe('change thing 2')
  expect((await ui.find({ type: 'Button', key: 'raw:step:app/build:2' }))?.text).toBe('plain')
  await ui.press({ key: 'raw:step:app/build:2' })
  expect((await ui.find({ type: 'Text', text: /In plain words/ }))?.text).toBe('✦ In plain words.')
  await ui.unmount()
})

test('a summary stored by an earlier session is not asked again', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  const calls = haiku(on)
  memStore(on, { [`plain:${textKey(STEP2_INPUT)}`]: { text: 'From the cache.' } })
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    await ui.press({ key: 'step:app/build:2' })
    expect((await ui.find({ type: 'Text', text: /From the cache/ }))?.text).toBe('✦ From the cache.')
    await ui.press({ key: 'step:app/build:2' })
    await ui.unmount()
  }
  expect(calls).toHaveLength(0)
})

test('a failed summary keeps the raw text and says why', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  haiku(on, () => ({ reason: 'api-error' }))
  const store = memStore(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('desktop'))
  await ui.press({ key: 'step:app/build:2' })
  expect((await ui.find({ type: 'Text', text: /change thing 2/ }))?.text).toBe('change thing 2')
  expect((await ui.find({ type: 'Text', text: /no plain summary/ }))?.text).toBe('no plain summary: api-error')
  expect(store.size).toBe(0)
  await ui.unmount()
})

test('the goal and milestones get plain summaries too', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  const calls = haiku(on, prompt => (prompt.includes('goal') ? 'The app gets built.' : 'Milestone two.'))
  memStore(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'goal:app/build' })
  expect((await ui.find({ type: 'Text', text: /The app gets built/ }))?.text).toBe('✦ The app gets built.')
  await ui.press({ key: 'ms:app/build:2' })
  expect((await ui.find({ type: 'Text', text: /Milestone two/ }))?.text).toBe('✦ Milestone two.')
  expect(calls.map(c => c.model)).toEqual(['haiku', 'haiku'])
  await ui.unmount()
})

test('no model call happens at start, on polls or on draws', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  const calls = haiku(on)
  memStore(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await clock.advance(16000)
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })
  await clock.settle()
  await ui.press({ key: 'scope' })
  expect(calls).toHaveLength(0)
  await ui.unmount()
})

test('the store keeps the newest 200 summaries', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  haiku(on)
  const seed = Object.fromEntries(Array.from({ length: 200 }, (_, i) => [`plain:old${i}`, { text: `t${i}` }]))
  const store = memStore(on, { other: 1, ...seed })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'step:app/build:2' })
  const kept = [...store.keys()].filter(k => k.startsWith('plain:'))
  expect(kept).toHaveLength(200)
  expect(kept).not.toContain('plain:old0')
  expect(kept).toContain('plain:old1')
  expect(kept).toContain(`plain:${textKey(STEP2_INPUT)}`)
  expect(store.get('other')).toBe(1)
  await ui.unmount()
})

test('the Ask box answers a question from the status of the shown tasks', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  const calls = haiku(on, () => 'The implementer is on step 2.')
  memStore(on)
  await start($, clock)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(PANE60(surface))
    expect(await ui.find({ type: 'Input', key: 'ask' })).toBeUndefined()
    await ui.press({ key: 'tab:ask' })
    expect((await ui.find({ type: 'Input', key: 'ask' }))?.props.placeholder).toBe('Ask about these tasks…')
    await ui.input({ key: 'ask', text: '   ' })
    expect(calls).toHaveLength(0)
    await ui.input({ key: 'ask', text: 'what is running?' })
    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({ model: 'haiku', effort: 'low' })
    expect(calls[0]?.prompt).toContain('<status>')
    expect(calls[0]?.prompt).toContain('"id":"app/build"')
    expect(calls[0]?.prompt).not.toContain('other/fix')
    expect(calls[0]?.prompt).toMatch(/Question: what is running\?$/)
    expect((await ui.find({ type: 'Text', text: /^Q:/ }))?.text).toBe('Q: what is running?')
    expect((await ui.find({ type: 'Text', text: /implementer is on step 2/ }))?.text).toBe('The implementer is on step 2.')
    await ui.press({ key: 'ask-clear' })
    expect(await ui.find({ type: 'Text', text: /implementer is on step 2/ })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'ask-clear' })).toBeUndefined()
    await ui.press({ key: 'tab:ask' })
    calls.length = 0
    await ui.unmount()
  }
})

test('a failed answer says why', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  haiku(on, () => ({ reason: 'api-error' }))
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'tab:ask' })
  await ui.input({ key: 'ask', text: 'status?' })
  expect((await ui.find({ type: 'Text', text: /no answer/ }))?.text).toBe('no answer: api-error')
  await ui.unmount()
})

test('mobile draws the pane, and its Ask tab says there is no Ask box', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'mobile' })
  expect(await ui.find({ type: 'Button', key: 'task:app/build' })).toBeDefined()
  await ui.press({ key: 'tab:ask' })
  expect(await ui.find({ type: 'Input', key: 'ask' })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /not available/ }))?.text).toBe('Ask is not available here.')
  await ui.press({ key: 'tab:more' })
  expect(await ui.find({ type: 'Text', text: 'Advanced options' })).toBeDefined()
  await ui.unmount()
})

// -- lost calls expire --------------------------------------------------------------------------------

const IDLE_OUT = output({ ...PROGRAM, running: { roles: [], last_spawn: null } })

// What a hot reload leaves: an Ask or a summary marked in flight in $.state, and no code left to settle it.
// A seeded key reads as its seed, at the store's own version, until the plugin writes it.
function seedState(on: On, seeds: Record<string, unknown>) {
  const live = new Set(Object.keys(seeds))
  const ticks = { n: 0 }
  on('state.get', async (_, e, next) => {
    const r = await next(e)
    const read = r.value as { value?: unknown; version: number } | undefined
    const seeded = e.plugin === 'orch' && live.has(e.key) && read !== undefined && read.value === undefined
    return seeded ? ({ value: { value: seeds[e.key], version: read.version } } as typeof r) : r
  })
  on('state.set', (_, e, next) => {
    if (e.plugin === 'orch') live.delete(e.key)
    if (e.plugin === 'orch' && e.key === 'frame') ticks.n += 1
    return next(e)
  })
  return ticks
}

// The spinner glyph of a row, which turns only while the 120 ms tick runs.
const glyphOf = async (ui: { find: (q: { type: string; text: RegExp }) => Promise<{ text: string } | undefined> }, re: RegExp) =>
  (await ui.find({ type: 'Text', text: re }))?.text?.[0]

test('an Ask left busy by a lost call expires and the tick stops', { timeoutMs: 30000 }, async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on, { exitCode: 0, stdout: IDLE_OUT, stderr: '' })
  const ticks = seedState(on, { ask: { state: 'busy', question: 'status?', at: NOW } })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'tab:ask' })
  const a = await glyphOf(ui, /thinking…/)
  await clock.advance(120)
  expect(await glyphOf(ui, /thinking…/)).not.toBe(a)
  await clock.advance(50000)
  expect(await ui.find({ type: 'Text', text: /thinking/ })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /no answer/ }))?.text).toBe('no answer: aborted')
  const settled = ticks.n
  await clock.advance(1200)
  expect(ticks.n).toBe(settled)
  await ui.unmount()
})

test('a summary left pending by a lost call expires, keeps the raw text, and is asked again on re-expand', { timeoutMs: 30000 }, async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on, { exitCode: 0, stdout: IDLE_OUT, stderr: '' })
  memStore(on)
  const calls = haiku(on, () => 'Second try.')
  const ticks = seedState(on, {
    expanded: { 'step:app/build:2': true },
    plain: { [textKey(STEP2_INPUT)]: { state: 'pending', at: NOW } },
  })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  const a = await glyphOf(ui, /plain summary…/)
  await clock.advance(120)
  expect(await glyphOf(ui, /plain summary…/)).not.toBe(a)
  await clock.advance(45000)
  expect(await ui.find({ type: 'Text', text: /plain summary…/ })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /no plain summary/ }))?.text).toBe('no plain summary: aborted')
  expect((await ui.find({ type: 'Text', text: /change thing 2/ }))?.text).toBe('change thing 2')
  const settled = ticks.n
  await clock.advance(1200)
  expect(ticks.n).toBe(settled)
  expect(calls).toHaveLength(0)
  await ui.press({ key: 'step:app/build:2' })
  await ui.press({ key: 'step:app/build:2' })
  expect(calls).toHaveLength(1)
  expect((await ui.find({ type: 'Text', text: /Second try/ }))?.text).toBe('✦ Second try.')
  await ui.unmount()
})

// -- code index -----------------------------------------------------------------------------------

test('the Index section sits in the More tab, folded, and opens to the details', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: 'Index' })).toBeUndefined()
  await ui.press({ key: 'tab:more' })
  expect(await ui.find({ type: 'Text', text: 'Index' })).toBeDefined()
  expect((await ui.find({ type: 'Button', key: 'index' }))?.text).toBe('▸ stale · 3 behind · 1.4 GB · revised 3m ago  ⚠ stale answers')
  expect(await ui.find({ type: 'Text', text: /contents/ })).toBeUndefined()
  await ui.press({ key: 'index' })
  expect((await ui.find({ type: 'Button', key: 'index' }))?.text).toMatch(/^▾ /)
  expect((await ui.find({ type: 'Text', text: /^commit/ }))?.text).toMatch(/2a465daf7c · 3 commits behind HEAD/)
  expect(await ui.find({ type: 'Text', text: /d5eb5b06fb/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /485,942 files/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /p50 4 ms · p95 31 ms/ })).toBeDefined()
  await ui.press({ key: 'index' })
  expect(await ui.find({ type: 'Text', text: /contents/ })).toBeUndefined()
  await ui.unmount()
})

test('the Index line is one per pane, not one per task', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'scope' })
  await ui.press({ key: 'task:other/fix' })
  await ui.press({ key: 'tab:more' })
  expect((await ui.findAll({ type: 'Button', key: 'index' }))).toHaveLength(1)
  await ui.unmount()
})

test('a missing orch-index says so, and a bad answer says why', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  w.index = { exitCode: 127, stdout: '', stderr: '' }
  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab:more' })
  expect((await ui.find({ type: 'Text', text: /orch-index/ }))?.text).toBe('orch-index is not installed')
  expect(await ui.find({ type: 'Text', text: /Loading|could not run/ })).toBeUndefined()
  await ui.unmount()

  w.index = { exitCode: 1, stdout: '', stderr: 'error: not a git repository\nmore' }
  await clock.advance(8000)
  // The tab stays open between mounts.
  ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /orch-index/ }))?.text).toBe('orch-index failed: error: not a git repository')
  await ui.unmount()
})

test('indexLook words the state as latest, stale, outdated or not built', () => {
  const look = (o: Partial<IndexStatus>) => indexLook({ ...INDEX, ...o })
  expect(look({ state: 'current' })).toEqual({ color: 'success', words: 'latest' })
  expect(look({ state: 'stale', commits_behind: 1 }).words).toBe('stale · 1 behind')
  expect(look({ state: 'stale', commits_behind: null }).words).toBe('stale')
  expect(look({ state: 'outdated' })).toEqual({ color: 'warning', words: 'outdated' })
  expect(look({ state: 'none' })).toEqual({ color: 'error', words: 'not built' })
  expect(look({ state: 'current', indexing: true }).words).toBe('indexing…')
})

test('index sizes, headline, sparkline and parsing', () => {
  expect(bytesText(512)).toBe('512 B')
  expect(bytesText(675840)).toBe('660 KB')
  expect(bytesText(1503238554)).toBe('1.4 GB')
  expect(indexHeadline(INDEX, NOW)).toBe('1.4 GB · revised 3m ago')
  expect(indexHeadline({ ...INDEX, db_bytes: null, indexed_at: null }, NOW)).toBe('')
  expect(sparkline([1, 8])).toBe('▂█')
  expect(sparkline([0, 0])).toBe('▁▁')
  expect(parseIndex(JSON.stringify(INDEX))?.state).toBe('stale')
  expect(parseIndex('{"schema":2,"state":"current"}')).toBeNull()
  expect(parseIndex('{"schema":1,"state":"weird"}')).toBeNull()
  expect(parseIndex('nope')).toBeNull()
})

test('indexDetails reports only what the index gives, and the fix when not current', () => {
  const none = indexDetails({ ...INDEX, state: 'none' }, NOW).map(r => r.text)
  expect(none).toEqual(['not built', 'run `orch-index index`'])
  const lean: IndexStatus = {
    schema: 1, state: 'current', indexing: false, indexed_commit: 'abc', head_commit: 'abc', commits_behind: 0,
    indexed_at: null, db_bytes: null, counts: null, parse_error_files: 0, parse_error_structural: 0,
  }
  expect(indexDetails(lean, NOW).map(r => r.label)).toEqual(['commit'])
  const rows = indexDetails({ ...INDEX, state: 'outdated', extractor_version: '8', binary_extractor_version: '9' }, NOW)
  expect(rows.find(r => r.label === 'extractor')?.text).toBe('built by v8, this orch-index is v9')
  expect(rows.find(r => r.label === 'answers')?.warn).toBe(true)
  expect(rows.find(r => r.label === 'run time')?.text).toMatch(/^█▂  last 2 runs, slowest 9\.0 s/)
  expect(rows.at(-1)?.label).toBe('fix')
  // A fresh index with one unlabelled query reports label_mix (and possibly by_command) as null.
  const fresh = { ...INDEX, state: 'current' as const, usage: { ...INDEX.usage!, queries: 1, label_mix: null, by_command: null } }
  const freshRows = indexDetails(fresh, NOW)
  expect(freshRows.find(r => r.label === 'answers')).toBeDefined()
  expect(freshRows.find(r => r.label === 'trust')).toBeUndefined()
})

test('placeText names the repo and marks a worktree', () => {
  const t = { repo: 'app', repo_path: '/p/app', worktree: '/p/app/.claude/worktrees/orch-x' } as StatusTask
  expect(placeText(t)).toBe('app · [WT] orch-x')
  expect(placeText({ ...t, worktree: null })).toBe('app')
  expect(placeText({ ...t, repo: null, worktree: null })).toBe('app')
})

test('About lists orch with the installed commit (12 characters) and orch-index', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'tab:more' })
  const all = (await ui.findAll({ type: 'Text', text: /./ })).map(t => t.text).join('\n')
  expect(all).toContain('About')
  expect(all).toContain('v0.1.0')
  expect(all).toContain('abc1234def56')
  expect(all).not.toContain('abc1234def567')
  expect(all).toContain('v0.2.0')
  expect(all).not.toContain('dev')
})

test('About says dev when orch@orch is installed somewhere else than this plugin root', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  w.installedAt = '/elsewhere/plugins/cache/orch/orch/0123456789ab'
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'tab:more' })
  const all = (await ui.findAll({ type: 'Text', text: /./ })).map(t => t.text).join('\n')
  expect(all).toContain('v0.1.0')
  expect(all).toContain('dev')
  expect(all).not.toContain('abc1234')
})

// -- the status card, the work panel and the folded tabs (ported from the approved mock) ---------------

const allText = async (ui: { findAll: (q: { type: string; text: RegExp }) => Promise<{ text: string }[]> }) =>
  (await ui.findAll({ type: 'Text', text: /./ })).map(t => t.text).join('\n')

const listOf = (n: number, doneN: number, label: string) => Array.from({ length: n }, (_, k) => {
  const i = k + 1
  return { id: String(i), title: `${label} ${i}`, done: i <= doneN, current: i === doneN + 1, summary: `about ${label} ${i}` }
})

test('the card shows the goal, facts, both bars and the agents; the panel counts milestones and steps', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  const text = await allText(ui)
  for (const want of ['Agents', '1 running', 'Overall  ', 'Steps    ', 'app · standard · roadmap · M2 (two)', 'implementer', 'Milestones', '1/3 · 33%', 'Steps (M2)', '1/2 · 50%']) {
    expect(text).toContain(want)
  }
  expect(text).not.toContain('Now')
  expect(await ui.find({ type: 'Button', key: 'goal:app/build' })).toBeDefined()
  await ui.unmount()
})

test('several agents: three shown, the rest fold behind a count', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const roles = ['implementer:a', 'implementer:b', 'code-reviewer', 'security-reviewer', 'explorer']
  const five = { ...PROGRAM, running: { ...PROGRAM.running, roles: roles.map(role => ({ role, state: 'in_progress', step: null, note: null })) } }
  world(on, { exitCode: 0, stdout: output(five), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  expect(await allText(ui)).toContain('5 running')
  expect(await allText(ui)).toContain('code-reviewer')
  expect(await allText(ui)).not.toContain('security-reviewer')
  expect((await ui.find({ type: 'Button', key: 'agents-fold:app/build' }))?.text).toBe('· 2 more')
  await ui.press({ key: 'agents-fold:app/build' })
  expect(await allText(ui)).toContain('explorer')
  expect((await ui.find({ type: 'Button', key: 'agents-fold:app/build' }))?.text).toBe('▴ fold')
  await ui.press({ key: 'agents-fold:app/build' })
  expect(await allText(ui)).not.toContain('explorer')
  await ui.unmount()
})

test('three agents or fewer show no fold row', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  expect(await ui.find({ type: 'Button', key: 'agents-fold:app/build' })).toBeUndefined()
  await ui.unmount()
})

test('a long plan: done and later items fold separately, and an expanded item shows its text', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const ms = Array.from({ length: 12 }, (_, k) => ({
    n: k + 1, goal: `milestone ${k + 1}`, size: 'M', state: k < 6 ? 'done' : k === 6 ? 'current' : 'pending', summary: `about milestone ${k + 1}`,
  }))
  const long = {
    ...PROGRAM, milestone_no: 7,
    milestones: { done: 6, total: 12, items: ms },
    steps: { ...PROGRAM.steps, total: 30, done: 16, percent: 53, current: ['17'], items: listOf(30, 16, 'step') },
  }
  world(on, { exitCode: 0, stdout: output(long), stderr: '' })
  haiku(on, () => 'Plain words.')
  memStore(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  const text = await allText(ui)
  expect(text).toContain('6/12 · 50%')
  expect(text).toContain('16/30 · 53%')
  expect(text).toContain('✓ M1–M6 done')
  expect(text).toContain('✓ 1–16 done')
  expect((await ui.find({ type: 'Button', key: 'fold:app/build:steps:later' }))?.text).toBe('· 12 more')

  await ui.press({ key: 'fold:app/build:steps:done' })
  expect((await ui.find({ type: 'Button', key: 'fold:app/build:steps:done' }))?.text).toBe('▴ fold done')
  expect((await ui.find({ type: 'Button', key: 'step:app/build:16' }))?.text).toBe('✓ 16 step 16')
  // Later items stay folded while done items are open.
  expect((await ui.find({ type: 'Button', key: 'fold:app/build:steps:later' }))?.text).toBe('· 12 more')
  await ui.press({ key: 'fold:app/build:steps:later' })
  expect((await ui.find({ type: 'Button', key: 'step:app/build:30' }))?.text).toBe('· 30 step 30')
  await ui.press({ key: 'fold:app/build:steps:done' })
  expect(await allText(ui)).toContain('✓ 1–16 done')

  await ui.press({ key: 'step:app/build:18' })
  expect(await allText(ui)).toContain('✦ Plain words.')
  await ui.unmount()
})

test('one done item and under three later items do not fold', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const small = { ...PROGRAM, steps: { ...PROGRAM.steps, total: 4, done: 1, percent: 25, current: ['2'], items: listOf(4, 1, 'step') } }
  world(on, { exitCode: 0, stdout: output(small), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  const keys = (await ui.findAll({ type: 'Button' })).map(b => b.key ?? '')
  expect(keys.filter(k => k.startsWith('fold:app/build:steps'))).toEqual([])
  expect(keys.filter(k => k.startsWith('step:'))).toHaveLength(4)
  await ui.unmount()
})

test('a task without milestones shows no Milestones block, and a task without steps no Steps block', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const single = { ...PROGRAM, program: false, milestone: null, milestone_no: null, milestones: null }
  const w = world(on, { exitCode: 0, stdout: output(single), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  const text = await allText(ui)
  expect(text).not.toContain('Milestones')
  expect(text).toContain('Steps')
  expect(text).not.toContain('Steps (M')
  expect(await ui.find({ type: 'Button', key: 'ms:app/build:1' })).toBeUndefined()
  await ui.unmount()

  const bare = { ...single, steps: { ...single.steps, total: null, done: null, percent: null, current: null, items: [] } }
  w.result = { exitCode: 0, stdout: output(bare), stderr: '' }
  await clock.advance(8000)
  const ui2 = await $.ui.mount(PANE60('terminal'))
  expect(await allText(ui2)).not.toContain('Steps (M')
  expect(await ui2.find({ type: 'Button', key: 'step:app/build:1' })).toBeUndefined()
  await ui2.unmount()
})

test('the tabs start folded, a click opens one and a second click folds it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  expect((await ui.findAll({ type: 'Button' })).map(b => b.key).filter(k => k?.startsWith('tab:'))).toEqual(['tab:history', 'tab:ask', 'tab:more'])
  expect(await allText(ui)).not.toContain('Advanced options')
  expect(await allText(ui)).not.toContain('No answered questions yet.')
  await ui.press({ key: 'tab:more' })
  expect(await allText(ui)).toContain('Advanced options')
  expect(await allText(ui)).toContain('Milestones')
  await ui.press({ key: 'tab:history' })
  expect(await allText(ui)).not.toContain('Advanced options')
  expect(await allText(ui)).toContain('No answered questions yet.')
  await ui.press({ key: 'tab:history' })
  expect(await allText(ui)).not.toContain('No answered questions yet.')
  await ui.unmount()
})

test('a pane running against an orch with no questions field renders; with one, History lists the answered', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  expect('questions' in PROGRAM).toBe(false)
  const w = world(on)
  await start($, clock)
  let ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'tab:history' })
  expect(await allText(ui)).toContain('No answered questions yet.')
  await ui.unmount()

  const opt = { label: 'Yes', text: null, kind: null, recommended: true }
  const q = (n: number, status: string, topic: string | null) => ({
    id: `Q${n}`, n, status, text: `Question text ${n}`, topic, group: null, reversible: 'yes', blocks: null, asked_by: null,
    asked: null, options: [opt], answer: `answer ${n}`, answered: '2026-10-29T10:00Z',
  })
  const counts = { open: 0, answered: 1, assumed: 1, withdrawn: 0 }
  const withQs = { ...PROGRAM, questions: { counts, groups: [], open_group: null, open: [], history: [q(2, 'assumed', null), q(1, 'answered', 'Storage')], history_total: 2 } }
  w.result = { exitCode: 0, stdout: output(withQs), stderr: '' }
  await clock.advance(8000)
  ui = await $.ui.mount(PANE60('terminal'))
  const rows = (await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('hist:')).map(b => b.text)
  expect(rows).toEqual(['▸ Question 2: Question text 2', '▸ Question 1: Storage'])
  const text = await allText(ui)
  expect(text).toContain('◆ Auto')
  expect(text).toContain('✓ Answered')
  await ui.press({ key: 'hist:Q1' })
  expect(await allText(ui)).toContain('→ answer 1')
  await ui.unmount()
})

test('AI summaries off shows the source text, calls no model, hides the summary buttons and is remembered', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  const calls = haiku(on)
  const store = memStore(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'tab:more' })
  expect((await ui.find({ type: 'Button', key: 'opt-ai' }))?.text).toMatch(/^\[x\] AI summaries/)
  await ui.press({ key: 'opt-ai' })
  expect((await ui.find({ type: 'Button', key: 'opt-ai' }))?.text).toMatch(/^\[ \] AI summaries/)
  expect(store.get('opt:ai')).toBe(false)

  await ui.press({ key: 'step:app/build:2' })
  await ui.press({ key: 'goal:app/build' })
  await ui.press({ key: 'ms:app/build:2' })
  expect(calls).toHaveLength(0)
  const text = await allText(ui)
  expect(text).toContain('change thing 2')
  expect(text).not.toContain('✦')
  const keys = (await ui.findAll({ type: 'Button' })).map(b => b.key ?? '')
  expect(keys.filter(k => k.startsWith('plain:') || k.startsWith('raw:'))).toEqual([])

  // On again: the summary button is back.
  await ui.press({ key: 'opt-ai' })
  expect(store.get('opt:ai')).toBe(true)
  expect((await ui.find({ type: 'Button', key: 'plain:step:app/build:2' }))?.text).toBe('✦ plain summary')
  await ui.unmount()
})

test('the AI summaries setting is read from the store at session start', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  world(on)
  const calls = haiku(on)
  memStore(on, { 'opt:ai': false })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'step:app/build:2' })
  expect(calls).toHaveLength(0)
  expect(await allText(ui)).toContain('change thing 2')
  await ui.press({ key: 'tab:more' })
  expect((await ui.find({ type: 'Button', key: 'opt-ai' }))?.text).toMatch(/^\[ \] /)
  await ui.unmount()
})

test('askContext sends only the question counts, not the questions', () => {
  const counts = { open: 1, answered: 0, assumed: 0, withdrawn: 0 }
  const t = task({ questions: { counts, groups: [], open_group: null, open: [], history: [], history_total: 0 } })
  const sent = JSON.parse(askContext([t]))[0]
  expect(sent.questions).toEqual({ counts })
  expect(JSON.parse(askContext([task({})]))[0].questions).toBeUndefined()
})

// -- answering: Needs you, groups, Next/Send, drafts, auto-answer, notify, History ------------------------

const UNG = '(ungrouped)'
const qopt = (label: string, text: string, recommended = false, kind: string | null = 'explanation') => ({ label, text, kind, recommended })
const mkq = (n: number, over: Partial<StatusQuestion> & Record<string, unknown> = {}): StatusQuestion => ({
  id: `Q${n}`, n, status: 'open', text: `Should item ${n} change?`, topic: `Item ${n}`, group: null, reversible: 'yes', blocks: 'none',
  asked_by: 'orchestrator', asked: '2026-10-29T10:00Z',
  options: [qopt('Keep', 'Nothing changes for you.', true), qopt('Change', 'Uses the new behaviour.')],
  answer: null, answered: null, detail: 'full', ...over,
}) as StatusQuestion
const critical = (n: number) => mkq(n, {
  reversible: 'no', text: `Delete item ${n} for good?`, topic: `Delete ${n}`,
  options: [qopt('Delete', 'The item is gone for good.'), qopt('Keep it', 'Nothing is lost.')],
})
const labelOf = (q: StatusQuestion) => q.group ?? UNG
const counts = (open: number, answered = 0, assumed = 0) => ({ open, answered, assumed, withdrawn: 0 })

// The questions block as `orch status --json` prints it: the first group's open questions in full, the others by count.
function qblock(open: StatusQuestion[], history: StatusQuestion[] = [], answered = 0) {
  const labels = [...new Set(open.map(labelOf))]
  return {
    counts: counts(open.length, answered),
    groups: labels.map(l => ({ group: l === UNG ? null : l, label: l, counts: counts(open.filter(q => labelOf(q) === l).length) })),
    open_group: labels[0] ?? null,
    open: labels[0] === undefined ? [] : open.filter(q => labelOf(q) === labels[0]),
    history, history_total: history.length,
  }
}
const withQs = (t: object, open: StatusQuestion[], history: StatusQuestion[] = [], answered = 0) =>
  ({ ...t, questions_open: open.length, questions: qblock(open, history, answered) })

async function pane($: Engine, on: On, tasks: object[], seed: Record<string, unknown> = {}) {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { exitCode: 0, stdout: output(...tasks), stderr: '' })
  const store = memStore(on, seed)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  return { clock, w, store, ui }
}
type Ui = Awaited<ReturnType<typeof pane>>['ui']
const buttonText = async (ui: Ui, key: string) => (await ui.find({ type: 'Button', key }))?.text
const buttons = async (ui: Ui) => (await ui.findAll({ type: 'Button' })).map(b => b.key ?? '')

test('Needs you: one open question, the recommended option preselected, every option explained', async ($, on) => {
  const reasoned = mkq(2, { options: [qopt('Keep', 'Nothing changes for you.', true), qopt('Change', 'Obvious from the name alone.', false, 'reason')] })
  const { ui } = await pane($, on, [withQs(PROGRAM, [mkq(1), reasoned, critical(3)])])
  const text = await allText(ui)
  expect(text).toContain('? Needs you')
  expect(text).toContain('3 pending')
  expect(text).toContain('Should item 1 change?')
  expect(text).toContain('blocks nothing · can be changed later')
  expect(text).toContain('Nothing changes for you.')
  expect(text).toContain('Uses the new behaviour.')
  expect(text).toContain('Recommended')
  // Only the open question shows its options; the others are rows.
  expect(text).not.toContain('Should item 2 change?')
  expect((await buttonText(ui, 'app/build:Q1:o0'))).toMatch(/^● Keep · Recommended$/)
  expect((await buttonText(ui, 'app/build:Q1:o1'))).toMatch(/^○ Change$/)
  await ui.press({ key: 'next:app/build' })
  expect(await allText(ui)).toContain('No explanation: Obvious from the name alone.')
  await ui.unmount()
})

test('Next moves on, a critical question has nothing preselected, the last one sends the batch and wakes once', async ($, on) => {
  const { ui, w, store } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2), critical(3)])])
  w.onAnswers = () => { w.result = { exitCode: 0, stdout: output(PROGRAM), stderr: '' } }
  expect(await buttonText(ui, 'next:app/build')).toBe('Next')
  await ui.press({ key: 'next:app/build' })
  expect(await allText(ui)).toContain('Should item 2 change?')
  await ui.press({ key: 'next:app/build' })

  // Question 3 is critical: no option is chosen, so there is no Send yet.
  expect(await allText(ui)).toContain('Delete item 3 for good?')
  expect(await allText(ui)).toContain('blocks nothing · cannot be undone')
  expect(await allText(ui)).toContain('Choose an option first')
  expect(await buttonText(ui, 'send:app/build')).toBeUndefined()
  expect(await buttonText(ui, 'next:app/build')).toBeUndefined()
  expect(await buttonText(ui, 'app/build:Q3:o0')).toBe('○ Delete')
  expect(await buttonText(ui, 'app/build:Q3:o1')).toBe('○ Keep it')
  expect(w.answers).toEqual([])

  await ui.press({ key: 'app/build:Q3:o1' })
  expect(await buttonText(ui, 'send:app/build')).toBe('Send 3 answers')
  await ui.press({ key: 'send:app/build' })
  expect(w.answers).toEqual([[ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Keep', 'Q2', 'Keep', 'Q3', 'Keep it']])
  expect(w.wakes).toEqual([WAKE_TEXT])
  expect(w.wakes[0]).not.toContain('Keep')
  expect(await allText(ui)).not.toContain('Needs you')
  expect(store.get('drafts')).toEqual({})
  await ui.unmount()
})

test('a pick counts only after Next; Send now sends the confirmed ones and the rest stay open', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2), mkq(3)])])
  await ui.press({ key: 'app/build:Q1:o1' })
  expect(await buttonText(ui, 'app/build:Q1:o1')).toBe('● Change')
  expect(await buttonText(ui, 'send-now:app/build')).toBeUndefined()
  await ui.press({ key: 'next:app/build' })
  expect(await buttonText(ui, 'send-now:app/build')).toBe('Send 1 now · 2 stay open')
  expect(await buttonText(ui, 'row:app/build:Q1')).toMatch(/^✓ Question 1: Item 1 {2}→ Change$/)
  await ui.press({ key: 'send-now:app/build' })
  expect(w.answers).toEqual([[ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Change']])
  expect(w.wakes).toHaveLength(1)
  await ui.unmount()
})

test('a failing orch answers wakes nobody, keeps the picks and shows the error', async ($, on) => {
  const { ui, w, store } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])])
  w.answered = { exitCode: 1, stdout: '', stderr: 'orch: boom\nmore' }
  await ui.press({ key: 'app/build:Q1:o1' })
  await ui.press({ key: 'next:app/build' })
  await ui.press({ key: 'send:app/build' })
  expect(w.answers).toHaveLength(1)
  expect(w.wakes).toEqual([])
  expect(await allText(ui)).toContain('orch answers failed: orch: boom')
  expect(JSON.stringify(store.get('drafts'))).toContain('"Q1"')
  expect(JSON.stringify(store.get('drafts'))).toContain('"Q2"')
  // The drafts are still there: a second try with orch working sends both.
  w.answered = { exitCode: 0, stdout: 'ok', stderr: '' }
  await ui.press({ key: 'send:app/build' })
  expect(w.answers[1]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Change', 'Q2', 'Keep'])
  expect(w.wakes).toEqual([WAKE_TEXT])
  expect(await allText(ui)).not.toContain('orch answers failed')
  await ui.unmount()
})

test('a draft for a question that was answered elsewhere is dropped on the refusal, the rest can go', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2), mkq(3)])])
  await ui.press({ key: 'next:app/build' })
  await ui.press({ key: 'next:app/build' })
  w.answered = { exitCode: 1, stdout: '', stderr: 'orch: Q2 is answered, not open; nothing written' }
  await ui.press({ key: 'send-now:app/build' })
  expect(w.wakes).toEqual([])
  expect(await allText(ui)).toContain('The draft for Q2 was dropped; send again.')
  w.answered = { exitCode: 0, stdout: 'ok', stderr: '' }
  await ui.press({ key: 'send-now:app/build' })
  expect(w.answers[1]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Keep'])
  await ui.unmount()
})

test('an answer travels as one argv element after `--`, never through a shell', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])])
  const nasty = `x'; rm -rf / $(id) --flag "q" \`y\``
  await ui.input({ key: 'app/build:Q1:own', text: nasty })
  expect(await allText(ui)).toContain(`● Your answer: ${nasty}`)
  await ui.press({ key: 'next:app/build' })
  await ui.press({ key: 'send:app/build' })
  const argv = w.answers[0] ?? []
  expect(argv.slice(0, 5)).toEqual([ORCH, 'answers', '-t', 'app/build', '--'])
  expect(argv.slice(5)).toEqual(['Q1', nasty, 'Q2', 'Keep'])
  expect(w.wakes).toHaveLength(1)
  await ui.unmount()
})

test('Accept confirms the recommended answers and leaves a critical question open', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2), critical(3), mkq(4, { options: [qopt('A', 'first'), qopt('B', 'second')] })])])
  expect(await buttonText(ui, 'accept:app/build')).toBe('Accept all recommended (2)')
  await ui.press({ key: 'accept:app/build' })
  expect(w.answers).toEqual([])
  expect(await buttonText(ui, 'row:app/build:Q1')).toMatch(/^✓ /)
  expect(await buttonText(ui, 'row:app/build:Q2')).toMatch(/^✓ /)
  expect(await buttonText(ui, 'row:app/build:Q3')).toMatch(/^▾ /)
  expect(await allText(ui)).toContain('Delete item 3 for good?')
  expect(await buttonText(ui, 'send-now:app/build')).toBe('Send 2 now · 2 stay open')
  await ui.press({ key: 'send-now:app/build' })
  expect(w.answers[0]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Keep', 'Q2', 'Keep'])
  await ui.unmount()
})

test('Skip for now moves on and a skipped question comes back through its row', async ($, on) => {
  const { ui } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])])
  await ui.press({ key: 'skip:app/build' })
  expect(await allText(ui)).toContain('Should item 2 change?')
  expect(await buttonText(ui, 'row:app/build:Q1')).toMatch(/skipped$/)
  // Only one question is left to ask, so there is nothing to skip to.
  expect(await buttonText(ui, 'skip:app/build')).toBeUndefined()
  await ui.press({ key: 'row:app/build:Q1' })
  expect(await allText(ui)).toContain('Should item 1 change?')
  expect(await buttonText(ui, 'row:app/build:Q1')).not.toMatch(/skipped/)
  await ui.unmount()
})

test('a poll keeps the draft and the open question, and a new question joins the end', async ($, on) => {
  const { ui, w, clock } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])])
  await ui.press({ key: 'app/build:Q1:o1' })
  // Q9 arrives and sorts first in the data.
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, [mkq(9), mkq(1), mkq(2)])), stderr: '' }
  await clock.advance(8000)
  expect(await allText(ui)).toContain('3 pending')
  expect(await allText(ui)).toContain('Should item 1 change?')
  expect(await buttonText(ui, 'app/build:Q1:o1')).toBe('● Change')
  await ui.press({ key: 'next:app/build' })
  expect(await allText(ui)).toContain('Should item 2 change?')
  await ui.press({ key: 'next:app/build' })
  expect(await allText(ui)).toContain('Should item 9 change?')
  await ui.unmount()
})

test('questions of another task are answered against that task', async ($, on) => {
  const other = withQs({ ...OTHER, in_cwd: true }, [mkq(1)])
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1)]), other])
  expect(await buttonText(ui, 'send:app/build')).toBe('Send 1 answer')
  expect(await buttonText(ui, 'send:other/fix')).toBe('Send 1 answer')
  await ui.press({ key: 'send:other/fix' })
  expect(w.answers).toEqual([[ORCH, 'answers', '-t', 'other/fix', '--', 'Q1', 'Keep']])
  await ui.press({ key: 'send:app/build' })
  expect(w.answers[1]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Keep'])
  await ui.unmount()
})

test('a Send for another folder\'s task records the answers, says so, and neither wakes nor compacts this session', async ($, on) => {
  const other = withQs({ ...OTHER, in_cwd: false }, [mkq(1)])
  const { ui, w } = await pane($, on, [withQs(PROGRAM, []), other])
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-compact-send' })
  await ui.press({ key: 'scope' })
  await ui.press({ key: 'task:other/fix' })
  expect(await allText(ui)).not.toContain('wakes the agent')
  expect(await allText(ui)).toContain("saved for that task's session")
  await ui.press({ key: 'send:other/fix' })
  expect(w.answers).toEqual([[ORCH, 'answers', '-t', 'other/fix', '--', 'Q1', 'Keep']])
  expect(w.wakes).toEqual([])
  expect(w.compacts).toEqual([])
  expect(w.toasts.join(' ')).toContain('next checkpoint')
  expect(w.toasts.join(' ')).not.toContain('woken')
  await ui.unmount()
})

test('the panel folds to its title', async ($, on) => {
  const { ui } = await pane($, on, [withQs(PROGRAM, [mkq(1)])])
  await ui.press({ key: 'needs-fold:app/build' })
  expect(await allText(ui)).not.toContain('Should item 1 change?')
  expect(await allText(ui)).toContain('1 pending')
  await ui.press({ key: 'needs-fold:app/build' })
  expect(await allText(ui)).toContain('Should item 1 change?')
  await ui.unmount()
})

test('a question whose options are not loaded yet says so, and can still be answered in words', async ($, on) => {
  const compact = mkq(1, { detail: 'compact', options: [] })
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [compact])])
  expect(await allText(ui)).toContain('Loading the options…')
  expect(await allText(ui)).toContain('Choose an option first')
  await ui.input({ key: 'app/build:Q1:own', text: 'my words' })
  await ui.press({ key: 'send:app/build' })
  expect(w.answers[0]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'my words'])
  await ui.unmount()
})

test('drafts survive a restart: they are read back from the store and sent', async ($, on) => {
  const draft: Draft = { pick: 'Change', custom: null, confirmed: true, skipped: false, group: UNG, answer: 'Change' }
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])], { drafts: { 'app/build': { Q1: draft } } })
  expect(await buttonText(ui, 'row:app/build:Q1')).toMatch(/^✓ Question 1/)
  expect(await allText(ui)).toContain('Should item 2 change?')
  expect(await buttonText(ui, 'send:app/build')).toBe('Send 2 answers')
  await ui.press({ key: 'send:app/build' })
  expect(w.answers[0]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Change', 'Q2', 'Keep'])
  await ui.unmount()
})

test('a draft of a question that is no longer open is dropped by the next poll', async ($, on) => {
  const draft: Draft = { pick: 'Change', custom: null, confirmed: true, skipped: false, group: UNG, answer: 'Change' }
  const { ui, w, clock, store } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])], { drafts: { 'app/build': { Q1: draft } } })
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, [mkq(2)])), stderr: '' }
  await clock.advance(8000)
  expect(store.get('drafts')).toEqual({})
  expect(await buttonText(ui, 'send:app/build')).toBe('Send 1 answer')
  await ui.unmount()
})

// -- auto-answer ----------------------------------------------------------------------------------------

test('Auto-answer is off until switched on, answers only reversible recommended questions, records Auto and wakes once', { timeoutMs: 30000 }, async ($, on) => {
  const open = [
    mkq(1), mkq(2), critical(3), mkq(4, { reversible: 'unknown' }), mkq(5, { options: [qopt('A', 'first'), qopt('B', 'second')] }),
    mkq(6, { detail: 'minimal', options: [] }),
  ]
  const { ui, w, clock } = await pane($, on, [withQs(PROGRAM, open)])
  await clock.advance(16000)
  expect(w.answers).toEqual([])
  expect(w.wakes).toEqual([])
  const assumed = [mkq(1, { status: 'assumed', answer: 'Keep', answered: '2026-10-29T10:31Z' }), mkq(2, { status: 'assumed', answer: 'Keep', answered: '2026-10-29T10:31Z' })]
  w.onAnswers = () => { w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, open.slice(2), assumed)), stderr: '' } }

  await ui.press({ key: 'tab:more' })
  expect((await buttonText(ui, 'opt-auto'))).toMatch(/^\[ \] Auto-answer/)
  await ui.press({ key: 'opt-auto' })
  expect((await buttonText(ui, 'opt-auto'))).toMatch(/^\[x\] Auto-answer/)
  expect(w.answers).toEqual([[ORCH, 'answers', '-t', 'app/build', '--auto', '--as', 'orch-pane', '--', 'Q1', 'Keep', 'Q2', 'Keep']])
  expect(w.wakes).toEqual([WAKE_TEXT])
  // Nothing more to take: no second run, no second wake.
  await clock.advance(16000)
  expect(w.answers).toHaveLength(1)
  expect(w.wakes).toHaveLength(1)

  await ui.press({ key: 'tab:history' })
  expect(await allText(ui)).toContain('◆ Auto')
  await ui.unmount()
})

test('Auto-answer leaves a question the person has already touched', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])])
  await ui.press({ key: 'app/build:Q1:o1' })
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-auto' })
  expect(w.answers).toEqual([[ORCH, 'answers', '-t', 'app/build', '--auto', '--as', 'orch-pane', '--', 'Q2', 'Keep']])
  await ui.unmount()
})

test('a refused auto-answer is not retried on every poll and shows the error', { timeoutMs: 30000 }, async ($, on) => {
  const { ui, w, clock } = await pane($, on, [withQs(PROGRAM, [mkq(1)])])
  w.answered = { exitCode: 1, stdout: '', stderr: 'orch: Q1 is not --reversible yes' }
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-auto' })
  await clock.advance(24000)
  expect(w.answers).toHaveLength(1)
  expect(w.wakes).toEqual([])
  expect(await allText(ui)).toContain('orch answers failed')
  await ui.unmount()
})

test('Auto-answer leaves another folder\'s task alone and wakes nobody for it', async ($, on) => {
  const other = withQs({ ...OTHER, in_cwd: false }, [mkq(1)])
  const { ui, w, clock } = await pane($, on, [withQs(PROGRAM, []), other])
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-auto' })
  await clock.advance(16000)
  expect(w.answers).toEqual([])
  expect(w.wakes).toEqual([])
  await ui.unmount()
})

// -- notify -------------------------------------------------------------------------------------------

test('Notify tells of a new question, not of the first look, and only while on', async ($, on) => {
  const { ui, w, clock, store } = await pane($, on, [withQs(PROGRAM, [mkq(1)])])
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-notify' })
  expect(store.get('opt:notify')).toBe(true)
  await clock.advance(8000)
  expect(w.notes).toEqual([])
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, [mkq(1), mkq(2), mkq(3)])), stderr: '' }
  await clock.advance(8000)
  expect(w.notes).toEqual(['build: 2 new questions waiting for you'])
  await ui.press({ key: 'opt-notify' })
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, [mkq(1), mkq(2), mkq(3), mkq(4)])), stderr: '' }
  await clock.advance(8000)
  expect(w.notes).toHaveLength(1)
  await ui.unmount()
})

// -- groups and 200 questions ---------------------------------------------------------------------------

function many() {
  const GROUPS: [string, number][] = [['Pane layout', 20], ['Storage', 40], ['Cleanup jobs', 31], ['Release notes', 109]]
  let n = 12
  const open = GROUPS.flatMap(([group, size]) => Array.from({ length: size }, (): StatusQuestion => {
    n += 1
    return n === 33 ? { ...critical(n), group } : mkq(n, { group })
  }))
  return open
}

test('200 questions: groups, a long list drawn as a few rows, a critical one survives Accept, batches send', async ($, on) => {
  const open = many()
  expect(open.length).toBe(200)
  const { ui, w } = await pane($, on, [withQs(PROGRAM, open, [], 12)])
  expect(await allText(ui)).toContain('200 pending')
  expect(await allText(ui)).toContain('12 of 212 sent')
  expect(await allText(ui)).toContain('Pane layout')
  expect(await allText(ui)).toContain('Release notes')
  expect(await allText(ui)).toContain('· 18 more')
  expect((await buttons(ui)).length).toBeLessThan(30)
  expect(await allText(ui)).toContain('20 left')

  // Storage is another group: its questions come from `orch status --json --question-group Storage`.
  const storage = open.filter(q => q.group === 'Storage')
  w.groups.Storage = output({ ...withQs(PROGRAM, open, [], 12), questions: { ...qblock(open, [], 12), open_group: 'Storage', open: storage } })
  await ui.press({ key: 'group:app/build:Storage' })
  expect(w.groupRuns).toHaveLength(1)
  expect(w.groupRuns[0]).toEqual([ORCH, 'status', '--json', '--cwd', '/proj', '--question-group', 'Storage'])
  expect(await allText(ui)).toContain('Delete item 33 for good?')
  expect((await buttons(ui)).length).toBeLessThan(30)
  expect(await buttonText(ui, 'accept:app/build')).toBe('Accept recommended in Storage (39)')
  await ui.press({ key: 'accept:app/build' })
  // The critical one is still the open question.
  expect(await allText(ui)).toContain('Delete item 33 for good?')
  expect(await buttonText(ui, 'send-now:app/build')).toBe('Send 39 now · 161 stay open')
  await ui.press({ key: 'send-now:app/build' })
  const argv = w.answers[0] ?? []
  expect(argv.slice(0, 5)).toEqual([ORCH, 'answers', '-t', 'app/build', '--'])
  expect(argv.length).toBe(5 + 39 * 2)
  expect(argv).not.toContain('Q33')
  expect(w.wakes).toHaveLength(1)
  await ui.unmount()
})

test('a pick made in one group survives switching to the ungrouped group', async ($, on) => {
  const open = [mkq(1, { group: 'Area A' }), mkq(2, { group: 'Area A' }), mkq(3)]
  const { ui, w, store } = await pane($, on, [withQs(PROGRAM, open)])
  w.groups[UNG] = output({ ...withQs(PROGRAM, open), questions: { ...qblock(open), open_group: UNG, open: [open[2]] } })
  await ui.press({ key: 'app/build:Q1:o1' })
  expect(JSON.stringify(store.get('drafts'))).toContain('"pick":"Change"')
  expect(JSON.stringify(store.get('drafts'))).toContain('"group":"Area A"')
  await ui.press({ key: `group:app/build:${UNG}` })
  expect(await allText(ui)).toContain('Should item 3 change?')
  expect(JSON.stringify(store.get('drafts'))).toContain('"Q1"')
  expect(JSON.stringify(store.get('drafts'))).toContain('"pick":"Change"')
  // Back in Area A the pick is still the one that shows.
  await ui.press({ key: 'group:app/build:Area A' })
  expect(await buttonText(ui, 'app/build:Q1:o1')).toBe('● Change')
  await ui.unmount()
})

test('a draft stored before groups were recorded (group null) is kept when a group opens', async ($, on) => {
  const open = [mkq(1, { group: 'Area A' }), mkq(3)]
  const old: Draft = { pick: 'Change', custom: null, confirmed: false, skipped: false, group: null, answer: null }
  const { ui, w, store } = await pane($, on, [withQs(PROGRAM, open)], { drafts: { 'app/build': { Q1: old } } })
  w.groups[UNG] = output({ ...withQs(PROGRAM, open), questions: { ...qblock(open), open_group: UNG, open: [open[1]] } })
  await ui.press({ key: `group:app/build:${UNG}` })
  expect(JSON.stringify(store.get('drafts'))).toContain('"Q1"')
  await ui.unmount()
})

test('a new question in another group does not move the open question on a poll', async ($, on) => {
  const a = [mkq(1, { group: 'Area A' }), mkq(2, { group: 'Area A' })]
  const { ui, w, clock } = await pane($, on, [withQs(PROGRAM, a)])
  await ui.press({ key: 'next:app/build' })
  expect(await allText(ui)).toContain('Should item 2 change?')
  // Q9 blocks everything, so it sorts first and its group becomes the data's open group.
  const open = [mkq(9, { group: 'Area B', blocks: 'all' }), ...a]
  w.result = { exitCode: 0, stdout: output({ ...withQs(PROGRAM, open) }), stderr: '' }
  w.groups['Area A'] = output({ ...withQs(PROGRAM, open), questions: { ...qblock(open), open_group: 'Area A', open: a } })
  await clock.advance(8000)
  expect(await allText(ui)).toContain('Should item 2 change?')
  expect(await allText(ui)).not.toContain('Should item 9 change?')
  // When Area A runs out, the data's first group takes over.
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, [open[0]!])), stderr: '' }
  await clock.advance(8000)
  expect(await allText(ui)).toContain('Should item 9 change?')
  await ui.unmount()
})

test('a group that emptied and refills on a poll does not take the open question back', async ($, on) => {
  const a = [mkq(1, { group: 'Area A' })]
  const b = [mkq(3, { group: 'Area B' }), mkq(4, { group: 'Area B' })]
  const { ui, w, clock } = await pane($, on, [withQs(PROGRAM, [...a, ...b])])
  expect(await allText(ui)).toContain('Should item 1 change?')
  // Area A is answered elsewhere: the data's first group, Area B, takes over, and the person moves to Q4.
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, b)), stderr: '' }
  await clock.advance(8000)
  await ui.press({ key: 'row:app/build:Q4' })
  expect(await allText(ui)).toContain('Should item 4 change?')
  // An agent parks a follow-up in Area A, and it sorts first.
  const open = [mkq(9, { group: 'Area A', blocks: 'all' }), ...b]
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, open)), stderr: '' }
  w.groups['Area B'] = output({ ...withQs(PROGRAM, open), questions: { ...qblock(open), open_group: 'Area B', open: b } })
  await clock.advance(8000)
  expect(await allText(ui)).toContain('Should item 4 change?')
  expect(await allText(ui)).not.toContain('Should item 9 change?')
  await ui.unmount()
})

test('a group that cannot be loaded on a poll keeps the open question for when it can', async ($, on) => {
  const a = [mkq(1, { group: 'Area A' }), mkq(2, { group: 'Area A' })]
  const { ui, w, clock } = await pane($, on, [withQs(PROGRAM, a)])
  await ui.press({ key: 'row:app/build:Q2' })
  expect(await allText(ui)).toContain('Should item 2 change?')
  const open = [mkq(9, { group: 'Area B', blocks: 'all' }), ...a]
  w.result = { exitCode: 0, stdout: output(withQs(PROGRAM, open)), stderr: '' }
  w.groups['Area A'] = 'not json'
  await clock.advance(8000)
  w.groups['Area A'] = output({ ...withQs(PROGRAM, open), questions: { ...qblock(open), open_group: 'Area A', open: a } })
  await clock.advance(8000)
  expect(await allText(ui)).toContain('Should item 2 change?')
  expect(await ui.find({ type: 'Button', key: 'row:app/build:Q1' })).toBeDefined()   // Q1 waits; Q2 is the open one
  await ui.unmount()
})

// -- History ---------------------------------------------------------------------------------------------

test('History pages ten at a time, searches, and marks auto-answers', async ($, on) => {
  const done = Array.from({ length: 25 }, (_, i) => mkq(i + 1, {
    status: i % 5 === 0 ? 'assumed' : 'answered', answer: i === 7 ? 'needle answer' : `answer ${i + 1}`, answered: '2026-10-29T10:00Z',
    group: i % 2 === 0 ? 'Even' : null,
  }))
  const { ui } = await pane($, on, [withQs(PROGRAM, [], done, 25)])
  await ui.press({ key: 'tab:history' })
  expect(await allText(ui)).toContain('Page 1 of 3')
  expect((await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('hist:'))).toHaveLength(10)
  expect(await allText(ui)).toContain('◆ Auto')
  expect(await allText(ui)).toContain('✓ Answered')
  await ui.press({ key: 'hist-next' })
  expect(await allText(ui)).toContain('Page 2 of 3')
  expect(await buttonText(ui, 'hist-prev')).toBe('‹ Newer')
  await ui.input({ key: 'hist-search', text: 'NEEDLE' })
  expect((await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('hist:'))).toHaveLength(1)
  expect(await allText(ui)).not.toContain('Page ')
  await ui.press({ key: 'hist:Q8' })
  expect(await allText(ui)).toContain('→ needle answer')
  await ui.input({ key: 'hist-search', text: 'no such thing' })
  expect(await allText(ui)).toContain('No matching questions.')
  await ui.input({ key: 'hist-search', text: 'Even' })
  expect(await allText(ui)).toContain('Page 1 of 2')
  await ui.unmount()
})

// -- pure logic -----------------------------------------------------------------------------------------

test('answerOf: a typed answer, then a pick, then the recommendation, and never a default for a critical question', () => {
  const q = mkq(1)
  const d = (over: Partial<Draft>): Draft => ({ pick: null, custom: null, confirmed: false, skipped: false, group: null, answer: null, ...over })
  expect(answerOf(q, undefined)).toBe('Keep')
  expect(answerOf(q, d({ pick: 'Change' }))).toBe('Change')
  expect(answerOf(q, d({ pick: 'Change', custom: ' words ' }))).toBe('words')
  expect(answerOf(q, d({ pick: 'Gone' }))).toBe('Keep')
  expect(pickedLabel(q, d({ custom: 'words' }))).toBeNull()
  expect(answerOf(critical(3), undefined)).toBeNull()
  expect(answerOf(critical(3), d({ pick: 'Keep it' }))).toBe('Keep it')
  expect(isCritical(critical(3))).toBe(true)
  expect(isCritical(mkq(1, { reversible: 'unknown' }))).toBe(false)
  expect(answerOf(mkq(1, { options: [qopt('A', 'x'), qopt('B', 'y')] }), undefined)).toBeNull()
})

test('option notes, meta text, queue order and the next question', () => {
  expect(optionNote(qopt('A', 'does a thing'))).toBe('does a thing')
  expect(optionNote(qopt('A', 'obvious from the name', false, 'reason'))).toBe('No explanation: obvious from the name')
  expect(optionNote({ label: 'A', text: null, kind: null, recommended: false })).toBe('No explanation: not given')
  expect(metaText(mkq(1, { blocks: '5' }))).toBe('blocks step 5 · can be changed later')
  expect(metaText(mkq(1, { blocks: 'all', reversible: 'no' }))).toBe('blocks everything · cannot be undone')
  expect(metaText(mkq(1, { blocks: null as unknown as string, reversible: 'unknown' }))).toBe('blocks everything · may not be undoable')
  expect(mergeOrder(['Q2', 'Q1', 'Q7'], ['Q9', 'Q1', 'Q2'])).toEqual(['Q2', 'Q1', 'Q9'])
  expect(mergeOrder(undefined, ['Q1', 'Q2'])).toEqual(['Q1', 'Q2'])
  const dr = (confirmed: boolean, skipped = false): Draft => ({ pick: null, custom: null, confirmed, skipped, group: null, answer: confirmed ? 'x' : null })
  expect(nextInQueue(['Q1', 'Q2', 'Q3'], { Q2: dr(true) }, 'Q1')).toBe('Q3')
  expect(nextInQueue(['Q1', 'Q2', 'Q3'], { Q3: dr(false, true) }, 'Q2')).toBe('Q1')
  expect(nextInQueue(['Q1'], undefined, 'Q1')).toBeNull()
  expect(pickCurrent(['Q1', 'Q2'], { Q1: dr(true) }, 'Q1')).toBe('Q2')
  expect(pickCurrent(['Q1', 'Q2'], undefined, 'Q2')).toBe('Q2')
  expect(pickCurrent(['Q1', 'Q2'], undefined, 'Q9')).toBe('Q1')
})

test('a multi-select question: toggled picks join with `|`; no preselect, no typed text, no accept, no auto', () => {
  const m = mkq(1, { multi: true, options: [qopt('A', 'x', true), qopt('B', 'y'), qopt('C', 'z')] })
  expect(isMulti(m)).toBe(true)
  expect(togglePick(null, 'A')).toBe('A')
  expect(togglePick('A', 'C')).toBe('A|C')
  expect(togglePick('A|C', 'A')).toBe('C')
  expect(togglePick('C', 'C')).toBeNull()
  expect(togglePick('A|C', 'Hold on')).toBe('Hold on')
  expect(togglePick('Hold on', 'B')).toBe('B')
  const d = (pick: string | null, custom: string | null = null): Draft => ({ pick, custom, confirmed: false, skipped: false, group: null, answer: null })
  expect(answerOf(m, d(null))).toBeNull()                 // the recommended option is not preselected
  expect(answerOf(m, d('A|C'))).toBe('A|C')
  expect(answerOf(m, d('A|Z'))).toBeNull()
  expect(answerOf(m, d('Hold on'))).toBe('Hold on')
  expect(answerOf(m, d(null, 'free text'))).toBeNull()    // orch refuses free text here
  expect(isChosen(m, d('A|C'), 'C')).toBe(true)
  expect(isChosen(m, d('A|C'), 'B')).toBe(false)
  expect(acceptable(m, undefined)).toBe(false)
  expect(autoPairs([m], undefined)).toEqual([])
})

test('answersArgv puts every answer after `--`; staleQuestion and autoPairs read their input strictly', () => {
  expect(answersArgv(false, '/o', 'a/b', [['Q1', '--x'], ['Q2', 'y z']], false))
    .toEqual(['/o', 'answers', '-t', 'a/b', '--', 'Q1', '--x', 'Q2', 'y z'])
  expect(answersArgv(true, 'C:/o', 'a/b', [['Q1', 'k']], true))
    .toEqual(['python', 'C:/o', 'answers', '-t', 'a/b', '--auto', '--as', 'orch-pane', '--', 'Q1', 'k'])
  expect(staleQuestion('orch: Q12 is answered, not open; nothing written')).toBe('Q12')
  expect(staleQuestion('orch: no question Q4 in /x; nothing written')).toBe('Q4')
  expect(staleQuestion('orch: boom')).toBeNull()
  const drafts: Record<string, Draft> = { Q2: { pick: 'Keep', custom: null, confirmed: false, skipped: false, group: null, answer: null } }
  expect(autoPairs([mkq(1), mkq(2), critical(3), mkq(4, { reversible: 'unknown' }), mkq(5, { detail: 'compact', options: [] })], drafts))
    .toEqual([['Q1', 'Keep']])
  expect(acceptable(mkq(1), undefined)).toBe(true)
  expect(acceptable(critical(3), undefined)).toBe(false)
  expect(acceptable(mkq(1), { Q1: { ...drafts.Q2!, confirmed: true } })).toBe(false)
})

test('historyRows keeps answered and assumed ones that match, pages are counted', () => {
  const h = [mkq(1, { status: 'answered', answer: 'yes' }), mkq(2, { status: 'withdrawn' }), mkq(3, { status: 'assumed', topic: 'Colour', answer: 'blue' })]
  expect(historyRows(h, '').map(q => q.id)).toEqual(['Q1', 'Q3'])
  expect(historyRows(h, ' COLOUR ').map(q => q.id)).toEqual(['Q3'])
  expect(historyRows(h, 'zzz')).toEqual([])
  expect([0, 1, 10, 11, 25].map(n => pageCount(n))).toEqual([1, 1, 1, 2, 3])
})

test('on Windows the answers run through python, from USERPROFILE', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { exitCode: 0, stdout: output(withQs(PROGRAM, [mkq(1)])), stderr: '' }, { OS: 'Windows_NT', USERPROFILE: 'C:\\home\\u', HOME: '/c/home/u' })
  memStore(on)
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'send:app/build' })
  expect(w.answers).toEqual([['python', ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Keep']])
  await ui.unmount()
})

test('mobile draws the questions and takes picks; typed answers need an Input it does not have', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { exitCode: 0, stdout: output(withQs(PROGRAM, [mkq(1), mkq(2)])), stderr: '' })
  memStore(on)
  await start($, clock)
  const ui = await $.ui.mount({ ...PANE, surface: 'mobile' })
  expect(await allText(ui)).toContain('Should item 1 change?')
  expect(await ui.find({ type: 'Input', key: 'app/build:Q1:own' })).toBeUndefined()
  await ui.press({ key: 'app/build:Q1:o1' })
  await ui.press({ key: 'next:app/build' })
  await ui.press({ key: 'send:app/build' })
  expect(w.answers[0]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Change', 'Q2', 'Keep'])
  await ui.unmount()
})

// -- Hold on and the full window of a long list ---------------------------------------------------------

test('Hold on closes every list of options, is never preselected, and sends the answer "Hold on"', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), critical(2)])])
  expect(await buttonText(ui, 'app/build:Q1:hold')).toBe('○ Hold on')
  expect(await buttonText(ui, 'app/build:Q1:o0')).toMatch(/^● Keep/)
  const keys = (await buttons(ui)).filter(k => k.startsWith('app/build:Q1:'))
  expect(keys).toEqual(['app/build:Q1:o0', 'app/build:Q1:o1', 'app/build:Q1:hold'])
  expect(await allText(ui)).toContain('Pause this question; nothing changes until you come back to it.')
  await ui.press({ key: 'app/build:Q1:hold' })
  expect(await buttonText(ui, 'app/build:Q1:hold')).toBe('● Hold on')
  expect(await buttonText(ui, 'app/build:Q1:o0')).toBe('○ Keep · Recommended')
  await ui.press({ key: 'next:app/build' })
  // The critical question has Hold on too, and nothing chosen.
  expect(await buttonText(ui, 'app/build:Q2:hold')).toBe('○ Hold on')
  expect(await allText(ui)).toContain('Choose an option first')
  await ui.press({ key: 'app/build:Q2:o0' })
  await ui.press({ key: 'send:app/build' })
  expect(w.answers[0]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Hold on', 'Q2', 'Delete'])
  await ui.unmount()
})

test('Accept and auto-answer never take Hold on', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1), mkq(2)])])
  await ui.press({ key: 'accept:app/build' })
  await ui.press({ key: 'send:app/build' })
  expect(w.answers[0]).toEqual([ORCH, 'answers', '-t', 'app/build', '--', 'Q1', 'Keep', 'Q2', 'Keep'])
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-auto' })
  expect(JSON.stringify(w.answers)).not.toContain('Hold on')
  await ui.unmount()
})

test('a question that already has a Hold on option keeps its own, with no second row', async ($, on) => {
  const own = mkq(1, { options: [qopt('Do it', 'Does it.', true), qopt('Hold on', 'Wait for the owner.')] })
  const { ui } = await pane($, on, [withQs(PROGRAM, [own])])
  expect((await buttons(ui)).filter(k => k.endsWith(':hold'))).toEqual([])
  expect(await buttonText(ui, 'app/build:Q1:o1')).toBe('○ Hold on')
  expect(await allText(ui)).toContain('Wait for the owner.')
  expect(needsHoldRow(own)).toBe(false)
  await ui.unmount()
})

test('the options of a question past the first full ones are fetched with --question-skip', async ($, on) => {
  const full = (n: number) => mkq(n)
  const tail = (n: number) => mkq(n, { detail: 'minimal', options: [], text: `Clipped ${n}`, topic: null })
  const base = [full(1), tail(2), tail(3), tail(4)]
  const { ui, w } = await pane($, on, [withQs(PROGRAM, base)])
  // The next question lacks its options, so the first poll already asked: the window from index 1 on (orch had no answer yet).
  expect(w.groupRuns).toHaveLength(1)
  w.groups[UNG] = skip => output({ ...withQs(PROGRAM, base), questions: { ...qblock(base), open: base.map((q, i) => (i < skip ? tail(i + 1) : mkq(i + 1, { text: `Whole text ${i + 1}` }))) } })
  await ui.press({ key: 'next:app/build' })
  expect(w.groupRuns[1]).toEqual([ORCH, 'status', '--json', '--cwd', '/proj', '--question-group', UNG, '--question-skip', '1'])
  expect(w.groupRuns[0]).toEqual(w.groupRuns[1])
  expect(await allText(ui)).toContain('Whole text 2')
  expect(await buttonText(ui, 'app/build:Q2:o0')).toMatch(/^● Keep/)
  expect(await allText(ui)).not.toContain('Loading the options…')
  // Q3 and Q4 came in the same window: no second fetch for them.
  await ui.press({ key: 'next:app/build' })
  await ui.press({ key: 'next:app/build' })
  expect(await buttonText(ui, 'app/build:Q4:o0')).toMatch(/^● Keep/)
  expect(w.groupRuns).toHaveLength(2)
  await ui.unmount()
})

test('skipFor starts the window at the first of the open and the next question that lacks options', () => {
  const full = (n: number) => mkq(n)
  const tail = (n: number) => mkq(n, { detail: 'compact', options: [] })
  const list = [full(1), full(2), tail(3), tail(4)]
  const order = ['Q1', 'Q2', 'Q3', 'Q4']
  expect(skipFor(list, order, undefined, 'Q1')).toBeNull()
  expect(skipFor(list, order, undefined, 'Q2')).toBe(2)
  expect(skipFor(list, order, undefined, 'Q3')).toBe(2)
  expect(skipFor(list, order, undefined, 'Q4')).toBe(3)
  expect(skipFor(list, order, undefined, null)).toBeNull()
  const full2 = { 'app/build\nQ3': mkq(3) }
  const t = { id: 'app/build', questions: qblock(list) } as unknown as StatusTask
  expect(openListOf(t, null, {}, full2).map(q => q.detail)).toEqual(['full', 'full', 'full', 'compact'])
})

// -- requested compaction ---------------------------------------------------------------------------------

const IDLE_PROGRAM = { ...PROGRAM, running: { roles: [], last_spawn: null } }
const TURN = { answer: '', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' } as const
const askCompact = ($: Engine, instructions?: string) =>
  $.tool.call({ tool: COMPACT_TOOL_FULL, ...(instructions === undefined ? {} : { instructions }) })
const endTurn = async ($: Engine, clock: MockClock, extra: Record<string, unknown> = {}) => {
  await $.turn.complete({ ...TURN, ...extra } as typeof TURN)
  await clock.settle()
}

test('the compaction tool is registered at session start', async ($, on) => {
  const { w } = await pane($, on, [IDLE_PROGRAM])
  expect(w.tools).toEqual(['compact_when_idle'])
  expect(COMPACT_TOOL_FULL).toBe('mcp__orch__compact_when_idle')
})

test('a request made while nothing runs compacts once, when the turn ends, with the caller\'s instructions, then wakes the agent', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM])
  const r = await askCompact($, 'keep the plan and step 7')
  expect(String(r.result ?? '')).toContain('Compaction requested')
  // Not during the turn.
  expect(w.compacts).toEqual([])
  await endTurn($, clock)
  expect(w.compacts).toEqual(['keep the plan and step 7'])
  expect(w.wakes).toEqual([COMPACT_WAKE_TEXT])
  expect(COMPACT_WAKE_TEXT).not.toContain('keep the plan')
  expect(w.order).toEqual(['compact', 'wake'])
  // At most once per request.
  await endTurn($, clock)
  await endTurn($, clock)
  expect(w.compacts).toHaveLength(1)
  expect(w.wakes).toHaveLength(1)
})

test('two calls in one turn make one request', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM])
  await askCompact($, 'first')
  const again = await askCompact($)
  expect(String(again.result ?? '')).toContain('already requested')
  await endTurn($, clock)
  expect(w.compacts).toEqual(['first'])
})

test('the tool refuses, with a reason, while a subagent runs or a spawn has not returned', async ($, on) => {
  const { w, clock } = await pane($, on, [PROGRAM])
  const refused = await askCompact($, 'x')
  expect(refused.deny ?? '').toContain('a subagent is running (implementer; task app/build)')
  expect(refused.deny ?? '').toContain('Ask again at a checkpoint')
  await endTurn($, clock)
  expect(w.compacts).toEqual([])
  expect(w.wakes).toEqual([])

  // A spawn recorded and not returned, with no role row yet: mid-spawn.
  const spawning = { ...PROGRAM, running: { roles: [], last_spawn: { ...PROGRAM.running.last_spawn, returned: false } } }
  w.result = { exitCode: 0, stdout: output(spawning), stderr: '' }
  const mid = await askCompact($)
  expect(mid.deny ?? '').toContain('implementer was spawned and has not returned (task app/build)')
  await endTurn($, clock)
  expect(w.compacts).toEqual([])
})

test('tasks of other folders do not hold a compaction back', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM, { ...PROGRAM, id: 'other/run', in_cwd: false }])
  const r = await askCompact($)
  expect(r.deny).toBeUndefined()
  await endTurn($, clock)
  expect(w.compacts).toHaveLength(1)
})

test('an unreadable orch status refuses too', async ($, on) => {
  const { w } = await pane($, on, [IDLE_PROGRAM])
  w.result = { exitCode: 1, stdout: '', stderr: 'orch: broken' }
  expect((await askCompact($)).deny ?? '').toContain('orch status failed: orch: broken')
})

test('a request is dropped, with a toast, when something started running before the turn ended', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM])
  await askCompact($)
  w.result = { exitCode: 0, stdout: output(PROGRAM), stderr: '' }
  await endTurn($, clock)
  expect(w.compacts).toEqual([])
  expect(w.wakes).toEqual([])
  // Dropped, not kept for later.
  w.result = { exitCode: 0, stdout: output(IDLE_PROGRAM), stderr: '' }
  await endTurn($, clock)
  expect(w.compacts).toEqual([])
})

test('a subagent\'s turn does not run the request', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM])
  await askCompact($)
  await endTurn($, clock, { agentId: 'a1' })
  expect(w.compacts).toEqual([])
  await endTurn($, clock)
  expect(w.compacts).toHaveLength(1)
})

test('while the turn still runs the compaction is retried a second apart, and then runs once', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM])
  await askCompact($, 'plan')
  w.compactFails = 2
  await $.turn.complete(TURN)
  await clock.settle()
  expect(w.compacts).toEqual([])
  await clock.advance(1000)
  expect(w.compacts).toEqual([])
  await clock.advance(1000)
  expect(w.compacts).toEqual(['plan'])
  expect(w.wakes).toEqual([COMPACT_WAKE_TEXT])
})

test('a compaction that never gets through is not retried until the model asks again', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM])
  await askCompact($)
  w.compactFails = 99
  await $.turn.complete(TURN)
  await clock.advance(6000)
  expect(w.compacts).toEqual([])
  expect(w.wakes).toEqual([])
  w.compactFails = 0
  await endTurn($, clock)
  expect(w.compacts).toEqual([])
  await askCompact($)
  await endTurn($, clock)
  expect(w.compacts).toHaveLength(1)
})

test('every retry checks again that nothing runs, and stops when an agent was spawned meanwhile', async ($, on) => {
  const { w, clock } = await pane($, on, [IDLE_PROGRAM])
  await askCompact($, 'plan')
  w.compactFails = 1
  await $.turn.complete(TURN)
  await clock.settle()
  expect(w.compacts).toEqual([])
  // The orchestrator spawned an agent and ended its turn while the first attempt waited.
  w.result = { exitCode: 0, stdout: output(PROGRAM), stderr: '' }
  const before = w.runs.length
  await clock.advance(1000)
  expect(w.runs.length).toBeGreaterThan(before)
  expect(w.compacts).toEqual([])
  expect(w.wakes).toEqual([])
})

test('Compact, then send: off by default; on, the answers land, the conversation compacts, then the agent is woken', async ($, on) => {
  const { ui, w, store } = await pane($, on, [withQs(IDLE_PROGRAM, [mkq(1)])])
  await ui.press({ key: 'send:app/build' })
  expect(w.compacts).toEqual([])
  expect(w.order).toEqual(['answers', 'wake'])

  w.result = { exitCode: 0, stdout: output(withQs(IDLE_PROGRAM, [mkq(2)])), stderr: '' }
  await ui.press({ key: 'tab:more' })
  expect(await buttonText(ui, 'opt-compact-send')).toMatch(/^\[ \] Compact, then send/)
  await ui.press({ key: 'opt-compact-send' })
  expect(store.get('opt:compact-send')).toBe(true)
  w.order.length = 0
  await ui.press({ key: 'send:app/build' })
  expect(w.order).toEqual(['answers', 'compact', 'wake'])
  expect(w.compacts[0]).toContain('read them back with `orch`')
  expect(w.wakes).toEqual([WAKE_TEXT, WAKE_TEXT])
  await ui.unmount()
})

test('Offer Mythos security reviews: off by default, read from `orch setting get`, a press runs `orch setting set`', async ($, on) => {
  const { ui, w } = await pane($, on, [PROGRAM])
  // Read at the session start; nothing in $.store holds it.
  expect(w.settingRuns[0]).toEqual([ORCH, 'setting', 'get', 'mythos_review'])
  await ui.press({ key: 'tab:more' })
  expect(await buttonText(ui, 'opt-mythos')).toMatch(/^\[ \] Offer Mythos security reviews/)
  await ui.press({ key: 'opt-mythos' })
  expect(w.settingRuns.some(r => r.join(' ') === `${ORCH} setting set mythos_review on`)).toBe(true)
  expect(w.mythos).toBe('on')
  expect(await buttonText(ui, 'opt-mythos')).toMatch(/^\[x\] Offer Mythos security reviews/)
  await ui.press({ key: 'opt-mythos' })
  expect(w.settingRuns.some(r => r.join(' ') === `${ORCH} setting set mythos_review off`)).toBe(true)
  expect(w.mythos).toBe('off')
  expect(await buttonText(ui, 'opt-mythos')).toMatch(/^\[ \] /)
  await ui.unmount()
})

test('Offer Mythos security reviews: the box shows the CLI value (a change made elsewhere, a refused write)', async ($, on) => {
  const { ui, w } = await pane($, on, [PROGRAM])
  w.mythos = 'on'                                   // changed from a terminal, after the session start
  await ui.press({ key: 'tab:more' })
  expect(await buttonText(ui, 'opt-mythos')).toMatch(/^\[x\] /)
  w.settingRefuses = true                           // the write fails: the box keeps what the CLI says
  await ui.press({ key: 'opt-mythos' })
  expect(w.mythos).toBe('on')
  expect(await buttonText(ui, 'opt-mythos')).toMatch(/^\[x\] /)
  await ui.unmount()
})

test('Compact, then send skips the compaction while an agent runs, and still sends and wakes', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(PROGRAM, [mkq(1)])])
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-compact-send' })
  await ui.press({ key: 'send:app/build' })
  expect(w.compacts).toEqual([])
  expect(w.order).toEqual(['answers', 'wake'])
  await ui.unmount()
})

test('Compact, then send does not compact when the answers failed', async ($, on) => {
  const { ui, w } = await pane($, on, [withQs(IDLE_PROGRAM, [mkq(1)])])
  await ui.press({ key: 'tab:more' })
  await ui.press({ key: 'opt-compact-send' })
  w.answered = { exitCode: 1, stdout: '', stderr: 'orch: boom' }
  await ui.press({ key: 'send:app/build' })
  expect(w.compacts).toEqual([])
  expect(w.wakes).toEqual([])
  await ui.unmount()
})

test('busyReason names the running roles, a spawn that has not returned, and ignores other folders', () => {
  const spawn = { ts: 't', role: 'planner', agent: 'orch-planner', tier: 'opus/high', steps: null, milestone: null, returned: false }
  expect(busyReason([task({ running: { roles: [{ role: 'implementer', state: 'in_progress', step: null, note: null }, { role: 'code-reviewer', state: 'in_progress', step: null, note: null }], last_spawn: null } })]))
    .toBe('a subagent is running (implementer, code-reviewer; task r/t)')
  expect(busyReason([task({ running: { roles: [], last_spawn: spawn } })])).toBe('planner was spawned and has not returned (task r/t)')
  expect(busyReason([task({ running: { roles: [], last_spawn: { ...spawn, returned: true } } })])).toBeNull()
  expect(busyReason([task({ in_cwd: false, running: { roles: [], last_spawn: spawn } })])).toBeNull()
  expect(busyReason([])).toBeNull()
})

// -- this session's task first ------------------------------------------------------------------------------

const MINE = { ...OTHER, id: 'elsewhere/drive', repo: 'elsewhere', slug: 'drive', in_cwd: false, this_session: true, updated: '2026-10-29T10:25Z' }
const SESSION_ID = 'sess-1'
const statusRun = (w: World) => w.runs.find(r => r.includes('status') && !r.includes('--question-group'))

test('this session\'s task in another repo is shown first, open, with its open questions', async ($, on) => {
  on('session.id', () => ({ value: SESSION_ID }))
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { exitCode: 0, stdout: output(PROGRAM, withQs(MINE, [mkq(1)])), stderr: '' })
  await start($, clock)
  expect(statusRun(w)).toEqual([ORCH, 'status', '--json', '--cwd', '/proj', '--session', SESSION_ID])
  const ui = await $.ui.mount(PANE60('terminal'))
  const heads = (await ui.findAll({ type: 'Button' })).map(b => b.key ?? '').filter(k => k.startsWith('task:'))
  expect(heads).toEqual(['task:elsewhere/drive', 'task:app/build'])
  const text = await allText(ui)
  expect(await ui.find({ type: 'Text', text: /^this session$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^this repo$/ })).toBeDefined()
  expect(text).toContain('Should item 1 change?')
  // The count of tasks elsewhere leaves this session's task out.
  expect((await ui.find({ type: 'Button', key: 'scope' }))?.text).toBe('[this repo]')
  await ui.unmount()
})

test('a task that is this session\'s and in this folder appears once, under this session', async ($, on) => {
  on('session.id', () => ({ value: SESSION_ID }))
  const clock = mock.clock(on, { now: NOW })
  world(on, { exitCode: 0, stdout: output({ ...PROGRAM, this_session: true }), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  const heads = (await ui.findAll({ type: 'Button' })).map(b => b.key ?? '').filter(k => k.startsWith('task:'))
  expect(heads).toEqual(['task:app/build'])
  const text = await allText(ui)
  expect(text).toContain('this session')
  expect(await ui.find({ type: 'Text', text: /^this repo$/ })).toBeUndefined()
  await ui.unmount()
})

test('with an older orch (no this_session) or no session id the pane is as before', async ($, on) => {
  on('session.id', () => { throw new Error('no session id') })
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { exitCode: 0, stdout: OUTPUT, stderr: '' })
  await start($, clock)
  expect(w.runs).toEqual([[ORCH, 'status', '--json', '--cwd', '/proj']])
  const ui = await $.ui.mount(PANE60('terminal'))
  const text = await allText(ui)
  expect(text).not.toContain('this session')
  expect(await ui.find({ type: 'Text', text: /^this repo$/ })).toBeUndefined()
  expect((await ui.find({ type: 'Button', key: 'scope' }))?.text).toBe('[this repo · 1 elsewhere]')
  expect(await ui.find({ type: 'Button', key: 'task:other/fix' })).toBeUndefined()
  await ui.unmount()
})

// The mock's refusal is the real one: `<plugin>/bin/orch status --json --session x` on an orch without the flag
// prints `orch: unrecognized argument '--session'` and exits 1.
test('an id but an older orch that rejects --session: run again without it, the folder\'s tasks still show', async ($, on) => {
  on('session.id', () => ({ value: SESSION_ID }))
  const clock = mock.clock(on, { now: NOW })
  const w = world(on)
  w.refuseSession = true
  await start($, clock)
  expect(w.runs).toEqual([[ORCH, 'status', '--json', '--cwd', '/proj', '--session', SESSION_ID], [ORCH, 'status', '--json', '--cwd', '/proj']])
  const ui = await $.ui.mount(PANE60('terminal'))
  const text = await allText(ui)
  expect(text).not.toContain('this session')
  expect(text).not.toContain('failed')
  expect((await ui.find({ type: 'Button', key: 'scope' }))?.text).toBe('[this repo · 1 elsewhere]')
  // The refusal is remembered: the next fetch goes straight to the plain command.
  w.runs.length = 0
  await clock.advance(8000)
  expect(w.runs.length).toBeGreaterThan(0)
  expect(w.runs.every(r => !r.includes('--session'))).toBe(true)
  await ui.unmount()
})

test('an id and an orch that fails for another reason: no retry, the error shows', async ($, on) => {
  on('session.id', () => ({ value: SESSION_ID }))
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { exitCode: 1, stdout: '', stderr: 'orch: boom' })
  await start($, clock)
  expect(w.runs).toEqual([[ORCH, 'status', '--json', '--cwd', '/proj', '--session', SESSION_ID]])
})

test('this session\'s task comes first with all tasks shown, newest first, and other tasks follow', async ($, on) => {
  on('session.id', () => ({ value: SESSION_ID }))
  const clock = mock.clock(on, { now: NOW })
  const older = { ...MINE, id: 'x/old', slug: 'old', updated: '2026-10-29T09:00Z' }
  world(on, { exitCode: 0, stdout: output(OTHER, older, PROGRAM, MINE), stderr: '' })
  await start($, clock)
  const ui = await $.ui.mount(PANE60('terminal'))
  await ui.press({ key: 'scope' })
  const heads = (await ui.findAll({ type: 'Button' })).map(b => b.key ?? '').filter(k => k.startsWith('task:'))
  expect(heads).toEqual(['task:elsewhere/drive', 'task:x/old', 'task:other/fix', 'task:app/build'])
  expect(await allText(ui)).toContain('other tasks')
  await ui.unmount()
})

test('splitScope and isMine: the session group, the folder group, never both', () => {
  const [a, b, c, d] = [
    task({ id: 'a', in_cwd: true }), task({ id: 'b', in_cwd: false, this_session: true, updated: '2026-10-29T10:10Z' }),
    task({ id: 'c', in_cwd: true, this_session: true, updated: '2026-10-29T10:20Z' }), task({ id: 'd', in_cwd: false, this_session: false }),
  ]
  const s = splitScope([a, b, c, d], false, T0)
  expect(s.session.map(t => t.id)).toEqual(['c', 'b'])
  expect(s.rest.map(t => t.id)).toEqual(['a'])
  expect(splitScope([a, b, c, d], true, T0).rest.map(t => t.id)).toEqual(['a', 'd'])
  expect([a, b, c, d].map(isMine)).toEqual([true, true, true, false])
  expect(isMine(task({ in_cwd: false }))).toBe(false)
  expect(splitScope([a, d], false, T0)).toEqual({ session: [], rest: [a] })
})

test('busyReason counts this session\'s task in another folder', () => {
  const spawn = { ts: 't', role: 'planner', agent: 'orch-planner', tier: 'opus/high', steps: null, milestone: null, returned: false }
  expect(busyReason([task({ in_cwd: false, this_session: true, running: { roles: [], last_spawn: spawn } })]))
    .toBe('planner was spawned and has not returned (task r/t)')
})
