import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine, MockClock } from 'claude-code/testing'

// The plugin's one module (hooks/register.tsx) runs each mod's session start in its own try/catch: a failing start of one
// mod leaves the other registered. These tests make one start throw and look at what the other still did.

type World = { commands: string[]; tools: string[]; logs: string[]; clock: MockClock }

function world(on: On, failing: 'orch-pane' | 'orch-ui' | null): World {
  const clock = mock.clock(on, { now: Date.parse('2026-11-10T10:00Z') })
  const w: World = { commands: [], tools: [], logs: [], clock }
  mock.env(on, { HOME: '/home/u' })
  on('session.cwd', () => ({ value: '/proj' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', (_, e) => {
    if (e.name === failing) throw new Error(`${e.name} start failed`)
    w.commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('tool.register', (_, e) => {
    w.tools.push(e.name)
    return { value: { tool: `mcp__orch__${e.name}` } }
  })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.panes', () => ({ value: [] }))
  on('ui.log', (_, e) => {
    w.logs.push(JSON.stringify(e))
    return { value: undefined }
  })
  on('process.run', () => ({ value: { exitCode: 1, stdout: '', stderr: 'no orch', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('store.get', () => ({ value: undefined }))
  return w
}

const start = async ($: Engine, w: World) => {
  await $.session.start({ cwd: '/proj', surface: 'terminal', isInteractive: true })
  await w.clock.settle() // the pane's first status run and the stored settings finish before the test ends
}

test('both mods start in the one session.start', async ($, on) => {
  const w = world(on, null)
  await start($, w)
  expect(w.commands).toEqual(['orch-pane', 'orch-ui'])
  expect(w.tools).toEqual(['compact_when_idle'])
})

test('a failing orch-pane start leaves orch-ui started', async ($, on) => {
  const w = world(on, 'orch-pane')
  await start($, w)
  expect(w.commands).toEqual(['orch-ui'])
  expect(w.logs.some(l => l.includes('orch-pane: session start failed'))).toBe(true)
})

test('a failing orch-ui start leaves orch-pane started', async ($, on) => {
  const w = world(on, 'orch-ui')
  await start($, w)
  expect(w.commands).toEqual(['orch-pane'])
  expect(w.tools).toEqual(['compact_when_idle'])
  expect(w.logs.some(l => l.includes('orch-ui: session start failed'))).toBe(true)
})
