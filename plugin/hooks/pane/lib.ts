// Pure logic of the pane: no `$`, no JSX. register.tsx draws with it; the tests call it directly.
import type { Draft, IndexStatus, StatusOption, StatusQuestion, StatusRole, StatusTask } from '../../types'

// -- small text helpers ---------------------------------------------------------------------------

export const clip = (text: string, width: number) =>
  text.length <= width ? text : `${text.slice(0, Math.max(1, width - 1))}…`

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))

// -- states ---------------------------------------------------------------------------------------

export const isRunning = (t: StatusTask) =>
  t.running.roles.length > 0 || (t.running.last_spawn !== null && !t.running.last_spawn.returned)

// The role's own spawn. `spawns` has one per running role; an orch older than that only has `last_spawn`.
export const spawnOf = (t: StatusTask, role: string | null) => {
  const { spawns, last_spawn: last } = t.running
  return spawns?.find(s => s.role === role) ?? (last && last.role === role ? last : null)
}

// Theme keys, so the colors follow the person's theme.
export function stateColor(state: string) {
  if (/done|merged|complete/.test(state)) return 'success'
  if (/block|fail|error|stop|abort/.test(state)) return 'error'
  if (/await|question|pause|wait/.test(state)) return 'warning'
  return 'claude'
}

// A state in which work is supposed to be moving (not finished, failed, or waiting on the person).
export const isActiveState = (state: string) => stateColor(state) === 'claude'

// -- time -----------------------------------------------------------------------------------------

export function elapsed(fromIso: string | null, now: number | null) {
  if (!fromIso || now === null) return null
  const from = Date.parse(fromIso)
  if (Number.isNaN(from)) return null
  const min = Math.max(0, Math.floor((now - from) / 60000))
  if (min < 60) return `${min}m`
  if (min < 48 * 60) return `${Math.floor(min / 60)}h ${min % 60}m`
  return `${Math.floor(min / 1440)}d`
}

export const ago = (fromIso: string | null, now: number | null) => {
  const e = elapsed(fromIso, now)
  return e === null ? null : `${e} ago`
}

// Local wall-clock HH:MM.
export const hhmm = (ms: number) => {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const LOG_RE = /^(\S+)\s+([\w-]+):\s+([\s\S]*)$/

export type Activity = { who: string; text: string; at: string | null }

// "2026-10-29T15:17Z implementer: started step(s) 6" → who, text, at.
export function lastActivity(t: StatusTask): Activity | null {
  const m = t.last_log ? LOG_RE.exec(t.last_log.trim()) : null
  if (m) {
    const [, at = '', who = '', rest = ''] = m
    let text = rest.trim()
    const again = `${who}:`
    while (text.startsWith(again)) text = text.slice(again.length).trim()
    return { who, text: text.replace(/\s+/g, ' '), at }
  }
  if (t.updated) return { who: '', text: 'updated', at: t.updated }
  return null
}

function parsed(iso: string | null | undefined) {
  if (!iso) return null
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? null : ms
}

// Minutes since the later of `updated` and the last log line; null when neither parses.
export function idleMinutes(t: StatusTask, now: number | null): number | null {
  if (now === null) return null
  const logAt = t.last_log ? parsed(LOG_RE.exec(t.last_log.trim())?.[1]) : null
  const times = [parsed(t.updated), logAt].filter((x): x is number => x !== null)
  if (times.length === 0) return null
  return Math.max(0, Math.floor((now - Math.max(...times)) / 60000))
}

export const STALE_MIN = 30

export const isStale = (t: StatusTask, now: number | null) =>
  isActiveState(t.state) && !isRunning(t) && (idleMinutes(t, now) ?? 0) > STALE_MIN

export const needsAttention = (t: StatusTask, now: number | null) =>
  (t.questions_open ?? 0) > 0 || !!t.error || isStale(t, now)

// Stable: tasks that need the person first, otherwise the input order.
export function sortByAttention(tasks: readonly StatusTask[], now: number | null): StatusTask[] {
  return tasks
    .map((t, i) => ({ t, i, a: needsAttention(t, now) ? 0 : 1 }))
    .sort((x, y) => x.a - y.a || x.i - y.i)
    .map(x => x.t)
}

// A task of this folder, or one this very session drives (`this_session`, set by `orch status --json --session`; absent on
// an older orch, then only the folder counts).
export const isMine = (t: StatusTask) => t.in_cwd === true || t.this_session === true

// The tasks to show: this session's first (most recently updated first), then the rest by attention. With `all` off the
// rest is this folder's tasks; a task of this session is never in both.
export function splitScope(tasks: readonly StatusTask[], all: boolean, now: number | null): { session: StatusTask[]; rest: StatusTask[] } {
  const lastAt = (t: StatusTask) => {
    const logAt = t.last_log ? parsed(LOG_RE.exec(t.last_log.trim())?.[1]) : null
    return Math.max(parsed(t.updated) ?? -Infinity, logAt ?? -Infinity)
  }
  const session = tasks
    .filter(t => t.this_session === true)
    .map((t, i) => ({ t, i, at: lastAt(t) }))
    .sort((x, y) => y.at - x.at || x.i - y.i)
    .map(x => x.t)
  const rest = sortByAttention(tasks.filter(t => t.this_session !== true && (all || t.in_cwd === true)), now)
  return { session, rest }
}

const baseName = (p: string) => p.replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? p

// Where a task lives: "myapp · [WT] orch-myapp-0006" (the worktree's folder), or just the repo name.
export function placeText(t: StatusTask): string {
  const repo = t.repo ?? (t.repo_path ? baseName(t.repo_path) : null)
  const wt = t.worktree ? `[WT] ${baseName(t.worktree)}` : null
  return [repo, wt].filter(Boolean).join(' · ')
}

export const scopeLabel = (all: boolean, elsewhere: number) =>
  all ? '[all tasks]' : elsewhere > 0 ? `[this repo · ${elsewhere} elsewhere]` : '[this repo]'

// -- code index -----------------------------------------------------------------------------------

export const STALE_ANSWER_WARN = 10

// Bytes as "660 KB", "1.4 GB".
export function bytesText(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let u = 0
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024
    u++
  }
  return u === 0 ? `${Math.round(v)} B` : `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[u]}`
}

export const secondsText = (s: number) => (s < 1 ? `${Math.round(s * 1000)} ms` : s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`)

// `orch-index status --json` output → IndexStatus, or null when it is not schema 1.
export function parseIndex(text: string): IndexStatus | null {
  try {
    const v = JSON.parse(text) as Partial<IndexStatus> | null
    const states = ['none', 'current', 'stale', 'outdated']
    return v && v.schema === 1 && typeof v.state === 'string' && states.includes(v.state) ? (v as IndexStatus) : null
  } catch {
    return null
  }
}

// Theme key and words for the index state: latest / stale / outdated / not built.
export function indexLook(ix: IndexStatus): { color: string; words: string } {
  if (ix.indexing) return { color: 'claude', words: 'indexing…' }
  switch (ix.state) {
    case 'current':
      return { color: 'success', words: 'latest' }
    case 'stale':
      return { color: 'warning', words: ix.commits_behind ? `stale · ${ix.commits_behind} behind` : 'stale' }
    case 'outdated':
      return { color: 'warning', words: 'outdated' }
    default:
      return { color: 'error', words: 'not built' }
  }
}

export const indexNeedsAnswerWarning = (ix: IndexStatus) => (ix.usage?.stale_answer_pct ?? 0) > STALE_ANSWER_WARN

// The collapsed row's facts: size and how long ago it was revised.
export function indexHeadline(ix: IndexStatus, now: number | null): string {
  const parts = [ix.db_bytes === null ? null : bytesText(ix.db_bytes), ix.indexed_at ? `revised ${ago(ix.indexed_at, now) ?? ix.indexed_at}` : null]
  return parts.filter(Boolean).join(' · ')
}

const SPARK = '▁▂▃▄▅▆▇█'

// One block per run, oldest first, scaled to the slowest.
export function sparkline(values: readonly number[]): string {
  const max = Math.max(...values)
  if (values.length === 0 || !(max > 0)) return SPARK[0]!.repeat(values.length)
  return values.map(v => SPARK[clamp(Math.round((v / max) * (SPARK.length - 1)), 0, SPARK.length - 1)]).join('')
}

const count = (n: number) => n.toLocaleString('en-US')

const pct = (n: number) => `${Number.isInteger(n) ? n : n.toFixed(1)}%`

export type DetailRow = { label: string; text: string; warn?: true }

// The expanded section's rows; each omitted when the index does not report it.
export function indexDetails(ix: IndexStatus, now: number | null): DetailRow[] {
  const rows: DetailRow[] = []
  const short = (sha: string | null) => (sha ? sha.slice(0, 10) : null)
  if (ix.state === 'none') {
    rows.push({ label: 'index', text: 'not built', warn: true }, { label: 'fix', text: 'run `orch-index index`' })
    return rows
  }
  if (ix.indexed_commit) {
    const behind = ix.state === 'stale' ? (ix.commits_behind ? ` · ${ix.commits_behind} commit${ix.commits_behind === 1 ? '' : 's'} behind HEAD ${short(ix.head_commit)}` : ` · HEAD is ${short(ix.head_commit)}`) : ''
    rows.push({ label: 'commit', text: `${short(ix.indexed_commit)}${behind}` })
  }
  if (ix.state === 'outdated') {
    rows.push({ label: 'extractor', text: `built by v${ix.extractor_version ?? '?'}, this orch-index is v${ix.binary_extractor_version ?? '?'}`, warn: true })
  }
  if (ix.indexed_at) rows.push({ label: 'revised', text: `${ago(ix.indexed_at, now) ?? ix.indexed_at} · ${ix.indexed_at}` })
  if (ix.counts) {
    const c = ix.counts
    rows.push({ label: 'contents', text: `${count(c.files)} files · ${count(c.symbols)} symbols · ${count(c.refs)} refs · ${count(c.includes)} includes` })
  }
  const langs = Object.entries(ix.languages ?? {}).sort((a, b) => b[1] - a[1])
  if (langs.length) rows.push({ label: 'languages', text: langs.map(([k, n]) => `${k} ${count(n)}`).join(' · ') })
  if (ix.parse_error_files) {
    rows.push({ label: 'parse errors', text: `${count(ix.parse_error_files)} files, ${count(ix.parse_error_structural ?? 0)} at declaration level`, warn: true })
  }
  const last = ix.last_index
  if (last) {
    const rate = last.files_per_sec ? ` · ${Math.round(last.files_per_sec)} files/s` : ''
    rows.push({ label: 'last run', text: `${last.kind} · ${secondsText(last.seconds)} · ${count(last.extracted)} extracted${last.reused !== undefined ? `, ${count(last.reused)} reused` : ''}${rate}` })
  }
  if (ix.history && ix.history.length > 1) {
    const runs = [...ix.history].reverse()
    rows.push({ label: 'run time', text: `${sparkline(runs.map(h => h.seconds))}  last ${runs.length} runs, slowest ${secondsText(Math.max(...runs.map(h => h.seconds)))}` })
  }
  const u = ix.usage
  if (u && u.queries > 0) {
    rows.push({ label: 'queries', text: `${count(u.queries)} in ${u.window_days}d${u.last_query_at ? ` · last ${ago(u.last_query_at, now) ?? u.last_query_at}` : ''}` })
    for (const [name, v] of Object.entries(u.by_command ?? {}).sort((a, b) => b[1].n - a[1].n)) {
      rows.push({ label: `  ${name}`, text: `${count(v.n)} · p50 ${v.p50_ms} ms · p95 ${v.p95_ms} ms` })
    }
    rows.push({ label: 'answers', text: `${pct(u.zero_hit_pct)} empty · ${pct(u.stale_answer_pct)} from a stale index`, ...(indexNeedsAnswerWarning(ix) ? { warn: true as const } : {}) })
    const mix = Object.entries(u.label_mix ?? {}).filter(([, f]) => f > 0).sort((a, b) => b[1] - a[1])
    if (mix.length) rows.push({ label: 'trust', text: mix.map(([k, f]) => `${k.replace('_', '-')} ${pct(Math.round(f * 100))}`).join(' · ') })
  }
  if (ix.state !== 'current') rows.push({ label: 'fix', text: 'run `orch-index index`' })
  return rows
}

// -- markdown-lite --------------------------------------------------------------------------------

export type MdRun = { text: string; code?: true; bold?: true; key?: true }
export type MdLine = { lead: string; runs: MdRun[] }

type Style = Omit<MdRun, 'text'>
type Word = MdRun[]

const sameStyle = (a: Style, b: Style) => !!a.code === !!b.code && !!a.bold === !!b.bold && !!a.key === !!b.key

function styleOf(r: MdRun): Style {
  const s: Style = {}
  if (r.code) s.code = true
  if (r.bold) s.bold = true
  if (r.key) s.key = true
  return s
}

// Inline marks: `code` and **bold**; an unclosed mark stays literal.
function inline(text: string): MdRun[] {
  const runs: MdRun[] = []
  const re = /`([^`]+)`|\*\*([^*]+?)\*\*/g
  let last = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) runs.push({ text: text.slice(last, m.index) })
    if (m[1] !== undefined) runs.push({ text: m[1], code: true })
    else runs.push({ text: m[2] ?? '', bold: true })
    last = m.index + m[0].length
  }
  if (last < text.length) runs.push({ text: text.slice(last) })
  return runs
}

// A bullet's leading `key:` or `**key:**` becomes a key run.
function keyed(content: string): MdRun[] {
  const bold = /^\*\*([^*\s][^*]{0,23}):\*\*\s*([\s\S]*)$/.exec(content)
  if (bold) return [{ text: `${bold[1]}:`, key: true }, ...(bold[2] ? [{ text: ' ' }, ...inline(bold[2])] : [])]
  const plain = /^([^\s:`*][^:`*]{0,23}):(?:\s+([\s\S]*))?$/.exec(content)
  if (plain && (plain[1] ?? '').split(' ').length <= 3) {
    return [{ text: `${plain[1]}:`, key: true }, ...(plain[2] ? [{ text: ' ' }, ...inline(plain[2])] : [])]
  }
  return inline(content)
}

// Runs → words (no whitespace inside a word; a word may mix styles, as `foo`s).
function words(runs: MdRun[]): Word[] {
  const out: Word[] = []
  let cur: Word = []
  for (const r of runs) {
    for (const piece of r.text.split(/(\s+)/)) {
      if (piece === '') continue
      if (/^\s+$/.test(piece)) {
        if (cur.length) out.push(cur)
        cur = []
        continue
      }
      cur.push({ ...styleOf(r), text: piece })
    }
  }
  if (cur.length) out.push(cur)
  return out
}

const wordLen = (w: Word) => w.reduce((n, r) => n + r.text.length, 0)

function pushRun(runs: MdRun[], r: MdRun) {
  const prev = runs[runs.length - 1]
  if (prev && sameStyle(prev, r)) prev.text += r.text
  else runs.push({ ...r })
}

// Cut a word into pieces of at most `room` characters, keeping styles.
function hardBreak(w: Word, room: number): Word[] {
  const pieces: Word[] = []
  let cur: Word = []
  let len = 0
  for (const r of w) {
    let text = r.text
    while (text.length > 0) {
      const take = Math.min(room - len, text.length)
      cur.push({ ...styleOf(r), text: text.slice(0, take) })
      len += take
      text = text.slice(take)
      if (len === room) {
        pieces.push(cur)
        cur = []
        len = 0
      }
    }
  }
  if (cur.length) pieces.push(cur)
  return pieces
}

function wrapRuns(runs: MdRun[], lead: string, cont: string, width: number): MdLine[] {
  const lines: MdLine[] = []
  let line: MdLine = { lead, runs: [] }
  let len = 0
  const room = () => Math.max(1, width - line.lead.length)
  const flush = () => {
    lines.push(line)
    line = { lead: cont, runs: [] }
    len = 0
  }
  for (const w of words(runs)) {
    const wl = wordLen(w)
    if (len > 0 && len + 1 + wl > room()) flush()
    if (wl > room() - (len > 0 ? len + 1 : 0)) {
      // Longer than a whole line: start it on its own line and cut it.
      if (len > 0) flush()
      const parts = hardBreak(w, room())
      parts.forEach((p, i) => {
        if (i > 0) flush()
        for (const r of p) pushRun(line.runs, r)
        len = wordLen(p)
      })
      continue
    }
    if (len > 0) {
      // The space takes the style shared by both neighbours (a space inside `a b`), else none.
      const prev = line.runs[line.runs.length - 1]
      const next = w[0]
      pushRun(line.runs, prev && next && sameStyle(prev, next) ? { ...styleOf(next), text: ' ' } : { text: ' ' })
      len += 1
    }
    for (const r of w) pushRun(line.runs, r)
    len += wl
  }
  if (line.runs.length > 0 || lines.length === 0) lines.push(line)
  return lines
}

type Block = { indent: number; marker: string | null; content: string }

const BULLET_RE = /^([-*]|\d+[.)])\s+([\s\S]*)$/

// Source lines → logical blocks; an indented plain line that continues a bullet's text joins it.
function blocks(text: string): (Block | null)[] {
  const out: (Block | null)[] = []
  for (const raw of text.replace(/\t/g, '  ').split('\n')) {
    const body = raw.trim()
    if (!body) {
      out.push(null)
      continue
    }
    const indent = raw.length - raw.trimStart().length
    const b = BULLET_RE.exec(body)
    const prev = out[out.length - 1]
    if (!b && prev && prev.marker !== null && indent >= prev.indent + 2) {
      prev.content += ` ${body}`
      continue
    }
    out.push(b ? { indent, marker: b[1] ?? '-', content: b[2] ?? '' } : { indent, marker: null, content: body })
  }
  return out
}

export function mdLite(text: string, width: number): MdLine[] {
  const w = Math.max(12, Math.floor(width))
  const maxLevel = Math.max(0, Math.floor((w / 2 - 2) / 2))
  const lines: MdLine[] = []
  for (const b of blocks(text)) {
    if (b === null) {
      const prev = lines[lines.length - 1]
      if (prev && prev.runs.length > 0) lines.push({ lead: '', runs: [] })
      continue
    }
    const indent = '  '.repeat(Math.min(maxLevel, Math.floor(b.indent / 2)))
    if (b.marker !== null) {
      const mark = /^\d/.test(b.marker) ? `${b.marker} ` : '• '
      const lead = indent + mark
      lines.push(...wrapRuns(keyed(b.content), lead, ' '.repeat(lead.length), w))
    } else {
      lines.push(...wrapRuns(inline(b.content), indent, indent, w))
    }
  }
  while (lines.length && lines[lines.length - 1]!.runs.length === 0) lines.pop()
  return lines
}

// One line of text with its inline marks (ticks, bold markers) removed: titles and goals in a row.
export const stripMarks = (text: string) => inline(text).map(r => r.text).join('')

export const lineText = (l: MdLine) => l.lead + l.runs.map(r => r.text).join('')

// The goal as one line: the `- goal:` bullet's text when the summary opens with one, else its first line.
export function goalLine(summary: string | null): string | null {
  if (!summary || !summary.trim()) return null
  const first = blocks(summary).find((b): b is Block => b !== null)
  if (!first) return null
  const m = first.marker !== null ? /^(?:\*\*goal:\*\*|goal:)\s*([\s\S]*)$/i.exec(first.content) : null
  const text = m ? m[1] ?? '' : first.content
  return inline(text).map(r => r.text).join('').replace(/\s+/g, ' ').trim() || null
}

// -- folding --------------------------------------------------------------------------------------

export type ItemState = 'done' | 'current' | 'pending'
export type FoldRow =
  | { kind: 'item'; index: number }
  | { kind: 'done'; from: number; to: number }
  | { kind: 'later'; from: number; to: number }

// Done items and later items fold separately. A run of two or more done items folds into one row. Later
// items fold only when three or more are pending: the next one stays in view, the rest fold. The current
// item, and anything not done that sits before it, always show.
export function fold<T>(items: readonly T[], stateOf: (t: T) => ItemState): FoldRow[] {
  const states = items.map(stateOf)
  let anchor = states.indexOf('current')
  if (anchor < 0) {
    const lastDone = states.lastIndexOf('done')
    anchor = states.findIndex((s, i) => i > lastDone && s === 'pending')
  }
  const pending = states.flatMap((s, i) => (s === 'pending' && i > anchor ? [i] : []))
  const foldLater = pending.length >= 3
  // 'done' and 'later' mark what may fold; anything else always shows.
  const kinds = states.map((s, i): 'done' | 'later' | null =>
    s === 'done' ? 'done' : foldLater && i > anchor && i !== pending[0] && s === 'pending' ? 'later' : null)
  const rows: FoldRow[] = []
  let i = 0
  while (i < states.length) {
    const kind = kinds[i]
    if (!kind) {
      rows.push({ kind: 'item', index: i })
      i++
      continue
    }
    let j = i
    while (j + 1 < states.length && kinds[j + 1] === kind) j++
    if (j === i) rows.push({ kind: 'item', index: i })
    else rows.push({ kind, from: i, to: j })
    i = j + 1
  }
  return rows
}

// "3/7 · 43%": done of total, and the share, as a heading's right side.
export const countTail = (done: number, total: number) =>
  `${done}/${total} · ${total === 0 ? 0 : Math.round((done / total) * 100)}%`

// -- bars -----------------------------------------------------------------------------------------

export const BAR_MIN = 10
export const BAR_MAX = 24

export function barLayout(avail: number, bars: readonly { label: string; tail: string }[]) {
  const labelW = Math.max(0, ...bars.map(b => b.label.length)) + 2
  const tailW = Math.max(0, ...bars.map(b => b.tail.length))
  const barW = clamp(avail - labelW - 2 - tailW, BAR_MIN, BAR_MAX)
  return { labelW, barW, tailW }
}

export const barCells = (percent: number, barW: number) => clamp(Math.round((percent / 100) * barW), 0, barW)

export const stepsTail = (percent: number, done: number, total: number) => `${done}/${total} · ${percent}%`

export const overallTail = (percent: number, estimate: boolean) =>
  `${percent}%`.padStart(4) + (estimate ? '  estimate' : '')

// A running agent's self-reported progress: an integer 0-99, anything else counts as not reported.
export const agentPct = (r: StatusRole): number | null =>
  typeof r.pct === 'number' && Number.isInteger(r.pct) && r.pct >= 0 && r.pct <= 99 ? r.pct : null

export const agentPctTail = (p: number) => `${p}%`.padStart(4)

// Gradient ends and the moving highlight of an agent's progress bar.
type Palette = { dark: number[]; light: number[]; glow: number[] }
const GREEN: Palette = { dark: [0x16, 0xa3, 0x4a], light: [0x4a, 0xde, 0x80], glow: [0xe0, 0xff, 0xec] } // #16a34a #4ade80 #e0ffec
// By role base name; the colours of the `color:` in each ~/.claude/agents/orch-*.md.
const PALETTES: Record<string, Palette> = {
  implementer: GREEN,
  planner: { dark: [0x93, 0x33, 0xea], light: [0xc0, 0x84, 0xfc], glow: [0xf3, 0xe8, 0xff] }, // #9333ea #c084fc #f3e8ff
  'code-reviewer': { dark: [0x08, 0x91, 0xb2], light: [0x22, 0xd3, 0xee], glow: [0xe0, 0xfb, 0xff] }, // #0891b2 #22d3ee #e0fbff
  'security-reviewer': { dark: [0xdc, 0x26, 0x26], light: [0xf8, 0x71, 0x71], glow: [0xff, 0xe4, 0xe4] }, // #dc2626 #f87171 #ffe4e4
  'plan-reviewer': { dark: [0xca, 0x8a, 0x04], light: [0xfa, 0xcc, 0x15], glow: [0xff, 0xf9, 0xdb] }, // #ca8a04 #facc15 #fff9db
  director: { dark: [0xc0, 0x26, 0xd3], light: [0xe8, 0x79, 0xf9], glow: [0xfd, 0xf0, 0xff] }, // #c026d3 #e879f9 #fdf0ff
}

// Idle (0%) bar: the whole bar shimmers in dark grey.
export const GREY: Palette = { dark: [0x3a, 0x3a, 0x3a], light: [0x55, 0x55, 0x55], glow: [0x8a, 0x8a, 0x8a] }

export function agentPalette(role: string | null): Palette {
  const base = (role ?? '').split(':')[0] ?? ''
  return Object.hasOwn(PALETTES, base) ? PALETTES[base]! : GREEN
}

const RADIUS = 3.5

const hex = (rgb: number[]) => `#${rgb.map(c => Math.round(c).toString(16).padStart(2, '0')).join('')}`
const mix = (a: number[], b: number[], t: number) => a.map((c, i) => c + ((b[i] ?? c) - c) * t)

// One colour per filled cell: a left→right gradient in the palette (green by default) with a highlight moving one cell per frame,
// period `filled + 6`.
export function shimmer(filled: number, frame: number, palette: Palette = GREEN): string[] {
  if (filled <= 0) return []
  const period = filled + 6
  const pos = (((frame % period) + period) % period) - 3
  return Array.from({ length: filled }, (_, i) => {
    const base = mix(palette.dark, palette.light, filled === 1 ? 0 : i / (filled - 1))
    const glow = Math.max(0, 1 - Math.abs(i - pos) / RADIUS)
    return hex(mix(base, palette.glow, glow * 0.85))
  })
}

// -- questions ------------------------------------------------------------------------------------

// The text a wake sends: fixed, no answer in it. The agent reads the answers with `orch`.
export const WAKE_TEXT =
  'orch-pane: the user answered parked questions. Read the answers with `orch status --json` (the questions block, history) or the task\'s questions.md, then carry on.'

export const NO_DRAFT: Draft = { pick: null, custom: null, confirmed: false, skipped: false, group: null, answer: null }

// A question that cannot be undone: nothing is preselected for it, and Accept and auto-answer leave it alone.
export const isCritical = (q: StatusQuestion) => q.reversible === 'no'

export const recommendedOf = (q: StatusQuestion): StatusOption | undefined => q.options.find(o => o.recommended)

// Whether the entry carries its options. `compact` and `minimal` entries (a long list's tail) do not.
export const hasOptions = (q: StatusQuestion) => (q.detail ?? 'full') === 'full'

// The last option of every question: pause it. Sending it records the answer "Hold on". It is never preselected,
// never taken by Accept and never by an auto-answer.
export const HOLD_LABEL = 'Hold on'
export const HOLD_NOTE = 'Pause this question; nothing changes until you come back to it.'

// A question that already has an option of that name keeps its own, with its own explanation.
export const needsHoldRow = (q: StatusQuestion) => !q.options.some(o => o.label.trim().toLowerCase() === HOLD_LABEL.toLowerCase())

// A multi-select question's answer: the chosen labels joined by `|` (a label never holds one), as `orch answers` expects.
export const MULTI_SEP = '|'
export const isMulti = (q: StatusQuestion) => q.multi === true

const chosenOf = (pick: string | null | undefined): string[] => (pick ?? '').split(MULTI_SEP).filter(Boolean)

const isOption = (q: StatusQuestion, label: string) =>
  isMulti(q)
    ? (label === HOLD_LABEL || (chosenOf(label).length > 0 && chosenOf(label).every(l => q.options.some(o => o.label === l))))
    : label === HOLD_LABEL || q.options.some(o => o.label === label)

// A pick after pressing `label` on a multi-select question: toggles it in the set; Hold on stands alone.
export function togglePick(current: string | null | undefined, label: string): string | null {
  if (label === HOLD_LABEL) return current === HOLD_LABEL ? null : HOLD_LABEL
  const set = chosenOf(current).filter(l => l !== HOLD_LABEL)
  const next = set.includes(label) ? set.filter(l => l !== label) : [...set, label]
  return next.length > 0 ? next.join(MULTI_SEP) : null
}

// Whether an option shows as chosen.
export const isChosen = (q: StatusQuestion, d: Draft | undefined, label: string): boolean =>
  isMulti(q) ? chosenOf(pickedLabel(q, d)).includes(label) : pickedLabel(q, d) === label

// What Next would record: the typed text, else the picked option, else the recommended one (never for a critical question).
export function answerOf(q: StatusQuestion, d: Draft | undefined): string | null {
  const typed = isMulti(q) ? undefined : d?.custom?.trim()   // orch refuses free text on a multi-select question
  if (typed) return typed
  if (d?.pick && isOption(q, d.pick)) return d.pick
  if (isCritical(q) || isMulti(q)) return null
  return recommendedOf(q)?.label ?? null
}

// The option that shows as chosen: none when a typed answer is in use.
export function pickedLabel(q: StatusQuestion, d: Draft | undefined): string | null {
  if (d?.custom?.trim()) return null
  return answerOf(q, d)
}

// An option's line under its label: its explanation, else the stated reason it has none.
export const optionNote = (o: StatusOption) =>
  o.kind === 'explanation' && o.text ? o.text : `No explanation: ${o.text ?? 'not given'}`

// "blocks step 5 · can be changed later".
export function metaText(q: StatusQuestion): string {
  const b = (q.blocks ?? 'all').trim().toLowerCase()
  const blocks = b === 'all' || b === '' ? 'blocks everything' : b === 'none' ? 'blocks nothing' : `blocks step ${q.blocks}`
  const rev = q.reversible === 'yes' ? 'can be changed later' : q.reversible === 'no' ? 'cannot be undone' : 'may not be undoable'
  return `${blocks} · ${rev}`
}

// The queue's order: questions still open keep the place they joined at; new ones go to the end, in data order.
export function mergeOrder(prev: readonly string[] | undefined, open: readonly string[]): string[] {
  const still = new Set(open)
  const kept = (prev ?? []).filter(id => still.has(id))
  const have = new Set(kept)
  return [...kept, ...open.filter(id => !have.has(id))]
}

// The next question to ask after `from`: open, not confirmed, not skipped; the rest of the queue first, then the start.
export function nextInQueue(order: readonly string[], drafts: Record<string, Draft> | undefined, from: string | null): string | null {
  const ok = (id: string) => id !== from && !drafts?.[id]?.confirmed && !drafts?.[id]?.skipped
  const at = from === null ? -1 : order.indexOf(from)
  return order.slice(at + 1).find(ok) ?? order.slice(0, Math.max(0, at)).find(ok) ?? null
}

// `orch answers` argv for one task: every answer an element of its own, after `--` so none reads as a flag.
export function answersArgv(windows: boolean, orch: string, taskId: string, pairs: readonly [string, string][], auto: boolean): string[] {
  return [
    ...(windows ? ['python'] : []), orch, 'answers', '-t', taskId, ...(auto ? ['--auto', '--as', 'orch-pane'] : []),
    '--', ...pairs.flatMap(([id, text]) => [id, text]),
  ]
}

// "Q3 is answered, not open; nothing written" names a draft that went stale; "no question Q3" too.
export function staleQuestion(stderr: string): string | null {
  const m = /\b(Q\d+) is \w+, not open|no question (Q\d+)/.exec(stderr)
  return m ? (m[1] ?? m[2] ?? null) : null
}

// Open questions an auto-answer may take: reversible, a recommendation to take, options loaded, no draft of the person's.
export function autoPairs(open: readonly StatusQuestion[], drafts: Record<string, Draft> | undefined): [string, string][] {
  return open.flatMap((q): [string, string][] => {
    const rec = recommendedOf(q)
    const d = drafts?.[q.id]
    const touched = d && (d.confirmed || d.pick !== null || d.custom !== null || d.skipped)
    return q.status === 'open' && q.reversible === 'yes' && !isMulti(q) && hasOptions(q) && rec && !touched ? [[q.id, rec.label]] : []
  })
}

// Questions Accept would confirm: open, not critical, with a recommendation, not already confirmed.
export const acceptable = (q: StatusQuestion, drafts: Record<string, Draft> | undefined) =>
  q.status === 'open' && !isCritical(q) && !isMulti(q) && hasOptions(q) && recommendedOf(q) !== undefined && !drafts?.[q.id]?.confirmed

export const confirmedOf = (drafts: Record<string, Draft> | undefined) =>
  Object.entries(drafts ?? {}).filter(([, d]) => d.confirmed && d.answer)

// -- compaction -----------------------------------------------------------------------------------

// The tool the model calls to ask for a compaction: `mcp__<plugin>__<name>`.
export const COMPACT_TOOL = 'compact_when_idle'
export const COMPACT_TOOL_FULL = `mcp__orch-pane__${COMPACT_TOOL}`

// Fixed texts, none carrying a caller's words. After a requested compaction the agent is woken once, to re-read
// its state from orch.
export const COMPACT_WAKE_TEXT =
  'orch-pane: the conversation was compacted at your request. Re-read the task state with `orch resume` (or `orch status --json`) and carry on.'
export const COMPACT_SEND_NOTE =
  'Keep the task state, the parked questions and the answers just recorded; read them back with `orch` rather than from memory.'

// Why a compaction must wait: a subagent runs, or a spawn was recorded and has not returned. Only tasks of this
// folder or session count. Null when nothing runs.
export function busyReason(tasks: readonly StatusTask[]): string | null {
  for (const t of tasks.filter(x => isMine(x) && isRunning(x))) {
    const roles = t.running.roles.map(r => r.role ?? '?')
    const spawn = t.running.last_spawn
    return roles.length > 0
      ? `a subagent is running (${roles.join(', ')}; task ${t.id})`
      : `${spawn?.role ?? 'a subagent'} was spawned and has not returned (task ${t.id})`
  }
  return null
}

export const HISTORY_PAGE = 10

// Answered and assumed questions matching a search (topic, text, group and answer), in the given order.
export function historyRows(history: readonly StatusQuestion[], needle: string): StatusQuestion[] {
  const n = needle.trim().toLowerCase()
  return history
    .filter(q => q.status === 'answered' || q.status === 'assumed')
    .filter(q => !n || `${q.topic ?? ''} ${q.text} ${q.group ?? ''} ${q.answer ?? ''}`.toLowerCase().includes(n))
}

export const pageCount = (n: number, size = HISTORY_PAGE) => Math.max(1, Math.ceil(n / size))

export const groupKey = (taskId: string, group: string) => `${taskId}\n${group}`

// What `orch status --json` calls the questions that have no group.
export const UNGROUPED = '(ungrouped)'

// The group being answered: the one the person picked while it still has open questions, else the data's first.
export function activeGroupOf(t: StatusTask, picked: string | null): string | null {
  const qq = t.questions
  if (!qq) return null
  const open = (label: string) => qq.groups.some(g => g.label === label && g.counts.open > 0)
  return picked !== null && open(picked) ? picked : qq.open_group
}

// The active group's open questions: the status block's own, or the ones fetched for that group. An entry drawn
// without its options (a long list's tail) is replaced by its full entry once one was fetched for it.
export function openListOf(t: StatusTask, picked: string | null, fetched: Record<string, StatusQuestion[]>,
  full: Record<string, StatusQuestion> = {}): StatusQuestion[] {
  const qq = t.questions
  const label = activeGroupOf(t, picked)
  if (!qq || label === null) return []
  const list = label === qq.open_group ? qq.open : fetched[groupKey(t.id, label)] ?? []
  return list.map(e => (hasOptions(e) ? e : full[groupKey(t.id, e.id)] ?? e))
}

// Where `--question-skip` should start to give the open question and the next one their options: the index in the
// group's list of the first of them that lacks them, or null when both have theirs.
export function skipFor(list: readonly StatusQuestion[], order: readonly string[], drafts: Record<string, Draft> | undefined,
  current: string | null): number | null {
  const next = current === null ? null : nextInQueue(order, drafts, current)
  const lacking = [current, next].flatMap(id => (id === null ? [] : list.flatMap((q, i) => (q.id === id && !hasOptions(q) ? [i] : []))))
  return lacking.length === 0 ? null : Math.min(...lacking)
}

// The open question: the wanted one while it is not confirmed, else the first still to ask.
export function pickCurrent(order: readonly string[], drafts: Record<string, Draft> | undefined, wanted: string | null): string | null {
  if (wanted !== null && order.includes(wanted) && !drafts?.[wanted]?.confirmed) return wanted
  return nextInQueue(order, drafts, null)
}

// -- model helpers --------------------------------------------------------------------------------

// cyrb53: a 53-bit string hash; a cache key, not a security use. The prefix names the prompt version.
export function textKey(text: string): string {
  let h1 = 0xdeadbeef
  let h2 = 0x41c6ce57
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i)
    h1 = Math.imul(h1 ^ ch, 2654435761)
    h2 = Math.imul(h2 ^ ch, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  const n = 4294967296 * (2097151 & h2) + (h1 >>> 0)
  return `p1-${n.toString(16).padStart(14, '0')}`
}

// What a plain-language summary is asked about, and so what its cache key covers.
export const plainInput = (kind: 'goal' | 'milestone' | 'step', title: string | null, summary: string) =>
  `${kind}${title ? `: ${title}` : ''}\n\n${summary}`

const ITEM_CLIP = 160

type Lean = Omit<StatusTask, 'task_dir' | 'repo_path' | 'worktree'>

function lean(t: StatusTask, items: 'full' | 'clip' | 'none'): Lean {
  const { task_dir: _d, repo_path: _r, worktree: _w, ...rest } = t as StatusTask & Record<string, unknown>
  const cut = (s: string | null) => (items === 'full' || s === null ? s : items === 'clip' ? clip(s, ITEM_CLIP) : null)
  return {
    ...rest,
    // The parked questions are answered in the pane, not through Ask: only their counts go to the model.
    questions: t.questions ? { counts: t.questions.counts } : undefined,
    steps: { ...t.steps, items: t.steps.items.map(s => ({ ...s, summary: cut(s.summary) })) },
    milestones: t.milestones
      ? { ...t.milestones, items: t.milestones.items.map(m => ({ ...m, summary: cut(m.summary) })) }
      : null,
  } as Lean
}

// The status of the shown tasks for the Ask prompt, bounded by `limit` characters.
export function askContext(tasks: readonly StatusTask[], limit = 60000): string {
  let last = ''
  for (const items of ['full', 'clip', 'none'] as const) {
    last = JSON.stringify(tasks.map(t => lean(t, items)))
    if (last.length <= limit) return last
  }
  return last
}
