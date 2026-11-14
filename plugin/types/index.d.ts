// The plugin's types contract (one self-contained file): orch-pane's types, then orch-ui's, then the state of both
// mods under the plugin's one name (their keys do not overlap).

// ---- orch-pane

// The part of `orch status --json` (schema 1) this mod reads. Missing data is null.
export type StatusRole = {
  role: string | null
  state: string | null
  step: string | null
  note: string | null
  pct?: number | null
}

export type StatusSpawn = {
  ts: string | null
  role: string | null
  agent: string | null
  tier: string | null
  steps: string[] | null
  milestone: number | null
  returned: boolean
}

export type StatusStep = { id: string; title: string | null; done: boolean; current: boolean; summary: string | null }

export type StatusMilestone = {
  n: number
  goal: string | null
  size: string | null
  state: 'done' | 'current' | 'pending'
  summary: string | null
}

// One parked question of `orch status --json`. An orch from before the questions block has none of this.
export type StatusOption = { label: string; text: string | null; kind: string | null; recommended: boolean }

export type StatusQuestion = {
  id: string
  n: number
  status: string
  text: string
  topic: string | null
  group: string | null
  reversible: string | null
  // Several options may be picked; the answer is the chosen labels joined by `|`. Absent in an orch from before multi-select.
  multi?: boolean
  blocks: string | null
  asked_by: string | null
  asked: string | null
  options: StatusOption[]
  answer: string | null
  answered: string | null
  // `full`, or `compact` / `minimal` once the list's byte budget is spent: those two carry no options. An answered
  // question past the newest few of the history is `history`: no options, but its answer, time and topic.
  detail?: 'full' | 'compact' | 'minimal' | 'history'
}

export type StatusQuestions = {
  counts: { open: number; answered: number; assumed: number; withdrawn: number }
  groups: { group: string | null; label: string; counts: { open: number; answered: number; assumed: number; withdrawn: number } }[]
  open_group: string | null
  open: StatusQuestion[]
  history: StatusQuestion[]
  history_total: number
}

export type StatusTask = {
  id: string
  repo: string | null
  slug: string | null
  task_dir: string | null
  repo_path: string | null
  worktree: string | null
  in_cwd: boolean | null
  // Absent on an orch older than `--session`; null when the pane passed no session id.
  this_session?: boolean | null
  state: string
  state_words: string | null
  mode: string | null
  shape: string | null
  program: boolean | null
  milestone: string | null
  milestone_no: number | null
  summary: string | null
  updated: string | null
  last_log: string | null
  questions_open: number | null
  // Absent on an orch older than the questions block.
  questions?: StatusQuestions | null
  running: { roles: StatusRole[]; last_spawn: StatusSpawn | null; spawns?: StatusSpawn[] }
  steps: {
    total: number | null
    done: number | null
    percent: number | null
    current: string[] | null
    items: StatusStep[]
  }
  milestones: { done: number; total: number | null; items: StatusMilestone[] } | null
  overall: { percent: number | null; estimate: boolean; basis: string }
  error: string | null
}

export type StatusJson = { schema: 1; generated: string; cwd: string | null; tasks: StatusTask[] }

// The part of `orch-index status --json` (schema 1) this mod reads. Missing data is null or absent.
export type IndexStatus = {
  schema: 1
  state: 'none' | 'current' | 'stale' | 'outdated'
  indexing: boolean
  indexed_commit: string | null
  head_commit: string | null
  commits_behind: number | null
  indexed_at: string | null
  db_bytes: number | null
  counts: { files: number; symbols: number; refs: number; includes: number; bases: number; names: number } | null
  parse_error_files: number | null
  parse_error_structural: number | null
  extractor_version?: string | null
  binary_extractor_version?: string | null
  languages?: Record<string, number>
  last_index?: {
    kind: string
    seconds: number
    files_total?: number
    extracted: number
    reused?: number
    files_per_sec?: number
  } | null
  history?: { at: string; kind: string; seconds: number; extracted: number }[]
  usage?: {
    window_days: number
    queries: number
    by_command: Record<string, { n: number; p50_ms: number; p95_ms: number }> | null
    zero_hit_pct: number
    stale_answer_pct: number
    // null until a query carries a trust label (orch-index reports it so after a fresh index).
    label_mix: Record<string, number> | null
    last_query_at: string | null
  } | null
}

// okAt: when the last good fetch landed (an error keeps it). index: the code index of the session folder;
// indexNote says why there is none (orch-index missing, or it failed).
export type Snapshot = {
  data: StatusJson | null
  error: string | null
  fetchedAt: number | null
  okAt: number | null
  index: IndexStatus | null
  indexNote: string | null
  about?: About | null
}

// One installed part: version and commit, or `installed: false`. commit is null when the part does not record one.
export type AboutPart = { name: string; installed: boolean; version: string | null; commit: string | null }
export type About = AboutPart[]

// A plain-language summary, by the cache key of the text it summarises.
export type PlainEntry = { state: 'pending'; at: number } | { state: 'done'; text: string } | { state: 'error'; reason: string }

// The Ask box's one question and its answer.
export type AskState =
  | { state: 'idle' }
  | { state: 'busy'; question: string; at: number }
  | { state: 'done'; question: string; answer: string }
  | { state: 'error'; question: string; reason: string }

// The folded bottom panel's open tab; null is all folded.
export type Tab = 'history' | 'ask' | 'more'

// An unsent answer, per task and question id (kept in $.store, D5). `answer` is what Next recorded: the option's
// label or the typed text. Only a confirmed draft is sent.
export type Draft = {
  pick: string | null
  custom: string | null
  confirmed: boolean
  skipped: boolean
  group: string | null
  answer: string | null
}
export type Drafts = Record<string, Record<string, Draft>>

// What the answering panel remembers between draws: the open question and the open group of each task, the order
// the questions joined the queue, other groups' questions fetched on demand, and what a Send said.
export type QState = {
  current: Record<string, string | null>
  group: Record<string, string | null>
  order: Record<string, string[]>
  fetched: Record<string, StatusQuestion[]>
  // Full entries (options and all) fetched with `--question-skip` for questions the status block drew without them.
  full: Record<string, StatusQuestion>
  sending: Record<string, boolean>
  error: Record<string, string | null>
}

// ---- orch-ui

// The reply formatter's rewrite as parts (hooks/format.ts parses it), which route took the reply, and the
// per-message entry (by the key of the reply's text), the keys that received a valid
// entry in turn order, and this session's counts with the last outcome.
export type RouteKind = 'short' | 'long'
export type BannerTag = 'act' | 'ok' | 'info'
export type RowTag = 'error' | 'warn' | 'note' | 'good'
export type SectionTag = 'do' | 'more' | 'ask'
export type Row = { tag: RowTag; title: string; detail: string[] }
export type Part =
  | { kind: 'banner'; tag: BannerTag; title: string }
  | { kind: 'plain'; body: string }
  | { kind: 'section'; tag: SectionTag; title: string; body: string }
  | { kind: 'card'; title: string; rows: Row[] }
export type FmtEntry =
  // `since`: when the conversion was claimed (ms since the epoch), for the placeholder's elapsed seconds
  | { state: 'pending'; since?: number }
  | { state: 'done'; kind: RouteKind; parts: Part[] }
  | { state: 'kept'; kind: RouteKind; reason: string }
// The main loop's turns: the number of the newest turn that started, and when it started while it runs (null once it ended).
export type FmtTurn = { n: number; at: number | null }
// The placeholder's animation: the frame number and the time (ms since the epoch) of the last tick.
export type FmtFrame = { i: number; now: number }
export type FmtOrderItem = { key: string; seq: number }
export type FmtStats = {
  formatted: number
  kept: number
  last: { reason: string; kind: RouteKind; model: string } | null
  // money by the billing that paid: api runs and subscription (plan) runs are kept apart
  spentApiUsd: number
  spentPlanUsd: number
  // the last run's billing; labels a $0.00 note while nothing has been spent
  billing: Billing | null
  viaBaseUrl: boolean
  // eligible replies seen, how many carried the marker, and on-request conversions (the ones the user had to ask for)
  eligible: number
  marked: number
  asked: number
}
// Which replies are converted: only the ones the orchestrator marked, or every eligible one.
export type Trigger = 'marked' | 'auto'
// The newest eligible reply of the session by turn.complete order; `retried` once a request converted it a second time.
export type FmtLatest = { key: string; text: string; seq: number; retried: boolean }

// One formatter call through a `claude -p` child (hooks/format.ts builds the argv and env and checks the stream).
// `api` is `--bare` with ANTHROPIC_API_KEY; `subscription` is the claude.ai login with the key blanked.
export type Billing = 'api' | 'subscription'
export type Effort = 'low' | 'medium' | 'high'
// Where an api run gets its key: the session's ANTHROPIC_API_KEY (inherited, never read), or the key file.
export type KeySource = 'env' | 'file'
export type CallRequest = { system: string; prompt: string; model: string; effort: string; kind: RouteKind }
// Why a call produced no text. The first group stops formatting for the session until a row is pressed.
export type CallReason =
  | 'billing-mismatch'
  | 'model-mismatch'
  | 'no-credential'
  | 'auth-failed'
  | 'cli-missing'
  | 'cloud-provider'
  | 'base-url-set'
  | 'workdir-failed'
  | 'key-file-missing'
  | 'key-file-unsafe'
  | 'key-file-invalid'
  | 'rate-limited'
  | 'overloaded'
  | 'api-error'
  | 'truncated'
  | 'cli-error'
  | 'no-answer'
  | 'cli-output'
  | 'cli-failed'
  | 'timeout'
export type CallOutcome =
  | { ok: true; text: string; costUsd: number; durationMs: number; durationApiMs: number; outputTokens: number }
  // paidBy is set where the run's own report names who paid (billing-mismatch); otherwise the configured billing paid.
  | { ok: false; reason: CallReason; costUsd: number; paidBy?: Billing }

declare module 'claude-code' {
  interface PluginState {
    orch: {
      // orch-pane
      snapshot: Shaped<Snapshot>
      showAll: boolean
      expanded: Record<string, boolean>
      dismissed: boolean
      frame: number
      plain: Record<string, PlainEntry>
      ask: AskState
      tab: Tab | null
      ai: boolean
      notify: boolean
      compactSend: boolean
      auto: boolean
      // "Offer Mythos security reviews": the value `orch setting get mythos_review` printed (the CLI's file is the source)
      mythos: boolean
      drafts: Shaped<{ loaded: boolean; byTask: Drafts }>
      qstate: QState
      hist: { query: string; page: number }
      // orch-ui
      fmtOn: boolean
      fmtOrch: boolean
      fmtRaw: boolean
      fmtSeq: number
      fmtOrder: FmtOrderItem[]
      fmtStats: FmtStats
      fmtEntries: StateFamily<FmtEntry>
      fmtMarks: StateFamily<boolean>
      fmtModel: string
      fmtEffort: string
      fmtBilling: Billing
      fmtKey: KeySource
      fmtPause: string | null
      fmtTrigger: Trigger
      fmtLatest: FmtLatest | null
      fmtLoaded: boolean
      fmtTurn: FmtTurn
      fmtFrame: FmtFrame
      // conversions claimed and not yet ended; the animation timer runs while any is, or while a turn runs
      fmtPendingN: number
      fmtTicking: boolean
      // counts the changes to the originals of hidden replies (module state), so a row that read it draws again
      fmtHeld: number
    }
  }
}
