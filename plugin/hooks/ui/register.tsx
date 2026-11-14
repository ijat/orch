import type { EngineInterface, Register } from 'claude-code'

import { loadFormat, registerFormat } from './format-view'

// orch-ui's session-start work. plugin/hooks/register.tsx runs it from the plugin's one session.start hook (a second
// unmatched one on that event is refused), in its own try/catch. The settings it owns (the Format rows) are drawn by
// orch-pane's More tab, under the "orch-ui" heading; this mod has no pane or command of its own.
export async function startUi($: EngineInterface): Promise<void> {
  void loadFormat($)
}

export const registerUi: Register = on => {
  registerFormat(on)
}
