import type { Register } from 'claude-code'

import { registerPane, startPane } from './pane/register'
import { registerUi, startUi } from './ui/register'

// The plugin's one module (hooks.json `modules`). It composes the orch-pane and orch-ui mods. An unmatched
// session.start may be registered only once per module, so this is the one: it runs each mod's start in its own
// try/catch, so a failing start leaves the other mod working. Every other hook is registered by the mod itself.
export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      await startPane($)
    } catch (err) {
      $.ui.log(`orch-pane: session start failed: ${String(err)}`, { to: 'debug' })
    }
    try {
      await startUi($)
    } catch (err) {
      $.ui.log(`orch-ui: session start failed: ${String(err)}`, { to: 'debug' })
    }

    return next(e)
  })

  registerPane(on)
  registerUi(on)
}
