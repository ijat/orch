import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine, MockClock } from 'claude-code/testing'

// The plugin's one module (hooks/register.tsx) runs both mods' session starts, each in its own try/catch. Neither start
// registers a bare command (/orch:pane is the plugin's command file, served by a command.run hook), and
// neither makes an engine call left uncaught, so no test here can make one start fail: this checks what both do.

type World = { commands: string[]; tools: string[]; logs: string[]; clock: MockClock }

function world(on: On): World {
  const clock = mock.clock(on, { now: Date.parse('2026-11-10T10:00Z') })
  const w: World = { commands: [], tools: [], logs: [], clock }
  mock.env(on, { HOME: '/home/u' })
  on('session.cwd', () => ({ value: '/proj' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', (_, e) => {
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
  const w = world(on)
  await start($, w)
  expect(w.commands).toEqual([])
  expect(w.tools).toEqual(['compact_when_idle'])
  expect(w.logs.some(l => l.includes('session start failed'))).toBe(false)
})
